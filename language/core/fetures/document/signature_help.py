from collections import namedtuple
from functools import wraps
import sublime
import sublime_plugin

from ....constant import COMMAND_PREFIX
from ...document import is_valid_document
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


class _DocumentSignatureHelpCommand(sublime_plugin.TextCommand):
    client = None

    @client_must_ready
    def run(
        self,
        edit: sublime.Edit,
        row: int = -1,
        column: int = -1,
    ):
        if not is_valid_document(self.view):
            return

        if row < 0:
            point = self.view.sel()[0].begin()
            row, column = self.view.rowcol(point)
        else:
            point = self.view.text_point(row, column)

        if not self.view.match_selector(point, "meta.function-call.arguments"):
            return

        self.client.textdocument_signaturehelp(self.view, row, column)


class DocumentSignatureHelpEventListener(sublime_plugin.EventListener):
    client = None

    @client_must_ready
    def on_modified_async(self, view: sublime.View):
        self.trigger_signature_help(view)

    def trigger_signature_help(self, view: sublime.View):
        if not is_valid_document(view):
            return
        point = view.sel()[0].begin()
        if view.substr(point - 1) in {"(", ","}:
            view.run_command(f"{COMMAND_PREFIX}_document_signature_help")


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentSignatureHelpMixins:

    signature_help_target = None

    @must_initialized
    def textdocument_signaturehelp(self, view, row, col):
        if document := self.session.get_document(view.file_name()):
            self.signature_help_target = document
            self.message_pool.send_request(
                "textDocument/signatureHelp",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_signaturehelp(self, session: Session, params: Response):
        if err := params.error:
            print(err["message"])

        elif result := params.result:
            signatures = result["signatures"]
            if not signatures:
                return

            message = "".join(
                [
                    "```python\n",
                    "\n".join([s["label"] for s in signatures]),
                    "\n```",
                ]
            )
            view = self.signature_help_target.view
            row, col = view.rowcol(view.sel()[0].a)
            self.signature_help_target.show_popup(message, row, col)
