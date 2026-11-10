#!/usr/bin/env python3
"""Start or reuse the workbench, with an explicit managed-host fallback."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import webbrowser

APP = "fagao-scan-workbench"
ROOT = Path(__file__).resolve().parent.parent
HEIYAN_BOOKLIST_URL = "https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7"
PROXY_ENVIRONMENT_KEYS = frozenset(("http_proxy", "https_proxy", "all_proxy", "no_proxy", "ftp_proxy"))
IS_WINDOWS = os.name == "nt"


class NativeBackgroundRequired(RuntimeError):
    """The host must own a foreground task; never pretend it was detached."""

    def __init__(self, data_dir: Path, port: int):
        super().__init__("Windows 未允许独立后台启动，请由助手使用宿主的原生后台任务运行返回的命令。")
        self.result = {
            "status": "background_required", "code": "native_background_required",
            "reason": "windows_detach_not_permitted", "browser_opened": False,
            "data_dir": str(data_dir), "url": f"http://127.0.0.1:{port}/",
            "action": {
                "type": "run_in_background",
                "argv": [sys.executable, str(ROOT / "scripts/launch.py"), "--data-dir", str(data_dir),
                         "--port", str(port), "--foreground", "--no-browser"],
                "cwd": str(ROOT), "health_url": f"http://127.0.0.1:{port}/api/health",
            },
        }


def windows_in_job() -> bool | None:
    """Read membership only. Do not alter a host job or assume all jobs kill."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        in_job = wintypes.BOOL()
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
            return None
        return bool(in_job.value)
    except (AttributeError, OSError):
        return None


def spawn_service(command: list[str], options: dict, data_dir: Path, port: int, *, foreground: bool):
    """Use OS-permitted separation, or ask the host to manage the task itself."""
    options = dict(options)
    if not foreground:
        if IS_WINDOWS:
            # DETACHED_PROCESS only detaches the console. A host may additionally
            # kill its job on tool completion; request breakaway only if permitted.
            options["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
                                        | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000))
        else:
            options["start_new_session"] = True
    try:
        return subprocess.Popen(command, **options)
    except OSError as error:
        if not foreground and IS_WINDOWS and getattr(error, "winerror", None) == 5 and windows_in_job() is True:
            # Do not retry without BREAKAWAY and claim a short-lived child is
            # persistent. The native host background facility is the fallback.
            raise NativeBackgroundRequired(data_dir, port) from error
        raise


def stop_owned_child(process) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def emit_json(value: dict, *, file=None) -> None:
    # Windows tools may capture a legacy codepage. ASCII JSON is lossless and
    # avoids failing merely because a local path contains Chinese or emoji.
    print(json.dumps(value, ensure_ascii=True), file=file, flush=True)


def sanitize_environment(environment: dict) -> dict:
    """Keep runtime settings, but never inherit a terminal's proxy lifetime."""
    return {key: value for key, value in environment.items() if key.lower() not in PROXY_ENVIRONMENT_KEYS}


