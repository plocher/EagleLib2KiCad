"""Unit tests for KiCad environment path-variable resolution behavior."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from eaglelib2kicad.services.kicad_environment_service import KiCadEnvironmentService


class TestKiCadEnvironmentService(unittest.TestCase):
    """Tests for runtime KiCad URI variable expansion semantics."""

    def setUp(self) -> None:
        self.service = KiCadEnvironmentService()

    def test_symbol_uri_resolves_using_kicad_common_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            config_home = temp_root / "9.0"
            config_home.mkdir(parents=True, exist_ok=True)
            _write_table(
                table_path=config_home / "sym-lib-table",
                table_name="sym_lib_table",
                nickname="Device",
                uri="${KICAD9_SYMBOL_DIR}/Device.kicad_sym",
            )
            _write_kicad_common(
                config_home=config_home,
                variables={"KICAD9_SYMBOL_DIR": "/opt/kicad/symbols"},
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KICAD9_SYMBOL_DIR", None)
                snapshot = self.service.discover_configured_libraries(config_home=config_home)
            self.assertEqual(len(snapshot.symbol_libraries), 1)
            self.assertEqual(
                snapshot.symbol_libraries[0].resolved_path,
                Path("/opt/kicad/symbols/Device.kicad_sym"),
            )

    def test_project_text_variables_override_global_variables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            config_home = temp_root / "9.0"
            project_directory = temp_root / "example-project"
            config_home.mkdir(parents=True, exist_ok=True)
            project_directory.mkdir(parents=True, exist_ok=True)

            _write_table(
                table_path=project_directory / "sym-lib-table",
                table_name="sym_lib_table",
                nickname="Custom",
                uri="${KICAD_USER_LIB}/symbols/Custom.kicad_sym",
            )
            _write_kicad_common(
                config_home=config_home,
                variables={"KICAD_USER_LIB": "/global/libs"},
            )
            _write_project_file(
                project_file_path=project_directory / "example-project.kicad_pro",
                text_variables={"KICAD_USER_LIB": "/project/libs"},
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KICAD_USER_LIB", None)
                snapshot = self.service.discover_configured_libraries(
                    config_home=config_home,
                    project_directory=project_directory,
                )
            self.assertEqual(len(snapshot.symbol_libraries), 1)
            self.assertEqual(
                snapshot.symbol_libraries[0].resolved_path,
                Path("/project/libs/symbols/Custom.kicad_sym"),
            )

    def test_versioned_footprint_dir_can_be_derived_from_model_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            config_home = temp_root / "9.0"
            config_home.mkdir(parents=True, exist_ok=True)
            _write_table(
                table_path=config_home / "fp-lib-table",
                table_name="fp_lib_table",
                nickname="BuiltInFootprints",
                uri="${KICAD9_FOOTPRINT_DIR}",
            )
            _write_kicad_common(
                config_home=config_home,
                variables={"KICAD8_MODEL_DIR": "/opt/kicad/share/3dmodels"},
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KICAD9_FOOTPRINT_DIR", None)
                snapshot = self.service.discover_configured_libraries(config_home=config_home)
            self.assertEqual(len(snapshot.footprint_libraries), 1)
            self.assertEqual(
                snapshot.footprint_libraries[0].resolved_path,
                Path("/opt/kicad/share/footprints"),
            )


def _write_table(
    *,
    table_path: Path,
    table_name: str,
    nickname: str,
    uri: str,
) -> None:
    table_path.write_text(
        (
            f"({table_name}\n"
            f"  (lib (name \"{nickname}\") (type \"KiCad\") (uri \"{uri}\") (options \"\") (descr \"\"))\n"
            ")\n"
        ),
        encoding="utf-8",
    )


def _write_kicad_common(
    *,
    config_home: Path,
    variables: dict[str, str],
) -> None:
    (config_home / "kicad_common.json").write_text(
        json.dumps({"environment": {"vars": variables}}, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_project_file(
    *,
    project_file_path: Path,
    text_variables: dict[str, str],
) -> None:
    project_file_path.write_text(
        json.dumps({"meta": {"version": 1}, "text_variables": text_variables}, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
