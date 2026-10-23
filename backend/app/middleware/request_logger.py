import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestLoggingMiddleware(
    BaseHTTPMiddleware
):

    async def dispatch(
        self,
        request,
        call_next
    ):

        start_time = time.time()

        response = await call_next(request)

        process_time = round(
            time.time() - start_time,
            3
        )

        logger.info(
            f"{request.method} "
            f"{request.url.path} "
            f"status={response.status_code} "
            f"time={process_time}s"
        )

        return response
