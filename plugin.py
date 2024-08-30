"""Python tools for Sublime Text"""

import logging
import threading
from dataclasses import dataclass
from typing import List, Optional

import sublime
import sublime_plugin
from sublime import HoverZone

from .internal.handler import BaseHandler
from .internal.constant import LOGGING_CHANNEL
from .internal.pyserver_handler import (
    is_valid_document,
    get_handler,
    get_settings_envs,
)
from .internal.workspace import TextChange


LOGGER = logging.getLogger(LOGGING_CHANNEL)
HANDLER: BaseHandler = None


def setup_logger(level: int):
    """"""
    LOGGER.setLevel(level)
    fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOGGER.addHandler(sh)


def plugin_loaded():
    """plugin entry point"""
    setup_logger(logging.ERROR)

    global HANDLER
    HANDLER = get_handler()


def plugin_unloaded():
    """executed before plugin unloaded"""
    if HANDLER:
        HANDLER.terminate()


class BaseEventListener:
    def __init__(self):
        self.prev_completion_point = 0

    def _on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        # check point in valid source
        if not (is_valid_document(view) and hover_zone == sublime.HOVER_TEXT):
            return

        row, col = view.rowcol(point)
        threading.Thread(target=self._on_hover_task, args=(view, row, col)).start()

    def _on_hover_task(self, view: sublime.View, row: int, col: int):
        if not HANDLER.is_ready():
            self._initialize_server(view)

        HANDLER.textdocument_didopen(view)
        HANDLER.textdocument_hover(view, row, col)

    def _is_completion_valid(self, view: sublime.View, point: int) -> bool:
        """is completion valid at point"""

        # point unchanged
        if point == self.prev_completion_point:
            return True
        # point changed but still in same word
        word = view.word(self.prev_completion_point)
        if view.substr(word).isidentifier() and point in word:
            return True
        return False

    def _on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        if not HANDLER.is_ready():
            return None

        point = locations[0]

        # check point in valid source
        if not is_valid_document(view):
            return None

        if (
            document := HANDLER.action_target.completion
        ) and document.is_completion_available():

            items = document.pop_completion()
            if items and self._is_completion_valid(view, point):
                return sublime.CompletionList(
                    items, flags=sublime.INHIBIT_WORD_COMPLETIONS
                )

            document.hide_completion()
            return None

        self.prev_completion_point = point
        row, col = view.rowcol(point)

        HANDLER.textdocument_completion(view, row, col)
        view.run_command("hide_auto_complete")

        sublime.set_timeout_async(
            self._trigger_signaturehelp(view, point, row, col), 0.5
        )
        return None

    def _trigger_signaturehelp(
        self, view: sublime.View, point: int, row: int, col: int
    ):
        # Some times server response signaturehelp after cursor moved.
        view.hide_popup()

        # Only request signature on function arguments
        if not view.match_selector(point, "meta.function-call.arguments"):
            return

        HANDLER.textdocument_signaturehelp(view, row, col)

    def _initialize_server(self, view: sublime.View):
        """initialize server"""
        HANDLER.run_server(get_settings_envs())
        HANDLER.initialize(view)

    def _on_activated_async(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view)
            return

        if LOGGER.level == logging.DEBUG:
            return

        # initialize server
        self._initialize_server(view)
        HANDLER.textdocument_didopen(view)

    def _on_post_save_async(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didsave(view)

    def _on_close(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didclose(view)

    def _on_load(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def _on_reload(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def _on_revert(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)


class TextChangeListener:
    def _on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()

        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didchange(
                view, [self.to_text_change(c) for c in changes]
            )

    @staticmethod
    def to_text_change(change: sublime.TextChange) -> TextChange:
        """"""
        start = (change.a.row, change.a.col)
        end = (change.b.row, change.b.col)
        return TextChange(start, end, change.str)


class DocumentFormattingCommand:
    def _run(self, edit: sublime.Edit):
        if HANDLER.is_ready():
            HANDLER.textdocument_formatting(self.view)


class GotoDefinitionCommand:
    def _run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_definition(self.view, start_row, start_col)


class RenameCommand:
    def _run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            # move cursor to point
            self.view.sel().clear()
            self.view.sel().add(point)

            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_preparerename(self.view, start_row, start_col)


@dataclass
class _BufferedTextChange:
    """"""

    region: sublime.Region
    new_text: str
    cursor_move: int = 0

    def moved_region(self, move: int) -> sublime.Region:
        return sublime.Region(self.region.a + move, self.region.b + move)


class ApplyTextChangesCommand:
    """changes item must serialized from 'TextChange'"""

    def _run(self, edit: sublime.Edit, changes: List[dict]):
        text_changes = [self.to_text_change(self.view, c) for c in changes]
        active_selection = list(self.view.sel())

        self.apply(edit, text_changes)
        self.relocate_selection(active_selection, text_changes)

    @staticmethod
    def to_text_change(view: sublime.View, change: dict) -> _BufferedTextChange:
        change = TextChange(**change)
        start_point = view.text_point(*change.start)
        end_point = view.text_point(*change.end)

        region = sublime.Region(start_point, end_point)
        cursor_move = len(change.text) - region.size()

        return _BufferedTextChange(region, change.text, cursor_move)

    def apply(self, edit: sublime.Edit, text_changes: List[_BufferedTextChange]):
        cursor_move = 0
        for change in text_changes:
            replaced_region = change.moved_region(cursor_move)
            self.view.erase(edit, replaced_region)
            self.view.insert(edit, replaced_region.a, change.new_text)
            cursor_move += change.cursor_move

    def relocate_selection(
        self, selections: List[sublime.Region], changes: List[_BufferedTextChange]
    ):
        """relocate current selection following text changes"""
        moved_selections = []
        for selection in selections:
            temp_selection = selection
            for change in changes:
                if temp_selection.begin() > change.region.begin():
                    temp_selection.a += change.cursor_move
                    temp_selection.b += change.cursor_move

            moved_selections.append(temp_selection)

        # we must clear current selection
        self.view.sel().clear()
        self.view.sel().add_all(moved_selections)


class PythontoolsApplyTextChangesCommand(
    sublime_plugin.TextCommand, ApplyTextChangesCommand
):
    """changes item must serialized from 'TextChange'"""

    def run(self, edit: sublime.Edit, changes: List[dict]):
        self._run(edit, changes)


class PythontoolsEventListener(sublime_plugin.EventListener, BaseEventListener):
    def __init__(self):
        super().__init__()

    def on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        self._on_hover(view, point, hover_zone)

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        return self._on_query_completions(view, prefix, locations)

    def on_activated_async(self, view: sublime.View):
        self._on_activated_async(view)

    def on_post_save_async(self, view: sublime.View):
        self._on_post_save_async(view)

    def on_close(self, view: sublime.View):
        self._on_close(view)

    def on_load(self, view: sublime.View):
        self._on_load(view)

    def on_reload(self, view: sublime.View):
        self._on_reload(view)

    def on_revert(self, view: sublime.View):
        self._on_revert(view)


class PythontoolsTextChangeListener(
    sublime_plugin.TextChangeListener, TextChangeListener
):
    def on_text_changed(self, changes: List[sublime.TextChange]):
        self._on_text_changed(changes)


class PythontoolsDocumentFormattingCommand(
    sublime_plugin.TextCommand, DocumentFormattingCommand
):
    def run(self, edit: sublime.Edit):
        self._run(edit)

    def is_visible(self):
        return is_valid_document(self.view)


class PythontoolsGotoDefinitionCommand(
    sublime_plugin.TextCommand, GotoDefinitionCommand
):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        self._run(edit, event)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PythontoolsRenameCommand(sublime_plugin.TextCommand, RenameCommand):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        self._run(edit, event)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PythontoolsTerminateCommand(sublime_plugin.WindowCommand):
    def run(self):
        if HANDLER:
            HANDLER.terminate()

    def is_visible(self):
        return HANDLER and HANDLER.is_ready()
