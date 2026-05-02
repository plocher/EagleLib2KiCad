"""Shared diagnostic helpers for E2K Behave step modules."""

from __future__ import annotations


def pending_step(step_name: str) -> None:
    """Raise a clear placeholder error for unimplemented step behavior."""
    raise NotImplementedError(f"Step stub not yet implemented: {step_name}")
