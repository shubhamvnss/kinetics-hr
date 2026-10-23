"""
to_be_design_agent.py
 
Deloitte Kinetic - Representative Case To-Be Transformation Design
 
Purpose
-------
Creates a stage-specific Current State -> To-Be State transformation design
using ONLY:
 
1. Representative case selected by representative_case.py
2. Actual event-log activities belonging to that representative case
3. Tier 1 KPI
4. Tier 2 KPI
5. Gap references
6. Recommended agents from recommendation_agent.py
7. Systems actually observed in the representative case event log
 
The LLM does NOT:
- select the representative case
- invent current-state activities
- invent systems
- invent recommended agents
- calculate KPI values
- calculate activity reduction metrics
 
The LLM is used to design the transformation and explain how the
recommended agents change the representative-case workflow.
 
Output
------
A validated ToBeDesign object containing:
 
- Executive transformation summary
- Current-state activities
- To-be activities
- Activity transformations
- Reduced / consolidated activities
- Value-added activities
- Recommended agents
- System integrations
- Agentic workflow
- Human-in-loop points
- Deployment considerations
- Diagram definition
"""
 
from __future__ import annotations
 
import json
import os
from typing import Dict, List, Optional, Any, Tuple

from .. import db
 
from pydantic import BaseModel, ConfigDict, Field, field_validator
 
from ..core.llm_client import create_client
 
 
# ============================================================================
# CONFIGURATION
# ============================================================================
 
MODEL = os.getenv("TO_BE_MODEL", "gpt-4o")
MAX_TOKENS = int(os.getenv("TO_BE_MAX_TOKENS", "8000"))
 

import base64
import os

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont


def generate_png_from_diagram(
    nodes,
    edges,
    case_id,
):
    """
    Generate flow diagram PNG directly from
    diagram_nodes and diagram_edges.
    """

    os.makedirs(
        "generated_diagrams",
        exist_ok=True
    )

    width = 1400
    height = max(
        500,
        len(nodes) * 180
    )

    image = Image.new(
        "RGB",
        (width, height),
        "white"
    )

    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype(
            "arial.ttf",
            18
        )
    except Exception:
        font = ImageFont.load_default()

    node_positions = {}

    x = 450
    y = 80

    # Draw nodes

    for idx, node in enumerate(nodes):

        box_x1 = x
        box_y1 = y + (idx * 150)

        box_x2 = box_x1 + 500
        box_y2 = box_y1 + 80

        draw.rounded_rectangle(
            (
                box_x1,
                box_y1,
                box_x2,
                box_y2
            ),
            radius=12,
            fill="#D9EAD3",
            outline="black",
            width=2
        )

        draw.text(
            (
                box_x1 + 15,
                box_y1 + 25
            ),
            node["label"],
            fill="black",
            font=font
        )

        node_positions[node["id"]] = (
            (box_x1 + box_x2) // 2,
            (box_y1 + box_y2) // 2
        )

    # Draw edges

    for edge in edges:

        source = edge["source"]
        target = edge["target"]

        if (
            source not in node_positions
            or target not in node_positions
        ):
            continue

        sx, sy = node_positions[source]
        tx, ty = node_positions[target]

        draw.line(
            (
                sx,
                sy,
                tx,
                ty
            ),
            fill="black",
            width=3
        )

        # Arrow

        draw.polygon(
            [
                (tx, ty),
                (tx - 8, ty - 12),
                (tx + 8, ty - 12)
            ],
            fill="black"
        )

        if edge.get("label"):

            mx = (sx + tx) // 2
            my = (sy + ty) // 2

            draw.text(
                (mx + 10, my),
                edge["label"],
                fill="blue",
                font=font
            )

    png_path = (
        f"generated_diagrams/"
        f"{case_id}_flow.png"
    )

    image.save(
        png_path,
        format="PNG"
    )

    with open(
        png_path,
        "rb"
    ) as fp:

        png_base64 = base64.b64encode(
            fp.read()
        ).decode()

    return png_path, png_base64

# ============================================================================
# PYDANTIC MODELS
# ============================================================================
 
