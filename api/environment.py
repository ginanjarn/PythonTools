"""python environment helper"""

import json
import subprocess
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

if os.name == "nt":
    # if on Windows, hide process window
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
else:
    STARTUPINFO = None


def child_process(command: List[str]) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None,
        cwd=None,
        shell=True,
        # bufsize=0,
        startupinfo=STARTUPINFO,
    )


@dataclass
class Environment:
    pythonpath: str
    envs: Optional[Dict[str, Any]]

    def __post_init__(self):
        if isinstance(self.pythonpath, Path):
            self.pythonpath = str(self.pythonpath)

    @classmethod
    def fromdict(cls, data: dict):
        return cls(data["pythonpath"], data["envs"])


class Scanner(ABC):
    """environment scanner"""

    @abstractmethod
    def scan(self) -> Environment:
        """scan environment"""


class System(Scanner):
    """scan for system installed python"""

    def scan(self):
        for folder in os.environ["PATH"].split(os.pathsep):
            pythonpath = Path(folder).joinpath("python.exe")

            if pythonpath.is_file():
                yield Environment(pythonpath, None)


class Conda(Scanner):
    """scan conda environment"""

    @staticmethod
    def get_environment(condapath: Path, basepath: Path) -> dict:
        """get environment with 'conda activate [env]'"""

        print(f"get environment for {basepath}")

        get_env_script = "import os,json;print(json.dumps(os.environ.copy(),indent=2))"
        command = [
            # "conda",
            str(condapath),
            "activate",
            basepath,
            "&&",
            "python",
            "-c",
            get_env_script,
        ]
        process = child_process(command)
        stdout, stderr = process.communicate()
        if retcode := process.returncode:
            print(stderr.strip().decode(), file=sys.stderr)
            raise OSError(f"process terminated with exit code {retcode}")

        return json.loads(stdout)

    def scan_envs(self, condapath: Path, basepath: Path):
        folders = [path for path in basepath.glob("envs/*") if path.is_dir()]
        for folder in folders:
            pythonenvpath = folder.joinpath("python.exe")

            if pythonenvpath.is_file():
                yield Environment(
                    pythonenvpath, self.get_environment(condapath, folder)
                )

    def scan(self):
        """scan conda environment"""

        # scan at home
        home = Path().home()
        folders = [path for path in home.glob("*conda*") if path.is_dir()]
        for folder in folders:
            pythonpath = folder.joinpath("python.exe")

            if pythonpath.is_file():
                condapath = folder.joinpath("condabin", "conda")
                yield Environment(pythonpath, self.get_environment(condapath, folder))

                yield from self.scan_envs(condapath, folder)


class Venv(Scanner):
    def __init__(self, workspacepath: Path):
        self.workspacepath = Path(workspacepath)

    @staticmethod
    def get_environment(activatepath: Path, basepath: Path) -> dict:
        """get environment with 'activate'"""

        print(f"get environment for {basepath}")

        get_env_script = "import os,json;print(json.dumps(os.environ.copy(),indent=2))"
        command = [
            str(activatepath),
            "&&",
            "python",
            "-c",
            get_env_script,
        ]
        process = child_process(command)
        stdout, stderr = process.communicate()
        if retcode := process.returncode:
            print(stderr.strip().decode(), file=sys.stderr)
            raise OSError(f"process terminated with exit code {retcode}")

        return json.loads(stdout)

    def scan(self):
        folders = [path for path in self.workspacepath.iterdir() if path.is_dir()]
        for folder in folders:
            pythonpath = folder.joinpath("python.exe")
            activatepath = folder.joinpath("Scripts", "activate")

            if pythonpath.is_file():
                yield Environment(
                    pythonpath, self.get_environment(activatepath, folder)
                )
