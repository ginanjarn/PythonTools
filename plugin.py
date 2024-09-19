"""Python tools for Sublime Text"""

import logging
from typing import List, Optional

import sublime
import sublime_plugin
from sublime import HoverZone

from .internal.constant import LOGGING_CHANNEL
from .internal.command_event_base import (
    BaseOpenEventListener,
    BaseSaveEventListener,
    BaseCloseEventListener,
    BaseHoverEventListener,
    BaseCompletionEventListener,
    BaseTextChangeListener,
    BaseApplyTextChangesCommand,
    BaseDocumentSignatureHelpCommand,
    BaseDocumentFormattingCommand,
    BaseGotoDefinitionCommand,
    BasePrepareRenameCommand,
    BaseRenameCommand,
)
from .internal.handler import BaseHandler
from .internal.pyserver_handler import get_handler, is_valid_document
from .internal.sublime_settings import Settings


LOGGER = logging.getLogger(LOGGING_CHANNEL)
HANDLER: BaseHandler = get_handler()


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
    if HANDLER:
        HANDLER.terminate()


class PythontoolsOpenEventListener(sublime_plugin.EventListener, BaseOpenEventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_activated_async(self, view: sublime.View):
        self._on_activated_async(view)

    def on_load(self, view: sublime.View):
        self._on_load(view)

    def on_reload(self, view: sublime.View):
        self._on_reload(view)

    def on_revert(self, view: sublime.View):
        self._on_revert(view)


class PythontoolsSaveEventListener(sublime_plugin.EventListener, BaseSaveEventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_post_save_async(self, view: sublime.View):
        self._on_post_save_async(view)


class PythontoolsCloseEventListener(
    sublime_plugin.EventListener, BaseCloseEventListener
):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_close(self, view: sublime.View):
        self._on_close(view)


class PythontoolsTextChangeListener(
    sublime_plugin.TextChangeListener, BaseTextChangeListener
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_text_changed(self, changes: List[sublime.TextChange]):
        self._on_text_changed(changes)


class PythontoolsCompletionEventListener(
    sublime_plugin.EventListener, BaseCompletionEventListener
):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        return self._on_query_completions(view, prefix, locations)


class PythontoolsHoverEventListener(
    sublime_plugin.EventListener, BaseHoverEventListener
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        self._on_hover(view, point, hover_zone)


class PythontoolsDocumentSignatureHelpCommand(
    sublime_plugin.TextCommand, BaseDocumentSignatureHelpCommand
):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self, edit: sublime.Edit, point: int):
        self._run(edit, point)

    def is_visible(self):
        return is_valid_document(self.view)


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


class PythontoolsPrepareRenameCommand(
    sublime_plugin.TextCommand, BasePrepareRenameCommand
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

    def run(self, edit: sublime.Edit, row: int, column: int, new_name: str):
        self._run(edit, row, column, new_name)

    def is_visible(self):
        return is_valid_document(self.view)


class PythontoolsApplyTextChangesCommand(
    sublime_plugin.TextCommand, BaseApplyTextChangesCommand
):
    """changes item must serialized from 'TextChange'"""

    def run(self, edit: sublime.Edit, changes: List[dict]):
        self._run(edit, changes)


class PythontoolsTerminateCommand(sublime_plugin.WindowCommand):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.handler = HANDLER

    def run(self):
        if self.handler:
            self.handler.terminate()

    def is_visible(self):
        return self.handler and self.handler.is_ready()
