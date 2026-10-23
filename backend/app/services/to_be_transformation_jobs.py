from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from app.repositories import to_be_transformation_repository as jobs
from app.services.to_be_transformation_service import run


logger = logging.getLogger(__name__)
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="to-be-transformation")


def _execute(root_cause_id: str) -> None:
    try:
        result = run(
            root_cause_id,
            on_case_selected=lambda case_id: jobs.update_case_id(root_cause_id, case_id),
        )
        case_id = (result.get("summary") or {}).get("case_id")
        jobs.complete_job(root_cause_id, case_id, result)
    except Exception as error:
        logger.exception("Background To-Be transformation failed for root cause %s", root_cause_id)
        try:
            jobs.fail_job(root_cause_id, type(error).__name__)
        except Exception:
            logger.exception("Could not persist failure for root cause %s", root_cause_id)


def enqueue(root_cause_id: str) -> None:
    try:
        _worker.submit(_execute, root_cause_id)
    except RuntimeError:
        logger.exception("Background worker is unavailable for root cause %s", root_cause_id)
        jobs.fail_job(root_cause_id, "background_worker_unavailable")