"""diagnostics object"""

import threading
from collections import namedtuple, defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Callable

import sublime

from ..constant import PACKAGE_NAME, COMMAND_PREFIX
from .document import TextChange

PathStr = str
LineCharacter = namedtuple("LineCharacter", ["line", "character"])


@dataclass
class DiagnosticItem:
    severity: int
    region: sublime.Region
    message: str

    @classmethod
    def from_rpc(cls, view: sublime.View, diagnostic: dict, /):
        """"""
        start = LineCharacter(**diagnostic["range"]["start"])
        end = LineCharacter(**diagnostic["range"]["end"])
        region = sublime.Region(view.text_point(*start), view.text_point(*end))
        message = diagnostic["message"]
        if source := diagnostic.get("source"):
            message = f"{message} ({source})"

        return cls(diagnostic["severity"], region, message)


@dataclass
class ReportSettings:
    highlight_text: bool = True
    show_status: bool = True
    show_panel: bool = False


class DiagnosticManager:
    """"""

    REGIONS_KEY = f"{PACKAGE_NAME}_DIAGNOSTIC_REGIONS"
    STATUS_KEY = f"{PACKAGE_NAME}_DIAGNOSTIC_STATUS"

    def __init__(self, settings: ReportSettings = None) -> None:
        # One file may be openend in many View. For efficiency reason,
        # only show report such text highlight on active view.

        # Store raw diagnostic items to use later in CodeAction.
        self.raw_itams_map: Dict[PathStr, List[dict]] = defaultdict(list)
        self.items_map: Dict[PathStr, List[DiagnosticItem]] = defaultdict(list)

        self.settings = settings or ReportSettings()
        self.panel = DiagnosticPanel()

        self._change_lock = threading.Lock()
        self._active_view: sublime.View = None

    def reset(self):
        # clear all regions before diagnostics_map cleared
        self._clear_all_regions()
        self._clear_all_status()
        self._active_view = None
        self.panel.destroy()
        self.raw_itams_map.clear()
        self.items_map.clear()

    def get_raw(self, view: sublime.View) -> List[dict]:
        """return raw dict of diagnostic items"""
        with self._change_lock:
            return self.raw_itams_map[view.file_name()]

    def get(
        self,
        view: sublime.View,
        filter_fn: Callable[[DiagnosticItem], bool] = None,
    ) -> List[DiagnosticItem]:
        """return diagnostic item"""
        with self._change_lock:
            if not filter_fn:
                return self.items_map[view.file_name()]
            return [d for d in self.items_map[view.file_name()] if filter_fn(d)]

    def add(self, view: sublime.View, diagnostics: List[dict]):
        """add diagnostics"""
        with self._change_lock:
            self.raw_itams_map[view.file_name()] = diagnostics
            self.items_map[view.file_name()] = [
                DiagnosticItem.from_rpc(view, d) for d in diagnostics
            ]
            self._show_report(view)

    def remove(self, view: sublime.View):
        """remove diagnostics"""
        with self._change_lock:
            try:
                del self.raw_itams_map[view.file_name()]
                del self.items_map[view.file_name()]
            except KeyError:
                pass
            self._show_report(view)

    def set_active_view(self, view: sublime.View):
        """set active view"""
        if view == self._active_view:
            # active view not changed
            return

        self._active_view = view
        self._show_report(view)

    def _show_report(self, view: sublime.View):
        if view != self._active_view:
            # cancel show report
            return

        diagnostic_items = self.items_map[view.file_name()]

        if self.settings.highlight_text:
            self._highlight_regions(view, diagnostic_items)
        if self.settings.show_status:
            self._show_status(view, diagnostic_items)
        if self.settings.show_panel:
            self._show_panel(self.panel, view, diagnostic_items)

    @classmethod
    def _highlight_regions(
        cls, view: sublime.View, diagnostic_items: List[DiagnosticItem]
    ):
        regions = [item.region for item in diagnostic_items]
        view.add_regions(
            key=cls.REGIONS_KEY,
            regions=regions,
            scope="string",
            icon="dot",
            flags=sublime.DRAW_NO_FILL
            | sublime.DRAW_NO_OUTLINE
            | sublime.DRAW_SQUIGGLY_UNDERLINE,
        )

    @classmethod
    def _clear_all_regions(cls):
        for window in sublime.windows():
            # erase regions
            for view in [v for v in window.views()]:
                view.erase_regions(cls.REGIONS_KEY)

    @classmethod
    def _clear_all_status(cls):
        for window in sublime.windows():
            # erase regions
            for view in [v for v in window.views()]:
                view.erase_status(cls.STATUS_KEY)

    @classmethod
    def _show_status(cls, view: sublime.View, diagnostic_items: List[DiagnosticItem]):
        err_count = len([item for item in diagnostic_items if item.severity == 1])
        warn_count = len([item for item in diagnostic_items if item.severity == 2])
        view.set_status(cls.STATUS_KEY, f"Errors {err_count}, Warnings {warn_count}")

    @staticmethod
    def _show_panel(
        panel: "DiagnosticPanel",
        view: sublime.View,
        diagnostic_items: List[DiagnosticItem],
    ):
        def build_line(view: sublime.View, item: DiagnosticItem):
            short_name = Path(view.file_name()).name
            row, col = view.rowcol(item.region.begin())
            return f"{short_name}:{row+1}:{col} {item.message}"

        content = "\n".join([build_line(view, item) for item in diagnostic_items])
        panel.set_content(content)
        panel.show()


class DiagnosticPanel:
    OUTPUT_PANEL_NAME = f"{PACKAGE_NAME}_PANEL"
    SETTINGS = {"gutter": False, "word_wrap": False}

    def __init__(self):
        self.panel: sublime.View = None

    def _create_panel(self):
        self.panel = sublime.active_window().create_output_panel(self.OUTPUT_PANEL_NAME)
        self.panel.settings().update(self.SETTINGS)
        self.panel.set_read_only(False)

    def set_content(self, text: str):
        if not (self.panel and self.panel.is_valid()):
            self._create_panel()

        start = (0, 0)
        end = self.panel.rowcol(self.panel.size())

        change = TextChange(start, end, text, -1)
        self.panel.run_command(
            f"{COMMAND_PREFIX}_apply_text_changes",
            {"changes": [asdict(change)]},
        )

    def show(self) -> None:
        """show output panel"""
        sublime.active_window().run_command(
            "show_panel", {"panel": f"output.{self.OUTPUT_PANEL_NAME}"}
        )

    def destroy(self):
        """destroy output panel"""
        for window in sublime.windows():
            window.destroy_output_panel(self.OUTPUT_PANEL_NAME)