def preflight() -> dict:
    """Check local requirements before opening the UI; never log into a source."""
    if sys.version_info < (3, 10):
        raise RuntimeError("需要 Python 3.10 或更新版本，请让 WorkBuddy 自动准备完整的用户目录 Python 运行时。")
    modules = {}
    for name in ("sqlite3", "ssl", "urllib.request", "http.server", "threading", "json"):
        try:
            modules[name] = importlib.import_module(name)
        except (ImportError, OSError) as exc:
            raise RuntimeError(f"Python 运行时缺少 {name}。请让 WorkBuddy 更换完整运行时后重试，不需要用户手动安装浏览器依赖。") from exc
    try:
        with modules["sqlite3"].connect(":memory:") as connection:
            connection.execute("CREATE TABLE ready (value TEXT)")
            connection.execute("INSERT INTO ready VALUES (?)", ("发糕",))
            if connection.execute("SELECT value FROM ready").fetchone()[0] != "发糕":
                raise RuntimeError("SQLite 自检未通过。")
        modules["ssl"].create_default_context()
    except Exception as exc:
        raise RuntimeError("Python 的数据库或 HTTPS 组件无法正常工作，请让 WorkBuddy 自动修复运行时后重试。") from exc
    for name in ("fagao/server.py", "fagao/store.py", "fagao/overview.py", "fagao/dashboard.py", "fagao/public_rankings.py", "fagao/network.py", "fagao/overseas.py", "fagao/overseas_readers.py", "fagao/royalroad_fallback.py", "fagao/scribble_import.py", "fagao/opening_content.py", "fagao/reader_crypto.py", "fagao/_vendor/__init__.py", "fagao/_vendor/pyaes/__init__.py", "fagao/_vendor/pyaes/aes.py", "fagao/_vendor/pyaes/blockfeeder.py", "fagao/_vendor/pyaes/util.py", "web/index.html", "web/app.js", "web/styles.css", "web/theme.js"):
        path = ROOT / name
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"安装文件缺失或为空：{name}。请让 WorkBuddy 重新下载同一固定版本；用户资料无需删除。")
    try:
        charmap = json.loads((ROOT / "fagao/assets/fanqie-charmap.json").read_text(encoding="utf-8"))
        if not isinstance(charmap, dict) or not charmap or any(
            not str(key).isdigit() or not 0xE000 <= int(key) <= 0xF8FF
            or not isinstance(value, str) or len(value) != 1
            for key, value in charmap.items()
        ):
            raise ValueError("invalid character table")
    except (OSError, ValueError) as exc:
        raise RuntimeError("番茄字表资源缺失或损坏，请让 WorkBuddy 重新下载完整版本；不要等取文时再处理。") from exc
    return {
        "status": "ready", "checks": ["python", "sqlite", "https", "web_assets", "fanqie_charmap"],
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "features": {
            "rankings": {"status": "local_ready", "platforms": ["fanqie", "qidian", "ciweimao"]},
            "fanqie_text": {"status": "local_ready"},
            "opening_text": {"status": "local_ready", "platforms": ["qidian", "ciweimao"], "start_chapter": 1, "max_chapters": 10},
            "overseas": {"status": "local_ready", "platforms": ["goodnovel", "goodnovel_short", "meganovel", "dreame", "webnovel", "royalroad", "scribblehub"], "translation": "workbuddy_context"},
            "heiyan": {"status": "external_link", "url": HEIYAN_BOOKLIST_URL, "login": "in_user_browser"},
            "legacy_heiyan_backend": {"status": "not_required"},
        },
        "network": {"status": "local_ready", "policy": "application_owned", "version": 1, "inherited_proxy_environment": "ignored"},
        "network_verified": False,
    }


def default_data_dir() -> Path:
    configured = os.environ.get("FAGAO_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return (base / "FagaoWorkbench").resolve()
    return (Path.home() / ".fagao-workbench").resolve()


def health(port: int) -> dict | None:
    # Never send a localhost request through a user's system proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1.2) as response:
            if response.status != 200:
                return None
            raw = response.read(65537)
            if len(raw) > 65536:
                return None
            result = json.loads(raw)
            return result if isinstance(result, dict) else None
    except (OSError, ValueError, URLError):
        return None


def listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.4)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def matching_health(result: dict | None, data_dir: Path) -> bool:
    if not result or result.get("app") != APP or not result.get("data_dir") or not result.get("source_root"):
        return False
    try:
        return (Path(result["data_dir"]).expanduser().resolve() == data_dir.resolve()
                and Path(result["source_root"]).expanduser().resolve() == ROOT.resolve())
    except (ValueError, TypeError, OSError):
        return False


def running_result(status: str, url: str, data_dir: Path, service_health: dict) -> dict:
    """Report the running service's evidence, including when it predates isolation."""
    network = service_health.get("network")
    return {
        "status": status, "url": url, "data_dir": str(data_dir),
        "network": network if isinstance(network, dict) else {
            "status": "unknown", "reason": "service_did_not_report_network_policy",
        },
        "lifecycle": {"mode": "existing_service", "host_survival_verified": False,
                      "follow_up": "verify_in_separate_tool_call"},
    }


def ensure_running(data_dir: Path, port: int, timeout: float = 30) -> dict:
    return _ensure_running(data_dir, port, timeout)[0]


