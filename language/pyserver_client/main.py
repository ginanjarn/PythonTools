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
from ..plugin_core.child_process import ChildProcess
from ..plugin_core.client import Client
from ..plugin_core.document import is_valid_document
from ..plugin_core.sublime_settings import Settings
from ..plugin_core.transport import StandardIO

from ..plugin_core.features.document_updater import _ApplyTextChangesCommand
from ..plugin_core.features.server_manager import (
    _StartServerCommand,
    _TerminateServerCommand,
)
from ..plugin_core.features.initializer import _InitializeCommand
from ..plugin_core.features.text_document.synchronization import (
    DocumentSynchronizeEventListener,
    DocumentSynchronizeTextChangeListener,
)
from ..plugin_core.features.text_document.completion import CompletionEventListener
from ..plugin_core.features.text_document.signature_help import (
    _DocumentSignatureHelpCommand,
    DocumentSignatureHelpEventListener,
)
from ..plugin_core.features.text_document.hover import HoverEventListener
from ..plugin_core.features.text_document.formatting import _DocumentFormattingCommand
from ..plugin_core.features.text_document.definition import _GotoDefinitionCommand
from ..plugin_core.features.text_document.rename import (
    _PrepareRenameCommand,
    _RenameCommand,
)
from ..plugin_core.features.text_document.symbol import _DocumentSymbolCommand


LOGGER = logging.getLogger(LOGGING_CHANNEL)
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

    client: Client = None

    def on_activated_async(self, view: sublime.View):
        if not is_valid_document(view):
            return
        if not self.client:
            return
        if self.client.is_ready():
            return

        is_server_running = self.client.is_server_running
        if not is_server_running():
            view.run_command(
                f"{COMMAND_PREFIX}_start_server",
                {"envs": get_envs_settings()},
            )

        # initialize
        for _ in range(25):
            if is_server_running():
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


class PythonToolsDocumentSymbolCommand(_DocumentSymbolCommand):
    """PythonToolsDocumentSymbolCommand"""


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
    child = ChildProcess(command, lsserver_workdir())
    transport = StandardIO(child)
    CLIENT = Client(child, transport)


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
        _DocumentSymbolCommand,
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
