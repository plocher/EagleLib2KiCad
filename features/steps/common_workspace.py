"""Shared context and table helpers for E2K Behave step modules."""

from __future__ import annotations

from typing import Any


def state(context: Any) -> dict[str, Any]:
    """Return the mutable scenario state bag used by step definitions."""
    if not hasattr(context, "e2k_state"):
        context.e2k_state = {}
    return context.e2k_state


def table_rows(context: Any) -> list[dict[str, str]]:
    """Convert the active Behave table to a list of row dictionaries."""
    if context.table is None:
        return []
    return [dict(row.items()) for row in context.table]


def record_table(context: Any, key: str) -> None:
    """Store table rows for a step under a stable state key."""
    state(context)[key] = table_rows(context)
