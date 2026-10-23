"""Kinetic-native Fix-with-AI recommendations.

The reference implementation expects the event-log warehouse used by that project. Kinetic
deployments also contain a persisted root-cause JSON document, so this endpoint deliberately
uses that document as a safe fallback when the reference tables are not installed.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.agents.recommendation_agent import deterministic_agents, build_fallback_plan
from app.core.config import settings
from app.core.database import get_connection
from app.schemas.response import RecommendationLLM, LLM_RESPONSE_FORMAT
from app.services.impact_service import calculate_impact_from_root_cause
from app.services.process_context import build_process_context, DEFAULT_EVENT_SYSTEMS
from app.services.representative_case import select_representative_case
from app.services.root_cause_service import normalize_root_cause_id
from app.services.audit_service import history as audit_history, new_execution_id, start, complete, fail

router = APIRouter(tags=["Fix with AI"])

CACHE_VERSION = settings.CACHE_VERSION


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _normalize_candidate_id(value: Any) -> str | None:
    return normalize_root_cause_id(value)


def _iter_root_causes(payload: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    summary = ((payload.get("frontend_summary") or {}).get("analysis_summary") or payload.get("analysis_summary") or payload)
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for kpi in summary.get("critical_kpis") or []:
        if not isinstance(kpi, dict):
            continue
        for index, cause in enumerate(kpi.get("top_root_causes") or kpi.get("likely_drivers") or []):
            if isinstance(cause, str):
                cause = {"root_cause": cause}
            if isinstance(cause, dict):
                candidate = {**cause}
                candidate.setdefault("root_cause_id", f"{kpi.get('kpi_id', 'kpi')}-{cause.get('rank', index + 1)}")
                candidate.setdefault("rank", cause.get("rank", index + 1))
                rows.append((candidate, kpi))
    for block in payload.get("tier1_root_causes") or []:
        if not isinstance(block, dict):
            continue
        kpi = block.get("tier1_kpi") or {}
        for index, cause in enumerate(block.get("root_causes") or []):
            if isinstance(cause, str):
                cause = {"root_cause": cause}
            if isinstance(cause, dict):
                candidate = {**cause}
                candidate.setdefault("root_cause_id", f"{kpi.get('kpi_id', 'kpi')}-{cause.get('rank', index + 1)}")
                candidate.setdefault("rank", cause.get("rank", index + 1))
                rows.append((candidate, kpi))
    return rows


def _find_root_cause(payload: dict[str, Any], root_cause_id: str) -> tuple[dict, dict]:
    target = normalize_root_cause_id(root_cause_id)
    for cause, kpi in _iter_root_causes(payload):
        candidate = _normalize_candidate_id(cause.get("root_cause_id") or f"{kpi.get('kpi_id', 'kpi')}-{cause.get('rank', 1)}")
        if candidate == target or str(cause.get("rank", 1)) == str(root_cause_id):
            return {**cause, "root_cause_id": candidate or cause.get("root_cause_id") or target, "rank": cause.get("rank", 1)}, kpi
        legacy_values = [
            cause.get("id"),
            cause.get("root_cause_id"),
            cause.get("root_cause"),
            f"{kpi.get('kpi_id', 'kpi')}-{cause.get('rank', 1)}",
        ]
        for legacy in legacy_values:
            if legacy and normalize_root_cause_id(legacy) == target:
                return {**cause, "root_cause_id": candidate or target, "rank": cause.get("rank", 1)}, kpi
    raise HTTPException(404, detail={"error": "root_cause_not_found", "root_cause_id": root_cause_id,
                                     "message": f"Root cause '{root_cause_id}' was not found."})


def _validate_recommendation(plan: dict[str, Any], systems: list[str], routing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise HTTPException(400, detail={"error": "invalid_recommendation", "message": "Recommendation plan must be an object."})
    summary = str(plan.get("summary") or "").strip()
    agents = plan.get("agents") or plan.get("agent") or []
    if not agents and any(plan.get(key) for key in ("agent_id", "name", "purpose")):
        agents = [plan]
    if isinstance(agents, dict):
        agents = [agents]
    if not isinstance(agents, list):
        raise HTTPException(400, detail={"error": "invalid_recommendation", "message": "Recommendation plan 'agents' must be a list."})

    def text_items(value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([value] if value else [])
        result = []
        for item in values:
            if isinstance(item, dict):
                item = item.get("description") or item.get("text") or item.get("name") or json.dumps(item)
            result.append(str(item))
        return result

    normalized_agents: list[dict[str, Any]] = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get("agent_id") or agent.get("id") or f"agent-{index + 1}")
        name = str(agent.get("name") or agent.get("agent_id") or agent_id)
        purpose = str(agent.get("purpose") or agent.get("responsibility") or "Validate the recommended process adjustment.")
        systems_for_agent = [str(item) for item in (agent.get("systems") or []) if str(item).strip()]
        if systems_for_agent and any(system not in systems for system in systems_for_agent):
            agent["systems"] = [system for system in systems_for_agent if system in systems]
        normalized_agents.append(
            {
                "agent_id": agent_id,
                "name": name,
                "purpose": purpose,
                "problem_addressed": str(agent.get("problem_addressed") or agent.get("issue") or summary or "Process issue"),
                "impact": str(agent.get("impact") or "Improves the matched evidence and reduces operational friction."),
                "action": text_items(agent.get("action") or agent.get("actions")),
                "trigger": text_items(agent.get("trigger") or agent.get("triggers")),
                "systems": list(agent.get("systems") or systems),
                "human_in_loop": bool(agent.get("human_in_loop", True)),
                "human_in_loop_detail": str(agent.get("human_in_loop_detail") or "A reviewer validates the recommendation before operational action."),
                "expected_outcome": str(agent.get("expected_outcome") or "A governed recommendation is delivered for review."),
                "sequence_order": int(agent.get("sequence_order") or index + 1),
                "why_this_agent": str(agent.get("why_this_agent") or f"{name} addresses the diagnosed {summary or 'process pattern'}."),
            }
        )

    expected = (routing.get("agents") or []) if routing else []
    if expected:
        # Deterministic routing is the source of truth for how many agents we expose.
        if len(expected) > 3:
            expected = expected[:3]
        if len(expected) < 2:
            raise ValueError("Deterministic routing must provide between 2 and 3 agents")

        # Pad or trim Azure-provided agents to match deterministic routing.
        while len(normalized_agents) < len(expected):
            idx = len(normalized_agents)
            selected = expected[idx] if idx < len(expected) and isinstance(expected[idx], dict) else {}
            name = str((selected or {}).get("name") or f"Agent {idx + 1}")
            normalized_agents.append(
                {
                    "agent_id": str((selected or {}).get("agent_id") or f"agent-{idx + 1}"),
                    "name": name,
                    "purpose": str((selected or {}).get("purpose") or "Validate the recommended process adjustment."),
                    "problem_addressed": summary or "Process issue",
                    "impact": "Improves the matched evidence and reduces operational friction.",
                    "action": ["Validate feasibility and implementation impact."],
                    "trigger": ["Triggered when the diagnosed pattern is detected in new cases."],
                    "systems": list((selected or {}).get("systems") or systems),
                    "human_in_loop": True,
                    "human_in_loop_detail": "A reviewer validates the recommendation before operational action.",
                    "expected_outcome": "A governed recommendation is delivered for review.",
                    "sequence_order": idx + 1,
                    "why_this_agent": f"{name} addresses the diagnosed {summary or 'process pattern'}.",
                }
            )

        if len(normalized_agents) > len(expected):
            normalized_agents = normalized_agents[: len(expected)]

        for index, (normalized, selected) in enumerate(
            zip(normalized_agents, expected), start=1
        ):
            selected = selected if isinstance(selected, dict) else {}
            normalized["agent_id"] = str(selected.get("agent_id") or normalized.get("agent_id") or f"agent-{index}")
            normalized["name"] = str(selected.get("name") or normalized.get("name") or f"Agent {index}")
            if not normalized.get("purpose"):
                normalized["purpose"] = str(selected.get("purpose") or "Validate the recommended process adjustment.")
            normalized["sequence_order"] = index
    else:
        # No deterministic routing – rely solely on Azure but still cap at 3 agents.
        if len(normalized_agents) > 3:
            normalized_agents = normalized_agents[:3]

    # Allow any number of Azure-provided agents (including none). The UI will
    # display whatever the model returned instead of forcing a deterministic
    # fallback when the agent count is outside 2–3.

    return {
        "summary": summary or "",
        "agents": normalized_agents,
    }


def _fallback(cause: dict, kpi: dict) -> dict:
    issue = cause.get("root_cause") or cause.get("title") or "Unspecified process issue"
    evidence = cause.get("statistics") or cause.get("impact_statistics") or []
    affected = cause.get("affected_cases") or {}
    count = affected.get("count") if isinstance(affected, dict) else None
    percentage = affected.get("percentage") if isinstance(affected, dict) else None
    total_cases = int(count or 0)
    if percentage and count:
        total_cases = max(int(round((count * 100) / percentage)), count)
    impact = calculate_impact_from_root_cause({**cause, "affected_cases": affected, "total_cases": total_cases}, {
        "total_cases": total_cases,
        "impact_percentage": percentage,
        "impact_statistics": evidence,
    })
    system_names = [str(item).strip() for item in (cause.get("systems") or []) if str(item).strip()]
    if not system_names:
        system_names = list(DEFAULT_EVENT_SYSTEMS)

    root_cause_id = normalize_root_cause_id(cause.get("root_cause_id") or f"{kpi.get('kpi_id', 'T1-01')}-{cause.get('rank', 1)}") or f"RC-{kpi.get('kpi_id', 'T1-01')}-{cause.get('rank', 1)}"
    root_cause_payload = {
        **cause,
        "root_cause_id": root_cause_id,
        "tier1_kpi_id": kpi.get("kpi_id") or cause.get("tier1_kpi_id"),
        "tier2_kpi_ids": cause.get("linked_tier2_kpis") or cause.get("tier2_kpi_ids") or cause.get("tier2_kpis") or [],
        "gap_references": cause.get("gap_references") or cause.get("gaps") or [],
        "systems": system_names,
    }
    context = {
        "root_cause": root_cause_payload,
        "tier1_kpi": kpi,
        "tier2_kpis": root_cause_payload["tier2_kpi_ids"],
        "gaps": root_cause_payload["gap_references"],
        "systems": system_names,
    }
    routing = deterministic_agents(context)
    representative_case = select_representative_case(root_cause_payload)
    process_context = build_process_context(
        root_cause_payload,
        kpi,
        [{"kpi_id": item} for item in root_cause_payload["tier2_kpi_ids"]],
        [{"gap_reference": item} for item in root_cause_payload["gap_references"]],
    )

    return {
        "execution_id": "FIX-" + hashlib.sha256(f"{issue}{datetime.now().timestamp()}".encode()).hexdigest()[:8].upper(),
        "run_id": None,
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.AZURE_OPENAI_DEPLOYMENT or "gpt-5.1",
        "root_cause": root_cause_payload,
        "impact": impact,
        "representative_case": representative_case,
        "routing": routing,
        "process_context": process_context,
    }


def _parse_azure_content(content: Any) -> dict[str, Any]:
    """Parse Azure content into a JSON object.

    If the model returns plain text instead of JSON, surface it as a summary so
    the frontend can still display whatever information Azure returned instead
    of falling back to a deterministic plan.
    """
    generated = _json(content)
    if isinstance(generated, dict):
        return generated

    cleaned = str(content or "").strip()
    if not cleaned:
        return {}

    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        # Non-JSON Azure output: return it as a best-effort summary so the UI
        # can display the raw model answer instead of triggering deterministic
        # fallback.
        return {"summary": cleaned}

    if not isinstance(parsed, dict):
        # Treat non-object JSON (arrays, scalars) as a summary wrapper.
        return {"summary": cleaned}

    return parsed


def _validate_future_state(value: Any) -> dict[str, list[dict[str, Any]]]:
    if isinstance(value, dict):
        raw_steps = value.get("steps")
    elif isinstance(value, list):
        raw_steps = value
    else:
        raw_steps = None
    if not isinstance(raw_steps, list):
        raise ValueError("Azure response is missing future_state.steps")
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "id": str(step.get("id") or f"S{index + 1}"),
                "name": str(step.get("name") or f"Step {index + 1}"),
                "type": str(step.get("type") or "process"),
                "description": str(step.get("description") or ""),
            }
        )
    if not steps:
        raise ValueError("Azure response contains no usable future-state steps")
    return {"steps": steps}


def _build_default_future_state(systems: list[str], agents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    steps: list[dict[str, Any]] = []
    # Baseline existing process step
    steps.append(
        {
            "id": "S1",
            "name": "Current process baseline",
            "type": "existing",
            "description": "Current as-is process for the diagnosed root cause.",
        }
    )

    # One step per agent, up to three total steps
    for index, agent in enumerate(agents, start=2):
        if index > 4:
            break
        steps.append(
            {
                "id": f"S{index}",
                "name": f"Deploy {agent.get('name', 'AI agent')}",
                "type": "ai_agent",
                "description": str(
                    agent.get("purpose")
                    or agent.get("problem_addressed")
                    or "Deploy governed AI agent into the process."
                ),
            }
        )

    if len(steps) < 2:
        steps.append(
            {
                "id": "S2",
                "name": "Review and govern recommendations",
                "type": "human",
                "description": "Loan-operations team reviews AI recommendations before implementation.",
            }
        )

    return {"steps": steps}


def _azure_completion(url: str, body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY or ""},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            output = payload.get("output") or {}
            choices = output.get("choices") or []
            if not choices:
                raise KeyError("Azure response is missing output.choices")
            message = choices[0].get("message") or {}
            parsed = message.get("parsed")
            if isinstance(parsed, dict):
                return parsed
            content = message.get("content")
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if text:
                            text_parts.append(str(text))
                content = "\n".join(text_parts)
            return _parse_azure_content(content)
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 429, 500, 502, 503, 504}
            if not retryable or attempt == 3:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            try:
                delay = float(retry_after) if retry_after else 2 ** attempt
            except (TypeError, ValueError):
                delay = 2 ** attempt
            time.sleep(min(max(delay, 1), 15))


def _azure_chat_completion(url: str, body: bytes) -> Any:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY or ""},
        method="POST",
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
            choices = payload.get("choices") or []
            if not choices:
                raise KeyError("Azure chat response is missing choices")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                text_parts: list[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if text:
                            text_parts.append(str(text))
                content = "\n".join(text_parts)
            return _parse_azure_content(content)
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 429, 500, 502, 503, 504}
            if not retryable or attempt == 3:
                # Capture Azure error body for diagnostics
                body_text = ""
                try:
                    body_bytes = error.read()
                    if body_bytes:
                        body_text = body_bytes.decode("utf-8", errors="ignore")
                except Exception:
                    body_text = ""
                if body_text:
                    raise RuntimeError(f"HTTP Error {error.code}: {body_text}") from error
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            try:
                delay = float(retry_after) if retry_after else 2 ** attempt
            except (TypeError, ValueError):
                delay = 2 ** attempt
            time.sleep(min(max(delay, 1), 15))


def _generate_with_azure(base: dict) -> dict:
    # The deterministic builder supplies evidence/context only; generated sections must come from Azure.
    base.pop("recommendation_plan", None)
    base.pop("future_state", None)
    base.pop("generation_mode", None)
    endpoint = (settings.AZURE_OPENAI_ENDPOINT or "").rstrip("/")
    api_key = settings.AZURE_OPENAI_API_KEY
    if not endpoint or not api_key:
        raise HTTPException(503, detail={"error": "azure_not_configured", "message": "Azure OpenAI is not configured."})

    responses_url = (
        f"{endpoint}/openai/deployments/{settings.AZURE_OPENAI_DEPLOYMENT}"
        f"/responses?api-version={settings.AZURE_OPENAI_API_VERSION}"
    )
    chat_url = (
        f"{endpoint}/openai/deployments/{settings.AZURE_OPENAI_DEPLOYMENT}"
        f"/chat/completions?api-version={settings.AZURE_OPENAI_API_VERSION}"
    )
    systems = [str(item) for item in (base["root_cause"].get("systems") or DEFAULT_EVENT_SYSTEMS)]
    routing = base.get("routing") or {}
    process_context = base.get("process_context") or {}

    prompt = {
        "root_cause": base["root_cause"],
        "impact": base["impact"],
        "representative_case": base.get("representative_case"),
        "process_context": process_context,
        "deterministic_agent_routing": routing,
        "allowed_systems": process_context.get("allowed_systems") or systems,
    }

    responses_body = json.dumps(
        {
            "model": settings.AZURE_OPENAI_DEPLOYMENT,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "You are a governed loan-operations remediation agent. "
                                "Use only the supplied deterministic context and allowed_systems. "
                                "Return JSON that strictly follows the provided json_schema."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Using the deterministic context, propose between 2 and 3 governed AI agents and a "
                                "concise future-state process to address the diagnosed root cause. "
                                "For each agent, fill every field with Deloitte-client-ready narrative: "
                                "purpose, problem_addressed, impact, why_this_agent, human_in_loop_detail, "
                                "expected_outcome, plus 3-6 specific actions and 2-4 precise triggers grounded "
                                "in the KPI gap, root-cause description, impact metrics, representative case and "
                                "allowed_systems. Do not invent new systems or KPIs. Respond only with JSON."
                            ),
                        },
                        {"type": "input_json", "input": prompt},
                    ],
                },
            ],
            "response_format": LLM_RESPONSE_FORMAT,
            "temperature": 0.0,
        },
        default=str,
    ).encode("utf-8")

    chat_body = json.dumps(
        {
            "model": settings.AZURE_OPENAI_DEPLOYMENT,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a governed loan-operations remediation agent. Return a single JSON object only.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "root_cause": base["root_cause"],
                            "impact": base["impact"],
                            "representative_case": base.get("representative_case"),
                            "process_context": process_context,
                            "deterministic_agent_routing": routing,
                            "allowed_systems": process_context.get("allowed_systems") or systems,
                            "instruction": (
                                "Propose between 2 and 3 governed AI agents and a concise future-state process "
                                "to address the diagnosed root cause. "
                                "For each agent, fill every field with Deloitte-client-ready narrative: "
                                "purpose, problem_addressed, impact, why_this_agent, human_in_loop_detail, "
                                "expected_outcome, plus 3-6 specific actions and 2-4 precise triggers grounded "
                                "in the KPI gap, root-cause description, impact metrics, representative case and "
                                "allowed_systems. Do not invent new systems or KPIs. "
                                "Respond ONLY with JSON containing recommendation_plan and future_state."
                            ),
                        },
                        default=str,
                    ),
                },
            ],
            "temperature": 0.1,
        },
        default=str,
    ).encode("utf-8")

    try:
        parsed = _azure_completion(responses_url, responses_body)
        try:
            plan = RecommendationLLM.model_validate(parsed)
        except ValidationError as exc:
            raise HTTPException(
                502,
                detail={
                    "error": "azure_unusable_response",
                    "message": "Azure OpenAI returned a response that did not match the expected schema.",
                },
            ) from exc

        if not 2 <= len(plan.agents) <= 3:
            raise HTTPException(
                502,
                detail={
                    "error": "azure_unusable_response",
                    "message": "Azure must return between 2 and 3 usable agents.",
                },
            )

        expected_agents = routing.get("agents") or []
        if expected_agents:
            if len(plan.agents) != len(expected_agents):
                raise HTTPException(
                    502,
                    detail={
                        "error": "azure_unusable_response",
                        "message": "Azure returned a different number of agents than deterministic routing.",
                    },
                )
            normalized_agents = []
            for agent, selected in zip(plan.agents, expected_agents):
                agent.agent_id = selected.get("agent_id", agent.agent_id)
                agent.name = selected.get("name", agent.name)
                normalized_agents.append(agent)
            plan.agents = normalized_agents

        case = base.get("representative_case")
        if case is not None:
            case["reason_selected"] = plan.representative_case_explanation

        base["recommendation_plan"] = {
            "summary": plan.summary,
            "agents": [a.model_dump() for a in plan.agents],
        }
        base["future_state"] = plan.future_state.model_dump()
        base["generation_mode"] = "azure_responses"
        base.pop("generation_error", None)
        return base
    except urllib.error.HTTPError as error:
        if error.code == 404:
            # Fallback for Azure resources that do not support the Responses API yet.
            try:
                chat_parsed = _azure_chat_completion(chat_url, chat_body)
                if not isinstance(chat_parsed, dict):
                    raise ValueError("Azure chat response must be a JSON object")

                plan_like = chat_parsed.get("recommendation_plan")
                # If the model returned agents/summary at the top level instead of nesting
                # them under recommendation_plan, treat the whole object as the plan.
                if not isinstance(plan_like, dict):
                    if any(key in chat_parsed for key in ("agents", "agent", "summary", "problem_addressed", "impact")):
                        plan_like = chat_parsed
                    else:
                        raise ValueError("Azure chat response is missing recommendation_plan and usable agent definitions")

                validated_plan = _validate_recommendation(plan_like, systems, routing)

                future_value = None
                if isinstance(chat_parsed, dict):
                    if "future_state" in chat_parsed:
                        future_value = chat_parsed["future_state"]
                    elif "futureState" in chat_parsed:
                        future_value = chat_parsed["futureState"]
                    elif "steps" in chat_parsed:
                        future_value = {"steps": chat_parsed["steps"]}

                if future_value is not None:
                    try:
                        validated_future = _validate_future_state(future_value)
                    except Exception:
                        validated_future = _build_default_future_state(systems, validated_plan.get("agents") or [])
                else:
                    validated_future = _build_default_future_state(systems, validated_plan.get("agents") or [])

                case = base.get("representative_case")
                if case is not None:
                    case["reason_selected"] = validated_plan.get(
                        "representative_case_explanation",
                    ) or "Representative case selected from deterministic pipeline."

                base["recommendation_plan"] = validated_plan
                base["future_state"] = validated_future
                base["generation_mode"] = "azure_openai"
                base.pop("generation_error", None)
                return base
            except Exception as fallback_error:
                # Deterministic, non-LLM fallback: preserve a governed plan even when
                # Azure chat returns an unexpected shape.
                deterministic = build_fallback_plan({"root_cause": base["root_cause"]}, base["root_cause"])
                agents = deterministic.get("agents") or []
                # Align systems with the deterministic event-log systems in scope.
                for index, agent in enumerate(agents, start=1):
                    agent["systems"] = systems
                    agent["sequence_order"] = index

                case = base.get("representative_case")
                if case is not None:
                    case["reason_selected"] = (
                        deterministic.get("representative_case_explanation")
                        or case.get("reason_selected")
                        or "Representative case selected from deterministic pipeline."
                    )

                summary = deterministic.get("summary")
                if not summary:
                    root_cause_title = base.get("root_cause", {}).get("title") or "the diagnosed process pattern"
                    summary = (
                        f"Address {root_cause_title} with a governed remediation plan that links KPI gaps, "
                        "root-cause evidence, and AI agents."
                    )

                base["recommendation_plan"] = {
                    "summary": summary,
                    "agents": agents,
                }
                base["future_state"] = deterministic.get("future_state") or _build_default_future_state(
                    systems, agents
                )
                base["generation_mode"] = "deterministic_fallback"
                base["generation_error"] = f"Azure OpenAI chat fallback failed: {fallback_error}"
                return base

        raise HTTPException(
            error.code,
            detail={"error": "azure_http_error", "message": f"Azure OpenAI request failed with HTTP {error.code}."},
        )
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(
            502,
            detail={"error": "azure_unusable_response", "message": "Azure OpenAI returned an unusable recommendation."},
        )


def _load() -> dict:
    conn = get_connection()
    try:
        schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'SELECT payload FROM "{schema}".corporate_loan_root_causes ORDER BY created_at DESC LIMIT 1')
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No root cause analysis found")
        return _json(row["payload"])
    finally:
        conn.close()


def _audit(response: dict) -> None:
    mode = response.get("generation_mode")
    if mode not in {"azure_openai", "azure_responses"}:
        # Deterministic fallback results are not persisted in ai_fix_audit, but they
        # are still returned to the caller so the UI has a usable plan.
        if mode == "deterministic_fallback":
            return
        raise HTTPException(
            500,
            detail={
                "error": "azure_response_required",
                "message": "Only a successful Azure OpenAI response can be stored.",
            },
        )

    # Tag stored responses with a cache version so older runs can be ignored
    # without needing to wipe the underlying audit table.
    response = {**response, "cache_version": CACHE_VERSION}

    conn = get_connection()
    try:
        schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{schema}".ai_fix_audit (
                execution_id TEXT PRIMARY KEY,
                root_cause_id TEXT NOT NULL,
                run_id INTEGER,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                response JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                error_message TEXT,
                impacted_case_count INTEGER,
                representative_case_id TEXT
            )''')
            for column_definition in (
                "run_id INTEGER",
                "model TEXT",
                "status TEXT NOT NULL DEFAULT 'completed'",
                "response JSONB",
                "completed_at TIMESTAMPTZ",
                "error_message TEXT",
                "impacted_case_count INTEGER",
                "representative_case_id TEXT",
            ):
                cur.execute(f'ALTER TABLE "{schema}".ai_fix_audit ADD COLUMN IF NOT EXISTS {column_definition}')
            cur.execute(
                f'''INSERT INTO "{schema}".ai_fix_audit
                    (execution_id, root_cause_id, model, status, response, completed_at,
                     impacted_case_count, representative_case_id)
                    VALUES (%s, %s, %s, 'completed', %s::jsonb, NOW(), %s, %s)
                    ON CONFLICT (execution_id) DO UPDATE SET
                      response = EXCLUDED.response, status = 'completed',
                      completed_at = EXCLUDED.completed_at''',
                (
                    response["execution_id"],
                    response["root_cause"]["root_cause_id"],
                    response.get("model"),
                    json.dumps(response, default=str),
                    (response.get("impact") or {}).get("impacted_cases"),
                    (response.get("representative_case") or {}).get("case_id"),
                ),
            )
        conn.commit()
    except Exception as error:
        conn.rollback()
        raise HTTPException(500, detail={"error": "azure_response_not_stored", "message": str(error)}) from error
    finally:
        conn.close()


def _has_two_to_three_agents(payload: dict[str, Any]) -> bool:
    plan = payload.get("recommendation_plan") if isinstance(payload, dict) else None
    if not isinstance(plan, dict):
        return False
    agents = plan.get("agents")
    return isinstance(agents, list) and 2 <= len(agents) <= 3 and all(isinstance(agent, dict) for agent in agents)


def _load_cached_fix(root_cause_id: str, expected_routing: dict[str, Any] | None = None) -> dict | None:
    try:
        conn = get_connection()
        schema = settings.DB_SCHEMA.replace('"', '""')
        with conn.cursor() as cur:
            cur.execute(
                f'''SELECT response FROM "{schema}".ai_fix_audit
                    WHERE root_cause_id = %s
                      AND response IS NOT NULL
                      AND response->>'generation_mode' IN ('azure_openai', 'azure_responses')
                    ORDER BY created_at DESC LIMIT 1''',
                (normalize_root_cause_id(root_cause_id),),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        payload = _json(row["response"] if isinstance(row, dict) and "response" in row else row[0])
        if not isinstance(payload, dict):
            return None
        # Only reuse cache entries written with the current cache version. Older
        # cache entries are effectively ignored so previous Azure runs are
        # treated as invalidated.
        if payload.get("cache_version") != CACHE_VERSION:
            return None
        if not _has_two_to_three_agents(payload):
            return None
        cached_routing = expected_routing or payload.get("routing") or {}
        expected = [item.get("agent_id") for item in cached_routing.get("agents") or [] if isinstance(item, dict)]
        actual = [item.get("agent_id") for item in payload["recommendation_plan"]["agents"]]
        if expected and actual != expected:
            return None
        return payload
    except Exception:
        return None


def _refresh_cached_representative_case(cached: dict, cause: dict, kpi: dict, root_cause_id: str) -> dict:
    """Keep the successful Azure plan cached, but never reuse stale case-selection data."""
    root_cause = {
        **cause,
        "root_cause_id": root_cause_id,
        "tier1_kpi_id": kpi.get("kpi_id") or cause.get("tier1_kpi_id"),
        "tier2_kpi_ids": cause.get("linked_tier2_kpis") or cause.get("tier2_kpi_ids") or cause.get("tier2_kpis") or [],
        "gap_references": cause.get("gap_references") or cause.get("gaps") or [],
    }
    refreshed = select_representative_case(root_cause)
    return {**cached, "representative_case": refreshed}


@router.post("/root-causes/{root_cause_id}/fix-with-ai")
@router.get("/root-causes/{root_cause_id}/fix-with-ai")
def fix_with_ai(root_cause_id: str):
    payload = _load()
    cause, kpi = _find_root_cause(payload, root_cause_id)
    canonical_id = normalize_root_cause_id(cause.get("root_cause_id") or root_cause_id) or root_cause_id
    tier2_ids = cause.get("linked_tier2_kpis") or cause.get("tier2_kpi_ids") or cause.get("tier2_kpis") or []
    gap_ids = cause.get("gap_references") or cause.get("gaps") or []
    expected_routing = deterministic_agents({
        "tier1_kpi": kpi,
        "tier2_kpis": tier2_ids,
        "gaps": gap_ids,
        "root_cause": {**cause, "tier1_kpi_id": kpi.get("kpi_id") or cause.get("tier1_kpi_id"), "tier2_kpi_ids": tier2_ids, "gap_references": gap_ids},
    })
    cached = _load_cached_fix(canonical_id, expected_routing)
    if cached:
        return _refresh_cached_representative_case(cached, cause, kpi, canonical_id)

    response = _fallback(cause, kpi)
    response = _generate_with_azure(response)
    response["root_cause_id"] = normalize_root_cause_id(response.get("root_cause", {}).get("root_cause_id") or root_cause_id) or root_cause_id
    response["root_cause"] = {**response.get("root_cause", {}), "root_cause_id": response["root_cause_id"]}
    _audit(response)
    return response
