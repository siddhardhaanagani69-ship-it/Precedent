"""Validate all model output before it can affect council state."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Output(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Option(Output):
    id: Literal["A", "B", "C"]
    label: str = Field(min_length=1, max_length=160)


class Consequence(Output):
    id: str = Field(pattern=r"^c\d+$")
    label: str = Field(min_length=1, max_length=160)
    attribute: str
    impact: float = Field(ge=-3, le=3)
    checkable_online: bool = False


class Situation(Output):
    key: str
    label: str
    values: list[str] = Field(min_length=2, max_length=5)
    user_value: str | None = None


class Plan(Output):
    title: str = Field(min_length=1, max_length=160)
    options: list[Option] = Field(min_length=2, max_length=3)
    attributes: list[str] = Field(min_length=4, max_length=6)
    consequences: list[Consequence] = Field(min_length=6, max_length=10)
    situational: list[Situation] = Field(min_length=2, max_length=4)
    user_summary: str = Field(min_length=1, max_length=600)
    search_queries: list[str] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def references(self):
        for ids in ([o.id for o in self.options], self.attributes,
                    [c.id for c in self.consequences], [s.key for s in self.situational]):
            if len(ids) != len(set(ids)):
                raise ValueError("IDs must be unique")
        if any(c.attribute not in self.attributes for c in self.consequences):
            raise ValueError("Consequences must reference a listed attribute")
        return self


class Reason(Output):
    text: str = Field(max_length=300)
    consequence_id: str
    attribute: str
    valence: Literal[-1, 1]


class MinedStory(Output):
    idx: int = Field(ge=0)
    relevant: bool
    option_id: str | None = None
    outcome: Literal["glad", "regret", "mixed", "unknown"] = "unknown"
    months_after: float | None = Field(default=None, ge=0)
    context: dict[str, str | None] = Field(default_factory=dict)
    reasons: list[Reason] = Field(default_factory=list, max_length=10)
    summary: str = Field(default="", max_length=1000)
    evidence_quote: str = Field(default="", max_length=1200)


class Extraction(Output):
    stories: list[MinedStory] = Field(max_length=6)


class Persona(Output):
    cohort_key: str
    name: str = Field(min_length=1, max_length=100)
    persona: str = Field(min_length=1, max_length=600)


class Personas(Output):
    agents: list[Persona] = Field(max_length=4)


class Claim(Output):
    text: str = Field(max_length=800)
    cites: list[str] = Field(default_factory=list, max_length=12)


class BeliefChange(Output):
    option_id: str
    consequence_id: str
    new_p: float = Field(ge=0, le=1)
    cites: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(max_length=600)


class Argument(Output):
    message: str = Field(min_length=1, max_length=1800)
    claims: list[Claim] = Field(default_factory=list, max_length=8)
    belief_changes: list[BeliefChange] = Field(default_factory=list, max_length=10)


class VerdictText(Output):
    summary: str = Field(min_length=1, max_length=1200)
    crux: str = Field(min_length=1, max_length=500)
    cheap_test: str = Field(min_length=1, max_length=500)


class VerificationQuery(Output):
    query: str = Field(min_length=5, max_length=400)


class Finding(Output):
    supported: bool = False
    source_index: int = Field(ge=0, le=2, default=0)
    finding: str = Field(default='', max_length=600)
    evidence_quote: str = Field(default='', max_length=1200)
    suggested_p: float = Field(default=0.5, ge=0, le=1)


class ModeratorNote(Output):
    message: str = Field(min_length=1, max_length=500)


class QuestionText(Output):
    text: str = Field(min_length=1, max_length=500)
    why: str = Field(min_length=1, max_length=500)
