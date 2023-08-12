"""envoronment settings helper"""

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Iterable

import sublime
import sublime_plugin

from .api import virtual_environment as venv
from .api.sublime_settings import Settings


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


class PythontoolsSetEnvironmentCommand(sublime_plugin.WindowCommand):
    def run(self, scan=False):
        thread = threading.Thread(target=self._run, args=(scan,))
        thread.start()

    def _run(self, scan=False):
        managers = list(self.load_cache())
        if not managers or scan:
            managers = list(self.scan_managers())
            self.save_cache(managers)

        titles = [m.python_bin for m in managers]
        titles.append("Scan environments...")

        def select_item(index=-1):
            if index < 0:
                return
            elif index == len(managers):
                self.window.run_command("pythontools_set_environment", {"scan": True})
                return

            pythonpath = managers[index].python_bin
            environment = venv.get_environment(managers[index])

            with Settings(save=True) as settings:
                settings.set("python", pythonpath)
                settings.set("envs", environment)

        self.window.show_quick_panel(titles, on_select=select_item)

    def scan_managers(self) -> Iterator[venv.Manager]:
        workdir = get_workspace_path(self.window.active_view())
        yield from venv.scan(workdir)

    cache_path = Path(__file__).parent.joinpath("var/environment_managers.json")

    def load_cache(self) -> Iterator[venv.Manager]:
        if not self.cache_path.is_file():
            return

        data = json.loads(self.cache_path.read_text())
        for item in data:
            yield venv.Manager(
                python_bin=item["python_bin"],
                activate_command=item["activate_command"],
            )

    def save_cache(self, managers: Iterable[venv.Manager]) -> None:
        dict_managers = [asdict(m) for m in managers]

        cache_dir = self.cache_path.parent
        if not cache_dir.is_dir():
            cache_dir.mkdir(parents=True)

        data = json.dumps(dict_managers, indent=2)
        self.cache_path.write_text(data)
