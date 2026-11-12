#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Provision the build selected by the verified workbench source."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

# The raw bootstrap runs this file with -I from the already verified release.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import install_submission_component as component
from scripts.submission_archive import extract_component_archive
from scripts.submission_download import download_component


def selected_build(pins: list[dict], system: str, arch: str) -> dict | None:
    supported = {"darwin": {"arm64", "x64"}, "win32": {"x64"}}
    if arch not in supported.get(system, set()):
        return None
    matches = [p for p in pins if p["platform"] == system
               and (p["arch"] == arch or (system == "darwin" and p["arch"] == "universal"))]
    if len(matches) != 1:
        raise ValueError("官方组件清单没有唯一匹配本机的版本。")
    pin = matches[0]
    if (not isinstance(pin.get("archive_url"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(pin.get("archive_sha256", "")))
            or type(pin.get("size_bytes")) is not int
            or not 0 < pin["size_bytes"] <= 512 * 1024 * 1024):
        raise ValueError("官方投稿组件缺少有效的下载校验记录。")
    return pin


def copy_archive(source: Path, destination: Path, pin: dict) -> None:
    if source.stat().st_size != pin["size_bytes"] or not source.is_file():
        raise ValueError("本机投稿组件包大小与官方发布记录不符。")
    h = hashlib.sha256()
    written = 0
    with source.open("rb") as src, destination.open("xb") as dest:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            written += len(chunk)
            if written > pin["size_bytes"]:
                raise ValueError("本机投稿组件包大小与官方发布记录不符。")
            h.update(chunk)
            dest.write(chunk)
    if written != pin["size_bytes"] or h.hexdigest() != pin["archive_sha256"]:
        raise ValueError("本机投稿组件包与官方 SHA-256 不符。")


def provision(data: Path, *, system: str, arch: str, archive: Path | None = None) -> dict:
    pins = component.read_pins()
    pin = selected_build(pins, system, arch)
    result = {"platform": system, "arch": arch}
    if pin is None:
        return {**result, "status": "unsupported", "drafts_available": True}
    result["archive_sha256"] = pin["archive_sha256"]
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    component.require_directory(data)
    parent = data / "components"
    parent.mkdir(mode=0o700, exist_ok=True)
    component.require_directory(parent)
    target = parent / "submission"
    previous = parent / ".submission-previous"
    prior = component.read_pins("prior_installations")
    # A verified repeat installation works offline even when the original ZIP
    # is no longer available. Skip only an intact, matching complete tree.
    if os.path.lexists(target) and not os.path.lexists(previous):
        with component.component_lock(parent):
            installed = component.inspect_installation(target, system, arch, [*pins, *prior])
            if installed and installed["tree_sha256"] == pin["tree_sha256"]:
                return {**result, "status": "already_installed"}
    with tempfile.TemporaryDirectory(prefix=".submission-download-", dir=parent) as temp:
        staging = Path(temp)
        verified = staging / "component.zip"
        if archive is not None:
            copy_archive(archive, verified, pin)
        else:
            download_component(pin["archive_url"], verified, pin["archive_sha256"], pin["size_bytes"])
        bundle = extract_component_archive(verified, staging / "expanded", system)
        status = component.install(bundle, data, system, arch, pins, prior)
    return {**result, "status": status}


def main() -> int:
    parser = argparse.ArgumentParser(description="随工作台核验并安装适合本机的小说投稿组件")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--component-archive", type=Path, default=os.environ.get("FAGAO_SUBMISSION_ARCHIVE") or None)
    args = parser.parse_args()
    try:
        info = provision(args.data_dir, system=sys.platform, arch=component.host_architecture(),
                         archive=args.component_archive)
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(info, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
