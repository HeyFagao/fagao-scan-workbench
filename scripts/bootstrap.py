#!/usr/bin/env python3
"""Install a pinned public source release and open a healthy local workbench.

No package hook is executed. Only the project's declared launch.py is started.
Python itself is prepared by WorkBuddy before invoking this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid
import zipfile

APP = "fagao-scan-workbench"
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_MEMBERS = 10000
REQUIRED = ("scripts/launch.py", "fagao/server.py", "fagao/store.py", "fagao/__main__.py", "web/index.html")
EXCLUDED = {".git", "node_modules", "__pycache__", ".venv", ".DS_Store", ".env", ".data",
            "browser-profiles", ".fagao-release.json", ".fagao-install.json", ".install.lock"}
RUNTIME_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm", ".log")


def excluded_path(relative: Path) -> bool:
    return (any(part in EXCLUDED or part.startswith((".env.", ".staging-", ".download-"))
                for part in relative.parts)
            or relative.name.lower().endswith(RUNTIME_SUFFIXES))


class InstallError(RuntimeError):
    pass


def default_data_dir() -> Path:
    if os.environ.get("FAGAO_DATA_DIR"):
        return Path(os.environ["FAGAO_DATA_DIR"]).expanduser().resolve()
    if os.name == "nt":
        return (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "FagaoWorkbench").resolve()
    return (Path.home() / ".fagao-workbench").resolve()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError) as exc:
        raise InstallError(f"安装记录无效，请保留现场交给维护助手：{path}") from exc


def validate_source(source: Path) -> None:
    for name in REQUIRED:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"下载内容缺少本工程必需文件：{name}")
    if APP not in (source / "scripts/launch.py").read_text(encoding="utf-8"):
        raise InstallError("启动入口不属于发糕扫榜工作台。")


def safe_member(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name or ":" in name:
        raise InstallError("压缩包包含不安全的文件路径。")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in (".", "..") for p in path.parts):
        raise InstallError("压缩包包含路径穿越。")
    if any(p.endswith((".", " ")) for p in path.parts):
        raise InstallError("压缩包路径不兼容安全的跨平台解压。")
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    if any(p.split(".")[0].upper() in reserved for p in path.parts):
        raise InstallError("压缩包包含保留设备名。")
    return path


def extract_archive(archive: Path, destination: Path, expected_root: str) -> None:
    if archive.stat().st_size > MAX_ARCHIVE:
        raise InstallError("下载包超过允许大小。")
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if not members or len(members) > MAX_MEMBERS:
            raise InstallError("压缩包文件数量不正确。")
        total = 0
        seen = set()
        accepted = []
        for member in members:
            path = safe_member(member.filename)
            if not path.parts or path.parts[0] != expected_root:
                raise InstallError("压缩包根目录与指定仓库版本不符。")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise InstallError("压缩包包含链接或特殊文件，已拒绝。")
            if member.flag_bits & 1:
                raise InstallError("不接受加密压缩包。")
            relative = PurePosixPath(*path.parts[1:])
            key = str(relative).casefold()
            if key in seen:
                raise InstallError("压缩包包含重复或大小写冲突路径。")
            seen.add(key)
            total += member.file_size
            if total > MAX_EXPANDED or member.file_size < 0:
                raise InstallError("压缩包展开后超过允许大小。")
            accepted.append((member, relative))
        destination.mkdir(parents=True, exist_ok=False)
        for member, relative in accepted:
            if str(relative) == ".":
                continue
            target = destination.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zipped.open(member) as reader, target.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    written += len(chunk)
                    if written > member.file_size or written > MAX_EXPANDED:
                        raise InstallError("压缩内容大小与声明不符。")
                    writer.write(chunk)
            target.chmod(0o755 if (member.external_attr >> 16) & 0o111 else 0o644)


class PinnedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname != "codeload.github.com":
            raise InstallError("下载发生了未经允许的跨站跳转。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(repo: str, commit: str, destination: Path) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise InstallError("仓库须是明确的 owner/repo。")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        raise InstallError("必须提供完整的 40 位 Git 提交 SHA，不能使用浮动 main 分支。")
    url = f"https://codeload.github.com/{repo}/zip/{commit.lower()}"
    opener = build_opener(PinnedRedirect())
    request = Request(url, headers={"User-Agent": "FagaoWorkbench-Installer/0.1"})
    try:
        with opener.open(request, timeout=45) as response, destination.open("xb") as handle:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_ARCHIVE:
                raise InstallError("下载包超过允许大小。")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise InstallError("下载包超过允许大小。")
                handle.write(chunk)
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(f"指定 GitHub 版本下载失败：{type(exc).__name__}。请检查仓库、提交与网络，未改动已有版本。") from exc


def copy_local(source: Path, destination: Path) -> str:
    source = source.expanduser().resolve()
    validate_source(source)
    files = []
    size = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if excluded_path(rel):
            continue
        if path.is_symlink():
            raise InstallError("本地来源含符号链接，请提供完整的可信工程。")
        if not path.is_file():
            continue
        size += path.stat().st_size
        if size > MAX_EXPANDED or len(files) >= MAX_MEMBERS:
            raise InstallError("本地工程超过允许大小，请移除运行数据与依赖目录。")
        files.append((path, rel))
    destination.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    for path, rel in files:
        content = path.read_bytes()
        digest.update(rel.as_posix().encode() + b"\0" + content + b"\0")
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
    return "local-" + digest.hexdigest()[:20]


def inventory(source: Path) -> dict:
    return {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(source.rglob("*")) if p.is_file() and not p.is_symlink()}


def verify_inventory(source: Path, files: dict) -> None:
    actual = set()
    for candidate in source.rglob("*"):
        relative = candidate.relative_to(source)
        if excluded_path(relative) and relative.as_posix() not in files:
            continue
        if candidate.is_symlink():
            raise InstallError(f"已安装版本包含额外链接，未执行：{relative}")
        if candidate.is_file():
            actual.add(relative.as_posix())
    if actual != set(files):
        difference = sorted(actual.symmetric_difference(files))
        raise InstallError("已安装版本出现额外或缺失文件，未执行：" + ", ".join(difference[:5]))
    for name, digest in files.items():
        path = source.joinpath(*safe_member(name).parts)
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise InstallError(f"已安装版本的文件被修改，未覆盖或执行：{name}")


def health(port: int) -> dict | None:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return None
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def service_matches(value: dict | None, data_dir: Path, release: Path) -> bool:
    if not value or value.get("app") != APP or not value.get("data_dir") or not value.get("source_root"):
        return False
    try:
        return Path(value["data_dir"]).resolve() == data_dir.resolve() and Path(value["source_root"]).resolve() == release.resolve()
    except (OSError, ValueError, TypeError):
        return False



def runtime_environment(root: Path) -> dict:
    """An obsolete optional browser runtime must not block the core workbench."""
    env = os.environ.copy()
    config = root / "runtime.json"
    if not config.exists():
        return env
    try:
        value = read_json(config)
    except InstallError:
        return env
    required = value.get("enable_legacy_heiyan_backend") is True
    node = value.get("node_executable")
    if node is None:
        return env
    if not isinstance(node, str) or not node or "\x00" in node:
        if required:
            raise InstallError("runtime.json 的 node_executable 须为 Node 可执行文件绝对路径。")
        return env
    candidate = Path(node).expanduser()
    if not candidate.is_absolute() or not candidate.is_file() or candidate.name.lower() not in ("node", "node.exe"):
        if required:
            raise InstallError("runtime.json 指定的 Node 不存在或不是有效的 Node 可执行文件绝对路径。")
        return env
    env["PATH"] = str(candidate.parent) + os.pathsep + env.get("PATH", "")
    return env


OPEN_SCRIPT = '''#!/usr/bin/env python3
# Generated by the Fagao installer; reads the atomically selected release.
import json, os, pathlib, re, subprocess, sys
from urllib.request import build_opener, ProxyHandler
root = pathlib.Path(__file__).resolve().parent
current = json.loads((root / "current.json").read_text(encoding="utf-8"))
release_id = current["release_id"]
if not re.fullmatch(r"(?:[a-f0-9]{40}|local-[a-f0-9]{20})", release_id):
    raise SystemExit("Invalid Fagao release pointer")
release = root / "releases" / release_id
if release.is_symlink() or not release.is_dir():
    raise SystemExit("The installed Fagao release is missing; run the installer again")
port = int(current["port"])
try:
    response = build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/api/health", timeout=1)
    state = json.loads(response.read(65536))
except (OSError, ValueError):
    state = None
if state and (state.get("app") != "fagao-scan-workbench" or pathlib.Path(state.get("data_dir", "")).resolve() != pathlib.Path(current["data_dir"]).resolve() or pathlib.Path(state.get("source_root", "")).resolve() != release.resolve()):
    raise SystemExit("An older or different service is running. Ask WorkBuddy to restart the verified Fagao service.")
environment = os.environ.copy()
runtime_file = root / "runtime.json"
if runtime_file.exists():
    try:
        runtime = json.loads(runtime_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        runtime = {}
    if not isinstance(runtime, dict):
        runtime = {}
    node = runtime.get("node_executable")
    if node is not None:
        candidate = pathlib.Path(node).expanduser() if isinstance(node, str) and node and "\\x00" not in node else None
        valid = candidate is not None and candidate.is_absolute() and candidate.is_file() and candidate.name.lower() in ("node", "node.exe")
        if not valid and runtime.get("enable_legacy_heiyan_backend") is True:
            raise SystemExit("The configured private Node executable is missing or invalid")
        if valid:
            environment["PATH"] = str(candidate.parent) + os.pathsep + environment.get("PATH", "")
command = [sys.executable, str(release / "scripts" / "launch.py"), "--data-dir", current["data_dir"], "--port", str(port)]
raise SystemExit(subprocess.call(command, cwd=release, env=environment))
'''


PROJECT_INSTRUCTIONS = """# 发糕扫榜工作台 · 固定项目入口

