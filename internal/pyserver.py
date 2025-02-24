"""pyserver spesific handler"""

import logging
import threading

from collections import namedtuple, defaultdict
from dataclasses import dataclass
from functools import wraps
from html import escape as escape_html
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any, Union

import sublime

from .constant import (
    COMMAND_PREFIX,
    LOGGING_CHANNEL,
    PACKAGE_NAME,
)
from .document import (
    BufferedDocument,
    UnbufferedDocument,
    TextChange,
)
from .errors import MethodNotFound
from .uri import (
    path_to_uri,
    uri_to_path,
)
from .lsp_client import (
    Client,
    Transport,
    StandardIO,
    MethodName,
    Response,
)
from .panels import (
    DiagnosticPanel,
    input_text,
    PathEncodedStr,
    open_location,
)
from .session import Session
from .sublime_settings import Settings
from .workspace import (
    get_workspace_path,
    create_document,
    rename_document,
    delete_document,
)

LOGGER = logging.getLogger(LOGGING_CHANNEL)
LineCharacter = namedtuple("LineCharacter", ["line", "character"])
"""Line Character namedtuple"""

Params = Union[Response, dict]
HandlerFunction = Callable[[str, Params], Any]


COMPLETION_KIND_MAP = defaultdict(
    lambda _: sublime.KIND_AMBIGUOUS,
    {
        1: (sublime.KindId.COLOR_ORANGISH, "t", ""),  # text
        2: (sublime.KindId.FUNCTION, "", ""),  # method
        3: (sublime.KindId.FUNCTION, "", ""),  # function
        4: (sublime.KindId.FUNCTION, "c", ""),  # constructor
        5: (sublime.KindId.VARIABLE, "", ""),  # field
        6: (sublime.KindId.VARIABLE, "", ""),  # variable
        7: (sublime.KindId.TYPE, "", ""),  # class
        8: (sublime.KindId.TYPE, "", ""),  # interface
        9: (sublime.KindId.NAMESPACE, "", ""),  # module
        10: (sublime.KindId.VARIABLE, "", ""),  # property
        11: (sublime.KindId.TYPE, "", ""),  # unit
        12: (sublime.KindId.COLOR_ORANGISH, "v", ""),  # value
        13: (sublime.KindId.TYPE, "", ""),  # enum
        14: (sublime.KindId.KEYWORD, "", ""),  # keyword
        15: (sublime.KindId.SNIPPET, "s", ""),  # snippet
        16: (sublime.KindId.VARIABLE, "v", ""),  # color
        17: (sublime.KindId.VARIABLE, "p", ""),  # file
        18: (sublime.KindId.VARIABLE, "p", ""),  # reference
        19: (sublime.KindId.VARIABLE, "p", ""),  # folder
        20: (sublime.KindId.VARIABLE, "v", ""),  # enum member
        21: (sublime.KindId.VARIABLE, "c", ""),  # constant
        22: (sublime.KindId.TYPE, "", ""),  # struct
        23: (sublime.KindId.TYPE, "e", ""),  # event
        24: (sublime.KindId.KEYWORD, "", ""),  # operator
        25: (sublime.KindId.TYPE, "", ""),  # type parameter
    },
)


