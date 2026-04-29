"""KiCad environment and library table services.

This module provides service APIs for:
- discovering configured symbol/footprint libraries with nicknames
- mutating library tables (add/remove/rename)
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Literal, Sequence

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
        symbol_entries, symbol_tables = self._collect_entries(
            library_type="symbol",
            project_directory=project_directory,
            config_home=detected_config_home,
        )
        footprint_entries, footprint_tables = self._collect_entries(
            library_type="footprint",
            project_directory=project_directory,
            config_home=detected_config_home,
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
    ) -> Path:
        """Resolve a KiCad URI with variable expansion."""
        defaults = {
            "KICAD_SYMBOL_DIR": "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
            "KICAD_FOOTPRINT_DIR": "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
            "KIPRJMOD": str(project_directory) if project_directory is not None else "",
        }
        expanded = _URI_VARIABLE_PATTERN.sub(
            lambda match: os.environ.get(match.group(1), defaults.get(match.group(1), "")),
            uri,
        )
        expanded_path = Path(expanded).expanduser()
        if expanded_path.is_absolute():
            return expanded_path
        if project_directory is not None:
            return (project_directory / expanded_path).resolve()
        return expanded_path.resolve()

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