这是安装器维护的固定 WorkBuddy 项目目录。版本升级后继续使用此目录，不将项目工作目录绑定到 releases 中的某一个版本。

开始维护或分析前：

1. 读取本目录上一级的 `current.json`。它指向当前已验证版本，包含 `release_id`、`data_dir`、`interpreter` 与 `port`。不要把本机路径、账号资料或数据库上传到公共仓库。
2. 当前源码位于上一级的 `releases/<release_id>`。读取该目录的 `AGENTS.md` 和 `docs/workbuddy.md`；每次启动新任务重新读取指针，不能缓存旧版本路径。
3. 使用 `current.json` 中的 `interpreter` 作为 Python 绝对路径，并把进程工作目录设为当前源码目录。执行分析 CLI 时始终传 `--data-dir` 指定该记录中的资料目录；每段对话使用独立且稳定的 `--session`。
4. 日常打开工作台，运行上一级的 `open-workbench.py`，不要另建服务或猜测端口。这个入口按 `current.json` 选择版本，并核对服务所属资料与源码目录。
5. 只有在定位、取文和实际阅读后才分析。用户说“第二本”“原榜第 14 名”“刚选的这本”时，按当前版本文档调用 CLI 固定作品；后续不因更新榜单而换书。
6. 更新源码不会替换资料目录；旧版本正在运行时按安装结果处理，先核对具体服务进程再重启，不终止无关 Python、浏览器或其他应用。
7. 当前黑岩入口是腾讯文档书单，点击后由用户在普通浏览器登录；不是掌文后台，不为这个入口安装 Node 或 Playwright。启动器会先检查当前功能的本地依赖与资源，失败由维护助手修复后再打开。
8. 用户说“翻译这本”时，按当前版本 docs/workbuddy.md 调用 translation-context 固定作品，从返回的英文原文翻译书名和简介；翻译由当前对话模型完成，工作台原文保留。复制的翻译指令若带有快照和作品 ID，始终使用这两个固定 ID。

