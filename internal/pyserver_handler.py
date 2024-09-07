"""pyserver spesific handler"""

import logging
import threading

from functools import wraps
from io import StringIO
from pathlib import Path
from typing import Optional, Dict, List, Any

import sublime

from . import lsp_client
from . import workspace
from .constant import (
    LOGGING_CHANNEL,
    PACKAGE_NAME,
    VIEW_SELECTOR,
)
from .handler import BaseHandler, COMPLETION_KIND_MAP
from .sublime_settings import Settings
from .workspace import (
    BufferedDocument,
    UnbufferedDocument,
    TextChange,
)

PathStr = str
PathEncodedStr = str
"""Path encoded '<file_name>:<row>:<column>'"""
LOGGER = logging.getLogger(LOGGING_CHANNEL)


class Session:
    def __init__(self):
        self.event = threading.Event()

    def is_begin(self):
        return self.event.is_set()

    def begin(self):
        """begin session"""
        self.event.set()

    def done(self):
        """done session"""
        self.event.clear()

    def must_begin(self, func):
        """return 'None' if not begin"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self.event.is_set():
                return None

            return func(*args, **kwargs)

        return wrapper

    def wait_begin(self, func):
        """return function after session is begin"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            self.event.wait()
            return func(*args, **kwargs)

        return wrapper


