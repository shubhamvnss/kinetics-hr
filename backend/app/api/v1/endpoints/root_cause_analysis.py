from fastapi import APIRouter

from app.services.root_cause_service import RootCauseService, normalize_root_cause_id

router = APIRouter()


@router.get(
    "/root-cause",
    tags=["Root Cause Analysis"]
)
async def get_root_cause(root_cause_id: str | None = None):
    analysis = RootCauseService.get_root_cause_analysis()
    if not analysis:
        return analysis

    if root_cause_id:
        normalized = normalize_root_cause_id(root_cause_id)
        candidates = []
        if isinstance(analysis, dict):
            summary = (analysis.get('frontend_summary') or {}).get('analysis_summary') or {}
            for block in summary.get('critical_kpis') or []:
                for cause in block.get('top_root_causes') or []:
                    if isinstance(cause, dict):
                        candidates.append(cause)
            for block in analysis.get('tier1_root_causes') or []:
                for cause in block.get('root_causes') or []:
                    if isinstance(cause, dict):
                        candidates.append(cause)
        for candidate in candidates:
            candidate_id = normalize_root_cause_id(candidate.get('root_cause_id'))
            if candidate_id == normalized or str(candidate.get('rank')) == str(root_cause_id):
                return candidate
        return {"root_cause_id": normalized or root_cause_id, "status": "not_found", "message": "Root cause not found in the current analysis."}

    return analysis


@router.get(
    "/root-causes/{root_cause_id}",
    tags=["Root Cause Analysis"],
    include_in_schema=False,
)
async def get_root_cause_by_id(root_cause_id: str):
    return await get_root_cause(root_cause_id)
