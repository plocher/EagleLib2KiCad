"""Eagle library context service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class EagleDeviceContext:
    """One Eagle deviceset/device context."""

    deviceset_name: str
    device_name: str
    symbol_name: str
    package_name: str
    source_file: Path


class EagleLibraryContextService:
    """Extract normalized device contexts from an Eagle `.lbr` file."""

    def load_device_contexts(self, library_file: Path) -> tuple[EagleDeviceContext, ...]:
        """Load all Eagle deviceset/device contexts from a library file."""
        if not library_file.exists():
            raise FileNotFoundError(f"Eagle library not found: {library_file}")
        if library_file.suffix.lower() != ".lbr":
            raise ValueError(f"Expected .lbr file, got: {library_file.suffix}")

        try:
            tree = ET.parse(library_file)
        except ET.ParseError as exc:
            raise ValueError(f"Failed to parse Eagle library {library_file}: {exc}") from exc

        contexts = self._extract_device_contexts(tree.getroot(), source_file=library_file.resolve())
        return tuple(contexts)

    def _extract_device_contexts(
        self,
        root: ET.Element,
        *,
        source_file: Path,
    ) -> Sequence[EagleDeviceContext]:
        """Extract contexts from parsed Eagle XML."""
        contexts: list[EagleDeviceContext] = []

        for deviceset in root.findall(".//devicesets/deviceset"):
            deviceset_name = str(deviceset.get("name", "")).strip()
            if not deviceset_name:
                continue

            symbol_name = ""
            first_gate = deviceset.find("./gates/gate")
            if first_gate is not None:
                symbol_name = str(first_gate.get("symbol", "")).strip()
            if not symbol_name:
                symbol_name = deviceset_name

            devices = deviceset.findall("./devices/device")
            if not devices:
                contexts.append(
                    EagleDeviceContext(
                        deviceset_name=deviceset_name,
                        device_name="default",
                        symbol_name=symbol_name,
                        package_name="",
                        source_file=source_file,
                    )
                )
                continue

            for device in devices:
                contexts.append(
                    EagleDeviceContext(
                        deviceset_name=deviceset_name,
                        device_name=str(device.get("name", "")).strip() or "default",
                        symbol_name=symbol_name,
                        package_name=str(device.get("package", "")).strip(),
                        source_file=source_file,
                    )
                )

        return contexts

