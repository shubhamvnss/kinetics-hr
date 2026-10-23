"""
backend.routers.to_be_transformation - the Current-State vs To-Be-State HTTP surface.

    GET  /api/root-causes/{root_cause_id}/to-be-transformation    the transformation view
    POST /api/root-causes/to-be-transformation                    same, body-driven

Mirrors backend/routers/fix_with_ai.py's error-mapping convention: domain exceptions become clean
HTTP responses, stack traces stay in the server log.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from .. import db
from ..agents.fix_plan_orchestrator import InsufficientEvidence, RootCauseNotFound
from ..agents.orchestrator import (NoImpactedCases, NoRepresentativeCase,
                                                        ToBeGenerationError, run)


log = logging.getLogger("kinetic.to_be_transformation")
router = APIRouter(prefix="/api", tags=["to-be-transformation"])


class ToBeTransformationRequest(BaseModel):
    root_cause_id: str
    run_id: Optional[int] = None


def _fail(exc: Exception, root_cause_id: str):
    if isinstance(exc, RootCauseNotFound):
        raise HTTPException(status_code=404, detail={
            "error": "root_cause_not_found", "root_cause_id": root_cause_id, "message": str(exc)})
    if isinstance(exc, InsufficientEvidence):
        raise HTTPException(status_code=422, detail={
            "error": "insufficient_evidence", "root_cause_id": root_cause_id, "message": str(exc)})
    if isinstance(exc, NoImpactedCases):
        raise HTTPException(status_code=422, detail={
            "error": "no_impacted_cases", "root_cause_id": root_cause_id, "message": str(exc)})
    if isinstance(exc, NoRepresentativeCase):
        raise HTTPException(status_code=422, detail={
            "error": "no_representative_case", "root_cause_id": root_cause_id, "message": str(exc)})
    if isinstance(exc, ToBeGenerationError):
        log.exception("To-Be transformation failed for %s", root_cause_id)
        raise HTTPException(status_code=502, detail={
            "error": "to_be_generation_failed", "root_cause_id": root_cause_id,
            "message": "The To-Be transformation view could not be generated."})
    log.exception("To-Be transformation failed for %s", root_cause_id)
    raise HTTPException(status_code=500, detail={
        "error": "internal_error", "root_cause_id": root_cause_id,
        "message": "The To-Be transformation view could not be completed."})


def _execute(root_cause_id: str, run_id: Optional[int]) -> dict:
    try:
        with db.connection() as conn:
            return run(conn, root_cause_id, run_id=run_id)
    except HTTPException:
        raise
    except Exception as exc:
        _fail(exc, root_cause_id)


@router.get("/root-causes/{root_cause_id}/to-be-transformation")
def to_be_transformation(root_cause_id: str, run_id: Optional[int] = Query(None)):
    return _execute(root_cause_id, run_id)


@router.post("/root-causes/to-be-transformation")
def to_be_transformation_post(body: ToBeTransformationRequest = Body(...)):
    return _execute(body.root_cause_id, body.run_id)
