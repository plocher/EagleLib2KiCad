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
    symbol_pin_count: int
    mapped_pin_count: int
    package_pad_count: int
    is_power_symbol: bool
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

        symbol_pin_counts = self._symbol_pin_count_index(root)
        package_pad_counts = self._package_pad_count_index(root)

        for deviceset in root.findall(".//devicesets/deviceset"):
            deviceset_name = str(deviceset.get("name", "")).strip()
            if not deviceset_name:
                continue

            symbol_name = ""
            gate_symbol_names: list[str] = []
            first_gate = deviceset.find("./gates/gate")
            if first_gate is not None:
                symbol_name = str(first_gate.get("symbol", "")).strip()
            for gate in deviceset.findall("./gates/gate"):
                gate_symbol_name = str(gate.get("symbol", "")).strip()
                if gate_symbol_name:
                    gate_symbol_names.append(gate_symbol_name)
            if not symbol_name:
                symbol_name = deviceset_name
            symbol_pin_count = self._deviceset_symbol_pin_count(
                gate_symbol_names=gate_symbol_names,
                symbol_pin_counts=symbol_pin_counts,
            )

            devices = deviceset.findall("./devices/device")
            is_power_symbol = self._is_power_symbol_deviceset(
                deviceset=deviceset,
                gate_symbol_names=gate_symbol_names,
                symbol_pin_counts=symbol_pin_counts,
            )
            if not devices:
                contexts.append(
                    EagleDeviceContext(
                        deviceset_name=deviceset_name,
                        device_name="default",
                        symbol_name=symbol_name,
                        package_name="",
                        symbol_pin_count=symbol_pin_count,
                        mapped_pin_count=0,
                        package_pad_count=0,
                        is_power_symbol=is_power_symbol,
                        source_file=source_file,
                    )
                )
                continue

            for device in devices:
                package_name = str(device.get("package", "")).strip()
                contexts.append(
                    EagleDeviceContext(
                        deviceset_name=deviceset_name,
                        device_name=str(device.get("name", "")).strip() or "default",
                        symbol_name=symbol_name,
                        package_name=package_name,
                        symbol_pin_count=symbol_pin_count,
                        mapped_pin_count=self._mapped_pin_count(device),
                        package_pad_count=package_pad_counts.get(package_name, 0),
                        is_power_symbol=is_power_symbol,
                        source_file=source_file,
                    )
                )

        return contexts

    @staticmethod
    def _mapped_pin_count(device: ET.Element) -> int:
        """Count unique mapped Eagle pins for one device variant."""
        pins: set[str] = set()
        for connect in device.findall("./connects/connect"):
            pin_name = str(connect.get("pin", "")).strip()
            if pin_name:
                pins.add(pin_name)
        return len(pins)

    @staticmethod
    def _symbol_pin_count_index(root: ET.Element) -> dict[str, int]:
        """Index Eagle symbol name to declared pin count."""
        index: dict[str, int] = {}
        for symbol in root.findall(".//symbols/symbol"):
            symbol_name = str(symbol.get("name", "")).strip()
            if not symbol_name:
                continue
            pins = {
                str(pin.get("name", "")).strip()
                for pin in symbol.findall("./pin")
                if str(pin.get("name", "")).strip()
            }
            index[symbol_name] = len(pins)
        return index

    @staticmethod
    def _package_pad_count_index(root: ET.Element) -> dict[str, int]:
        """Index Eagle package name to unique pad/smd count."""
        index: dict[str, int] = {}
        for package in root.findall(".//packages/package"):
            package_name = str(package.get("name", "")).strip()
            if not package_name:
                continue
            pins = {
                str(element.get("name", "")).strip()
                for element in package.findall("./pad") + package.findall("./smd")
                if str(element.get("name", "")).strip()
            }
            index[package_name] = len(pins)
        return index

    @staticmethod
    def _deviceset_symbol_pin_count(
        *,
        gate_symbol_names: Sequence[str],
        symbol_pin_counts: dict[str, int],
    ) -> int:
        """Estimate deviceset symbol pin count from gate-symbol pin declarations."""
        if not gate_symbol_names:
            return 0
        count = 0
        for symbol_name in gate_symbol_names:
            count += symbol_pin_counts.get(symbol_name, 0)
        return count

    @staticmethod
    def _is_power_symbol_deviceset(
        *,
        deviceset: ET.Element,
        gate_symbol_names: Sequence[str],
        symbol_pin_counts: dict[str, int],
    ) -> bool:
        """Identify likely schematic power symbols from Eagle metadata."""
        if not gate_symbol_names:
            return False
        if any(symbol_pin_counts.get(symbol_name, 0) != 1 for symbol_name in gate_symbol_names):
            return False
        devices = deviceset.findall("./devices/device")
        if not devices:
            return True
        for device in devices:
            package_name = str(device.get("package", "")).strip()
            has_connects = bool(device.findall("./connects/connect"))
            if package_name or has_connects:
                return False
        return True

