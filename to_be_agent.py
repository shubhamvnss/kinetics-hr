"""
backend.agents.to_be_transformation_agent - To-Be Designer.

Maps every current-state activity of a representative case onto a future-state disposition
(retain / automate / hand to an AI agent / combine / eliminate / keep human), using ONLY the
agents recommendation_agent.py already deterministically selected for this Tier 1 / Tier 2 / Gap
combination. This module does not select agents - it describes how the already-approved agents
touch the case's real activities.

Same defence-in-depth pattern as recommendation_agent.py: the prompt asks for the rules, the
Pydantic schema constrains the shape, and the functions below constrain the content against what
is actually true for this case (allowed agent names, systems that actually appear in this case's
event log, and full coverage of the current-state activities).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from .. import config
from ..to_be_schemas import NO_SYSTEM_IN_LOG, TO_BE_RESPONSE_FORMAT, ToBeDesignLLM


class ToBeError(RuntimeError):
    """The To-Be design could not be produced or validated."""


SYSTEM_PROMPT = """CORPORATE LOAN - TO-BE TRANSFORMATION DESIGNER (Deloitte Kinetic)

ROLE
You take one representative case's ACTUAL current-state activity sequence and redesign it into a
future-state sequence, using ONLY the AI agents that a deterministic routing layer has already
selected for this root cause. You do not select, invent, rename or add agents - you decide how the
already-approved agents (and plain deterministic automation, and human steps) touch each real
activity that this case actually went through.

INPUT
The evidence pack gives you: the complete current-state activity list for this case and stage(s)
(each with a `sequence` number, the real activity name, stage, owner team, role, user, system used,
timestamps, duration, waiting time, and manual/rework/exception/handoff flags), the Tier 1 KPI,
Tier 2 KPI(s), documented gaps, the root cause, and `recommended_agents` - the exact, ordered,
already-approved agent list with their purpose, actions, triggers and systems.

ABSOLUTE RULES
1. Return exactly one activity object per current-state row. `current_sequence` must equal that
   row's `sequence` value. Every current-state row must appear exactly once - do not skip any, do
   not duplicate any, and do not add rows for activities that are not in the current-state payload.
2. `responsible_agent` must be exactly the `name` of one entry in `recommended_agents`, or null.
   Set it only when change_type is "AI Agent". Never invent an agent name, rename one, or use an
   agent whose purpose does not logically address this activity.
3. change_type must be one of: Retained, Automated (Deterministic), AI Agent, Combined,
   Eliminated, Human.
   - "Combined": give two or more current-state rows the SAME to_be_sequence and the SAME
     to_be_activity text - that is what marks them as consolidated into one future step.
   - "Eliminated": the activity becomes unnecessary. Say why in reason_for_change. Still give it a
     to_be_sequence (any value is fine - it will not be counted as a future-state step) and a short
     to_be_activity such as "N/A - eliminated".
   - Preserve activities that do not need to change as "Retained" or "Human" - do not force a
     change onto every row.
4. human_in_the_loop = true whenever the step communicates with a customer, overrides a credit or
   compliance decision, commits the bank, or is a formal approval. Otherwise false. This flag can
   apply to any change_type, including "AI Agent" and "Automated (Deterministic)".
5. system_used must contain ONLY systems that appear in `case_systems` (the systems this case's
   own event log actually shows), or the exact literal string
   "Client API / integration dependency - system not present in event log" when the right
   capability genuinely needs an integration this case's event log does not show. Never name a
   system (SAP, Salesforce, Oracle, etc.) that is not in case_systems.
6. Never invent a metric, number, case id or activity that is not in the evidence pack.
7. Ground reason_for_change and kpi_gap_addressed in the actual Tier 1/Tier 2 KPI, gap and root
   cause evidence supplied - do not write generic boilerplate detached from this case's evidence.
8. Preserve the actual current-state sequence in your reasoning; do not reorder history, only
   design the future state.

