"""plugin entry point"""

import logging
import shlex
import time
from collections import defaultdict
from pathlib import Path
from shutil import which as sh_which
from typing import List, Optional

import sublime
import sublime_plugin

from ..constant import LOGGING_CHANNEL, COMMAND_PREFIX, PACKAGE_NAME
from ..plugin_core.client import BaseClient, ServerArguments
from ..plugin_core.document import is_valid_document
from ..plugin_core.sublime_settings import Settings
from ..plugin_core.transport import StandardIO

from ..plugin_core.features.document_updater import _ApplyTextChangesCommand
from ..plugin_core.features.server_manager import (
    _StartServerCommand,
    _TerminateServerCommand,
)
from ..plugin_core.features.initializer import _InitializeCommand, InitializerMixins
from ..plugin_core.features.text_document.synchronizer import (
    DocumentSynchronizeEventListener,
    DocumentSynchronizeTextChangeListener,
    DocumentSynchronizerMixins,
)
from ..plugin_core.features.text_document.completion import (
    CompletionEventListener,
    DocumentCompletionMixins,
)
from ..plugin_core.features.text_document.signature_help import (
    _DocumentSignatureHelpCommand,
    DocumentSignatureHelpEventListener,
    DocumentSignatureHelpMixins,
)
from ..plugin_core.features.text_document.hover import (
    HoverEventListener,
    DocumentHoverMixins,
)
from ..plugin_core.features.text_document.formatting import (
    _DocumentFormattingCommand,
    DocumentFormattingMixins,
)
from ..plugin_core.features.text_document.definition import (
    _GotoDefinitionCommand,
    DocumentDefinitionMixins,
)
from ..plugin_core.features.text_document.rename import (
    _PrepareRenameCommand,
    _RenameCommand,
    DocumentRenameMixins,
)
from ..plugin_core.features.text_document.diagnostics import DocumentDiagnosticsMixins
from ..plugin_core.features.workspace.command import WorkspaceExecuteCommandMixins
from ..plugin_core.features.workspace.edit import WorkspaceApplyEditMixins
from ..plugin_core.features.window.message import WindowMessageMixins


LOGGER = logging.getLogger(LOGGING_CHANNEL)


class Client(
    BaseClient,
    InitializerMixins,
    DocumentSynchronizerMixins,
    DocumentCompletionMixins,
    DocumentDefinitionMixins,
    DocumentDiagnosticsMixins,
    DocumentFormattingMixins,
    DocumentHoverMixins,
    DocumentRenameMixins,
    DocumentSignatureHelpMixins,
    WorkspaceExecuteCommandMixins,
    WorkspaceApplyEditMixins,
    WindowMessageMixins,
):
    """Client Implementation"""


_RUN_COMMAND_AFTER: int = -1


def get_envs_settings() -> Optional[dict]:
    """get environments defined in '*.sublime-settings'"""

    with Settings() as settings:
        if envs := settings.get("envs"):
            return envs

        # Prevent multiple call run_command
        now = time.time()
        global _RUN_COMMAND_AFTER
        if now < _RUN_COMMAND_AFTER:
            return None

        duration = 5  # in second
        _RUN_COMMAND_AFTER = now + duration

        sublime.active_window().run_command(f"{COMMAND_PREFIX}_set_environment")
        return None


class InitializerEventListener(sublime_plugin.EventListener):

    client = None

    def on_activated_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        if not self.client:
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


# -------------------------- Plugin Commands ------------------------------------


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


def setup_logger():
    """"""
    level = _get_logging_settings()
    LOGGER.setLevel(level)
    fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOGGER.addHandler(sh)


def _get_logging_settings():
    """get logging level defined in '*.sublime-settings'"""
    name_to_loglevel_map = defaultdict(
        lambda: logging.NOTSET,
        {
            "error": logging.ERROR,
            "warning": logging.WARNING,
            "info": logging.INFO,
            "debug": logging.DEBUG,
        },
    )
    with Settings() as settings:
        named_level = settings.get("logging_level", "")
    return name_to_loglevel_map[str(named_level).lower()]


def lsserver_command() -> List[str]:
    python = None
    with Settings() as settings:
        if executable_path := settings.get("python", None):
            python = executable_path
        # find from PATH
        elif executable_path := sh_which("python"):
            python = executable_path
        else:
            raise Exception("unable find python executable")
    return [python, "-m", "pyserver", "-i"]


def lsserver_args() -> List[str]:
    args = set()

    with Settings() as settings:
        if settings_flags := settings.get("args"):
            args.update(shlex.split(settings_flags))

    if LOGGER.level == logging.DEBUG:
        args.add("--verbose")
    return [arg for arg in args if arg]


def lsserver_workdir() -> Optional[str]:
    return str(Path(sublime.packages_path()) / PACKAGE_NAME / "pyserver")


# CLIENT SINGLETON
CLIENT = None


def setup_client():
    """"""
    global CLIENT

    command = lsserver_command() + lsserver_args()
    CLIENT = Client(ServerArguments(command, lsserver_workdir()), StandardIO)


def setup_plugins(client: Client) -> None:
    for command_or_event in {
        InitializerEventListener,
        # ----------------------
        _InitializeCommand,
        _StartServerCommand,
        _TerminateServerCommand,
        DocumentSynchronizeEventListener,
        DocumentSynchronizeTextChangeListener,
        CompletionEventListener,
        _DocumentSignatureHelpCommand,
        DocumentSignatureHelpEventListener,
        HoverEventListener,
        _DocumentFormattingCommand,
        _GotoDefinitionCommand,
        _PrepareRenameCommand,
        _RenameCommand,
    }:
        command_or_event.client = client


# ----------------------------- Entry Points ------------------------------------


def plugin_loaded():
    """plugin entry point"""
    setup_logger()
    setup_client()
    setup_plugins(CLIENT)


def plugin_unloaded():
    """executed before plugin unloaded"""
    if CLIENT:
        CLIENT.terminate()
