"""Safe observation metadata; no retry, send or completion policy."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import re

_PHASE = ContextVar("projection_phase", default="unspecified")
_SAFE_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")


@contextmanager
def projection_phase(name):
    token = _PHASE.set(name)
    try:
        yield
    finally:
        _PHASE.reset(token)


def current_projection_phase():
    return _PHASE.get()


def safe_error_metadata(value):
    if not isinstance(value, dict):
        return {}
    return {key: (item if isinstance(item, str) and _SAFE_CODE.fullmatch(item)
                 and not item.startswith(("eyJ", "sk-")) else "<redacted>")
            for key, item in value.items() if key in {"code", "type"}}


def parse_upstream_retry_after(value, *, now=None, fallback=60):
    """HTTP delta-seconds or HTTP-date, with explicit fallback provenance."""
    fallback_result = {"seconds": fallback, "source": "bridge_fallback",
                       "reason": "missing_upstream_header" if value is None else "invalid_upstream_header",
                       "upstream_header": None}
    if not isinstance(value, str) or len(value) > 128:
        return fallback_result
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,10}", value):
        return {"seconds": int(value), "source": "upstream_seconds",
                "reason": None, "upstream_header": value}
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return fallback_result
        now = now or datetime.now(timezone.utc)
        seconds = max(0, math.ceil((date - now).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return fallback_result
    # Reformat the parsed date; do not preserve arbitrary header contents.
    from email.utils import format_datetime
    return {"seconds": seconds, "source": "upstream_http_date", "reason": None,
            "upstream_header": format_datetime(date.astimezone(timezone.utc), usegmt=True)}