class CurrentStateActivity(BaseModel):
    """
    Actual activity executed by the representative case.
 
    This information must originate from the event log.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    sequence: int
    step_id: str
    activity_name: str
    stage: str
 
    owner_team: Optional[str] = None
    user_id: Optional[str] = None
 
    duration_minutes: float = 0
    waiting_minutes: float = 0
 
    manual: bool = False
    rework: bool = False
    exception: bool = False
    handoff: bool = False
 
    system_used: List[str] = Field(default_factory=list)
 
    @field_validator("system_used")
    @classmethod
    def clean_systems(cls, value):
        return [
            str(x).strip()
            for x in value
            if x and str(x).strip() and str(x).strip().upper() != "N/A"
        ]
 
 
class RecommendedAgent(BaseModel):
    """
    Agent definition coming from recommendation_agent.py.
 
    No new agents should be created by this script.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    agent_id: str
    name: str
    purpose: str
    problem_addressed: str
    impact: str
 
    why_this_agent: str
 
    action: List[str]
    trigger: List[str]
    systems: List[str]
 
    human_in_loop: bool
    human_in_loop_detail: str
 
    expected_outcome: str
    sequence_order: int
 
 
class ActivityTransformation(BaseModel):
    """
    Maps one actual representative-case activity to its To-Be treatment.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    current_step_id: str
    current_activity: str
 
    treatment: str
    """
    Allowed conceptual treatments:
    RETAIN
    AUTOMATE
    AUGMENT
    CONSOLIDATE
    ELIMINATE
    EXCEPTION_ONLY
    HUMAN_REVIEW
    """
 
    to_be_activity: str
 
    agent_id: Optional[str] = None
 
    rationale: str
 
    value_added: bool = False
    reduced_or_removed: bool = False
 
 
class ToBeActivity(BaseModel):
    """
    Activity in the proposed To-Be workflow.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    sequence: int
    activity_name: str
    activity_type: str
 
    source_current_steps: List[str]
 
    agent_id: Optional[str] = None
 
    owner: str
 
    system: List[str]
 
    human_in_loop: bool
 
    description: str
 
 
