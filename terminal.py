"""terminal commands"""

from pathlib import Path
import threading
import subprocess

import sublime
import sublime_plugin
from .api.sublime_settings import Settings


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


class PythontoolsOpenTerminalCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit, on_active_document: bool = False):
        if on_active_document:
            cwd = Path(self.view.file_name()).parent
        else:
            cwd = get_workspace_path(self.view)

        with Settings() as settings:
            env = settings.get("envs")

        thread = threading.Thread(target=self.open_terminal, args=(cwd, env))
        thread.start()

    def open_terminal(self, cwd=None, env=None):
        subprocess.Popen(["cmd.exe"], cwd=cwd, env=env)
