from __future__ import annotations

import time
from collections.abc import Callable


class TransientNSEError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def is_retryable_source_error(error: BaseException) -> bool:
    if isinstance(error, (PermissionError, ValueError, TypeError)):
        return False
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(error, "code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status is not None and 400 <= status < 500 and status != 429:
        return False
    return status == 429 or status is not None and 500 <= status <= 599


def retry_source_call(operation: Callable[[], object], *, attempts: int = 3,
                      base_delay: float = 0.5, max_delay: float = 8.0,
                      sleep: Callable[[float], None] = time.sleep):
    """Bound retries to transient transport/rate-limit/server failures only."""
    if not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5")
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts or not is_retryable_source_error(exc):
                raise
            retry_after = getattr(exc, "retry_after", None)
            try:
                delay = min(max(float(retry_after), 0), max_delay) if retry_after is not None else min(base_delay * 2 ** (attempt - 1), max_delay)
            except (TypeError, ValueError, OverflowError):
                delay = min(base_delay * 2 ** (attempt - 1), max_delay)
            sleep(delay)
