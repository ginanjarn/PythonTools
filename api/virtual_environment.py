"""virtual environment"""

import subprocess
import shlex
import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, Optional, Union
from collections import namedtuple


@dataclass
class Manager:
    """Base Environment manager"""

    python_bin: str
    activate_command: str


ProcessResult = namedtuple("ProcessResult", ["code", "stdout", "stderr"])


def get_environment(m: Manager) -> Optional[dict]:
    """get environment"""

    if not m.activate_command:
        return None

    get_env_script = "import os,json;print(json.dumps(os.environ.copy(),indent=2))"
    command = f"{m.activate_command} && python -c '{get_env_script}'"

    result = run_childprocess(command)
    if result.code == 0:
        return json.loads(result.stdout)

    print(result.stderr)
    return None


def scan(workdir: str) -> Iterator[Manager]:
    """scan available environment manager"""
    yield from scan_system()
    yield from scan_conda()
    yield from scan_venv(Path(workdir))


class System(Manager):
    """System"""


class Conda(Manager):
    """Conda"""


class Venv(Manager):
    """Venv"""


# There some difference layout for windows and posix
if os.name == "nt":
    BINARY_PATH = "Scripts"
    PYTHONPATH = "python.exe"
    # activate environment call 'Scripts/activate'
    ACTIVATE_PREFIX = ""
else:
    BINARY_PATH = "bin"
    PYTHONPATH = BINARY_PATH + "/" + "python"
    # activate environment call 'source bin/activate'
    ACTIVATE_PREFIX = "source "  # in posix 'source bin/activate'


def scan_system():
    for folder in os.environ["PATH"].split(os.pathsep):
        pythonpath = Path(folder).joinpath("python.exe")

        if pythonpath.is_file():
            yield System(str(pythonpath), None)


def scan_conda_envs(condapath: Path, basepath: Path):
    condapath = basepath.joinpath("condabin", "conda")
    folders = [path for path in basepath.glob("envs/*") if path.is_dir()]
    for folder in folders:
        pythonpath = folder.joinpath(PYTHONPATH)

        if pythonpath.is_file():
            yield Conda(str(pythonpath), f"'{condapath}' activate '{folder}'")


def scan_conda():
    """scan conda environment"""

    # scan at home
    home = Path().home()
    folders = [path for path in home.glob("*conda*") if path.is_dir()]
    for folder in folders:
        pythonpath = folder.joinpath(PYTHONPATH)

        if pythonpath.is_file():
            condapath = folder.joinpath("condabin", "conda")
            yield Conda(str(pythonpath), f"'{condapath}' activate '{folder}'")

            yield from scan_conda_envs(condapath, folder)


def scan_venv(workdir: Path):
    folders = [path for path in workdir.iterdir() if path.is_dir()]
    for folder in folders:
        pythonpath = folder.joinpath(PYTHONPATH)
        activatepath = folder.joinpath(BINARY_PATH, "activate")

        if pythonpath.is_file():
            yield Venv(pythonpath, f"'{ACTIVATE_PREFIX}{activatepath}'")


if os.name == "nt":
    # if on Windows, hide process window
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
else:
    STARTUPINFO = None


def run_childprocess(command: Union[list, str]) -> ProcessResult:
    if isinstance(command, str):
        command = shlex.split(command)

    print(f"execute: {shlex.join(command)}")
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO,
        shell=True,
    )

    stdout, stderr = proc.communicate()
    code = proc.poll()
    stdout = stdout.replace(b"\r\n", b"\n").decode() if stdout else ""
    stderr = stderr.replace(b"\r\n", b"\n").decode() if stderr else ""
    return ProcessResult(code, stdout, stderr)
