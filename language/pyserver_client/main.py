"""plugin entry point"""

import logging
import time
from functools import wraps
from typing import TYPE_CHECKING

import sublime
import sublime_plugin

if TYPE_CHECKING:
    from sublime_types import CommandArgs

from ..constant import LOGGING_CHANNEL, COMMAND_PREFIX
from ..plugin_core.document import is_valid_document
from ..plugin_core.sublime_settings import Settings
from ..plugin_core.features.document_helper import _ApplyTextChangesCommand
from .client import get_client, get_envs_settings

from ..plugin_core.features.server_manager import (
    _StartServerCommand,
    _TerminateServerCommand,
)
from ..plugin_core.features.initializer import _InitializeCommand
from ..plugin_core.features.document.synchronizer import (
    DocumentSynchronizeEventListener,
    DocumentSynchronizeTextChangeListener,
)
from ..plugin_core.features.document.completion import CompletionEventListener
from ..plugin_core.features.document.signature_help import (
    _DocumentSignatureHelpCommand,
    DocumentSignatureHelpEventListener,
)
from ..plugin_core.features.document.hover import HoverEventListener
from ..plugin_core.features.document.formatting import _DocumentFormattingCommand
from ..plugin_core.features.document.definition import _GotoDefinitionCommand
from ..plugin_core.features.document.rename import (
    _PrepareRenameCommand,
    _RenameCommand,
)

LOGGER = logging.getLogger(LOGGING_CHANNEL)
CLIENT = get_client()

_InitializeCommand.client = CLIENT
_StartServerCommand.client = CLIENT
_TerminateServerCommand.client = CLIENT
DocumentSynchronizeEventListener.client = CLIENT
DocumentSynchronizeTextChangeListener.client = CLIENT
CompletionEventListener.client = CLIENT
_DocumentSignatureHelpCommand.client = CLIENT
DocumentSignatureHelpEventListener.client = CLIENT
HoverEventListener.client = CLIENT
_DocumentFormattingCommand.client = CLIENT
_GotoDefinitionCommand.client = CLIENT
_PrepareRenameCommand.client = CLIENT
_RenameCommand.client = CLIENT


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


class ContextMenuEventListener(sublime_plugin.EventListener):

    @client_must_ready
    def on_post_text_command(
        self, view: sublime.View, command_name: str, args: "CommandArgs"
    ):
        # Move cursor on context metnu triggered position
        if command_name != "context_menu":
            return
        # clear current selections
        view.sel().clear()
        point = view.window_to_text((args["event"]["x"], args["event"]["y"]))
        view.sel().add(point)


class InitializerEventListener(sublime_plugin.EventListener):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = CLIENT

    def on_activated_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        if self.client.is_ready():
            return

        if not self.client.server.is_running():
            view.run_command(
                f"{COMMAND_PREFIX}_start_server",
                {"envs": get_envs_settings()},
            )

        # initialize
        for _ in range(25):
            if self.client.server.is_running():
                self.client.initialize(view)
                break
            # pause next iteration
            time.sleep(0.5)  # seconds
        else:
            # server not running
            return

        # open active document
        for _ in range(25):
            if self.client.is_ready():
                self.client.textdocument_didopen(view)
                break
            # pause next iteration
            time.sleep(0.5)  # seconds


class PythonToolsInitializeCommand(_InitializeCommand):
    """PythonToolsInitializeCommand"""


class PythonToolsDocumentSignatureHelpCommand(_DocumentSignatureHelpCommand):
    """PythonToolsDocumentSignatureHelpCommand"""


class PythonToolsDocumentFormattingCommand(_DocumentFormattingCommand):
    """PythonToolsDocumentFormattingCommand"""


class PythonToolsGotoDefinitionCommand(_GotoDefinitionCommand):
    """PythonToolsGotoDefinitionCommand"""


class PythonToolsPrepareRenameCommand(_PrepareRenameCommand):
    """PythonToolsPrepareRenameCommand"""


class PythonToolsRenameCommand(_RenameCommand):
    """PythonToolsRenameCommand"""


class PythonToolsApplyTextChangesCommand(_ApplyTextChangesCommand):
    """PythonToolsApplyTextChangesCommand"""


class PythonToolsStartServerCommand(_StartServerCommand):
    """PythonToolsStartServerCommand"""


class PythonToolsTerminateServerCommand(_TerminateServerCommand):
    """PythonToolsTerminateServerCommand"""
