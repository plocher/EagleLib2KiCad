"""KiCad library context loading and closure reporting service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from eaglelib2kicad.common.sexp import SexpParseError, parse_sexp
from eaglelib2kicad.services.kicad_environment_service import (
    KiCadEnvironmentSnapshot,
    KiCadLibraryEntry,
)


@dataclass(frozen=True)
class SymbolContext:
    """One normalized symbol context."""

    library_nickname: str
    symbol_name: str
    lib_id: str
    footprint_ref: str
    pin_count: int = 0


@dataclass(frozen=True)
class FootprintLibraryItem:
    """One footprint item in a configured footprint library."""

    library_nickname: str
    footprint_name: str
    source_path: Path
    pad_count: int = 0


@dataclass(frozen=True)
class FootprintClosureIssue:
    """One footprint-closure issue for a symbol context."""

    symbol_lib_id: str
    footprint_ref: str
    reason: str
    candidate_libraries: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class FootprintClosureReport:
    """Closure diagnostics for symbol footprint references."""

    resolved_count: int
    unresolved_count: int
    ambiguous_count: int
    unresolved: tuple[FootprintClosureIssue, ...]
    ambiguous: tuple[FootprintClosureIssue, ...]


@dataclass(frozen=True)
class KiCadLibraryContextSnapshot:
    """Loaded KiCad library contexts and closure diagnostics."""

    symbols: tuple[SymbolContext, ...]
    footprints: tuple[FootprintLibraryItem, ...]
    closure_report: FootprintClosureReport


class KiCadLibraryContextService:
    """Load configured KiCad libraries and evaluate symbol-footprint closure."""

    def load_contexts(
        self,
        *,
        environment: KiCadEnvironmentSnapshot,
    ) -> KiCadLibraryContextSnapshot:
        """Load symbol/footprint contexts from a KiCad environment snapshot."""
        symbols = self._load_symbol_contexts(environment.symbol_libraries)
        footprints = self._load_footprint_items(environment.footprint_libraries)
        closure_report = self._build_closure_report(symbols=symbols, footprints=footprints)
        return KiCadLibraryContextSnapshot(
            symbols=tuple(symbols),
            footprints=tuple(footprints),
            closure_report=closure_report,
        )

    def _load_symbol_contexts(
        self,
        symbol_libraries: Sequence[KiCadLibraryEntry],
    ) -> list[SymbolContext]:
        contexts: list[SymbolContext] = []
        for library in symbol_libraries:
            if not library.resolved_path.exists():
                continue
            text = library.resolved_path.read_text(encoding="utf-8")
            try:
                root = parse_sexp(text)
            except SexpParseError:
                continue
            if not root:
                continue

            for node in root[1:]:
                if not isinstance(node, list) or len(node) < 2:
                    continue
                if str(node[0]) != "symbol":
                    continue
                symbol_name = self._symbol_node_name(node)
                if not symbol_name:
                    continue
                properties = self._symbol_properties(node)
                if "Reference" not in properties:
                    # Nested unit/graphic symbol nodes do not define a root symbol.
                    continue
                lib_id = f"{library.nickname}:{symbol_name}"
                contexts.append(
                    SymbolContext(
                        library_nickname=library.nickname,
                        symbol_name=symbol_name,
                        lib_id=lib_id,
                        footprint_ref=properties.get("Footprint", "").strip(),
                        pin_count=self._count_symbol_pin_nodes(node),
                    )
                )
        return contexts

    def _load_footprint_items(
        self,
        footprint_libraries: Sequence[KiCadLibraryEntry],
    ) -> list[FootprintLibraryItem]:
        items: list[FootprintLibraryItem] = []
        for library in footprint_libraries:
            library_path = library.resolved_path
            if not library_path.exists() or not library_path.is_dir():
                continue
            for footprint_file in sorted(library_path.glob("*.kicad_mod")):
                if not footprint_file.is_file():
                    continue
                items.append(
                    FootprintLibraryItem(
                        library_nickname=library.nickname,
                        footprint_name=footprint_file.stem,
                        source_path=footprint_file,
                        pad_count=self._count_footprint_pads(footprint_file),
                    )
                )
        return items

    def _build_closure_report(
        self,
        *,
        symbols: Sequence[SymbolContext],
        footprints: Sequence[FootprintLibraryItem],
    ) -> FootprintClosureReport:
        footprint_by_library: dict[str, set[str]] = {}
        libraries_by_footprint_name: dict[str, set[str]] = {}

        for footprint in footprints:
            footprint_by_library.setdefault(footprint.library_nickname, set()).add(
                footprint.footprint_name
            )
            libraries_by_footprint_name.setdefault(footprint.footprint_name, set()).add(
                footprint.library_nickname
            )

        unresolved: list[FootprintClosureIssue] = []
        ambiguous: list[FootprintClosureIssue] = []
        resolved_count = 0

        for symbol in symbols:
            footprint_ref = symbol.footprint_ref
            if not footprint_ref:
                continue

            if ":" in footprint_ref:
                nickname, footprint_name = footprint_ref.split(":", 1)
                available = footprint_by_library.get(nickname, set())
                if footprint_name in available:
                    resolved_count += 1
                    continue
                unresolved.append(
                    FootprintClosureIssue(
                        symbol_lib_id=symbol.lib_id,
                        footprint_ref=footprint_ref,
                        reason="footprint_missing_in_named_library",
                    )
                )
                continue

            candidate_libraries = sorted(libraries_by_footprint_name.get(footprint_ref, set()))
            if len(candidate_libraries) == 1:
                resolved_count += 1
                continue
            if len(candidate_libraries) > 1:
                ambiguous.append(
                    FootprintClosureIssue(
                        symbol_lib_id=symbol.lib_id,
                        footprint_ref=footprint_ref,
                        reason="bare_footprint_reference_is_ambiguous",
                        candidate_libraries=tuple(candidate_libraries),
                    )
                )
                continue
            unresolved.append(
                FootprintClosureIssue(
                    symbol_lib_id=symbol.lib_id,
                    footprint_ref=footprint_ref,
                    reason="bare_footprint_reference_not_found",
                )
            )

        return FootprintClosureReport(
            resolved_count=resolved_count,
            unresolved_count=len(unresolved),
            ambiguous_count=len(ambiguous),
            unresolved=tuple(unresolved),
            ambiguous=tuple(ambiguous),
        )

    @staticmethod
    def _symbol_node_name(node: list[object]) -> str:
        if len(node) < 2:
            return ""
        name_token = node[1]
        if not isinstance(name_token, str):
            return ""
        if ":" in name_token:
            return name_token.split(":", 1)[1]
        return name_token

    @staticmethod
    def _symbol_properties(node: list[object]) -> dict[str, str]:
        properties: dict[str, str] = {}
        for child in node[2:]:
            if not isinstance(child, list) or len(child) < 3:
                continue
            if str(child[0]) != "property":
                continue
            key = child[1]
            value = child[2]
            if isinstance(key, str):
                properties[key] = str(value)
        return properties

    def _count_symbol_pin_nodes(self, node: list[object]) -> int:
        """Count `(pin ...)` nodes in a symbol tree recursively."""
        count = 0
        for child in node[2:]:
            if not isinstance(child, list) or not child:
                continue
            if str(child[0]) == "pin":
                count += 1
                continue
            count += self._count_symbol_pin_nodes(child)
        return count

    def _count_footprint_pads(self, footprint_file: Path) -> int:
        """Count `(pad ...)` nodes in a KiCad footprint file."""
        try:
            text = footprint_file.read_text(encoding="utf-8")
        except OSError:
            return 0
        try:
            root = parse_sexp(text)
        except SexpParseError:
            return 0
        if not root:
            return 0

        count = 0
        for item in root[1:]:
            if not isinstance(item, list) or not item:
                continue
            if str(item[0]) == "pad":
                count += 1
        return count