class PyserverHandler(BaseHandler):
    """"""

    session = Session()

    def is_ready(self) -> bool:
        return self.client.is_server_running() and self.session.is_begin()

    def terminate(self):
        """exit session"""
        self.client.terminate_server()
        self._reset_state()

    def initialize(self, view: sublime.View):
        # cancel if initializing
        if self._initializing:
            return

        # check if view not closed
        if view is None:
            return

        workspace_path = get_workspace_path(view)
        if not workspace_path:
            return

        self._initializing = True
        self.client.send_request(
            "initialize",
            {
                "rootPath": workspace_path,
                "rootUri": lsp_client.path_to_uri(workspace_path),
                "capabilities": {
                    "textDocument": {
                        "hover": {
                            "contentFormat": ["markdown", "plaintext"],
                        }
                    }
                },
            },
        )

    def handle_initialize(self, params: dict):
        if err := params.get("error"):
            print(err["message"])
            return

        self.client.send_notification("initialized", {})
        self._initializing = False

        self.session.begin()

    def handle_window_logmessage(self, params: dict):
        print(params["message"])

    def handle_window_showmessage(self, params: dict):
        sublime.status_message(params["message"])

    @session.wait_begin
    def textdocument_didopen(self, view: sublime.View, *, reload: bool = False):
        # check if view not closed
        if not (view and view.is_valid()):
            return

        file_name = view.file_name()

        if opened_document := self.workspace.get_document(view):
            if opened_document.file_name == file_name and (not reload):
                return

            # In SublimeText, rename file only retarget to new path
            # but the 'View' is not closed.
            # Close older document then reopen with new name.
            self.textdocument_didclose(view)

        document = BufferedDocument(view)
        self.workspace.add_document(document)

        # Document maybe opened in multiple 'View', send notification
        # only on first opening document.
        if len(self.workspace.get_documents(file_name)) == 1:
            self.client.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "languageId": document.language_id,
                        "text": document.text,
                        "uri": document.document_uri(),
                        "version": document.version,
                    }
                },
            )

    @session.must_begin
    def textdocument_didsave(self, view: sublime.View):
        if document := self.workspace.get_document(view):
            self.client.send_notification(
                "textDocument/didSave",
                {"textDocument": {"uri": document.document_uri()}},
            )

        else:
            # untitled document not yet loaded to server
            self.textdocument_didopen(view)

    @session.must_begin
    def textdocument_didclose(self, view: sublime.View):
        file_name = view.file_name()
        if document := self.workspace.get_document(view):
            self.workspace.remove_document(view)

            # if document still opened in other View
            if self.workspace.get_documents(file_name):
                return

            self.workspace.remove_invalid_diagnostic()
            self._show_diagnostic_report()

            self.client.send_notification(
                "textDocument/didClose",
                {"textDocument": {"uri": document.document_uri()}},
            )

    def _text_change_to_rpc(self, text_change: TextChange) -> dict:
        start = text_change.start
        end = text_change.end
        return {
            "range": {
                "end": {"character": end.column, "line": end.row},
                "start": {"character": start.column, "line": start.row},
            },
            "rangeLength": text_change.length,
            "text": text_change.text,
        }

    @session.must_begin
    def textdocument_didchange(self, view: sublime.View, changes: List[TextChange]):
        # Document can be related to multiple View but has same file_name.
        # Use get_document_by_name() because may be document already open
        # in other view and the argument view not assigned.
        file_name = view.file_name()
        if document := self.workspace.get_document_by_name(file_name):
            self.client.send_notification(
                "textDocument/didChange",
                {
                    "contentChanges": [self._text_change_to_rpc(c) for c in changes],
                    "textDocument": {
                        "uri": document.document_uri(),
                        "version": document.version,
                    },
                },
            )

    @session.must_begin
    def textdocument_hover(self, view, row, col):
        # In multi row/column layout, new popup will created in current View,
        # but active popup doesn't discarded.
        if other := self.action_target.hover:
            other.view.hide_popup()

        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/hover",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.hover = document

    def handle_textdocument_hover(self, params: dict):
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            message = result["contents"]["value"]
            start = result["range"]["start"]
            row, col = start["line"], start["character"]
            self.action_target.hover.show_popup(message, row, col)

    @session.must_begin
    def textdocument_completion(self, view, row, col):
        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/completion",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.completion = document

    @staticmethod
    def _build_completion(completion_item: dict) -> sublime.CompletionItem:
        text = completion_item["label"]
        try:
            insert_text = completion_item["textEdit"]["newText"]
        except KeyError:
            insert_text = text

        signature = completion_item["detail"]
        kind = COMPLETION_KIND_MAP[completion_item["kind"]]

        return sublime.CompletionItem.snippet_completion(
            trigger=text,
            snippet=insert_text,
            annotation=signature,
            kind=kind,
        )

    def handle_textdocument_completion(self, params: dict):
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            items = [self._build_completion(item) for item in result["items"]]
            self.action_target.completion.show_completion(items)

    @session.must_begin
    def textdocument_signaturehelp(self, view, row, col):
        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/signatureHelp",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.signature_help = document

    def handle_textdocument_signaturehelp(self, params: dict):
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
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
            view = self.action_target.signature_help.view
            row, col = view.rowcol(view.sel()[0].a)
            self.action_target.signature_help.show_popup(message, row, col)

    @staticmethod
    def _build_diagnostic_message(diagnostics_map: Dict[PathStr, Any]) -> str:

        def build_line(file_name, diagnostic):
            short_name = Path(file_name).name
            row = diagnostic["range"]["start"]["line"]
            col = diagnostic["range"]["start"]["character"]
            message = diagnostic["message"]
            source = diagnostic.get("source", "")

            # natural line index start with 1
            row += 1

            return f"{short_name}:{row}:{col}: {message} ({source})\n"

        message_buffer = StringIO()
        for file_name, diagnostics in diagnostics_map.items():
            message = "".join([build_line(file_name, d) for d in diagnostics])
            message_buffer.write(message)

        return message_buffer.getvalue()

    def _show_diagnostic_report(self):
        diagnostic_map = self.workspace.get_diagnostics()
        diagnostic_text = self._build_diagnostic_message(diagnostic_map)

        self.diagnostics_panel.set_content(diagnostic_text)
        self.diagnostics_panel.show()

    @staticmethod
    def _get_diagnostic_region(view: sublime.View, diagnostic: dict) -> sublime.Region:

        start = diagnostic["range"]["start"]
        end = diagnostic["range"]["end"]

        start_point = view.text_point(start["line"], start["character"])
        end_point = view.text_point(end["line"], end["character"])
        return sublime.Region(start_point, end_point)

    def handle_textdocument_publishdiagnostics(self, params: dict):
        file_name = lsp_client.uri_to_path(params["uri"])
        diagnostics = params["diagnostics"]

        self.workspace.set_diagnostic(file_name, diagnostics)
        self._show_diagnostic_report()

        for document in self.workspace.get_documents(file_name):
            regions = [
                self._get_diagnostic_region(document.view, diagnostic)
                for diagnostic in diagnostics
            ]
            document.highlight_text(regions)

    @staticmethod
    def _get_text_change(change: dict) -> TextChange:
        start = change["range"]["start"]
        end = change["range"]["end"]
        text = change["newText"]

        return TextChange(
            (start["line"], start["character"]), (end["line"], end["character"]), text
        )

    @session.must_begin
    def textdocument_formatting(self, view):
        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/formatting",
                {
                    "options": {"insertSpaces": True, "tabSize": 2},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.formatting = document

    def handle_textdocument_formatting(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            changes = [self._get_text_change(c) for c in result]
            self.action_target.formatting.apply_text_changes(changes)

    @staticmethod
    def _create_document(document_changes: dict):
        file_name = lsp_client.uri_to_path(document_changes["uri"])
        workspace.create_document(file_name)

    @staticmethod
    def _rename_document(document_changes: dict):
        old_name = lsp_client.uri_to_path(document_changes["oldUri"])
        new_name = lsp_client.uri_to_path(document_changes["newUri"])
        workspace.rename_document(old_name, new_name)

    @staticmethod
    def _delete_document(document_changes: dict):
        file_name = lsp_client.uri_to_path(document_changes["uri"])
        workspace.delete_document(file_name)

    def _apply_resource_changes(self, document_changes: dict):
        func = {
            "create": self._create_document,
            "rename": self._rename_document,
            "delete": self._delete_document,
        }
        kind = document_changes.get("kind")
        func[kind](document_changes)

    def _apply_textedit_changes(self, document_changes: dict):
        file_name = lsp_client.uri_to_path(document_changes["textDocument"]["uri"])
        edits = document_changes["edits"]
        changes = [self._get_text_change(c) for c in edits]

        document = self.workspace.get_document_by_name(
            file_name, UnbufferedDocument(file_name)
        )
        document.apply_text_changes(changes)
        document.save()

    def _apply_edit(self, edit: dict):
        for document_changes in edit["documentChanges"]:
            # documentChanges: TextEdit|CreateFile|RenameFile|DeleteFile

            # File Resource Changes
            if document_changes.get("kind"):
                self._apply_resource_changes(document_changes)
                return

            # TextEdit Changes
            self._apply_textedit_changes(document_changes)

    def handle_workspace_applyedit(self, params: dict) -> dict:
        try:
            self._apply_edit(params["edit"])
        except Exception as err:
            LOGGER.error(err, exc_info=True)
            return {"applied": False}
        else:
            return {"applied": True}

    def handle_workspace_executecommand(self, params: dict) -> dict:
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            LOGGER.info(result)

        return None

    @session.must_begin
    def textdocument_definition(self, view, row, col):
        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/definition",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.definition = document

    @staticmethod
    def _build_location(location: dict) -> PathEncodedStr:
        file_name = lsp_client.uri_to_path(location["uri"])
        row = location["range"]["start"]["line"]
        col = location["range"]["start"]["character"]
        return f"{file_name}:{row+1}:{col+1}"

    def handle_textdocument_definition(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            view = self.action_target.definition.view
            locations = [self._build_location(l) for l in result]
            self._open_locations(view, locations)

    @session.must_begin
    def textdocument_preparerename(self, view, row, col):
        if document := self.workspace.get_document(view):
            self.client.send_request(
                "textDocument/prepareRename",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": document.document_uri()},
                },
            )
            self.action_target.rename = document

    @session.must_begin
    def textdocument_rename(self, new_name, row, col):
        self.client.send_request(
            "textDocument/rename",
            {
                "newName": new_name,
                "position": {"character": col, "line": row},
                "textDocument": {"uri": self.action_target.rename.document_uri()},
            },
        )

    def _handle_preparerename(self, location: dict):
        view = self.action_target.rename.view

        start = location["range"]["start"]
        start_point = view.text_point(start["line"], start["character"])
        end = location["range"]["end"]
        end_point = view.text_point(end["line"], end["character"])

        region = sublime.Region(start_point, end_point)
        old_name = view.substr(region)
        row, col = view.rowcol(start_point)

        def request_rename(new_name):
            if new_name and old_name != new_name:
                self.textdocument_rename(new_name, row, col)

        self._input_rename(old_name, request_rename)

    def handle_textdocument_preparerename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._handle_preparerename(result)

    def handle_textdocument_rename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._apply_edit(result)


def get_handler() -> BaseHandler:
    """"""
    package_path = Path(sublime.packages_path(), PACKAGE_NAME)

    server_path = package_path.joinpath("pyserver")
    command = ["python", "-m", "pyserver", "-i"]
    transport = lsp_client.StandardIO(command, server_path)
    return PyserverHandler(transport)


def is_valid_document(view: sublime.View) -> bool:
    """"""
    if not view.file_name():
        return False
    return view.match_selector(0, VIEW_SELECTOR)


def get_workspace_path(view: sublime.View) -> str:
    """"""
    window = view.window()
    file_name = view.file_name()
    if not file_name:
        return ""

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


def get_envs_settings() -> Optional[dict]:
    """get environments defined in '*.sublime-settings'"""

    with Settings() as settings:
        if envs := settings.get("envs"):
            return envs

        sublime.active_window().run_command("pythontools_set_environment")
        return None
