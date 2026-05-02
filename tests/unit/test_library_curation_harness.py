"""Unit tests for the library_curation Behave harness."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.steps.library_curation_harness import AuditOptionRow
from features.steps.library_curation_harness import CuratedSymbolAuditRow
from features.steps.library_curation_harness import EagleDeviceRow
from features.steps.library_curation_harness import EagleSymbolRow
from features.steps.library_curation_harness import KiCadFootprintRow
from features.steps.library_curation_harness import KiCadSymbolRow
from features.steps.library_curation_harness import LibraryCurationScenarioInput
from features.steps.library_curation_harness import SourceSymbolHashRow
from features.steps.library_curation_harness import assert_rows_include
from features.steps.library_curation_harness import canonical_csv_bytes
from features.steps.library_curation_harness import canonical_json_bytes
from features.steps.library_curation_harness import run_library_curation_capability
from features.steps.library_curation_harness import run_library_curation_command


class TestLibraryCurationHarness(unittest.TestCase):
    """Coverage for deterministic harness behavior and core utility helpers."""

    def test_provenance_audit_status_matrix(self) -> None:
        """Classify provenance drift statuses for the in-sync/local/source/both matrix."""
        cases = (
            ("H1", "H2", "H1", "H2", "in_sync"),
            ("H1", "H2", "H1", "H2_EDIT", "local_changed"),
            ("H1", "H2", "H1_EDIT", "H2", "source_changed"),
            ("H1", "H2", "H1_EDIT", "H2_EDIT", "both_changed"),
        )
        for origin_hash, stored_local_hash, source_hash_now, curated_hash_now, expected_status in cases:
            scenario_input = LibraryCurationScenarioInput(
                curated_symbols=(
                    CuratedSymbolAuditRow(
                        symbol_name="R",
                        e2k_provenance=f"Device:R,{origin_hash},{stored_local_hash}",
                        curated_hash_now=curated_hash_now,
                    ),
                ),
                source_symbol_hashes=(
                    SourceSymbolHashRow(
                        library_nickname="Device",
                        symbol_name="R",
                        source_hash_now=source_hash_now,
                    ),
                ),
            )
            result = run_library_curation_capability(
                scenario_input,
                capability="provenance_audit",
                workflow="library_curation",
            )
            self.assertTrue(result.success)
            self.assertEqual(result.csv_output_rows[0]["AuditStatus"], expected_status)

    def test_unmanaged_rematch_uses_normalized_similarity(self) -> None:
        """Mark unmanaged symbols as rematched only when normalized names are similar."""
        scenario_input = LibraryCurationScenarioInput(
            curated_symbols=(
                CuratedSymbolAuditRow(
                    symbol_name="LOCAL_ONLY_SENSOR",
                    e2k_provenance="",
                    curated_hash_now="H_LOCAL_ONLY",
                ),
            ),
            source_symbol_hashes=(
                SourceSymbolHashRow(
                    library_nickname="Sensor_Generic",
                    symbol_name="SENSOR_6PIN",
                    source_hash_now="H_SENS_6PIN",
                ),
            ),
            audit_options=(AuditOptionRow(option="rematch_mode", value="true"),),
        )
        result = run_library_curation_capability(
            scenario_input,
            capability="provenance_audit",
            workflow="library_curation",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.csv_output_rows[0]["AuditStatus"], "unmanaged")
        self.assertEqual(result.csv_output_rows[0]["RematchStatus"], "rematched")
        self.assertEqual(result.csv_output_rows[0]["Confidence"], "low")

    def test_assert_rows_include_supports_subset_multiset_semantics(self) -> None:
        """Require one actual match per expected row, even for duplicate expected rows."""
        actual_rows = (
            {"A": "1", "B": "x", "Extra": "first"},
            {"A": "1", "B": "x", "Extra": "second"},
        )
        expected_rows = (
            {"A": "1", "B": "x"},
            {"A": "1", "B": "x"},
        )
        assert_rows_include(
            expected_rows=expected_rows,
            actual_rows=actual_rows,
            dataset_name="row matcher",
        )
        with self.assertRaises(AssertionError):
            assert_rows_include(
                expected_rows=(
                    {"A": "1", "B": "x"},
                    {"A": "1", "B": "x"},
                    {"A": "1", "B": "x"},
                ),
                actual_rows=actual_rows,
                dataset_name="row matcher",
            )

    def test_canonical_serialization_is_deterministic_for_row_order(self) -> None:
        """Produce identical canonical JSON/CSV bytes for semantically equivalent row sets."""
        rows_a = (
            {"DeviceKey": "B", "Classification": "x"},
            {"DeviceKey": "A", "Classification": "y"},
        )
        rows_b = (
            {"Classification": "y", "DeviceKey": "A"},
            {"Classification": "x", "DeviceKey": "B"},
        )
        self.assertEqual(canonical_json_bytes(rows_a), canonical_json_bytes(rows_b))
        self.assertEqual(canonical_csv_bytes(rows_a), canonical_csv_bytes(rows_b))

    def test_curated_generation_profile_decision_path(self) -> None:
        """Exercise a curated-generation path with role filtering and exact matching."""
        scenario_input = LibraryCurationScenarioInput(
            eagle_symbols=(
                EagleSymbolRow(symbol_name="R", role="functional_component", pin_count=2),
                EagleSymbolRow(symbol_name="AGND", role="schematic_annotation", pin_count=1),
            ),
            eagle_devices=(
                EagleDeviceRow(
                    deviceset="RESISTOR",
                    device="R0603",
                    symbol_name="R",
                    package_name="R_0603_1608Metric",
                    mapped_pin_count=2,
                ),
                EagleDeviceRow(
                    deviceset="AGND",
                    device="default",
                    symbol_name="AGND",
                    package_name="NONE",
                    mapped_pin_count=0,
                ),
            ),
            kicad_symbols=(
                KiCadSymbolRow(
                    library_nickname="Device",
                    symbol_name="R",
                    pin_count=2,
                    default_footprint="",
                    source_hash="H_ORIGIN_R",
                ),
                KiCadSymbolRow(
                    library_nickname="power",
                    symbol_name="GNDA",
                    pin_count=1,
                    default_footprint="",
                    source_hash="",
                ),
            ),
            kicad_footprints=(
                KiCadFootprintRow(
                    library_nickname="Resistor_SMD",
                    footprint_name="R_0603_1608Metric",
                    pad_count=2,
                ),
            ),
            role_filtering_overrides=(),
        )
        result = run_library_curation_command(
            scenario_input,
            command="library-curation --profile curated_generation",
        )
        self.assertTrue(result.success)
        assert_rows_include(
            expected_rows=(
                {"DeviceKey": "RESISTOR:R0603", "Classification": "exact_match"},
                {"DeviceKey": "AGND:default", "Classification": "semantic_match"},
            ),
            actual_rows=result.decision_report_rows,
            dataset_name="decision report",
        )
        assert_rows_include(
            expected_rows=(
                {"SymbolName": "R", "Origin": "copied_kicad"},
                {"SymbolName": "AGND", "Origin": "copied_kicad"},
            ),
            actual_rows=result.curated_symbol_rows,
            dataset_name="curated symbols",
        )


if __name__ == "__main__":
    unittest.main()
