"""Importer-side migration analysis orchestration service.

This service remains in EagleLib2KiCad while policy is evolving. It consumes
context services and emits review-friendly analysis rows with:
- category pathways
- confidence tiers
- review queue semantics
- generalized pin-count and library-semantic heuristics
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from eaglelib2kicad.services.eagle_library_context_service import EagleDeviceContext
from eaglelib2kicad.services.kicad_library_context_service import (
    FootprintLibraryItem,
    KiCadLibraryContextSnapshot,
    SymbolContext,
)


@dataclass(frozen=True)
class MigrationAnalysisRow:
    """One Eagle device migration analysis row."""

    device_key: str
    pathway: str
    symbol_name: str
    package_name: str
    confidence: str
    review_queue: str
    review_required: bool
    reasons: tuple[str, ...]
    symbol_library: str = ""
    footprint_library: str = ""
    eagle_pin_count: int = 0
    symbol_pin_count: int = 0
    footprint_pad_count: int = 0


@dataclass(frozen=True)
class _SymbolMatch:
    """Resolved symbol context plus matching metadata."""

    context: SymbolContext
    resolution: str


@dataclass(frozen=True)
class _FootprintMatch:
    """Resolved footprint context plus matching metadata."""

    item: FootprintLibraryItem
    resolution: str


class LibraryMigrationAnalysisService:
    """Analyze Eagle device contexts against loaded KiCad contexts."""

    _PASSIVE_KEYWORDS = {
        "resistor",
        "capacitor",
        "inductor",
        "ferrite",
        "bead",
        "passive",
        "rcl",
    }
    _IC_KEYWORDS = {
        "ic",
        "opamp",
        "amplifier",
        "regulator",
        "mcu",
        "microcontroller",
        "processor",
        "pmic",
        "driver",
        "logic",
        "transistor",
        "diode",
        "adc",
        "dac",
    }
    _IC_PART_PREFIXES = ("tps", "lm", "lt", "ad", "max", "pic", "stm", "atmega")
    _CONNECTOR_MECHANICAL_KEYWORDS = {
        "connector",
        "header",
        "usb",
        "jack",
        "socket",
        "switch",
        "relay",
        "button",
        "mechanical",
        "mounting",
        "hole",
        "terminal",
        "jumper",
        "rj",
    }
    _POWER_CANONICAL_NAMES = {
        "gnd",
        "vcc",
        "vdd",
        "vss",
        "agnd",
        "dgnd",
        "pgnd",
        "vccio",
        "vddio",
        "vbus",
        "vdrive",
        "3v3",
        "5v",
        "12v",
        "vin",
        "vout",
        "vbat",
        "avcc",
        "dvcc",
    }
    _GENERIC_SYMBOL_ALIASES = {
        "rus": ("R",),
        "resistor": ("R",),
        "cus": ("C",),
        "capacitor": ("C",),
        "cappolarized": ("CP", "C_Polarized"),
        "lus": ("L",),
        "inductor": ("L",),
        "diode": ("D",),
        "led": ("LED",),
    }
    _PIN_MISMATCH_REASONS = {
        "symbol_pin_count_mismatch_with_eagle",
        "footprint_pad_count_mismatch_with_eagle",
    }
    _SEMANTIC_FALLBACK_REASON = "symbol_semantic_fallback_match_needs_review"
    _FOOTPRINT_FAMILY_FALLBACK_REASON = "footprint_family_fallback_match_needs_review"
    _POWER_SYMBOL_ALIASES = {
        "agnd": ("GNDA", "GND"),
        "dgnd": ("DGND", "GND"),
        "pgnd": ("PGND", "GND"),
        "vccio": ("VBUS", "VDRIVE", "VCC"),
        "vddio": ("VBUS", "VDRIVE", "VDD"),
    }
    _PACKAGE_FAMILY_LIBRARY_HINTS = {
        "dip": ("package_dip", "dip"),
        "soic": ("package_so", "so"),
        "qfp": ("package_qfp", "qfp"),
        "qfn": ("package_dfn_qfn", "qfn", "dfn"),
        "to": ("package_to", "to"),
        "sot": ("package_to_sot_smd", "sot"),
        "bga": ("package_bga", "bga"),
        "lga": ("package_lga", "lga"),
    }

    def analyze(
        self,
        *,
        eagle_devices: Sequence[EagleDeviceContext],
        kicad_context: KiCadLibraryContextSnapshot,
    ) -> tuple[MigrationAnalysisRow, ...]:
        """Produce analysis rows for importer triage workflows."""
        symbol_exact = self._index_symbols_by_exact(kicad_context.symbols)
        symbol_canonical = self._index_symbols_by_canonical(kicad_context.symbols)
        footprint_exact = self._index_footprints_by_exact(kicad_context.footprints)
        footprint_canonical = self._index_footprints_by_canonical(kicad_context.footprints)
        rows: list[MigrationAnalysisRow] = []

        for device in eagle_devices:
            pathway = self._classify_pathway(device)
            package_name = device.package_name.strip()
            eagle_pin_count = self._derive_eagle_pin_count(device)
            symbol_match = self._resolve_symbol_match(
                device=device,
                pathway=pathway,
                symbol_exact=symbol_exact,
                symbol_canonical=symbol_canonical,
                eagle_pin_count=eagle_pin_count,
            )
            footprint_match = self._resolve_footprint_match(
                device=device,
                pathway=pathway,
                package_name=package_name,
                footprint_exact=footprint_exact,
                footprint_canonical=footprint_canonical,
                footprint_items=kicad_context.footprints,
                eagle_pin_count=eagle_pin_count,
            )
            symbol_known = symbol_match is not None
            footprint_known = not package_name or footprint_match is not None
            symbol_pin_count = symbol_match.context.pin_count if symbol_match is not None else 0
            footprint_pad_count = (
                footprint_match.item.pad_count if footprint_match is not None else 0
            )

            reasons: list[str] = []
            if not symbol_known:
                reasons.append("symbol_not_present_in_loaded_kicad_libraries")
            if package_name and not footprint_known:
                reasons.append("package_not_present_in_loaded_kicad_footprint_libraries")
            if symbol_match is not None and symbol_match.resolution in {
                "power_alias",
                "connector_family",
            }:
                reasons.append(self._SEMANTIC_FALLBACK_REASON)
            if footprint_match is not None and footprint_match.resolution == "family":
                reasons.append(self._FOOTPRINT_FAMILY_FALLBACK_REASON)
            if (
                symbol_known
                and eagle_pin_count > 0
                and symbol_pin_count > 0
                and symbol_pin_count != eagle_pin_count
            ):
                reasons.append("symbol_pin_count_mismatch_with_eagle")
            if (
                footprint_known
                and footprint_match is not None
                and eagle_pin_count > 0
                and footprint_pad_count > 0
                and footprint_pad_count != eagle_pin_count
            ):
                reasons.append("footprint_pad_count_mismatch_with_eagle")
            if pathway == "uncategorized":
                reasons.append("category_not_classified_for_policy_pathway")

            confidence = self._derive_confidence(
                pathway=pathway,
                package_name=package_name,
                symbol_known=symbol_known,
                footprint_known=footprint_known,
                reasons=reasons,
            )
            review_queue = self._derive_review_queue(
                pathway=pathway,
                confidence=confidence,
                reasons=reasons,
            )
            rows.append(
                MigrationAnalysisRow(
                    device_key=f"{device.deviceset_name}:{device.device_name}",
                    pathway=pathway,
                    symbol_name=device.symbol_name,
                    package_name=package_name,
                    confidence=confidence,
                    review_queue=review_queue,
                    review_required=review_queue != "none",
                    reasons=tuple(reasons),
                    symbol_library=(
                        symbol_match.context.library_nickname
                        if symbol_match is not None
                        else ""
                    ),
                    footprint_library=(
                        footprint_match.item.library_nickname
                        if footprint_match is not None
                        else ""
                    ),
                    eagle_pin_count=eagle_pin_count,
                    symbol_pin_count=symbol_pin_count,
                    footprint_pad_count=footprint_pad_count,
                )
            )

        return tuple(rows)

    def _classify_pathway(self, device: EagleDeviceContext) -> str:
        """Classify one Eagle device into a migration policy pathway."""
        if self._looks_like_power_symbol(device):
            return "schematic_annotation"
        if self._looks_like_connector_reference(device):
            return "connector_switch_mechanical"

        tokens = self._device_tokens(device)
        token_set = set(tokens)
        if token_set.intersection(self._PASSIVE_KEYWORDS):
            return "commodity_passive"
        if token_set.intersection(self._IC_KEYWORDS) or any(
            self._looks_like_ic_part_number(token) for token in token_set
        ):
            return "ic_regulator_specialty"
        if token_set.intersection(self._CONNECTOR_MECHANICAL_KEYWORDS):
            return "connector_switch_mechanical"
        return "uncategorized"

    def _looks_like_power_symbol(self, device: EagleDeviceContext) -> bool:
        """Detect likely schematic power symbols without hardcoded project names."""
        if device.is_power_symbol:
            return True
        symbol_canonical = self._canonicalize_name(device.symbol_name)
        deviceset_canonical = self._canonicalize_name(device.deviceset_name)
        if symbol_canonical in self._POWER_CANONICAL_NAMES:
            return True
        if deviceset_canonical in self._POWER_CANONICAL_NAMES:
            return True
        if device.symbol_name.strip().startswith("+") or device.deviceset_name.strip().startswith("+"):
            return True
        return False

    def _looks_like_connector_reference(self, device: EagleDeviceContext) -> bool:
        """Detect connector-style compact symbol naming (e.g., M06, M05X2, RJxx, DBxx)."""
        for raw_name in (device.symbol_name, device.deviceset_name):
            canonical = self._canonicalize_name(raw_name)
            if re.fullmatch(r"m\d+(x\d+)?", canonical):
                return True
            if canonical.startswith("rj") or canonical.startswith("db"):
                return True
        return False

    def _resolve_symbol_match(
        self,
        *,
        device: EagleDeviceContext,
        pathway: str,
        symbol_exact: dict[str, list[SymbolContext]],
        symbol_canonical: dict[str, list[SymbolContext]],
        eagle_pin_count: int,
    ) -> _SymbolMatch | None:
        """Resolve best symbol match using exact, normalized, and alias heuristics."""
        candidates = list(symbol_exact.get(self._normalize(device.symbol_name), []))
        resolution = "exact"
        if not candidates:
            candidates = list(
                symbol_canonical.get(self._canonicalize_name(device.symbol_name), [])
            )
            resolution = "canonical"
        if not candidates:
            for alias in self._symbol_alias_candidates(device):
                candidates = list(symbol_exact.get(self._normalize(alias), []))
                if not candidates:
                    candidates = list(symbol_canonical.get(self._canonicalize_name(alias), []))
                if candidates:
                    resolution = "alias"
                    break
        if not candidates:
            candidates = self._power_symbol_alias_candidates(
                device=device,
                symbol_exact=symbol_exact,
                symbol_canonical=symbol_canonical,
                eagle_pin_count=eagle_pin_count,
            )
            if candidates:
                resolution = "power_alias"
        if not candidates and pathway == "connector_switch_mechanical":
            candidates = self._connector_symbol_fallback_candidates(
                device=device,
                symbol_exact=symbol_exact,
                eagle_pin_count=eagle_pin_count,
            )
            if candidates:
                resolution = "connector_family"
        if not candidates:
            return None

        return _SymbolMatch(
            context=self._choose_symbol_candidate(candidates, pathway=pathway),
            resolution=resolution,
        )

    def _resolve_footprint_match(
        self,
        *,
        device: EagleDeviceContext,
        pathway: str,
        package_name: str,
        footprint_exact: dict[str, list[FootprintLibraryItem]],
        footprint_canonical: dict[str, list[FootprintLibraryItem]],
        footprint_items: Sequence[FootprintLibraryItem],
        eagle_pin_count: int,
    ) -> _FootprintMatch | None:
        """Resolve best footprint match using exact, normalized, and alias heuristics."""
        if not package_name:
            return None

        candidates = list(footprint_exact.get(self._normalize(package_name), []))
        resolution = "exact"
        if not candidates:
            candidates = list(
                footprint_canonical.get(self._canonicalize_name(package_name), [])
            )
            resolution = "canonical"
        if not candidates:
            for alias in self._package_alias_candidates(package_name):
                candidates = list(footprint_exact.get(self._normalize(alias), []))
                if not candidates:
                    candidates = list(footprint_canonical.get(self._canonicalize_name(alias), []))
                if candidates:
                    resolution = "alias"
                    break
        if not candidates:
            candidates = self._family_fallback_footprint_candidates(
                package_name=package_name,
                eagle_pin_count=eagle_pin_count,
                footprint_items=footprint_items,
            )
            if candidates:
                resolution = "family" if len(candidates) > 1 else "family_singleton"
        if not candidates:
            return None

        return _FootprintMatch(
            item=self._choose_footprint_candidate(
                candidates,
                pathway=pathway,
                package_name=package_name,
                eagle_pin_count=eagle_pin_count,
            ),
            resolution=resolution,
        )

    def _choose_symbol_candidate(
        self,
        candidates: Sequence[SymbolContext],
        *,
        pathway: str,
    ) -> SymbolContext:
        """Choose best symbol candidate based on library-name semantics."""
        ranked = []
        for candidate in candidates:
            library_name = self._normalize(candidate.library_nickname)
            score = 0
            if pathway == "schematic_annotation" and "power" in library_name:
                score += 100
            if library_name.startswith("device"):
                score += 20
            if "spcoast" in library_name:
                score += 10
            if pathway == "connector_switch_mechanical" and "connector" in library_name:
                score += 4
            ranked.append((score, library_name, candidate.symbol_name, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return ranked[0][3]

    def _choose_footprint_candidate(
        self,
        candidates: Sequence[FootprintLibraryItem],
        *,
        pathway: str,
        package_name: str,
        eagle_pin_count: int,
    ) -> FootprintLibraryItem:
        """Choose best footprint candidate using semantic + pin-count signals."""
        package_technology = self._package_technology_hint(package_name)
        package_family = self._package_family(package_name)
        ranked = []
        for candidate in candidates:
            library_name = self._normalize(candidate.library_nickname)
            canonical_library_name = self._canonicalize_name(candidate.library_nickname)
            score = 0
            if "spcoast" in library_name:
                score += 10
            if pathway == "connector_switch_mechanical" and "connector" in library_name:
                score += 6
            if package_technology == "smd" and ("smd" in library_name or "sot" in library_name):
                score += 4
            if package_technology == "tht" and "tht" in library_name:
                score += 4
            if eagle_pin_count > 0 and candidate.pad_count == eagle_pin_count:
                score += 12
            if package_family:
                family_hints = self._PACKAGE_FAMILY_LIBRARY_HINTS.get(package_family, tuple())
                if any(
                    hint.replace("_", "") in canonical_library_name
                    for hint in family_hints
                ):
                    score += 10
            if self._canonicalize_name(candidate.footprint_name) == self._canonicalize_name(
                package_name
            ):
                score += 4
            ranked.append((score, library_name, candidate.footprint_name, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        return ranked[0][3]

    def _symbol_alias_candidates(self, device: EagleDeviceContext) -> tuple[str, ...]:
        """Return potential symbol aliases for generic cross-library matching."""
        aliases: list[str] = []
        symbol_name = device.symbol_name.strip()
        if symbol_name:
            stripped = (
                symbol_name.replace("-US", "")
                .replace("_US", "")
                .replace("-EU", "")
                .replace("_EU", "")
                .replace("*", "")
                .strip("-_ ")
            )
            if stripped and stripped != symbol_name:
                aliases.append(stripped)

        tokens = self._device_tokens(device)
        if "resistor" in tokens:
            aliases.append("R")
        if "capacitor" in tokens or "cap" in tokens:
            aliases.extend(["C", "CP"])
        if "inductor" in tokens:
            aliases.append("L")
        if "diode" in tokens:
            aliases.append("D")
        if "led" in tokens:
            aliases.append("LED")

        canonical_symbol = self._canonicalize_name(symbol_name)
        aliases.extend(self._GENERIC_SYMBOL_ALIASES.get(canonical_symbol, tuple()))
        deduped = tuple(dict.fromkeys(alias for alias in aliases if alias))
        return deduped

    def _power_symbol_alias_candidates(
        self,
        *,
        device: EagleDeviceContext,
        symbol_exact: dict[str, list[SymbolContext]],
        symbol_canonical: dict[str, list[SymbolContext]],
        eagle_pin_count: int,
    ) -> list[SymbolContext]:
        """Return likely power-symbol candidates from known rail alias mappings."""
        canonical_inputs = (
            self._canonicalize_name(device.symbol_name),
            self._canonicalize_name(device.deviceset_name),
        )
        alias_names: list[str] = []
        for canonical_input in canonical_inputs:
            alias_names.extend(self._POWER_SYMBOL_ALIASES.get(canonical_input, tuple()))
        if not alias_names:
            return []

        candidates: list[SymbolContext] = []
        for alias in dict.fromkeys(alias_names):
            candidates.extend(symbol_exact.get(self._normalize(alias), []))
            candidates.extend(symbol_canonical.get(self._canonicalize_name(alias), []))
        if not candidates:
            return []

        expected_pin_count = eagle_pin_count if eagle_pin_count > 0 else 0
        if expected_pin_count > 0:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.pin_count in {0, expected_pin_count}
            ]
        if not candidates:
            return []

        deduped: list[SymbolContext] = []
        seen_keys: set[tuple[str, str]] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.library_nickname.casefold(),
                item.symbol_name.casefold(),
            ),
        ):
            key = (
                candidate.library_nickname.casefold(),
                candidate.symbol_name.casefold(),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(candidate)
        return deduped

    def _connector_symbol_fallback_candidates(
        self,
        *,
        device: EagleDeviceContext,
        symbol_exact: dict[str, list[SymbolContext]],
        eagle_pin_count: int,
    ) -> list[SymbolContext]:
        """Find connector-family symbol candidates for compact Eagle connector names."""
        expected_pin_count = eagle_pin_count if eagle_pin_count > 0 else 0
        explicit_names: list[str] = []

        for raw_name in (device.symbol_name, device.deviceset_name):
            canonical = self._canonicalize_name(raw_name)
            compact_match = re.fullmatch(r"m(\d+)(x(\d+))?", canonical)
            if compact_match is not None:
                primary_count = int(compact_match.group(1))
                multiplier = (
                    int(compact_match.group(3))
                    if compact_match.group(3) is not None
                    else 1
                )
                inferred_pin_count = primary_count * multiplier
                if expected_pin_count <= 0:
                    expected_pin_count = inferred_pin_count
                if multiplier == 1:
                    explicit_names.extend(
                        (
                            f"Conn_01x{primary_count:02d}",
                            f"Conn_01x{primary_count}",
                        )
                    )
                else:
                    explicit_names.extend(
                        (
                            f"Conn_{multiplier:02d}x{primary_count:02d}",
                            f"Conn_{multiplier}x{primary_count}",
                        )
                    )
                continue

            if canonical.startswith(("rj", "db")) and expected_pin_count <= 0:
                inferred_pin_count = self._extract_pin_count_hint(canonical)
                if inferred_pin_count > 0:
                    expected_pin_count = inferred_pin_count

        if expected_pin_count <= 0:
            return []

        candidates: list[SymbolContext] = []
        for explicit_name in dict.fromkeys(explicit_names):
            candidates.extend(symbol_exact.get(self._normalize(explicit_name), []))

        if not candidates:
            for symbol_group in symbol_exact.values():
                for candidate in symbol_group:
                    normalized_library = self._normalize(candidate.library_nickname)
                    normalized_symbol = self._normalize(candidate.symbol_name)
                    if (
                        "connector" not in normalized_library
                        and not normalized_symbol.startswith("conn_")
                    ):
                        continue
                    if (
                        candidate.pin_count > 0
                        and candidate.pin_count != expected_pin_count
                    ):
                        continue
                    candidates.append(candidate)
        if not candidates:
            return []

        deduped: list[SymbolContext] = []
        seen_keys: set[tuple[str, str]] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (
                item.library_nickname.casefold(),
                item.symbol_name.casefold(),
            ),
        ):
            key = (
                candidate.library_nickname.casefold(),
                candidate.symbol_name.casefold(),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(candidate)
        return deduped

    def _package_alias_candidates(self, package_name: str) -> tuple[str, ...]:
        """Return probable package aliases for footprint lookup."""
        normalized = self._normalize(package_name)
        canonical = self._canonicalize_name(package_name)
        aliases: list[str] = []
        if normalized.startswith("dil"):
            aliases.append(f"DIP{package_name[3:]}")
            aliases.append(f"DIP-{package_name[3:]}")
        if normalized.startswith("dip"):
            aliases.append(f"DIL{package_name[3:]}")
            aliases.append(f"DIL-{package_name[3:]}")
        if normalized.startswith("so") and len(package_name) > 2:
            suffix = package_name[2:]
            aliases.extend([f"SO-{suffix}", f"SOIC-{suffix}"])
        pin_hint = self._extract_pin_count_hint(canonical)
        if pin_hint > 0:
            aliases.extend(
                [
                    f"DIP-{pin_hint}",
                    f"DIP{pin_hint}",
                    f"SOIC-{pin_hint}",
                    f"SO-{pin_hint}",
                    f"SOP-{pin_hint}",
                    f"TSSOP-{pin_hint}",
                    f"SSOP-{pin_hint}",
                ]
            )
        if normalized.startswith("sot") and len(package_name) > 3:
            suffix = package_name[3:]
            aliases.append(f"SOT-{suffix}")
        return tuple(dict.fromkeys(alias for alias in aliases if alias))

    def _family_fallback_footprint_candidates(
        self,
        *,
        package_name: str,
        eagle_pin_count: int,
        footprint_items: Sequence[FootprintLibraryItem],
    ) -> list[FootprintLibraryItem]:
        """Find fallback footprint candidates by package family semantics."""
        family = self._package_family(package_name)
        if not family:
            return []
        pin_hint = self._extract_pin_count_hint(self._canonicalize_name(package_name))
        family_hints = self._PACKAGE_FAMILY_LIBRARY_HINTS.get(family, tuple())

        candidates: list[FootprintLibraryItem] = []
        for item in footprint_items:
            canonical_library_name = self._canonicalize_name(item.library_nickname)
            canonical_footprint_name = self._canonicalize_name(item.footprint_name)
            if not any(
                hint.replace("_", "") in canonical_library_name
                or hint.replace("_", "") in canonical_footprint_name
                for hint in family_hints
            ):
                continue
            expected_pin_count = eagle_pin_count if eagle_pin_count > 0 else pin_hint
            if expected_pin_count > 0 and item.pad_count > 0 and item.pad_count != expected_pin_count:
                continue
            candidates.append(item)
        return candidates

    def _derive_eagle_pin_count(self, device: EagleDeviceContext) -> int:
        """Choose best available Eagle pin-count source for matching heuristics."""
        if device.mapped_pin_count > 0:
            return device.mapped_pin_count
        if device.package_pad_count > 0:
            return device.package_pad_count
        return max(device.symbol_pin_count, 0)

    def _package_technology_hint(self, package_name: str) -> str:
        """Infer likely footprint technology class from package naming."""
        canonical = self._canonicalize_name(package_name)
        if not canonical:
            return ""
        smd_markers = ("sot", "so", "qfn", "qfp", "dfn", "lga", "bga", "smd")
        tht_markers = ("dip", "dil", "tht", "to220", "to92")
        if any(marker in canonical for marker in smd_markers):
            return "smd"
        if any(marker in canonical for marker in tht_markers):
            return "tht"
        return ""

    def _package_family(self, package_name: str) -> str:
        """Infer generalized package family token for footprint matching."""
        canonical = self._canonicalize_name(package_name)
        if not canonical:
            return ""
        if "dil" in canonical or "dip" in canonical:
            return "dip"
        if "soic" in canonical or canonical.startswith("so") or "sop" in canonical or "ssop" in canonical or "tssop" in canonical or "soj" in canonical:
            return "soic"
        if "qfp" in canonical:
            return "qfp"
        if "qfn" in canonical or "dfn" in canonical or "son" in canonical:
            return "qfn"
        if "sot" in canonical:
            return "sot"
        if "to" in canonical:
            return "to"
        if "bga" in canonical:
            return "bga"
        if "lga" in canonical:
            return "lga"
        return ""

    @staticmethod
    def _extract_pin_count_hint(canonical_package_name: str) -> int:
        """Extract first plausible pin-count integer from canonical package text."""
        match = re.search(r"(\d{1,3})", canonical_package_name)
        if match is None:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    @staticmethod
    def _index_symbols_by_exact(
        symbols: Sequence[SymbolContext],
    ) -> dict[str, list[SymbolContext]]:
        index: dict[str, list[SymbolContext]] = {}
        for symbol in symbols:
            key = symbol.symbol_name.strip().casefold()
            index.setdefault(key, []).append(symbol)
        return index

    def _index_symbols_by_canonical(
        self,
        symbols: Sequence[SymbolContext],
    ) -> dict[str, list[SymbolContext]]:
        index: dict[str, list[SymbolContext]] = {}
        for symbol in symbols:
            key = self._canonicalize_name(symbol.symbol_name)
            if not key:
                continue
            index.setdefault(key, []).append(symbol)
        return index

    @staticmethod
    def _index_footprints_by_exact(
        footprints: Sequence[FootprintLibraryItem],
    ) -> dict[str, list[FootprintLibraryItem]]:
        index: dict[str, list[FootprintLibraryItem]] = {}
        for footprint in footprints:
            key = footprint.footprint_name.strip().casefold()
            index.setdefault(key, []).append(footprint)
        return index

    def _index_footprints_by_canonical(
        self,
        footprints: Sequence[FootprintLibraryItem],
    ) -> dict[str, list[FootprintLibraryItem]]:
        index: dict[str, list[FootprintLibraryItem]] = {}
        for footprint in footprints:
            key = self._canonicalize_name(footprint.footprint_name)
            if not key:
                continue
            index.setdefault(key, []).append(footprint)
        return index

    def _looks_like_ic_part_number(self, token: str) -> bool:
        """Return True if token resembles a common IC/regulator part number."""
        return any(
            token.startswith(prefix) and any(ch.isdigit() for ch in token)
            for prefix in self._IC_PART_PREFIXES
        )

    @staticmethod
    def _device_tokens(device: EagleDeviceContext) -> list[str]:
        """Tokenize primary Eagle context fields for pathway classification."""
        combined = " ".join(
            (
                device.deviceset_name,
                device.device_name,
                device.symbol_name,
                device.package_name,
            )
        )
        return re.findall(r"[a-z0-9]+", combined.lower())

    def _derive_confidence(
        self,
        *,
        pathway: str,
        package_name: str,
        symbol_known: bool,
        footprint_known: bool,
        reasons: Sequence[str],
    ) -> str:
        """Derive high/medium/low confidence for one migration row."""
        if any(reason in self._PIN_MISMATCH_REASONS for reason in reasons):
            return "low"
        if pathway == "schematic_annotation":
            if not package_name:
                if symbol_known:
                    return "high"
                return "medium"
            if symbol_known:
                return "medium"
            return "low"
        if symbol_known and footprint_known:
            confidence = "high"
        elif symbol_known or footprint_known:
            confidence = "medium"
        else:
            confidence = "low"
        if pathway == "uncategorized" and confidence == "high":
            return "medium"
        return confidence

    def _derive_review_queue(
        self,
        *,
        pathway: str,
        confidence: str,
        reasons: Sequence[str],
    ) -> str:
        """Map confidence and reasons to importer review queue semantics."""
        if confidence == "high" and not reasons:
            return "none"
        if any(reason in self._PIN_MISMATCH_REASONS for reason in reasons):
            return "priority"
        if pathway == "schematic_annotation" and confidence in {"high", "medium"}:
            return "standard" if reasons else "none"
        if confidence == "low":
            return "priority"
        return "standard"

    @staticmethod
    def _normalize(value: str) -> str:
        """Normalize names for case-insensitive set membership checks."""
        return value.strip().casefold()

    @staticmethod
    def _canonicalize_name(value: str) -> str:
        """Normalize names for fuzzy identifier matching."""
        return re.sub(r"[^a-z0-9]+", "", value.strip().casefold())