class InitializeManager:
    """"""

    def __init__(self):
        self.initialize_event = threading.Event()
        self._is_initializing = False
        self._is_initialized = False

    def reset(self):
        self.initialize_event.clear()
        self._is_initializing = False
        self._is_initialized = False

    def set_initializing(self, status: bool = True) -> None:
        """"""
        self._is_initializing = status

    def is_initializing(self) -> bool:
        """"""
        return self._is_initializing

    def is_initialized(self) -> bool:
        """"""
        return self._is_initialized

    def initialize(self) -> None:
        """initialie session"""
        self.initialize_event.set()
        self._is_initializing = False
        self._is_initialized = True

    def uninitialize(self) -> None:
        """done session"""
        self._is_initialized = False
        self.initialize_event.clear()

    def must_initialized(self, func):
        """decorator to ignore function call if not initialized"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            if not self._is_initialized:
                return None

            return func(*args, **kwargs)

        return wrapper

    def wait_initialized(self, func):
        """decorator to wait function call execution until initialized"""

        @wraps(func)
        def wrapper(*args, **kwargs):
            self.initialize_event.wait()
            return func(*args, **kwargs)

        return wrapper


class PyserverClient(Client):
    """"""

    initialize_manager = InitializeManager()

    def __init__(self, transport: Transport):
        super().__init__(transport, self)

        # server message handler
        self.handler_map: Dict[MethodName, HandlerFunction] = dict()
        self._run_server_lock = threading.Lock()

        self.diagnostic_manager = DiagnosticManager(
            DiagnosticReportSettings(show_panel=False)
        )
        self._set_default_handler()

        # session data
        self.session = Session()

    def handle(self, method: MethodName, params: Params) -> Optional[Response]:
        """"""
        try:
            func = self.handler_map[method]
        except KeyError as err:
            raise MethodNotFound(err)

        return func(params)

    def register_handler(self, method: MethodName, function: HandlerFunction) -> None:
        """"""
        self.handler_map[method] = function

    def start_server(self, env: Optional[dict] = None) -> None:
        """"""
        # only one thread can run server
        if self._run_server_lock.locked():
            return

        with self._run_server_lock:
            if not self.is_server_running():
                sublime.status_message("running language server...")
                # sometimes the server stop working
                # we must reset the state before run server
                self.reset_state()

                self.run_server(env)
                self.listen()

    def reset_state(self) -> None:
        """reset session state"""
        self.session.reset()
        self.session.action_target.clear()
        self.initialize_manager.reset()
        self.diagnostic_manager.reset()

    def is_ready(self) -> bool:
        """check session is ready"""
        return self.is_server_running() and self.initialize_manager.is_initialized()

    def terminate(self) -> None:
        """terminate session"""
        self.terminate_server()
        self._reset_state()

    def _set_default_handler(self):
        default_handlers = {
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
        self.handler_map.update(default_handlers)

    def initialize(self, view: sublime.View):
        # cancel if initializing
        if self.initialize_manager.is_initializing():
            return

        # check if view not closed
        if view is None:
            return

        workspace_path = get_workspace_path(view)
        if not workspace_path:
            return

        self.initialize_manager.set_initializing()
        self.send_request(
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

    def handle_initialize(self, params: Response):
        if err := params.error:
            print(err["message"])
            return

        self.send_notification("initialized", {})
        self.initialize_manager.initialize()

    def handle_window_logmessage(self, params: dict):
        print(params["message"])

    def handle_window_showmessage(self, params: dict):
        sublime.status_message(params["message"])

    @initialize_manager.wait_initialized
    def textdocument_didopen(self, view: sublime.View, *, reload: bool = False):
        # check if view not closed
        if not (view and view.is_valid()):
            return

        file_name = view.file_name()
        self.diagnostic_manager.set_active_view(view)

        if opened_document := self.session.get_document(view):
            if opened_document.file_name == file_name and (not reload):
                return

            # In SublimeText, rename file only retarget to new path
            # but the 'View' is not closed.
            # Close older document then reopen with new name.
            self.textdocument_didclose(view)

        document = BufferedDocument(view)
        self.session.add_document(document)

        # Document maybe opened in multiple 'View', send notification
        # only on first opening document.
        if len(self.session.get_documents(file_name)) == 1:
            self.send_notification(
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

    @initialize_manager.must_initialized
    def textdocument_didsave(self, view: sublime.View):
        if document := self.session.get_document(view):
            self.send_notification(
                "textDocument/didSave",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
            )

        else:
            # untitled document not yet loaded to server
            self.textdocument_didopen(view)

    @initialize_manager.must_initialized
    def textdocument_didclose(self, view: sublime.View):
        file_name = view.file_name()
        self.diagnostic_manager.remove(view)

        if document := self.session.get_document(view):
            self.session.remove_document(view)

            # if document still opened in other View
            if self.session.get_documents(file_name):
                return

            self.send_notification(
                "textDocument/didClose",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
            )

    @initialize_manager.must_initialized
    def textdocument_didchange(self, view: sublime.View, changes: List[TextChange]):
        # Document can be related to multiple View but has same file_name.
        # Use get_document_by_name() because may be document already open
        # in other view and the argument view not assigned.
        file_name = view.file_name()
        if document := self.session.get_document_by_name(file_name):
            self.send_notification(
                "textDocument/didChange",
                {
                    "contentChanges": [textchange_to_rpc(c) for c in changes],
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
        diagnostic_message = "\n".join(
            [f"- {escape_html(d.message)}" for d in diagnostics]
        )
        return f"{title}\n{diagnostic_message}"

    @initialize_manager.must_initialized
    def textdocument_hover(self, view, row, col):
        method = "textDocument/hover"
        # In multi row/column layout, new popup will created in current View,
        # but active popup doesn't discarded.
        if other := self.session.action_target.get(method):
            other.view.hide_popup()

        if document := self.session.get_document(view):
            if message := self._get_diagnostic_message(view, row, col):
                document.show_popup(message, row, col)
                return

            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_hover(self, params: Response):
        method = "textDocument/hover"
        if err := params.error:
            print(err["message"])

        elif result := params.result:
            message = result["contents"]["value"]
            row, col = LineCharacter(**result["range"]["start"])
            self.session.action_target[method].show_popup(message, row, col)

    @initialize_manager.must_initialized
    def textdocument_completion(self, view, row, col):
        method = "textDocument/completion"
        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

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

    def handle_textdocument_completion(self, params: Response):
        method = "textDocument/completion"
        if err := params.error:
            print(err["message"])

        elif result := params.result:
            items = [self._build_completion(item) for item in result["items"]]
            self.session.action_target[method].show_completion(items)

    @initialize_manager.must_initialized
    def textdocument_signaturehelp(self, view, row, col):
        method = "textDocument/signatureHelp"
        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_signaturehelp(self, params: Response):
        method = "textDocument/signatureHelp"
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
            view = self.session.action_target[method].view
            row, col = view.rowcol(view.sel()[0].a)
            self.session.action_target[method].show_popup(message, row, col)

    def handle_textdocument_publishdiagnostics(self, params: dict):
        file_name = uri_to_path(params["uri"])
        diagnostics = params["diagnostics"]

        for document in self.session.get_documents(file_name):
            self.diagnostic_manager.set(document.view, diagnostics)

    @initialize_manager.must_initialized
    def textdocument_formatting(self, view):
        method = "textDocument/formatting"
        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "options": {"insertSpaces": True, "tabSize": 2},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_formatting(self, params: Response):
        method = "textDocument/formatting"
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            changes = [rpc_to_textchange(c) for c in result]
            self.session.action_target[method].apply_changes(changes)

    def handle_workspace_applyedit(self, params: dict) -> dict:
        try:
            WorkspaceEdit(self.session).apply_changes(params["edit"])

        except Exception as err:
            LOGGER.error(err, exc_info=True)
            return {"applied": False}
        else:
            return {"applied": True}

    def handle_workspace_executecommand(self, params: Response) -> dict:
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            LOGGER.info(result)

        return None

    @initialize_manager.must_initialized
    def textdocument_definition(self, view, row, col):
        method = "textDocument/definition"
        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    @staticmethod
    def _build_location(location: dict) -> PathEncodedStr:
        file_name = uri_to_path(location["uri"])
        start_row, start_col = LineCharacter(**location["range"]["start"])
        return f"{file_name}:{start_row+1}:{start_col+1}"

    def handle_textdocument_definition(self, params: Response):
        method = "textDocument/definition"
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            view = self.session.action_target[method].view
            locations = [self._build_location(l) for l in result]
            open_location(view, locations)

    @initialize_manager.must_initialized
    def textdocument_preparerename(self, view, row, col):
        method = "textDocument/prepareRename"
        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    @initialize_manager.must_initialized
    def textdocument_rename(self, view, row, col, new_name):
        method = "textDocument/rename"

        # Save all changes before perform rename
        for document in self.session.get_documents():
            document.save()

        if document := self.session.get_document(view):
            self.session.action_target[method] = document
            self.send_request(
                method,
                {
                    "newName": new_name,
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def _handle_preparerename(self, location: dict):
        method = "textDocument/prepareRename"
        view = self.session.action_target[method].view

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

    def handle_textdocument_preparerename(self, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            self._handle_preparerename(result)

    def handle_textdocument_rename(self, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            WorkspaceEdit(self.session).apply_changes(result)


def textchange_to_rpc(text_change: TextChange) -> dict:
    """"""
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


def rpc_to_textchange(change: dict) -> TextChange:
    """"""
    return TextChange(
        LineCharacter(**change["range"]["start"]),
        LineCharacter(**change["range"]["end"]),
        change["newText"],
        change["rangeLength"],
    )


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
        self.diagnostics: Dict[sublime.View, List[dict]] = {}

        self.settings = settings or DiagnosticReportSettings()
        self.panel = DiagnosticPanel()

        self._change_lock = threading.Lock()
        self._active_view: sublime.View = None
        self._active_view_diagnostics: List[DiagnosticItem] = []

    def reset(self):
        # erase regions
        for view in self.diagnostics.keys():
            view.erase_regions(self.REGIONS_KEY)

        self._active_view = None
        self._active_view_diagnostics = []
        self.panel.destroy()
        self.diagnostics = {}

    def get(self, view: sublime.View) -> List[dict]:
        with self._change_lock:
            return self.diagnostics.get(view, [])

    def set(self, view: sublime.View, diagostics: List[dict]):
        with self._change_lock:
            self.diagnostics.update({view: diagostics})
            self._on_diagnostic_changed(view)

    def remove(self, view: sublime.View):
        with self._change_lock:
            try:
                del self.diagnostics[view]
            except KeyError:
                pass
            self._on_diagnostic_changed(view)

    def set_active_view(self, view: sublime.View):
        if view == self._active_view:
            return

        self._active_view = view
        self._on_diagnostic_changed(view)

    def get_active_view_diagnostics(
        self, filter_func: Callable[[DiagnosticItem], bool] = None
    ) -> List[DiagnosticItem]:
        if not filter_func:
            return self._active_view_diagnostics
        return [d for d in self._active_view_diagnostics if filter_func(d)]

    def _on_diagnostic_changed(self, view: sublime.View):
        diagnostics = [
            self._to_diagnostic_item(view, diagnostic)
            for diagnostic in self.diagnostics.get(view, [])
        ]

        if self.settings.highlight_text:
            self._highlight_regions(view, diagnostics)
        if self.settings.show_status:
            self._show_status(view, diagnostics)

        if view != self._active_view:
            return

        self._active_view_diagnostics = diagnostics
        if self.settings.show_panel:
            self._show_panel(view, diagnostics)

    def _to_diagnostic_item(
        self, view: sublime.View, diagnostic: dict, /
    ) -> DiagnosticItem:

        start = LineCharacter(**diagnostic["range"]["start"])
        end = LineCharacter(**diagnostic["range"]["end"])
        region = sublime.Region(view.text_point(*start), view.text_point(*end))
        message = diagnostic["message"]
        if source := diagnostic.get("source"):
            message = f"{message} ({source})"

        return DiagnosticItem(diagnostic["severity"], region, message)

    REGIONS_KEY = f"{PACKAGE_NAME}_DIAGNOSTIC_REGIONS"

    def _highlight_regions(self, view: sublime.View, diagnostics: List[DiagnosticItem]):
        regions = [item.region for item in diagnostics]
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

    def _show_status(self, view: sublime.View, diagnostics: List[DiagnosticItem]):
        value = "ERROR %s, WARNING %s"
        err_count = len([item for item in diagnostics if item.severity == 1])
        warn_count = len(diagnostics) - err_count
        view.set_status(self.STATUS_KEY, value % (err_count, warn_count))

    def _show_panel(self, view: sublime.View, diagnostics: List[DiagnosticItem]):
        def build_line(view: sublime.View, item: DiagnosticItem):
            short_name = Path(view.file_name()).name
            row, col = view.rowcol(item.region.begin())
            return f"{short_name}:{row+1}:{col} {item.message}"

        content = "\n".join([build_line(view, item) for item in diagnostics])
        self.panel.set_content(content)
        self.panel.show()


class WorkspaceEdit:

    def __init__(self, session: Session):
        self.session = session

    def apply_changes(self, edit_changes: dict) -> None:
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
        changes = [rpc_to_textchange(c) for c in edits]

        document = self.session.get_document_by_name(
            file_name, UnbufferedDocument(file_name)
        )
        document.apply_changes(changes)
        document.save()

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


def get_client() -> PyserverClient:
    """"""
    package_path = Path(sublime.packages_path(), PACKAGE_NAME)

    server_path = package_path.joinpath("pyserver")
    command = ["python", "-m", "pyserver", "-i"]
    transport = StandardIO(command, server_path)
    return PyserverClient(transport)


def get_envs_settings() -> Optional[dict]:
    """get environments defined in '*.sublime-settings'"""

    with Settings() as settings:
        if envs := settings.get("envs"):
            return envs

        sublime.active_window().run_command("pythontools_set_environment")
        return None
