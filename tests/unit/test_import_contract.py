"""The adapter-seam contract (D2) must actually reject violations.

CI already runs ``lint-imports``, but a green run only proves the contract was
not violated, not that it can detect a violation. A typo in a ``source_modules``
entry, a loosened contract, or a changed ``forbidden_modules`` would all stay
green. These tests copy the real source tree and pyproject into a temp dir,
plant a violation in the copy, and require ``lint-imports`` to fail *for the
expected reason*: the contract is named as broken and the planted module is
named as the offender. An unmodified copy must pass, so a failure cannot come
from anything other than the plant.
"""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PACKAGE_SRC = REPO_ROOT / "src" / "agent_chassis"

_CONTRACT = tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]["contracts"][0]
CONTRACT_NAME: str = _CONTRACT["name"]
SOURCE_MODULES: list[str] = _CONTRACT["source_modules"]

# Deliberately NOT read from pyproject: the tests must hold the config to an
# independent oracle. Reading the forbidden module from the file under test
# would let a typo there pass every test (verified by mutation).
FORBIDDEN = "pydantic_ai"

# Top-level members that are allowed to import the vendor SDK (directly or via
# the adapter). Anything else under src/agent_chassis must be a contract source.
EXEMPT = {
    "adapters": "the seam itself: the only place the vendor SDK may be imported",
    "worker": "composition root: wires the adapter into the Temporal worker",
}


def _lint_imports_command() -> list[str]:
    # Prefer the interpreter's own bin dir so this works however pytest was
    # launched, not only when the venv happens to be on PATH.
    candidate = Path(sys.executable).parent / "lint-imports"
    if candidate.exists():
        return [str(candidate)]
    found = shutil.which("lint-imports")
    if found is None:
        pytest.fail("lint-imports not found; is the dev dependency group installed?")
    return [found]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A throwaway copy of the real package plus the real contract config."""
    shutil.copy(PYPROJECT, tmp_path / "pyproject.toml")
    shutil.copytree(
        PACKAGE_SRC,
        tmp_path / "agent_chassis",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return tmp_path


def _run_lint_imports(cwd: Path) -> subprocess.CompletedProcess[str]:
    # cwd matters: lint-imports puts it first on sys.path, so the copy under
    # test shadows the editable install of the real package.
    return subprocess.run(
        [*_lint_imports_command(), "--no-cache"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, "COLUMNS": "250"},  # keep rich from wrapping the report
        timeout=120,
    )


def _module_file(root: Path, module: str) -> Path:
    """The source file that defines ``module`` (package ``__init__`` or ``.py``)."""
    relative = Path(*module.split("."))
    as_package = root / relative / "__init__.py"
    return as_package if as_package.exists() else root / relative.with_suffix(".py")


def test_unmodified_tree_passes_the_contract(tree: Path) -> None:
    """Control: without a plant, the contract is kept — so any failure below is
    caused by the plant, not by the copy or the environment."""
    result = _run_lint_imports(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert CONTRACT_NAME in result.stdout
    assert "KEPT" in result.stdout


def test_contract_sources_are_exactly_the_non_exempt_top_level_modules() -> None:
    """A new package or module must be added to ``source_modules`` (or be
    explicitly exempted above); otherwise it silently escapes the contract.
    The expected set comes from the filesystem, not from the config."""
    members = {
        p.stem if p.suffix == ".py" else p.name
        for p in PACKAGE_SRC.iterdir()
        if p.name != "__pycache__"
        and p.name != "__init__.py"
        and (p.suffix == ".py" or (p / "__init__.py").exists())
    }
    expected = {f"agent_chassis.{name}" for name in members - EXEMPT.keys()}
    assert set(SOURCE_MODULES) == expected, (
        f"unguarded (add to source_modules or EXEMPT): {sorted(expected - set(SOURCE_MODULES))}; "
        f"stale entries: {sorted(set(SOURCE_MODULES) - expected)}"
    )


@pytest.mark.parametrize("module", SOURCE_MODULES)
def test_every_listed_chassis_module_is_actually_guarded(
    tree: Path, module: str
) -> None:
    """Planting a vendor import in each ``source_modules`` entry must break the
    contract. Catches a misspelled or stale entry, which would silently exempt
    that module."""
    target = _module_file(tree, module)
    assert target.exists(), f"{module} is listed in the contract but has no source file"
    with target.open("a") as f:
        f.write(f"\nimport {FORBIDDEN}\n")

    result = _run_lint_imports(tree)

    assert result.returncode != 0, f"{module} importing {FORBIDDEN} was NOT rejected"
    assert "Broken contracts" in result.stdout
    assert CONTRACT_NAME in result.stdout
    assert f"{module} is not allowed to import {FORBIDDEN}" in result.stdout


@pytest.mark.parametrize(
    "planted_source",
    [
        pytest.param(f"import {FORBIDDEN}\n", id="import"),
        pytest.param(f"from {FORBIDDEN} import Agent\n", id="from-import"),
        pytest.param(
            f"from agent_chassis.adapters.{FORBIDDEN} import adapter\n",
            id="indirect-via-adapter",
        ),
    ],
)
def test_every_import_form_is_rejected_and_the_offender_is_named(
    tree: Path, planted_source: str
) -> None:
    """Direct, ``from`` and indirect (through the adapter package) imports are
    all rejected, and the report names the planted module as the offender."""
    (tree / "agent_chassis" / "channels" / "_planted.py").write_text(planted_source)

    result = _run_lint_imports(tree)

    assert result.returncode != 0, f"{planted_source!r} was NOT rejected"
    assert CONTRACT_NAME in result.stdout
    assert "agent_chassis.channels._planted" in result.stdout
