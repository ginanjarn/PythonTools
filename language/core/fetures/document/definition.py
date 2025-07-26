from collections import namedtuple
from functools import wraps
from typing import Optional, List
import sublime
import sublime_plugin

from ...document import is_valid_document
from ...lsp_client import Response
from ...session import Session
from ...uri import path_to_uri, uri_to_path


def client_must_ready(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.client and self.client.is_ready():
            return func(self, *args, **kwargs)
        return None

    return wrapper


class _GotoDefinitionCommand(sublime_plugin.TextCommand):
    client = None

    @client_must_ready
    def run(
        self,
        edit: sublime.Edit,
        row: int = -1,
        column: int = -1,
        event: Optional[dict] = None,
    ):
        if not is_valid_document(self.view):
            return

        if event:
            point = event["text_point"]
        else:
            point = self.view.sel()[0].begin()

        if row < 0:
            row, column = self.view.rowcol(point)

        self.client.textdocument_definition(self.view, row, column)

    def is_visible(self):
        return is_valid_document(self.view)

    def want_event(self):
        return True


PathEncodedStr = str
"""Path encoded '<file_name>:<row>:<column>'"""
LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentDefinitionMixins:

    definition_target = None

    @must_initialized
    def textdocument_definition(self, view, row, col):
        if document := self.session.get_document(view.file_name()):
            self.definition_target = document
            self.message_pool.send_request(
                "textDocument/definition",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_definition(self, session: Session, params: Response):
        if error := params.error:
            print(error["message"])
        elif result := params.result:
            view = self.definition_target.view
            locations = [self._build_location(l) for l in result]
            open_location(view, locations)

    @staticmethod
    def _build_location(location: dict) -> PathEncodedStr:
        file_name = uri_to_path(location["uri"])
        start_row, start_col = LineCharacter(**location["range"]["start"])
        return f"{file_name}:{start_row+1}:{start_col+1}"


def set_selection(view: sublime.View, regions: List[sublime.Region]):
    """"""
    view.sel().clear()
    view.sel().add_all(regions)


def open_document(
    window: sublime.Window, file_name: PathEncodedStr, preview: bool = False
):
    """open document"""
    flags = sublime.ENCODED_POSITION
    if preview:
        flags |= sublime.TRANSIENT

    window.open_file(file_name, flags=flags)


def open_location(current_view: sublime.View, locations: List[PathEncodedStr]) -> None:
    """"""
    window = current_view.window()
    current_selections = list(current_view.sel())
    current_visible_region = current_view.visible_region()

    locations = sorted(locations)

    def open_location(index):
        if index >= 0:
            open_document(window, locations[index])
            return

        # else: revert to current state
        current_view.window().focus_view(current_view)
        set_selection(current_view, current_selections)
        current_view.show(current_visible_region, show_surrounds=False)

    def preview_location(index):
        open_document(window, locations[index], preview=True)

    window.show_quick_panel(
        items=locations,
        on_select=open_location,
        flags=sublime.MONOSPACE_FONT,
        on_highlight=preview_location,
        placeholder="Open location...",
    )
