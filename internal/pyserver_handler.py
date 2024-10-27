"""pyserver spesific handler"""

import logging
import threading

from collections import namedtuple
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Optional, Dict, List, Callable

import sublime

from . import lsp_client
from .constant import (
    COMMAND_PREFIX,
    LOGGING_CHANNEL,
    PACKAGE_NAME,
)
from .document import (
    BufferedDocument,
    UnbufferedDocument,
    TextChange,
    path_to_uri,
    uri_to_path,
)
from .handler import (
    BaseHandler,
    DiagnosticPanel,
    COMPLETION_KIND_MAP,
    input_text,
    open_location,
)
from .sublime_settings import Settings
from .workspace import (
    Workspace,
    get_workspace_path,
    create_document,
    rename_document,
    delete_document,
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

    def __init__(self, transport: lsp_client.Transport):
        super().__init__(transport)
        self.diagnostic_manager = DiagnosticManager(
            DiagnosticReportSettings(show_panel=False)
        )

        self.handler_map.update(
            {
                "initialize": self.handle_initialize,
                # window
                "window/logMessage": self.handle_window_logmessage,
                "window/showMessage": self.handle_window_showmessage,
                # workspace
                "workspace/applyEdit": self.handle_workspace_applyedit,
                "workspace/executeCommand": self.handle_workspace_executecommand,
                # textDocument
                "textDocument/hover": self.handle_textdocument_hover,
                "textDocument/completion": self.handle_textdocument_completion,
                "textDocument/signatureHelp": self.handle_textdocument_signaturehelp,
                "textDocument/publishDiagnostics": self.handle_textdocument_publishdiagnostics,
                "textDocument/formatting": self.handle_textdocument_formatting,
                "textDocument/definition": self.handle_textdocument_definition,
                "textDocument/prepareRename": self.handle_textdocument_preparerename,
                "textDocument/rename": self.handle_textdocument_rename,
            }
        )

    def is_ready(self) -> bool:
        return self.client.is_server_running() and self.session.is_begin()

    def terminate(self):
        """exit session"""
        self.client.terminate_server()
        self.diagnostic_manager.reset()
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
                "rootUri": path_to_uri(workspace_path),
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

        self.diagnostic_manager.reset()
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
        self.diagnostic_manager.set_active_view(view)

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
                        "uri": path_to_uri(document.file_name),
                        "version": document.version,
                    }
                },
            )

    @session.must_begin
    def textdocument_didsave(self, view: sublime.View):
        if document := self.workspace.get_document(view):
            self.client.send_notification(
                "textDocument/didSave",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
            )

        else:
            # untitled document not yet loaded to server
            self.textdocument_didopen(view)

    @session.must_begin
    def textdocument_didclose(self, view: sublime.View):
        file_name = view.file_name()
        self.diagnostic_manager.remove(view)

        if document := self.workspace.get_document(view):
            self.workspace.remove_document(view)

            # if document still opened in other View
            if self.workspace.get_documents(file_name):
                return

            self.client.send_notification(
                "textDocument/didClose",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
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
                        "uri": path_to_uri(document.file_name),
                        "version": document.version,
                    },
                },
            )

    def _get_diagnostic_message(self, view: sublime.View, row: int, col: int):
        point = view.text_point(row, col)

        def contain_point(item: DiagnosticItem):
            return item.region.contains(point)

        diagnostics = self.diagnostic_manager.get_active_view_diagnostics(contain_point)
        if not diagnostics:
            return ""

        title = "### Diagnostics:\n"
        diagnostic_message = "\n".join([f"- {d.message}" for d in diagnostics])
        return f"{title}\n{diagnostic_message}"

    @session.must_begin
    def textdocument_hover(self, view, row, col):
        method = "textDocument/hover"
        # In multi row/column layout, new popup will created in current View,
        # but active popup doesn't discarded.
        if other := self.action_target_map.get(method):
            other.view.hide_popup()

        if document := self.workspace.get_document(view):
            if message := self._get_diagnostic_message(view, row, col):
                document.show_popup(message, row, col)
                return

            self.client.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    def handle_textdocument_hover(self, params: dict):
        method = "textDocument/hover"
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            message = result["contents"]["value"]
            start = result["range"]["start"]
            row, col = start["line"], start["character"]
            self.action_target_map[method].show_popup(message, row, col)

    @session.must_begin
    def textdocument_completion(self, view, row, col):
        method = "textDocument/completion"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

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
        method = "textDocument/completion"
        if err := params.get("error"):
            print(err["message"])

        elif result := params.get("result"):
            items = [self._build_completion(item) for item in result["items"]]
            self.action_target_map[method].show_completion(items)

    @session.must_begin
    def textdocument_signaturehelp(self, view, row, col):
        method = "textDocument/signatureHelp"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    def handle_textdocument_signaturehelp(self, params: dict):
        method = "textDocument/signatureHelp"
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
            view = self.action_target_map[method].view
            row, col = view.rowcol(view.sel()[0].a)
            self.action_target_map[method].show_popup(message, row, col)

    def handle_textdocument_publishdiagnostics(self, params: dict):
        file_name = uri_to_path(params["uri"])
        diagnostics = params["diagnostics"]

        for document in self.workspace.get_documents(file_name):
            self.diagnostic_manager.update(document.view, diagnostics)

    @staticmethod
    def _get_text_change(change: dict) -> TextChange:
        start = change["range"]["start"]
        end = change["range"]["end"]
        text = change["newText"]
        length = change["rangeLength"]

        return TextChange(
            (start["line"], start["character"]),
            (end["line"], end["character"]),
            text,
            length,
        )

    @session.must_begin
    def textdocument_formatting(self, view):
        method = "textDocument/formatting"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "options": {"insertSpaces": True, "tabSize": 2},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    def handle_textdocument_formatting(self, params: dict):
        method = "textDocument/formatting"
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            changes = [self._get_text_change(c) for c in result]
            self.action_target_map[method].apply_text_changes(changes)

    def handle_workspace_applyedit(self, params: dict) -> dict:
        try:
            WorkspaceEdit(self.workspace).apply(params["edit"])

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
        method = "textDocument/definition"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    @staticmethod
    def _build_location(location: dict) -> PathEncodedStr:
        file_name = uri_to_path(location["uri"])
        row = location["range"]["start"]["line"]
        col = location["range"]["start"]["character"]
        return f"{file_name}:{row+1}:{col+1}"

    def handle_textdocument_definition(self, params: dict):
        method = "textDocument/definition"
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            view = self.action_target_map[method].view
            locations = [self._build_location(l) for l in result]
            open_location(view, locations)

    @session.must_begin
    def textdocument_preparerename(self, view, row, col):
        method = "textDocument/prepareRename"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    @session.must_begin
    def textdocument_rename(self, view, row, col, new_name):
        method = "textDocument/rename"
        if document := self.workspace.get_document(view):
            self.client.send_request(
                method,
                {
                    "newName": new_name,
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )
            self.action_target_map[method] = document

    def _handle_preparerename(self, location: dict):
        method = "textDocument/prepareRename"
        view = self.action_target_map[method].view

        start = location["range"]["start"]
        start_point = view.text_point(start["line"], start["character"])
        end = location["range"]["end"]
        end_point = view.text_point(end["line"], end["character"])

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

    def handle_textdocument_preparerename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._handle_preparerename(result)

    def handle_textdocument_rename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            WorkspaceEdit(self.workspace).apply(result)


class DiagnosticItem:
    __slots__ = ["severity", "region", "message"]

    def __init__(self, severity: int, region: sublime.Region, message: str) -> None:
        self.severity = severity
        self.region = region
        self.message = message

    def __repr__(self) -> str:
        text = "DiagnosticItem(severity=%s, region=%s, message='%s')"
        return text % (self.severity, self.region, self.message)


@dataclass
class DiagnosticReportSettings:
    highlight_text: bool = True
    show_status: bool = True
    show_panel: bool = False


class DiagnosticManager:
    def __init__(self, settings: DiagnosticReportSettings = None) -> None:
        self.settings = settings or DiagnosticReportSettings()
        self.diagnostics: Dict[sublime.View, List[dict]] = {}
        self.active_view: sublime.View = None
        self.panel = DiagnosticPanel()

        self._change_lock = threading.Lock()
        self._active_view_diagnostics: List[DiagnosticItem] = []

    def reset(self):
        # erase regions
        for view in self.diagnostics.keys():
            view.erase_regions(self.REGIONS_KEY)

        self.diagnostics = {}
        self.active_view = None
        self.panel.destroy()

        self._active_view_diagnostics = []

    def get(self, view: sublime.View) -> List[dict]:
        with self._change_lock:
            return self.diagnostics.get(view, [])

    def update(self, view: sublime.View, diagostics: List[dict]):
        with self._change_lock:
            self.diagnostics.update({view: diagostics})
            self._on_diagnostic_changed()

    def remove(self, view: sublime.View):
        with self._change_lock:
            try:
                del self.diagnostics[view]
            except KeyError:
                pass
            self._on_diagnostic_changed()

    def set_active_view(self, view: sublime.View):
        if view == self.active_view:
            return

        self.active_view = view
        self._on_diagnostic_changed()

    def get_active_view_diagnostics(
        self, predicate: Callable[[DiagnosticItem], bool] = None
    ) -> List[DiagnosticItem]:
        if not predicate:
            return self._active_view_diagnostics

        return [d for d in self._active_view_diagnostics if predicate(d)]

    LineCharacter = namedtuple("LineCharacter", ["line", "character"])

    def _to_diagnostic_item(
        self, view: sublime.View, diagnostic: dict, /
    ) -> DiagnosticItem:

        start = self.LineCharacter(**diagnostic["range"]["start"])
        end = self.LineCharacter(**diagnostic["range"]["end"])
        region = sublime.Region(view.text_point(*start), view.text_point(*end))
        message = diagnostic["message"]
        if source := diagnostic.get("source"):
            message += f" ({source})"

        return DiagnosticItem(diagnostic["severity"], region, message)

    def _update_active_view_diagnostics(self):
        self._active_view_diagnostics = [
            self._to_diagnostic_item(self.active_view, diagnostic)
            for diagnostic in self.diagnostics.get(self.active_view, [])
        ]

    def _on_diagnostic_changed(self):
        self._update_active_view_diagnostics()

        if self.settings.highlight_text:
            self._highlight_regions(self.active_view)
        if self.settings.show_status:
            self._show_status(self.active_view)
        if self.settings.show_panel:
            self._show_panel(self.active_view)

    REGIONS_KEY = f"{PACKAGE_NAME}_DIAGNOSTIC_REGIONS"

    def _highlight_regions(self, view: sublime.View):
        regions = [item.region for item in self._active_view_diagnostics]
        view.add_regions(
            key=self.REGIONS_KEY,
            regions=regions,
            scope="invalid",
            icon="dot",
            flags=sublime.DRAW_NO_FILL
            | sublime.DRAW_NO_OUTLINE
            | sublime.DRAW_SQUIGGLY_UNDERLINE,
        )

    STATUS_KEY = f"{PACKAGE_NAME}_DIAGNOSTIC_STATUS"

    def _show_status(self, view: sublime.View):
        if not self._active_view_diagnostics:
            view.erase_status(self.STATUS_KEY)
            return

        value = "ERROR %s, WARNING %s"
        err_count = len(
            [item for item in self._active_view_diagnostics if item.severity == 1]
        )
        warn_count = len(self._active_view_diagnostics) - err_count
        view.set_status(self.STATUS_KEY, value % (err_count, warn_count))

    def _show_panel(self, view: sublime.View):
        def wrap_location(view: sublime.View, item: DiagnosticItem):
            short_name = Path(view.file_name()).name
            row, col = view.rowcol(item.region.begin())
            return f"{short_name}:{row+1}:{col} {item.message}"

        content = "\n".join(
            [wrap_location(view, item) for item in self._active_view_diagnostics]
        )
        self.panel.set_content(content)
        self.panel.show()


class WorkspaceEdit:

    def __init__(self, workspace_: Workspace):
        self.workspace = workspace_

    def apply(self, edit_changes: dict) -> None:
        """"""

        for document_changes in edit_changes["documentChanges"]:
            # documentChanges: TextEdit|CreateFile|RenameFile|DeleteFile

            # File Resource Changes
            if document_changes.get("kind"):
                self._apply_resource_changes(document_changes)
                return

            # TextEdit Changes
            self._apply_textedit_changes(document_changes)

    def _apply_textedit_changes(self, document_changes: dict):
        file_name = uri_to_path(document_changes["textDocument"]["uri"])
        edits = document_changes["edits"]
        changes = [self._get_text_change(c) for c in edits]

        document = self.workspace.get_document_by_name(
            file_name, UnbufferedDocument(file_name)
        )
        document.apply_text_changes(changes)
        document.save()

    @staticmethod
    def _get_text_change(change: dict) -> TextChange:
        start = change["range"]["start"]
        end = change["range"]["end"]
        text = change["newText"]
        length = change["rangeLength"]

        return TextChange(
            (start["line"], start["character"]),
            (end["line"], end["character"]),
            text,
            length,
        )

    def _apply_resource_changes(self, changes: dict):
        func = {
            "create": self._create_document,
            "rename": self._rename_document,
            "delete": self._delete_document,
        }
        kind = changes["kind"]
        func[kind](changes)

    @staticmethod
    def _create_document(document_changes: dict):
        file_name = uri_to_path(document_changes["uri"])
        create_document(file_name)

    @staticmethod
    def _rename_document(document_changes: dict):
        old_name = uri_to_path(document_changes["oldUri"])
        new_name = uri_to_path(document_changes["newUri"])
        rename_document(old_name, new_name)

    @staticmethod
    def _delete_document(document_changes: dict):
        file_name = uri_to_path(document_changes["uri"])
        delete_document(file_name)


def get_handler() -> BaseHandler:
    """"""
    package_path = Path(sublime.packages_path(), PACKAGE_NAME)

    server_path = package_path.joinpath("pyserver")
    command = ["python", "-m", "pyserver", "-i"]
    transport = lsp_client.StandardIO(command, server_path)
    return PyserverHandler(transport)


def get_envs_settings() -> Optional[dict]:
    """get environments defined in '*.sublime-settings'"""

    with Settings() as settings:
        if envs := settings.get("envs"):
            return envs

        sublime.active_window().run_command("pythontools_set_environment")
        return None
