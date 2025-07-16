"""commands implementation"""

import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Iterator, Optional

import sublime
import sublime_plugin

from . import virtual_environment as venv


SETTINGS_BASENAME = "Python.sublime-settings"


@contextmanager
def Settings(
    *, base_name: str = SETTINGS_BASENAME, save: bool = False
) -> sublime.Settings:
    """sublime settings"""

    yield sublime.load_settings(base_name)
    if save:
        sublime.save_settings(base_name)


def get_workspace_path(view: sublime.View) -> Optional[Path]:
    try:
        file_name = Path(view.file_name())
    except TypeError:
        # file_name is None
        return None

    folders = [
        folder
        for folder in view.window().folders()
        if str(file_name).startswith(folder)
    ]
    if not folders:
        return None

    # return folder nearest to file
    return Path(max(folders))


def set_status_message(message: str):
    """set status message"""

    def func_wrapper(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            sublime.status_message(message)
            try:
                return func(*args, **kwargs)
            finally:
                sublime.status_message("Done")

        return wrapper

    return func_wrapper


class PythonToolsSetEnvironmentCommand(sublime_plugin.WindowCommand):
    """"""

    is_busy = False

    def run(self):
        if self.is_busy:
            return

        thread = threading.Thread(target=self.run_task)
        thread.start()

    def run_task(self):
        self.is_busy = True

        managers = list(self.scan_environments())
        items = [m.python_bin for m in managers]

        def on_select(index=-1):
            if index < 0:
                manager = None
            else:
                manager = managers[index]

            def _save_settings(manager):
                try:
                    self.save_settings(manager)
                finally:
                    self.is_busy = False

            # Process in thread to prevent blocking
            threading.Thread(target=_save_settings, args=(manager,)).start()

        self.window.show_quick_panel(
            items, on_select=on_select, placeholder="Select environment"
        )

    @set_status_message("Loading environment")
    def save_settings(self, manager: Optional[venv.EnvironmentManager]):
        if not manager:
            return

        pythonpath = manager.python_bin
        environment = venv.get_environment(manager)

        with Settings(save=True) as settings:
            settings.set("python", pythonpath)
            settings.set("envs", environment)

    @set_status_message("Scanning environments...")
    def scan_environments(self) -> Iterator[venv.EnvironmentManager]:
        workdir = ""
        if view := self.window.active_view():
            workdir = get_workspace_path(view)

        yield from venv.scan(workdir)
