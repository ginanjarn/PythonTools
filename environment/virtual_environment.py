"""virtual environment"""

import subprocess
import shlex
import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, Optional, Union, AnyStr


@dataclass
class EnvironmentManager:
    """Environment manager"""

    python_bin: str
    activate_command: str

    def __post_init__(self):
        # Fix TypeError if python_bin type is Path
        self.python_bin = str(self.python_bin)


@dataclass
class Global(EnvironmentManager):
    """Global environement"""


@dataclass
class Conda(EnvironmentManager):
    """Conda environment"""


@dataclass
class Venv(EnvironmentManager):
    """Venv environment"""


@dataclass
class ProcessResult:
    code: int
    stdout: str
    stderr: str


def normalize_newline(src: AnyStr) -> AnyStr:
    r"""normalize newline to '\n'"""

    if isinstance(src, bytes):
        return src.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return src.replace("\r\n", "\n").replace("\r", "\n")


if os.name == "nt":
    # if on Windows, hide process window
    STARTUPINFO = subprocess.STARTUPINFO()
    STARTUPINFO.dwFlags |= subprocess.SW_HIDE | subprocess.STARTF_USESHOWWINDOW
else:
    STARTUPINFO = None


def run_childprocess(command: Union[list, str], **kwargs) -> ProcessResult:
    if isinstance(command, str):
        command = shlex.split(command)

    print(f"execute: {shlex.join(command)}")
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO,
        shell=True,
        **kwargs,
    )

    stdout, stderr = proc.communicate()
    stdout = normalize_newline(stdout).decode()
    stderr = normalize_newline(stderr).decode()
    return ProcessResult(proc.returncode, stdout, stderr)


def get_environment(m: EnvironmentManager) -> Optional[dict]:
    """get environment"""

    if not m.activate_command:
        return None

    get_env_script = "import os,json;print(json.dumps(os.environ.copy(),indent=2))"
    get_env_command = f"'{m.python_bin}' -c '{get_env_script}'"
    command = f"{m.activate_command} && {get_env_command}"

    result = run_childprocess(command)
    if result.code == 0:
        return json.loads(result.stdout)

    print(result.stderr)
    return None


def scan(workdir: Optional[Path]) -> Iterator[EnvironmentManager]:
    """scan available environment manager"""

    yield from scan_global()
    yield from scan_conda()
    if workdir:
        yield from scan_venv(workdir)


# There some difference layout for windows and posix
if os.name == "nt":
    BIN_PATH = "Scripts"
    PYTHON_PATH = "python.exe"
    # activate environment call 'Scripts/activate'
    VENV_ACTIVATE_PREFIX = ""
else:
    BIN_PATH = "bin"
    PYTHON_PATH = os.path.join(BIN_PATH, "python")
    # activate environment call 'source bin/activate'
    VENV_ACTIVATE_PREFIX = "source "


def scan_global():
    """scan global environment"""

    for folder in os.environ["PATH"].split(os.pathsep):
        pythonpath = Path(folder, PYTHON_PATH)

        if pythonpath.is_file():
            yield Global(pythonpath, None)


def scan_conda_envs(condabin: Path, basepath: Path):
    condabin = Path(basepath, "condabin", "conda")
    folders = [path for path in basepath.glob("envs/*") if path.is_dir()]
    for folder in folders:
        pythonpath = Path(folder, PYTHON_PATH)

        if pythonpath.is_file():
            yield Conda(pythonpath, f"'{condabin}' activate '{folder}'")


def scan_conda():
    """scan conda environment"""

    # scan at home
    home = Path().home()
    folders = [path for path in home.glob("*conda*") if path.is_dir()]
    for folder in folders:
        pythonpath = Path(folder, PYTHON_PATH)

        if pythonpath.is_file():
            condabin = Path(folder, "condabin", "conda")
            yield Conda(pythonpath, f"'{condabin}' activate '{folder}'")

            yield from scan_conda_envs(condabin, folder)


def scan_venv(workdir: Path):
    """scan venv environment"""

    folders = [path for path in Path(workdir).iterdir() if path.is_dir()]
    for folder in folders:
        pythonpath = Path(folder, PYTHON_PATH)
        activatepath = Path(folder, BIN_PATH, "activate")

        if pythonpath.is_file():
            yield Venv(pythonpath, f"'{VENV_ACTIVATE_PREFIX}{activatepath}'")
