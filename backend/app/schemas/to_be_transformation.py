from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NO_SYSTEM_IN_LOG = "Client API / integration dependency - system not present in event log"

CHANGE_TYPES = [
    "Retained",
    "Automated (Deterministic)",
    "AI Agent",
    "Combined",
    "Eliminated",
    "Human",
]


class ToBeActivityLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_sequence: int = Field(ge=1)
    to_be_sequence: int = Field(ge=1)
    to_be_activity: str = Field(min_length=1)
    change_type: Literal[
        "Retained",
        "Automated (Deterministic)",
        "AI Agent",
        "Combined",
        "Eliminated",
        "Human",
    ]
    responsible_agent: str | None = None
    deterministic_automation: bool = False
    human_in_the_loop: bool = False
    system_used: list[str] = Field(default_factory=list)
    trigger: str = ""
    action: str = ""
    expected_outcome: str = ""
    reason_for_change: str = ""
    kpi_gap_addressed: str = ""

    @field_validator("to_be_activity", "trigger", "action", "expected_outcome", "reason_for_change", "kpi_gap_addressed")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("system_used")
    @classmethod
    def _clean_systems(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


class ToBeDesignLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_summary: str = Field(min_length=1)
    activities: list[ToBeActivityLLM] = Field(min_length=1)


_STRING = {"type": "string"}
_STRING_LIST = {"type": "array", "items": _STRING}

TO_BE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "kinetic_to_be_design",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["stage_summary", "activities"],
        "properties": {
            "stage_summary": _STRING,
            "activities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "current_sequence",
                        "to_be_sequence",
                        "to_be_activity",
                        "change_type",
                        "responsible_agent",
                        "deterministic_automation",
                        "human_in_the_loop",
                        "system_used",
                        "trigger",
                        "action",
                        "expected_outcome",
                        "reason_for_change",
                        "kpi_gap_addressed",
                    ],
                    "properties": {
                        "current_sequence": {"type": "integer", "minimum": 1},
                        "to_be_sequence": {"type": "integer", "minimum": 1},
                        "to_be_activity": _STRING,
                        "change_type": {"type": "string", "enum": CHANGE_TYPES},
                        "responsible_agent": {"type": ["string", "null"]},
                        "deterministic_automation": {"type": "boolean"},
                        "human_in_the_loop": {"type": "boolean"},
                        "system_used": _STRING_LIST,
                        "trigger": _STRING,
                        "action": _STRING,
                        "expected_outcome": _STRING,
                        "reason_for_change": _STRING,
                        "kpi_gap_addressed": _STRING,
                    },
                },
            },
        },
    },
}