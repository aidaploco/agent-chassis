from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EvalMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class EvalCase(BaseModel):
    """Eval case schema v1 (D17).

    ``messages`` is the conversation so far; the final message must be from
    the user (it is the turn under test). Assistant turns are allowed before
    it — multi-turn behavior is first-class, not an afterthought.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    messages: list[EvalMessage] = Field(min_length=1)
    expected_agent: str | None = None
    expected_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    expected_directive: Literal["reply", "route", "escalate"] | None = None
    guardrail_expectations: dict[str, Literal["pass", "trip"]] = Field(
        default_factory=dict
    )
    quality_prompt: str | None = None

    @property
    def last_user_message(self) -> str:
        return self.messages[-1].content


class EvalCaseError(ValueError):
    """A case file failed to load or validate."""


def load_cases(path: Path | str) -> list[EvalCase]:
    """Load every case from a YAML file or a directory of YAML files."""
    path = Path(path)
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    if not files:
        raise EvalCaseError(f"no case files found under {path}")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for file in files:
        raw = yaml.safe_load(file.read_text())
        if not isinstance(raw, list):
            raise EvalCaseError(f"{file}: case file root must be a list of cases")
        for index, item in enumerate(raw):
            try:
                case = EvalCase.model_validate(item)
            except ValidationError as exc:
                raise EvalCaseError(f"{file}[{index}]: {exc}") from exc
            if case.messages[-1].role != "user":
                raise EvalCaseError(
                    f"{file}[{index}] ({case.id}): last message must be from the user"
                )
            if case.id in seen:
                raise EvalCaseError(f"{file}[{index}]: duplicate case id {case.id!r}")
            seen.add(case.id)
            cases.append(case)
    return cases
