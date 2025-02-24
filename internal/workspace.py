"""Workspace module"""

from pathlib import Path

import sublime


PathStr = str


def get_workspace_path(view: sublime.View, return_parent: bool = True) -> str:
    """Get workspace path for view.

    Params:
        view: View
            target
        return_parent: bool
            if True, return parent folder if view not opened in 'Window folders'

    Returns:
        workspace path or empty string
    """
    file_name = view.file_name()
    if not file_name:
        return ""

    if folders := [
        folder for folder in view.window().folders() if file_name.startswith(folder)
    ]:
        # File is opened in multiple folder
        return max(folders)

    if not return_parent:
        return ""

    return str(Path(file_name).parent)


def open_document(file_name: PathStr, preview: bool = False):
    """open document"""
    flags = sublime.ENCODED_POSITION
    if preview:
        flags |= sublime.TRANSIENT

    sublime.active_window().open_file(file_name, flags=flags)


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
