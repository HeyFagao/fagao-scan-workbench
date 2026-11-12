#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Install a previously verified, unpacked copy of the personal submission component."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform as host
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fagao.submission_lock import component_lock


PINS = Path(__file__).with_name("submission-component-builds.json")
BUNDLE_ID = "com.fagao.workbench.submission"
LAYOUT = {
    "darwin": ("FagaoSubmission.app", "Contents/MacOS/FagaoSubmission", "Contents/Resources/app.asar"),
    "win32": ("FagaoSubmission", "FagaoSubmission.exe", "resources/app.asar"),
}
SHA256 = re.compile(r"[a-f0-9]{64}")


class InstallError(ValueError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def linklike(path: Path) -> bool:
    meta = path.lstat()
    return stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0) & 0x400)


def require_directory(path: Path) -> None:
    if linklike(path) or not path.is_dir():
        raise InstallError(f"需要真实目录：{path.name}")


def require_file(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    current = root
    for part in parts[:-1]:
        current = current / part
        require_directory(current)
    result = current / parts[-1]
    if linklike(result) or not result.is_file():
        raise InstallError(f"组件文件无效：{relative}")
    return result


def tree_sha256(root: Path, system: str) -> str:
    """Hash sorted, typed entries. macOS executable modes and link text are significant."""
    require_directory(root)
    root = root.resolve(strict=True)
    digest = hashlib.sha256()

    def add(fields: list) -> None:
        digest.update(json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")

    def walk(directory: Path) -> None:
        for item in sorted(directory.iterdir(), key=lambda p: p.name):
            name = item.relative_to(root).as_posix()
            mode = item.lstat().st_mode
            if linklike(item):
                if system != "darwin" or not stat.S_ISLNK(mode):
                    raise InstallError(f"组件含不支持的链接：{name}")
                dest = os.readlink(item)
                if os.path.isabs(dest):
                    raise InstallError(f"组件内链接必须为相对路径：{name}")
                try:
                    inside = item.resolve(strict=True).is_relative_to(root)
                except (OSError, RuntimeError):
                    inside = False
                if not inside:
                    raise InstallError(f"组件内链接超界或无效：{name}")
                add(["link", name, dest])
            elif stat.S_ISDIR(mode):
                add(["dir", name])
                walk(item)
            elif stat.S_ISREG(mode):
                add(["file", name, (mode & 0o111) if system == "darwin" else 0, sha256(item)])
            else:
                raise InstallError(f"组件含不支持的文件：{name}")

    walk(root)
    return digest.hexdigest()


def host_architecture() -> str:
    if sys.platform == "win32":
        # A 32-bit WorkBuddy/Python on 64-bit Windows can launch the x64 program.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        query = getattr(kernel, "IsWow64Process2", None)
        if query is not None:
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            query.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.USHORT), ctypes.POINTER(wintypes.USHORT))
            process, native = wintypes.USHORT(), wintypes.USHORT()
            if query(kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
                return {0x8664: "x64", 0xaa64: "arm64", 0x014c: "x86"}.get(native.value, "unknown")
    if sys.platform == "darwin" and host.machine().lower() == "x86_64":
        # WorkBuddy can run an Intel Python under Rosetta on Apple Silicon.
        translated = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"],
                                    capture_output=True, timeout=5)
        if translated.returncode == 0 and translated.stdout.strip() == b"1":
            return "arm64"
    return {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(
        name := host.machine().lower(), name)


def read_pins(key: str = "builds") -> list[dict]:
    manifest = json.loads(PINS.read_text(encoding="utf-8"))
    records = manifest.get(key, [] if key == "prior_installations" else None)
    if manifest.get("version") != 1 or not isinstance(records, list):
        raise InstallError("组件版本清单无效")
    for rec in records:
        if (not isinstance(rec, dict) or rec.get("platform") not in LAYOUT
                or rec.get("arch") not in ("arm64", "x64", "universal")
                or any(not SHA256.fullmatch(str(rec.get(key, "")))
                       for key in ("tree_sha256", "executable_sha256", "asar_sha256"))):
            raise InstallError("组件版本清单无效")
    return records


def verify_macos(root: Path) -> None:
    info = plistlib.loads(require_file(root, "Contents/Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "FagaoSubmission":
        raise InstallError("不是发糕工作台的个人投稿组件")
    checked = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(root)],
                             capture_output=True, timeout=90)
    if checked.returncode:
        raise InstallError("macOS 应用签名核验失败")


def fingerprint(root: Path, system: str, architecture: str) -> dict:
    """Maintainers can fingerprint independently reviewed private build outputs."""
    require_directory(root)
    _, executable_path, asar_path = LAYOUT[system]
    executable = require_file(root, executable_path)
    asar = require_file(root, asar_path)
    if system == "darwin":
        verify_macos(root)
    return {"platform": system, "arch": architecture, "tree_sha256": tree_sha256(root, system),
            "executable_sha256": sha256(executable), "asar_sha256": sha256(asar)}


def verify_pin(root: Path, system: str, architecture: str, pins: list[dict]) -> dict:
    actual = fingerprint(root, system, architecture)
    valid = [pin for pin in pins if pin["platform"] == system
             and pin["arch"] in (architecture, "universal" if system == "darwin" else architecture)]
    if not any(all(pin[key] == actual[key] for key in ("tree_sha256", "executable_sha256", "asar_sha256"))
               for pin in valid):
        raise InstallError("该投稿组件与已验证的本机版本清单不一致")
    return actual


def receipt_for(build: dict) -> dict:
    return {"version": 1, "platform": build["platform"], "edition": "personal",
            "executable_sha256": build["executable_sha256"], "asar_sha256": build["asar_sha256"]}


def check_managed(path: Path, system: str, architecture: str, pins: list[dict]) -> dict:
    require_directory(path)
    bundle_name, _, _ = LAYOUT[system]
    if {p.name for p in path.iterdir()} != {bundle_name, "receipt.json"}:
        raise InstallError("组件位置含有其他文件，请保留原目录")
    receipt = json.loads(require_file(path, "receipt.json").read_text(encoding="utf-8"))
    expected = verify_pin(path / bundle_name, system, architecture, pins)
    if receipt != receipt_for(expected):
        raise InstallError("现有投稿组件收据不匹配")
    return expected


def inspect_installation(path: Path, system: str, architecture: str, pins: list[dict]) -> dict | None:
    """Return the intact build, or None if a recognized Windows install needs repair."""
    try:
        return check_managed(path, system, architecture, pins)
    except (FileNotFoundError, InstallError):
        if system != "win32":
            raise

    # Recovery may rename only a real, identifiable installation. With a matching
    # receipt, one surviving pinned file identifies the bundle. Without one,
    # require both anchors from the same supported build before touching it.
    require_directory(path)
    name, exe, asar = LAYOUT[system]
    bundle = path / name
    if not os.path.lexists(bundle):
        raise InstallError("无法辨认现有投稿组件，请保留原目录")
    require_directory(bundle)
    known = [pin for pin in pins if pin["platform"] == system and pin["arch"] == architecture]
    has_receipt = os.path.lexists(path / "receipt.json")
    if has_receipt:
        receipt_file = require_file(path, "receipt.json")
        if receipt_file.stat().st_size > 4096:
            raise InstallError("无法辨认投稿组件的安装收据，请保留原目录")
        receipt = json.loads(receipt_file.read_bytes())
        known = [pin for pin in known if receipt == receipt_for(pin)]
    anchors = {}
    for key, relative in (("executable_sha256", exe), ("asar_sha256", asar)):
        try:
            anchors[key] = sha256(require_file(bundle, relative))
        except FileNotFoundError:
            pass
    threshold = 1 if has_receipt else 2
    if not any(sum(pin[key] == value for key, value in anchors.items()) >= threshold for pin in known):
        raise InstallError("无法辨认现有投稿组件，请保留原目录")
    return None


def mac_component_in_use(bundle: Path) -> bool:
    # proc_pidpath returns the executable path of each live PID, including helpers.
    # libproc may hide processes from other users. This private bundle belongs to
    # the account running the installer.
    import ctypes
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.proc_listallpids.restype = ctypes.c_int
    lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    lib.proc_pidpath.restype = ctypes.c_int
    capacity = lib.proc_listallpids(None, 0) + 128
    if capacity <= 128:
        raise OSError("无法检查投稿组件运行状态")
    while True:
        pids = (ctypes.c_int * capacity)()
        count = lib.proc_listallpids(pids, ctypes.sizeof(pids))
        if count <= 0:
            raise OSError("无法检查投稿组件运行状态")
        if count < capacity:
            break
        capacity *= 2
    resolved = bundle.resolve(strict=True)
    buffer = ctypes.create_string_buffer(4096)
    for pid in pids[:count]:
        if pid > 0 and lib.proc_pidpath(pid, buffer, len(buffer)) > 0:
            path = Path(os.fsdecode(buffer.value))
            if bundle.name in path.parts and path.resolve().is_relative_to(resolved):
                return True
    return False


def require_stopped(bundle: Path, system: str) -> None:
    # Platform selection is native for actual installations. Cross-built kit
    # verification can stage a foreign layout, whose executable is never run.
    if sys.platform == "darwin" and system == "darwin":
        active = mac_component_in_use(bundle)
    elif sys.platform == "win32" and system == "win32":
        from scripts.submission_windows_in_use import component_in_use
        active = component_in_use(bundle / LAYOUT[system][1])
    else:
        return
    if active:
        raise InstallError("投稿助手仍在运行，请从投稿助手菜单完整退出，再重新升级。")

def install(source: Path, data_root: Path, system: str, architecture: str, pins: list[dict],
            prior_installations: tuple | list = ()) -> str:
    if system not in LAYOUT:
        raise InstallError("投稿窗口支持 macOS 和 Windows")
    if not any(p["platform"] == system and p["arch"] in (architecture, "universal") for p in pins):
        raise InstallError("这个系统和架构尚无已验证的投稿组件")
    expected = verify_pin(source, system, architecture, pins)
    data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_directory(data_root)
    parent = data_root / "components"
    parent.mkdir(mode=0o700, exist_ok=True)
    require_directory(parent)
    target = parent / "submission"
    previous = parent / ".submission-previous"
    recognized = [*pins, *prior_installations]
    with component_lock(parent):
        if os.path.lexists(previous):
            check_managed(previous, system, architecture, recognized)
            if os.path.lexists(target):
                check_managed(target, system, architecture, recognized)
                require_stopped(previous / LAYOUT[system][0], system)
                shutil.rmtree(previous)
            else:
                os.replace(previous, target)
        has_target = os.path.lexists(target)
        existing = inspect_installation(target, system, architecture, recognized) if has_target else None
        # Distinct package trees may contain identical executable and ASAR bytes.
        if existing == expected:
            return "already_installed"
        if has_target:
            require_stopped(target / LAYOUT[system][0], system)
        # Only a completely verified prior version may be deleted after an
        # upgrade. A repaired installation may contain personal or unknown files.
        backup = (parent / ("submission-repair-backup-" + uuid.uuid4().hex)
                  if has_target and existing is None else previous)
        temp = Path(tempfile.mkdtemp(prefix=".submission-stage-", dir=parent))
        try:
            bundle = temp / LAYOUT[system][0]
            if system == "darwin":
                subprocess.run(["/usr/bin/ditto", str(source), str(bundle)], check=True, timeout=300)
            else:
                shutil.copytree(source, bundle, symlinks=True)
            if verify_pin(bundle, system, architecture, pins) != expected:
                raise InstallError("投稿组件复制后校验失败")
            (temp / "receipt.json").write_text(json.dumps(receipt_for(expected), indent=2) + "\n", encoding="utf-8")
            (temp / "receipt.json").chmod(0o600)
            if has_target:
                require_stopped(target / LAYOUT[system][0], system)
                os.replace(target, backup)
            try:
                os.replace(temp, target)
            except OSError:
                if has_target:
                    os.replace(backup, target)
                raise
            if existing is not None:
                shutil.rmtree(previous)
            return "installed"
        finally:
            if temp.is_dir():
                shutil.rmtree(temp)


def main() -> int:
    parser = argparse.ArgumentParser(description="从本地已核验文件安装发糕个人投稿组件")
    parser.add_argument("--source", type=Path, required=True, help="Mac .app 或 Windows 程序目录")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".fagao-workbench")
    args = parser.parse_args()
    try:
        status = install(args.source.expanduser().absolute(), args.data_dir.expanduser().absolute(),
                         sys.platform, host_architecture(), read_pins(), read_pins("prior_installations"))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"安装未完成：{exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": status, "edition": "personal"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