Write for a banking operations executive: concrete, calm and specific. No marketing language."""


def build_payload(context: dict) -> str:
    return json.dumps({
        "case_id": context.get("case_id"),
        "stages": context.get("stages"),
        "current_state": context.get("current_state"),
        "tier_1_kpi": context.get("tier1_kpi"),
        "tier_2_kpis": context.get("tier2_kpis"),
        "documented_gaps": context.get("gaps"),
        "root_cause": context.get("root_cause_summary"),
        "recommended_agents": context.get("recommended_agents"),
        "case_systems": context.get("case_systems"),
    }, separators=(",", ":"), default=str)


def _validate_coverage(plan: ToBeDesignLLM, current_state: List[dict]) -> Optional[str]:
    expected = {row["sequence"] for row in current_state}
    got = [a.current_sequence for a in plan.activities]
    got_set = set(got)
    if len(got) != len(set(got)):
        dupes = sorted({x for x in got if got.count(x) > 1})
        return f"duplicate current_sequence values in the response: {dupes}"
    missing = sorted(expected - got_set)
    extra = sorted(got_set - expected)
    if missing:
        return f"missing current_sequence values that must each appear exactly once: {missing}"
    if extra:
        return f"current_sequence values that do not exist in current_state: {extra}"
    return None


def _invented_agents(plan: ToBeDesignLLM, allowed_names: List[str]) -> List[str]:
    lookup = {n.strip().lower() for n in allowed_names}
    offenders = []
    for a in plan.activities:
        if a.responsible_agent and a.responsible_agent.strip().lower() not in lookup:
            if a.responsible_agent not in offenders:
                offenders.append(a.responsible_agent)
    return offenders


def _enforce_agent_consistency(plan: ToBeDesignLLM) -> None:
    """responsible_agent only makes sense when change_type is AI Agent."""
    for a in plan.activities:
        if a.change_type != "AI Agent":
            a.responsible_agent = None


def _invented_systems(plan: ToBeDesignLLM, allowed_systems: List[str]) -> List[str]:
    if not allowed_systems:
        return []
    lookup = {s.strip().lower() for s in allowed_systems}
    offenders = []
    for a in plan.activities:
        for system in a.system_used:
            if system == NO_SYSTEM_IN_LOG:
                continue
            if system.strip().lower() not in lookup and system not in offenders:
                offenders.append(system)
    return offenders


def _strip_invented_systems(plan: ToBeDesignLLM, allowed_systems: List[str]) -> None:
    lookup = {s.strip().lower() for s in allowed_systems}
    for a in plan.activities:
        a.system_used = [s for s in a.system_used if s == NO_SYSTEM_IN_LOG or s.strip().lower() in lookup]


def _call_model(client, payload: str, correction: Optional[str]) -> str:
    user_input = payload if not correction else (
        f"{payload}\n\nYour previous answer was rejected: {correction}\n"
        f"Return a corrected answer that satisfies the schema and every absolute rule."
    )
    kwargs = {
        "model": config.MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": [{"role": "user", "content": user_input}],
        "text": {"format": TO_BE_RESPONSE_FORMAT},
        "max_output_tokens": config.MAX_OUTPUT_TOKENS,
    }
    if config.EFFORT and config.EFFORT != "default":
        kwargs["reasoning"] = {"effort": config.EFFORT}

    response = client.responses.create(**kwargs)

    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "unknown")
        raise ToBeError(f"The model returned an incomplete response ({reason}).")
    for item in getattr(response, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                raise ToBeError(f"The model declined the request: {block.refusal}")
    return (getattr(response, "output_text", "") or "").strip()


def generate(context: dict, client=None) -> Tuple[ToBeDesignLLM, str]:
    """Produce a validated To-Be design covering every current-state activity exactly once."""
    client = client or config.llm_client()
    current_state = context.get("current_state") or []
    if not current_state:
        raise ToBeError("No current-state activities were supplied to design a To-Be view from.")

    allowed_names = list(context.get("allowed_agent_names") or [])
    allowed_systems = list(context.get("case_systems") or [])
    payload = build_payload(context)
    correction, last_error = None, None

    for attempt in (1, 2):
        try:
            raw = _call_model(client, payload, correction)
            if not raw:
                raise ValueError("the model returned no text")

            plan = ToBeDesignLLM.model_validate_json(raw)

            coverage_error = _validate_coverage(plan, current_state)
            if coverage_error:
                raise ValueError(coverage_error)

            _enforce_agent_consistency(plan)

            offenders = _invented_agents(plan, allowed_names)
            if offenders:
                raise ValueError(
                    f"these agents are not in recommended_agents: {', '.join(offenders)}. "
                    f"responsible_agent must exactly match a recommended_agents name, or be null."
                )

            system_offenders = _invented_systems(plan, allowed_systems)
            if system_offenders:
                if attempt == 1:
                    raise ValueError(
                        f"these systems do not appear in case_systems: {', '.join(system_offenders)}. "
                        f"Use only systems from case_systems, or the literal "
                        f"'{NO_SYSTEM_IN_LOG}'."
                    )
                _strip_invented_systems(plan, allowed_systems)

            return plan, config.model_label()

        except ToBeError:
            raise
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            correction = last_error[:1500]

    raise ToBeError(f"The To-Be design could not be validated: {last_error}")