class SystemIntegration(BaseModel):
    """
    System integration restricted to systems observed in the
    representative-case event log.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    system_name: str
    purpose: str
    integration_type: str
    used_by_agents: List[str]
 
 
class ToBeDesign(BaseModel):
    """
    Complete validated To-Be transformation design.
    """
 
    model_config = ConfigDict(extra="forbid")
 
    status: str = "SUCCESS"
 
    case_id: str
    application_id: Optional[str] = None
 
    process_stage: str
 
    executive_summary: str
 
    objective: str
 
    kpi_context: List[str]
 
    gap_references: List[str]
 
    key_focus_areas: List[str]
 
    current_state_activities: List[CurrentStateActivity]
 
    to_be_activities: List[ToBeActivity]
 
    activity_transformations: List[ActivityTransformation]
 
    reduced_activities: List[str]
 
    retained_activities: List[str]
 
    value_added_activities: List[str]
 
    recommended_agents: List[RecommendedAgent]
 
    integrations: List[SystemIntegration]
 
    human_in_loop_points: List[str]
 
    expected_business_outcomes: List[str]
 
    architecture_layers: List[str]
 
    agent_workflow: List[str]
 
    diagram_nodes: List[Dict[str, Any]]
 
    diagram_edges: List[Dict[str, Any]]
 
    @field_validator("reduced_activities")
    @classmethod
    def clean_reduced(cls, value):
        return [x.strip() for x in value if x and x.strip()]
 
    @field_validator("retained_activities")
    @classmethod
    def clean_retained(cls, value):
        return [x.strip() for x in value if x and x.strip()]
 
 
# ============================================================================
# NORMALIZATION HELPERS
# ============================================================================
 
def normalize_systems(value: Any) -> List[str]:
    """
    Convert event-log system_used into a clean list.
 
    Examples:
        "LOS/SAP" -> ["LOS", "SAP"]
        ["LOS", "SAP"] -> ["LOS", "SAP"]
        "N/A" -> []
    """
 
    if value is None:
        return []
 
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).split("/")
 
    result = []
 
    for item in raw:
        item = str(item).strip()
 
        if not item:
            continue
 
        if item.upper() == "N/A":
            continue
 
        if item not in result:
            result.append(item)
 
    return result
 
 
def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
 
    return str(value).strip().upper() in {
        "Y",
        "YES",
        "TRUE",
        "1",
    }
 


def build_current_state_activities(events, representative_case=None):
    """
    Convert event_log rows into CurrentStateActivity objects.
    """

    representative_case = representative_case or {}
    activities = []

    for idx, event in enumerate(events, start=1):
        # Build systems list
        systems = []

        if event.get("system_used"):
            systems.append(str(event["system_used"]))

        if event.get("source_system") and event["source_system"] not in systems:
            systems.append(str(event["source_system"]))

        activity = CurrentStateActivity(
            sequence=idx,

            step_id=str(
                event.get("step_id")
                or event.get("step")
                or ""
            ),

            activity_name=str(
                event.get("activity_name")
                or event.get("activity")
                or ""
            ),

            stage=str(
                event.get("stage")
                or representative_case.get("process_stage")
                or ""
            ),

            owner_team=event.get("owner_team"),

            user_id=event.get("user_id"),

            duration_minutes=float(
                event.get("duration_minutes") or 0
            ),

            waiting_minutes=float(
                event.get("waiting_time_minutes")
                or event.get("waiting_minutes")
                or 0
            ),

            manual=normalize_bool(
                event.get("manual_touch_flag")
                or event.get("manual")
            ),

            rework=normalize_bool(
                event.get("rework_flag")
                or event.get("rework")
            ),

            exception=normalize_bool(
                event.get("exception_flag")
                or event.get("exception")
            ),

            handoff=normalize_bool(
                event.get("handoff_flag")
                or event.get("handoff")
            ),

            system_used=systems,
        )

        activities.append(activity)

    return activities

def get_current_state_activities(
    conn,
    process_instance_id: str,
    stage: str
):
    sql = f"""
        SELECT
            event_id,
            process_instance_id,
            application_id,
            customer_id,
            step_id,
            activity_name,
            stage,
            event_type,
            owner_team,
            role_name,
            role_id,
            user_id,
            system_used,
            system_id,
            source_system,
            timestamp_start,
            timestamp_end,
            duration_minutes,
            waiting_time_minutes,
            open_days_as_of_extract,
            status,
            decision_value,
            exception_flag,
            exception_reason,
            rework_flag,
            rework_loop_type,
            handoff_flag,
            manual_touch_flag,
            gap_reference,
            previous_step_id,
            next_step_id,
            loaded_at
        FROM corporate_loan.event_log
        WHERE process_instance_id = %s
          AND stage = %s
        ORDER BY timestamp_start
    """

    events = db.rows(
        conn,
        sql,
        (process_instance_id, stage)
    )

    return build_current_state_activities(events)
 
# ============================================================================
# REPRESENTATIVE CASE EXTRACTION
# ============================================================================
 
def extract_representative_case_context(
    representative_case: Dict[str, Any],
    stage_events: List[Dict[str, Any]],
) -> Tuple[
    List[CurrentStateActivity],
    List[str]
]:
    """
    Converts raw representative-case event records into the strict
    CurrentStateActivity structure.

    IMPORTANT:
    This function does NOT retrieve events from the whole population.
 
    It receives only the events belonging to the selected representative case.
    """
 
    activities = []
 
    observed_systems = set()

    process_instance_id =representative_case["case_id"]
 
    for idx, event in enumerate(stage_events, start=1):
 
        systems = normalize_systems(
            event.get("system_used")
            or event.get("systems")
        )
 
        observed_systems.update(systems)
    with db.connection() as conn:
        activities = get_current_state_activities(
                    conn,
                    process_instance_id,
                    representative_case.get("process_stage", "")
            )
    return activities, sorted(observed_systems)
 
 
# ============================================================================
# AGENT VALIDATION
# ============================================================================
 
def validate_recommended_agents(
    recommended_agents: List[Dict[str, Any]]
) -> List[RecommendedAgent]:
    """
    Converts recommendation_agent.py output into strict Pydantic objects.
 
    This prevents the To-Be agent from inventing a new agent.
    """
 
    validated = []
 
    for agent in recommended_agents:
 
        validated.append(
            RecommendedAgent.model_validate(agent)
        )
 
    return validated
 
 
# ============================================================================
# DETERMINISTIC SYSTEM RESTRICTION
# ============================================================================
 
def restrict_agent_systems_to_event_log(
    agents: List[RecommendedAgent],
    observed_systems: List[str]
) -> List[RecommendedAgent]:
    """
    Restricts agent systems to systems actually observed in the
    representative-case event log.
 
    If recommendation_agent.py proposes a system that is NOT observed
    in the representative case, it is removed from the integration
    context.
    """
 
    observed = {
        x.strip().lower()
        for x in observed_systems
    }
 
    output = []
 
    for agent in agents:
 
        permitted = []
 
        for system in agent.systems:
 
            if system.strip().lower() in observed:
                permitted.append(system)
 
        agent_copy = agent.model_copy(
            update={
                "systems": permitted
            }
        )
 
        output.append(agent_copy)
 
    return output
 
 
# ============================================================================
# DETERMINISTIC METRICS
# ============================================================================
 
def calculate_current_state_metrics(
    activities: List[CurrentStateActivity]
) -> Dict[str, Any]:
 
    return {
        "activity_count": len(activities),
 
        "manual_activity_count": sum(
            1 for x in activities if x.manual
        ),
 
        "rework_activity_count": sum(
            1 for x in activities if x.rework
        ),
 
        "exception_activity_count": sum(
            1 for x in activities if x.exception
        ),
 
        "handoff_count": sum(
            1 for x in activities if x.handoff
        ),
 
        "total_waiting_minutes": round(
            sum(x.waiting_minutes for x in activities),
            1,
        ),
 
        "total_processing_minutes": round(
            sum(x.duration_minutes for x in activities),
            1,
        ),
    }
 
 
def calculate_to_be_metrics(
    current_count: int,
    transformations: List[ActivityTransformation],
    to_be_activities: List[ToBeActivity],
) -> Dict[str, Any]:
 
    reduced = sum(
        1
        for x in transformations
        if x.reduced_or_removed
    )
 
    return {
        "current_activity_count": current_count,
 
        "to_be_activity_count": len(
            to_be_activities
        ),
 
        "reduced_activity_count": reduced,
 
        "activity_reduction_pct": (
            round(
                reduced / current_count * 100,
                1
            )
            if current_count
            else 0
        ),
    }
 
 
# ============================================================================
# PROMPT
# ============================================================================
 
def build_prompt(
    representative_case: Dict[str, Any],
    current_activities: List[CurrentStateActivity],
    tier1_kpi: Dict[str, Any],
    tier2_kpi: Dict[str, Any],
    gap_references: List[str],
    agents: List[RecommendedAgent],
    observed_systems: List[str],
) -> str:
 
    current_json = [
        x.model_dump()
        for x in current_activities
    ]
 
    agent_json = [
        x.model_dump()
        for x in agents
    ]
    print("agent_json: ", agent_json)
 
    return f"""
