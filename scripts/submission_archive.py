# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Extract an already digest-verified native submission release into private staging."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile
import zlib


LICENSE_DIR = "THIRD_PARTY_LICENSES"
BUNDLES = {"darwin": "FagaoSubmission.app", "win32": "FagaoSubmission"}
MAX_ENTRIES = 8192
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_ENTRY = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
WINDOWS_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?\Z", re.I)


def member_parts(name: str) -> tuple[str, ...]:
    parts = name.split("/")
    if (len(name) > 1024 or len(parts) > 48 or any(
        not part or part in (".", "..") or part.endswith((" ", "."))
        or any(ord(c) < 32 or c in '\\:<>"|?*' for c in part)
        or WINDOWS_DEVICE.fullmatch(part) for part in parts
    )):
        raise ValueError("投稿组件压缩包路径无效")
    return tuple(parts)


def _members(zf: zipfile.ZipFile, bundle: str, system: str):
    entries = zf.infolist()
    if len(entries) > MAX_ENTRIES or sum(info.file_size for info in entries) > MAX_TOTAL:
        raise ValueError("投稿组件压缩包超出大小限制")
    seen = set()
    nodes = {}
    planned = []
    regular_roots = set()
    for info in entries:
        is_dir = info.is_dir()
        name = info.filename[:-1] if is_dir else info.filename
        if info.orig_filename != info.filename or name in seen:
            raise ValueError("投稿组件压缩包含重复或无效文件")
        seen.add(name)
        parts = member_parts(name)
        if parts[0] not in (bundle, LICENSE_DIR):
            raise ValueError("投稿组件压缩包内容不匹配")
        if (info.file_size > MAX_ENTRY or info.flag_bits & 1
                or info.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED)
                or info.external_attr & 0x400):
            raise ValueError("投稿组件压缩包含不支持的文件")
        mode = info.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if is_dir and file_type in (0, stat.S_IFDIR) and info.file_size == 0:
            kind = "dir"
        elif not is_dir and file_type in (0, stat.S_IFREG):
            kind = "file"
            regular_roots.add(parts[0])
        elif (not is_dir and file_type == stat.S_IFLNK and system == "darwin"
              and parts[0] == bundle and 0 < info.file_size <= 1024):
            kind = "link"
        else:
            raise ValueError("投稿组件文件类型无效")
        if len(parts) == 1 and kind != "dir":
            raise ValueError("投稿组件根目录无效")
        # Register implicit directories too. A later entry must not turn one into
        # a symlink or a file, or alias it on a case-insensitive filesystem.
        for n in range(1, len(parts) + 1):
            path = "/".join(parts[:n])
            key = unicodedata.normalize("NFC", path).casefold()
            value = (path, kind if n == len(parts) else "dir")
            if nodes.setdefault(key, value) != value:
                raise ValueError("投稿组件压缩包路径冲突")
        planned.append((info, parts, kind, mode))
    if regular_roots != {bundle, LICENSE_DIR}:
        raise ValueError("投稿组件或随附许可缺失")
    return planned


def _extract(zf: zipfile.ZipFile, staging: Path, bundle: str, system: str):
    links = []
    for info, parts, kind, mode in _members(zf, bundle, system):
        path = staging.joinpath(*parts)
        if kind == "dir":
            path.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if kind == "link":
            target = zf.read(info).decode("utf-8")
            # Electron's framework links use child paths such as Versions/Current/A.
            # Keeping link targets relative and descending prevents any link chain
            # from escaping the signed bundle, including before final validation.
            member_parts(target)
            links.append((path, target))
        else:
            with zf.open(info) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            if path.stat().st_size != info.file_size:
                raise ValueError("投稿组件解压后长度不匹配")
            if system == "darwin":
                path.chmod(0o644 | (mode & 0o111))
    for path, target in links:
        path.symlink_to(target)
    for path, _ in links:
        try:
            inside = path.resolve(strict=True).is_relative_to((staging / bundle).resolve())
        except (OSError, RuntimeError):
            inside = False
        if not inside:
            raise ValueError("投稿组件内含无效链接")


def extract_component_archive(archive: Path, destination: Path, system: str) -> Path:
    """Return the bundle and put the matching notices beside it in an empty directory."""
    bundle = BUNDLES.get(system)
    if not bundle:
        raise ValueError("投稿组件压缩包平台不受支持")
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("投稿组件压缩包大小不符合要求")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (destination.is_symlink() or not destination.is_dir() or any(destination.iterdir())
            or getattr(destination.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("投稿组件临时目录必须为空")
    with tempfile.TemporaryDirectory(prefix=".unpack-", dir=destination) as temp:
        staging = Path(temp)
        try:
            with zipfile.ZipFile(archive) as zf:
                _extract(zf, staging, bundle, system)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError) as exc:
            raise ValueError("投稿组件压缩包校验失败") from exc
        # No archive-specified symlink exists while we create regular files.
        # Publish both completed trees to the caller's private working directory.
        os.replace(staging / bundle, destination / bundle)
        try:
            os.replace(staging / LICENSE_DIR, destination / LICENSE_DIR)
        except OSError:
            os.replace(destination / bundle, staging / bundle)
            raise
    return destination / bundle
