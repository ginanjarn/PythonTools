"""Commands plugin implementation

This module used to name TextCommand and WindowCommand
which called by Commands, Menu, etc.

In SublimeText plugins, command name must be unique or
it will be replaced by recent loaded plugin.
Command name assignment similar to a dict type.
"""

import sublime_plugin
from .plugin_implementation import (
    DocumentFormattingCommandMixins,
    DocumentSignatureHelpCommandMixins,
    GotoDefinitionCommandMixins,
    PrepareRenameCommandMixins,
    RenameCommandMixins,
    ApplyTextChangesCommandMixins,
    TerminateCommandMixins,
)


class PythonToolsDocumentSignatureHelpCommand(
    DocumentSignatureHelpCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsDocumentSignatureHelpCommand"""


class PythonToolsDocumentFormattingCommand(
    DocumentFormattingCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsDocumentFormattingCommand"""


class PythonToolsGotoDefinitionCommand(
    GotoDefinitionCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsGotoDefinitionCommand"""


class PythonToolsPrepareRenameCommand(
    PrepareRenameCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsPrepareRenameCommand"""


class PythonToolsRenameCommand(
    RenameCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsRenameCommand"""


class PythonToolsApplyTextChangesCommand(
    ApplyTextChangesCommandMixins,
    sublime_plugin.TextCommand,
):
    """PythonToolsApplyTextChangesCommand"""


class PythonToolsTerminateCommand(
    TerminateCommandMixins,
    sublime_plugin.WindowCommand,
):
    """PythonToolsTerminateCommand"""