You are the To-Be Transformation Design Agent for Deloitte Kinetic.
 
Your responsibility is to transform ONE representative case for ONE
specific process stage.
 
You MUST follow the rules below.
 
============================================================
RULE 1 - REPRESENTATIVE CASE ONLY
============================================================
 
The Current State must be based ONLY on the supplied representative
case and its supplied stage events.
 
Do not describe a generic process.
 
Do not infer activities from other cases.
 
Do not invent activities.
 
============================================================
RULE 2 - RECOMMENDED AGENTS
============================================================
 
You may ONLY use the agents supplied under recommended_agents.
 
Do not create another agent.
 
Do not rename an agent.
 
Do not add an agent.
 
Do not recommend an agent that is not present in the supplied list.
 
============================================================
RULE 3 - SYSTEMS
============================================================
 
The only systems allowed in the To-Be design are:
 
{json.dumps(observed_systems, indent=2)}
 
Do not introduce SAP, Salesforce, Oracle, ServiceNow, email,
Excel, APIs or any other system unless it appears in the supplied
observed-system list.
 
============================================================
RULE 4 - KPI CONTEXT
============================================================
 
Tier 1 KPI:
 
{json.dumps(tier1_kpi, indent=2, default=str)}
 
Tier 2 KPI:
 
{json.dumps(tier2_kpi, indent=2, default=str)}
 
Gap references:
 
{json.dumps(gap_references, indent=2)}
 
Use these to explain WHY the transformation is being proposed.
 
============================================================
RULE 5 - REPRESENTATIVE CASE
============================================================
 
Representative case:
 
{json.dumps(representative_case, indent=2, default=str)}
 
============================================================
RULE 6 - CURRENT STATE
============================================================
 
Actual activities:
 
{json.dumps(current_json, indent=2)}
 
Preserve the actual activity sequence.
 
