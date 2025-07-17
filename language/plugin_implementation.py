"""plugin implementation"""

import logging
import threading
import time
from dataclasses import dataclass
from functools import wraps
from typing import List, Optional

import sublime
import sublime_plugin
from sublime import HoverZone

from .constant import LOGGING_CHANNEL
from .document import TextChange, is_valid_document
from .sublime_settings import Settings

from .pyserver import get_client, get_envs_settings

LOGGER = logging.getLogger(LOGGING_CHANNEL)
CLIENT = get_client()


def setup_logger(level: int):
    """"""
    LOGGER.setLevel(level)
    fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOGGER.addHandler(sh)


def get_logging_settings():
    """get logging level defined in '*.sublime-settings'"""
    level_map = {
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "verbose": logging.DEBUG,
    }
    with Settings() as settings:
        settings_level = settings.get("logging")
        return level_map.get(settings_level, logging.ERROR)


def plugin_loaded():
    """plugin entry point"""
    setup_logger(get_logging_settings())


def plugin_unloaded():
    """executed before plugin unloaded"""
    if CLIENT:
        CLIENT.terminate()


def client_must_ready(func):
    """only call function if client is ready"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not CLIENT.is_ready():
            return None
        return func(*args, **kwargs)

    return wrapper


class InitializerEventListener(sublime_plugin.EventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    def on_activated_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        if self.client.is_ready():
            return

        self.client.start_server(get_envs_settings())
        self.client.initialize(view)

        # open active document
        for _ in range(25):
            if self.client.is_ready():
                self.client.textdocument_didopen(view)
                break
            # pause next iteration
            time.sleep(0.5)  # seconds


class DocumentSynchronizerMixins:

    def didopen(self, view: sublime.View, *, reload: bool = False):
        if not is_valid_document(view):
            return
        self.client.textdocument_didopen(view, reload=reload)

    def didsave(self, view: sublime.View):
        if not is_valid_document(view):
            return
        self.client.textdocument_didsave(view)

    def didclose(self, view: sublime.View):
        if not is_valid_document(view):
            return
        self.client.textdocument_didclose(view)

    def didchange(self, view: sublime.View, changes: List[TextChange]):
        if not is_valid_document(view):
            return
        self.client.textdocument_didchange(view, changes)


class DocumentSynchronizeEventListener(
    DocumentSynchronizerMixins, sublime_plugin.EventListener
):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def on_activated_async(self, view: sublime.View):
        self.didopen(view)

    @client_must_ready
    def on_load_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_reload_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_revert_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_post_save_async(self, view: sublime.View):
        self.didsave(view)

    @client_must_ready
    def on_close(self, view: sublime.View):
        self.didclose(view)


class DocumentSynchronizeTextChangeListener(
    DocumentSynchronizerMixins, sublime_plugin.TextChangeListener
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()
        if not is_valid_document(view):
            return
        self.didchange(view, [self.to_text_change(c) for c in changes])

    @staticmethod
    def to_text_change(change: sublime.TextChange) -> TextChange:
        """"""
        start = (change.a.row, change.a.col)
        end = (change.b.row, change.b.col)
        return TextChange(start, end, change.str, change.len_utf8)


class CompletionEventListener(sublime_plugin.EventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT
        self.prev_completion_point = 0

    @client_must_ready
    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        if not is_valid_document(view):
            return None

        point = min(locations)
        if (
            document := self.client.session.action_target.get("textDocument/completion")
        ) and document.is_completion_available():

            items = document.pop_completion()
            if (not items) or self._is_context_changed(
                view, self.prev_completion_point, point
            ):
                self.hide_completions(view)
                return None

            return sublime.CompletionList(items, flags=sublime.INHIBIT_WORD_COMPLETIONS)

        self.prev_completion_point = point

        row, col = view.rowcol(point)
        self.client.textdocument_completion(view, row, col)
        self.hide_completions(view)

        return None

    def _is_context_changed(self, view: sublime.View, old: int, new: int) -> bool:
        """check if context moved from old point"""

        # point unchanged
        if old == new:
            return False
        # point changed but still in same word
        word = view.word(old)
        if new in word and view.substr(word).isidentifier():
            return False

        return True

    def hide_completions(self, view: sublime.View):
        view.run_command("hide_auto_complete")


class HoverEventListener(sublime_plugin.EventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        if hover_zone != HoverZone.TEXT:
            return
        if not is_valid_document(view):
            return
        row, col = view.rowcol(point)
        threading.Thread(target=self._on_hover_task, args=(view, row, col)).start()

    def _on_hover_task(self, view: sublime.View, row: int, col: int):
        # Hover may be not in current active document, open it
        self.client.textdocument_didopen(view)
        self.client.textdocument_hover(view, row, col)


class DocumentSignatureHelpEventListener(sublime_plugin.EventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

        self._trigger_row = 0

    @client_must_ready
    def on_modified_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        point = view.sel()[0].begin()
        if not view.match_selector(point, "meta.function-call.arguments"):
            return

        prefix = view.substr(point - 1)
        if prefix not in {"(", ","}:
            return

        row, column = view.rowcol(point)
        self._trigger_row = row
        self.client.textdocument_signaturehelp(view, row, column)
        view.run_command("auto_complete")

    @client_must_ready
    def on_selection_modified_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        point = view.sel()[0].begin()
        row, _ = view.rowcol(point)
        if view.match_selector(point, "meta.function-call.arguments") and (
            row == self._trigger_row
        ):
            return
        view.hide_popup()


class DocumentFormattingCommandMixins:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def run(self, edit: sublime.Edit):
        if not is_valid_document(self.view):
            return
        self.client.textdocument_formatting(self.view)

    def is_visible(self):
        return is_valid_document(self.view)


class GotoDefinitionCommandMixins:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def run(
        self,
        edit: sublime.Edit,
        row: int = 0,
        column: int = 0,
        event: Optional[dict] = None,
        natural_index: bool = False,
    ):
        if not is_valid_document(self.view):
            return

        if natural_index:
            row -= 1
            column -= 1

        if event:
            text_point = event.get("text_point", -1)
            if text_point > -1:
                row, column = self.view.rowcol(text_point)

        if row < 0 or column < 0:
            raise ValueError("row or column index must > -1")

        self.client.textdocument_definition(self.view, row, column)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PrepareRenameCommandMixins:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        if not is_valid_document(self.view):
            return
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        # move cursor to point
        self.view.sel().clear()
        self.view.sel().add(point)

        start_row, start_col = self.view.rowcol(point)
        self.client.textdocument_preparerename(self.view, start_row, start_col)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class RenameCommandMixins:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    @client_must_ready
    def run(self, edit: sublime.Edit, row: int, column: int, new_name: str):
        if not is_valid_document(self.view):
            return
        self.client.textdocument_rename(self.view, row, column, new_name)

    def is_visible(self):
        return is_valid_document(self.view)


@dataclass
class _BufferedTextChange:
    region: sublime.Region
    old_text: str
    new_text: str

    __slots__ = ["region", "old_text", "new_text"]

    def offset_move(self) -> int:
        return len(self.new_text) - len(self.old_text)

    def get_moved_region(self, move: int) -> sublime.Region:
        a, b = self.region.to_tuple()
        return sublime.Region(a + move, b + move)


class ApplyTextChangesCommandMixins:
    """changes item must serialized from 'TextChange'"""

    def run(self, edit: sublime.Edit, changes: List[dict]):

        text_changes = [self.to_text_change(c) for c in changes]
        active_selection = list(self.view.sel())

        self.apply(edit, text_changes)
        self.relocate_selection(active_selection, text_changes)

    def apply(self, edit: sublime.Edit, text_changes: List[_BufferedTextChange]):
        move = 0
        for change in text_changes:
            replaced_region = change.get_moved_region(move)
            self.view.replace(edit, replaced_region, change.new_text)
            move += change.offset_move()

    def to_text_change(self, change: dict) -> _BufferedTextChange:
        text_point = self.view.text_point

        change = TextChange(**change)
        region = sublime.Region(text_point(*change.start), text_point(*change.end))
        old_text = self.view.substr(region)

        return _BufferedTextChange(region, old_text, change.text)

    def relocate_selection(
        self, selections: List[sublime.Region], changes: List[_BufferedTextChange]
    ):
        """relocate current selection following text changes"""
        moved_selections = []
        for selection in selections:
            move = sum(
                [
                    change.offset_move()
                    for change in changes
                    if change.region < selection
                ]
            )
            a, b = selection.to_tuple()
            moved_selections.append(sublime.Region(a + move, b + move))

        # we must clear current selection
        self.view.sel().clear()
        self.view.sel().add_all(moved_selections)


class TerminateCommandMixins:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    def run(self):
        if self.client:
            self.client.terminate()

    def is_visible(self):
        return self.client and self.client.is_ready()
