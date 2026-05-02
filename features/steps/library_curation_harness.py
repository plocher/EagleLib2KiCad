"""Deterministic in-harness execution support for library_curation Behave steps."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
import json
import re
import shlex
from typing import Iterable, Mapping, Sequence


_POWER_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "agnd": ("GNDA", "GND"),
    "dgnd": ("DGND", "GND"),
    "pgnd": ("PGND", "GND"),
    "gnd": ("GND",),
    "vcc": ("VCC", "VDD"),
}
_LOCAL_HASH_PLACEHOLDER = "<localhash_excluding_provenance>"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EagleSymbolRow:
    """Typed row for `an Eagle library contains symbols` tables."""

    symbol_name: str
    role: str
    pin_count: int


@dataclass(frozen=True)
class EagleDeviceRow:
    """Typed row for `the Eagle library contains devices` tables."""

    deviceset: str
    device: str
    symbol_name: str
    package_name: str
    mapped_pin_count: int

    @property
    def device_key(self) -> str:
        """Return canonical `DeviceSet:Device` key used by scenarios."""
        return f"{self.deviceset}:{self.device}"


@dataclass(frozen=True)
class KiCadSymbolRow:
    """Typed row for `a KiCad symbol corpus contains` tables."""

    library_nickname: str
    symbol_name: str
    pin_count: int
    default_footprint: str
    source_hash: str


@dataclass(frozen=True)
class KiCadFootprintRow:
    """Typed row for `a KiCad footprint corpus contains` tables."""

    library_nickname: str
    footprint_name: str
    pad_count: int


@dataclass(frozen=True)
class RoleFilteringOverrideRow:
    """Typed row for role filtering override tables."""

    excluded_role: str


@dataclass(frozen=True)
class MatchingPolicyOverrideRow:
    """Typed row for matching policy override tables."""

    key: str
    value: str


@dataclass(frozen=True)
class CuratedSymbolAuditRow:
    """Typed row for curated symbol provenance-audit inputs."""

    symbol_name: str
    e2k_provenance: str
    curated_hash_now: str


@dataclass(frozen=True)
class SourceSymbolHashRow:
    """Typed row for source-hash provenance-audit inputs."""

    library_nickname: str
    symbol_name: str
    source_hash_now: str


@dataclass(frozen=True)
class AuditOptionRow:
    """Typed row for provenance audit options."""

    option: str
    value: str


@dataclass(frozen=True)
class LibraryCurationScenarioInput:
    """Structured scenario intake for command/capability execution."""

    workflow: str = "library_curation"
    eagle_symbols: tuple[EagleSymbolRow, ...] = tuple()
    eagle_devices: tuple[EagleDeviceRow, ...] = tuple()
    kicad_symbols: tuple[KiCadSymbolRow, ...] = tuple()
    kicad_footprints: tuple[KiCadFootprintRow, ...] = tuple()
    role_filtering_overrides: tuple[RoleFilteringOverrideRow, ...] = tuple()
    matching_policy_overrides: tuple[MatchingPolicyOverrideRow, ...] = tuple()
    curated_symbols: tuple[CuratedSymbolAuditRow, ...] = tuple()
    source_symbol_hashes: tuple[SourceSymbolHashRow, ...] = tuple()
    audit_options: tuple[AuditOptionRow, ...] = tuple()


@dataclass(frozen=True)
class ArtifactBytes:
    """Canonical deterministic JSON+CSV bytes for one artifact."""

    json_bytes: bytes
    csv_bytes: bytes


@dataclass
class LibraryCurationRunResult:
    """Execution result payload consumed by step assertions."""

    success: bool
    error_message: str = ""
    csv_output_rows: tuple[dict[str, str], ...] = tuple()
    curated_symbol_rows: tuple[dict[str, str], ...] = tuple()
    curated_footprint_rows: tuple[dict[str, str], ...] = tuple()
    converted_symbol_rows: tuple[dict[str, str], ...] = tuple()
    converted_footprint_rows: tuple[dict[str, str], ...] = tuple()
    mapping_summary_rows: tuple[dict[str, str], ...] = tuple()
    decision_report_rows: tuple[dict[str, str], ...] = tuple()
    curated_symbol_property_rows: tuple[dict[str, str], ...] = tuple()
    approved_device_keys: tuple[str, ...] = tuple()
    origin_by_device_key: dict[str, str] = field(default_factory=dict)
    artifact_bytes: dict[str, ArtifactBytes] = field(default_factory=dict)


@dataclass(frozen=True)
class _DeviceDecision:
    """Internal deterministic decision row for one Eagle device."""

    device: EagleDeviceRow
    classification: str
    review_queue: str
    approved: bool
    origin: str
    symbol_match: KiCadSymbolRow | None
    footprint_match: KiCadFootprintRow | None


@dataclass(frozen=True)
class _ParsedProvenance:
    """Parsed E2K_PROVENANCE payload fields."""

    library_nickname: str
    symbol_name: str
    origin_hash: str
    stored_local_hash: str


def parse_eagle_symbols(rows: Sequence[Mapping[str, str]]) -> tuple[EagleSymbolRow, ...]:
    """Parse and validate Eagle symbol table rows."""
    _validate_required_columns(
        rows,
        required_columns=("SymbolName", "Role", "PinCount"),
        table_name="eagle_symbols",
    )
    parsed: list[EagleSymbolRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            EagleSymbolRow(
                symbol_name=_required_text(row, "SymbolName", "eagle_symbols", index),
                role=_required_text(row, "Role", "eagle_symbols", index),
                pin_count=_required_int(row, "PinCount", "eagle_symbols", index),
            )
        )
    return tuple(parsed)


def parse_eagle_devices(rows: Sequence[Mapping[str, str]]) -> tuple[EagleDeviceRow, ...]:
    """Parse and validate Eagle devices table rows."""
    _validate_required_columns(
        rows,
        required_columns=(
            "DeviceSet",
            "Device",
            "SymbolName",
            "PackageName",
            "MappedPinCount",
        ),
        table_name="eagle_devices",
    )
    parsed: list[EagleDeviceRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            EagleDeviceRow(
                deviceset=_required_text(row, "DeviceSet", "eagle_devices", index),
                device=_required_text(row, "Device", "eagle_devices", index),
                symbol_name=_required_text(row, "SymbolName", "eagle_devices", index),
                package_name=_required_text(row, "PackageName", "eagle_devices", index),
                mapped_pin_count=_required_int(row, "MappedPinCount", "eagle_devices", index),
            )
        )
    return tuple(parsed)


def parse_kicad_symbols(rows: Sequence[Mapping[str, str]]) -> tuple[KiCadSymbolRow, ...]:
    """Parse and validate KiCad symbol corpus rows."""
    _validate_required_columns(
        rows,
        required_columns=("LibraryNickname", "SymbolName", "PinCount"),
        table_name="kicad_symbols",
    )
    parsed: list[KiCadSymbolRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            KiCadSymbolRow(
                library_nickname=_required_text(row, "LibraryNickname", "kicad_symbols", index),
                symbol_name=_required_text(row, "SymbolName", "kicad_symbols", index),
                pin_count=_required_int(row, "PinCount", "kicad_symbols", index),
                default_footprint=_optional_text(row, "DefaultFootprint"),
                source_hash=_optional_text(row, "SourceHash"),
            )
        )
    return tuple(parsed)


def parse_kicad_footprints(rows: Sequence[Mapping[str, str]]) -> tuple[KiCadFootprintRow, ...]:
    """Parse and validate KiCad footprint corpus rows."""
    _validate_required_columns(
        rows,
        required_columns=("LibraryNickname", "FootprintName", "PadCount"),
        table_name="kicad_footprints",
    )
    parsed: list[KiCadFootprintRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            KiCadFootprintRow(
                library_nickname=_required_text(
                    row,
                    "LibraryNickname",
                    "kicad_footprints",
                    index,
                ),
                footprint_name=_required_text(
                    row,
                    "FootprintName",
                    "kicad_footprints",
                    index,
                ),
                pad_count=_required_int(row, "PadCount", "kicad_footprints", index),
            )
        )
    return tuple(parsed)


def parse_role_filtering_overrides(
    rows: Sequence[Mapping[str, str]],
) -> tuple[RoleFilteringOverrideRow, ...]:
    """Parse and validate role filtering override rows."""
    _validate_required_columns(
        rows,
        required_columns=("ExcludedRole",),
        table_name="role_filtering_overrides",
    )
    parsed: list[RoleFilteringOverrideRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            RoleFilteringOverrideRow(
                excluded_role=_required_text(
                    row,
                    "ExcludedRole",
                    "role_filtering_overrides",
                    index,
                )
            )
        )
    return tuple(parsed)


def parse_matching_policy_overrides(
    rows: Sequence[Mapping[str, str]],
) -> tuple[MatchingPolicyOverrideRow, ...]:
    """Parse and validate matching policy override rows."""
    _validate_required_columns(
        rows,
        required_columns=("Key", "Value"),
        table_name="matching_policy_overrides",
    )
    parsed: list[MatchingPolicyOverrideRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            MatchingPolicyOverrideRow(
                key=_required_text(row, "Key", "matching_policy_overrides", index),
                value=_required_text(row, "Value", "matching_policy_overrides", index),
            )
        )
    return tuple(parsed)


def parse_curated_symbols(rows: Sequence[Mapping[str, str]]) -> tuple[CuratedSymbolAuditRow, ...]:
    """Parse and validate curated symbols table for provenance audit."""
    _validate_required_columns(
        rows,
        required_columns=("SymbolName", "E2K_PROVENANCE", "CuratedHashNow"),
        table_name="curated_symbols",
    )
    parsed: list[CuratedSymbolAuditRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            CuratedSymbolAuditRow(
                symbol_name=_required_text(row, "SymbolName", "curated_symbols", index),
                e2k_provenance=_optional_text(row, "E2K_PROVENANCE"),
                curated_hash_now=_required_text(row, "CuratedHashNow", "curated_symbols", index),
            )
        )
    return tuple(parsed)


def parse_source_symbol_hashes(
    rows: Sequence[Mapping[str, str]],
) -> tuple[SourceSymbolHashRow, ...]:
    """Parse and validate source hash rows for provenance audit."""
    _validate_required_columns(
        rows,
        required_columns=("LibraryNickname", "SymbolName", "SourceHashNow"),
        table_name="source_symbol_hashes",
    )
    parsed: list[SourceSymbolHashRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            SourceSymbolHashRow(
                library_nickname=_required_text(
                    row,
                    "LibraryNickname",
                    "source_symbol_hashes",
                    index,
                ),
                symbol_name=_required_text(row, "SymbolName", "source_symbol_hashes", index),
                source_hash_now=_required_text(
                    row,
                    "SourceHashNow",
                    "source_symbol_hashes",
                    index,
                ),
            )
        )
    return tuple(parsed)


def parse_audit_options(rows: Sequence[Mapping[str, str]]) -> tuple[AuditOptionRow, ...]:
    """Parse and validate audit options rows."""
    _validate_required_columns(
        rows,
        required_columns=("Option", "Value"),
        table_name="audit_options",
    )
    parsed: list[AuditOptionRow] = []
    for index, row in enumerate(rows):
        parsed.append(
            AuditOptionRow(
                option=_required_text(row, "Option", "audit_options", index),
                value=_required_text(row, "Value", "audit_options", index),
            )
        )
    return tuple(parsed)


def run_library_curation_command(
    scenario_input: LibraryCurationScenarioInput,
    command: str,
) -> LibraryCurationRunResult:
    """Run one deterministic harness execution for a command string."""
    try:
        profile = _parse_profile_from_command(command)
        if profile == "advisory_matching":
            return _run_advisory_matching(scenario_input)
        if profile == "curated_generation":
            return _run_curated_generation(scenario_input)
        if profile == "fidelity_conversion":
            return _run_fidelity_conversion(scenario_input)
        raise ValueError(f"Unsupported library-curation profile: {profile}")
    except Exception as exc:  # pragma: no cover - defensive for Behave surfaces
        return LibraryCurationRunResult(success=False, error_message=str(exc))


def run_library_curation_capability(
    scenario_input: LibraryCurationScenarioInput,
    capability: str,
    workflow: str,
) -> LibraryCurationRunResult:
    """Run one deterministic harness execution for a capability invocation."""
    try:
        if normalize_name(workflow) != "library_curation":
            raise ValueError(f"Unsupported workflow for harness capability run: {workflow}")
        if normalize_name(capability) != "provenance_audit":
            raise ValueError(
                f"Unsupported capability for library_curation harness: {capability}"
            )
        return _run_provenance_audit(scenario_input)
    except Exception as exc:  # pragma: no cover - defensive for Behave surfaces
        return LibraryCurationRunResult(success=False, error_message=str(exc))


def assert_rows_include(
    *,
    expected_rows: Sequence[Mapping[str, str]],
    actual_rows: Sequence[Mapping[str, str]],
    dataset_name: str,
) -> None:
    """Assert expected rows are present via subset/multiset matching."""
    remaining_actual_rows = [dict(row) for row in actual_rows]
    normalized_expected = [_normalize_row_dict(row) for row in expected_rows]
    for expected in normalized_expected:
        match_index = _find_subset_match_index(expected, remaining_actual_rows)
        if match_index < 0:
            expected_json = json.dumps(expected, sort_keys=True)
            actual_json = json.dumps(remaining_actual_rows, sort_keys=True, indent=2)
            raise AssertionError(
                f"Missing expected {dataset_name} row: {expected_json}\n"
                f"Available rows: {actual_json}"
            )
        remaining_actual_rows.pop(match_index)


def normalize_name(value: str) -> str:
    """Return trimmed case-folded value for equality checks."""
    return value.strip().casefold()


def canonicalize_name(value: str) -> str:
    """Return alphanumeric canonicalized value for fuzzy matching."""
    return re.sub(r"[^a-z0-9]+", "", normalize_name(value))


def canonical_json_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    """Serialize row dictionaries to canonical deterministic JSON bytes."""
    canonical_rows = _canonicalize_rows(rows)
    return json.dumps(
        canonical_rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_csv_bytes(rows: Sequence[Mapping[str, str]]) -> bytes:
    """Serialize row dictionaries to canonical deterministic CSV bytes."""
    canonical_rows = _canonicalize_rows(rows)
    if not canonical_rows:
        return b""
    fieldnames = sorted({key for row in canonical_rows for key in row})
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in canonical_rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return buffer.getvalue().encode("utf-8")


def _run_advisory_matching(
    scenario_input: LibraryCurationScenarioInput,
) -> LibraryCurationRunResult:
    """Run advisory_matching profile behavior."""
    decisions = _build_device_decisions(scenario_input)
    decision_report_rows = _decision_report_rows(decisions)
    result = LibraryCurationRunResult(
        success=True,
        csv_output_rows=decision_report_rows,
        decision_report_rows=decision_report_rows,
        approved_device_keys=tuple(
            sorted(
                decision.device.device_key
                for decision in decisions
                if decision.approved
            )
        ),
        origin_by_device_key={
            decision.device.device_key: decision.origin for decision in decisions
        },
    )
    result.artifact_bytes = _artifact_bytes_for_result(result)
    return result


def _run_curated_generation(
    scenario_input: LibraryCurationScenarioInput,
) -> LibraryCurationRunResult:
    """Run curated_generation profile behavior."""
    decisions = _build_device_decisions(scenario_input)
    decision_report_rows = _decision_report_rows(decisions)
    curated_symbol_rows = _curated_symbol_rows(decisions)
    curated_footprint_rows = _curated_footprint_rows(decisions)
    mapping_summary_rows = _mapping_summary_rows(decisions)
    curated_symbol_property_rows = _curated_symbol_property_rows(
        decisions=decisions,
        curated_symbol_rows=curated_symbol_rows,
    )
    result = LibraryCurationRunResult(
        success=True,
        csv_output_rows=decision_report_rows,
        curated_symbol_rows=curated_symbol_rows,
        curated_footprint_rows=curated_footprint_rows,
        mapping_summary_rows=mapping_summary_rows,
        decision_report_rows=decision_report_rows,
        curated_symbol_property_rows=curated_symbol_property_rows,
        approved_device_keys=tuple(
            sorted(
                decision.device.device_key
                for decision in decisions
                if decision.approved
            )
        ),
        origin_by_device_key={
            decision.device.device_key: decision.origin for decision in decisions
        },
    )
    result.artifact_bytes = _artifact_bytes_for_result(result)
    return result


def _run_fidelity_conversion(
    scenario_input: LibraryCurationScenarioInput,
) -> LibraryCurationRunResult:
    """Run fidelity_conversion profile behavior."""
    converted_symbol_rows = tuple(
        {"SymbolName": symbol.symbol_name}
        for symbol in sorted(
            scenario_input.eagle_symbols,
            key=lambda item: (normalize_name(item.symbol_name), item.symbol_name),
        )
    )
    seen_packages: set[str] = set()
    converted_footprint_rows: list[dict[str, str]] = []
    for device in sorted(
        scenario_input.eagle_devices,
        key=lambda item: (
            normalize_name(item.deviceset),
            normalize_name(item.device),
        ),
    ):
        package_name = device.package_name.strip()
        if not package_name or _is_none_package(package_name):
            continue
        normalized_package = normalize_name(package_name)
        if normalized_package in seen_packages:
            continue
        seen_packages.add(normalized_package)
        converted_footprint_rows.append({"PackageName": package_name})
    result = LibraryCurationRunResult(
        success=True,
        converted_symbol_rows=tuple(converted_symbol_rows),
        converted_footprint_rows=tuple(converted_footprint_rows),
    )
    result.artifact_bytes = _artifact_bytes_for_result(result)
    return result


def _run_provenance_audit(
    scenario_input: LibraryCurationScenarioInput,
) -> LibraryCurationRunResult:
    """Run `provenance_audit` capability behavior."""
    rematch_mode = _option_truthy(scenario_input.audit_options, option_name="rematch_mode")
    source_index = {
        (
            normalize_name(row.library_nickname),
            normalize_name(row.symbol_name),
        ): row.source_hash_now
        for row in scenario_input.source_symbol_hashes
    }
    source_symbol_rows = tuple(scenario_input.source_symbol_hashes)
    csv_rows: list[dict[str, str]] = []
    for curated_row in sorted(
        scenario_input.curated_symbols,
        key=lambda item: normalize_name(item.symbol_name),
    ):
        parsed_provenance = _parse_provenance(curated_row.e2k_provenance)
        if parsed_provenance is None:
            rematch_status = ""
            confidence = ""
            if rematch_mode and _has_similarity_rematch(
                symbol_name=curated_row.symbol_name,
                source_rows=source_symbol_rows,
            ):
                rematch_status = "rematched"
                confidence = "low"
            csv_rows.append(
                {
                    "SymbolName": curated_row.symbol_name,
                    "AuditStatus": "unmanaged",
                    "RematchStatus": rematch_status,
                    "Confidence": confidence,
                }
            )
            continue

        source_hash_now = source_index.get(
            (
                normalize_name(parsed_provenance.library_nickname),
                normalize_name(parsed_provenance.symbol_name),
            ),
            "",
        )
        source_changed = source_hash_now != parsed_provenance.origin_hash
        local_changed = curated_row.curated_hash_now != parsed_provenance.stored_local_hash
        if not source_changed and not local_changed:
            audit_status = "in_sync"
        elif source_changed and not local_changed:
            audit_status = "source_changed"
        elif not source_changed and local_changed:
            audit_status = "local_changed"
        else:
            audit_status = "both_changed"
        csv_rows.append(
            {
                "SymbolName": curated_row.symbol_name,
                "AuditStatus": audit_status,
                "RematchStatus": "",
                "Confidence": "",
            }
        )
    result = LibraryCurationRunResult(success=True, csv_output_rows=tuple(csv_rows))
    result.artifact_bytes = _artifact_bytes_for_result(result)
    return result


def _build_device_decisions(
    scenario_input: LibraryCurationScenarioInput,
) -> tuple[_DeviceDecision, ...]:
    """Build deterministic decisions per Eagle device for profile logic."""
    symbol_by_name = {
        normalize_name(symbol.symbol_name): symbol for symbol in scenario_input.eagle_symbols
    }
    excluded_roles = {
        normalize_name(row.excluded_role) for row in scenario_input.role_filtering_overrides
    }
    prefer_kicad_symbols = _policy_bool(
        scenario_input.matching_policy_overrides,
        key_name="prefer_kicad_symbols",
        default_value=True,
    )
    decisions: list[_DeviceDecision] = []
    sorted_devices = sorted(
        scenario_input.eagle_devices,
        key=lambda item: (
            normalize_name(item.deviceset),
            normalize_name(item.device),
        ),
    )
    for device in sorted_devices:
        role = ""
        eagle_symbol = symbol_by_name.get(normalize_name(device.symbol_name))
        if eagle_symbol is not None:
            role = normalize_name(eagle_symbol.role)
        if role and role in excluded_roles:
            continue

        symbol_candidates, symbol_resolution = _resolve_symbol_candidates(
            device=device,
            kicad_symbols=scenario_input.kicad_symbols,
        )
        footprint_candidates = _resolve_footprint_candidates(
            device=device,
            kicad_footprints=scenario_input.kicad_footprints,
        )
        symbol_match = symbol_candidates[0] if len(symbol_candidates) == 1 else None
        footprint_match = footprint_candidates[0] if len(footprint_candidates) == 1 else None

        if not prefer_kicad_symbols:
            classification = "override_applied"
            review_queue = "none"
            approved = True
            origin = "transformed_eagle"
        elif len(symbol_candidates) > 1:
            classification = "ambiguous"
            review_queue = "priority"
            approved = False
            origin = "transformed_eagle"
        elif not _is_none_package(device.package_name) and not footprint_candidates:
            classification = "unresolved_package"
            review_queue = "standard"
            approved = False
            origin = "transformed_eagle"
        elif symbol_match is not None and symbol_resolution == "semantic":
            classification = "semantic_match"
            review_queue = "standard"
            approved = True
            origin = "copied_kicad"
        elif symbol_match is not None:
            classification = "exact_match"
            review_queue = "none"
            approved = True
            origin = "copied_kicad"
        else:
            classification = "unresolved_package"
            review_queue = "standard"
            approved = False
            origin = "transformed_eagle"

        decisions.append(
            _DeviceDecision(
                device=device,
                classification=classification,
                review_queue=review_queue,
                approved=approved,
                origin=origin,
                symbol_match=symbol_match,
                footprint_match=footprint_match,
            )
        )
    return tuple(decisions)


def _resolve_symbol_candidates(
    *,
    device: EagleDeviceRow,
    kicad_symbols: Sequence[KiCadSymbolRow],
) -> tuple[tuple[KiCadSymbolRow, ...], str]:
    """Resolve symbol candidates with exact, semantic alias, then pin fallback."""
    canonical_symbol = canonicalize_name(device.symbol_name)
    exact_candidates = tuple(
        symbol
        for symbol in kicad_symbols
        if canonicalize_name(symbol.symbol_name) == canonical_symbol
        and _pin_compatible(device.mapped_pin_count, symbol.pin_count)
    )
    if exact_candidates:
        return _dedupe_kicad_symbols(exact_candidates), "exact"

    semantic_candidates: list[KiCadSymbolRow] = []
    for alias in _POWER_ALIAS_MAP.get(canonical_symbol, tuple()):
        alias_canonical = canonicalize_name(alias)
        semantic_candidates.extend(
            symbol
            for symbol in kicad_symbols
            if canonicalize_name(symbol.symbol_name) == alias_canonical
            and _pin_compatible(device.mapped_pin_count, symbol.pin_count)
        )
    if semantic_candidates:
        return _dedupe_kicad_symbols(semantic_candidates), "semantic"

    if device.mapped_pin_count > 0:
        pin_fallback = tuple(
            symbol
            for symbol in kicad_symbols
            if symbol.pin_count == device.mapped_pin_count
        )
        if pin_fallback:
            return _dedupe_kicad_symbols(pin_fallback), "pin_fallback"
    return tuple(), ""


def _resolve_footprint_candidates(
    *,
    device: EagleDeviceRow,
    kicad_footprints: Sequence[KiCadFootprintRow],
) -> tuple[KiCadFootprintRow, ...]:
    """Resolve footprint candidates with exact then family+pin fallback."""
    if _is_none_package(device.package_name):
        return tuple()

    canonical_package = canonicalize_name(device.package_name)
    exact_candidates = tuple(
        footprint
        for footprint in kicad_footprints
        if canonicalize_name(footprint.footprint_name) == canonical_package
        and _pin_compatible(device.mapped_pin_count, footprint.pad_count)
    )
    if exact_candidates:
        return _dedupe_kicad_footprints(exact_candidates)

    family = _package_family(device.package_name)
    if not family:
        return tuple()
    family_candidates = tuple(
        footprint
        for footprint in kicad_footprints
        if _package_family(footprint.footprint_name) == family
        and _pin_compatible(device.mapped_pin_count, footprint.pad_count)
    )
    if family_candidates:
        return _dedupe_kicad_footprints(family_candidates)
    return tuple()


def _decision_report_rows(decisions: Sequence[_DeviceDecision]) -> tuple[dict[str, str], ...]:
    """Create decision report rows from internal device decisions."""
    rows = [
        {
            "DeviceKey": decision.device.device_key,
            "Classification": decision.classification,
            "ReviewQueue": decision.review_queue,
        }
        for decision in decisions
    ]
    return tuple(_canonicalize_rows(rows))


def _curated_symbol_rows(decisions: Sequence[_DeviceDecision]) -> tuple[dict[str, str], ...]:
    """Create curated symbol library rows for curated_generation profile."""
    rows_by_symbol: dict[str, dict[str, str]] = {}
    for decision in decisions:
        if decision.classification == "ambiguous":
            continue
        symbol_name = decision.device.symbol_name
        normalized_symbol = normalize_name(symbol_name)
        if normalized_symbol in rows_by_symbol:
            continue
        rows_by_symbol[normalized_symbol] = {
            "SymbolName": symbol_name,
            "Origin": decision.origin,
        }
    return tuple(_canonicalize_rows(rows_by_symbol.values()))


def _curated_footprint_rows(decisions: Sequence[_DeviceDecision]) -> tuple[dict[str, str], ...]:
    """Create curated footprint library rows for unresolved package variants."""
    rows_by_package: dict[str, dict[str, str]] = {}
    for decision in decisions:
        package_name = decision.device.package_name
        if _is_none_package(package_name):
            continue
        if decision.classification != "unresolved_package":
            continue
        normalized_package = normalize_name(package_name)
        if normalized_package in rows_by_package:
            continue
        rows_by_package[normalized_package] = {"FootprintName": package_name}
    return tuple(_canonicalize_rows(rows_by_package.values()))


def _mapping_summary_rows(decisions: Sequence[_DeviceDecision]) -> tuple[dict[str, str], ...]:
    """Create per-deviceset mapping summary rows."""
    decisions_by_deviceset: dict[str, list[_DeviceDecision]] = {}
    for decision in decisions:
        decisions_by_deviceset.setdefault(decision.device.deviceset, []).append(decision)
    summary_rows: list[dict[str, str]] = []
    for deviceset in sorted(decisions_by_deviceset, key=normalize_name):
        group = decisions_by_deviceset[deviceset]
        input_variants = len(group)
        curated_symbols = {
            normalize_name(decision.device.symbol_name)
            for decision in group
            if decision.classification != "ambiguous"
        }
        covered_packages = sum(
            1
            for decision in group
            if not _is_none_package(decision.device.package_name)
            and (decision.footprint_match is not None or decision.classification == "unresolved_package")
        )
        unresolved_variants = sum(1 for decision in group if not decision.approved)
        summary_rows.extend(
            (
                {
                    "DeviceSet": deviceset,
                    "Metric": "input_device_variants",
                    "Value": str(input_variants),
                },
                {
                    "DeviceSet": deviceset,
                    "Metric": "curated_symbol_variants",
                    "Value": str(len(curated_symbols)),
                },
                {
                    "DeviceSet": deviceset,
                    "Metric": "covered_package_variants",
                    "Value": str(covered_packages),
                },
                {
                    "DeviceSet": deviceset,
                    "Metric": "unresolved_variants",
                    "Value": str(unresolved_variants),
                },
            )
        )
    return tuple(_canonicalize_rows(summary_rows))


def _curated_symbol_property_rows(
    *,
    decisions: Sequence[_DeviceDecision],
    curated_symbol_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    """Create E2K_PROVENANCE rows for copied_kicad curated symbols."""
    match_by_symbol: dict[str, _DeviceDecision] = {}
    for decision in decisions:
        if decision.symbol_match is None:
            continue
        normalized_symbol = normalize_name(decision.device.symbol_name)
        current = match_by_symbol.get(normalized_symbol)
        if current is None or decision.device.device_key < current.device.device_key:
            match_by_symbol[normalized_symbol] = decision
    rows: list[dict[str, str]] = []
    for curated_row in curated_symbol_rows:
        if normalize_name(curated_row.get("Origin", "")) != "copied_kicad":
            continue
        symbol_name = curated_row.get("SymbolName", "")
        decision = match_by_symbol.get(normalize_name(symbol_name))
        if decision is None or decision.symbol_match is None:
            continue
        symbol_match = decision.symbol_match
        origin_hash = symbol_match.source_hash or "UNKNOWN_ORIGIN"
        rows.append(
            {
                "SymbolName": symbol_name,
                "PropertyName": "E2K_PROVENANCE",
                "PropertyValueFormat": (
                    f"{symbol_match.library_nickname}:{symbol_match.symbol_name},"
                    f"{origin_hash},{_LOCAL_HASH_PLACEHOLDER}"
                ),
            }
        )
    return tuple(_canonicalize_rows(rows))


def _artifact_bytes_for_result(result: LibraryCurationRunResult) -> dict[str, ArtifactBytes]:
    """Create deterministic JSON+CSV bytes for tracked output artifacts."""
    artifacts: dict[str, Sequence[Mapping[str, str]]] = {
        "curated_symbol_output": result.curated_symbol_rows,
        "curated_footprint_output": result.curated_footprint_rows,
        "decision_report": result.decision_report_rows,
        "csv_output": result.csv_output_rows,
    }
    return {
        name: ArtifactBytes(
            json_bytes=canonical_json_bytes(rows),
            csv_bytes=canonical_csv_bytes(rows),
        )
        for name, rows in artifacts.items()
    }


def _parse_profile_from_command(command: str) -> str:
    """Parse and validate profile from command string."""
    parts = shlex.split(command)
    if not parts or parts[0] != "library-curation":
        raise ValueError(f"Unsupported command for harness runner: {command}")
    for index, part in enumerate(parts):
        if part == "--profile" and index + 1 < len(parts):
            return parts[index + 1].strip()
        if part.startswith("--profile="):
            return part.split("=", 1)[1].strip()
    raise ValueError(f"Missing --profile argument for command: {command}")


def _validate_required_columns(
    rows: Sequence[Mapping[str, str]],
    *,
    required_columns: Sequence[str],
    table_name: str,
) -> None:
    """Validate that every row has required columns for a table."""
    if not rows:
        return
    for row_index, row in enumerate(rows):
        missing = [column for column in required_columns if column not in row]
        if missing:
            raise ValueError(
                f"Table '{table_name}' row {row_index} missing required columns: {missing}"
            )


def _required_text(
    row: Mapping[str, str],
    column_name: str,
    table_name: str,
    row_index: int,
) -> str:
    """Extract a required non-empty string value from one row."""
    value = row.get(column_name, "")
    normalized = value.strip()
    if not normalized:
        raise ValueError(
            f"Table '{table_name}' row {row_index} has empty required value for '{column_name}'."
        )
    return normalized


def _optional_text(row: Mapping[str, str], column_name: str) -> str:
    """Extract an optional string value from one row."""
    return row.get(column_name, "").strip()


def _required_int(
    row: Mapping[str, str],
    column_name: str,
    table_name: str,
    row_index: int,
) -> int:
    """Extract a required integer value from one row."""
    value = _required_text(row, column_name, table_name, row_index)
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Table '{table_name}' row {row_index} column '{column_name}' is not an integer: {value}"
        ) from exc


def _policy_bool(
    overrides: Sequence[MatchingPolicyOverrideRow],
    *,
    key_name: str,
    default_value: bool,
) -> bool:
    """Read boolean matching-policy override with deterministic precedence."""
    normalized_key = normalize_name(key_name)
    for override in reversed(tuple(overrides)):
        if normalize_name(override.key) == normalized_key:
            return normalize_name(override.value) in _TRUE_VALUES
    return default_value


def _option_truthy(
    options: Sequence[AuditOptionRow],
    *,
    option_name: str,
) -> bool:
    """Read boolean audit option by name."""
    normalized_option = normalize_name(option_name)
    for option in reversed(tuple(options)):
        if normalize_name(option.option) == normalized_option:
            return normalize_name(option.value) in _TRUE_VALUES
    return False


def _parse_provenance(value: str) -> _ParsedProvenance | None:
    """Parse E2K_PROVENANCE payload, returning None for unmanaged/invalid values."""
    raw_value = value.strip()
    if not raw_value:
        return None
    segments = [segment.strip() for segment in raw_value.split(",")]
    if len(segments) != 3:
        return None
    library_and_symbol, origin_hash, stored_local_hash = segments
    if ":" not in library_and_symbol:
        return None
    library_nickname, symbol_name = [part.strip() for part in library_and_symbol.split(":", 1)]
    if not library_nickname or not symbol_name or not origin_hash or not stored_local_hash:
        return None
    return _ParsedProvenance(
        library_nickname=library_nickname,
        symbol_name=symbol_name,
        origin_hash=origin_hash,
        stored_local_hash=stored_local_hash,
    )


def _has_similarity_rematch(
    *,
    symbol_name: str,
    source_rows: Sequence[SourceSymbolHashRow],
) -> bool:
    """Return True when unmanaged symbol name has normalized similarity to a source symbol."""
    for source_row in source_rows:
        if _normalized_name_similarity(symbol_name, source_row.symbol_name):
            return True
    return False


def _normalized_name_similarity(left: str, right: str) -> bool:
    """Return normalized token similarity for conservative rematch decisions."""
    left_tokens = {token for token in _name_tokens(left) if len(token) >= 3}
    right_tokens = {token for token in _name_tokens(right) if len(token) >= 3}
    if left_tokens.intersection(right_tokens):
        return True
    left_canonical = canonicalize_name(left)
    right_canonical = canonicalize_name(right)
    if len(left_canonical) >= 4 and left_canonical in right_canonical:
        return True
    if len(right_canonical) >= 4 and right_canonical in left_canonical:
        return True
    return False


def _name_tokens(value: str) -> tuple[str, ...]:
    """Split name into normalized alpha/numeric tokens."""
    lower_value = normalize_name(value)
    raw_tokens = re.findall(r"[a-z]+|\d+", lower_value)
    return tuple(token for token in raw_tokens if token)


def _package_family(value: str) -> str:
    """Return coarse package family token for footprint matching."""
    canonical = canonicalize_name(value)
    if not canonical:
        return ""
    if "tssop" in canonical:
        return "tssop"
    if "msop" in canonical:
        return "msop"
    if "soic" in canonical or canonical.startswith("so"):
        return "soic"
    if "dip" in canonical or "dil" in canonical:
        return "dip"
    if "qfp" in canonical or "lqfp" in canonical:
        return "qfp"
    if "qfn" in canonical or "dfn" in canonical:
        return "qfn"
    if "sot" in canonical:
        return "sot"
    return ""


def _is_none_package(package_name: str) -> bool:
    """Return True when package name represents no footprint."""
    return normalize_name(package_name) in {"", "none", "-"}


def _pin_compatible(target_pin_count: int, candidate_pin_count: int) -> bool:
    """Return True when pin/pad counts are compatible."""
    if target_pin_count <= 0 or candidate_pin_count <= 0:
        return True
    return target_pin_count == candidate_pin_count


def _dedupe_kicad_symbols(symbols: Iterable[KiCadSymbolRow]) -> tuple[KiCadSymbolRow, ...]:
    """Dedupe symbol candidates in deterministic sorted order."""
    unique: dict[tuple[str, str], KiCadSymbolRow] = {}
    for symbol in symbols:
        key = (normalize_name(symbol.library_nickname), normalize_name(symbol.symbol_name))
        unique.setdefault(key, symbol)
    sorted_values = sorted(
        unique.values(),
        key=lambda item: (
            normalize_name(item.library_nickname),
            normalize_name(item.symbol_name),
        ),
    )
    return tuple(sorted_values)


def _dedupe_kicad_footprints(
    footprints: Iterable[KiCadFootprintRow],
) -> tuple[KiCadFootprintRow, ...]:
    """Dedupe footprint candidates in deterministic sorted order."""
    unique: dict[tuple[str, str], KiCadFootprintRow] = {}
    for footprint in footprints:
        key = (
            normalize_name(footprint.library_nickname),
            normalize_name(footprint.footprint_name),
        )
        unique.setdefault(key, footprint)
    sorted_values = sorted(
        unique.values(),
        key=lambda item: (
            normalize_name(item.library_nickname),
            normalize_name(item.footprint_name),
        ),
    )
    return tuple(sorted_values)


def _canonicalize_rows(rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, str], ...]:
    """Normalize and sort row dictionaries deterministically."""
    normalized_rows = [_normalize_row_dict(row) for row in rows]
    all_keys = sorted({key for row in normalized_rows for key in row})

    def sort_key(row: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(row.get(key, "") for key in all_keys)

    sorted_rows = sorted(normalized_rows, key=sort_key)
    return tuple(sorted_rows)


def _normalize_row_dict(row: Mapping[str, str]) -> dict[str, str]:
    """Normalize row dictionary values to deterministic strings."""
    return {str(key): str(value).strip() for key, value in row.items()}


def _find_subset_match_index(
    expected_row: Mapping[str, str],
    actual_rows: Sequence[Mapping[str, str]],
) -> int:
    """Find first actual row index that satisfies expected subset columns."""
    for index, actual_row in enumerate(actual_rows):
        if all(actual_row.get(key, "") == value for key, value in expected_row.items()):
            return index
    return -1
