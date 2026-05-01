"""Convert Eagle .lbr package footprints to KiCad .kicad_mod footprints.

This module intentionally keeps conversion logic explicit and readable so it can be
used as a learning tool in addition to being an importer.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if SRC_DIR.exists() and str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from eaglelib2kicad.services.eagle_library_context_service import (
    EagleLibraryContextService,
)
from eaglelib2kicad.services.kicad_environment_service import KiCadEnvironmentService
from eaglelib2kicad.services.kicad_library_context_service import (
    KiCadLibraryContextService,
)
from eaglelib2kicad.services.library_migration_analysis_service import (
    LibraryMigrationAnalysisService,
    MigrationAnalysisRow,
)


DEFAULT_EAGLE_LAYER_MAP: dict[int, str] = {
    1: "F.Cu",
    16: "B.Cu",
    20: "Edge.Cuts",
    21: "F.SilkS",
    22: "B.SilkS",
    25: "F.SilkS",
    26: "B.SilkS",
    27: "F.Fab",
    28: "B.Fab",
    29: "F.Mask",
    30: "B.Mask",
    31: "F.Paste",
    32: "B.Paste",
    35: "F.Adhes",
    36: "B.Adhes",
    39: "F.CrtYd",
    40: "B.CrtYd",
    41: "F.CrtYd",
    42: "B.CrtYd",
    43: "Cmts.User",
    46: "Edge.Cuts",
    47: "Dwgs.User",
    48: "Dwgs.User",
    49: "Cmts.User",
    51: "F.Fab",
    52: "B.Fab",
    102: "Cmts.User",
    103: "Cmts.User",
    104: "F.SilkS",
}

LayerMapInput = Mapping[int, str | Sequence[str]]
LayerMapNormalized = dict[int, tuple[str, ...]]

_ILLEGAL_FILENAME_CHARS = re.compile(r"[<>:\"/\\|?*\s]+")
_ROTATION_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
_KICAD_FOOTPRINT_HEADER = re.compile(r'^\s*\(footprint\s+"([^"]+)"', re.MULTILINE)
_KICAD_VALUE_PROPERTY = re.compile(r'(\(property\s+"Value"\s+")([^"]*)(")')


@dataclass(frozen=True)
class Point:
    """A 2D point in millimeters in KiCad footprint coordinates."""

    x: float
    y: float


@dataclass(frozen=True)
class Rotation:
    """Parsed Eagle rotation descriptor."""

    degrees: float
    mirror: bool
    spin: bool


@dataclass(frozen=True)
class TextStyle:
    """Text placement and style used for generated KiCad text primitives."""

    x: float
    y: float
    angle_degrees: float
    layer: str
    size: float
    thickness: float
    mirror: bool = False
    h_justify: str | None = None
    v_justify: str | None = None

@dataclass(frozen=True)
class DimensionUnitMarker:
    """Unit marker text discovered in an Eagle package (e.g., mm or ")."""

    text: str
    layer_number: int | None
    x: float
    y: float
    size: float
    angle_degrees: float


@dataclass(frozen=True)
class DesignRules:
    """Subset of Eagle design rules used by KiCad when importing pads."""

    ps_elongation_long: int = 100
    rv_pad_top: float = 0.25
    rl_min_pad_top: float = 0.254
    rl_max_pad_top: float = 0.508
    sr_roundness: float = 0.0
    sr_min_roundness: float = 0.0
    sr_max_roundness: float = 0.0

    @staticmethod
    def from_root(root: ET.Element) -> "DesignRules":
        """Build design rules from the Eagle document, with KiCad-like defaults."""

        params: dict[str, str] = {}
        for param in root.findall("./drawing/designrules/param"):
            name = param.attrib.get("name")
            value = param.attrib.get("value")
            if name and value is not None:
                params[name] = value

        return DesignRules(
            ps_elongation_long=int(params.get("psElongationLong", "100")),
            rv_pad_top=float(params.get("rvPadTop", "0.25")),
            rl_min_pad_top=parse_eagle_distance(params.get("rlMinPadTop", "10mil")),
            rl_max_pad_top=parse_eagle_distance(params.get("rlMaxPadTop", "20mil")),
            sr_roundness=float(params.get("srRoundness", "0.0")),
            sr_min_roundness=parse_eagle_distance(params.get("srMinRoundness", "0mil")),
            sr_max_roundness=parse_eagle_distance(params.get("srMaxRoundness", "0mil")),
        )


@dataclass(frozen=True)
class PackageInfo:
    """Summary metadata for a package available in an Eagle library."""

    eagle_name: str
    kicad_name: str
    description: str
    primitive_counts: dict[str, int]


@dataclass(frozen=True)
class ConversionResult:
    """Outcome of converting and writing one footprint."""

    eagle_name: str
    kicad_name: str
    output_path: Path
    created: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class KiCadFootprintItem:
    """One footprint item discovered in a KiCad .pretty library."""

    name: str
    file_path: Path

@dataclass(frozen=True)
class MigrationAnalysisArtifact:
    """Structured analysis artifact emitted for review queue workflows."""

    generated_at_utc: str
    eagle_library: str
    kicad_config_home: str
    kicad_project_directory: str | None
    total_devices: int
    queue_counts: dict[str, int]
    confidence_counts: dict[str, int]
    pathway_counts: dict[str, int]
    rows: tuple[MigrationAnalysisRow, ...]

    def to_json_dict(self) -> dict[str, object]:
        """Serialize artifact to a JSON-compatible dictionary."""
        return {
            "generated_at_utc": self.generated_at_utc,
            "eagle_library": self.eagle_library,
            "kicad_config_home": self.kicad_config_home,
            "kicad_project_directory": self.kicad_project_directory,
            "total_devices": self.total_devices,
            "queue_counts": self.queue_counts,
            "confidence_counts": self.confidence_counts,
            "pathway_counts": self.pathway_counts,
            "rows": [
                {
                    "device_key": row.device_key,
                    "pathway": row.pathway,
                    "symbol_name": row.symbol_name,
                    "package_name": row.package_name,
                    "confidence": row.confidence,
                    "review_queue": row.review_queue,
                    "review_required": row.review_required,
                    "reasons": list(row.reasons),
                    "symbol_library": row.symbol_library,
                    "footprint_library": row.footprint_library,
                    "eagle_pin_count": row.eagle_pin_count,
                    "symbol_pin_count": row.symbol_pin_count,
                    "footprint_pad_count": row.footprint_pad_count,
                }
                for row in self.rows
            ],
        }


def build_migration_analysis_artifact(
    *,
    eagle_library: Path,
    kicad_config_home: Path,
    kicad_project_directory: Path | None,
    rows: Sequence[MigrationAnalysisRow],
) -> MigrationAnalysisArtifact:
    """Build structured analysis artifact from migration analysis rows."""
    queue_counter = Counter(row.review_queue for row in rows)
    confidence_counter = Counter(row.confidence for row in rows)
    pathway_counter = Counter(row.pathway for row in rows)
    queue_counts = {
        "none": queue_counter.get("none", 0),
        "standard": queue_counter.get("standard", 0),
        "priority": queue_counter.get("priority", 0),
    }
    return MigrationAnalysisArtifact(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        eagle_library=str(eagle_library.resolve()),
        kicad_config_home=str(kicad_config_home.resolve()),
        kicad_project_directory=(
            str(kicad_project_directory.resolve())
            if kicad_project_directory is not None
            else None
        ),
        total_devices=len(rows),
        queue_counts=queue_counts,
        confidence_counts={
            "high": confidence_counter.get("high", 0),
            "medium": confidence_counter.get("medium", 0),
            "low": confidence_counter.get("low", 0),
        },
        pathway_counts={
            "commodity_passive": pathway_counter.get("commodity_passive", 0),
            "ic_regulator_specialty": pathway_counter.get("ic_regulator_specialty", 0),
            "connector_switch_mechanical": pathway_counter.get(
                "connector_switch_mechanical", 0
            ),
            "schematic_annotation": pathway_counter.get("schematic_annotation", 0),
            "uncategorized": pathway_counter.get("uncategorized", 0),
        },
        rows=tuple(rows),
    )


def run_migration_analysis(
    *,
    eagle_library: Path,
    kicad_project_directory: Path | None = None,
    kicad_config_home: Path | None = None,
) -> MigrationAnalysisArtifact:
    """Run importer-side migration analysis and return report artifact."""
    eagle_context_service = EagleLibraryContextService()
    environment_service = KiCadEnvironmentService()
    kicad_context_service = KiCadLibraryContextService()
    migration_service = LibraryMigrationAnalysisService()

    eagle_devices = eagle_context_service.load_device_contexts(eagle_library)
    environment_snapshot = environment_service.discover_configured_libraries(
        project_directory=kicad_project_directory,
        config_home=kicad_config_home,
    )
    kicad_context = kicad_context_service.load_contexts(environment=environment_snapshot)
    rows = migration_service.analyze(
        eagle_devices=eagle_devices,
        kicad_context=kicad_context,
    )
    return build_migration_analysis_artifact(
        eagle_library=eagle_library,
        kicad_config_home=environment_snapshot.config_home,
        kicad_project_directory=kicad_project_directory,
        rows=rows,
    )


@dataclass
class EagleLibrary:
    """An Eagle XML library loaded from a .lbr file."""

    path: Path
    root: ET.Element
    design_rules: DesignRules
    packages: dict[str, ET.Element] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "EagleLibrary":
        """Load and validate an Eagle library file."""

        validate_eagle_library_path(path)
        tree = ET.parse(path)
        root = tree.getroot()
        package_nodes = root.findall("./drawing/library/packages/package")
        packages = {
            package.attrib["name"]: package
            for package in package_nodes
            if "name" in package.attrib
        }
        return cls(
            path=path,
            root=root,
            design_rules=DesignRules.from_root(root),
            packages=packages,
        )

    def list_packages(self) -> list[PackageInfo]:
        """Return package metadata sorted by Eagle package name."""

        package_list: list[PackageInfo] = []
        for eagle_name in sorted(self.packages):
            package = self.packages[eagle_name]
            package_list.append(
                PackageInfo(
                    eagle_name=eagle_name,
                    kicad_name=sanitize_footprint_name(eagle_name),
                    description=extract_package_description(package),
                    primitive_counts=count_package_primitives(package),
                )
            )
        return package_list


class EagleToKiCadConverter:
    """Convert Eagle package elements to KiCad footprint S-expressions."""

    def __init__(
        self,
        design_rules: DesignRules,
        include_dimensions: bool = True,
        layer_map: LayerMapInput | None = None,
    ) -> None:
        self._design_rules = design_rules
        self._include_dimensions = include_dimensions
        self._layer_map = normalize_layer_map(layer_map)
        self._warnings: set[str] = set()
        self._dimension_unit_markers: list[DimensionUnitMarker] = []
        self._consumed_dimension_unit_marker_indices: set[int] = set()
        self._dimension_marker_by_element_id: dict[int, DimensionUnitMarker] = {}

    def convert_package(self, package: ET.Element, package_name: str) -> tuple[str, tuple[str, ...]]:
        """Convert one Eagle package XML node to KiCad .kicad_mod text."""

        self._warnings = set()
        self._dimension_unit_markers = self._collect_dimension_unit_markers(package)
        self._consumed_dimension_unit_marker_indices = set()
        self._dimension_marker_by_element_id = {}
        if self._include_dimensions:
            self._reserve_dimension_unit_markers(package)
        kicad_name = sanitize_footprint_name(package_name)
        has_smd = any(child.tag == "smd" for child in package)
        has_pth_pad = any(child.tag == "pad" for child in package)

        lines: list[str] = []
        append = lines.append

        append(f'(footprint "{escape_kicad_string(kicad_name)}"')
        append("  (version 20240108)")
        append('  (generator "eagle_to_kicad_learning_converter")')
        append('  (layer "F.Cu")')

        description = extract_package_description(package)
        if description:
            append(f'  (descr "{escape_kicad_string(description)}")')

        reference_style, value_style = self._infer_reference_value_styles(package, kicad_name)
        self._emit_property_block(lines, "Reference", "REF**", reference_style, hide=False)
        self._emit_property_block(lines, "Value", kicad_name, value_style, hide=False)
        self._emit_property_block(lines, "Footprint", "", TextStyle(0.0, 0.0, 0.0, "F.Fab", 1.0, 0.15), hide=True)
        self._emit_property_block(lines, "Datasheet", "", TextStyle(0.0, 0.0, 0.0, "F.Fab", 1.0, 0.15), hide=True)
        self._emit_property_block(lines, "Description", "", TextStyle(0.0, 0.0, 0.0, "F.Fab", 1.0, 0.15), hide=True)

        if has_smd and not has_pth_pad:
            append("  (attr smd)")
        elif has_pth_pad:
            append("  (attr through_hole)")

        for child in package:
            self._convert_child(lines, child, kicad_name)
        self._dimension_unit_markers = []
        self._consumed_dimension_unit_marker_indices = set()
        self._dimension_marker_by_element_id = {}

        append(")")
        return "\n".join(lines) + "\n", tuple(sorted(self._warnings))

    def _convert_child(self, lines: list[str], child: ET.Element, kicad_name: str) -> None:
        """Dispatch one Eagle primitive conversion."""

        tag = child.tag
        if tag in {"description"}:
            return
        if tag == "wire":
            self._emit_wire(lines, child)
            return
        if tag == "circle":
            self._emit_circle(lines, child)
            return
        if tag == "rectangle":
            self._emit_rectangle(lines, child)
            return
        if tag == "polygon":
            self._emit_polygon(lines, child)
            return
        if tag == "pad":
            self._emit_pad(lines, child)
            return
        if tag == "smd":
            self._emit_smd(lines, child)
            return
        if tag == "hole":
            self._emit_hole(lines, child)
            return
        if tag == "text":
            if not self._include_dimensions and child.attrib.get("layer") in {"46", "47"}:
                self._warnings.add("Skipped Eagle text on measurement/dimension layer (disabled by option).")
            else:
                self._emit_text(lines, child, kicad_name)
            return
        if tag == "dimension":
            if self._include_dimensions:
                self._emit_dimension_as_line(lines, child)
            else:
                self._warnings.add("Skipped Eagle <dimension> primitive (disabled by option).")
            return
        self._warnings.add(f"Skipped unsupported primitive <{tag}>.")

    def _infer_reference_value_styles(self, package: ET.Element, kicad_name: str) -> tuple[TextStyle, TextStyle]:
        """Locate >NAME and >VALUE in Eagle package text and map to KiCad property styles."""

        ref_style = TextStyle(0.0, -1.5, 0.0, "F.SilkS", 1.0, 0.15)
        val_style = TextStyle(0.0, 1.5, 0.0, "F.Fab", 1.0, 0.15)

        for text in package.findall("text"):
            raw_text = (text.text or "").strip().upper()
            if raw_text not in {">NAME", ">VALUE"}:
                continue

            style = self._text_style_from_element(text)
            if raw_text == ">NAME":
                ref_style = style
            elif raw_text == ">VALUE":
                val_style = style

        if ref_style.size <= 0:
            ref_style = TextStyle(ref_style.x, ref_style.y, ref_style.angle_degrees, ref_style.layer, 1.0, 0.15, ref_style.mirror, ref_style.h_justify, ref_style.v_justify)
        if val_style.size <= 0:
            val_style = TextStyle(val_style.x, val_style.y, val_style.angle_degrees, val_style.layer, 1.0, 0.15, val_style.mirror, val_style.h_justify, val_style.v_justify)
        return ref_style, val_style

    def _emit_property_block(self, lines: list[str], name: str, value: str, style: TextStyle, hide: bool) -> None:
        """Emit one KiCad property field."""

        lines.append(f'  (property "{escape_kicad_string(name)}" "{escape_kicad_string(value)}"')
        lines.append(
            f'    (at {fmt_mm(style.x)} {fmt_mm(style.y)} {fmt_mm(style.angle_degrees)})'
        )
        lines.append(f'    (layer "{style.layer}")')
        if hide:
            lines.append("    (hide yes)")
        effects = f'    (effects (font (size {fmt_mm(style.size)} {fmt_mm(style.size)}) (thickness {fmt_mm(max(style.thickness, 0.01))})))'
        if style.h_justify or style.v_justify or style.mirror:
            justify_tokens = [token for token in (style.h_justify, style.v_justify) if token]
            if style.mirror:
                justify_tokens.append("mirror")
            justify = " ".join(justify_tokens)
            effects = (
                f'    (effects (font (size {fmt_mm(style.size)} {fmt_mm(style.size)}) '
                f'(thickness {fmt_mm(max(style.thickness, 0.01))})) (justify {justify}))'
            )
        lines.append(effects)
        lines.append("  )")

    def _emit_wire(self, lines: list[str], wire: ET.Element) -> None:
        """Convert Eagle wire to KiCad fp_line/fp_arc."""

        layer_names = self._map_layers(wire.attrib.get("layer"))
        if not layer_names:
            return

        start = Point(parse_coord(wire.attrib["x1"]), -parse_coord(wire.attrib["y1"]))
        end = Point(parse_coord(wire.attrib["x2"]), -parse_coord(wire.attrib["y2"]))
        width = parse_coord(wire.attrib["width"])
        curve = wire.attrib.get("curve")

        if not curve or float(curve) == 0:
            for layer_name in layer_names:
                stroke_width = width if width > 0 else default_line_width_for_layer(layer_name)
                lines.extend(
                    [
                        "  (fp_line",
                        f"    (start {fmt_mm(start.x)} {fmt_mm(start.y)})",
                        f"    (end {fmt_mm(end.x)} {fmt_mm(end.y)})",
                        f"    (stroke (width {fmt_mm(stroke_width)}) (type solid))",
                        f'    (layer "{layer_name}")',
                        "  )",
                    ]
                )
            return

        curve_degrees = float(curve)
        midpoint = calculate_arc_midpoint(start, end, curve_degrees)
        for layer_name in layer_names:
            stroke_width = width if width > 0 else default_line_width_for_layer(layer_name)
            lines.extend(
                [
                    "  (fp_arc",
                    f"    (start {fmt_mm(start.x)} {fmt_mm(start.y)})",
                    f"    (mid {fmt_mm(midpoint.x)} {fmt_mm(midpoint.y)})",
                    f"    (end {fmt_mm(end.x)} {fmt_mm(end.y)})",
                    f"    (stroke (width {fmt_mm(stroke_width)}) (type solid))",
                    f'    (layer "{layer_name}")',
                    "  )",
                ]
            )

    def _emit_two_vertex_polygon(
        self,
        lines: list[str],
        polygon: ET.Element,
        vertices: Sequence[ET.Element],
        layer_names: Sequence[str],
    ) -> None:
        """Approximate a 2-vertex Eagle polygon as a stroked line or arc."""

        width = parse_coord(polygon.attrib.get("width", "0.1016"))
        width = max(width, 0.01)
        start = Point(parse_coord(vertices[0].attrib["x"]), -parse_coord(vertices[0].attrib["y"]))
        end = Point(parse_coord(vertices[1].attrib["x"]), -parse_coord(vertices[1].attrib["y"]))
        curve_text = vertices[0].attrib.get("curve")

        if curve_text and float(curve_text) != 0:
            curve_degrees = float(curve_text)
            midpoint = calculate_arc_midpoint(start, end, curve_degrees)
            for layer_name in layer_names:
                lines.extend(
                    [
                        "  (fp_arc",
                        f"    (start {fmt_mm(start.x)} {fmt_mm(start.y)})",
                        f"    (mid {fmt_mm(midpoint.x)} {fmt_mm(midpoint.y)})",
                        f"    (end {fmt_mm(end.x)} {fmt_mm(end.y)})",
                        f"    (stroke (width {fmt_mm(width)}) (type solid))",
                        f'    (layer "{layer_name}")',
                        "  )",
                    ]
                )
            self._warnings.add("Converted 2-vertex polygon with curve to fp_arc approximation.")
            return

        for layer_name in layer_names:
            lines.extend(
                [
                    "  (fp_line",
                    f"    (start {fmt_mm(start.x)} {fmt_mm(start.y)})",
                    f"    (end {fmt_mm(end.x)} {fmt_mm(end.y)})",
                    f"    (stroke (width {fmt_mm(width)}) (type solid))",
                    f'    (layer "{layer_name}")',
                    "  )",
                ]
            )
        self._warnings.add("Converted 2-vertex polygon to fp_line approximation.")

    def _emit_circle(self, lines: list[str], circle: ET.Element) -> None:
        """Convert Eagle circle to KiCad fp_circle."""

        layer_names = self._map_layers(circle.attrib.get("layer"))
        if not layer_names:
            return

        center = Point(parse_coord(circle.attrib["x"]), -parse_coord(circle.attrib["y"]))
        radius = parse_coord(circle.attrib["radius"])
        width = parse_coord(circle.attrib["width"])
        fill = "none"
        stroke_width = width
        if width <= 0:
            fill = "solid"
            stroke_width = 0.01

        for layer_name in layer_names:
            lines.extend(
                [
                    "  (fp_circle",
                    f"    (center {fmt_mm(center.x)} {fmt_mm(center.y)})",
                    f"    (end {fmt_mm(center.x + radius)} {fmt_mm(center.y)})",
                    f"    (stroke (width {fmt_mm(max(stroke_width, 0.01))}) (type solid))",
                    f"    (fill {fill})",
                    f'    (layer "{layer_name}")',
                    "  )",
                ]
            )

    def _emit_rectangle(self, lines: list[str], rectangle: ET.Element) -> None:
        """Convert Eagle rectangle to KiCad filled polygon."""

        layer_names = self._map_layers(rectangle.attrib.get("layer"))
        if not layer_names:
            return

        x1 = parse_coord(rectangle.attrib["x1"])
        y1 = -parse_coord(rectangle.attrib["y1"])
        x2 = parse_coord(rectangle.attrib["x2"])
        y2 = -parse_coord(rectangle.attrib["y2"])
        corners = [
            Point(x1, y1),
            Point(x2, y1),
            Point(x2, y2),
            Point(x1, y2),
        ]

        rotation = parse_rotation(rectangle.attrib.get("rot"))
        if rotation.degrees != 0:
            center = Point((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            corners = [rotate_point(point, center, rotation.degrees) for point in corners]

        points = " ".join(f"(xy {fmt_mm(point.x)} {fmt_mm(point.y)})" for point in corners)
        for layer_name in layer_names:
            lines.extend(
                [
                    "  (fp_poly",
                    f"    (pts {points})",
                    "    (stroke (width 0.01) (type solid))",
                    "    (fill solid)",
                    f'    (layer "{layer_name}")',
                    "  )",
                ]
            )

    def _emit_polygon(self, lines: list[str], polygon: ET.Element) -> None:
        """Convert Eagle polygon to KiCad fp_poly with optional arc interpolation."""

        layer_names = self._map_layers(polygon.attrib.get("layer"))
        if not layer_names:
            return

        vertices = [vertex for vertex in polygon.findall("vertex")]
        if len(vertices) == 2:
            self._emit_two_vertex_polygon(lines, polygon, vertices, layer_names)
            return
        if len(vertices) < 2:
            self._warnings.add("Skipped polygon with fewer than two vertices.")
            return

        points = polygon_vertices_to_points(vertices)
        width = parse_coord(polygon.attrib.get("width", "0.01"))
        points_text = " ".join(f"(xy {fmt_mm(point.x)} {fmt_mm(point.y)})" for point in points)
        for layer_name in layer_names:
            lines.extend(
                [
                    "  (fp_poly",
                    f"    (pts {points_text})",
                    f"    (stroke (width {fmt_mm(max(width, 0.01))}) (type solid))",
                    "    (fill solid)",
                    f'    (layer "{layer_name}")',
                    "  )",
                ]
            )

    def _emit_pad(self, lines: list[str], pad: ET.Element) -> None:
        """Convert Eagle through-hole pad to KiCad pad."""

        name = pad.attrib.get("name", "")
        x = parse_coord(pad.attrib.get("x", "0"))
        y = -parse_coord(pad.attrib.get("y", "0"))
        drill = parse_coord(pad.attrib.get("drill", "0"))
        if drill <= 0:
            self._warnings.add(f"Skipped pad '{name}' with non-positive drill.")
            return

        diameter_raw = pad.attrib.get("diameter")
        if diameter_raw is not None:
            diameter = parse_coord(diameter_raw)
        else:
            annulus = clamp(
                drill * self._design_rules.rv_pad_top,
                self._design_rules.rl_min_pad_top,
                self._design_rules.rl_max_pad_top,
            )
            diameter = drill + 2.0 * annulus

        shape_name = pad.attrib.get("shape", "round").lower()
        kicad_shape = "circle"
        is_octagon = False
        if shape_name == "square":
            kicad_shape = "rect"
        elif shape_name in {"long", "offset"}:
            kicad_shape = "oval"
            elongation_factor = 1.0 + (self._design_rules.ps_elongation_long / 100.0)
            diameter_x = diameter * elongation_factor
        elif shape_name == "octagon":
            kicad_shape = "roundrect"
            is_octagon = True
        else:
            diameter_x = diameter

        if shape_name not in {"long", "offset"}:
            diameter_x = diameter
        diameter_y = diameter

        rotation = parse_rotation(pad.attrib.get("rot"))
        layer_tokens: list[str] = ['"*.Cu"']
        if pad.attrib.get("stop", "yes") != "no":
            layer_tokens.append('"*.Mask"')

        layers = " ".join(layer_tokens)
        lines.append(
            "  "
            + f'(pad "{escape_kicad_string(name)}" thru_hole {kicad_shape} '
            + f'(at {fmt_mm(x)} {fmt_mm(y)} {fmt_mm(rotation.degrees)}) '
            + f'(size {fmt_mm(diameter_x)} {fmt_mm(diameter_y)}) '
            + f'(drill {fmt_mm(drill)}) '
            + f"(layers {layers}))"
        )
        if is_octagon:
            lines[-1] = lines[-1][:-1] + " (chamfer_ratio 0.292893) (chamfer top_left top_right bottom_left bottom_right))"

    def _emit_smd(self, lines: list[str], smd: ET.Element) -> None:
        """Convert Eagle SMD pad to KiCad SMD pad."""

        name = smd.attrib.get("name", "")
        x = parse_coord(smd.attrib.get("x", "0"))
        y = -parse_coord(smd.attrib.get("y", "0"))
        dx = parse_coord(smd.attrib.get("dx", "0"))
        dy = parse_coord(smd.attrib.get("dy", "0"))
        if dx <= 0 or dy <= 0:
            self._warnings.add(f"Skipped SMD '{name}' with non-positive dimensions.")
            return

        layer = int(smd.attrib.get("layer", "1"))
        if layer not in {1, 16}:
            self._warnings.add(f"Skipped SMD '{name}' on unsupported copper layer {layer}.")
            return

        copper = "F.Cu" if layer == 1 else "B.Cu"
        mask = "F.Mask" if layer == 1 else "B.Mask"
        paste = "F.Paste" if layer == 1 else "B.Paste"
        layer_tokens = [f'"{copper}"']
        if smd.attrib.get("cream", "yes") != "no":
            layer_tokens.append(f'"{paste}"')
        if smd.attrib.get("stop", "yes") != "no":
            layer_tokens.append(f'"{mask}"')

        min_size = min(dx, dy)
        rule_round_radius = clamp(
            min_size * self._design_rules.sr_roundness,
            self._design_rules.sr_min_roundness * 2.0,
            self._design_rules.sr_max_roundness * 2.0,
        )
        explicit_roundness = float(smd.attrib.get("roundness", "0"))
        round_ratio = 0.0
        if explicit_roundness > 0:
            round_ratio = max(round_ratio, explicit_roundness / 200.0)
        if rule_round_radius > 0:
            round_ratio = max(round_ratio, rule_round_radius / min_size / 2.0)

        shape = "roundrect" if round_ratio > 0 else "rect"
        rotation = parse_rotation(smd.attrib.get("rot"))
        layers = " ".join(layer_tokens)
        pad_line = (
            "  "
            + f'(pad "{escape_kicad_string(name)}" smd {shape} '
            + f'(at {fmt_mm(x)} {fmt_mm(y)} {fmt_mm(rotation.degrees)}) '
            + f'(size {fmt_mm(dx)} {fmt_mm(dy)}) '
            + f"(layers {layers})"
        )
        if round_ratio > 0:
            pad_line += f" (roundrect_rratio {fmt_mm(clamp(round_ratio, 0.0, 0.5))})"
        pad_line += ")"
        lines.append(pad_line)

    def _emit_hole(self, lines: list[str], hole: ET.Element) -> None:
        """Convert Eagle hole primitive to KiCad NPTH pad."""

        x = parse_coord(hole.attrib.get("x", "0"))
        y = -parse_coord(hole.attrib.get("y", "0"))
        drill = parse_coord(hole.attrib.get("drill", "0"))
        if drill <= 0:
            self._warnings.add("Skipped non-positive hole drill.")
            return

        lines.append(
            "  "
            + f'(pad "" np_thru_hole circle (at {fmt_mm(x)} {fmt_mm(y)} 0) '
            + f'(size {fmt_mm(drill)} {fmt_mm(drill)}) '
            + f'(drill {fmt_mm(drill)}) '
            + '(layers "*.Cu" "*.Mask"))'
        )

    def _emit_text(self, lines: list[str], text: ET.Element, kicad_name: str) -> None:
        """Convert Eagle package text to KiCad user text, except NAME/VALUE placeholders."""

        raw_text = (text.text or "").strip()
        if not raw_text:
            return
        if raw_text.upper() in {">NAME", ">VALUE"}:
            return
        if self._is_consumed_dimension_marker_text(text):
            return
        interpreted = raw_text.replace(">NAME", "REF**").replace(">VALUE", kicad_name)
        for layer_name in self._map_layers(text.attrib.get("layer"), fallback_layer="Cmts.User"):
            style = self._text_style_from_element(text, layer_override=layer_name)
            lines.append(f'  (fp_text user "{escape_kicad_string(html.unescape(interpreted))}"')
            lines.append(
                f"    (at {fmt_mm(style.x)} {fmt_mm(style.y)} {fmt_mm(style.angle_degrees)})"
            )
            lines.append(f'    (layer "{style.layer}")')

            effects = (
                f"    (effects (font (size {fmt_mm(style.size)} {fmt_mm(style.size)}) "
                f"(thickness {fmt_mm(max(style.thickness, 0.01))}))"
            )
            justify_tokens = [token for token in (style.h_justify, style.v_justify) if token]
            if style.mirror:
                justify_tokens.append("mirror")
            if justify_tokens:
                effects += f" (justify {' '.join(justify_tokens)})"
            effects += ")"
            lines.append(effects)
            lines.append("  )")

    def _emit_dimension_as_line(self, lines: list[str], dimension: ET.Element) -> None:
        """Convert Eagle dimensions to native KiCad dimension primitives."""
        layer_names = self._map_layers(dimension.attrib.get("layer"), fallback_layer="Dwgs.User")
        if not layer_names:
            return

        x1 = parse_coord(dimension.attrib.get("x1", "0"))
        y1 = -parse_coord(dimension.attrib.get("y1", "0"))
        x2 = parse_coord(dimension.attrib.get("x2", "0"))
        y2 = -parse_coord(dimension.attrib.get("y2", "0"))
        x3 = parse_coord(dimension.attrib.get("x3", "0"))
        y3 = -parse_coord(dimension.attrib.get("y3", "0"))
        dtype = dimension.attrib.get("dtype", "").strip().lower()
        width = max(parse_coord(dimension.attrib.get("width", "0.1")), 0.01)
        text_size = max(parse_coord(dimension.attrib.get("textsize", "1.27")), 0.1)
        text_thickness = max(text_size * 0.1, 0.01)
        angle_degrees = self._dimension_text_angle(dimension, x1, y1, x2, y2)
        marker = self._dimension_marker_by_element_id.get(id(dimension))
        display_unit = self._dimension_display_unit(dimension, marker)
        unit_mode = self._dimension_units_mode(display_unit)
        precision = self._dimension_precision_enum(dimension, display_unit)
        suffix = self._dimension_suffix(marker, display_unit)
        dimension_type, orientation = self._dimension_kind(dtype)
        if dimension_type == "orthogonal":
            height = self._orthogonal_dimension_height(
                x1=x1,
                y1=y1,
                x3=x3,
                y3=y3,
                orientation=orientation or 0,
            )
        else:
            height = self._aligned_dimension_height(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                x3=x3,
                y3=y3,
            )
        value_text = self._format_dimension_value_text(
            dimension,
            x1,
            y1,
            x2,
            y2,
            unit_override=display_unit,
            precision_enum=precision,
            suffix=suffix,
        )
        for layer_name in layer_names:
            lines.append("  (dimension")
            lines.append(f"    (type {dimension_type})")
            lines.append(f'    (layer "{layer_name}")')
            lines.append("    (pts")
            lines.append(f"      (xy {fmt_mm(x1)} {fmt_mm(y1)}) (xy {fmt_mm(x2)} {fmt_mm(y2)})")
            lines.append("    )")
            lines.append(f"    (height {fmt_mm(height)})")
            if orientation is not None:
                lines.append(f"    (orientation {orientation})")
            lines.append("    (format")
            lines.append('      (prefix "")')
            lines.append(f'      (suffix "{escape_kicad_string(suffix)}")')
            lines.append(f"      (units {unit_mode})")
            lines.append("      (units_format 0)")
            lines.append(f"      (precision {precision})")
            lines.append("    )")
            lines.append("    (style")
            lines.append(f"      (thickness {fmt_mm(width)})")
            lines.append("      (arrow_length 1.27)")
            lines.append("      (text_position_mode 2)")
            lines.append("      (arrow_direction outward)")
            lines.append("      (extension_height 0.58642)")
            lines.append("      (extension_offset 0.5)")
            lines.append("      (keep_text_aligned yes)")
            lines.append("    )")
            lines.append(f'    (gr_text "{escape_kicad_string(value_text)}"')
            lines.append(f"      (at {fmt_mm(x3)} {fmt_mm(y3)} {fmt_mm(angle_degrees)})")
            lines.append(f'      (layer "{layer_name}")')
            lines.append(
                "      (effects "
                f"(font (size {fmt_mm(text_size)} {fmt_mm(text_size)}) "
                f"(thickness {fmt_mm(text_thickness)})))"
            )
            lines.append("    )")
            lines.append("  )")

    def _collect_dimension_unit_markers(self, package: ET.Element) -> list[DimensionUnitMarker]:
        """Collect unit marker texts used alongside Eagle dimension primitives."""

        markers: list[DimensionUnitMarker] = []
        for text in package.findall("text"):
            raw_text = (text.text or "").strip()
            normalized_text = raw_text.lower()
            if raw_text != '"' and normalized_text not in {"mm", "mil", "mils", "in", "inch"}:
                continue

            layer_number = self._parse_layer_number(text.attrib.get("layer"))
            x = parse_coord(text.attrib.get("x", "0"))
            y = -parse_coord(text.attrib.get("y", "0"))
            size = max(parse_coord(text.attrib.get("size", "1.27")), 0.1)
            rotation = parse_rotation(text.attrib.get("rot"))
            angle_degrees = -rotation.degrees if rotation.mirror else rotation.degrees
            markers.append(
                DimensionUnitMarker(
                    text=raw_text,
                    layer_number=layer_number,
                    x=x,
                    y=y,
                    size=size,
                    angle_degrees=angle_degrees,
                )
            )
        return markers

    def _reserve_dimension_unit_markers(self, package: ET.Element) -> None:
        """Reserve marker texts used by dimensions so they can be omitted from plain text output."""

        for dimension in package.findall("dimension"):
            x1 = parse_coord(dimension.attrib.get("x1", "0"))
            y1 = -parse_coord(dimension.attrib.get("y1", "0"))
            x2 = parse_coord(dimension.attrib.get("x2", "0"))
            y2 = -parse_coord(dimension.attrib.get("y2", "0"))
            x3 = parse_coord(dimension.attrib.get("x3", "0"))
            y3 = -parse_coord(dimension.attrib.get("y3", "0"))
            marker = self._find_dimension_unit_marker(dimension, x1, y1, x2, y2, x3, y3)
            if marker is not None:
                self._dimension_marker_by_element_id[id(dimension)] = marker

    def _is_consumed_dimension_marker_text(self, text: ET.Element) -> bool:
        """Return True when a text element was consumed as a dimension unit marker."""

        if not self._consumed_dimension_unit_marker_indices:
            return False

        raw_text = (text.text or "").strip()
        normalized_text = raw_text if raw_text == '"' else raw_text.lower()
        if normalized_text not in {'"', "mm", "mil", "mils", "in", "inch"}:
            return False

        layer_number = self._parse_layer_number(text.attrib.get("layer"))
        x = parse_coord(text.attrib.get("x", "0"))
        y = -parse_coord(text.attrib.get("y", "0"))
        size = max(parse_coord(text.attrib.get("size", "1.27")), 0.1)
        rotation = parse_rotation(text.attrib.get("rot"))
        angle_degrees = -rotation.degrees if rotation.mirror else rotation.degrees

        for marker_index in self._consumed_dimension_unit_marker_indices:
            marker = self._dimension_unit_markers[marker_index]
            if self._dimension_markers_equivalent(
                marker=marker,
                text=raw_text,
                layer_number=layer_number,
                x=x,
                y=y,
                size=size,
                angle_degrees=angle_degrees,
            ):
                return True

        return False

    def _dimension_markers_equivalent(
        self,
        marker: DimensionUnitMarker,
        text: str,
        layer_number: int | None,
        x: float,
        y: float,
        size: float,
        angle_degrees: float,
    ) -> bool:
        """Compare marker identity by text/layer/geometry with float tolerance."""

        marker_text = marker.text if marker.text == '"' else marker.text.lower()
        current_text = text if text == '"' else text.lower()
        return (
            marker_text == current_text
            and marker.layer_number == layer_number
            and math.isclose(marker.x, x, abs_tol=1e-6)
            and math.isclose(marker.y, y, abs_tol=1e-6)
            and math.isclose(marker.size, size, abs_tol=1e-6)
            and math.isclose(marker.angle_degrees, angle_degrees, abs_tol=1e-6)
        )

    def _parse_layer_number(self, layer_text: str | None) -> int | None:
        """Parse a layer number safely from optional text."""

        if layer_text is None:
            return None
        try:
            return int(layer_text)
        except ValueError:
            return None

    def _dimension_unit_tokens(self, unit: str) -> set[str]:
        """Return valid marker tokens for a given Eagle dimension unit."""

        normalized_unit = unit.strip().lower()
        if normalized_unit in {"inch", "in"}:
            return {'"', "in", "inch"}
        if normalized_unit in {"mil", "mils"}:
            return {"mil", "mils"}
        return {"mm"}

    def _find_dimension_unit_marker(
        self,
        dimension: ET.Element,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
    ) -> DimensionUnitMarker | None:
        """Pick the best unit marker text to pair with one dimension value."""

        target_tokens = self._dimension_unit_tokens(dimension.attrib.get("unit", "mm"))
        dimension_layer_number = self._parse_layer_number(dimension.attrib.get("layer"))
        dtype = dimension.attrib.get("dtype", "").strip().lower()
        available_candidates: list[tuple[int, DimensionUnitMarker]] = []
        candidates: list[tuple[int, DimensionUnitMarker]] = []
        for marker_index, marker in enumerate(self._dimension_unit_markers):
            if marker_index in self._consumed_dimension_unit_marker_indices:
                continue
            available_candidates.append((marker_index, marker))
        if not available_candidates:
            return None

        same_layer_candidates = [
            (marker_index, marker)
            for marker_index, marker in available_candidates
            if dimension_layer_number is not None and marker.layer_number == dimension_layer_number
        ]
        if same_layer_candidates:
            available_candidates = same_layer_candidates

        matching_candidates: list[tuple[int, DimensionUnitMarker]] = []
        for marker_index, marker in available_candidates:
            marker_token = marker.text if marker.text == '"' else marker.text.lower()
            if marker_token in target_tokens:
                matching_candidates.append((marker_index, marker))

        candidates = matching_candidates if matching_candidates else available_candidates

        if dtype == "horizontal":
            right_endpoint = max(x1, x2)

            def score(item: tuple[int, DimensionUnitMarker]) -> tuple[float, float, float, float]:
                _, marker = item
                edge_tolerance = max(marker.size, 0.5)
                return (
                    0.0 if marker.x >= right_endpoint - edge_tolerance else 1.0,
                    abs(marker.y - y3),
                    abs(marker.x - right_endpoint),
                    abs(marker.x - x3),
                )
        elif dtype == "vertical":

            def score(item: tuple[int, DimensionUnitMarker]) -> tuple[float, float]:
                _, marker = item
                return (
                    abs(marker.x - x3),
                    abs(marker.y - y3),
                )
        else:

            def score(item: tuple[int, DimensionUnitMarker]) -> tuple[float]:
                _, marker = item
                return (math.hypot(marker.x - x3, marker.y - y3),)

        marker_index, marker = min(candidates, key=score)
        self._consumed_dimension_unit_marker_indices.add(marker_index)
        return marker

    def _dimension_display_unit(
        self,
        dimension: ET.Element,
        marker: DimensionUnitMarker | None,
    ) -> str:
        """Select display unit for generated dimension text, preferring marker unit when present."""

        dimension_unit = dimension.attrib.get("unit", "mm").strip().lower()
        if marker is None:
            return dimension_unit

        marker_unit = self._marker_text_to_unit(marker.text)
        if marker_unit is None:
            return dimension_unit
        return marker_unit

    def _dimension_kind(self, dtype: str) -> tuple[str, int | None]:
        """Map Eagle dtype to KiCad dimension type and optional orthogonal orientation."""

        if dtype == "horizontal":
            return ("orthogonal", 0)
        if dtype == "vertical":
            return ("orthogonal", 1)
        if dtype not in {"", "aligned", "parallel", "diagonal"}:
            self._warnings.add(f"Converted unsupported Eagle dimension dtype '{dtype}' as aligned.")
        return ("aligned", None)

    def _orthogonal_dimension_height(
        self,
        x1: float,
        y1: float,
        x3: float,
        y3: float,
        orientation: int,
    ) -> float:
        """Compute KiCad orthogonal dimension height from Eagle points."""

        if orientation == 1:
            return x3 - x1
        return y3 - y1

    def _aligned_dimension_height(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
    ) -> float:
        """Compute KiCad aligned dimension height from Eagle feature and crossbar points."""

        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        if length <= 1e-9:
            self._warnings.add("Encountered zero-length Eagle dimension; using zero height.")
            return 0.0
        cross = ((x3 - x1) * delta_y) - ((y3 - y1) * delta_x)
        return -cross / length

    def _dimension_units_mode(self, unit: str) -> int:
        """Map canonical unit token to KiCad DIM_UNITS_MODE enum value."""

        normalized = unit.strip().lower()
        if normalized in {"inch", "in"}:
            return 0
        if normalized in {"mil", "mils"}:
            return 1
        if normalized in {"mm", "millimeter", "millimeters"}:
            return 2
        self._warnings.add(f"Unknown Eagle dimension unit '{unit}', treated as millimeters.")
        return 2

    def _dimension_precision_enum(self, dimension: ET.Element, unit: str) -> int:
        """Map Eagle precision hints to KiCad DIM_PRECISION enum values."""

        precision_text = (dimension.attrib.get("precision") or "").strip()
        if precision_text:
            try:
                digits = int(precision_text)
            except ValueError:
                digits = self._dimension_default_precision_digits(unit)
        else:
            digits = self._dimension_default_precision_digits(unit)
        return int(clamp(float(digits), 0.0, 5.0))

    def _dimension_precision_digits(self, unit: str, precision_enum: int) -> int:
        """Return decimal digits implied by KiCad DIM_PRECISION and units."""

        if precision_enum < 6:
            return max(0, precision_enum)

        normalized = unit.strip().lower()
        if normalized in {"inch", "in"}:
            return max(0, precision_enum - 4)
        if normalized in {"mil", "mils"}:
            return max(0, precision_enum - 7)
        return max(0, precision_enum - 5)

    def _dimension_default_precision_digits(self, unit: str) -> int:
        """Choose fallback decimals when Eagle omits explicit precision."""

        normalized = unit.strip().lower()
        if normalized in {"inch", "in"}:
            return 3
        if normalized in {"mil", "mils"}:
            return 1
        return 3

    def _dimension_suffix(
        self,
        marker: DimensionUnitMarker | None,
        unit: str,
    ) -> str:
        """Choose suffix text for dimension display, preferring explicit Eagle markers."""

        if marker is not None:
            if marker.text == '"':
                return '"'
            marker_text = marker.text.strip()
            if marker_text:
                return f" {marker_text}"
            return ""

        normalized = unit.strip().lower()
        if normalized in {"inch", "in"}:
            return " in"
        if normalized in {"mil", "mils"}:
            return " mils"
        return " mm"

    def _marker_text_to_unit(self, marker_text: str) -> str | None:
        """Map a marker token to a canonical Eagle-like unit name."""

        if marker_text == '"':
            return "inch"
        normalized = marker_text.strip().lower()
        if normalized in {"in", "inch"}:
            return "inch"
        if normalized in {"mil", "mils"}:
            return "mil"
        if normalized == "mm":
            return "mm"
        return None

    def _format_dimension_value_text(
        self,
        dimension: ET.Element,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        unit_override: str | None = None,
        precision_enum: int | None = None,
        suffix: str = "",
    ) -> str:
        """Return a dimension text string matching KiCad value formatting behavior."""

        dtype = dimension.attrib.get("dtype", "").strip().lower()
        if dtype == "horizontal":
            distance_mm = abs(x2 - x1)
        elif dtype == "vertical":
            distance_mm = abs(y2 - y1)
        else:
            distance_mm = math.hypot(x2 - x1, y2 - y1)

        unit = (
            unit_override
            if unit_override is not None
            else dimension.attrib.get("unit", "mm")
        ).strip().lower()
        if unit in {"inch", "in"}:
            value = distance_mm / 25.4
        elif unit in {"mil", "mils"}:
            value = distance_mm / 0.0254
        elif unit in {"mm", "millimeter", "millimeters"}:
            value = distance_mm
        else:
            value = distance_mm
            self._warnings.add(f"Unknown Eagle dimension unit '{unit}', treated as millimeters.")
            unit = "mm"

        if precision_enum is None:
            precision_enum = self._dimension_default_precision_digits(unit)

        precision = self._dimension_precision_digits(unit, precision_enum)
        text = f"{value:.{precision}f}"

        suppress_zeroes = (dimension.attrib.get("suppresszeroes") or "").strip().lower() in {"yes", "true", "1"}
        if suppress_zeroes and "." in text:
            text = text.rstrip("0").rstrip(".")

        if text in {"", "-0", "-0.0"}:
            text = "0"
        return text + suffix

    def _dimension_text_angle(
        self,
        dimension: ET.Element,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> float:
        """Return an angle for the generated dimension value text."""

        dtype = dimension.attrib.get("dtype", "").strip().lower()
        if dtype == "vertical":
            return 90.0
        if dtype == "horizontal":
            return 0.0

        delta_x = x2 - x1
        delta_y = y2 - y1
        if abs(delta_x) < 1e-9 and abs(delta_y) < 1e-9:
            return 0.0
        if abs(delta_x) >= abs(delta_y) * 2.0:
            return 0.0
        if abs(delta_y) >= abs(delta_x) * 2.0:
            return 90.0
        return math.degrees(math.atan2(delta_y, delta_x))

    def _text_style_from_element(
        self,
        text: ET.Element,
        layer_override: str | None = None,
    ) -> TextStyle:
        """Convert Eagle text placement attributes to KiCad text style."""

        layer_name = layer_override or self._primary_mapped_layer(
            text.attrib.get("layer"),
            fallback_layer="Cmts.User",
        )
        x = parse_coord(text.attrib.get("x", "0"))
        y = -parse_coord(text.attrib.get("y", "0"))
        size = parse_coord(text.attrib.get("size", "1.27"))
        ratio = float(text.attrib.get("ratio", "8"))
        thickness = max(size * ratio / 100.0, 0.01)
        kicad_size = max(size - thickness, 0.1)

        align = text.attrib.get("align", "bottom-left")
        h_justify, v_justify = map_text_alignment(align)

        rotation = parse_rotation(text.attrib.get("rot"))
        angle = -rotation.degrees if rotation.mirror else rotation.degrees

        return TextStyle(
            x=x,
            y=y,
            angle_degrees=angle,
            layer=layer_name,
            size=kicad_size,
            thickness=thickness,
            mirror=rotation.mirror,
            h_justify=h_justify,
            v_justify=v_justify,
        )

    def _primary_mapped_layer(
        self,
        layer_text: str | None,
        fallback_layer: str | None = None,
    ) -> str:
        """Return the first mapped KiCad layer for an Eagle layer."""

        layer_names = self._map_layers(layer_text, fallback_layer=fallback_layer)
        if layer_names:
            return layer_names[0]
        return fallback_layer or "Cmts.User"

    def _map_layers(
        self,
        layer_text: str | None,
        fallback_layer: str | None = None,
    ) -> tuple[str, ...]:
        """Map an Eagle layer number to one or more KiCad layer names."""

        if layer_text is None:
            if fallback_layer:
                return (fallback_layer,)
            self._warnings.add("Skipped primitive with no layer attribute.")
            return ()

        try:
            layer = int(layer_text)
        except ValueError:
            if fallback_layer:
                return (fallback_layer,)
            self._warnings.add(f"Skipped primitive with invalid layer value '{layer_text}'.")
            return ()

        if layer in self._layer_map:
            mapped_layers = self._layer_map[layer]
            if mapped_layers:
                return mapped_layers
            self._warnings.add(f"Skipped primitive on Eagle layer {layer} (mapping empty).")
            return ()

        if 2 <= layer <= 15:
            return (f"In{layer - 1}.Cu",)

        if fallback_layer:
            return (fallback_layer,)

        self._warnings.add(f"Skipped primitive on unmapped Eagle layer {layer}.")
        return ()


def normalize_layer_map(layer_map: LayerMapInput | None) -> LayerMapNormalized:
    """Normalize layer map input to explicit tuples for one-to-many mappings."""

    normalized: LayerMapNormalized = {
        layer: (target,)
        for layer, target in DEFAULT_EAGLE_LAYER_MAP.items()
    }
    if layer_map is None:
        return normalized

    for raw_layer, raw_targets in layer_map.items():
        layer = int(raw_layer)
        if isinstance(raw_targets, str):
            targets = [raw_targets]
        else:
            targets = list(raw_targets)

        deduped_targets = tuple(
            dict.fromkeys(target.strip() for target in targets if target and target.strip())
        )
        normalized[layer] = deduped_targets

    return normalized


def validate_eagle_library_path(path: Path) -> None:
    """Validate that an Eagle library path exists and points to a .lbr file."""

    if not path.exists():
        raise FileNotFoundError(f"Eagle library does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"Eagle library path is not a file: {path}")
    if path.suffix.lower() != ".lbr":
        raise ValueError(f"Eagle library should have .lbr extension: {path}")


def validate_pretty_directory(path: Path) -> None:
    """Validate that a destination KiCad .pretty library directory exists."""

    if not path.exists():
        raise FileNotFoundError(f"KiCad library directory does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"KiCad target path is not a directory: {path}")
    if path.suffix.lower() != ".pretty":
        raise ValueError(f"KiCad target directory should end with .pretty: {path}")


def extract_package_description(package: ET.Element) -> str:
    """Extract and normalize a package description."""

    description = package.findtext("description", default="")
    return html.unescape(description).strip()


def count_package_primitives(package: ET.Element) -> dict[str, int]:
    """Count primitive tags in one Eagle package."""

    counts: dict[str, int] = {}
    for child in package:
        counts[child.tag] = counts.get(child.tag, 0) + 1
    return counts


def sanitize_footprint_name(eagle_name: str) -> str:
    """Convert an Eagle package name to a filesystem-safe KiCad footprint base name."""

    cleaned = _ILLEGAL_FILENAME_CHARS.sub("_", eagle_name.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "unnamed_footprint"


def read_kicad_footprint_name(content: str, fallback_name: str) -> str:
    """Extract a KiCad footprint object name from .kicad_mod text."""

    match = _KICAD_FOOTPRINT_HEADER.search(content)
    if match is None:
        return fallback_name
    return html.unescape(match.group(1))


def list_kicad_footprints(pretty_directory: Path) -> list[KiCadFootprintItem]:
    """Enumerate footprints from a KiCad .pretty directory."""

    validate_pretty_directory(pretty_directory)
    items: list[KiCadFootprintItem] = []
    for footprint_path in sorted(pretty_directory.glob("*.kicad_mod")):
        fallback_name = footprint_path.stem
        try:
            content = footprint_path.read_text(encoding="utf-8")
        except OSError:
            items.append(KiCadFootprintItem(name=fallback_name, file_path=footprint_path))
            continue
        footprint_name = read_kicad_footprint_name(content, fallback_name)
        items.append(KiCadFootprintItem(name=footprint_name, file_path=footprint_path))
    return sorted(items, key=lambda item: (item.name.lower(), item.name, item.file_path.name.lower()))


def infer_affix_pattern(names: Sequence[str]) -> tuple[str, str]:
    """Infer common prefix/postfix for selected names, avoiding overlap."""

    normalized_names = [name for name in names if name]
    if len(normalized_names) <= 1:
        return ("", "")

    common_prefix = normalized_names[0]
    for name in normalized_names[1:]:
        while common_prefix and not name.startswith(common_prefix):
            common_prefix = common_prefix[:-1]
        if not common_prefix:
            break

    remainders = [name[len(common_prefix):] for name in normalized_names]
    if not remainders:
        return (common_prefix, "")

    common_postfix_reversed = remainders[0][::-1]
    for remainder in remainders[1:]:
        reversed_remainder = remainder[::-1]
        while common_postfix_reversed and not reversed_remainder.startswith(common_postfix_reversed):
            common_postfix_reversed = common_postfix_reversed[:-1]
        if not common_postfix_reversed:
            break

    return (common_prefix, common_postfix_reversed[::-1])


def match_affix_pattern(name: str, old_prefix: str, old_postfix: str) -> bool:
    """Return True when name matches the old prefix/postfix rename pattern."""

    if not name.startswith(old_prefix):
        return False
    if old_postfix and not name.endswith(old_postfix):
        return False
    return len(name) >= len(old_prefix) + len(old_postfix)


def apply_affix_pattern(
    name: str,
    old_prefix: str,
    old_postfix: str,
    new_prefix: str,
    new_postfix: str,
) -> str | None:
    """Apply an affix rename pattern and return the new name, or None when unmatched."""

    if not match_affix_pattern(name, old_prefix, old_postfix):
        return None

    if old_postfix:
        base_end = len(name) - len(old_postfix)
    else:
        base_end = len(name)
    base = name[len(old_prefix):base_end]
    return new_prefix + base + new_postfix


def rewrite_kicad_footprint_name(content: str, new_name: str) -> str:
    """Rewrite footprint object name and synchronized Value property when appropriate."""

    header_match = _KICAD_FOOTPRINT_HEADER.search(content)
    if header_match is None:
        raise ValueError("Unable to rename footprint: missing '(footprint \"...\")' header.")

    escaped_new_name = escape_kicad_string(new_name)
    old_name = header_match.group(1)
    updated_content = (
        content[:header_match.start(1)]
        + escaped_new_name
        + content[header_match.end(1):]
    )

    def _replace_value(match: re.Match[str]) -> str:
        current_value = match.group(2)
        if current_value != old_name:
            return match.group(0)
        return f"{match.group(1)}{escaped_new_name}{match.group(3)}"

    return _KICAD_VALUE_PROPERTY.sub(_replace_value, updated_content, count=1)


def parse_eagle_distance(value: str) -> float:
    """Parse an Eagle dimension string to millimeters."""

    text = value.strip().lower()
    if text.endswith("mil"):
        return float(text[:-3]) * 0.0254
    if text.endswith("mm"):
        return float(text[:-2])
    if text.endswith("inch"):
        return float(text[:-4]) * 25.4
    if text.endswith("in"):
        return float(text[:-2]) * 25.4
    return float(text)


def parse_coord(value: str) -> float:
    """Parse coordinate-like Eagle fields to millimeters."""

    return parse_eagle_distance(value)


def parse_rotation(rotation_text: str | None) -> Rotation:
    """Parse Eagle rotation format [S][M]R<degrees>."""

    if not rotation_text:
        return Rotation(degrees=0.0, mirror=False, spin=False)
    mirror = "M" in rotation_text
    spin = "S" in rotation_text
    number_match = _ROTATION_NUMBER.search(rotation_text)
    degrees = float(number_match.group(0)) if number_match else 0.0
    return Rotation(degrees=degrees, mirror=mirror, spin=spin)


def map_text_alignment(align: str) -> tuple[str | None, str | None]:
    """Map Eagle alignment names to KiCad justify tokens."""

    mapping: dict[str, tuple[str | None, str | None]] = {
        "center": (None, None),
        "center-left": ("left", None),
        "center-right": ("right", None),
        "top-left": ("left", "top"),
        "top-center": (None, "top"),
        "top-right": ("right", "top"),
        "bottom-left": ("left", "bottom"),
        "bottom-center": (None, "bottom"),
        "bottom-right": ("right", "bottom"),
    }
    return mapping.get(align, ("left", "bottom"))


def calculate_arc_center(start: Point, end: Point, curve_degrees: float) -> Point:
    """Calculate arc center using KiCad's Eagle importer geometry."""

    dx = end.x - start.x
    dy = end.y - start.y
    length = math.hypot(dx, dy)
    if length == 0 or curve_degrees == 0:
        return Point(start.x, start.y)

    midpoint = Point((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    tan_half = math.tan(math.radians(curve_degrees) / 2.0)
    if tan_half == 0:
        return midpoint

    dist = length / (2.0 * tan_half)
    center = Point(
        midpoint.x + dist * (dy / length),
        midpoint.y - dist * (dx / length),
    )
    return center


def calculate_arc_midpoint(start: Point, end: Point, curve_degrees: float) -> Point:
    """Calculate KiCad fp_arc midpoint from Eagle start/end/curve."""

    center = calculate_arc_center(start, end, curve_degrees)
    radius = math.hypot(start.x - center.x, start.y - center.y)
    if radius == 0:
        return Point((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)

    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    # KiCad's coordinate system flips Y vs Eagle, so effective sweep sign is inverted.
    middle_angle = start_angle - math.radians(curve_degrees) / 2.0
    return Point(center.x + radius * math.cos(middle_angle), center.y + radius * math.sin(middle_angle))


def polygon_vertices_to_points(vertices: Sequence[ET.Element]) -> list[Point]:
    """Convert Eagle polygon vertices (including curves) into KiCad polygon points."""

    points: list[Point] = []
    ring = list(vertices) + [vertices[0]]

    for index in range(len(ring) - 1):
        current = ring[index]
        nxt = ring[index + 1]
        current_point = Point(parse_coord(current.attrib["x"]), -parse_coord(current.attrib["y"]))
        next_point = Point(parse_coord(nxt.attrib["x"]), -parse_coord(nxt.attrib["y"]))
        points.append(current_point)

        curve_value = current.attrib.get("curve")
        if not curve_value:
            continue

        curve_degrees = float(curve_value)
        if curve_degrees == 0:
            continue

        center = calculate_arc_center(current_point, next_point, curve_degrees)
        radius = math.hypot(current_point.x - center.x, current_point.y - center.y)
        if radius == 0:
            continue

        sweep = math.radians(curve_degrees)
        end_angle = math.atan2(next_point.y - center.y, next_point.x - center.x)
        segment_count = max(2, int(math.ceil(abs(curve_degrees) / 10.0)))
        delta = sweep / segment_count
        angle = end_angle + sweep
        while abs(angle - end_angle) > abs(delta):
            points.append(
                Point(
                    center.x + radius * math.cos(angle),
                    center.y + radius * math.sin(angle),
                )
            )
            angle -= delta

    return points


def rotate_point(point: Point, center: Point, degrees: float) -> Point:
    """Rotate a point around a center by degrees."""

    radians = math.radians(degrees)
    cos_angle = math.cos(radians)
    sin_angle = math.sin(radians)
    px = point.x - center.x
    py = point.y - center.y
    return Point(
        center.x + px * cos_angle - py * sin_angle,
        center.y + px * sin_angle + py * cos_angle,
    )


def default_line_width_for_layer(layer_name: str) -> float:
    """Return a KiCad-like default line width for zero-width Eagle wires."""

    if layer_name in {"F.SilkS", "B.SilkS"}:
        return 0.12
    if layer_name in {"F.CrtYd", "B.CrtYd"}:
        return 0.05
    if layer_name == "Edge.Cuts":
        return 0.1
    return 0.1


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp a value to [minimum, maximum]."""

    return min(maximum, max(minimum, value))


def escape_kicad_string(text: str) -> str:
    """Escape a string for KiCad S-expression string values."""
    sanitized = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return sanitized.replace("\\", "\\\\").replace('"', '\\"')


def fmt_mm(value: float) -> str:
    """Format millimeter value for compact KiCad output."""

    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in {"-0", "-0.0", ""}:
        return "0"
    return text


def convert_packages(
    library: EagleLibrary,
    package_names: Iterable[str],
    destination_pretty: Path,
    overwrite: bool = False,
    include_dimensions: bool = True,
    layer_map: LayerMapInput | None = None,
) -> list[ConversionResult]:
    """Convert selected packages and write .kicad_mod files."""

    validate_pretty_directory(destination_pretty)
    converter = EagleToKiCadConverter(
        library.design_rules,
        include_dimensions=include_dimensions,
        layer_map=layer_map,
    )
    results: list[ConversionResult] = []

    for package_name in package_names:
        package = library.packages.get(package_name)
        if package is None:
            kicad_name = sanitize_footprint_name(package_name)
            results.append(
                ConversionResult(
                    eagle_name=package_name,
                    kicad_name=kicad_name,
                    output_path=destination_pretty / f"{kicad_name}.kicad_mod",
                    created=False,
                    warnings=(f"Package '{package_name}' not found in library.",),
                )
            )
            continue

        kicad_name = sanitize_footprint_name(package_name)
        output_path = destination_pretty / f"{kicad_name}.kicad_mod"
        if output_path.exists() and not overwrite:
            results.append(
                ConversionResult(
                    eagle_name=package_name,
                    kicad_name=kicad_name,
                    output_path=output_path,
                    created=False,
                    warnings=("Destination footprint already exists (overwrite disabled).",),
                )
            )
            continue

        content, warnings = converter.convert_package(package, package_name)
        output_path.write_text(content, encoding="utf-8")
        results.append(
            ConversionResult(
                eagle_name=package_name,
                kicad_name=kicad_name,
                output_path=output_path,
                created=True,
                warnings=warnings,
            )
        )

    return results


def build_cli_parser() -> argparse.ArgumentParser:
    """Create CLI parser for headless conversion usage."""

    parser = argparse.ArgumentParser(
        description="Convert selected Eagle .lbr packages into KiCad .kicad_mod footprints."
    )
    parser.add_argument("--eagle-lib", type=Path, required=True, help="Path to Eagle .lbr file.")
    parser.add_argument(
        "--kicad-pretty",
        type=Path,
        required=True,
        help="Path to target KiCad .pretty directory.",
    )
    parser.add_argument(
        "--package",
        action="append",
        default=[],
        help="Eagle package name to import. Repeat for multiple packages.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List packages in Eagle library and exit.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .kicad_mod files in destination.",
    )
    parser.add_argument(
        "--include-dimensions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Import Eagle dimension primitives and dimension-layer text (default: enabled).",
    )
    parser.add_argument(
        "--layer-map",
        action="append",
        default=[],
        metavar="EAGLE_LAYER:KICAD_LAYER[,KICAD_LAYER...]",
        help=(
            "Override one Eagle layer mapping. Repeatable. "
            "Example: --layer-map 21:F.SilkS,F.Fab ; use an empty right side to disable a layer."
        ),
    )
    parser.add_argument(
        "--analyze-migration",
        action="store_true",
        help="Generate migration analysis report artifact for the Eagle library.",
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        default=None,
        help=(
            "Output path for migration analysis JSON artifact. "
            "Default: <kicad-pretty>/<eagle-lib-stem>.migration-analysis.json"
        ),
    )
    parser.add_argument(
        "--kicad-project-dir",
        type=Path,
        default=None,
        help="Optional KiCad project directory for project-scope library table discovery.",
    )
    parser.add_argument(
        "--kicad-config-home",
        type=Path,
        default=None,
        help="Optional KiCad config directory containing global sym-lib-table/fp-lib-table.",
    )
    return parser

def parse_layer_map_overrides(entries: Sequence[str]) -> LayerMapNormalized:
    """Parse CLI layer mapping overrides."""

    overrides: LayerMapNormalized = {}
    for entry in entries:
        if ":" not in entry:
            raise ValueError(f"Invalid --layer-map '{entry}'. Expected EAGLE_LAYER:KICAD_LAYER[,KICAD_LAYER...]")

        layer_text, target_text = entry.split(":", 1)
        layer = int(layer_text.strip())
        targets = tuple(
            dict.fromkeys(
                token.strip()
                for token in target_text.split(",")
                if token.strip()
            )
        )
        overrides[layer] = targets

    return overrides


def run_cli(args: argparse.Namespace) -> int:
    """Execute CLI command and print results."""

    library = EagleLibrary.load(args.eagle_lib)
    layer_map_overrides = parse_layer_map_overrides(args.layer_map)

    if args.list:
        for package in library.list_packages():
            print(package.eagle_name)
        return 0

    selected_packages = list(dict.fromkeys(args.package))
    if not selected_packages and not args.analyze_migration:
        print(
            "No packages requested. Use --package PACKAGE_NAME (repeatable), "
            "--list, or --analyze-migration."
        )
        return 1

    if selected_packages:
        results = convert_packages(
            library=library,
            package_names=selected_packages,
            destination_pretty=args.kicad_pretty,
            overwrite=args.overwrite,
            include_dimensions=args.include_dimensions,
            layer_map=layer_map_overrides or None,
        )
        created_count = 0
        for result in results:
            status = "CREATED" if result.created else "SKIPPED"
            print(f"[{status}] {result.eagle_name} -> {result.output_path.name}")
            if result.created:
                created_count += 1
            for warning in result.warnings:
                print(f"  - {warning}")
        print(f"\nConverted {created_count}/{len(results)} package(s).")

    if args.analyze_migration:
        artifact = run_migration_analysis(
            eagle_library=args.eagle_lib,
            kicad_project_directory=args.kicad_project_dir,
            kicad_config_home=args.kicad_config_home,
        )
        output_path = (
            args.analysis_output
            if args.analysis_output is not None
            else args.kicad_pretty / f"{args.eagle_lib.stem}.migration-analysis.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(artifact.to_json_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        print("\nMigration analysis report:")
        print(
            "  queue counts: "
            f"none={artifact.queue_counts['none']} "
            f"standard={artifact.queue_counts['standard']} "
            f"priority={artifact.queue_counts['priority']}"
        )
        print(f"  artifact: {output_path}")
    return 0


def main() -> int:
    """CLI entrypoint."""

    parser = build_cli_parser()
    args = parser.parse_args()
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
