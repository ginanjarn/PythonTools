"""virtual environment"""

import subprocess
import shutil
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
    get_env_command = f'"{m.python_bin}" -c "{get_env_script}"'
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
    PYTHON = "python.exe"
    SCRIPTS_OR_BIN = "Scripts"
    r'''activate environment exec "Scripts\activate"'''
    ACTIVATE_PREFIX = ""
else:
    PYTHON = "bin/python"
    SCRIPTS_OR_BIN = "bin"
    '''activate environment exec "source bin/activate"'''
    ACTIVATE_PREFIX = "source "


def scan_global():
    """scan global environment"""
    suffix = ".exe" if os.name == "nt" else ""
    if found := shutil.which(f"python{suffix}"):
        for python_path in found.splitlines():
            yield Global(Path(python_path), None)


def scan_conda_envs(condabin: Path, basepath: Path):
    folders = [path for path in basepath.glob("envs/*") if path.is_dir()]
    for envs_folder in folders:
        pythonpath = envs_folder / PYTHON
        if pythonpath.is_file():
            yield Conda(pythonpath, f'"{condabin}" activate "{envs_folder}"')


def scan_conda():
    """scan conda environment"""

    # scan at home
    home = Path().home()
    folders = [path for path in home.glob("*conda*") if path.is_dir()]
    for folder in folders:
        pythonpath = folder / PYTHON
        if pythonpath.is_file():
            condabin = folder / "condabin" / "conda"
            yield Conda(pythonpath, f'"{condabin}" activate "{folder}"')
            yield from scan_conda_envs(condabin, folder)


def scan_venv(workdir: Path):
    """scan venv environment"""

    folders = [path for path in Path(workdir).iterdir() if path.is_dir()]
    for folder in folders:
        pythonpath = folder / PYTHON
        activatepath = folder / SCRIPTS_OR_BIN / "activate"
        if pythonpath.is_file():
            yield Venv(pythonpath, f'"{ACTIVATE_PREFIX}{activatepath}"')
