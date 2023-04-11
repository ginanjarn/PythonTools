"""envoronment settings helper"""

import json
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from itertools import chain
from typing import Dict, Any, List

import sublime
import sublime_plugin

from .api.environment import Environment, System, Conda, Venv
from .api.sublime_settings import Settings


@dataclass
class InterpreterSettings:
    python_path: str
    envs: Dict[str, Any]


def get_workspace_path(view: sublime.View) -> str:
    window = view.window()
    file_name = view.file_name()

    if folders := [
        folder for folder in window.folders() if file_name.startswith(folder)
    ]:
        return max(folders)
    return str(Path(file_name).parent)


class PythontoolsSetEnvironmentCommand(sublime_plugin.WindowCommand):
    def run(self, scan: bool = False):
        threading.Thread(target=self.show_environments, args=(scan,)).start()

    def show_environments(self, scan: bool = False):
        if scan:
            envs = self.scan_environments()
        else:
            envs = self.load_cached_environments()

        interpreters = [env.pythonpath for env in envs]
        interpreters.append("Scan Environments...")

        def select_environment(index=-1):
            if index < 0:
                return

            try:
                self.set(envs[index])
            except IndexError:
                self.window.run_command("pythontools_set_environment", {"scan": True})

        self.window.show_quick_panel(interpreters, on_select=select_environment)

    def set(self, env: Environment):
        with Settings(save=True) as settings:
            settings.set("python", env.pythonpath)
            settings.set("envs", env.envs)

    def scan_environments(self) -> List[Environment]:
        sublime.status_message("scanning environments...")

        workspace = get_workspace_path(self.window.active_view())
        envs = list(chain(System().scan(), Conda().scan(), Venv(workspace).scan()))

        self._write_cache(envs)
        return envs

    cache_path = Path(__file__).parent.joinpath("var", "environments.json")

    def _write_cache(self, envs: List[Environment]):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        norm_envs = [asdict(env) for env in envs]
        self.cache_path.write_text(json.dumps(norm_envs, indent=2))

    def load_cached_environments(self) -> List[Environment]:
        try:
            jstr = self.cache_path.read_text()
            data = json.loads(jstr)

            return [Environment.fromdict(env) for env in data]

        except (FileNotFoundError, json.JSONDecodeError):
            return self.scan_environments()
