"""Python tools for Sublime Text"""

import logging
from typing import List, Optional

import sublime
import sublime_plugin
from sublime import HoverZone

from .command_event_base import (
    BaseEventListener,
    BaseTextChangeListener,
    BaseApplyTextChangesCommand,
    BaseDocumentFormattingCommand,
    BaseGotoDefinitionCommand,
    BaseRenameCommand,
)
from .internal.handler import BaseHandler
from .internal.constant import LOGGING_CHANNEL
from .internal.pyserver_handler import is_valid_document, get_handler


LOGGER = logging.getLogger(LOGGING_CHANNEL)
HANDLER: BaseHandler = get_handler()


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


def plugin_unloaded():
    """executed before plugin unloaded"""
    if HANDLER:
        HANDLER.terminate()


class PythontoolsApplyTextChangesCommand(
    sublime_plugin.TextCommand, BaseApplyTextChangesCommand
):
    """changes item must serialized from 'TextChange'"""

    def run(self, edit: sublime.Edit, changes: List[dict]):
        self._run(edit, changes)


class PythontoolsEventListener(sublime_plugin.EventListener, BaseEventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

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
    sublime_plugin.TextChangeListener, BaseTextChangeListener
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_text_changed(self, changes: List[sublime.TextChange]):
        self._on_text_changed(changes)


class PythontoolsDocumentFormattingCommand(
    sublime_plugin.TextCommand, BaseDocumentFormattingCommand
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self, edit: sublime.Edit):
        self._run(edit)

    def is_visible(self):
        return is_valid_document(self.view)


class PythontoolsGotoDefinitionCommand(
    sublime_plugin.TextCommand, BaseGotoDefinitionCommand
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        self._run(edit, event)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PythontoolsRenameCommand(sublime_plugin.TextCommand, BaseRenameCommand):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        self._run(edit, event)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class PythontoolsTerminateCommand(sublime_plugin.WindowCommand):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self):
        if self.handler:
            self.handler.terminate()

    def is_visible(self):
        return self.handler and self.handler.is_ready()
