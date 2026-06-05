"""Locale and time-context block for system prompts.

The Mela AI assistant is built for U.S. companies. Every system prompt is
prepended with:

  1. An American-English directive (spelling, units, date format).
  2. A time block stating the current date/time in the user's local timezone
     (defaulting to America/Chicago / Central Time when unknown).

The user's timezone is supplied by the browser via the ``X-User-Timezone``
request header (an IANA name like ``America/Chicago``). When the header is
missing or invalid, we fall back to ``America/Chicago``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/Chicago"  # Central Time (CST/CDT)


def _safe_zone(tz_name: Optional[str]):
    """Return a ZoneInfo for the requested IANA name or the default."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = (tz_name or "").strip()
    if name:
        try:
            return ZoneInfo(name), name
        except ZoneInfoNotFoundError:
            logger.debug("Unknown timezone %r, falling back to %s", name, DEFAULT_TIMEZONE)
        except Exception as e:
            logger.debug("ZoneInfo error for %r: %s", name, e)
    return ZoneInfo(DEFAULT_TIMEZONE), DEFAULT_TIMEZONE


def build_locale_block(tz_name: Optional[str] = None) -> str:
    """Return a system-prompt fragment with American-English + current time.

    The block is short, prescriptive, and safe to prepend to any system prompt.
    Uses U.S. conventions: month-day-year, 12-hour clock, ``$``, miles, °F.
    """
    zone, resolved = _safe_zone(tz_name)
    now = datetime.now(zone)
    # American format: "Monday, May 12, 2026 at 2:07 PM CDT"
    # %-d / %-I are POSIX-only; on Windows fall back to %#d / %#I, then plain.
    for fmt in (
        "%A, %B %-d, %Y at %-I:%M %p %Z",
        "%A, %B %#d, %Y at %#I:%M %p %Z",
        "%A, %B %d, %Y at %I:%M %p %Z",
    ):
        try:
            formatted = now.strftime(fmt)
            break
        except (ValueError, Exception):
            continue
    else:
        formatted = now.isoformat()

    iso = now.isoformat(timespec="seconds")

    return (
        "## Locale & Time\n"
        "- Use **American English** at all times: spelling (color, organize, analyze, "
        "behavior, center), grammar, and idiom. Do not use British/Commonwealth "
        "spellings (colour, organise, analyse, behaviour, centre).\n"
        "- Default units: U.S. customary (miles, feet, pounds, °F, USD `$`). "
        "Use the metric system only when the user asks or when the source data "
        "is metric, and convert when helpful.\n"
        "- Default date format: month-day-year (e.g., May 12, 2026 or 5/12/2026). "
        "Use 12-hour time with AM/PM unless the user requests 24-hour.\n"
        f"- Current local date and time: **{formatted}**.\n"
        f"- ISO 8601 timestamp: `{iso}` (timezone: `{resolved}`).\n"
        "- When the user says \"today\", \"tomorrow\", \"this week\", or any "
        "relative time, resolve it against the local time above \u2014 do not say "
        "you don't know the current date.\n"
    )


def resolve_user_timezone(request) -> str:
    """Pull the user's timezone from the FastAPI ``Request``.

    Looks for, in order:
      - ``request._user_timezone`` attribute (set by endpoint dependencies)
      - ``X-User-Timezone`` header
      - ``DEFAULT_TIMEZONE`` (America/Chicago)
    """
    if request is None:
        return DEFAULT_TIMEZONE
    tz = getattr(request, "_user_timezone", None)
    if tz:
        return tz
    try:
        hdr = request.headers.get("x-user-timezone") or request.headers.get(
            "X-User-Timezone"
        )
        if hdr:
            return hdr
    except Exception:
        pass
    return DEFAULT_TIMEZONE
