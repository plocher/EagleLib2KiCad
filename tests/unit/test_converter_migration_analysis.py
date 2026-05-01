"""Unit tests for converter-side migration analysis artifact generation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from eaglelib2kicad.services.library_migration_analysis_service import MigrationAnalysisRow


def _load_converter_module() -> ModuleType:
    """Load tools/eagle_to_kicad_converter.py as an importable module."""
    module_path = PROJECT_ROOT / "tools" / "eagle_to_kicad_converter.py"
    spec = importlib.util.spec_from_file_location("eagle_to_kicad_converter", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load eagle_to_kicad_converter module spec.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestConverterMigrationAnalysis(unittest.TestCase):
    """Tests for converter integration with migration-analysis services."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.converter = _load_converter_module()

    def test_build_migration_analysis_artifact_counts(self) -> None:
        rows = [
            MigrationAnalysisRow(
                device_key="RESISTOR:R0603",
                pathway="commodity_passive",
                symbol_name="R",
                package_name="R_0603_1608Metric",
                confidence="high",
                review_queue="none",
                review_required=False,
                reasons=tuple(),
            ),
            MigrationAnalysisRow(
                device_key="TPS7A47:SOT223",
                pathway="ic_regulator_specialty",
                symbol_name="TPS7A47",
                package_name="SOT223",
                confidence="low",
                review_queue="priority",
                review_required=True,
                reasons=(
                    "symbol_not_present_in_loaded_kicad_libraries",
                    "package_not_present_in_loaded_kicad_footprint_libraries",
                ),
            ),
        ]
        artifact = self.converter.build_migration_analysis_artifact(
            eagle_library=Path("/tmp/parts.lbr"),
            kicad_config_home=Path("/tmp/kicad-config"),
            kicad_project_directory=None,
            rows=rows,
        )
        self.assertEqual(artifact.total_devices, 2)
        self.assertEqual(artifact.queue_counts["none"], 1)
        self.assertEqual(artifact.queue_counts["priority"], 1)
        self.assertEqual(artifact.confidence_counts["high"], 1)
        self.assertEqual(artifact.confidence_counts["low"], 1)
        payload = artifact.to_json_dict()
        self.assertEqual(payload["total_devices"], 2)
        self.assertEqual(len(payload["rows"]), 2)

    def test_run_migration_analysis_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            eagle_library_path = tmp_path / "sample.lbr"
            config_home = tmp_path / "kicad-config"
            symbols_file = tmp_path / "TestSymbols.kicad_sym"
            footprints_dir = tmp_path / "TestFootprints.pretty"
            config_home.mkdir(parents=True, exist_ok=True)
            footprints_dir.mkdir(parents=True, exist_ok=True)

            eagle_library_path.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<eagle>
  <drawing>
    <library>
      <devicesets>
        <deviceset name="RESISTOR">
          <gates>
            <gate name="G$1" symbol="R"/>
          </gates>
          <devices>
            <device name="R0603" package="R_0603_1608Metric"/>
          </devices>
        </deviceset>
        <deviceset name="TPS7A47">
          <gates>
            <gate name="G$1" symbol="TPS7A47"/>
          </gates>
          <devices>
            <device name="SOT223" package="SOT223"/>
          </devices>
        </deviceset>
      </devicesets>
    </library>
  </drawing>
</eagle>
""",
                encoding="utf-8",
            )
            symbols_file.write_text(
                """(kicad_symbol_lib
  (symbol "R"
    (property "Reference" "R")
    (property "Footprint" "R_0603_1608Metric")
  )
)
""",
                encoding="utf-8",
            )
            (footprints_dir / "R_0603_1608Metric.kicad_mod").write_text(
                '(footprint "R_0603_1608Metric" (layer "F.Cu"))\n',
                encoding="utf-8",
            )
            (config_home / "sym-lib-table").write_text(
                f"""(sym_lib_table
  (lib (name "TestSymbols") (type "KiCad") (uri "{symbols_file}") (options "") (descr ""))
)
""",
                encoding="utf-8",
            )
            (config_home / "fp-lib-table").write_text(
                f"""(fp_lib_table
  (lib (name "TestFootprints") (type "KiCad") (uri "{footprints_dir}") (options "") (descr ""))
)
""",
                encoding="utf-8",
            )

            artifact = self.converter.run_migration_analysis(
                eagle_library=eagle_library_path,
                kicad_config_home=config_home,
            )
            self.assertEqual(artifact.total_devices, 2)
            self.assertEqual(artifact.queue_counts["none"], 1)
            self.assertEqual(artifact.queue_counts["priority"], 1)
            row_by_key = {row.device_key: row for row in artifact.rows}
            self.assertEqual(
                row_by_key["RESISTOR:R0603"].pathway,
                "commodity_passive",
            )
            self.assertEqual(
                row_by_key["RESISTOR:R0603"].review_queue,
                "none",
            )
            self.assertEqual(
                row_by_key["TPS7A47:SOT223"].review_queue,
                "priority",
            )


if __name__ == "__main__":
    unittest.main()