如果 `current.json` 缺失、字段无效或目标版本不在本安装目录中，停止启动并让维护助手重新执行可信安装流程。不要用其他用户目录或猜测的版本兜底。
"""


def create_project_workspace(root: Path, manifest: dict) -> Path:
    managed_write(root, "workspace/AGENTS.md", PROJECT_INSTRUCTIONS.encode("utf-8"), manifest)
    return root / "workspace"


def sha_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def managed_write(root: Path, name: str, content: bytes, manifest: dict, executable=False) -> Path:
    relative = safe_member(name)
    target = root.joinpath(*relative.parts)
    for parent in target.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise InstallError(f"拒绝通过链接写入固定入口：{parent}")
    target.parent.mkdir(parents=True, exist_ok=True)
    files = manifest.setdefault("files", {})
    if target.is_symlink():
        raise InstallError(f"拒绝覆盖链接：{target}")
    if target.exists() and sha_bytes(target.read_bytes()) != files.get(name):
        if target.read_bytes() != content:
            raise InstallError(f"固定入口已被外部修改，未覆盖：{target}")
    target.write_bytes(content)
    if executable:
        target.chmod(0o755)
    files[name] = sha_bytes(content)
    return target


def create_shortcut(root: Path, manifest: dict) -> Path:
    entry = managed_write(root, "open-workbench.py", OPEN_SCRIPT.encode(), manifest, True)
    python = str(Path(sys.executable).resolve())
    if os.name == "nt":
        # Percent is doubled because cmd expands environment references in .bat.
        quote = lambda s: '"' + s.replace('%', '%%') + '"'
        content = '@echo off\r\nchcp 65001 >nul\r\n' + quote(python) + ' ' + quote(str(entry)) + '\r\nif errorlevel 1 pause\r\n'
        return managed_write(root, "打开工作台.bat", content.encode("utf-8"), manifest)
    if sys.platform == "darwin":
        import shlex
        content = "#!/bin/bash\n" + shlex.quote(python) + " " + shlex.quote(str(entry)) + "\n"
        return managed_write(root, "打开工作台.command", content.encode(), manifest, True)
    def desktop_quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    content = "[Desktop Entry]\nType=Application\nName=发糕扫榜工作台\nTerminal=false\nExec=" + desktop_quote(python) + " " + desktop_quote(str(entry)) + "\n"
    return managed_write(root, "发糕扫榜.desktop", content.encode(), manifest, True)


def install(*, repo=None, commit=None, source_dir=None, install_root=None, data_dir=None, port=18376, no_browser=False) -> dict:
    if sys.version_info < (3, 10):
        raise InstallError("需要 Python 3.10 或更新版本。")
    if not 1024 <= int(port) <= 65535:
        raise InstallError("端口须在 1024—65535 之间。")
    data = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir()
    raw_root = Path(install_root).expanduser().absolute() if install_root else data / "app"
    if raw_root.is_symlink():
        raise InstallError("安装目录是符号链接，请使用独立的真实目录。")
    root = raw_root.resolve()
    if source_dir:
        local_source = Path(source_dir).expanduser().absolute()
        if local_source.is_symlink():
            raise InstallError("本地来源不能是符号链接，请使用真实工程目录。")
        local_source = local_source.resolve()
        if root == local_source or local_source in root.parents:
            raise InstallError("安装目录不能位于本地源工程内部，以免复制安装资料与版本自身。")
    if root == data or root in data.parents:
        raise InstallError("程序安装目录不能覆盖资料目录或其父目录。")
    marker = root / ".fagao-install.json"
    if root.exists():
        if not root.is_dir():
            raise InstallError("安装路径已经被文件占用。")
        if not marker.is_file():
            if any(root.iterdir()):
                raise InstallError("目标目录已有非本工程内容，拒绝覆盖。请选新的安装目录。")
            manifest = {"app":APP,"format":1,"files":{}}
        else:
            if marker.is_symlink():
                raise InstallError("安装标记不能是符号链接。")
            manifest = read_json(marker)
            if manifest.get("app") != APP or manifest.get("format") != 1:
                raise InstallError("目标目录不属于本工程，拒绝覆盖。")
    else:
        root.mkdir(parents=True, mode=0o700)
        manifest = {"app":APP,"format":1,"files":{}}
    for name in ("releases", "current.json", ".install.lock"):
        if (root/name).is_symlink():
            raise InstallError("安装目录含不安全链接，已停止。")
    lock = root / ".install.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise InstallError("另一处正在安装；若已中断，请让维护助手核实后清理安装锁。") from exc
    os.close(descriptor)
    stage = root / (".staging-" + uuid.uuid4().hex)
    try:
        atomic_json(marker, manifest)
        (root / "releases").mkdir(exist_ok=True)
        if source_dir:
            release_id = copy_local(Path(source_dir), stage)
            source = {"kind":"local","source_dir":str(Path(source_dir).expanduser().resolve())}
        else:
            if not repo or not commit:
                raise InstallError("远程安装须明确 --repo 和完整 --commit。")
            commit = commit.lower()
            archive = root / (".download-" + uuid.uuid4().hex + ".zip")
            try:
                download_archive(repo, commit, archive)
                extract_archive(archive, stage, repo.split("/")[1] + "-" + commit)
            finally:
                archive.unlink(missing_ok=True)
            release_id = commit
            source = {"kind":"github","repo":repo,"commit":commit}
        validate_source(stage)
        release = root / "releases" / release_id
        release_meta = {"app":APP,"release_id":release_id,"source":source,"files":inventory(stage)}
        if release.exists():
            if release.is_symlink() or read_json(release/".fagao-release.json") != release_meta:
                raise InstallError("同名版本目录与安装记录不符，拒绝覆盖。")
            validate_source(release)
            verify_inventory(release,release_meta["files"])
            shutil.rmtree(stage)
        else:
            atomic_json(stage/".fagao-release.json",release_meta)
            os.replace(stage, release)
        # Verify fixed entry ownership before starting a new process. The pointer
        # remains unchanged until the newly launched service passes health checks.
        shortcut = create_shortcut(root,manifest)
        project_dir = create_project_workspace(root,manifest)
        atomic_json(marker,manifest)
        current_health = health(port)
        if current_health and not service_matches(current_health,data,release):
            return {"status":"restart_required","release_id":release_id,"release_dir":str(release),"message":"新版本已准备，但旧版本或另一服务仍在运行。尚未切换 current，也未声称升级完成。请让 WorkBuddy 核实并停止旧服务，再运行本安装命令。","data_dir":str(data)}
        cmd = [sys.executable,str(release/"scripts/launch.py"),"--data-dir",str(data),"--port",str(port)]
        if no_browser:
            cmd.append("--no-browser")
        run = subprocess.run(cmd,cwd=release,capture_output=True,text=True,timeout=50,env=runtime_environment(root))
        if run.returncode:
            raise InstallError("版本已准备，但启动失败，未切换现用版本。" + run.stderr[-1800:])
        try:
            launched = json.loads(run.stdout)
        except ValueError as exc:
            raise InstallError("启动入口没有返回可验证结果，未切换现用版本。") from exc
        readiness = launched.get("readiness", {})
        if not isinstance(readiness, dict) or readiness.get("status") != "ready":
            raise InstallError("本地依赖与必要资源未通过启动自检，未切换现用版本。请由 WorkBuddy 修复后重新安装。")
        if not service_matches(health(port),data,release):
            raise InstallError("工作台未通过版本与资料目录健康检查，未切换现用版本。")
        interpreter = str(Path(sys.executable).resolve())
        atomic_json(root/"current.json",{"app":APP,"release_id":release_id,"data_dir":str(data),"port":port,"source":source,"interpreter":interpreter})
        return {"status":"ready","release_id":release_id,"release_dir":str(release),"url":f"http://127.0.0.1:{port}/","data_dir":str(data),"shortcut":str(shortcut),"project_dir":str(project_dir),"interpreter":interpreter,"browser_opened":bool(launched.get("browser_opened")),"pid":launched.get("pid"),"service":launched.get("status"),"readiness":readiness}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        lock.unlink(missing_ok=True)


def main() -> int:
    parser=argparse.ArgumentParser(description="安装固定版本并启动发糕扫榜工作台")
    parser.add_argument("--repo",help="公开仓库 owner/repo")
    parser.add_argument("--commit",help="固定的完整 40 位提交 SHA")
    parser.add_argument("--source-dir",type=Path,help="仅用于明确可信的本地工程/离线验收")
    parser.add_argument("--install-root",type=Path)
    parser.add_argument("--data-dir",type=Path)
    parser.add_argument("--port",type=int,default=18376)
    parser.add_argument("--no-browser",action="store_true",help="仅用于无桌面验收，不自动打开浏览器")
    args=parser.parse_args()
    if args.source_dir and (args.repo or args.commit):
        parser.error("--source-dir 与 --repo/--commit 二选一。")
    try:
        result=install(**vars(args))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result["status"]=="restart_required" else 0
    except (InstallError,OSError,ValueError,zipfile.BadZipFile,subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error":{"code":"install_failed","message":str(exc)}},ensure_ascii=False),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
