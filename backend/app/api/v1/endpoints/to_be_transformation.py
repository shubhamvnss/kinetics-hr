import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.repositories import to_be_transformation_repository as jobs
from app.services.to_be_transformation_jobs import enqueue


logger = logging.getLogger(__name__)
router = APIRouter(tags=["To-Be Transformation"])


@router.get("/root-causes/{root_cause_id}/to-be-transformation")
def to_be_transformation(
    root_cause_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    retry: bool = Query(False),
):
    try:
        state = jobs.get_job(root_cause_id)
        if state and state["status"] == "completed":
            result = state.get("result")
            if isinstance(result, str):
                import json
                result = json.loads(result)
            return JSONResponse(status_code=200, content=result)

        if state and state["status"] == "failed" and not retry:
            return _failed_response(state, root_cause_id, request.url.path)

        if jobs.claim_job(root_cause_id, retry_failed=retry):
            background_tasks.add_task(enqueue, root_cause_id)

        state = jobs.get_job(root_cause_id)
        if state is None:
            raise RuntimeError("The transformation job could not be created.")
        if state["status"] == "completed":
            result = state.get("result")
            if isinstance(result, str):
                import json
                result = json.loads(result)
            return JSONResponse(status_code=200, content=result)
        if state["status"] == "failed":
            return _failed_response(state, root_cause_id, request.url.path)

        return JSONResponse(
            status_code=202,
            headers={"Retry-After": "3"},
            content={
                "status": "running",
                "root_cause_id": root_cause_id,
                "case_id": state.get("case_id"),
                "message": "The To-Be transformation is running in the background.",
                "poll_url": request.url.path,
                "retry_after_seconds": 3,
                "started_at": _iso(state.get("started_at")),
                "updated_at": _iso(state.get("updated_at")),
            },
        )
    except Exception as error:
        logger.exception("Could not read or start To-Be job for root cause %s", root_cause_id)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "transformation_job_store_unavailable",
                "message": "The transformation job could not be read or started. Retry shortly.",
            },
        ) from error


@router.get("/root-causes/{root_cause_id}/to-be-design")
def to_be_design(
    root_cause_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    retry: bool = Query(False),
):
    return to_be_transformation(
        root_cause_id=root_cause_id,
        background_tasks=background_tasks,
        request=request,
        retry=retry,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _failed_response(state: dict, root_cause_id: str, poll_url: str) -> JSONResponse:
    error_code = state.get("error_code") or "transformation_failed"
    status_code = {
        "RootCauseNotFound": 404,
        "InsufficientEvidence": 422,
        "NoImpactedCases": 422,
        "NoRepresentativeCase": 422,
    }.get(error_code, 500)
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "failed",
            "root_cause_id": root_cause_id,
            "case_id": state.get("case_id"),
            "error": error_code,
            "message": "The To-Be transformation failed. Use retry=true to start it again.",
            "retry_url": f"{poll_url}?retry=true",
            "finished_at": _iso(state.get("finished_at")),
        },
    )
