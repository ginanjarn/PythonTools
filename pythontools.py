"""Python tools for Sublime Text"""

import logging
import threading
from typing import List, Optional

import sublime
import sublime_plugin
from sublime import HoverZone

from .handler import BaseHandler
from .pyserver_handler import (
    LOGGING_CHANNEL,
    is_valid_document,
    get_handler,
    get_settings_envs,
)


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


class EventListener(sublime_plugin.EventListener):
    def __init__(self):
        self.prev_completion_point = 0

    def on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        # check point in valid source
        if not (is_valid_document(view) and hover_zone == sublime.HOVER_TEXT):
            return

        row, col = view.rowcol(point)
        if HANDLER.is_ready():
            HANDLER.textdocument_hover(view, row, col)

        else:
            threading.Thread(target=self._on_hover, args=(view, row, col)).start()

    def _on_hover(self, view, row, col):

        # initialize server if not ready
        if not HANDLER.is_ready():
            HANDLER.run_server(get_settings_envs())
            HANDLER.initialize(view)

        # on multi column layout, sometime we hover on other document which may
        # not loaded yet
        HANDLER.textdocument_didopen(view)
        # request on hover
        HANDLER.textdocument_hover(view, row, col)

    def on_query_completions(
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
            word = view.word(self.prev_completion_point)
            # point unchanged
            if point == self.prev_completion_point:
                show = True
            # point changed but still in same word
            elif view.substr(word).isidentifier() and point in word:
                show = True
            else:
                show = False

            if (cache := document.pop_completion()) and show:
                return sublime.CompletionList(
                    cache, flags=sublime.INHIBIT_WORD_COMPLETIONS
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

    def on_activated_async(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view)
            return

        if LOGGER.level == logging.DEBUG:
            return

        # initialize server
        HANDLER.run_server(get_settings_envs())
        HANDLER.initialize(view)
        HANDLER.textdocument_didopen(view)

    def on_post_save_async(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didsave(view)

    def on_close(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didclose(view)

    def on_load(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def on_reload(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def on_revert(self, view: sublime.View):
        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()

        # check point in valid source
        if not is_valid_document(view):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didchange(
                view, [self.change_as_rpc(c) for c in changes]
            )

    @staticmethod
    def change_as_rpc(change: sublime.TextChange) -> dict:
        start = change.a
        end = change.b
        return {
            "range": {
                "end": {"character": end.col, "line": end.row},
                "start": {"character": start.col, "line": start.row},
            },
            "rangeLength": change.len_utf8,
            "text": change.str,
        }


class PythontoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        if HANDLER.is_ready():
            HANDLER.textdocument_formatting(self.view)

    def is_visible(self):
        return is_valid_document(self.view)


class PythontoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_definition(self.view, start_row, start_col)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PythontoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            # move cursor to point
            self.view.sel().clear()
            self.view.sel().add(point)

            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_preparerename(self.view, start_row, start_col)

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
