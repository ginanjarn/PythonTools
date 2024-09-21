"""envoronment settings helper"""

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Iterable

import sublime
import sublime_plugin

from .internal import virtual_environment as venv
from .internal.sublime_settings import Settings


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


class PythonToolsSetEnvironmentCommand(sublime_plugin.WindowCommand):
    """"""

    def run(self, scan: bool = False):
        thread = threading.Thread(target=self.run_task, args=(scan,))
        thread.start()

    def run_task(self, scan: bool = False):
        managers = list(self.load_cache())
        if not managers or scan:
            managers = list(self.scan_managers())
            self.save_cache(managers)

        items = [m.python_bin for m in managers]

        # Set as last item
        items.append("Scan environments...")
        scan_environments_index = len(items) - 1

        def on_select(index=-1):
            if index < 0:
                return

            elif index == scan_environments_index:
                self.window.run_command("pythontools_set_environment", {"scan": True})
                return

            # Process in thread to prevent blocking
            threading.Thread(target=self.save_settings, args=(managers[index],)).start()

        self.window.show_quick_panel(items, on_select=on_select)

    def save_settings(self, manager: venv.EnvironmentManager):
        pythonpath = manager.python_bin
        environment = venv.get_environment(manager)

        with Settings(save=True) as settings:
            settings.set("python", pythonpath)
            settings.set("envs", environment)

    def scan_managers(self) -> Iterator[venv.EnvironmentManager]:
        workdir = ""
        if view := self.window.active_view():
            workdir = get_workspace_path(view)

        yield from venv.scan(workdir)

    cache_path = Path(__file__).parent.joinpath("var/environment_managers.json")

    def load_cache(self) -> Iterator[venv.EnvironmentManager]:
        try:
            data = json.loads(self.cache_path.read_text())
            yield from (venv.EnvironmentManager(**item) for item in data["items"])

        except Exception:
            pass

    def save_cache(self, managers: Iterable[venv.EnvironmentManager]) -> None:
        items = [asdict(m) for m in managers]
        data = json.dumps({"items": items}, indent=2)

        cache_dir = self.cache_path.parent
        if not cache_dir.is_dir():
            cache_dir.mkdir(parents=True)

        self.cache_path.write_text(data)