def _ensure_running(data_dir: Path, port: int, timeout: float = 30, *, foreground: bool = False) -> tuple[dict, object | None]:
    current = health(port)
    url = f"http://127.0.0.1:{port}/"
    if matching_health(current, data_dir):
        return running_result("reused", url, data_dir, current), None
    if current or listening(port):
        if current and current.get("app") == APP:
            detail = "该端口已由另一版本或另一份资料目录的发糕工作台使用。"
        else:
            detail = "该端口已被其他程序占用，不能把它当作发糕工作台打开。"
        raise RuntimeError(f"{detail} 请关闭冲突程序，或让维护助手用 --port 选择其他端口。")

    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(exist_ok=True, mode=0o700)
    lock_path = data_dir / f"launch-{port}.lock"
    acquired = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock:
                lock.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
            acquired = True
            break
        except FileExistsError:
            current = health(port)
            if matching_health(current, data_dir):
                return running_result("reused", url, data_dir, current), None
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.25)
    if not acquired:
        raise RuntimeError("另一处正在启动工作台，请稍后再打开。若持续出现，请检查资料目录中的启动日志。")

    process = None
    try:
        # A concurrent launcher may have finished while we acquired the lock.
        current = health(port)
        if matching_health(current, data_dir):
            return running_result("reused", url, data_dir, current), None
        if listening(port):
            raise RuntimeError("端口在启动前被其他程序占用，请稍后重试或换一个端口。")
        log_path = logs_dir / "workbench.log"
        environment = sanitize_environment(os.environ)
        environment["FAGAO_DATA_DIR"] = str(data_dir)
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        options = {"cwd": str(ROOT), "env": environment, "stdin": subprocess.DEVNULL}
        with log_path.open("ab") as log:
            process = spawn_service(
                [sys.executable, "-m", "fagao.server", "--port", str(port), "--data-dir", str(data_dir)],
                {**options, "stdout": log, "stderr": subprocess.STDOUT}, data_dir, port,
                foreground=foreground,
            )
        deadline = time.monotonic() + timeout
        healthy_samples = 0
        while time.monotonic() < deadline:
            current = health(port)
            if process.poll() is not None:
                raise RuntimeError(f"本地程序未能启动（退出码 {process.returncode}）。日志：{log_path}")
            healthy_samples = healthy_samples + 1 if matching_health(current, data_dir) else 0
            if healthy_samples >= 2:
                (data_dir / "server.pid").write_text(str(process.pid), encoding="ascii")
                return {
                    **running_result("started", url, data_dir, current),
                    "pid": process.pid, "log_path": str(log_path),
                    "lifecycle": {"mode": "host_managed_foreground" if foreground else
                                  ("windows_breakaway" if IS_WINDOWS else "posix_session"),
                                  "host_survival_verified": False,
                                  "follow_up": "verify_in_separate_tool_call"},
                }, process
            time.sleep(0.25)
        raise RuntimeError(f"程序启动后未通过健康检查。日志：{log_path}")
    except BaseException:
        # Only terminate the child created by this call, never an existing service.
        stop_owned_child(process)
        raise
    finally:
        lock_path.unlink(missing_ok=True)


def run_foreground(data_dir: Path, port: int, readiness: dict) -> int:
    """Hold the service inside a native host background task; never detach it."""
    result, process = _ensure_running(data_dir, port, foreground=True)
    readiness["network"] = result["network"]
    result.update(readiness=readiness, browser_opened=False)
    if process is None:
        emit_json(result)
        return 0
    try:
        emit_json(result)
        return 0 if process.wait() == 0 else 1
    except KeyboardInterrupt:
        return 130
    finally:
        stop_owned_child(process)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动或唤起发糕扫榜工作台")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=18376)
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="只检查本地依赖和必要文件，不启动服务或浏览器")
    parser.add_argument("--foreground", action="store_true", help="由宿主后台任务管理服务，保持当前命令运行，不自动打开浏览器")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("需要 Python 3.10 或更新版本，请让 WorkBuddy 帮你准备运行环境。")
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024—65535 之间。")
    try:
        readiness = preflight()
        if args.check:
            emit_json({"status": "checked", "readiness": readiness})
            return 0
        if args.foreground:
            return run_foreground(args.data_dir.expanduser().resolve(), args.port, readiness)
        result = ensure_running(args.data_dir.expanduser().resolve(), args.port)
        readiness["network"] = result["network"]
        result["readiness"] = readiness
        result["browser_opened"] = False if args.no_browser else bool(webbrowser.open(result["url"], new=2))
        emit_json(result)
        if not args.no_browser and not result["browser_opened"]:
            print("工作台已经运行，浏览器未能自动打开。请访问：" + result["url"], file=sys.stderr)
        return 0
    except NativeBackgroundRequired as error:
        emit_json({**error.result, "readiness": readiness})
        return 3
    except (OSError, RuntimeError) as error:
        emit_json({"status": "failed", "error": {"code": "launch_failed", "message": str(error)}}, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
