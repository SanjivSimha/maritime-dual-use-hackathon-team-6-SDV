"""tools/demo.ps1 uv discovery, exercised by running Windows PowerShell for real.

Windows only (the launcher is a Windows PowerShell 5.1 script); skipped elsewhere, including CI.
Each case runs in an isolated temporary copy whose path contains spaces, with an environment
built for the case: the caller's environment, uv cache and venvs are not modified. Launches use
the existing uv cache read-only and offline; only processes started here are stopped.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
POWERSHELL = SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
DEAD_PROXY = "http://127.0.0.1:9"  # any network attempt fails fast


def real_uv() -> Path | None:
    """An installed uv executable: UV (set by `uv run`), PATH, or the base `py -m uv` package."""
    for candidate in (os.environ.get("UV"), shutil.which("uv")):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    if shutil.which("py"):
        env = {k: v for k, v in os.environ.items() if k.upper() != "VIRTUAL_ENV"}
        probe = subprocess.run(
            ["py", "-c", "import uv; print(uv.find_uv_bin())"],
            capture_output=True,
            text=True,
            env=env,
        )
        if probe.returncode == 0:
            return Path(probe.stdout.strip())
    return None


def py_launcher_dir() -> Path | None:
    found = shutil.which("py")
    return Path(found).parent if found else None


def no_uv_venv(tmp_path: Path) -> Path:
    """A real virtual environment with no uv in it (what a user may have activated)."""
    venv = tmp_path / "active venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    return venv


def isolated_env(venv: Path, extra_path=()) -> dict:
    """Plain-shell environment: no uv on PATH, an active venv without uv, no network."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {"PATH", "VIRTUAL_ENV", "UV"} and not k.upper().startswith("UV_")
    }
    path = [str(venv / "Scripts"), str(SYSTEM32), str(POWERSHELL.parent), *map(str, extra_path)]
    env.update(
        PATH=os.pathsep.join(path),
        VIRTUAL_ENV=str(venv),
        HTTP_PROXY=DEAD_PROXY,
        HTTPS_PROXY=DEAD_PROXY,
        ALL_PROXY=DEAD_PROXY,
    )
    return env


def launch(script: Path, env: dict, cwd: Path, *args, wait_s=120):
    return subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=wait_s,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve_until_ready(script: Path, env: dict, cwd: Path, port: int):
    """Start the launcher, wait for GET / == 200, then stop only this process tree."""
    import httpx

    out = cwd / "launcher.log"
    with out.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Port",
                str(port),
            ],
            cwd=cwd,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, out.read_text(encoding="utf-8", errors="replace")
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{port}/", timeout=0.5, trust_env=False).status_code
                    == 200
                ):
                    return True, out.read_text(encoding="utf-8", errors="replace")
            except httpx.TransportError:
                time.sleep(0.2)
        return False, out.read_text(encoding="utf-8", errors="replace")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def copy_repo(dest: Path) -> Path:
    """Just what the launcher and server need, under a path with spaces."""
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(REPO / name, dest / name)
    for name in ("shipos", "data", "tools"):
        shutil.copytree(REPO / name, dest / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "tests" / "fixtures", dest / "tests" / "fixtures")
    return dest / "tools" / "demo.ps1"


@pytest.fixture
def launcher_only(tmp_path):
    root = tmp_path / "repo with spaces"
    (root / "tools").mkdir(parents=True)
    shutil.copy2(REPO / "tools" / "demo.ps1", root / "tools" / "demo.ps1")
    return root / "tools" / "demo.ps1"


def test_failed_py_probe_without_candidates_gives_guidance(tmp_path, launcher_only):
    py_dir = py_launcher_dir()
    if py_dir is None:
        pytest.skip("the py launcher is needed to reproduce the failing probe")
    env = isolated_env(no_uv_venv(tmp_path), extra_path=[py_dir])
    elsewhere = tmp_path / "somewhere else"
    elsewhere.mkdir()
    result = launch(launcher_only, env, elsewhere, "-Port", str(free_port()))
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "shipos demo: no uv found" in output and "-Prepare" in output
    assert "NativeCommandError" not in output and "No module named uv" not in output


def test_failed_py_probe_falls_back_to_repository_uv(tmp_path):
    uv, py_dir = real_uv(), py_launcher_dir()
    if uv is None or py_dir is None:
        pytest.skip("needs an installed uv (copied as .tools\\uv.exe) and the py launcher")
    root = tmp_path / "repo with spaces"
    root.mkdir()
    script = copy_repo(root)
    (root / ".tools").mkdir()
    shutil.copy2(uv, root / ".tools" / "uv.exe")
    env = isolated_env(no_uv_venv(tmp_path), extra_path=[py_dir])
    elsewhere = tmp_path / "somewhere else"
    elsewhere.mkdir()
    ok, log = serve_until_ready(script, env, elsewhere, free_port())
    assert ok, log  # offline (dead proxy), from outside the repository, path with spaces
    assert "NativeCommandError" not in log


def test_normal_uv_launch_still_works(tmp_path):
    uv = real_uv()
    if uv is None:
        pytest.skip("needs an installed uv")
    root = tmp_path / "repo with spaces"
    root.mkdir()
    script = copy_repo(root)
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() != "VIRTUAL_ENV" and k.upper() != "UV" and not k.upper().startswith("UV_")
    }
    env["PATH"] = os.pathsep.join([str(uv.parent), str(SYSTEM32), str(POWERSHELL.parent)])
    env.update(HTTP_PROXY=DEAD_PROXY, HTTPS_PROXY=DEAD_PROXY, ALL_PROXY=DEAD_PROXY)
    ok, log = serve_until_ready(script, env, root, free_port())
    assert ok, log
