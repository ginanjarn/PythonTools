import threading
from collections import namedtuple
from functools import wraps
from html import escape as escape_html
import sublime
import sublime_plugin

from ...diagnostics import DiagnosticItem
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


class HoverEventListener(sublime_plugin.EventListener):
    client = None

    @client_must_ready
    def on_hover(self, view: sublime.View, point: int, hover_zone: sublime.HoverZone):
        if hover_zone != sublime.HoverZone.TEXT:
            return
        if not is_valid_document(view):
            return
        row, col = view.rowcol(point)
        threading.Thread(target=self._on_hover_task, args=(view, row, col)).start()

    def _on_hover_task(self, view: sublime.View, row: int, col: int):
        # Hover may be not in current active document, open it
        self.client.textdocument_didopen(view)
        self.client.textdocument_hover(view, row, col)


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentHoverMixins:

    hover_target = None

    @must_initialized
    def textdocument_hover(self, view, row, col):
        # method = "textDocument/hover"
        # In multi row/column layout, new popup will created in current View,
        # but active popup doesn't discarded.
        if other := self.hover_target:
            other.view.hide_popup()

        if document := self.session.get_document(view.file_name()):
            if message := self._get_diagnostic_message(view, row, col):
                document.show_popup(message, row, col)
                return

            self.hover_target = document
            self.message_pool.send_request(
                "textDocument/hover",
                {
                    "position": {"character": col, "line": row},
                    "textDocument": {"uri": path_to_uri(document.file_name)},
                },
            )

    def handle_textdocument_hover(self, session: Session, params: Response):
        if err := params.error:
            print(err["message"])

        elif result := params.result:
            message = result["contents"]["value"]
            row, col = LineCharacter(**result["range"]["start"])
            self.hover_target.show_popup(message, row, col)

    def _get_diagnostic_message(self, view: sublime.View, row: int, col: int):
        point = view.text_point(row, col)

        def contain_point(item: "DiagnosticItem"):
            return item.region.contains(point)

        items = self.session.diagnostic_manager.get(view, contain_point)
        if not items:
            return ""

        title = "### Diagnostics:\n"
        diagnostic_message = "\n".join([f"- {escape_html(d.message)}" for d in items])
        return f"{title}\n{diagnostic_message}"
