"""Fixed-port MinerU api-server lifecycle for batch ``standard`` conversions.

Starting a MinerU VLM costs seconds, so a batch that needs ``standard`` starts
*one* ``mineru-kit api-server`` on a dedicated loopback port, parses every PDF
through ``--remote-url``, then kills the server by process group.

Ownership is tracked with a pid file so a foreign process on the port is never
killed or blindly reused: we reuse only a *healthy* MinerU api-server that our
pid file says we own, otherwise we refuse and report.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
DEFAULT_SERVER_TIER = "standard"
DEFAULT_STARTUP_TIMEOUT = 240.0
HEALTH_POLL_INTERVAL = 1.0
TERM_GRACE_SECONDS = 10.0
PID_FILE_DIRNAME = "pdf-extract"


class MineruServerError(Exception):
    """Raised when the shared api-server cannot be started or safely reused."""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def ss_reports_listen(ss_output: str, port: int) -> bool:
    """True if ``ss -ltn`` output shows a listener on ``port``."""
    suffix = f":{port}"
    for line in ss_output.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "LISTEN":
            continue
        if parts[3].endswith(suffix):
            return True
    return False


def pid_file_path(port: int = DEFAULT_PORT) -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / PID_FILE_DIRNAME / f"api-server-{port}.pid"


def read_pid_file(path: Path) -> Optional[int]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_pid_file(path: Path, pid: int, port: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": pid, "port": port}), encoding="utf-8")


def remove_pid_file(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_is_mineru_api_server(pid: int) -> bool:
    """Best-effort ownership check via ``/proc/<pid>/cmdline``."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return False
    return "api-server" in cmdline and "mineru" in cmdline


# --------------------------------------------------------------------------- #
# Side-effecting probes (injectable on the class)
# --------------------------------------------------------------------------- #


def port_is_listening(port: int, host: str = DEFAULT_HOST) -> bool:
    output = _run_ss()
    if output is not None:
        return ss_reports_listen(output, port)
    return _socket_port_open(host, port)


def _run_ss() -> Optional[str]:
    try:
        completed = subprocess.run(
            ["ss", "-ltn"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _socket_port_open(host: str, port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def health_ok(remote_url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{remote_url}/v1/health", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return payload.get("status") == "ok"


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=TERM_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


# --------------------------------------------------------------------------- #
# Server manager
# --------------------------------------------------------------------------- #


class MineruApiServer:
    """Own (or reuse) one MinerU api-server; guaranteed to die on ``stop``."""

    def __init__(
        self,
        bin_path: str,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        tier: str = DEFAULT_SERVER_TIER,
        pid_file: Optional[Path] = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        command: Optional[list[str]] = None,
        port_probe: Callable[[int], bool] = port_is_listening,
        health_check: Callable[[str], bool] = health_ok,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.host = host
        self.port = port
        self.tier = tier
        self.bin_path = bin_path
        self.pid_file = Path(pid_file) if pid_file else pid_file_path(port)
        self.startup_timeout = startup_timeout
        self.command = command or [
            bin_path,
            "api-server",
            "--host",
            host,
            "--port",
            str(port),
            "--tier",
            tier,
            "--preload-models",
        ]
        self._port_probe = port_probe
        self._health_check = health_check
        self._popen = popen
        self._sleep = sleep
        self._process: Optional[subprocess.Popen] = None
        self.reused = False

    @property
    def remote_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        """Spawn a server, reuse a healthy owned one, or raise."""
        self._clear_stale_pid()

        if self._port_probe(self.port):
            if self._owns_healthy_server():
                self.reused = True
                return
            raise MineruServerError(
                f"port {self.port} is occupied by another process; "
                "refusing to reuse or kill it"
            )

        try:
            self._process = self._popen(
                self.command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise MineruServerError(f"failed to launch mineru api-server: {exc}") from exc

        write_pid_file(self.pid_file, self._process.pid, self.port)
        if not self._wait_until_healthy():
            self.stop()
            raise MineruServerError(
                f"mineru api-server did not become healthy within "
                f"{self.startup_timeout:.0f}s"
            )

    def stop(self) -> None:
        """Kill the process group we started; never touch a reused server."""
        process = self._process
        self._process = None
        if process is None:
            return
        _terminate_process_group(process)
        if read_pid_file(self.pid_file) == process.pid:
            remove_pid_file(self.pid_file)

    def __enter__(self) -> "MineruApiServer":
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.stop()
        return False

    # -- internals ---------------------------------------------------------- #

    def _clear_stale_pid(self) -> None:
        pid = read_pid_file(self.pid_file)
        if pid is None:
            return
        if pid_is_alive(pid) and pid_is_mineru_api_server(pid):
            return
        # Dead, or a recycled pid that is not our server: drop the marker,
        # never kill it.
        remove_pid_file(self.pid_file)

    def _owns_healthy_server(self) -> bool:
        pid = read_pid_file(self.pid_file)
        if pid is None or not pid_is_alive(pid):
            return False
        if not pid_is_mineru_api_server(pid):
            return False
        return self._health_check(self.remote_url)

    def _wait_until_healthy(self) -> bool:
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                return False
            if self._health_check(self.remote_url):
                return True
            self._sleep(HEALTH_POLL_INTERVAL)
        return False
