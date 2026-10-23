"""Fix-with-AI contracts and LLM output schemas.

Ported from backend_two.backend.schemas so the Kinetic app backend can
validate Fix-with-AI responses and enforce a strict JSON format.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = [
    "Process",
    "People",
    "Technology",
    "Data",
    "Policy",
    "Governance",
    "Communication",
    "Workload",
    "External",
]
STEP_TYPES = ["existing", "ai_agent", "human", "decision"]


# ====================================================================== model output
class AgentRecommendation(BaseModel):
    """One recommended AI agent. Narrative only - no quantified benefit claims."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    name: str
    purpose: str
    problem_addressed: str
    impact: str
    why_this_agent: str = Field(description="KPI -> root cause -> evidence -> agent -> impact")
    action: List[str] = Field(min_length=1)
    trigger: List[str] = Field(min_length=1)
    systems: List[str]
    human_in_loop: bool
    human_in_loop_detail: str
    expected_outcome: str
    sequence_order: int

    @field_validator("action", "trigger", "systems")
    @classmethod
    def _strip(cls, v: List[str]) -> List[str]:
        return [s.strip() for s in v if s and s.strip()]


class FutureStateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["existing", "ai_agent", "human", "decision"]
    description: str


class FutureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: List[FutureStateStep] = Field(min_length=2)


class RecommendationLLM(BaseModel):
    """Exactly what the recommendation agent must return."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    representative_case_explanation: str
    agents: List[AgentRecommendation] = Field(min_length=1)
    future_state: FutureState

    @field_validator("agents")
    @classmethod
    def _sequence(cls, agents: List[AgentRecommendation]) -> List[AgentRecommendation]:
        ordered = sorted(agents, key=lambda a: a.sequence_order)
        for i, a in enumerate(ordered, start=1):
            a.sequence_order = i
        return ordered


# ====================================================================== API response
class RootCauseOut(BaseModel):
    root_cause_id: str
    title: str
    description: str
    category: str
    confidence: Optional[str] = None
    rank: Optional[int] = None
    tier_1_kpi_id: Optional[str] = None
    tier_1_kpi: Optional[str] = None
    tier_2_kpi: List[str] = []
    gap_references: List[str] = []
    stages: List[str] = []
    evidence: List[str] = []
    kpi_current_value: Optional[float] = None
    kpi_target: Optional[float] = None
    kpi_unit: Optional[str] = None
    kpi_variance_vs_target_pct: Optional[float] = None
    kpi_health: Optional[str] = None


class ImpactMetric(BaseModel):
    label: str
    value: Optional[float] = None
    unit: str = ""
    detail: str = ""


class ImpactOut(BaseModel):
    impacted_cases: int
    total_cases: int
    impact_percentage: Optional[float] = None
    average_cycle_time: Optional[float] = None
    target_cycle_time: Optional[float] = None
    average_sla_breach: Optional[float] = None
    sla_breach_rate: Optional[float] = None
    rework_rate: Optional[float] = None
    average_handoffs: Optional[float] = None
    average_manual_touches: Optional[float] = None
    average_waiting_days: Optional[float] = None
    additional_cycle_time_vs_unimpacted: Optional[float] = None
    metrics: List[ImpactMetric] = []


class KeyEvent(BaseModel):
    sequence: int
    step_id: Optional[str] = None
    activity: str
    stage: Optional[str] = None
    owner_team: Optional[str] = None
    timestamp: Optional[str] = None
    duration_minutes: Optional[float] = None
    waiting_minutes: Optional[float] = None
    is_rework: bool = False
    is_exception: bool = False
    is_manual: bool = False
    is_handoff: bool = False
    matched_root_cause: bool = False


class CaseEvidence(BaseModel):
    evidence_type: str
    evidence_text: str
    evidence_score: Optional[float] = None


class RepresentativeCaseOut(BaseModel):
    case_id: str
    application_id: Optional[str] = None
    customer_name: Optional[str] = None
    reason_selected: str
    selection_score: Optional[float] = None
    process_stage: Optional[str] = None
    current_status: Optional[str] = None
    cycle_time: Optional[float] = None
    sla: Optional[float] = None
    sla_breach: Optional[float] = None
    rework_loops: Optional[int] = None
    handoffs: Optional[int] = None
    manual_touches: Optional[int] = None
    systems_touched: Optional[int] = None
    requested_amount: Optional[float] = None
    key_events: List[KeyEvent] = []
    evidence: List[CaseEvidence] = []


class RecommendationPlanOut(BaseModel):
    summary: str
    agents: List[AgentRecommendation]


class FixWithAIResponse(BaseModel):
    execution_id: str
    run_id: Optional[int] = None
    status: Literal["completed", "no_impact"]
    generated_at: str
    model: str
    message: Optional[str] = None
    root_cause: RootCauseOut
    impact: ImpactOut
    representative_case: Optional[RepresentativeCaseOut] = None
    recommendation_plan: Optional[RecommendationPlanOut] = None
    future_state: Optional[FutureState] = None


class RootCauseSummary(BaseModel):
    """Row in the root-cause list endpoint, so the UI can resolve ids."""

    root_cause_id: str
    run_id: int
    rank: Optional[int] = None
    title: str
    category: Optional[str] = None
    tier_1_kpi_id: Optional[str] = None
    tier_1_kpi: Optional[str] = None
    tier_2_kpi: List[str] = []


# ====================================================================== strict schema for the model

def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}

LLM_OUTPUT_SCHEMA = _obj(
    {
        "summary": _STR,
        "representative_case_explanation": _STR,
        "agents": {
            "type": "array",
            "items": _obj(
                {
                    "agent_id": _STR,
                    "name": _STR,
                    "purpose": _STR,
                    "problem_addressed": _STR,
                    "impact": _STR,
                    "why_this_agent": _STR,
                    "action": _STR_LIST,
                    "trigger": _STR_LIST,
                    "systems": _STR_LIST,
                    "human_in_loop": {"type": "boolean"},
                    "human_in_loop_detail": _STR,
                    "expected_outcome": _STR,
                    "sequence_order": {"type": "integer"},
                }
            ),
        },
        "future_state": _obj(
            {
                "steps": {
                    "type": "array",
                    "items": _obj(
                        {
                            "id": _STR,
                            "name": _STR,
                            "type": {"type": "string", "enum": STEP_TYPES},
                            "description": _STR,
                        }
                    ),
                },
            }
        ),
    }
)

LLM_RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "kinetic_fix_recommendation",
    "schema": LLM_OUTPUT_SCHEMA,
    "strict": True,
}
