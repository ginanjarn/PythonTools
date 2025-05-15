"""plugin implementation"""

import logging
import threading
from typing import List, Optional

import sublime
from sublime import HoverZone

from .constant import LOGGING_CHANNEL, COMMAND_PREFIX
from .document import TextChange
from .pyserver import PyserverClient, get_envs_settings

LOGGER = logging.getLogger(LOGGING_CHANNEL)


def initialize_server(client: PyserverClient, view: sublime.View):
    """initialize server"""
    client.start_server(get_envs_settings())
    client.initialize(view)


class OpenEventListener:

    def __init__(self, *args, **kwargs):
        self.client: PyserverClient
        self.prev_completion_point = 0

    def _on_activated_async(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didopen(view)
            return

        if LOGGER.level == logging.DEBUG:
            return

        # initialize server
        initialize_server(self.client, view)
        self.client.textdocument_didopen(view)

    def _on_load(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didopen(view, reload=True)

    def _on_reload(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didopen(view, reload=True)

    def _on_revert(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didopen(view, reload=True)


class SaveEventListener:

    def __init__(self, *args, **kwargs):
        self.client: PyserverClient
        self.prev_completion_point = 0

    def _on_post_save_async(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didsave(view)


class CloseEventListener:

    def __init__(self, *args, **kwargs):
        self.client: PyserverClient
        self.prev_completion_point = 0

    def _on_close(self, view: sublime.View):
        if self.client.is_ready():
            self.client.textdocument_didclose(view)


class TextChangeListener:

    def __init__(self, *args, **kwargs):
        self.buffer: sublime.Buffer
        self.client: PyserverClient

    def _on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()
        if self.client.is_ready():
            self.client.textdocument_didchange(
                view, [self.to_text_change(c) for c in changes]
            )

    @staticmethod
    def to_text_change(change: sublime.TextChange) -> TextChange:
        """"""
        start = (change.a.row, change.a.col)
        end = (change.b.row, change.b.col)
        return TextChange(start, end, change.str, change.len_utf8)


class CompletionEventListener:

    def __init__(self, *args, **kwargs):
        self.client: PyserverClient
        self.prev_completion_point = 0

    def _is_context_changed(self, view: sublime.View, old: int, new: int) -> bool:
        """check if context moved from old point"""

        # point unchanged
        if old == new:
            return False
        # point changed but still in same word
        word = view.word(old)
        if view.substr(word).isidentifier() and new in word:
            return False
        return True

    def _on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        if not self.client.is_ready():
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

    def hide_completions(self, view: sublime.View):
        view.run_command("hide_auto_complete")


class HoverEventListener:

    def __init__(self, *args, **kwargs):
        self.client: PyserverClient

    def _on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        row, col = view.rowcol(point)
        threading.Thread(target=self._on_hover_task, args=(view, row, col)).start()

    def _on_hover_task(self, view: sublime.View, row: int, col: int):
        if not self.client.is_ready():
            initialize_server(self.client, view)

        self.client.textdocument_didopen(view)
        self.client.textdocument_hover(view, row, col)


class DocumentSignatureHelpEventListener:

    prev_word = sublime.Region(0)
    prev_count = 0

    def __init__(self, *args, **kwargs):
        self.view: sublime.View
        self.client: PyserverClient

    def _on_selection_modified(self, view: sublime.View):
        if self.client.is_ready():
            point = view.sel()[0].begin()

            # Only request signature on function arguments
            if not view.match_selector(point, "meta.function-call.arguments"):
                view.hide_popup()
                return

            # Only trigger on content modification
            new_count = view.change_count()
            if self.prev_count == new_count:
                return
            self.prev_count = new_count

            # Keep current visible signature
            current_word = view.word(point)
            if view.is_popup_visible() and current_word.intersects(self.prev_word):
                return
            self.prev_word = current_word

            row, col = view.rowcol(point)
            self.client.textdocument_signaturehelp(view, row, col)


class DocumentFormattingCommand:

    def __init__(self, *args, **kwargs):
        self.view: sublime.View
        self.client: PyserverClient

    def _run(self, edit: sublime.Edit):
        if self.client.is_ready():
            self.client.textdocument_formatting(self.view)


class GotoDefinitionCommand:

    def __init__(self, *args, **kwargs):
        self.view: sublime.View
        self.client: PyserverClient

    def _run(
        self,
        edit: sublime.Edit,
        row: int = 0,
        column: int = 0,
        event: Optional[dict] = None,
        natural_index: bool = False,
    ):

        if natural_index:
            row -= 1
            column -= 1

        if event:
            text_point = event.get("text_point", -1)
            if text_point > -1:
                row, column = self.view.rowcol(text_point)

        if row < 0 or column < 0:
            raise ValueError("row or column index must > -1")

        if self.client.is_ready():
            self.client.textdocument_definition(self.view, row, column)


class PrepareRenameCommand:

    def __init__(self, *args, **kwargs):
        self.view: sublime.View
        self.client: PyserverClient

    def _run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if self.client.is_ready():
            # move cursor to point
            self.view.sel().clear()
            self.view.sel().add(point)

            start_row, start_col = self.view.rowcol(point)
            self.client.textdocument_preparerename(self.view, start_row, start_col)


class RenameCommand:

    def __init__(self, *args, **kwargs):
        self.view: sublime.View
        self.client: PyserverClient

    def _run(self, edit: sublime.Edit, row: int, column: int, new_name: str):
        if self.client.is_ready():
            self.client.textdocument_rename(self.view, row, column, new_name)


class _BufferedTextChange:
    __slots__ = ["region", "old_text", "new_text"]

    def __init__(self, region: sublime.Region, old_text: str, new_text: str) -> None:
        self.region = region
        self.old_text = old_text
        self.new_text = new_text

    def offset_move(self) -> int:
        return len(self.new_text) - len(self.old_text)

    def get_moved_region(self, move: int) -> sublime.Region:
        return sublime.Region(self.region.a + move, self.region.b + move)


class ApplyTextChangesCommand:
    """changes item must serialized from 'TextChange'"""

    def __init__(self, *args, **kwargs):
        self.view: sublime.View

    def _run(self, edit: sublime.Edit, changes: List[dict]):
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
        change = TextChange(**change)

        start = self.view.text_point(*change.start)
        end = self.view.text_point(*change.end)
        region = sublime.Region(start, end)
        old_text = self.view.substr(region)

        return _BufferedTextChange(region, old_text, change.text)

    def relocate_selection(
        self, selections: List[sublime.Region], changes: List[_BufferedTextChange]
    ):
        """relocate current selection following text changes"""
        moved_selections = []
        for selection in selections:
            move = 0
            for change in changes:
                changed_region = change.region
                if changed_region.begin() < selection.begin():
                    move += change.offset_move()

            moved = sublime.Region(selection.a + move, selection.b + move)
            moved_selections.append(moved)

        # we must clear current selection
        self.view.sel().clear()
        self.view.sel().add_all(moved_selections)
