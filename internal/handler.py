"""handler"""

import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, List, Callable

import sublime

from . import errors
from . import lsp_client
from . import workspace
from .constant import PACKAGE_NAME
from .workspace import (
    BufferedDocument,
    Workspace,
    TextChange,
    TextHighlighter,
)

PathStr = str
PathEncodedStr = str
"""Path encoded '<file_name>:<row>:<column>'"""


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


@dataclass
class ActionTarget:
    hover: BufferedDocument = None
    completion: BufferedDocument = None
    signature_help: BufferedDocument = None
    formatting: BufferedDocument = None
    definition: BufferedDocument = None
    rename: BufferedDocument = None


class BaseHandler(lsp_client.Handler):
    """Base handler"""

    def handle(self, method: str, params: dict) -> Optional[dict]:
        norm_method = f"handle_{method}".replace("/", "_").replace(".", "_").lower()
        try:
            func = getattr(self, norm_method)
        except AttributeError as err:
            raise errors.MethodNotFound(method) from err

        return func(params)

    def __init__(self, transport: lsp_client.Transport):
        self.transport = transport
        self.client = lsp_client.Client(self.transport, self)

        # workspace status
        self._initializing = False
        self.workspace = Workspace()

        self.diagnostics_panel = DiagnosticPanel()

        # commands document target
        self.action_target = ActionTarget()
        self.run_server_lock = threading.Lock()

    def _reset_state(self) -> None:
        self._initializing = False
        self.workspace = Workspace()
        self.diagnostics_panel.destroy()
        TextHighlighter.clear_all()

        # commands document target
        self.action_target = ActionTarget()
        self.session.done()

    def run_server(self, env: Optional[dict] = None) -> None:
        # only one thread can run server
        if self.run_server_lock.locked():
            return

        with self.run_server_lock:
            if not self.client.is_server_running():
                sublime.status_message("running pyserver...")
                # sometimes the server stop working
                # we must reset the state before run server
                self._reset_state()

                self.client.run_server(env)
                self.client.listen()

    @staticmethod
    def _open_locations(view: sublime.View, locations: List[PathEncodedStr]) -> None:
        """"""
        current_selections = tuple(view.sel())
        current_visible_region = view.visible_region()

        def set_selection(view, selections, visible_region):
            view.window().focus_view(view)
            view.sel().clear()
            view.sel().add_all(selections)
            view.show(visible_region, show_surrounds=False)

        locations = sorted(locations)

        def open_location(index):
            if index < 0:
                # canceled
                set_selection(view, current_selections, current_visible_region)
                return

            workspace.open_document(locations[index])

        def preview_location(index):
            workspace.open_document(locations[index], preview=True)

        sublime.active_window().show_quick_panel(
            items=locations,
            on_select=open_location,
            flags=sublime.MONOSPACE_FONT,
            on_highlight=preview_location,
            placeholder="Open location...",
        )

    @staticmethod
    def _input_rename(old_name: str, on_done_callback: Callable[[str], None]) -> None:
        """"""
        sublime.active_window().show_input_panel(
            caption="rename",
            initial_text=old_name,
            on_done=on_done_callback,
            on_change=None,
            on_cancel=None,
        )

    def is_ready(self) -> bool:
        raise NotImplementedError("is_ready")

    def terminate(self):
        raise NotImplementedError("terminate")

    def initialize(self, view: sublime.View) -> None: ...
    def textdocument_didopen(
        self, view: sublime.View, *, reload: bool = False
    ) -> None: ...
    def textdocument_didsave(self, view: sublime.View) -> None: ...
    def textdocument_didclose(self, view: sublime.View) -> None: ...
    def textdocument_didchange(
        self, view: sublime.View, changes: List[TextChange]
    ) -> None: ...
    def textdocument_hover(self, view: sublime.View, row: int, col: int) -> None: ...
    def textdocument_completion(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_signaturehelp(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_formatting(self, view: sublime.View) -> None: ...
    def textdocument_definition(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_preparerename(
        self, view: sublime.View, row: int, col: int
    ) -> None: ...
    def textdocument_rename(self, new_name: str, row: int, col: int) -> None: ...
