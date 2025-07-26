from collections import namedtuple
from functools import wraps
from typing import List
import sublime
import sublime_plugin

from ...document import Document, TextChange, is_valid_document
from ...uri import path_to_uri


class DocumentSynchronizerMixins:
    client = None

    def didopen(self, view: sublime.View, *, reload: bool = False):
        if not is_valid_document(view):
            return
        self.client.textdocument_didopen(view, reload=reload)

    def didsave(self, view: sublime.View):
        if not is_valid_document(view):
            return
        self.client.textdocument_didsave(view)

    def didclose(self, view: sublime.View):
        if not is_valid_document(view):
            return
        self.client.textdocument_didclose(view)

    def didchange(self, view: sublime.View, changes: List[TextChange]):
        if not is_valid_document(view):
            return
        self.client.textdocument_didchange(view, changes)


def client_must_ready(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.client and self.client.is_ready():
            return func(self, *args, **kwargs)
        return None

    return wrapper


class DocumentSynchronizeEventListener(
    DocumentSynchronizerMixins, sublime_plugin.EventListener
):

    @client_must_ready
    def on_activated_async(self, view: sublime.View):
        self.didopen(view)

    @client_must_ready
    def on_load_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_reload_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_revert_async(self, view: sublime.View):
        self.didopen(view, reload=True)

    @client_must_ready
    def on_post_save_async(self, view: sublime.View):
        self.didsave(view)

    @client_must_ready
    def on_close(self, view: sublime.View):
        self.didclose(view)

    @client_must_ready
    def on_associate_buffer_async(self, buffer: sublime.Buffer):
        # close all associated buffer
        for view in buffer.views():
            self.didclose(view)
        # then reopen active view
        self.didopen(buffer.primary_view())


class DocumentSynchronizeTextChangeListener(
    DocumentSynchronizerMixins, sublime_plugin.TextChangeListener
):

    @client_must_ready
    def on_text_changed(self, changes: List[sublime.TextChange]):
        view = self.buffer.primary_view()
        if not is_valid_document(view):
            return
        self.didchange(view, [self.to_text_change(c) for c in changes])

    @staticmethod
    def to_text_change(change: sublime.TextChange) -> TextChange:
        """"""
        start = (change.a.row, change.a.col)
        end = (change.b.row, change.b.col)
        return TextChange(start, end, change.str, change.len_utf8)


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


def must_initialized(func):
    """exec if initialized"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self.session.is_initialized():
            return None
        return func(self, *args, **kwargs)

    return wrapper


class DocumentSynchronizerMixins:

    @must_initialized
    def textdocument_didopen(self, view: sublime.View, *, reload: bool = False):
        # check if view not closed
        if not (view and view.is_valid()):
            return

        file_name = view.file_name()
        self.session.diagnostic_manager.set_active_view(view)

        # Check if document has opened
        if self.session.get_document(file_name):
            if not reload:
                return

            # Close older document.
            self.textdocument_didclose(view)

        document = Document(view)
        self.message_pool.send_notification(
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

        # Add current document
        self.session.add_document(document)

    @must_initialized
    def textdocument_didsave(self, view: sublime.View):
        if document := self.session.get_document(view.file_name()):
            self.message_pool.send_notification(
                "textDocument/didSave",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
            )

        else:
            # untitled document not yet loaded to server
            self.textdocument_didopen(view)

    @must_initialized
    def textdocument_didclose(self, view: sublime.View):
        self.session.diagnostic_manager.remove(view)
        file_name = view.file_name()

        # if document still opened in other View
        for iterview in sublime.active_window().views():
            if iterview.file_name() == file_name:
                return

        if document := self.session.get_document(file_name):
            self.session.remove_document(file_name)
            self.message_pool.send_notification(
                "textDocument/didClose",
                {"textDocument": {"uri": path_to_uri(document.file_name)}},
            )

    @must_initialized
    def textdocument_didchange(self, view: sublime.View, changes: List[TextChange]):
        file_name = view.file_name()
        if document := self.session.get_document(file_name):
            self.message_pool.send_notification(
                "textDocument/didChange",
                {
                    "contentChanges": [textchange_to_rpc(c) for c in changes],
                    "textDocument": {
                        "uri": path_to_uri(document.file_name),
                        "version": document.version,
                    },
                },
            )


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
