"""Unit tests for migration analysis heuristics."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from eaglelib2kicad.services.eagle_library_context_service import EagleDeviceContext
from eaglelib2kicad.services.kicad_library_context_service import (
    FootprintClosureReport,
    FootprintLibraryItem,
    KiCadLibraryContextSnapshot,
    SymbolContext,
)
from eaglelib2kicad.services.library_migration_analysis_service import (
    LibraryMigrationAnalysisService,
)


class TestLibraryMigrationAnalysisService(unittest.TestCase):
    """Contract-style unit tests for importer-side analysis heuristics."""

    def setUp(self) -> None:
        self.service = LibraryMigrationAnalysisService()
        self.source_file = Path("/tmp/sample.lbr")

    def test_passive_with_symbol_and_package_match_gets_high_confidence(self) -> None:
        context = _build_context(
            symbols=[("TestSymbols", "R", 2)],
            footprints=[("TestFootprints", "R_0603_1608Metric", 2)],
        )
        devices = [
            _device(
                deviceset_name="RESISTOR",
                device_name="R0603",
                symbol_name="R",
                package_name="R_0603_1608Metric",
                symbol_pin_count=2,
                mapped_pin_count=2,
                package_pad_count=2,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].pathway, "commodity_passive")
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "none")
        self.assertFalse(rows[0].review_required)
        self.assertEqual(rows[0].reasons, tuple())

    def test_connector_with_missing_package_is_standard_review(self) -> None:
        context = _build_context(
            symbols=[("TestSymbols", "USB_C_Receptacle", 16)],
            footprints=[("Connector", "RJ45_Horizontal", 8)],
        )
        devices = [
            _device(
                deviceset_name="USB-C",
                device_name="RECEPTACLE",
                symbol_name="USB_C_Receptacle",
                package_name="USB_C_HRO_TYPEC_31_M_12",
                symbol_pin_count=16,
                mapped_pin_count=16,
                package_pad_count=16,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "connector_switch_mechanical")
        self.assertEqual(rows[0].confidence, "medium")
        self.assertEqual(rows[0].review_queue, "standard")
        self.assertTrue(rows[0].review_required)
        self.assertIn(
            "package_not_present_in_loaded_kicad_footprint_libraries",
            rows[0].reasons,
        )

    def test_ic_with_missing_symbol_and_package_is_priority_review(self) -> None:
        context = _build_context(symbols=[], footprints=[])
        devices = [
            _device(
                deviceset_name="TPS7A47",
                device_name="SOT223",
                symbol_name="TPS7A47",
                package_name="SOT223",
                symbol_pin_count=5,
                mapped_pin_count=5,
                package_pad_count=5,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "ic_regulator_specialty")
        self.assertEqual(rows[0].confidence, "low")
        self.assertEqual(rows[0].review_queue, "priority")
        self.assertTrue(rows[0].review_required)
        self.assertIn("symbol_not_present_in_loaded_kicad_libraries", rows[0].reasons)
        self.assertIn(
            "package_not_present_in_loaded_kicad_footprint_libraries",
            rows[0].reasons,
        )

    def test_uncategorized_device_requires_standard_review_even_if_matched(self) -> None:
        context = _build_context(
            symbols=[("Custom", "CUSTOM", 4)],
            footprints=[("Custom", "CUSTOM_FP", 4)],
        )
        devices = [
            _device(
                deviceset_name="WIDGET",
                device_name="REV_A",
                symbol_name="CUSTOM",
                package_name="CUSTOM_FP",
                symbol_pin_count=4,
                mapped_pin_count=4,
                package_pad_count=4,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "uncategorized")
        self.assertEqual(rows[0].confidence, "medium")
        self.assertEqual(rows[0].review_queue, "standard")
        self.assertTrue(rows[0].review_required)
        self.assertIn("category_not_classified_for_policy_pathway", rows[0].reasons)

    def test_power_symbol_from_power_library_gets_no_review(self) -> None:
        context = _build_context(symbols=[("power", "+3V3", 1)], footprints=[])
        devices = [
            _device(
                deviceset_name="+3V3",
                device_name="default",
                symbol_name="+3V3",
                package_name="",
                symbol_pin_count=1,
                mapped_pin_count=0,
                package_pad_count=0,
                is_power_symbol=True,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "schematic_annotation")
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "none")
        self.assertEqual(rows[0].reasons, tuple())
        self.assertEqual(rows[0].symbol_library, "power")

    def test_power_annotation_without_symbol_match_is_standard_review(self) -> None:
        context = _build_context(symbols=[], footprints=[])
        devices = [
            _device(
                deviceset_name="AGND",
                device_name="default",
                symbol_name="AGND",
                package_name="",
                symbol_pin_count=1,
                mapped_pin_count=0,
                package_pad_count=0,
                is_power_symbol=True,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "schematic_annotation")
        self.assertEqual(rows[0].confidence, "medium")
        self.assertEqual(rows[0].review_queue, "standard")
        self.assertIn("symbol_not_present_in_loaded_kicad_libraries", rows[0].reasons)

    def test_power_alias_resolves_agnd_to_gnda_with_review_reason(self) -> None:
        context = _build_context(symbols=[("power", "GNDA", 1)], footprints=[])
        devices = [
            _device(
                deviceset_name="AGND",
                device_name="default",
                symbol_name="AGND",
                package_name="",
                symbol_pin_count=1,
                mapped_pin_count=0,
                package_pad_count=0,
                is_power_symbol=True,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "schematic_annotation")
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "standard")
        self.assertNotIn("symbol_not_present_in_loaded_kicad_libraries", rows[0].reasons)
        self.assertIn("symbol_semantic_fallback_match_needs_review", rows[0].reasons)
        self.assertEqual(rows[0].symbol_library, "power")

    def test_connector_compact_name_resolves_to_generic_connector_symbol(self) -> None:
        context = _build_context(
            symbols=[("Connector_Generic", "Conn_01x06", 6)],
            footprints=[("Connector_PinHeader_2.54mm", "PinHeader_1x06_P2.54mm_Vertical", 6)],
        )
        devices = [
            _device(
                deviceset_name="M06",
                device_name="HDR",
                symbol_name="M06",
                package_name="PinHeader_1x06_P2.54mm_Vertical",
                symbol_pin_count=6,
                mapped_pin_count=6,
                package_pad_count=6,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].pathway, "connector_switch_mechanical")
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "standard")
        self.assertEqual(rows[0].symbol_library, "Connector_Generic")
        self.assertNotIn("symbol_not_present_in_loaded_kicad_libraries", rows[0].reasons)
        self.assertIn("symbol_semantic_fallback_match_needs_review", rows[0].reasons)

    def test_dil_package_resolves_to_dip_family_footprint(self) -> None:
        context = _build_context(
            symbols=[("Device", "LM358", 8)],
            footprints=[("Package_DIP", "DIP-8_W7.62mm", 8)],
        )
        devices = [
            _device(
                deviceset_name="LM358",
                device_name="DIL08",
                symbol_name="LM358",
                package_name="DIL08",
                symbol_pin_count=8,
                mapped_pin_count=8,
                package_pad_count=8,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "none")
        self.assertNotIn(
            "package_not_present_in_loaded_kicad_footprint_libraries",
            rows[0].reasons,
        )
        self.assertEqual(rows[0].footprint_library, "Package_DIP")

    def test_so_package_resolves_to_soic_family_footprint(self) -> None:
        context = _build_context(
            symbols=[("Device", "LM324", 14)],
            footprints=[("Package_SO", "SOIC-14_3.9x8.7mm_P1.27mm", 14)],
        )
        devices = [
            _device(
                deviceset_name="LM324",
                device_name="SO14",
                symbol_name="LM324",
                package_name="SO14",
                symbol_pin_count=14,
                mapped_pin_count=14,
                package_pad_count=14,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].confidence, "high")
        self.assertEqual(rows[0].review_queue, "none")
        self.assertNotIn(
            "package_not_present_in_loaded_kicad_footprint_libraries",
            rows[0].reasons,
        )
        self.assertEqual(rows[0].footprint_library, "Package_SO")

    def test_package_alias_match_with_pin_mismatch_becomes_priority(self) -> None:
        context = _build_context(
            symbols=[("Device", "TPS7A47", 5)],
            footprints=[("Package_TO_SOT_SMD", "SOT-223", 4)],
        )
        devices = [
            _device(
                deviceset_name="TPS7A47",
                device_name="SOT223",
                symbol_name="TPS7A47",
                package_name="SOT223",
                symbol_pin_count=5,
                mapped_pin_count=5,
                package_pad_count=5,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertEqual(rows[0].confidence, "low")
        self.assertEqual(rows[0].review_queue, "priority")
        self.assertIn("footprint_pad_count_mismatch_with_eagle", rows[0].reasons)
        self.assertNotIn(
            "package_not_present_in_loaded_kicad_footprint_libraries",
            rows[0].reasons,
        )
        self.assertEqual(rows[0].footprint_library, "Package_TO_SOT_SMD")

    def test_symbol_alias_resolves_l_us_to_device_library(self) -> None:
        context = _build_context(
            symbols=[("Device", "L", 2)],
            footprints=[("Inductor_SMD", "L_0805_2012Metric", 2)],
        )
        devices = [
            _device(
                deviceset_name="INDUCTOR",
                device_name="L0805",
                symbol_name="L-US",
                package_name="L_0805_2012Metric",
                symbol_pin_count=2,
                mapped_pin_count=2,
                package_pad_count=2,
            )
        ]

        rows = self.service.analyze(eagle_devices=devices, kicad_context=context)
        self.assertNotIn("symbol_not_present_in_loaded_kicad_libraries", rows[0].reasons)
        self.assertEqual(rows[0].symbol_library, "Device")


def _device(
    *,
    deviceset_name: str,
    device_name: str,
    symbol_name: str,
    package_name: str,
    symbol_pin_count: int,
    mapped_pin_count: int,
    package_pad_count: int,
    is_power_symbol: bool = False,
) -> EagleDeviceContext:
    return EagleDeviceContext(
        deviceset_name=deviceset_name,
        device_name=device_name,
        symbol_name=symbol_name,
        package_name=package_name,
        symbol_pin_count=symbol_pin_count,
        mapped_pin_count=mapped_pin_count,
        package_pad_count=package_pad_count,
        is_power_symbol=is_power_symbol,
        source_file=Path("/tmp/sample.lbr"),
    )


def _build_context(
    *,
    symbols: list[tuple[str, str, int]],
    footprints: list[tuple[str, str, int]],
) -> KiCadLibraryContextSnapshot:
    symbol_contexts = tuple(
        SymbolContext(
            library_nickname=library,
            symbol_name=name,
            lib_id=f"{library}:{name}",
            footprint_ref="",
            pin_count=pin_count,
        )
        for library, name, pin_count in symbols
    )
    footprint_contexts = tuple(
        FootprintLibraryItem(
            library_nickname=library,
            footprint_name=name,
            source_path=Path(f"/tmp/{name}.kicad_mod"),
            pad_count=pad_count,
        )
        for library, name, pad_count in footprints
    )
    return KiCadLibraryContextSnapshot(
        symbols=symbol_contexts,
        footprints=footprint_contexts,
        closure_report=FootprintClosureReport(
            resolved_count=0,
            unresolved_count=0,
            ambiguous_count=0,
            unresolved=tuple(),
            ambiguous=tuple(),
        ),
    )


if __name__ == "__main__":
    unittest.main()
