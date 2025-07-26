from collections import namedtuple
from functools import wraps
from typing import Optional, Callable
import sublime
import sublime_plugin

from ..workspace.edit import WorkspaceEdit
from ...document import is_valid_document
from ...lsp_client import Response
from ...session import Session
from ...uri import path_to_uri
from ....constant import COMMAND_PREFIX


def client_must_ready(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.client and self.client.is_ready():
            return func(self, *args, **kwargs)
        return None

    return wrapper


class _PrepareRenameCommand(sublime_plugin.TextCommand):
    client = None

    @client_must_ready
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        if not is_valid_document(self.view):
            return
        if event:
            point = event["text_point"]
        else:
            point = self.view.sel()[0].begin()

        start_row, start_col = self.view.rowcol(point)
        self.client.textdocument_preparerename(self.view, start_row, start_col)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


class _RenameCommand(sublime_plugin.TextCommand):
    client = None

    @client_must_ready
    def run(self, edit: sublime.Edit, row: int, column: int, new_name: str):
        if not is_valid_document(self.view):
            return
        self.client.textdocument_rename(self.view, row, column, new_name)

    def is_visible(self):
        return is_valid_document(self.view)


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentRenameMixins:

    rename_target = None

    @must_initialized
    def textdocument_preparerename(self, view, row, col):
        if document := self.session.get_document(view.file_name()):
            self.rename_target = document
            self.message_pool.send_request(
                "textDocument/prepareRename",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def _handle_preparerename(self, session: Session, location: dict):
        view = self.rename_target.view

        start = LineCharacter(**location["range"]["start"])
        end = LineCharacter(**location["range"]["end"])
        start_point = view.text_point(*start)
        end_point = view.text_point(*end)

        region = sublime.Region(start_point, end_point)
        old_name = view.substr(region)
        row, col = view.rowcol(start_point)

        def request_rename(new_name):
            if new_name and old_name != new_name:
                view.run_command(
                    f"{COMMAND_PREFIX}_rename",
                    {"row": row, "column": col, "new_name": new_name},
                )

        input_text("rename", old_name, request_rename)

    def handle_textdocument_preparerename(self, session: Session, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            self._handle_preparerename(session, result)

    @must_initialized
    def textdocument_rename(self, view, row, col, new_name):
        # Save all changes before perform rename
        for document in self.session.get_documents():
            document.save()

        if document := self.session.get_document(view.file_name()):
            self.rename_target = document
            self.message_pool.send_request(
                "textDocument/rename",
                {
                    "newName": new_name,
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_rename(self, session: Session, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            WorkspaceEdit(session).apply_changes(result)


def input_text(
    title: str, default_text: str, on_done_callback: Callable[[str], None]
) -> None:
    """"""
    sublime.active_window().show_input_panel(
        caption=title,
        initial_text=default_text,
        on_done=on_done_callback,
        on_change=None,
        on_cancel=None,
    )
