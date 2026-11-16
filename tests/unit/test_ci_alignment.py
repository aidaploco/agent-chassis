"""What ships must be what is tested.

Dependabot bumped the Docker base image from Python 3.13 to 3.15 while CI only
ran 3.13, so the shipped interpreter had never run the suite. These tests tie
the image, the declared floor and the CI matrix together, so the next bump
fails here (on the Dependabot PR itself) until the new version is in the matrix.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def _image_python() -> str:
    match = re.search(
        r"^FROM python:(\d+\.\d+)", (ROOT / "Dockerfile").read_text(), re.MULTILINE
    )
    assert match, "Dockerfile has no `FROM python:X.Y` line"
    return match.group(1)


def _ci_matrix() -> list[str]:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    return [str(v) for v in ci["jobs"]["test"]["strategy"]["matrix"]["python-version"]]


def test_the_image_python_is_in_the_ci_matrix():
    assert _image_python() in _ci_matrix(), (
        f"the Docker image runs Python {_image_python()} but CI only tests "
        f"{_ci_matrix()}: add it to the test matrix in .github/workflows/ci.yml"
    )


def test_the_declared_floor_is_in_the_ci_matrix():
    requires = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "requires-python"
    ]
    floor = re.search(r">=\s*(\d+\.\d+)", requires)
    assert floor, f"unexpected requires-python: {requires}"
    assert floor.group(1) in _ci_matrix()