============================================================
RULE 7 - RECOMMENDED AGENTS
============================================================
 
{json.dumps(agent_json, indent=2)}
 
============================================================
TO-BE DESIGN
============================================================
 
For every relevant current activity determine whether it should be:
 
RETAIN
AUTOMATE
AUGMENT
CONSOLIDATE
ELIMINATE
EXCEPTION_ONLY
HUMAN_REVIEW
 
Create the To-Be workflow.
 
The To-Be workflow must show:
 
Current activity
        ->
Transformation
        ->
To-Be activity
        ->
Agent if applicable
        ->
Human review if applicable
        ->
System if applicable
 
============================================================
IMPORTANT
============================================================
 
Do not claim a quantified benefit such as:
 
"reduces cycle time by 40%"
"reduces effort by 60%"
"reduces cost by $20K"
 
unless that number is explicitly provided in the input.
 
You may describe qualitative outcomes.
 
============================================================
OUTPUT
============================================================
 
Return JSON matching the supplied Pydantic schema.
 
Include:
 
executive_summary
objective
kpi_context
gap_references
key_focus_areas
current_state_activities
to_be_activities
activity_transformations
reduced_activities
retained_activities
value_added_activities
recommended_agents
integrations
human_in_loop_points
expected_business_outcomes
architecture_layers
agent_workflow
diagram_nodes
diagram_edges
"""
 
 
# ============================================================================
# OPENAI CALL
# ============================================================================
 
# def call_to_be_llm(
#     prompt: str
# ) -> Dict[str, Any]:
 
#     client = create_client()
 
#     response = client.chat.completions.create(
#         model=MODEL,
 
#         messages=[
#             {
#                 "role": "system",
#                 "content": (
#                     "You are a deterministic transformation design "
#                     "assistant. Return only valid JSON."
#                 ),
#             },
#             {
#                 "role": "user",
#                 "content": prompt,
#             },
#         ],
 
#         temperature=0,
 
#         response_format={
#             "type": "json_object"
#         },
 
#         max_tokens=MAX_TOKENS,
#     )
 
#     content = response.choices[0].message.content
 
#     if not content:
#         raise RuntimeError(
#             "To-Be design agent returned an empty response."
#         )
 
#     return json.loads(content)
 

def call_to_be_llm(prompt: str) -> Dict[str, Any]:

    client = create_client()

    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "executive_summary": {"type": "string"},
            "objective": {"type": "string"},
            "kpi_context": {
                "type": "array",
                "items": {"type": "string"}
            },
            "gap_references": {
                "type": "array",
                "items": {"type": "string"}
            },
            "key_focus_areas": {
                "type": "array",
                "items": {"type": "string"}
            },
            "to_be_activities": {
                "type": "array"
            },
            "activity_transformations": {
                "type": "array"
            },
            "reduced_activities": {
                "type": "array",
                "items": {"type": "string"}
            },
            "retained_activities": {
                "type": "array",
                "items": {"type": "string"}
            },
            "value_added_activities": {
                "type": "array",
                "items": {"type": "string"}
            },
            "integrations": {
                "type": "array"
            },
            "human_in_loop_points": {
                "type": "array",
                "items": {"type": "string"}
            },
            "expected_business_outcomes": {
                "type": "array",
                "items": {"type": "string"}
            },
            "architecture_layers": {
                "type": "array",
                "items": {"type": "string"}
            },
            "agent_workflow": {
                "type": "array",
                "items": {"type": "string"}
            },
            "diagram_nodes": {
                "type": "array"
            },
            "diagram_edges": {
                "type": "array"
            }
        },
        "required": [
            "status",
            "executive_summary",
            "objective",
            "kpi_context",
            "gap_references",
            "key_focus_areas",
            "to_be_activities",
            "activity_transformations",
            "reduced_activities",
            "retained_activities",
            "value_added_activities",
            "integrations",
            "human_in_loop_points",
            "expected_business_outcomes",
            "architecture_layers",
            "agent_workflow",
            "diagram_nodes",
            "diagram_edges"
        ]
    }

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "system",
                "content": """
Return ONLY valid JSON.

IMPORTANT:

status must be string.

kpi_context must be List[str].

reduced_activities must be List[str].

retained_activities must be List[str].

value_added_activities must be List[str].

agent_workflow must be List[str].

For every to_be_activity use EXACTLY:

