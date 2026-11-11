#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Install the public release anonymously into a fresh native Windows fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from unittest.mock import patch

from windows_install_smoke import ROOT, OwnedProcess, health, matching_health, need, run_in_job, server_pid, write_json

PORT = 18376
REPOSITORY = "ShiQi-Han03/fagao-scan-workbench"


def blob(commit: str, name: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)


def public_installer(commit: str, destination: Path) -> str:
    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/scripts/bootstrap.py"
    request = urllib.request.Request(url, headers={"User-Agent": "Fagao-Workbench-Windows-Acceptance"})
    with urllib.request.urlopen(request, timeout=45) as response:
        need(response.status == 200 and response.geturl() == url, "Unexpected public installer response")
        content = response.read(1024 * 1024 + 1)
    need(len(content) <= 1024 * 1024 and content == blob(commit, "scripts/bootstrap.py"),
         "The anonymous installer does not match its Git blob")
    destination.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def acceptance(source_sha: str, receipt: dict) -> None:
    need(os.name == "nt", "A native Windows host is required")
    need(os.environ.get("GITHUB_REPOSITORY", "").lower() == REPOSITORY.lower(), "Unexpected repository")
    commit = os.environ["GITHUB_SHA"]
    need(re.fullmatch(r"[a-f0-9]{40}", commit) and re.fullmatch(r"[a-f0-9]{64}", source_sha),
         "Expected fixed source and archive hashes")
    need(health(PORT) is None, "Default workbench port already in use")
    pins = json.loads(blob(commit, "scripts/submission-component-builds.json"))["builds"]
    pin, = [p for p in pins if p["platform"] == "win32" and p["arch"] == "x64"]
    need(pin["archive_url"].startswith(f"https://github.com/{REPOSITORY}/releases/download/"),
         "Expected an official GitHub release asset")
    receipt.update(platform=platform.platform(), python=platform.python_version(), source_commit=commit,
                   source_archive_sha256=source_sha, component_url=pin["archive_url"], component_archive_override=False)
    # Remove any inherited GitHub and component credentials or test-only overrides.
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PAT", "FAGAO_SUBMISSION_ARCHIVE"}}
    with tempfile.TemporaryDirectory(prefix="发糕 Windows 公开安装 ") as fixture:
        root = Path(fixture).resolve()
        data = root / "本地资料"
        source = data / "app" / "releases" / commit
        bootstrap = root / "bootstrap.py"
        receipt["installer_sha256"] = public_installer(commit, bootstrap)
        owned = None
        try:
            with patch.dict(os.environ, {**env, "FAGAO_DATA_DIR": str(data)}, clear=True):
                need(not data.exists(), "Expected clean installer data")
                cmd = [sys.executable, str(bootstrap), "--repo", REPOSITORY, "--commit", commit,
                       "--archive-sha256", source_sha, "--no-browser"]
                code, result = run_in_job(cmd, root / "public install", allow_breakaway=True, cwd=root, timeout=420)
            need(code == 0 and result.get("status") == "ready", f"Public bootstrap failed: {result.get('error')}")
            need(result["release_id"] == commit and Path(result["release_dir"]).resolve() == source,
                 "Default install path or revision differs")
            matching_health(PORT, data, source)
            owned = OwnedProcess(server_pid(data))
            download = result["download"]
            need(download["archive_sha256"] == source_sha and download["transport"] in ("python_https", "windows_system_curl"),
                 "The source archive did not use a public HTTPS transport")
            provision = result["submission_component"]
            need(provision["status"] == "installed" and provision["archive_sha256"] == pin["archive_sha256"],
                 "The fresh install did not fetch the pinned component")
            component = data / "components" / "submission"
            installed = json.loads((component / "receipt.json").read_bytes())
            with (component / "FagaoSubmission/resources/app.asar").open("rb") as stream:
                asar_sha = hashlib.file_digest(stream, "sha256").hexdigest()
            need(asar_sha == installed["asar_sha256"] == pin["asar_sha256"], "Installed renderer checksum differs")
            need(installed["edition"] == "personal" and installed["platform"] == "win32", "Unexpected editor edition")
            fixed = data / "app/open-workbench.py"
            code, entry = run_in_job([sys.executable, str(fixed)], root / "open default entry", allow_breakaway=False)
            need(code == 0 and entry.get("status") == "reused" and server_pid(data) == owned.pid,
                 "The standard workbench entry did not reuse the installed service")
            receipt.update(component_transport="anonymous_https_release_download", source_transport=download["transport"],
                           component_archive_sha256=pin["archive_sha256"], component_asar_sha256=asar_sha,
                           default_workbench_entry=True)
            native = root / "native-ui.json"
            node = shutil.which("node")
            need(node, "Node is required for Windows browser acceptance")
            ui = subprocess.run([node, str(ROOT / "scripts/windows_submission_ui.cjs"), "--data-dir", str(data),
                                 "--url", f"http://127.0.0.1:{PORT}/", "--receipt", str(native)], cwd=ROOT, timeout=240)
            if native.exists():
                receipt["native_ui"] = json.loads(native.read_bytes())
            need(ui.returncode == 0 and receipt.get("native_ui", {}).get("status") == "passed",
                 "Native composer acceptance failed")
        finally:
            if owned is None:
                observed = health(PORT)
                if observed and Path(observed.get("source_root", "")).resolve() == source and Path(
                        observed.get("data_dir", "")).resolve() == data:
                    owned = OwnedProcess(server_pid(data))
            if owned is not None:
                owned.close()
    receipt["cleanup_completed"] = not root.exists()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = {"status": "running", "cleanup_completed": False}
    try:
        acceptance(args.source_archive_sha256, result)
        need(result["cleanup_completed"], "The temporary install directory still exists")
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error)[:1000])
        raise
    finally:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.receipt, result)


if __name__ == "__main__":
    main()
