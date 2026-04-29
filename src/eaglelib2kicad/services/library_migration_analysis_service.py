"""Importer-side migration analysis orchestration service.

This service remains in EagleLib2KiCad while policy is evolving. It consumes
context services and emits review-friendly analysis rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from eaglelib2kicad.services.eagle_library_context_service import EagleDeviceContext
from eaglelib2kicad.services.kicad_library_context_service import (
    KiCadLibraryContextSnapshot,
)


@dataclass(frozen=True)
class MigrationAnalysisRow:
    """One Eagle device migration analysis row."""

    device_key: str
    symbol_name: str
    package_name: str
    confidence: str
    review_required: bool
    reasons: tuple[str, ...]


class LibraryMigrationAnalysisService:
    """Analyze Eagle device contexts against loaded KiCad contexts."""

    def analyze(
        self,
        *,
        eagle_devices: Sequence[EagleDeviceContext],
        kicad_context: KiCadLibraryContextSnapshot,
    ) -> tuple[MigrationAnalysisRow, ...]:
        """Produce analysis rows for importer triage workflows."""
        known_symbol_names = {symbol.symbol_name for symbol in kicad_context.symbols}
        known_footprints = {footprint.footprint_name for footprint in kicad_context.footprints}
        rows: list[MigrationAnalysisRow] = []

        for device in eagle_devices:
            reasons: list[str] = []
            confidence = "medium"

            symbol_known = device.symbol_name in known_symbol_names
            footprint_known = not device.package_name or device.package_name in known_footprints

            if not symbol_known:
                reasons.append("symbol_not_present_in_loaded_kicad_libraries")
            if not footprint_known:
                reasons.append("package_not_present_in_loaded_kicad_footprint_libraries")

            if symbol_known and footprint_known:
                confidence = "high"
            elif not symbol_known and not footprint_known:
                confidence = "low"

            rows.append(
                MigrationAnalysisRow(
                    device_key=f"{device.deviceset_name}:{device.device_name}",
                    symbol_name=device.symbol_name,
                    package_name=device.package_name,
                    confidence=confidence,
                    review_required=bool(reasons),
                    reasons=tuple(reasons),
                )
            )

        return tuple(rows)

