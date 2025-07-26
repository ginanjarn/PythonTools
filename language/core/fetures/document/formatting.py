from collections import namedtuple
from functools import wraps
import sublime
import sublime_plugin

from ...document import is_valid_document, TextChange
from ...lsp_client import Response
from ...session import Session
from ...uri import path_to_uri


def client_must_ready(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.client and self.client.is_ready():
            return func(self, *args, **kwargs)
        return None

    return wrapper


class _DocumentFormattingCommand(sublime_plugin.TextCommand):
    client = None

    @client_must_ready
    def run(self, edit: sublime.Edit):
        if not is_valid_document(self.view):
            return
        self.client.textdocument_formatting(self.view)

    def is_visible(self):
        return is_valid_document(self.view)


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentFormattingMixins:

    formatting_target = None

    @must_initialized
    def textdocument_formatting(self, view):
        if document := self.session.get_document(view.file_name()):
            self.formatting_target = document
            self.message_pool.send_request(
                "textDocument/formatting",
                {
                    "options": {"insertSpaces": True, "tabSize": 2},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_formatting(self, session: Session, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            changes = [rpc_to_textchange(c) for c in result]
            self.formatting_target.apply_changes(changes)


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def rpc_to_textchange(change: dict) -> TextChange:
    """"""
    return TextChange(
        LineCharacter(**change["range"]["start"]),
        LineCharacter(**change["range"]["end"]),
        change["newText"],
        change["rangeLength"],
    )
