#!/usr/bin/env python3
"""Start or reuse the local workbench without tying it to an agent session."""

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
    for name in ("fagao/server.py", "fagao/store.py", "fagao/overview.py", "fagao/dashboard.py", "fagao/public_rankings.py", "web/index.html", "web/app.js", "web/styles.css"):
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
            "heiyan": {"status": "external_link", "url": HEIYAN_BOOKLIST_URL, "login": "in_user_browser"},
            "legacy_heiyan_backend": {"status": "not_required"},
        },
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
    if not result or result.get("app") != APP or not result.get("data_dir"):
        return False
    try:
        return Path(result["data_dir"]).expanduser().resolve() == data_dir.resolve()
    except (ValueError, TypeError, OSError):
        return False


def ensure_running(data_dir: Path, port: int, timeout: float = 30) -> dict:
    current = health(port)
    url = f"http://127.0.0.1:{port}/"
    if matching_health(current, data_dir):
        return {"status": "reused", "url": url, "data_dir": str(data_dir)}
    if current or listening(port):
        if current and current.get("app") == APP:
            detail = "该端口已由另一份资料目录的发糕工作台使用。"
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
            if matching_health(health(port), data_dir):
                return {"status": "reused", "url": url, "data_dir": str(data_dir)}
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
        if matching_health(health(port), data_dir):
            return {"status": "reused", "url": url, "data_dir": str(data_dir)}
        if listening(port):
            raise RuntimeError("端口在启动前被其他程序占用，请稍后重试或换一个端口。")
        log_path = logs_dir / "workbench.log"
        environment = os.environ.copy()
        environment["FAGAO_DATA_DIR"] = str(data_dir)
        environment["PYTHONUNBUFFERED"] = "1"
        options = {"cwd": str(ROOT), "env": environment, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "fagao.server", "--port", str(port), "--data-dir", str(data_dir)],
                stdout=log,
                stderr=subprocess.STDOUT,
                **options,
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if matching_health(health(port), data_dir):
                (data_dir / "server.pid").write_text(str(process.pid), encoding="ascii")
                return {
                    "status": "started", "url": url, "data_dir": str(data_dir),
                    "pid": process.pid, "log_path": str(log_path),
                }
            if process.poll() is not None:
                raise RuntimeError(f"本地程序未能启动（退出码 {process.returncode}）。日志：{log_path}")
            time.sleep(0.25)
        raise RuntimeError(f"程序启动后未通过健康检查。日志：{log_path}")
    except BaseException:
        # Only terminate the child created by this call, never an existing service.
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        raise
    finally:
        lock_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动或唤起发糕扫榜工作台")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=18376)
    parser.add_argument("--no-browser", action="store_true", help="只启动服务，不自动打开浏览器")
    parser.add_argument("--check", action="store_true", help="只检查本地依赖和必要文件，不启动服务或浏览器")
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error("需要 Python 3.10 或更新版本，请让 WorkBuddy 帮你准备运行环境。")
    if not 1024 <= args.port <= 65535:
        parser.error("端口须在 1024—65535 之间。")
    try:
        readiness = preflight()
        if args.check:
            print(json.dumps({"status": "checked", "readiness": readiness}, ensure_ascii=False))
            return 0
        result = ensure_running(args.data_dir.expanduser().resolve(), args.port)
        result["readiness"] = readiness
        result["browser_opened"] = False if args.no_browser else bool(webbrowser.open(result["url"], new=2))
        print(json.dumps(result, ensure_ascii=False))
        if not args.no_browser and not result["browser_opened"]:
            print("工作台已经运行，浏览器未能自动打开。请访问：" + result["url"], file=sys.stderr)
        return 0
    except (OSError, RuntimeError) as error:
        print("发糕工作台启动失败：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
