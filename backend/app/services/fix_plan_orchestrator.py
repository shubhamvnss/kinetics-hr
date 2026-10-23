from app.services.recommendation_agent import build_fallback_plan, deterministic_agents


def orchestrate_fix_plan(context: dict, root_cause: dict | None = None) -> dict:
    return build_fallback_plan(context, root_cause)


__all__ = ['orchestrate_fix_plan', 'deterministic_agents', 'build_fallback_plan']
