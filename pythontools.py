"""Python tools for Sublime Text"""

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from functools import wraps
from io import StringIO
from pathlib import Path
from typing import List, Dict, Optional, Any

import sublime
import sublime_plugin
from sublime import HoverZone

from .api import lsp_client
from .api.sublime_settings import Settings
from . import workspace
from .workspace import (
    BufferedDocument,
    TextChange,
    TextHighlighter,
    UnbufferedDocument,
    Workspace,
)

PathStr = str

PACKAGE_NAME = str(Path(__file__).parent)
LOGGING_CHANNEL = "pythontools"
LOGGER = logging.getLogger(LOGGING_CHANNEL)


def setup_logger(level: int):
    """"""
    LOGGER.setLevel(level)
    fmt = logging.Formatter("%(levelname)s %(filename)s:%(lineno)d  %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    LOGGER.addHandler(sh)


# custom kind
KIND_PATH = (sublime.KIND_ID_VARIABLE, "p", "")
KIND_VALUE = (sublime.KIND_ID_VARIABLE, "u", "")
KIND_TEXT = (sublime.KIND_ID_VARIABLE, "t", "")
COMPLETION_KIND_MAP = defaultdict(
    lambda _: sublime.KIND_AMBIGUOUS,
    {
        1: KIND_TEXT,  # text
        2: sublime.KIND_FUNCTION,  # method
        3: sublime.KIND_FUNCTION,  # function
        4: sublime.KIND_FUNCTION,  # constructor
        5: sublime.KIND_VARIABLE,  # field
        6: sublime.KIND_VARIABLE,  # variable
        7: sublime.KIND_TYPE,  # class
        8: sublime.KIND_TYPE,  # interface
        9: sublime.KIND_NAMESPACE,  # module
        10: sublime.KIND_VARIABLE,  # property
        11: KIND_VALUE,  # unit
        12: KIND_VALUE,  # value
        13: sublime.KIND_NAMESPACE,  # enum
        14: sublime.KIND_KEYWORD,  # keyword
        15: sublime.KIND_SNIPPET,  # snippet
        16: KIND_VALUE,  # color
        17: KIND_PATH,  # file
        18: sublime.KIND_NAVIGATION,  # reference
        19: KIND_PATH,  # folder
        20: sublime.KIND_VARIABLE,  # enum member
        21: sublime.KIND_VARIABLE,  # constant
        22: sublime.KIND_TYPE,  # struct
        23: sublime.KIND_MARKUP,  # event
        24: sublime.KIND_MARKUP,  # operator
        25: sublime.KIND_TYPE,  # type parameter
    },
)


class DiagnosticPanel:
    OUTPUT_PANEL_NAME = f"{PACKAGE_NAME}_PANEL"
    SETTINGS = {"gutter": False, "word_wrap": False}

    def __init__(self):
        self.panel: sublime.View = None

    def _create_panel(self):
        self.panel = sublime.active_window().create_output_panel(self.OUTPUT_PANEL_NAME)
        self.panel.settings().update(self.SETTINGS)
        self.panel.set_read_only(False)

    def set_content(self, text: str):
        if not (self.panel and self.panel.is_valid()):
            self._create_panel()

        # clear content
        self.panel.run_command("select_all")
        self.panel.run_command("left_delete")

        self.panel.run_command("append", {"characters": text})

    def show(self) -> None:
        """show output panel"""
        sublime.active_window().run_command(
            "show_panel", {"panel": f"output.{self.OUTPUT_PANEL_NAME}"}
        )

    def destroy(self):
        """destroy output panel"""
        for window in sublime.windows():
            window.destroy_output_panel(self.OUTPUT_PANEL_NAME)


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


@dataclass
class ActionTarget:
    hover: BufferedDocument = None
    completion: BufferedDocument = None
    signature_help: BufferedDocument = None
    formatting: BufferedDocument = None
    definition: BufferedDocument = None
    rename: BufferedDocument = None


class PyserverHandler(lsp_client.BaseHandler):
    """"""

    session = Session()

    def __init__(self, transport: lsp_client.Transport):
        self.transport = transport
        self.client = lsp_client.Client(self.transport, self)

        # workspace status
        self._initializing = False
        self.workspace = Workspace()

        self.diagnostics_panel = DiagnosticPanel()

        # commands document target
        self.action_target = ActionTarget()

    def _reset_state(self):
        self._initializing = False
        self.workspace = Workspace()
        self.diagnostics_panel.destroy()
        TextHighlighter.clear_all()

        # commands document target
        self.action_target = ActionTarget()
        self.session.done()

    @staticmethod
    def get_settings_envs() -> Optional[dict]:
        with Settings() as settings:
            if envs := settings.get("envs"):
                return envs

            sublime.active_window().run_command("pythontools_set_environment")
            return None

    def is_ready(self) -> bool:
        return self.client.is_server_running() and self.session.is_begin()

    run_server_lock = threading.Lock()

    def run_server(self):
        # only one thread can run server
        if self.run_server_lock.locked():
            return

        with self.run_server_lock:
            if not self.client.is_server_running():
                sublime.status_message("running pyserver...")
                # sometimes the server stop working
                # we must reset the state before run server
                self._reset_state()

                self.client.run_server(self.get_settings_envs())
                self.client.listen()

    def terminate(self):
        """exit session"""
        self.client.terminate_server()
        self._reset_state()

    def active_window(self) -> sublime.Window:
        return sublime.active_window()

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

    @session.must_begin
    def textdocument_didchange(self, view: sublime.View, changes: List[dict]):
        # Document can be related to multiple View but has same file_name.
        # Use get_document_by_name() because may be document already open
        # in other view and the argument view not assigned.
        file_name = view.file_name()
        if document := self.workspace.get_document_by_name(file_name):
            self.client.send_notification(
                "textDocument/didChange",
                {
                    "contentChanges": changes,
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

    def _open_locations(self, locations: List[dict]):
        current_view = self.action_target.definition.view
        current_sel = tuple(current_view.sel())
        visible_region = current_view.visible_region()

        def restore_selection():
            sublime.active_window().focus_view(current_view)
            current_view.sel().clear()
            current_view.sel().add_all(current_sel)
            current_view.show(visible_region, show_surrounds=False)

        def build_location(location: dict):
            file_name = lsp_client.uri_to_path(location["uri"])
            row = location["range"]["start"]["line"]
            col = location["range"]["start"]["character"]
            return f"{file_name}:{row+1}:{col+1}"

        locations = [build_location(l) for l in locations]
        locations.sort()

        def open_location(index):
            if index < 0:
                restore_selection()
                return

            flags = sublime.ENCODED_POSITION
            sublime.active_window().open_file(locations[index], flags=flags)

        def preview_location(index):
            flags = sublime.ENCODED_POSITION | sublime.TRANSIENT
            sublime.active_window().open_file(locations[index], flags=flags)

        sublime.active_window().show_quick_panel(
            items=locations,
            on_select=open_location,
            flags=sublime.MONOSPACE_FONT,
            on_highlight=preview_location,
            placeholder="Open location...",
        )

    def handle_textdocument_definition(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._open_locations(result)

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

    def _input_rename(self, symbol_location: dict):
        view = self.action_target.rename.view

        start = symbol_location["range"]["start"]
        start_point = view.text_point(start["line"], start["character"])
        end = symbol_location["range"]["end"]
        end_point = view.text_point(end["line"], end["character"])

        region = sublime.Region(start_point, end_point)
        old_name = view.substr(region)

        def request_rename(new_name):
            self.textdocument_rename(new_name, start["line"], start["character"])

        sublime.active_window().show_input_panel(
            caption="rename",
            initial_text=old_name,
            on_done=request_rename,
            on_change=None,
            on_cancel=None,
        )

    def handle_textdocument_preparerename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._input_rename(result)

    def handle_textdocument_rename(self, params: dict):
        if error := params.get("error"):
            print(error["message"])
        elif result := params.get("result"):
            self._apply_edit(result)


HANDLER: PyserverHandler = None


def setup_handler():
    """"""
    global HANDLER

    # pyserver path defined here because it located relativeto this file
    server_path = Path(__file__).parent.joinpath("pyserver")
    command = ["python", "-m", "pyserver", "-i"]
    transport = lsp_client.StandardIO(command, server_path)
    HANDLER = PyserverHandler(transport)


def plugin_loaded():
    """plugin entry point"""
    setup_logger(logging.ERROR)
    setup_handler()


def plugin_unloaded():
    """executed before plugin unloaded"""
    if HANDLER:
        HANDLER.terminate()


def valid_context(view: sublime.View, point: int):
    if not (view and view.is_valid()):
        return False

    # Console is valid selector for 'source.python' but the file_name is None.
    if not view.file_name():
        return False
    return view.match_selector(point, "source.python")


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()
    if not file_name:
        return ""

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


class EventListener(sublime_plugin.EventListener):
    def __init__(self):
        self.prev_completion_point = 0

    def on_hover(self, view: sublime.View, point: int, hover_zone: HoverZone):
        # check point in valid source
        if not (valid_context(view, point) and hover_zone == sublime.HOVER_TEXT):
            return

        row, col = view.rowcol(point)
        if HANDLER.is_ready():
            HANDLER.textdocument_hover(view, row, col)

        else:
            threading.Thread(target=self._on_hover, args=(view, row, col)).start()

    def _on_hover(self, view, row, col):

        # initialize server if not ready
        if not HANDLER.is_ready():
            HANDLER.run_server()
            HANDLER.initialize(view)

        # on multi column layout, sometime we hover on other document which may
        # not loaded yet
        HANDLER.textdocument_didopen(view)
        # request on hover
        HANDLER.textdocument_hover(view, row, col)

    def on_query_completions(
        self, view: sublime.View, prefix: str, locations: List[int]
    ) -> sublime.CompletionList:
        if not HANDLER.is_ready():
            return None

        point = locations[0]

        # check point in valid source
        if not valid_context(view, point):
            return None

        if (
            document := HANDLER.action_target.completion
        ) and document.is_completion_available():
            word = view.word(self.prev_completion_point)
            # point unchanged
            if point == self.prev_completion_point:
                show = True
            # point changed but still in same word
            elif view.substr(word).isidentifier() and point in word:
                show = True
            else:
                show = False

            if (cache := document.pop_completion()) and show:
                return sublime.CompletionList(
                    cache, flags=sublime.INHIBIT_WORD_COMPLETIONS
                )

            document.hide_completion()
            return None

        self.prev_completion_point = point
        row, col = view.rowcol(point)

        HANDLER.textdocument_completion(view, row, col)
        view.run_command("hide_auto_complete")

        sublime.set_timeout_async(
            self._trigger_signaturehelp(view, point, row, col), 0.5
        )
        return None

    def _trigger_signaturehelp(
        self, view: sublime.View, point: int, row: int, col: int
    ):
        # Some times server response signaturehelp after cursor moved.
        view.hide_popup()

        # Only request signature on function arguments
        if not view.match_selector(point, "meta.function-call.arguments"):
            return

        HANDLER.textdocument_signaturehelp(view, row, col)

    def on_activated_async(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view)
            return

        if LOGGER.level == logging.DEBUG:
            return

        # initialize server
        HANDLER.run_server()
        HANDLER.initialize(view)
        HANDLER.textdocument_didopen(view)

    def on_post_save_async(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didsave(view)

    def on_close(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didclose(view)

    def on_load(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def on_reload(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)

    def on_revert(self, view: sublime.View):
        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didopen(view, reload=True)


class TextChangeListener(sublime_plugin.TextChangeListener):
    def on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()

        # check point in valid source
        if not valid_context(view, 0):
            return

        if HANDLER.is_ready():
            HANDLER.textdocument_didchange(
                view, [self.change_as_rpc(c) for c in changes]
            )

    @staticmethod
    def change_as_rpc(change: sublime.TextChange) -> dict:
        start = change.a
        end = change.b
        return {
            "range": {
                "end": {"character": end.col, "line": end.row},
                "start": {"character": start.col, "line": start.row},
            },
            "rangeLength": change.len_utf8,
            "text": change.str,
        }


class PythontoolsDocumentFormattingCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit):
        if HANDLER.is_ready():
            HANDLER.textdocument_formatting(self.view)

    def is_visible(self):
        return valid_context(self.view, 0)


class PythontoolsGotoDefinitionCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_definition(self.view, start_row, start_col)

    def is_visible(self):
        return valid_context(self.view, 0)

    def want_event(self):
        return True


class PythontoolsRenameCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, event: Optional[dict] = None):
        cursor = self.view.sel()[0]
        point = event["text_point"] if event else cursor.a
        if HANDLER.is_ready():
            # move cursor to point
            self.view.sel().clear()
            self.view.sel().add(point)

            start_row, start_col = self.view.rowcol(point)
            HANDLER.textdocument_preparerename(self.view, start_row, start_col)

    def is_visible(self):
        return valid_context(self.view, 0)

    def want_event(self):
        return True


class PythontoolsTerminateCommand(sublime_plugin.WindowCommand):
    def run(self):
        if HANDLER:
            HANDLER.terminate()

    def is_visible(self):
        return HANDLER and HANDLER.is_ready()