{
  "sequence": int,
  "activity_name": str,
  "activity_type": str,
  "source_current_steps": [str],
  "agent_id": str | null,
  "owner": str,
  "system": [str],
  "human_in_loop": bool,
  "description": str
}

For every activity_transformation use EXACTLY:

IMPORTANT:
For every integration use EXACTLY:
{
"system_name": str,
"purpose": str,
"integration_type": str,
"used_by_agents": [str]
}

{
  "current_step_id": str,
  "current_activity": str,
  "treatment": str,
  "to_be_activity": str,
  "agent_id": str | null,
  "rationale": str,
  "value_added": bool,
  "reduced_or_removed": bool
}
"""
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        response_format={"type": "json_object"}
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError(
            "To-Be design agent returned empty response."
        )

    result = json.loads(content)

    print("=" * 100)
    print(json.dumps(result, indent=2))
    print("=" * 100)

    return result
 
# ============================================================================
# DIAGRAM GENERATION
# ============================================================================
 
def build_mermaid_diagram(
    design: ToBeDesign
) -> str:
 
    lines = [
        "flowchart TD",
        "    START([Representative Case])",
    ]
 
    previous = "START"
 
    for idx, activity in enumerate(
        design.to_be_activities,
        start=1,
    ):
 
        node_id = f"A{idx}"
 
        label = (
            f"{activity.activity_name}"
        )
 
        if activity.agent_id:
            label += f"<br/>Agent: {activity.agent_id}"
 
        if activity.human_in_loop:
            label += "<br/>Human Review"
 
        lines.append(
            f'    {node_id}["{label}"]'
        )
 
        lines.append(
            f"    {previous} --> {node_id}"
        )
 
        previous = node_id
 
    lines.append(
        f"    {previous} --> END([Stage Complete])"
    )
 
    return "\n".join(lines)
 
 
# ============================================================================
# MAIN DESIGN FUNCTION
# ============================================================================
 
def create_to_be_design(
    representative_case: Dict[str, Any],
    stage_events: List[Dict[str, Any]],
    tier1_kpi: Dict[str, Any],
    tier2_kpi: Dict[str, Any],
    gap_references: List[str],
    recommended_agents: List[Dict[str, Any]],
) -> Dict[str, Any]:
 
    # ------------------------------------------------------------
    # 1. REPRESENTATIVE CASE VALIDATION
    # ------------------------------------------------------------
 
    case_id = (
        representative_case.get("case_id")
        or representative_case.get("process_instance_id")
    )
 
    if not case_id:
 
        return {
            "status": "NO_REPRESENTATIVE_CASE",
            "message": (
                "To-Be design cannot be generated because no "
                "representative case was supplied."
            ),
        }
 
    process_stage = (
        representative_case.get("process_stage")
        or representative_case.get("stage")
    )
 
    if not process_stage:
 
        raise ValueError(
            "Representative case does not contain process_stage."
        )
 
    # ------------------------------------------------------------
    # 2. EXTRACT ACTUAL REPRESENTATIVE-CASE ACTIVITIES
    # ------------------------------------------------------------
 
    current_activities, observed_systems = (
        extract_representative_case_context(
            representative_case,
            stage_events,
        )
    )
 
    if not current_activities:
 
        return {
            "status": "NO_STAGE_EVENTS",
            "case_id": case_id,
            "process_stage": process_stage,
            "message": (
                "Representative case exists, but no event-log "
                "activities were found for the requested stage."
            ),
        }
 
    # ------------------------------------------------------------
    # 3. VALIDATE RECOMMENDED AGENTS
    # ------------------------------------------------------------
 
    agents = validate_recommended_agents(
        recommended_agents
    )
 
    if not agents:
 
        return {
            "status": "NO_RECOMMENDED_AGENTS",
            "case_id": case_id,
            "process_stage": process_stage,
            "message": (
                "No recommended agents were supplied by "
                "recommendation_agent.py."
            ),
        }
 
    # ------------------------------------------------------------
    # 4. SYSTEM RESTRICTION
    # ------------------------------------------------------------
 
    agents = restrict_agent_systems_to_event_log(
        agents,
        observed_systems,
    )
 
    # ------------------------------------------------------------
    # 5. CURRENT STATE METRICS
    # ------------------------------------------------------------
 
    current_metrics = calculate_current_state_metrics(
        current_activities
    )
 
    # ------------------------------------------------------------
    # 6. BUILD LLM CONTEXT
    # ------------------------------------------------------------
 
    prompt = build_prompt(
        representative_case=representative_case,
 
        current_activities=current_activities,
 
        tier1_kpi=tier1_kpi,
 
        tier2_kpi=tier2_kpi,
 
        gap_references=gap_references,
 
        agents=agents,
 
        observed_systems=observed_systems,
    )
 
    # ------------------------------------------------------------
    # 7. LLM TRANSFORMATION DESIGN
    # ------------------------------------------------------------
 
    raw_design = call_to_be_llm(prompt)
 
    # ------------------------------------------------------------
    # 8. FORCE INPUT VALUES THAT MUST NEVER BE INVENTED
    # ------------------------------------------------------------
 
    raw_design["case_id"] = case_id
 
    raw_design["application_id"] = (
        representative_case.get("application_id")
    )
 
    raw_design["process_stage"] = process_stage
 
    raw_design["current_state_activities"] = [
        x.model_dump()
        for x in current_activities
    ]
 
    raw_design["recommended_agents"] = [
        x.model_dump()
        for x in agents
    ]
 
    raw_design["gap_references"] = gap_references
 
    # ------------------------------------------------------------
    # 9. VALIDATE COMPLETE RESPONSE
    # ------------------------------------------------------------
 
    design = ToBeDesign.model_validate(
        raw_design
    )
 
    # ------------------------------------------------------------
    # 10. VALIDATE SYSTEM INTEGRATIONS
    # ------------------------------------------------------------
 
    observed_lower = {
        x.lower()
        for x in observed_systems
    }
 
    for integration in design.integrations:
 
        if (
            integration.system_name.lower()
            not in observed_lower
        ):
            raise ValueError(
                f"Invalid system integration detected: "
                f"{integration.system_name}. "
                f"This system was not present in the "
                f"representative-case event log."
            )
 
    # ------------------------------------------------------------
    # 11. VALIDATE AGENTS AGAINST RECOMMENDATION OUTPUT
    # ------------------------------------------------------------
 
    allowed_agent_ids = {
        x.agent_id
        for x in agents
    }
 
    for activity in design.to_be_activities:
 
        if (
            activity.agent_id
            and activity.agent_id not in allowed_agent_ids
        ):
            raise ValueError(
                f"To-Be design introduced an unapproved agent: "
                f"{activity.agent_id}"
            )
 
    for transformation in design.activity_transformations:
 
        if (
            transformation.agent_id
            and transformation.agent_id not in allowed_agent_ids
        ):
            raise ValueError(
                f"Activity transformation introduced an "
                f"unapproved agent: "
                f"{transformation.agent_id}"
            )
 
    # ------------------------------------------------------------
    # 12. CALCULATE TO-BE METRICS
    # ------------------------------------------------------------
 
    to_be_metrics = calculate_to_be_metrics(
        current_count=len(current_activities),
 
        transformations=design.activity_transformations,
 
        to_be_activities=design.to_be_activities,
    )
 
    # ------------------------------------------------------------
    # 13. ADD DETERMINISTIC METRICS TO OUTPUT
    # ------------------------------------------------------------
 
    result = design.model_dump()
 
    result["current_state_metrics"] = current_metrics
 
    result["to_be_metrics"] = to_be_metrics
 
    result["observed_systems"] = observed_systems
 
    result["representative_case"] = {
        "case_id": case_id,
        "application_id": representative_case.get(
            "application_id"
        ),
        "stage": process_stage,
    }
 
    # ------------------------------------------------------------
    # 14. GENERATE DIAGRAM
    # ------------------------------------------------------------
 
    # result["mermaid_diagram"] = build_mermaid_diagram(
    #     design
    # )

    result["mermaid_diagram"] = build_mermaid_diagram(
        design
    )

    try:
        png_file, png_base64 = (
            generate_png_from_diagram(
                design.diagram_nodes,
                design.diagram_edges,
                design.case_id,
            )
        )

        result["diagram_png_file"] = png_file

        result["diagram_png_base64"] = png_base64

    except Exception as ex:

        result["diagram_png_file"] = None

        result["diagram_png_base64"] = None

        result["diagram_generation_error"] = str(
            ex
        )
    result["status"] = "SUCCESS"
 
    return result
 
