"""Interactive Eagle-to-KiCad footprint importer UI.

This app is intentionally simple and local-first:
- Load an Eagle .lbr
- Filter and select package footprints
- Import selected packages into a KiCad .pretty library
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Literal, Sequence

from eagle_to_kicad_converter import (
    apply_affix_pattern,
    infer_affix_pattern,
    list_kicad_footprints,
    match_affix_pattern,
    rewrite_kicad_footprint_name,
    ConversionResult,
    DEFAULT_EAGLE_LAYER_MAP,
    EagleLibrary,
    EagleToKiCadConverter,
    KiCadFootprintItem,
    PackageInfo,
    convert_packages,
    sanitize_footprint_name,
)


DEFAULT_EAGLE_LIBRARY = Path("/Users/jplocher/Dropbox/eagle/EagleTools/parts/SPCoast-eagle.lbr")
DEFAULT_KICAD_PRETTY = Path("/Users/jplocher/Dropbox/KiCad/SPCoast_KiCad_Library/footprints/SPCoast.pretty")
KI_LAYER_CHOICES = [
    "F.Cu",
    *[f"In{index}.Cu" for index in range(1, 15)],
    "B.Cu",
    "F.Paste",
    "B.Paste",
    "F.Mask",
    "B.Mask",
    "F.SilkS",
    "B.SilkS",
    "F.Fab",
    "B.Fab",
    "F.CrtYd",
    "B.CrtYd",
    "F.Adhes",
    "B.Adhes",
    "Edge.Cuts",
    "Dwgs.User",
    "Cmts.User",
    "Eco1.User",
    "Eco2.User",
    "Margin",
]
EAGLE_ACCENT_COLOR = "#8bb8ff"
KICAD_ACCENT_COLOR = "#8dd9a2"
EAGLE_FIELD_COLOR = "#1e2d44"
KICAD_FIELD_COLOR = "#1f3528"
EAGLE_TEXT_COLOR = "#eef5ff"
KICAD_TEXT_COLOR = "#eefcf1"
LAST_LAYER_MAPPING_PATH_FILE = Path.home() / ".spcoast_eagle_to_kicad_last_mapping_path.txt"

RenamePreviewStatus = Literal["rename", "unchanged", "blocked"]
ImportPresenceStatus = Literal["new", "imported", "conflict", "renamed", "unknown"]


def configure_ui_styles(master: tk.Misc) -> None:
    """Configure shared accent styles for Eagle/KiCad visual separation."""

    style = ttk.Style(master)
    style.configure("Eagle.TLabel", foreground=EAGLE_ACCENT_COLOR)
    style.configure("KiCad.TLabel", foreground=KICAD_ACCENT_COLOR)
    style.configure("EagleHeader.TLabel", foreground=EAGLE_ACCENT_COLOR)
    style.configure("KiCadHeader.TLabel", foreground=KICAD_ACCENT_COLOR)


@dataclass(frozen=True)
class RenamePreviewRow:
    """One row in the rename preview table."""

    source_name: str
    target_name: str
    status: RenamePreviewStatus


@dataclass(frozen=True)
class RenameTransactionEntry:
    """One renamed file and its reversible content/path state."""

    source_name: str
    target_name: str
    source_path: Path
    target_path: Path
    source_content: str
    target_content: str


@dataclass(frozen=True)
class ImportPresenceInfo:
    """One import-list status classification against destination .pretty contents."""

    status: ImportPresenceStatus
    detail: str = ""


class FootprintRenamePanel(ttk.Frame):
    """Embedded panel for pattern-based renaming of KiCad footprints."""

    def __init__(self, master: tk.Misc, pretty_directory: Path) -> None:
        super().__init__(master, padding=10)
        configure_ui_styles(master)

        self.pretty_directory = pretty_directory
        self.library_label_var = tk.StringVar(value=f"Library: {self.pretty_directory.name}")
        self.search_var = tk.StringVar()
        self.filter_case_insensitive_var = tk.BooleanVar(value=True)
        self.pattern_mode_var = tk.StringVar(value="affix")
        self.old_prefix_var = tk.StringVar()
        self.old_postfix_var = tk.StringVar()
        self.new_prefix_var = tk.StringVar()
        self.new_postfix_var = tk.StringVar()
        self.regex_search_var = tk.StringVar()
        self.regex_replace_var = tk.StringVar()
        self.regex_ignore_case_var = tk.BooleanVar(value=False)
        self.collision_mode_var = tk.StringVar(value="block")
        self.allow_partial_apply_var = tk.BooleanVar(value=True)
        self.show_blocked_rows_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Adjust pattern fields or select footprints to begin.")

        self._updating_selection = False
        self._updating_old_pattern = False
        self._visible_footprints: list[KiCadFootprintItem] = []
        self._all_footprints: list[KiCadFootprintItem] = []
        self._footprint_by_name: dict[str, KiCadFootprintItem] = {}
        self._preview_rows: list[RenamePreviewRow] = []
        self._last_transaction: list[RenameTransactionEntry] = []

        self._build_ui()
        self._wire_events()
        self._set_pretty_directory(pretty_directory, show_error=False)

    def _build_ui(self) -> None:
        """Create rename dialog widgets."""

        self.columnconfigure(0, weight=5)
        self.columnconfigure(1, weight=7)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Filter", style="KiCadHeader.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(header, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        ttk.Checkbutton(
            header,
            text="Case-insensitive",
            variable=self.filter_case_insensitive_var,
        ).grid(row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(header, textvariable=self.library_label_var, style="KiCad.TLabel").grid(
            row=0,
            column=3,
            sticky="e",
            padx=(12, 0),
        )

        left = ttk.Frame(self)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        list_toolbar = ttk.Frame(left)
        list_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(list_toolbar, text="Select All Visible", command=self._select_all_visible).pack(side=tk.LEFT)
        ttk.Button(list_toolbar, text="Clear Selection", command=self._clear_selection).pack(
            side=tk.LEFT,
            padx=(8, 0),
        )
        ttk.Button(list_toolbar, text="Select Renamable", command=self._select_renamable).pack(
            side=tk.LEFT,
            padx=(8, 0),
        )

        list_frame = ttk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.footprint_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            activestyle="none",
            exportselection=False,
            background=KICAD_FIELD_COLOR,
            foreground=KICAD_TEXT_COLOR,
            selectbackground=KICAD_ACCENT_COLOR,
            selectforeground="#102417",
        )
        self.footprint_list.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.footprint_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.footprint_list.configure(yscrollcommand=list_scroll.set)

        right = ttk.Frame(self)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        pattern_box = ttk.LabelFrame(right, text="Pattern", padding=8)
        pattern_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        pattern_box.columnconfigure(0, weight=1)

        self.pattern_mode_tabs = ttk.Notebook(pattern_box)
        self.affix_mode_frame = ttk.Frame(self.pattern_mode_tabs, padding=8)
        self.regex_mode_frame = ttk.Frame(self.pattern_mode_tabs, padding=8)
        self.pattern_mode_tabs.add(self.affix_mode_frame, text="Affix")
        self.pattern_mode_tabs.add(self.regex_mode_frame, text="Regex")
        self.pattern_mode_tabs.grid(row=0, column=0, sticky="ew")
        self.pattern_mode_tabs.bind("<<NotebookTabChanged>>", self._on_pattern_mode_tab_changed)

        self.affix_mode_frame.columnconfigure(1, weight=1)
        self.affix_mode_frame.columnconfigure(3, weight=1)
        ttk.Label(self.affix_mode_frame, text="Old prefix").grid(row=0, column=0, sticky="w")
        self.old_prefix_entry = ttk.Entry(self.affix_mode_frame, textvariable=self.old_prefix_var)
        self.old_prefix_entry.grid(row=0, column=1, sticky="ew", padx=(6, 10))
        ttk.Label(self.affix_mode_frame, text="Old postfix").grid(row=0, column=2, sticky="w")
        self.old_postfix_entry = ttk.Entry(self.affix_mode_frame, textvariable=self.old_postfix_var)
        self.old_postfix_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        ttk.Label(self.affix_mode_frame, text="New prefix").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.new_prefix_entry = ttk.Entry(self.affix_mode_frame, textvariable=self.new_prefix_var)
        self.new_prefix_entry.grid(row=1, column=1, sticky="ew", padx=(6, 10), pady=(6, 0))
        ttk.Label(self.affix_mode_frame, text="New postfix").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.new_postfix_entry = ttk.Entry(self.affix_mode_frame, textvariable=self.new_postfix_var)
        self.new_postfix_entry.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(6, 0))

        self.regex_mode_frame.columnconfigure(1, weight=1)
        self.regex_mode_frame.columnconfigure(3, weight=1)
        ttk.Label(self.regex_mode_frame, text="Regex search").grid(row=0, column=0, sticky="w")
        self.regex_search_entry = ttk.Entry(self.regex_mode_frame, textvariable=self.regex_search_var)
        self.regex_search_entry.grid(row=0, column=1, sticky="ew", padx=(6, 10))
        ttk.Label(self.regex_mode_frame, text="Regex replace").grid(row=0, column=2, sticky="w")
        self.regex_replace_entry = ttk.Entry(self.regex_mode_frame, textvariable=self.regex_replace_var)
        self.regex_replace_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self.regex_ignore_case_check = ttk.Checkbutton(
            self.regex_mode_frame,
            text="Regex ignore case",
            variable=self.regex_ignore_case_var,
        )
        self.regex_ignore_case_check.grid(row=1, column=3, sticky="e", pady=(6, 0))

        if self.pattern_mode_var.get() == "regex":
            self.pattern_mode_tabs.select(self.regex_mode_frame)
        else:
            self.pattern_mode_tabs.select(self.affix_mode_frame)

        collisions_row = ttk.Frame(pattern_box)
        collisions_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        collisions_row.columnconfigure(1, weight=1)
        ttk.Label(collisions_row, text="Collisions").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            collisions_row,
            text="Block conflicting targets",
            value="block",
            variable=self.collision_mode_var,
        ).grid(row=0, column=1, sticky="w", padx=(10, 10))
        ttk.Radiobutton(
            collisions_row,
            text="Auto dedupe target names",
            value="dedupe",
            variable=self.collision_mode_var,
        ).grid(row=0, column=2, sticky="w")

        preview_header = ttk.Frame(right)
        preview_header.grid(row=1, column=0, sticky="ew")
        preview_header.columnconfigure(0, weight=1)
        ttk.Label(preview_header, text="Live Preview", style="KiCadHeader.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self.apply_button = ttk.Button(
            preview_header,
            text="Apply Rename",
            command=self._apply_rename,
            state=tk.DISABLED,
        )
        self.apply_button.grid(row=0, column=1, sticky="e")
        self.undo_button = ttk.Button(
            preview_header,
            text="Undo Last Rename",
            command=self._undo_last_rename,
            state=tk.DISABLED,
        )
        self.undo_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Checkbutton(
            preview_header,
            text="Apply renamable only",
            variable=self.allow_partial_apply_var,
        ).grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Checkbutton(
            preview_header,
            text="Show blocked",
            variable=self.show_blocked_rows_var,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        preview_frame = ttk.Frame(right)
        preview_frame.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        tree_style = ttk.Style(self)
        tree_style.configure(
            "RenamePreview.Treeview",
            background=KICAD_FIELD_COLOR,
            fieldbackground=KICAD_FIELD_COLOR,
            foreground=KICAD_TEXT_COLOR,
        )
        tree_style.map(
            "RenamePreview.Treeview",
            background=[("selected", KICAD_ACCENT_COLOR)],
            foreground=[("selected", "#102417")],
        )

        self.preview_tree = ttk.Treeview(
            preview_frame,
            columns=("source", "target"),
            show="headings",
            selectmode="none",
            style="RenamePreview.Treeview",
        )
        self.preview_tree.heading("source", text="Current Name")
        self.preview_tree.heading("target", text="Target Name")
        self.preview_tree.column("source", width=320, anchor=tk.W)
        self.preview_tree.column("target", width=320, anchor=tk.W)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview_tree.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_tree.configure(yscrollcommand=preview_scroll.set)
        self.preview_tree.tag_configure("rename", foreground="#2b7f2b")
        self.preview_tree.tag_configure("unchanged", foreground="#7a7a7a")
        self.preview_tree.tag_configure("blocked", foreground="#a11414")
        self.preview_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self.preview_tree.selection_remove(self.preview_tree.selection()),
        )
        self.preview_tree.bind(
            "<FocusOut>",
            lambda _event: self.preview_tree.selection_remove(self.preview_tree.selection()),
        )


        status = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _wire_events(self) -> None:
        """Attach variable traces and list selection events."""

        self.search_var.trace_add("write", lambda *_: self._refresh_visible_footprints())
        self.filter_case_insensitive_var.trace_add("write", lambda *_: self._refresh_visible_footprints())
        self.old_prefix_var.trace_add("write", lambda *_: self._on_old_pattern_changed())
        self.old_postfix_var.trace_add("write", lambda *_: self._on_old_pattern_changed())
        self.new_prefix_var.trace_add("write", lambda *_: self._refresh_preview())
        self.new_postfix_var.trace_add("write", lambda *_: self._refresh_preview())
        self.regex_search_var.trace_add("write", lambda *_: self._refresh_preview())
        self.regex_replace_var.trace_add("write", lambda *_: self._refresh_preview())
        self.regex_ignore_case_var.trace_add("write", lambda *_: self._refresh_preview())
        self.collision_mode_var.trace_add("write", lambda *_: self._refresh_preview())
        self.allow_partial_apply_var.trace_add("write", lambda *_: self._refresh_preview())
        self.show_blocked_rows_var.trace_add("write", lambda *_: self._render_preview())
        self.footprint_list.bind("<<ListboxSelect>>", lambda _event: self._on_list_selection_changed())
        self.footprint_list.bind("<Control-Button-1>", self._on_list_ctrl_click)
        self.footprint_list.bind("<Command-Button-1>", self._on_list_ctrl_click)

    def _on_list_ctrl_click(self, event: tk.Event[tk.Listbox]) -> str:
        """Toggle one footprint row selection for disjoint multi-select workflows."""

        index = self.footprint_list.nearest(event.y)
        if index < 0 or index >= len(self._visible_footprints):
            return "break"

        self._updating_selection = True
        try:
            if index in self.footprint_list.curselection():
                self.footprint_list.selection_clear(index)
            else:
                self.footprint_list.selection_set(index)
            self.footprint_list.activate(index)
            self.footprint_list.see(index)
        finally:
            self._updating_selection = False

        self._on_list_selection_changed()
        return "break"

    def set_pretty_directory(self, pretty_directory: Path, show_error: bool = True) -> bool:
        """Set target KiCad library and refresh visible footprints."""
        return self._set_pretty_directory(pretty_directory, show_error=show_error)

    def _set_pretty_directory(self, pretty_directory: Path, show_error: bool) -> bool:
        """Internal directory update helper with optional user-facing errors."""

        self.pretty_directory = pretty_directory
        self.library_label_var.set(f"Library: {pretty_directory.name}")
        try:
            self._reload_footprints()
        except Exception as exc:
            self._all_footprints = []
            self._visible_footprints = []
            self._footprint_by_name = {}
            self._preview_rows = []
            self.footprint_list.delete(0, tk.END)
            self._render_preview()
            self.apply_button.configure(state=tk.DISABLED)
            self.status_var.set("Select a valid KiCad .pretty directory.")
            if show_error:
                messagebox.showerror("Rename Error", str(exc), parent=self.winfo_toplevel())
            return False
        return True

    def _reload_footprints(self) -> None:
        """Load footprint metadata from the target .pretty library."""

        self._all_footprints = list_kicad_footprints(self.pretty_directory)
        self._footprint_by_name = {item.name: item for item in self._all_footprints}
        if len(self._footprint_by_name) != len(self._all_footprints):
            messagebox.showwarning(
                "Duplicate Names",
                "Some footprint names are duplicated in this library; only one entry per name is editable.",
                parent=self.winfo_toplevel(),
            )
        self._refresh_visible_footprints()

    def _refresh_visible_footprints(self) -> None:
        """Refresh listbox content using current filter."""

        selected_before = set(self._selected_names())
        filter_text = self.search_var.get().strip()
        if filter_text:
            if self.filter_case_insensitive_var.get():
                needle = filter_text.lower()
                self._visible_footprints = [
                    item
                    for item in self._all_footprints
                    if needle in item.name.lower() or needle in item.file_path.name.lower()
                ]
            else:
                self._visible_footprints = [
                    item
                    for item in self._all_footprints
                    if filter_text in item.name or filter_text in item.file_path.name
                ]
        else:
            self._visible_footprints = list(self._all_footprints)

        self.footprint_list.delete(0, tk.END)
        for item in self._visible_footprints:
            self.footprint_list.insert(tk.END, item.name)

        if selected_before:
            self._set_selected_names(
                {
                    item.name
                    for item in self._visible_footprints
                    if item.name in selected_before
                }
            )
        self._refresh_preview()

    def _selected_names(self) -> list[str]:
        """Return currently selected visible footprint names."""

        selected_names: list[str] = []
        for index in self.footprint_list.curselection():
            if 0 <= index < len(self._visible_footprints):
                selected_names.append(self._visible_footprints[index].name)
        return selected_names

    def _set_selected_names(self, selected_names: set[str]) -> None:
        """Set listbox selection from a set of footprint names."""

        self._updating_selection = True
        try:
            self.footprint_list.selection_clear(0, tk.END)
            for index, item in enumerate(self._visible_footprints):
                if item.name in selected_names:
                    self.footprint_list.selection_set(index)
            if selected_names:
                first_index = min(
                    (
                        index
                        for index, item in enumerate(self._visible_footprints)
                        if item.name in selected_names
                    ),
                    default=0,
                )
                self.footprint_list.see(first_index)
        finally:
            self._updating_selection = False

    def _select_all_visible(self) -> None:
        """Select all currently visible footprints."""

        self.footprint_list.selection_set(0, tk.END)
        self._on_list_selection_changed()

    def _select_renamable(self) -> None:
        """Select only rows that can currently be renamed (status=rename)."""

        selected_names = self._selected_names()
        if not selected_names:
            return

        try:
            preview_rows = self._build_preview_rows(selected_names)
        except re.error:
            return

        renamable_names = {
            row.source_name
            for row in preview_rows
            if row.status == "rename"
        }
        self._set_selected_names(renamable_names)
        self._refresh_preview()

    def _clear_selection(self) -> None:
        """Clear listbox selection and reset inferred old pattern."""

        self.footprint_list.selection_clear(0, tk.END)
        self._on_list_selection_changed()

    def _on_list_selection_changed(self) -> None:
        """Infer old affix fields from current selection."""

        if self._updating_selection:
            return

        selected_names = self._selected_names()
        inferred_prefix, inferred_postfix = infer_affix_pattern(selected_names)
        self._updating_old_pattern = True
        try:
            self.old_prefix_var.set(inferred_prefix)
            self.old_postfix_var.set(inferred_postfix)
        finally:
            self._updating_old_pattern = False
        self._refresh_preview()

    def _on_old_pattern_changed(self) -> None:
        """Use old pattern fields to drive selection updates."""

        if self._updating_old_pattern:
            return
        if self.pattern_mode_var.get() == "affix":
            self._sync_selection_from_old_pattern()
        self._refresh_preview()

    def _on_pattern_mode_tab_changed(self, _event: tk.Event | None = None) -> None:
        """React to affix/regex mode tab changes."""

        selected_tab = self.pattern_mode_tabs.select()
        if selected_tab == str(self.affix_mode_frame):
            self.pattern_mode_var.set("affix")
            self._sync_selection_from_old_pattern()
        else:
            self.pattern_mode_var.set("regex")
        self._refresh_preview()

    def _sync_selection_from_old_pattern(self) -> None:
        """Update visible selection using old prefix/postfix in replace mode."""

        old_prefix = self.old_prefix_var.get()
        old_postfix = self.old_postfix_var.get()
        if old_prefix == "" and old_postfix == "":
            return
        matching_names = {
            item.name
            for item in self._visible_footprints
            if match_affix_pattern(item.name, old_prefix, old_postfix)
        }
        self._set_selected_names(matching_names)

    def _refresh_preview(self) -> None:
        """Recompute preview rows and apply-button state."""

        selected_names = self._selected_names()
        pattern_mode = self.pattern_mode_var.get()
        if not selected_names:
            self._preview_rows = []
            self._render_preview()
            self.apply_button.configure(state=tk.DISABLED)
            self.status_var.set("Select one or more footprints to preview rename results.")
            return

        if pattern_mode == "regex" and self.regex_search_var.get().strip() == "":
            self._preview_rows = []
            self._render_preview()
            self.apply_button.configure(state=tk.DISABLED)
            self.status_var.set("Enter a regex search pattern to preview rename operations.")
            return

        try:
            self._preview_rows = self._build_preview_rows(selected_names)
        except re.error as exc:
            self._preview_rows = []
            self._render_preview()
            self.apply_button.configure(state=tk.DISABLED)
            self.status_var.set(f"Invalid regex pattern: {exc}")
            return
        self._render_preview()

        has_rename = any(row.status == "rename" for row in self._preview_rows)
        has_blocked = any(row.status == "blocked" for row in self._preview_rows)
        rename_count = sum(row.status == "rename" for row in self._preview_rows)
        blocked_count = sum(row.status == "blocked" for row in self._preview_rows)
        if has_rename and (not has_blocked or self.allow_partial_apply_var.get()):
            self.apply_button.configure(state=tk.NORMAL)
            if has_blocked:
                self.status_var.set(
                    f"{rename_count} rename(s) ready; {blocked_count} blocked row(s) will be skipped."
                )
            else:
                self.status_var.set(f"{rename_count} rename(s) ready.")
        else:
            self.apply_button.configure(state=tk.DISABLED)
            if has_blocked:
                self.status_var.set("Resolve blocked rows before applying rename.")
            else:
                self.status_var.set("Pattern produced no effective renames.")

    def _build_preview_rows(self, selected_names: Sequence[str]) -> list[RenamePreviewRow]:
        """Compute row status for selected names under current affix/regex mode."""

        pattern_mode = self.pattern_mode_var.get()

        old_prefix = self.old_prefix_var.get()
        old_postfix = self.old_postfix_var.get()
        new_prefix = self.new_prefix_var.get()
        new_postfix = self.new_postfix_var.get()
        regex_pattern_text = self.regex_search_var.get()
        regex_replace_text = self.regex_replace_var.get()

        compiled_regex: re.Pattern[str] | None = None
        if pattern_mode == "regex":
            flags = re.IGNORECASE if self.regex_ignore_case_var.get() else 0
            compiled_regex = re.compile(regex_pattern_text, flags)

        mutable_rows: list[dict[str, str]] = []
        for source_name in sorted(selected_names, key=lambda item: item.lower()):
            transformed: str | None
            if pattern_mode == "regex":
                if compiled_regex is None or compiled_regex.search(source_name) is None:
                    transformed = None
                else:
                    transformed = compiled_regex.sub(regex_replace_text, source_name)
            else:
                transformed = apply_affix_pattern(
                    source_name,
                    old_prefix,
                    old_postfix,
                    new_prefix,
                    new_postfix,
                )
            if transformed is None:
                mutable_rows.append(
                    {
                        "source_name": source_name,
                        "target_name": source_name,
                        "status": "unchanged",
                    }
                )
                continue

            sanitized_target = sanitize_footprint_name(transformed)
            row_status: RenamePreviewStatus = "rename" if sanitized_target != source_name else "unchanged"
            mutable_rows.append(
                {
                    "source_name": source_name,
                    "target_name": sanitized_target,
                    "status": row_status,
                }
            )

        changing_rows = [row for row in mutable_rows if row["status"] == "rename"]
        collision_mode = self.collision_mode_var.get()

        if collision_mode == "dedupe":
            occupied_names = {item.name for item in self._all_footprints}
            for row in changing_rows:
                occupied_names.discard(row["source_name"])
            for row in changing_rows:
                base_target_name = row["target_name"]
                deduped_target_name = base_target_name
                suffix_number = 2
                while deduped_target_name in occupied_names:
                    deduped_target_name = f"{base_target_name}_{suffix_number}"
                    suffix_number += 1
                row["target_name"] = deduped_target_name
                occupied_names.add(deduped_target_name)

        target_counts: dict[str, int] = {}
        for row in changing_rows:
            target_counts[row["target_name"]] = target_counts.get(row["target_name"], 0) + 1

        all_names = {item.name for item in self._all_footprints}
        changing_sources = {row["source_name"] for row in changing_rows}
        names_after_removal = all_names - changing_sources

        for row in mutable_rows:
            if row["status"] != "rename":
                continue
            target_name = row["target_name"]
            if target_counts.get(target_name, 0) > 1:
                row["status"] = "blocked"
                continue
            if collision_mode != "dedupe" and target_name in names_after_removal:
                row["status"] = "blocked"

        return [
            RenamePreviewRow(
                source_name=row["source_name"],
                target_name=row["target_name"],
                status=row["status"],  # type: ignore[arg-type]
            )
            for row in mutable_rows
        ]

    def _render_preview(self) -> None:
        """Render preview rows with color-coded status."""

        self.preview_tree.delete(*self.preview_tree.get_children())
        rows_to_render = (
            self._preview_rows
            if self.show_blocked_rows_var.get()
            else [row for row in self._preview_rows if row.status != "blocked"]
        )
        for row in rows_to_render:
            self.preview_tree.insert(
                "",
                tk.END,
                values=(row.source_name, row.target_name),
                tags=(row.status,),
            )

    def _apply_rename(self) -> None:
        """Apply current non-blocked rename rows as one transaction."""

        rename_rows = [row for row in self._preview_rows if row.status == "rename"]
        if not rename_rows:
            return

        try:
            entries = self._build_transaction_entries(rename_rows)
            self._execute_transaction(entries)
        except Exception as exc:  # noqa: BLE001 - UI displays controlled error.
            messagebox.showerror("Rename Error", str(exc), parent=self.winfo_toplevel())
            self.status_var.set("Rename failed.")
            return

        self._last_transaction = entries
        self.undo_button.configure(state=tk.NORMAL)
        self._reload_footprints()
        self.status_var.set(f"Applied {len(entries)} rename(s).")

    def _build_transaction_entries(
        self,
        rename_rows: Sequence[RenamePreviewRow],
    ) -> list[RenameTransactionEntry]:
        """Build file-level transaction entries for selected rename rows."""

        entries: list[RenameTransactionEntry] = []
        for row in rename_rows:
            source_item = self._footprint_by_name.get(row.source_name)
            if source_item is None:
                raise ValueError(f"Footprint '{row.source_name}' is no longer present in the library.")

            source_path = source_item.file_path
            target_path = self.pretty_directory / f"{row.target_name}.kicad_mod"
            source_content = source_path.read_text(encoding="utf-8")
            target_content = rewrite_kicad_footprint_name(source_content, row.target_name)
            entries.append(
                RenameTransactionEntry(
                    source_name=row.source_name,
                    target_name=row.target_name,
                    source_path=source_path,
                    target_path=target_path,
                    source_content=source_content,
                    target_content=target_content,
                )
            )
        return entries

    def _execute_transaction(self, entries: Sequence[RenameTransactionEntry]) -> None:
        """Apply a rename transaction with temporary-path staging for safety."""

        temp_paths = self._stage_files_to_temp(
            [entry.source_path for entry in entries],
            suffix="apply",
        )
        written_targets: list[Path] = []
        try:
            for entry in entries:
                entry.target_path.write_text(entry.target_content, encoding="utf-8")
                written_targets.append(entry.target_path)
        except Exception:
            for target_path in written_targets:
                if target_path.exists():
                    target_path.unlink()
            for original_path, staged_path in temp_paths.items():
                if staged_path.exists():
                    staged_path.rename(original_path)
            raise
        else:
            for staged_path in temp_paths.values():
                if staged_path.exists():
                    staged_path.unlink()

    def _undo_last_rename(self) -> None:
        """Undo last applied rename transaction in this dialog session."""

        if not self._last_transaction:
            return

        target_paths = [
            entry.target_path
            for entry in self._last_transaction
            if entry.target_path.exists()
        ]
        staged_targets = self._stage_files_to_temp(target_paths, suffix="undo")

        created_sources: list[Path] = []
        try:
            for entry in self._last_transaction:
                if entry.source_path.exists():
                    entry.source_path.unlink()
                entry.source_path.write_text(entry.source_content, encoding="utf-8")
                created_sources.append(entry.source_path)
        except Exception as exc:  # noqa: BLE001 - UI displays controlled error.
            for source_path in created_sources:
                if source_path.exists():
                    source_path.unlink()
            for original_path, staged_path in staged_targets.items():
                if staged_path.exists():
                    staged_path.rename(original_path)
            messagebox.showerror("Undo Error", str(exc), parent=self.winfo_toplevel())
            self.status_var.set("Undo failed.")
            return

        for staged_path in staged_targets.values():
            if staged_path.exists():
                staged_path.unlink()

        self._last_transaction = []
        self.undo_button.configure(state=tk.DISABLED)
        self._reload_footprints()
        self.status_var.set("Undid last rename transaction.")

    def _stage_files_to_temp(self, file_paths: Sequence[Path], suffix: str) -> dict[Path, Path]:
        """Move files to temporary names and return original->temporary mapping."""

        staged_paths: dict[Path, Path] = {}
        for index, original_path in enumerate(file_paths):
            if not original_path.exists():
                continue
            temp_path = original_path.with_name(f".{original_path.name}.{suffix}.{index}.tmp")
            nonce = 0
            while temp_path.exists():
                nonce += 1
                temp_path = original_path.with_name(
                    f".{original_path.name}.{suffix}.{index}.{nonce}.tmp"
                )
            original_path.rename(temp_path)
            staged_paths[original_path] = temp_path
        return staged_paths


class EagleToKiCadApp(ttk.Frame):
    """Tkinter app for interactive package selection and import."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.master.title("Eagle ↔ KiCad Footprint Library Tools")
        self.master.geometry("1200x760")
        configure_ui_styles(master)

        self.library_path_var = tk.StringVar(
            value=str(DEFAULT_EAGLE_LIBRARY if DEFAULT_EAGLE_LIBRARY.exists() else "")
        )
        self.pretty_path_var = tk.StringVar(
            value=str(DEFAULT_KICAD_PRETTY if DEFAULT_KICAD_PRETTY.exists() else "")
        )
        self.search_var = tk.StringVar()
        self.overwrite_var = tk.BooleanVar(value=False)
        self.include_footprint_layers_var = tk.BooleanVar(value=True)
        self.include_symbol_layers_var = tk.BooleanVar(value=False)
        self.include_other_layers_var = tk.BooleanVar(value=False)
        self.layer_map_summary_var = tk.StringVar(value="Load Eagle library first")
        self.library_load_button_var = tk.StringVar(value="Load")
        self.rename_load_button_var = tk.StringVar(value="Load")
        self.status_var = tk.StringVar(
            value=(
                "Load an Eagle .lbr to import packages, "
                "or choose a KiCad .pretty and open Rename Tool."
            )
        )

        self.library: EagleLibrary | None = None
        self.packages: list[PackageInfo] = []
        self.filtered_packages: list[PackageInfo] = []
        self.eagle_layers: list[tuple[int, str]] = []
        self.layer_mapping: dict[int, list[str]] = {}
        self._active_layer_mapping_preset_path: Path | None = None
        self._package_presence_by_eagle_name: dict[str, ImportPresenceInfo] = {}
        self._package_presence_cache_key: tuple[Any, ...] | None = None
        self._updating_package_selection = False
        self._loaded_library_path: Path | None = None
        self._loaded_rename_pretty_path: Path | None = None

        self._build_ui()
        self._wire_events()
        self.pack(expand=True, fill=tk.BOTH)
        self._autoload_startup_defaults()

    def _build_ui(self) -> None:
        """Create all widgets and layout containers."""

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.main_tabs = ttk.Notebook(self)
        self.main_tabs.grid(row=0, column=0, sticky="nsew")
        self.main_tabs.bind("<<NotebookTabChanged>>", self._on_main_tab_changed)

        self.import_tab = ttk.Frame(self.main_tabs)
        self.rename_tab = ttk.Frame(self.main_tabs)
        self.main_tabs.add(self.import_tab, text="Import")
        self.main_tabs.add(self.rename_tab, text="Rename")

        self.import_tab.columnconfigure(0, weight=1)
        self.import_tab.rowconfigure(1, weight=1)
        self.rename_tab.columnconfigure(0, weight=1)
        self.rename_tab.rowconfigure(1, weight=1)

        import_path_frame = ttk.Frame(self.import_tab, padding=8)
        import_path_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        import_path_frame.columnconfigure(1, weight=1)
        ttk.Label(import_path_frame, text="Import from (source) Eagle .lbr", style="EagleHeader.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
        )
        self.library_path_entry = tk.Entry(
            import_path_frame,
            textvariable=self.library_path_var,
            bg=EAGLE_FIELD_COLOR,
            fg=EAGLE_TEXT_COLOR,
            insertbackground=EAGLE_TEXT_COLOR,
            highlightthickness=1,
            highlightbackground=EAGLE_ACCENT_COLOR,
            highlightcolor=EAGLE_ACCENT_COLOR,
        )
        self.library_path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(import_path_frame, text="Browse…", command=self._browse_library).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        self.library_load_button = ttk.Button(
            import_path_frame,
            textvariable=self.library_load_button_var,
            command=self._load_library,
        )
        self.library_load_button.grid(
            row=0,
            column=3,
            padx=(8, 0),
        )
        ttk.Label(
            import_path_frame,
            text="Import to (destination) KiCad .pretty",
            style="KiCadHeader.TLabel",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        self.pretty_path_entry = tk.Entry(
            import_path_frame,
            textvariable=self.pretty_path_var,
            bg=KICAD_FIELD_COLOR,
            fg=KICAD_TEXT_COLOR,
            insertbackground=KICAD_TEXT_COLOR,
            highlightthickness=1,
            highlightbackground=KICAD_ACCENT_COLOR,
            highlightcolor=KICAD_ACCENT_COLOR,
        )
        self.pretty_path_entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Button(import_path_frame, text="Browse…", command=self._browse_pretty).grid(
            row=1,
            column=2,
            padx=(8, 0),
            pady=(8, 0),
        )
        ttk.Label(import_path_frame, text="Layer mapping", style="KiCadHeader.TLabel").grid(
            row=2,
            column=0,
            sticky="w",
            padx=(0, 8),
            pady=(8, 0),
        )
        self.layer_map_summary_entry = ttk.Entry(
            import_path_frame,
            textvariable=self.layer_map_summary_var,
            state="readonly",
        )
        self.layer_map_summary_entry.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=(8, 0),
        )
        ttk.Button(import_path_frame, text="Layer Mapping…", command=self._open_layer_mapping_dialog).grid(
            row=2,
            column=2,
            sticky="w",
            padx=(8, 0),
            pady=(8, 0),
        )

        body = ttk.PanedWindow(self.import_tab, orient=tk.HORIZONTAL)
        body.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        body.add(left, weight=5)

        right = ttk.Frame(body)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=2)
        body.add(right, weight=4)

        ttk.Label(left, text="Eagle Package Selection", style="EagleHeader.TLabel").grid(
            row=0,
            column=0,
            sticky="w",
            pady=(0, 4),
        )

        filter_row = ttk.Frame(left)
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        filter_row.columnconfigure(1, weight=1)
        ttk.Label(filter_row, text="Filter", style="Eagle.TLabel").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(filter_row, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(filter_row, text="Select All Filtered", command=self._select_all_filtered).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(filter_row, text="Clear Selection", command=self._clear_selection).grid(row=0, column=3, padx=(8, 0))

        list_frame = ttk.Frame(left)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.package_list = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            activestyle="none",
            exportselection=False,
            background=EAGLE_FIELD_COLOR,
            foreground=EAGLE_TEXT_COLOR,
            selectbackground=EAGLE_ACCENT_COLOR,
            selectforeground="#0f2035",
        )
        self.package_list.grid(row=0, column=0, sticky="nsew")
        package_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.package_list.yview)
        package_scroll.grid(row=0, column=1, sticky="ns")
        self.package_list.configure(yscrollcommand=package_scroll.set)

        import_row = ttk.Frame(left)
        import_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(import_row, text="Import Selected", command=self._import_selected).pack(side=tk.LEFT)
        ttk.Checkbutton(
            import_row,
            text="Overwrite existing",
            variable=self.overwrite_var,
        ).pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(right, text="Eagle Package Details", style="EagleHeader.TLabel").grid(row=0, column=0, sticky="w")
        self.details_text = tk.Text(
            right,
            height=14,
            wrap="word",
            state=tk.DISABLED,
            background=EAGLE_FIELD_COLOR,
            foreground=EAGLE_TEXT_COLOR,
            insertbackground=EAGLE_TEXT_COLOR,
            selectbackground=EAGLE_ACCENT_COLOR,
            selectforeground="#0f2035",
        )
        self.details_text.grid(row=1, column=0, sticky="nsew", pady=(4, 10))

        ttk.Label(right, text="KiCad Import Log", style="KiCadHeader.TLabel").grid(row=2, column=0, sticky="w")
        self.log_text = tk.Text(
            right,
            wrap="word",
            state=tk.DISABLED,
            background=KICAD_FIELD_COLOR,
            foreground=KICAD_TEXT_COLOR,
            insertbackground=KICAD_TEXT_COLOR,
            selectbackground=KICAD_ACCENT_COLOR,
            selectforeground="#102417",
        )
        self.log_text.grid(row=3, column=0, sticky="nsew", pady=(4, 0))

        rename_path_frame = ttk.Frame(self.rename_tab, padding=8)
        rename_path_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        rename_path_frame.columnconfigure(1, weight=1)

        self.rename_pretty_path_var = tk.StringVar(value=self.pretty_path_var.get())
        ttk.Label(
            rename_path_frame,
            text="Rename footprints in KiCad .pretty",
            style="KiCadHeader.TLabel",
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 8),
        )
        self.rename_pretty_path_entry = tk.Entry(
            rename_path_frame,
            textvariable=self.rename_pretty_path_var,
            bg=KICAD_FIELD_COLOR,
            fg=KICAD_TEXT_COLOR,
            insertbackground=KICAD_TEXT_COLOR,
            highlightthickness=1,
            highlightbackground=KICAD_ACCENT_COLOR,
            highlightcolor=KICAD_ACCENT_COLOR,
        )
        self.rename_pretty_path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(rename_path_frame, text="Browse…", command=self._browse_rename_pretty).grid(
            row=0,
            column=2,
            padx=(8, 0),
        )
        self.rename_load_button = ttk.Button(
            rename_path_frame,
            textvariable=self.rename_load_button_var,
            command=self._load_rename_library,
        )
        self.rename_load_button.grid(
            row=0,
            column=3,
            padx=(8, 0),
        )

        initial_rename_directory = Path(self.rename_pretty_path_var.get()).expanduser()
        self.rename_panel = FootprintRenamePanel(self.rename_tab, initial_rename_directory)
        self.rename_panel.grid(row=1, column=0, sticky="nsew")

        status = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _wire_events(self) -> None:
        """Attach widget event handlers."""

        self.search_var.trace_add("write", lambda *_: self._refresh_package_list())
        self.pretty_path_var.trace_add("write", lambda *_: self._on_import_destination_changed())
        self.overwrite_var.trace_add("write", lambda *_: self._on_overwrite_mode_changed())
        self.library_path_var.trace_add("write", lambda *_: self._refresh_load_buttons_state())
        self.rename_pretty_path_var.trace_add("write", lambda *_: self._refresh_load_buttons_state())
        self.package_list.bind("<<ListboxSelect>>", self._on_package_selected)
        self.package_list.bind("<Control-Button-1>", self._on_package_ctrl_click)
        self.package_list.bind("<Command-Button-1>", self._on_package_ctrl_click)
        self._refresh_load_buttons_state()

    def _on_main_tab_changed(self, _event: tk.Event[ttk.Notebook]) -> None:
        """Refresh rename data silently when the Rename tab becomes active."""

        if self.main_tabs.select() == str(self.rename_tab):
            self._load_rename_library(show_error=False)

    def _sync_rename_panel_directory(self, show_error: bool) -> bool:
        """Keep embedded rename panel in sync with rename tab KiCad .pretty path."""

        destination = Path(self.rename_pretty_path_var.get()).expanduser()
        return self.rename_panel.set_pretty_directory(destination, show_error=show_error)

    def _on_import_destination_changed(self) -> None:
        """Invalidate and refresh import presence status when target .pretty path changes."""

        self._invalidate_package_presence_cache()
        if self.packages:
            self._refresh_package_list(update_status=False)

    def _on_overwrite_mode_changed(self) -> None:
        """Refresh importability behavior when overwrite mode changes."""

        if self.packages:
            self._refresh_package_list()
            self._on_package_selected(None)

    def _refresh_load_buttons_state(self) -> None:
        """Set Load/Reload labels based on whether current paths match loaded state."""

        library_path = self._path_from_string(self.library_path_var.get())
        if library_path is None:
            self.library_load_button_var.set("Load")
            self.library_load_button.configure(state=tk.DISABLED)
        else:
            if self._loaded_library_path is not None and library_path == self._loaded_library_path:
                self.library_load_button_var.set("Reload")
            else:
                self.library_load_button_var.set("Load")
            self.library_load_button.configure(state=tk.NORMAL)

        rename_path = self._path_from_string(self.rename_pretty_path_var.get())
        if rename_path is None:
            self.rename_load_button_var.set("Load")
            self.rename_load_button.configure(state=tk.DISABLED)
        else:
            if (
                self._loaded_rename_pretty_path is not None
                and rename_path == self._loaded_rename_pretty_path
            ):
                self.rename_load_button_var.set("Reload")
            else:
                self.rename_load_button_var.set("Load")
            self.rename_load_button.configure(state=tk.NORMAL)

    def _path_from_string(self, raw_path: str) -> Path | None:
        """Return expanded path from a string, or None when empty."""

        normalized = raw_path.strip()
        if not normalized:
            return None
        return Path(normalized).expanduser()

    def _invalidate_package_presence_cache(self) -> None:
        """Drop cached destination comparison status for package list rows."""

        self._package_presence_cache_key = None
        self._package_presence_by_eagle_name = {}

    def _package_presence_signature(self) -> tuple[Any, ...]:
        """Return cache signature for package-vs-destination status computation."""

        source_path = str(self.library.path) if self.library is not None else ""
        destination_path = str(self._path_from_string(self.pretty_path_var.get()) or "")
        normalized_layer_map = tuple(
            sorted(
                (
                    layer_number,
                    tuple(targets),
                )
                for layer_number, targets in self._layer_map_for_conversion().items()
            )
        )
        return (source_path, destination_path, normalized_layer_map)

    def _canonicalize_footprint_content(self, content: str) -> str:
        """Normalize KiCad footprint text for stable semantic content comparisons."""

        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        filtered_lines: list[str] = []
        for line in normalized_content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("(version ") or stripped.startswith("(generator "):
                continue
            filtered_lines.append(line.rstrip())
        normalized_content = "\n".join(filtered_lines).strip() + "\n"

        try:
            normalized_content = rewrite_kicad_footprint_name(
                normalized_content,
                "__CANONICAL_FOOTPRINT__",
            )
        except Exception:
            pass
        return normalized_content

    def _ensure_package_presence_cache(self) -> None:
        """Compute package import presence statuses when cache is stale."""

        signature = self._package_presence_signature()
        if self._package_presence_cache_key == signature and self._package_presence_by_eagle_name:
            return
        self._package_presence_by_eagle_name = self._compute_package_presence_for_destination()
        self._package_presence_cache_key = signature

    def _compute_package_presence_for_destination(self) -> dict[str, ImportPresenceInfo]:
        """Classify package list rows as imported/conflict/renamed/new for destination path."""

        if self.library is None:
            return {}

        destination_path = self._path_from_string(self.pretty_path_var.get())
        if destination_path is None or destination_path.suffix.lower() != ".pretty" or not destination_path.is_dir():
            return {
                package.eagle_name: ImportPresenceInfo(status="unknown")
                for package in self.packages
            }

        destination_content_by_name: dict[str, str] = {}
        destination_names_by_content: dict[str, set[str]] = {}
        try:
            destination_items = list_kicad_footprints(destination_path)
        except Exception:
            return {
                package.eagle_name: ImportPresenceInfo(status="unknown")
                for package in self.packages
            }

        for item in destination_items:
            try:
                content = item.file_path.read_text(encoding="utf-8")
            except OSError:
                continue
            canonical_content = self._canonicalize_footprint_content(content)
            destination_content_by_name[item.name] = canonical_content
            destination_names_by_content.setdefault(canonical_content, set()).add(item.name)

        converter = EagleToKiCadConverter(
            self.library.design_rules,
            include_dimensions=True,
            layer_map=self._layer_map_for_conversion(),
        )
        presence_by_eagle_name: dict[str, ImportPresenceInfo] = {}
        for package in self.packages:
            package_node = self.library.packages.get(package.eagle_name)
            if package_node is None:
                presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(status="unknown")
                continue

            try:
                generated_content, _warnings = converter.convert_package(
                    package_node,
                    package.eagle_name,
                )
            except Exception:
                presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(status="unknown")
                continue

            canonical_generated_content = self._canonicalize_footprint_content(generated_content)
            target_name = package.kicad_name
            destination_same_name_content = destination_content_by_name.get(target_name)
            if destination_same_name_content is not None:
                if destination_same_name_content == canonical_generated_content:
                    presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(status="imported")
                else:
                    presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(status="conflict")
                continue

            renamed_matches = sorted(destination_names_by_content.get(canonical_generated_content, set()))
            if renamed_matches:
                if len(renamed_matches) == 1:
                    detail = renamed_matches[0]
                else:
                    detail = f"{renamed_matches[0]} (+{len(renamed_matches) - 1})"
                presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(
                    status="renamed",
                    detail=detail,
                )
                continue

            presence_by_eagle_name[package.eagle_name] = ImportPresenceInfo(status="new")

        return presence_by_eagle_name

    def _presence_suffix_for_package(self, package: PackageInfo) -> str:
        """Build display suffix for one package status label in import list."""

        presence = self._package_presence_by_eagle_name.get(package.eagle_name)
        if presence is None or presence.status == "unknown":
            return ""
        if presence.status == "new":
            return ""
        if presence.status == "imported":
            return "✓"
        if presence.status == "conflict":
            return "⚠"
        if presence.status == "renamed":
            return "↺"
        return ""

    def _presence_color_for_package(self, package: PackageInfo) -> str:
        """Return list row text color for one package import presence state."""

        presence = self._package_presence_by_eagle_name.get(package.eagle_name)
        if presence is None:
            return EAGLE_TEXT_COLOR
        if presence.status == "imported":
            return "#93a3be"
        if presence.status == "conflict":
            return "#ff8c8c"
        if presence.status == "renamed":
            return "#e9cf8a"
        return EAGLE_TEXT_COLOR

    def _is_package_importable(self, package: PackageInfo) -> bool:
        """Return whether one package is currently importable under active overwrite mode."""

        presence = self._package_presence_by_eagle_name.get(package.eagle_name)
        if presence is None:
            return True
        if presence.status in {"new", "unknown"}:
            return True
        if presence.status == "conflict":
            return self.overwrite_var.get()
        return False


    def _presence_detail_for_package(self, package: PackageInfo) -> str:
        """Return a user-facing explanation of one package's destination comparison status."""

        presence = self._package_presence_by_eagle_name.get(package.eagle_name)
        if presence is None or presence.status == "unknown":
            return "Destination comparison unavailable for this package."
        if presence.status == "new":
            return "No same-name or same-content match found in destination library."
        if presence.status == "imported":
            return (
                f"Destination footprint '{package.kicad_name}' already exists and content matches exactly."
            )
        if presence.status == "conflict":
            if self.overwrite_var.get():
                return (
                    f"Destination footprint '{package.kicad_name}' exists with different content; "
                    "import will overwrite while overwrite mode is enabled."
                )
            return (
                f"Destination footprint '{package.kicad_name}' exists with different content; "
                "enable overwrite mode to replace it."
            )
        if presence.status == "renamed":
            if presence.detail:
                return (
                    f"Equivalent footprint content already exists under a different name: "
                    f"'{presence.detail}'."
                )
            return "Equivalent footprint content already exists under a different name."
        return "Destination comparison unavailable for this package."

    def _on_package_ctrl_click(self, event: tk.Event[tk.Listbox]) -> str:
        """Toggle one import row for disjoint multi-select with control/command click."""

        index = self.package_list.nearest(event.y)
        if index < 0 or index >= len(self.filtered_packages):
            return "break"

        if index in self.package_list.curselection():
            self._updating_package_selection = True
            try:
                self.package_list.selection_clear(index)
            finally:
                self._updating_package_selection = False
            self._on_package_selected(None)
            return "break"

        self._updating_package_selection = True
        try:
            self.package_list.selection_set(index)
            self.package_list.activate(index)
            self.package_list.see(index)
        finally:
            self._updating_package_selection = False
        self._on_package_selected(None)
        return "break"

    def _browse_library(self) -> None:
        """Open file picker for Eagle .lbr."""

        path = filedialog.askopenfilename(
            title="Select Eagle Library",
            filetypes=[("Eagle Library", "*.lbr"), ("All Files", "*.*")],
            initialfile=self.library_path_var.get() or None,
        )
        if path:
            self.library_path_var.set(path)

    def _browse_pretty(self) -> None:
        """Open directory picker for KiCad .pretty."""

        path = filedialog.askdirectory(
            title="Select KiCad .pretty directory",
            initialdir=self.pretty_path_var.get() or None,
        )
        if path:
            self.pretty_path_var.set(path)

    def _browse_rename_pretty(self) -> None:
        """Open directory picker for Rename tab KiCad .pretty."""

        path = filedialog.askdirectory(
            title="Select KiCad .pretty directory for rename",
            initialdir=self.rename_pretty_path_var.get() or None,
        )
        if path:
            self.rename_pretty_path_var.set(path)
            self._load_rename_library(show_error=False)

    def _load_library(self, show_error: bool = True) -> None:
        """Load Eagle library and populate package list."""
        library_path = self._path_from_string(self.library_path_var.get())
        if library_path is None:
            self._refresh_load_buttons_state()
            return

        try:
            self.library = EagleLibrary.load(library_path)
        except Exception as exc:  # noqa: BLE001 - UI presents controlled error to user.
            if show_error:
                messagebox.showerror("Load Error", str(exc))
            self.status_var.set("Failed to load Eagle library.")
            self._refresh_load_buttons_state()
            return

        self.packages = self.library.list_packages()
        self._loaded_library_path = library_path
        self._initialize_layer_mapping()
        self._restore_last_loaded_mapping_if_available()
        self._invalidate_package_presence_cache()
        self._refresh_package_list()
        self._append_log(f"Loaded {len(self.packages)} packages from {library_path}")
        self._append_log(
            f"Initialized layer mapping for {len(self.eagle_layers)} Eagle layer(s)."
        )
        self.status_var.set(
            f"Loaded {len(self.packages)} package(s) across {len(self.eagle_layers)} Eagle layer(s)."
        )
        self._refresh_load_buttons_state()

    def _load_rename_library(self, show_error: bool = True) -> None:
        """Load rename-tab KiCad .pretty path into the embedded rename panel."""
        rename_path = self._path_from_string(self.rename_pretty_path_var.get())
        if rename_path is None:
            self._refresh_load_buttons_state()
            return
        if self._sync_rename_panel_directory(show_error=show_error):
            self._loaded_rename_pretty_path = rename_path
        self._refresh_load_buttons_state()

    def _autoload_startup_defaults(self) -> None:
        """Preload libraries shown in path fields when available."""

        self._load_library(show_error=False)
        self._load_rename_library(show_error=False)

    def _initialize_layer_mapping(self) -> None:
        """Initialize per-layer mappings in manual mode (empty mappings)."""

        self.eagle_layers = self._extract_eagle_layers()
        self._active_layer_mapping_preset_path = None
        self._invalidate_package_presence_cache()
        self.layer_mapping = {
            layer_number: []
            for layer_number, _ in self.eagle_layers
        }
        self._refresh_layer_mapping_summary()

    def _apply_auto_mapping_to_all_layers(self) -> None:
        """Apply default mapping table across all discovered Eagle layers."""
        self._active_layer_mapping_preset_path = None
        self._invalidate_package_presence_cache()

        for layer_number, _layer_name in self.eagle_layers:
            self.layer_mapping[layer_number] = self._default_targets_for_layer(layer_number)

    def _extract_eagle_layers(self) -> list[tuple[int, str]]:
        """Extract known Eagle layers from the loaded library."""

        if self.library is None:
            return []

        layer_names: dict[int, str] = {}
        for layer in self.library.root.findall("./drawing/layers/layer"):
            number_text = layer.attrib.get("number")
            if number_text is None:
                continue
            try:
                number = int(number_text)
            except ValueError:
                continue
            layer_names[number] = layer.attrib.get("name", f"Layer {number}")

        if not layer_names:
            for package in self.library.packages.values():
                for primitive in package:
                    layer_text = primitive.attrib.get("layer")
                    if layer_text is None:
                        continue
                    try:
                        number = int(layer_text)
                    except ValueError:
                        continue
                    layer_names.setdefault(number, f"Layer {number}")

        return sorted(layer_names.items())

    def _default_targets_for_layer(self, layer_number: int) -> list[str]:
        """Return converter default mapping for one Eagle layer."""

        if layer_number in DEFAULT_EAGLE_LAYER_MAP:
            return [DEFAULT_EAGLE_LAYER_MAP[layer_number]]
        if 2 <= layer_number <= 15:
            return [f"In{layer_number - 1}.Cu"]
        return []

    def _layer_group(self, layer_number: int) -> str:
        """Classify Eagle layer number into footprint/symbol/other ranges."""

        if 1 <= layer_number <= 52:
            return "footprint"
        if 53 <= layer_number <= 102:
            return "symbol"
        return "other"

    def _is_layer_enabled(self, layer_number: int) -> bool:
        """Return whether a layer is currently enabled for show/process actions."""

        group = self._layer_group(layer_number)
        if group == "footprint":
            return self.include_footprint_layers_var.get()
        if group == "symbol":
            return self.include_symbol_layers_var.get()
        return self.include_other_layers_var.get()

    def _refresh_layer_mapping_summary(self) -> None:
        """Update the short status label describing mapping state."""

        if not self.eagle_layers:
            self.layer_map_summary_var.set("Load Eagle library first")
            return

        enabled_layers = [
            (layer_number, layer_name)
            for layer_number, layer_name in self.eagle_layers
            if self._is_layer_enabled(layer_number)
        ]
        if not enabled_layers:
            self.layer_map_summary_var.set("No enabled layer ranges")
            return

        mapped_layers = 0
        mapping_links = 0
        all_default = True
        for layer_number, _layer_name in enabled_layers:
            current_targets = self.layer_mapping.get(layer_number, [])
            if current_targets:
                mapped_layers += 1
                mapping_links += len(current_targets)
            if current_targets != self._default_targets_for_layer(layer_number):
                all_default = False

        if mapping_links == 0:
            self.layer_map_summary_var.set("Manual (empty)")
            return
        if self._active_layer_mapping_preset_path is not None:
            preset_label = self._active_layer_mapping_preset_path.stem
            self.layer_map_summary_var.set(
                f"Preset: {preset_label} ({mapped_layers} layer(s), {mapping_links} link(s))"
            )
            return

        if all_default:
            self.layer_map_summary_var.set(
                f"AUTO defaults ({mapped_layers} layer(s), {mapping_links} link(s))"
            )
            return

        self.layer_map_summary_var.set(
            f"Custom ({mapped_layers} layer(s), {mapping_links} link(s))"
        )

    def _open_layer_mapping_dialog(self) -> None:
        """Open a KiCad-style layer mapping editor for Eagle layer imports."""

        if self.library is None or not self.eagle_layers:
            messagebox.showwarning("No Library", "Load an Eagle .lbr file first.")
            return

        dialog = tk.Toplevel(self)
        dialog.title("Layer Mapping (Eagle → KiCad)")
        dialog.geometry("1380x560")
        dialog.transient(self.master)
        dialog.grab_set()

        container = ttk.Frame(dialog, padding=10)
        container.pack(expand=True, fill=tk.BOTH)
        container.columnconfigure(0, weight=3)
        container.columnconfigure(1, weight=4)
        container.columnconfigure(2, weight=1)
        container.columnconfigure(3, weight=4)
        container.columnconfigure(4, weight=6)
        container.rowconfigure(1, weight=1)

        ttk.Label(container, text="Eagle Layers").grid(row=0, column=0, sticky="w")
        ttk.Label(container, text="Available KiCad Layers").grid(row=0, column=1, sticky="w")
        ttk.Label(container, text="Mapped KiCad Layers").grid(row=0, column=3, sticky="w")

        overview_header = ttk.Frame(container)
        overview_header.grid(row=0, column=4, sticky="ew", padx=(8, 0))
        overview_header.columnconfigure(0, weight=1)
        ttk.Label(overview_header, text="All Destinations (KiCad <- Eagle Sources)").grid(
            row=0,
            column=0,
            sticky="w",
        )
        show_empty_destinations_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            overview_header,
            text="Show empty",
            variable=show_empty_destinations_var,
            command=lambda: _refresh_overview_list(),
        ).grid(row=0, column=1, sticky="e")

        range_controls = ttk.Frame(container)
        range_controls.grid(row=2, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(range_controls, text="Show/Process ranges:").pack(side=tk.LEFT)
        ttk.Checkbutton(
            range_controls,
            text="Footprints 1-52",
            variable=self.include_footprint_layers_var,
            command=lambda: _on_range_toggle(),
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            range_controls,
            text="Symbols 53-102",
            variable=self.include_symbol_layers_var,
            command=lambda: _on_range_toggle(),
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            range_controls,
            text="Other 103-255",
            variable=self.include_other_layers_var,
            command=lambda: _on_range_toggle(),
        ).pack(side=tk.LEFT, padx=(8, 0))

        eagle_list = tk.Listbox(
            container,
            activestyle="none",
            exportselection=False,
            font=("Menlo", 11),
        )
        eagle_list.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        eagle_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=eagle_list.yview)
        eagle_scroll.grid(row=1, column=0, sticky="nse")
        eagle_list.configure(yscrollcommand=eagle_scroll.set)

        available_list = tk.Listbox(
            container,
            font=("Menlo", 11),
        )
        available_list.grid(row=1, column=1, sticky="nsew", padx=(0, 8))
        available_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=available_list.yview)
        available_scroll.grid(row=1, column=1, sticky="nse")
        available_list.configure(yscrollcommand=available_scroll.set)

        button_column = ttk.Frame(container)
        button_column.grid(row=1, column=2, sticky="ns")

        mapped_list = tk.Listbox(
            container,
            selectmode=tk.EXTENDED,
            activestyle="none",
            exportselection=False,
            font=("Menlo", 11),
        )
        mapped_list.grid(row=1, column=3, sticky="nsew")
        mapped_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=mapped_list.yview)
        mapped_scroll.grid(row=1, column=3, sticky="nse")
        mapped_list.configure(yscrollcommand=mapped_scroll.set)

        overview_text = tk.Text(
            container,
            wrap=tk.NONE,
            state=tk.DISABLED,
            cursor="arrow",
            font=("Menlo", 11),
        )
        overview_text.grid(row=1, column=4, sticky="nsew", padx=(8, 0))
        overview_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=overview_text.yview)
        overview_scroll.grid(row=1, column=4, sticky="nse")
        overview_text.configure(yscrollcommand=overview_scroll.set)
        background_rgb = overview_text.winfo_rgb(overview_text.cget("background"))
        luminance = (
            (0.2126 * background_rgb[0])
            + (0.7152 * background_rgb[1])
            + (0.0722 * background_rgb[2])
        ) / 65535.0
        if luminance < 0.5:
            palette = {
                "destination": "#f3f3f3",
                "arrow": "#d6d6d6",
                "source": "#8fd2ff",
                "separator": "#c8c8c8",
                "empty": "#b8b8b8",
                "selected_bg": "#245a9b",
                "selected_fg": "#ffffff",
            }
        else:
            palette = {
                "destination": "#333333",
                "arrow": "#666666",
                "source": "#005ea8",
                "separator": "#767676",
                "empty": "#8a8a8a",
                "selected_bg": "#d9ebff",
                "selected_fg": "#000000",
            }
        overview_text.tag_configure("overview_destination", foreground=palette["destination"])
        overview_text.tag_configure("overview_arrow", foreground=palette["arrow"])
        overview_text.tag_configure("overview_source", foreground=palette["source"])
        overview_text.tag_configure("overview_separator", foreground=palette["separator"])
        overview_text.tag_configure("overview_empty", foreground=palette["empty"])
        overview_text.tag_configure(
            "overview_selected",
            background=palette["selected_bg"],
            foreground=palette["selected_fg"],
        )

        for layer_name in KI_LAYER_CHOICES:
            available_list.insert(tk.END, layer_name)

        layer_name_by_number = dict(self.eagle_layers)
        visible_eagle_layers: list[tuple[int, str]] = []
        overview_link_by_tag: dict[str, tuple[str, int]] = {}
        overview_selected_links: set[tuple[str, int]] = set()
        overview_tag_counter = 0
        selection_var = tk.StringVar(value="Select an Eagle layer to edit mappings.")
        action_var = tk.StringVar(
            value=(
                "Tip: choose a source layer, then use ADD/REMOVE/AUTO. "
                "In overview, click a source token to focus it; shift-click to multi-select."
            )
        )
        ttk.Label(container, textvariable=selection_var).grid(
            row=3,
            column=0,
            columnspan=5,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Label(container, textvariable=action_var).grid(
            row=4,
            column=0,
            columnspan=5,
            sticky="w",
            pady=(4, 0),
        )

        def _set_action(text: str) -> None:
            action_var.set(text)

        def _selected_layer_number() -> int | None:
            selected = eagle_list.curselection()
            if not selected:
                return None
            index = selected[0]
            if index < 0 or index >= len(visible_eagle_layers):
                return None
            return visible_eagle_layers[index][0]

        def _refresh_eagle_layer_list(preferred_layer: int | None = None) -> None:
            previous_layer = preferred_layer if preferred_layer is not None else _selected_layer_number()
            visible_eagle_layers.clear()
            visible_eagle_layers.extend(
                [
                    (layer_number, layer_name)
                    for layer_number, layer_name in self.eagle_layers
                    if self._is_layer_enabled(layer_number)
                ]
            )

            eagle_list.delete(0, tk.END)
            for layer_number, layer_name in visible_eagle_layers:
                eagle_list.insert(tk.END, f"{layer_number:>3}  {layer_name}")

            if not visible_eagle_layers:
                eagle_list.selection_clear(0, tk.END)
                _refresh_mapped_list()
                _refresh_overview_list()
                return

            selected_layer = previous_layer
            if selected_layer is None or not any(layer == selected_layer for layer, _ in visible_eagle_layers):
                selected_layer = visible_eagle_layers[0][0]

            for index, (layer_number, _layer_name) in enumerate(visible_eagle_layers):
                if layer_number != selected_layer:
                    continue
                eagle_list.selection_clear(0, tk.END)
                eagle_list.selection_set(index)
                eagle_list.see(index)
                break

            _refresh_mapped_list()
            _refresh_overview_list()

        def _refresh_mapped_list() -> None:
            mapped_list.delete(0, tk.END)
            layer_number = _selected_layer_number()
            if layer_number is None:
                selection_var.set("Select an Eagle layer to edit mappings.")
                return

            layer_name = layer_name_by_number.get(layer_number, f"Layer {layer_number}")
            selection_var.set(f"Eagle layer {layer_number} ({layer_name})")
            for target in self.layer_mapping.get(layer_number, []):
                mapped_list.insert(tk.END, target)

        def _refresh_overview_list(keep_view: bool = True) -> None:
            nonlocal overview_tag_counter

            previous_view = overview_text.yview()[0] if keep_view else 0.0
            previous_selection = set(overview_selected_links)
            overview_link_by_tag.clear()

            destination_to_sources: dict[str, list[int]] = {}
            for source_layer, targets in self.layer_mapping.items():
                if not self._is_layer_enabled(source_layer):
                    continue
                for target in targets:
                    destination_to_sources.setdefault(target, []).append(source_layer)

            destinations = list(KI_LAYER_CHOICES)
            destinations.extend(
                sorted(
                    destination
                    for destination in destination_to_sources
                    if destination not in KI_LAYER_CHOICES
                )
            )
            destination_width = max((len(destination) for destination in destinations), default=0)

            overview_text.configure(state=tk.NORMAL)
            overview_text.delete("1.0", tk.END)
            for destination in destinations:
                source_layers = sorted(set(destination_to_sources.get(destination, [])))
                if not source_layers:
                    if not show_empty_destinations_var.get():
                        continue
                    overview_text.insert(
                        tk.END,
                        f"{destination:<{destination_width}}",
                        ("overview_destination",),
                    )
                    overview_text.insert(tk.END, " <- ", ("overview_arrow",))
                    overview_text.insert(tk.END, "(none)", ("overview_empty",))
                    overview_text.insert(tk.END, "\n")
                    continue

                overview_text.insert(
                    tk.END,
                    f"{destination:<{destination_width}}",
                    ("overview_destination",),
                )
                overview_text.insert(tk.END, " <- ", ("overview_arrow",))
                for source_index, layer_number in enumerate(source_layers):
                    if source_index > 0:
                        overview_text.insert(tk.END, ", ", ("overview_separator",))
                    source_label = layer_name_by_number.get(layer_number, f"Layer {layer_number}")
                    link_tag = f"overview_link_{overview_tag_counter}"
                    overview_tag_counter += 1
                    overview_text.insert(
                        tk.END,
                        f"{layer_number}:{source_label}",
                        ("overview_source", link_tag),
                    )
                    overview_link_by_tag[link_tag] = (destination, layer_number)
                    overview_text.tag_bind(link_tag, "<Button-1>", _on_overview_link_click)
                    overview_text.tag_bind(link_tag, "<Double-Button-1>", _on_overview_link_double_click)
                overview_text.insert(tk.END, "\n")

            valid_links = set(overview_link_by_tag.values())
            overview_selected_links.clear()
            overview_selected_links.update(previous_selection & valid_links)
            _apply_overview_selection_styles()
            if keep_view:
                overview_text.yview_moveto(previous_view)
            else:
                overview_text.yview_moveto(0.0)

        def _select_eagle_layer(layer_number: int) -> bool:
            for index, (current_layer, _layer_name) in enumerate(visible_eagle_layers):
                if current_layer != layer_number:
                    continue
                eagle_list.selection_clear(0, tk.END)
                eagle_list.selection_set(index)
                eagle_list.see(index)
                _refresh_mapped_list()
                return True
            return False

        def _sync_from_overview_link(link: tuple[str, int]) -> None:
            destination, source_layer = link
            if not _select_eagle_layer(source_layer):
                return

            mapped_list.selection_clear(0, tk.END)
            for index in range(mapped_list.size()):
                if mapped_list.get(index) != destination:
                    continue
                mapped_list.selection_set(index)
                mapped_list.see(index)
                break
        def _link_from_overview_event(event: tk.Event[tk.Text]) -> tuple[str, int] | None:
            index = overview_text.index(f"@{event.x},{event.y}")
            for tag_name in reversed(overview_text.tag_names(index)):
                link = overview_link_by_tag.get(tag_name)
                if link is not None:
                    return link
            return None

        def _apply_overview_selection_styles() -> None:
            overview_text.configure(state=tk.NORMAL)
            overview_text.tag_remove("overview_selected", "1.0", tk.END)
            for tag_name, link in overview_link_by_tag.items():
                if link not in overview_selected_links:
                    continue
                tag_ranges = overview_text.tag_ranges(tag_name)
                if len(tag_ranges) < 2:
                    continue
                overview_text.tag_add("overview_selected", tag_ranges[0], tag_ranges[1])
            overview_text.tag_raise("overview_selected")
            overview_text.configure(state=tk.DISABLED)

        def _remove_selected_overview_links() -> None:
            if not overview_selected_links:
                _set_action("Select one or more overview source links to remove.")
                return

            removed_count = 0
            for destination, source_layer in sorted(
                overview_selected_links,
                key=lambda item: (item[1], item[0]),
            ):
                targets = self.layer_mapping.setdefault(source_layer, [])
                if destination not in targets:
                    continue
                targets.remove(destination)
                removed_count += 1
            if removed_count == 0:
                _set_action("No links removed from overview selection.")
                return
            self._active_layer_mapping_preset_path = None
            self._invalidate_package_presence_cache()

            _refresh_mapped_list()
            _refresh_overview_list()
            self._refresh_layer_mapping_summary()
            _set_action(f"Removed {removed_count} link(s) from overview selection.")

        def _on_overview_link_click(event: tk.Event[tk.Text]) -> str:
            link = _link_from_overview_event(event)
            if link is None:
                return "break"

            multi_select = bool(event.state & 0x0001) or bool(event.state & 0x0004)
            if multi_select:
                if link in overview_selected_links:
                    overview_selected_links.remove(link)
                else:
                    overview_selected_links.add(link)
            else:
                overview_selected_links.clear()
                overview_selected_links.add(link)

            _apply_overview_selection_styles()
            _sync_from_overview_link(link)
            return "break"

        def _on_overview_link_double_click(event: tk.Event[tk.Text]) -> str:
            link = _link_from_overview_event(event)
            if link is None:
                return "break"
            overview_selected_links.clear()
            overview_selected_links.add(link)
            _apply_overview_selection_styles()
            _remove_selected_overview_links()
            return "break"

        def _add_mapping() -> None:
            layer_number = _selected_layer_number()
            if layer_number is None:
                _set_action("Select an Eagle layer first.")
                return

            selected_targets = [available_list.get(index) for index in available_list.curselection()]
            if not selected_targets:
                _set_action("Select one or more KiCad target layers to add.")
                return

            targets = self.layer_mapping.setdefault(layer_number, [])
            added_count = 0
            added_links: set[tuple[str, int]] = set()
            for target in selected_targets:
                if target not in targets:
                    targets.append(target)
                    added_count += 1
                    added_links.add((target, layer_number))
            if added_links:
                overview_selected_links.clear()
                overview_selected_links.update(added_links)
            self._active_layer_mapping_preset_path = None
            self._invalidate_package_presence_cache()

            _refresh_mapped_list()
            _refresh_overview_list()
            self._refresh_layer_mapping_summary()
            if added_count == 0:
                _set_action("No changes: selected mapping(s) already present.")
                return
            _set_action(f"Added {added_count} mapping(s) to Eagle layer {layer_number}.")

        def _remove_mapping() -> None:
            layer_number = _selected_layer_number()
            if layer_number is None:
                _set_action("Select an Eagle layer first.")
                return

            selected_indices = sorted(mapped_list.curselection(), reverse=True)
            if not selected_indices:
                _set_action("Select one or more mapped layers to remove.")
                return

            targets = self.layer_mapping.setdefault(layer_number, [])
            removed_count = 0
            removed_links: set[tuple[str, int]] = set()
            for index in selected_indices:
                if 0 <= index < len(targets):
                    removed_links.add((targets[index], layer_number))
                    del targets[index]
                    removed_count += 1
            overview_selected_links.difference_update(removed_links)
            self._active_layer_mapping_preset_path = None
            self._invalidate_package_presence_cache()

            _refresh_mapped_list()
            _refresh_overview_list()
            self._refresh_layer_mapping_summary()
            _set_action(f"Removed {removed_count} mapping(s) from Eagle layer {layer_number}.")

        def _auto_all() -> None:
            self._apply_auto_mapping_to_all_layers()
            overview_selected_links.clear()
            _refresh_mapped_list()
            _refresh_overview_list()
            self._refresh_layer_mapping_summary()
            _set_action("AUTO applied standard mappings across all Eagle layers.")

        def _clear_all() -> None:
            for layer_number, _layer_name in self.eagle_layers:
                self.layer_mapping[layer_number] = []
            self._active_layer_mapping_preset_path = None
            self._invalidate_package_presence_cache()
            overview_selected_links.clear()
            _refresh_mapped_list()
            _refresh_overview_list()
            self._refresh_layer_mapping_summary()
            _set_action("Cleared all mappings (manual mode).")

        def _on_range_toggle() -> None:
            selected_layer = _selected_layer_number()
            self._invalidate_package_presence_cache()
            _refresh_eagle_layer_list(preferred_layer=selected_layer)
            self._refresh_layer_mapping_summary()
            _set_action("Updated visible/process layer ranges.")

        def _save_mapping_preset() -> None:
            path_text = filedialog.asksaveasfilename(
                title="Save Layer Mapping Preset",
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")],
                initialfile="eagle_to_kicad_layer_mapping.json",
            )
            if not path_text:
                return

            path = Path(path_text).expanduser()
            if not path.suffix:
                path = path.with_suffix(".json")
            try:
                self._save_layer_mapping_to_file(path)
            except Exception as exc:  # noqa: BLE001 - UI presents controlled error to user.
                messagebox.showerror("Save Mapping Error", str(exc), parent=dialog)
                return

            _set_action(f"Saved mapping preset to {path.name}.")

        def _load_mapping_preset() -> None:
            path_text = filedialog.askopenfilename(
                title="Load Layer Mapping Preset",
                filetypes=[("JSON Files", "*.json")],
            )
            if not path_text:
                return

            path = Path(path_text).expanduser()
            try:
                applied_layers, applied_links = self._load_layer_mapping_from_file(path)
            except Exception as exc:  # noqa: BLE001 - UI presents controlled error to user.
                messagebox.showerror("Load Mapping Error", str(exc), parent=dialog)
                return
            overview_selected_links.clear()

            _refresh_mapped_list()
            _refresh_overview_list()
            _set_action(
                f"Loaded mapping preset: {applied_layers} layer(s), {applied_links} mapping link(s)."
            )

        ttk.Button(button_column, text="ADD >", command=_add_mapping).pack(fill=tk.X, pady=(0, 8))
        ttk.Button(button_column, text="< Remove", command=_remove_mapping).pack(fill=tk.X, pady=(0, 8))
        ttk.Button(button_column, text="AUTO", command=_auto_all).pack(fill=tk.X, pady=(0, 8))
        ttk.Button(button_column, text="CLEAR", command=_clear_all).pack(fill=tk.X, pady=(0, 8))
        ttk.Button(button_column, text="Remove Overview Sel.", command=_remove_selected_overview_links).pack(fill=tk.X)

        footer = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        footer.pack(fill=tk.X)
        ttk.Button(footer, text="Load Mapping…", command=_load_mapping_preset).pack(side=tk.LEFT)
        ttk.Button(footer, text="Save Mapping…", command=_save_mapping_preset).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side=tk.RIGHT)

        _refresh_eagle_layer_list()
        eagle_list.bind("<<ListboxSelect>>", lambda _event: _refresh_mapped_list())

    def _refresh_package_list(self, update_status: bool = True) -> None:
        """Refresh the package list based on active filter text."""
        self._ensure_package_presence_cache()

        filter_text = self.search_var.get().strip().lower()
        if filter_text:
            self.filtered_packages = [
                package
                for package in self.packages
                if filter_text in package.eagle_name.lower()
                or filter_text in package.kicad_name.lower()
                or filter_text in package.description.lower()
            ]
        else:
            self.filtered_packages = list(self.packages)

        self.package_list.delete(0, tk.END)
        for index, package in enumerate(self.filtered_packages):
            label = package.eagle_name
            if package.kicad_name != package.eagle_name:
                label += f"  →  {package.kicad_name}"
            presence_suffix = self._presence_suffix_for_package(package)
            if presence_suffix:
                label += f"  {presence_suffix}"
            self.package_list.insert(tk.END, label)
            self.package_list.itemconfig(index, fg=self._presence_color_for_package(package))

        self._set_details_text("")
        if not update_status:
            return

        presence_counts = {
            "new": 0,
            "imported": 0,
            "conflict": 0,
            "renamed": 0,
            "unknown": 0,
        }
        for package in self.filtered_packages:
            presence = self._package_presence_by_eagle_name.get(package.eagle_name)
            if presence is None:
                continue
            if presence.status in presence_counts:
                presence_counts[presence.status] += 1
        summary = (
            "Showing "
            f"{len(self.filtered_packages)} package(s): "
            f"{presence_counts['new']} new, "
            f"{presence_counts['imported']} imported, "
            f"{presence_counts['conflict']} conflict, "
            f"{presence_counts['renamed']} renamed."
        )
        if presence_counts["unknown"] > 0:
            summary += f" {presence_counts['unknown']} unknown."
        self.status_var.set(summary)

    def _selected_packages(self) -> list[PackageInfo]:
        """Return currently selected package metadata rows."""

        indices = self.package_list.curselection()
        selected: list[PackageInfo] = []
        for index in indices:
            if 0 <= index < len(self.filtered_packages):
                selected.append(self.filtered_packages[index])
        return selected

    def _on_package_selected(self, _event: tk.Event[tk.Listbox] | None) -> None:
        """Update details panel for selected package(s)."""
        if self._updating_package_selection:
            return

        selected = self._selected_packages()
        if not selected:
            self._set_details_text("")
            return

        if len(selected) == 1:
            package = selected[0]
            primitive_lines = [
                f"- {primitive}: {count}"
                for primitive, count in sorted(package.primitive_counts.items())
            ]
            description = package.description or "(no description)"
            importable_now = self._is_package_importable(package)
            importable_text = "yes" if importable_now else "no"
            status_detail = self._presence_detail_for_package(package)
            text = (
                f"Eagle package: {package.eagle_name}\n"
                f"KiCad footprint: {package.kicad_name}\n"
                f"Importable now: {importable_text}\n\n"
                f"Import status:\n{status_detail}\n\n"
                f"Description:\n{description}\n\n"
                f"Primitive counts:\n" + "\n".join(primitive_lines)
            )
            self._set_details_text(text)
            if importable_now:
                self.status_var.set(f"Selected 1 package: {package.eagle_name}")
            else:
                self.status_var.set(
                    f"Selected 1 package (not importable): {package.eagle_name}"
                )
            return

        names = "\n".join(f"- {package.eagle_name}" for package in selected[:20])
        extra = "" if len(selected) <= 20 else f"\n... and {len(selected) - 20} more"
        text = f"{len(selected)} packages selected.\n\n{names}{extra}"
        self._set_details_text(text)
        importable_count = sum(1 for package in selected if self._is_package_importable(package))
        blocked_count = len(selected) - importable_count
        self.status_var.set(
            f"Selected {len(selected)} package(s): {importable_count} importable, {blocked_count} blocked."
        )

    def _set_details_text(self, text: str) -> None:
        """Set details panel contents."""

        self.details_text.configure(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)
        if text:
            self.details_text.insert("1.0", text)
        self.details_text.configure(state=tk.DISABLED)

    def _append_log(self, text: str) -> None:
        """Append one line to the log output area."""

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _select_all_filtered(self) -> None:
        """Select all currently visible importable rows."""
        self.package_list.selection_clear(0, tk.END)
        for index, package in enumerate(self.filtered_packages):
            if self._is_package_importable(package):
                self.package_list.selection_set(index)
        self._on_package_selected(None)

    def _clear_selection(self) -> None:
        """Clear package selection."""

        self.package_list.selection_clear(0, tk.END)
        self._set_details_text("")
        self.status_var.set("Selection cleared.")

    def _layer_map_for_conversion(self) -> dict[int, Sequence[str]]:
        """Return current GUI mapping state in converter input format."""
        return {
            layer_number: tuple(targets) if self._is_layer_enabled(layer_number) else tuple()
            for layer_number, targets in self.layer_mapping.items()
        }

    def _serialize_layer_mapping(self) -> dict[str, Any]:
        """Serialize current layer mapping for saving as a preset file."""

        return {
            "format": "eagle_to_kicad_layer_mapping_v1",
            "layers": {
                str(layer_number): list(targets)
                for layer_number, targets in sorted(self.layer_mapping.items())
                if targets
            },
        }

    def _save_layer_mapping_to_file(self, path: Path) -> None:
        """Write the current mapping to a JSON preset file."""

        payload = self._serialize_layer_mapping()
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self._active_layer_mapping_preset_path = path
        self._remember_last_loaded_mapping_preset(path)
        self._refresh_layer_mapping_summary()

    def _remember_last_loaded_mapping_preset(self, path: Path) -> None:
        """Persist path to the most recently loaded mapping preset."""
        try:
            LAST_LAYER_MAPPING_PATH_FILE.write_text(str(path), encoding="utf-8")
        except OSError:
            return

    def _read_last_loaded_mapping_preset(self) -> Path | None:
        """Read persisted mapping preset path, if available."""

        if not LAST_LAYER_MAPPING_PATH_FILE.exists():
            return None
        try:
            raw_path = LAST_LAYER_MAPPING_PATH_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw_path:
            return None
        return Path(raw_path).expanduser()

    def _restore_last_loaded_mapping_if_available(self) -> None:
        """Attempt to restore the last loaded mapping preset for current Eagle layers."""

        preset_path = self._read_last_loaded_mapping_preset()
        if preset_path is None or not preset_path.exists():
            return
        try:
            applied_layers, applied_links = self._load_layer_mapping_from_file(preset_path)
        except Exception:
            return
        self._append_log(
            f"Restored mapping preset from {preset_path} ({applied_layers} layer(s), {applied_links} link(s))."
        )

    def _load_layer_mapping_from_file(self, path: Path) -> tuple[int, int]:
        """Load layer mapping from a JSON preset file and apply to known Eagle layers."""

        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Invalid mapping file: root JSON value must be an object.")

        layers_value = raw.get("layers")
        if not isinstance(layers_value, dict):
            raise ValueError("Invalid mapping file: missing object field 'layers'.")

        known_layers = {layer_number for layer_number, _layer_name in self.eagle_layers}
        valid_targets = set(KI_LAYER_CHOICES)
        self.layer_mapping = {
            layer_number: []
            for layer_number in known_layers
        }

        applied_layers = 0
        applied_links = 0
        for raw_layer_number, raw_targets in layers_value.items():
            try:
                layer_number = int(raw_layer_number)
            except ValueError:
                continue
            if layer_number not in known_layers:
                continue
            if not isinstance(raw_targets, list):
                continue

            targets: list[str] = []
            for target in raw_targets:
                if not isinstance(target, str):
                    continue
                normalized = target.strip()
                if not normalized or normalized not in valid_targets or normalized in targets:
                    continue
                targets.append(normalized)

            self.layer_mapping[layer_number] = targets
            if targets:
                applied_layers += 1
                applied_links += len(targets)
        self._active_layer_mapping_preset_path = path
        self._remember_last_loaded_mapping_preset(path)
        self._invalidate_package_presence_cache()

        self._refresh_layer_mapping_summary()
        return applied_layers, applied_links

    def _open_rename_dialog(self) -> None:
        """Switch to embedded rename tab for the currently selected KiCad .pretty library."""
        self._load_rename_library(show_error=True)
        self.main_tabs.select(self.rename_tab)
        destination = Path(self.rename_pretty_path_var.get()).expanduser()
        if destination.name:
            self.status_var.set(f"Rename tab ready for {destination.name}.")

    def _import_selected(self) -> None:
        """Convert and write selected packages to destination .pretty library."""

        if self.library is None:
            messagebox.showwarning("No Library", "Load an Eagle .lbr file first.")
            return

        selected = [package for package in self._selected_packages() if self._is_package_importable(package)]
        if not selected:
            messagebox.showwarning(
                "No Importable Selection",
                "Select one or more importable packages to import.",
            )
            return

        try:
            destination = Path(self.pretty_path_var.get()).expanduser()
            results = convert_packages(
                library=self.library,
                package_names=[package.eagle_name for package in selected],
                destination_pretty=destination,
                overwrite=self.overwrite_var.get(),
                include_dimensions=True,
                layer_map=self._layer_map_for_conversion(),
            )
        except Exception as exc:  # noqa: BLE001 - UI presents controlled error to user.
            messagebox.showerror("Import Error", str(exc))
            self.status_var.set("Import failed.")
            return

        created = sum(1 for result in results if result.created)
        skipped = len(results) - created
        for result in results:
            self._log_result(result)
        self._invalidate_package_presence_cache()
        self._refresh_package_list(update_status=False)

        summary = f"Import complete: {created} created, {skipped} skipped."
        if skipped > 0 and not self.overwrite_var.get():
            summary += " Enable 'Overwrite existing' to replace existing footprints."
        self.status_var.set(summary)
        messagebox.showinfo("Import Complete", summary)

    def _log_result(self, result: ConversionResult) -> None:
        """Log one conversion result with warnings."""

        state = "CREATED" if result.created else "SKIPPED"
        line = f"[{state}] {result.eagle_name} -> {result.output_path.name}"
        self._append_log(line)
        for warning in result.warnings:
            self._append_log(f"  - {warning}")


def main() -> int:
    """Launch the Tk GUI application."""

    root = tk.Tk()
    root.minsize(960, 640)
    EagleToKiCadApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
