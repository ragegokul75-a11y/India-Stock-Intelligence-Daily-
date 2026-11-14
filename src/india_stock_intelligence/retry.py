"""Small bounded retry helper for idempotent, read-only operations."""

import logging
import sqlite3
import time
from datetime import datetime, timezone
from collections.abc import Callable
from collections.abc import Iterator
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)
TRANSIENT_READ_ERRORS = (TimeoutError, ConnectionError, sqlite3.OperationalError)


def retry_read_only(operation: Callable, *, attempts: int = 3, initial_delay: float = 0.25,
                    retryable: tuple[type[BaseException], ...] = TRANSIENT_READ_ERRORS):
    """Retry transient read failures only; callers must not pass a mutating operation."""
    if attempts < 1 or attempts > 5:
        raise ValueError("attempts must be between 1 and 5")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except retryable:
            if attempt == attempts:
                raise
            delay = initial_delay * (2 ** (attempt - 1))
            logger.warning("Read-only operation failed; retry %d/%d in %.2fs", attempt, attempts - 1, delay)
            time.sleep(delay)


_PERMANENT_ERROR_NAMES = {
    "AuthenticationError",
    "PermissionDeniedError",
    "BadRequestError",
    "InvalidRequestError",
    "OutputValidationError",
    "PaidAPICallNotApproved",
}
_TRANSIENT_ERROR_NAMES = {
    "APITimeoutError",
    "APIConnectionError",
    "TimeoutException",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "RemoteDisconnected",
}


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_retryable_agent_error(exc: BaseException) -> bool:
    """Classify only known transient agent/API failures as safe to retry."""
    chain = list(_exception_chain(exc))
    if isinstance(exc, (PermissionError, ValueError, TypeError, AssertionError)):
        return False
    if any(type(error).__name__ in _PERMANENT_ERROR_NAMES for error in chain):
        return False

    statuses: list[int] = []
    for error in chain:
        for candidate in (
            getattr(error, "status_code", None),
            getattr(getattr(error, "response", None), "status_code", None),
        ):
            try:
                if candidate is not None:
                    statuses.append(int(candidate))
            except (TypeError, ValueError):
                continue
    if any(status in {401, 403} or 400 <= status < 500 and status != 429 for status in statuses):
        return False
    if any(status == 429 or 500 <= status <= 599 for status in statuses):
        return True
    if any(isinstance(error, (TimeoutError, ConnectionError)) for error in chain):
        return True
    return any(type(error).__name__ in _TRANSIENT_ERROR_NAMES for error in chain)


def agent_retry_delay(exc: BaseException, attempt: int, *, base_delay: float = 0.25,
                      max_delay: float = 30.0) -> float:
    """Honor a numeric/date Retry-After header, otherwise use bounded exponential backoff."""
    for error in _exception_chain(exc):
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            continue
        retry_after = next(
            (value for key, value in headers.items() if str(key).lower() == "retry-after"),
            None,
        )
        if retry_after is None:
            continue
        try:
            return min(max(float(retry_after), 0.0), max_delay)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return min(max(seconds, 0.0), max_delay)
            except (TypeError, ValueError, OverflowError):
                pass
    return min(base_delay * (2 ** max(attempt - 1, 0)), max_delay)
