"""KiCad environment and library table services.

This module provides service APIs for:
- discovering configured symbol/footprint libraries with nicknames
- mutating library tables (add/remove/rename)
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Literal, Mapping, Sequence

from eaglelib2kicad.common.sexp import SexpParseError, parse_sexp

LibraryType = Literal["symbol", "footprint"]
LibraryScope = Literal["global", "project"]

_URI_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


@dataclass(frozen=True)
class KiCadLibraryEntry:
    """One configured KiCad library entry."""

    library_type: LibraryType
    scope: LibraryScope
    nickname: str
    uri: str
    resolved_path: Path
    table_path: Path


@dataclass(frozen=True)
class KiCadEnvironmentSnapshot:
    """Configured library snapshot discovered from KiCad tables."""

    config_home: Path
    symbol_libraries: tuple[KiCadLibraryEntry, ...]
    footprint_libraries: tuple[KiCadLibraryEntry, ...]
    symbol_table_paths: tuple[Path, ...]
    footprint_table_paths: tuple[Path, ...]


@dataclass(frozen=True)
class LibraryMutationResult:
    """Outcome of a library-table mutation operation."""

    operation: str
    library_type: LibraryType
    scope: LibraryScope
    table_path: Path
    nickname: str
    applied: bool
    details: str


class KiCadEnvironmentService:
    """Discover and mutate KiCad symbol/footprint library configuration."""

    def discover_configured_libraries(
        self,
        *,
        project_directory: Path | None = None,
        config_home: Path | None = None,
    ) -> KiCadEnvironmentSnapshot:
        """Load configured KiCad libraries from global/project tables."""
        detected_config_home = self._detect_config_home(config_home)
        path_variables = self._build_path_variables(
            config_home=detected_config_home,
            project_directory=project_directory,
        )
        symbol_entries, symbol_tables = self._collect_entries(
            library_type="symbol",
            project_directory=project_directory,
            config_home=detected_config_home,
            path_variables=path_variables,
        )
        footprint_entries, footprint_tables = self._collect_entries(
            library_type="footprint",
            project_directory=project_directory,
            config_home=detected_config_home,
            path_variables=path_variables,
        )
        return KiCadEnvironmentSnapshot(
            config_home=detected_config_home,
            symbol_libraries=tuple(symbol_entries),
            footprint_libraries=tuple(footprint_entries),
            symbol_table_paths=tuple(symbol_tables),
            footprint_table_paths=tuple(footprint_tables),
        )

    def add_library(
        self,
        *,
        library_type: LibraryType,
        scope: LibraryScope,
        nickname: str,
        uri: str,
        project_directory: Path | None = None,
        config_home: Path | None = None,
        apply: bool = False,
    ) -> LibraryMutationResult:
        """Add a library entry to KiCad configuration."""
        table_path = self._table_path_for_scope(
            library_type=library_type,
            scope=scope,
            project_directory=project_directory,
            config_home=self._detect_config_home(config_home),
        )
        records = self._load_table_records(table_path)

        normalized_nickname = nickname.strip()
        if not normalized_nickname:
            raise ValueError("Nickname cannot be empty")
        if any(record["name"] == normalized_nickname for record in records):
            raise ValueError(f"Library nickname already exists: {normalized_nickname}")

        records.append(
            {
                "name": normalized_nickname,
                "type": "KiCad",
                "uri": uri.strip(),
                "options": "",
                "descr": "",
            }
        )

        if apply:
            self._write_table_records(
                table_path=table_path,
                library_type=library_type,
                records=records,
            )
        return LibraryMutationResult(
            operation="add",
            library_type=library_type,
            scope=scope,
            table_path=table_path,
            nickname=normalized_nickname,
            applied=apply,
            details="added" if apply else "planned",
        )

    def remove_library(
        self,
        *,
        library_type: LibraryType,
        scope: LibraryScope,
        nickname: str,
        project_directory: Path | None = None,
        config_home: Path | None = None,
        apply: bool = False,
    ) -> LibraryMutationResult:
        """Remove a library entry from KiCad configuration."""
        table_path = self._table_path_for_scope(
            library_type=library_type,
            scope=scope,
            project_directory=project_directory,
            config_home=self._detect_config_home(config_home),
        )
        records = self._load_table_records(table_path)
        normalized_nickname = nickname.strip()
        updated = [record for record in records if record["name"] != normalized_nickname]

        if len(updated) == len(records):
            raise ValueError(f"Library nickname not found: {normalized_nickname}")

        if apply:
            self._write_table_records(
                table_path=table_path,
                library_type=library_type,
                records=updated,
            )
        return LibraryMutationResult(
            operation="remove",
            library_type=library_type,
            scope=scope,
            table_path=table_path,
            nickname=normalized_nickname,
            applied=apply,
            details="removed" if apply else "planned",
        )

    def rename_library(
        self,
        *,
        library_type: LibraryType,
        scope: LibraryScope,
        old_nickname: str,
        new_nickname: str,
        project_directory: Path | None = None,
        config_home: Path | None = None,
        apply: bool = False,
    ) -> LibraryMutationResult:
        """Rename a configured library nickname."""
        table_path = self._table_path_for_scope(
            library_type=library_type,
            scope=scope,
            project_directory=project_directory,
            config_home=self._detect_config_home(config_home),
        )
        records = self._load_table_records(table_path)
        old_name = old_nickname.strip()
        replacement = new_nickname.strip()

        if not replacement:
            raise ValueError("New nickname cannot be empty")
        if any(record["name"] == replacement for record in records):
            raise ValueError(f"Target nickname already exists: {replacement}")

        changed = False
        for record in records:
            if record["name"] == old_name:
                record["name"] = replacement
                changed = True
                break
        if not changed:
            raise ValueError(f"Library nickname not found: {old_name}")

        if apply:
            self._write_table_records(
                table_path=table_path,
                library_type=library_type,
                records=records,
            )
        return LibraryMutationResult(
            operation="rename",
            library_type=library_type,
            scope=scope,
            table_path=table_path,
            nickname=replacement,
            applied=apply,
            details=f"{old_name} -> {replacement}" if apply else f"planned {old_name} -> {replacement}",
        )

    def _collect_entries(
        self,
        *,
        library_type: LibraryType,
        project_directory: Path | None,
        config_home: Path,
        path_variables: Mapping[str, str],
    ) -> tuple[list[KiCadLibraryEntry], list[Path]]:
        """Collect table entries from global and optional project scopes."""
        entries: list[KiCadLibraryEntry] = []
        table_paths: list[Path] = []

        global_table = self._table_path_for_scope(
            library_type=library_type,
            scope="global",
            project_directory=project_directory,
            config_home=config_home,
        )
        if global_table.exists():
            table_paths.append(global_table)
            entries.extend(
                self._entries_from_table(
                    table_path=global_table,
                    library_type=library_type,
                    scope="global",
                    project_directory=project_directory,
                    path_variables=path_variables,
                )
            )

        if project_directory is not None:
            project_table = self._table_path_for_scope(
                library_type=library_type,
                scope="project",
                project_directory=project_directory,
                config_home=config_home,
            )
            if project_table.exists():
                table_paths.append(project_table)
                entries.extend(
                    self._entries_from_table(
                        table_path=project_table,
                        library_type=library_type,
                        scope="project",
                        project_directory=project_directory,
                        path_variables=path_variables,
                    )
                )
        return entries, table_paths

    def _entries_from_table(
        self,
        *,
        table_path: Path,
        library_type: LibraryType,
        scope: LibraryScope,
        project_directory: Path | None,
        path_variables: Mapping[str, str],
    ) -> list[KiCadLibraryEntry]:
        """Parse one table file into strongly typed entries."""
        records = self._load_table_records(table_path)
        entries: list[KiCadLibraryEntry] = []
        for record in records:
            uri = record.get("uri", "")
            nickname = record.get("name", "").strip()
            if not nickname or not uri:
                continue
            entries.append(
                KiCadLibraryEntry(
                    library_type=library_type,
                    scope=scope,
                    nickname=nickname,
                    uri=uri,
                    resolved_path=self._resolve_uri(
                        uri=uri,
                        library_type=library_type,
                        project_directory=project_directory,
                        path_variables=path_variables,
                    ),
                    table_path=table_path,
                )
            )
        return entries

    def _load_table_records(self, table_path: Path) -> list[dict[str, str]]:
        """Load raw lib records from a KiCad library table file."""
        text = table_path.read_text(encoding="utf-8")
        try:
            root = parse_sexp(text)
        except SexpParseError as exc:
            raise ValueError(f"Failed to parse KiCad table {table_path}: {exc}") from exc

        if not root:
            return []
        records: list[dict[str, str]] = []
        for item in root[1:]:
            if not isinstance(item, list) or not item:
                continue
            if item[0] != "lib":
                continue
            record: dict[str, str] = {}
            for field in item[1:]:
                if not isinstance(field, list) or len(field) < 2:
                    continue
                key = str(field[0])
                value = str(field[1])
                record[key] = value
            records.append(record)
        return records

    def _write_table_records(
        self,
        *,
        table_path: Path,
        library_type: LibraryType,
        records: Sequence[dict[str, str]],
    ) -> None:
        """Write records to a KiCad library table file."""
        table_path.parent.mkdir(parents=True, exist_ok=True)
        root_name = "sym_lib_table" if library_type == "symbol" else "fp_lib_table"
        lines = [f"({root_name}"]
        for record in records:
            name = self._escape(record.get("name", ""))
            lib_kind = self._escape(record.get("type", "KiCad"))
            uri = self._escape(record.get("uri", ""))
            options = self._escape(record.get("options", ""))
            descr = self._escape(record.get("descr", ""))
            lines.append(
                f'  (lib (name "{name}") (type "{lib_kind}") (uri "{uri}") (options "{options}") (descr "{descr}"))'
            )
        lines.append(")")
        table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _table_path_for_scope(
        self,
        *,
        library_type: LibraryType,
        scope: LibraryScope,
        project_directory: Path | None,
        config_home: Path,
    ) -> Path:
        table_name = "sym-lib-table" if library_type == "symbol" else "fp-lib-table"
        if scope == "global":
            return config_home / table_name
        if project_directory is None:
            raise ValueError("Project directory is required for project-scope table operations")
        return project_directory / table_name

    def _resolve_uri(
        self,
        *,
        uri: str,
        library_type: LibraryType,
        project_directory: Path | None,
        path_variables: Mapping[str, str],
    ) -> Path:
        """Resolve a KiCad URI with variable expansion."""
        del library_type
        expanded = uri
        for _ in range(8):
            replaced = _URI_VARIABLE_PATTERN.sub(
                lambda match: path_variables.get(match.group(1), match.group(0)),
                expanded,
            )
            if replaced == expanded:
                break
            expanded = replaced
        expanded_path = Path(expanded).expanduser()
        if expanded_path.is_absolute():
            return expanded_path
        if project_directory is not None:
            return (project_directory / expanded_path).resolve()
        return expanded_path.resolve()

    def _build_path_variables(
        self,
        *,
        config_home: Path,
        project_directory: Path | None,
    ) -> dict[str, str]:
        """Build effective KiCad variable map from global config, project, and env."""
        variables: dict[str, str] = {}
        variables.update(self._load_global_path_variables(config_home=config_home))
        variables.update(self._load_project_path_overrides(project_directory=project_directory))

        if project_directory is not None:
            variables["KIPRJMOD"] = str(project_directory)
        else:
            variables.setdefault("KIPRJMOD", "")
        variables.setdefault("KICAD_CONFIG_HOME", str(config_home))

        major_version = self._config_major_version(config_home)
        self._apply_kicad_directory_aliases(variables, major_version=major_version)

        for key, value in os.environ.items():
            if key.startswith("KICAD") or key == "KIPRJMOD":
                variables[key] = value
        self._apply_kicad_directory_aliases(variables, major_version=major_version)
        return variables

    def _load_global_path_variables(
        self,
        *,
        config_home: Path,
    ) -> dict[str, str]:
        """Load global KiCad path variables from kicad_common.json."""
        common_json_path = config_home / "kicad_common.json"
        if not common_json_path.exists():
            return {}
        try:
            data = json.loads(common_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        environment_block = data.get("environment", {})
        if not isinstance(environment_block, dict):
            return {}
        vars_block = environment_block.get("vars", {})
        if not isinstance(vars_block, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in vars_block.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _load_project_path_overrides(
        self,
        *,
        project_directory: Path | None,
    ) -> dict[str, str]:
        """Load project-level text variable overrides from .kicad_pro, if available."""
        project_file_path = self._project_file_path(project_directory)
        if project_file_path is None:
            return {}
        try:
            data = json.loads(project_file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        text_variables = data.get("text_variables", {})
        if not isinstance(text_variables, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in text_variables.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    @staticmethod
    def _project_file_path(project_directory: Path | None) -> Path | None:
        """Locate a KiCad project file in the given project directory."""
        if project_directory is None:
            return None
        preferred = project_directory / f"{project_directory.name}.kicad_pro"
        if preferred.exists():
            return preferred
        candidates = sorted(project_directory.glob("*.kicad_pro"))
        if len(candidates) == 1:
            return candidates[0]
        return None

    @staticmethod
    def _config_major_version(config_home: Path) -> str:
        """Extract KiCad major version token from config-home path if present."""
        match = re.search(r"(\d+)(?:\.\d+)?$", config_home.name)
        if match is None:
            return ""
        return match.group(1)

    def _apply_kicad_directory_aliases(
        self,
        variables: dict[str, str],
        *,
        major_version: str,
    ) -> None:
        """Bridge versioned and unversioned KiCad directory variable aliases."""
        model_roots = self._kicad_share_roots_from_model_vars(variables)
        current_root = model_roots.get(major_version, "")
        if not current_root and model_roots:
            current_root = model_roots[sorted(model_roots.keys())[-1]]

        if major_version and current_root:
            variables.setdefault(
                f"KICAD{major_version}_SYMBOL_DIR",
                str(Path(current_root) / "symbols"),
            )
            variables.setdefault(
                f"KICAD{major_version}_FOOTPRINT_DIR",
                str(Path(current_root) / "footprints"),
            )

        if major_version:
            versioned_symbol_key = f"KICAD{major_version}_SYMBOL_DIR"
            versioned_footprint_key = f"KICAD{major_version}_FOOTPRINT_DIR"
            if versioned_symbol_key in variables:
                variables.setdefault("KICAD_SYMBOL_DIR", variables[versioned_symbol_key])
            if versioned_footprint_key in variables:
                variables.setdefault("KICAD_FOOTPRINT_DIR", variables[versioned_footprint_key])
            if "KICAD_SYMBOL_DIR" in variables:
                variables.setdefault(versioned_symbol_key, variables["KICAD_SYMBOL_DIR"])
            if "KICAD_FOOTPRINT_DIR" in variables:
                variables.setdefault(versioned_footprint_key, variables["KICAD_FOOTPRINT_DIR"])

    @staticmethod
    def _kicad_share_roots_from_model_vars(variables: Mapping[str, str]) -> dict[str, str]:
        """Derive SharedSupport roots from versioned model-dir variables when present."""
        roots: dict[str, str] = {}
        for key, raw_value in variables.items():
            match = re.fullmatch(r"KICAD(\d+)_MODEL_DIR", key)
            if match is None:
                continue
            model_dir = Path(raw_value).expanduser()
            folder_name = model_dir.name.casefold().rstrip("/")
            if folder_name != "3dmodels":
                continue
            roots[match.group(1)] = str(model_dir.parent)
        return roots

    @staticmethod
    def _detect_config_home(config_home: Path | None) -> Path:
        """Detect KiCad config directory."""
        if config_home is not None:
            return config_home

        env_home = os.environ.get("KICAD_CONFIG_HOME")
        if env_home:
            return Path(env_home).expanduser()

        candidates = [
            Path.home() / "Library/Preferences/kicad/9.0",
            Path.home() / "Library/Preferences/kicad/8.0",
            Path.home() / "Library/Preferences/kicad",
            Path.home() / ".config/kicad",
        ]
        for candidate in candidates:
            if (candidate / "sym-lib-table").exists() or (candidate / "fp-lib-table").exists():
                return candidate
        return candidates[0]

