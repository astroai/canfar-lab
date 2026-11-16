"""Timestamps for the sync mailbox protocol (UTC, filesystem-sortable)."""

from __future__ import annotations

from datetime import UTC, datetime

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def now() -> datetime:
    return datetime.now(UTC)


def utc_iso() -> str:
    return now().strftime(ISO_FORMAT)


def stamp() -> str:
    return now().strftime(STAMP_FORMAT)


def parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


__all__ = ["ISO_FORMAT", "STAMP_FORMAT", "now", "parse_iso", "stamp", "utc_iso"]
