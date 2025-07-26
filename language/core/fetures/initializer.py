from pathlib import Path

import sublime
import sublime_plugin

from ..lsp_client import Response
from ..session import Session, InitializeStatus
from ..uri import path_to_uri


class _InitializeCommand(sublime_plugin.TextCommand):

    client = None

    def run(self, edit: sublime.Edit):
        self.client.initialize(self.view)


class InitializerMixins:

    def initialize(self, view: sublime.View):
        # cancel if initializing
        if self.session.is_initializing():
            return

        # check if view not closed
        if view is None:
            return

        workspace_path = get_workspace_path(view)
        if not workspace_path:
            return

        self.session.set_initialize_status(InitializeStatus.Initializing)
        self.message_pool.send_request(
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

    def handle_initialize(self, session: Session, params: Response):
        if err := params.error:
            print(err["message"])
            return

        self.message_pool.send_notification("initialized", {})
        self.session.set_initialize_status(InitializeStatus.Initialized)


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
