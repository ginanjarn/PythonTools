import logging
from collections import namedtuple
from pathlib import Path
from typing import List

import sublime

from ....constant import LOGGING_CHANNEL
from ...document import TextChange
from ...session import Session
from ...uri import uri_to_path

LOGGER = logging.getLogger(LOGGING_CHANNEL)
PathStr = str


class WorkspaceApplyEditMixins:
    def handle_workspace_applyedit(self, session: Session, params: dict) -> dict:
        try:
            WorkspaceEdit(session).apply_changes(params["edit"])

        except Exception as err:
            LOGGER.error(err, exc_info=True)
            return {"applied": False}
        else:
            return {"applied": True}


LineCharacter = namedtuple("LineCharacter", ["line", "character"])


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

        if document := self.session.get_document(file_name):
            document.apply_changes(changes)
            document.save()

        else:
            update_document(file_name, changes)

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


class FileUpdater:
    def __init__(self, file_name: PathStr) -> None:
        self.path = Path(file_name)

    def apply(self, changes: List[TextChange]) -> None:
        old_text = self.path.read_text()
        new_text = self._update_text(old_text, changes)
        self.path.write_text(new_text)

    def _get_offset(self, lines: List[str], row: int, column: int) -> int:
        line_offset = sum([len(l) for l in lines[:row]])
        return line_offset + column

    def _update_text(self, source: str, changes: List[TextChange]) -> str:
        temp = source

        for change in changes:
            lines = temp.splitlines(keepends=True)
            start_offset = self._get_offset(lines, *change.start)
            end_offset = self._get_offset(lines, *change.end)

            temp = f"{temp[:start_offset]}{change.text}{temp[end_offset:]}"

        return temp


def update_document(file_name: PathStr, changes: List[TextChange]):
    """update document"""
    updater = FileUpdater(file_name)
    updater.apply(changes)


def create_document(file_name: PathStr, text: str = ""):
    """create document"""
    path = Path(file_name)
    path.touch()
    path.write_text(text)


def rename_document(old_name: PathStr, new_name: PathStr):
    """rename document"""
    path = Path(old_name)
    path.rename(new_name)

    # Sublime Text didn't update the view target if renamed
    for window in sublime.windows():
        for view in [v for v in window.views() if v.file_name() == old_name]:
            view.retarget(new_name)


def delete_document(file_name: PathStr):
    """delete document"""
    path = Path(file_name)
    path.unlink()

    # Sublime Text didn't close deleted file
    for window in sublime.windows():
        for view in [v for v in window.views() if v.file_name() == file_name]:
            view.close()
