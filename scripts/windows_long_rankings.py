#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Install the pinned Windows client and verify the new long rankings with isolated data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch

from windows_install_smoke import (
    ROOT, OwnedProcess, health, installed_service, need, reference_archive,
    run_in_job, server_pid, write_json,
)

REPOSITORY = "ShiQi-Han03/fagao-scan-workbench"
PORT = 18376


def captured(command, cwd):
    run = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", timeout=210)
    if run.returncode:
        raise RuntimeError("Acceptance command failed: " + run.stdout[-2000:])
    return run.stdout


def verify_platforms(source, receipt):
    from fagao.long_rankings import COLLECTORS

    registered = sorted(COLLECTORS)
    need(registered, "No new long ranking collector installed")
    tests = source / "tests"
    need(all((tests / f"test_{name}_rank.py").is_file() for name in registered),
         "An enabled platform has no parser fixture")
    backend_tests = 0
    for pattern in ("test_long_platform_flow.py", "test_*_rank.py"):
        output = captured([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                           "-p", pattern, "-v"], source)
        result = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
        need(result and int(result[1]) > 0 and output.strip().endswith("OK"),
             f"The backend regression {pattern} did not run cleanly")
        backend_tests += int(result[1])
    receipt["backend_tests"] = backend_tests

    # Both the live loopback fixture and its DOM run the installed release.
    # The harness supplies an isolated account and intercepts every upstream fetch.
    ui_output = captured([sys.executable, "-B", "tests/run_ui_smoke.py"], source)
    reports = [json.loads(line) for line in ui_output.splitlines() if line.startswith("{")]
    ui, = [row for row in reports if row.get("passed") is True]
    need(sorted(ui["long_platforms"]) == registered and ui["long_platform_checks"] > 0
         and ui["external_platform_requests"] == 0, "New platform UI coverage incomplete")
    receipt.update(ui=ui, platforms=registered)


def verify_install(root, receipt):
    commit = os.environ["GITHUB_SHA"]
    need(os.name == "nt" and struct.calcsize("P") == 8 and platform.machine().upper() in ("AMD64", "X86_64"),
         "A native Windows x64 runner is required")
    need(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and health(PORT) is None,
         "Unexpected repository or occupied workbench port")
    archive, source_sha, files = reference_archive(root, REPOSITORY, commit)
    bootstrap = root / "bootstrap.py"
    bootstrap.write_bytes(subprocess.check_output(["git", "show", commit + ":scripts/bootstrap.py"], cwd=ROOT))
    data = root / "本机资料"
    source = data / "app" / "releases" / commit
    receipt.update(source_commit=commit, source_archive_sha256=source_sha, source_tree_files=files,
                   installer_sha256=hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
                   platform=platform.platform(), python=platform.python_version())
    command = [sys.executable, str(bootstrap), "--repo", REPOSITORY, "--commit", commit,
               "--archive-file", str(archive), "--archive-sha256", source_sha, "--no-browser"]
    environment = {k: v for k, v in os.environ.items() if k.upper() not in
                   {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PAT", "FAGAO_SUBMISSION_ARCHIVE"}}
    environment.update(FAGAO_DATA_DIR=str(data), FAGAO_QA_NODE_MODULES=str(ROOT / "node_modules"))
    owned = None
    with patch.dict(os.environ, environment, clear=True):
        try:
            code, installed = run_in_job(command, root / "first install", allow_breakaway=True, timeout=360)
            need(code == 0 and installed.get("release_id") == commit and Path(installed["release_dir"]) == source,
                 "Pinned candidate installation failed: " + str(installed.get("message", ""))[:500])
            _, owned, observed = installed_service(installed, data, PORT)
            receipt["app_version"] = observed["version"]

            pin, = [p for p in json.loads((source / "scripts/submission-component-builds.json").read_bytes())["builds"]
                    if p["platform"] == "win32" and p["arch"] == "x64"]
            component = json.loads((data / "components/submission/receipt.json").read_bytes())
            need(installed["submission_component"]["status"] == "installed"
                 and component["asar_sha256"] == pin["asar_sha256"], "Personal submission asset not installed")
            receipt.update(component_url=pin["archive_url"], component_archive_sha256=pin["archive_sha256"])

            code, entry = run_in_job([sys.executable, str(data / "app/open-workbench.py")], root / "open",
                                     allow_breakaway=False)
            need(code == 0 and entry["status"] == "reused" and server_pid(data) == owned.pid,
                 "Standard workbench entry failed")
            receipt["default_workbench_entry"] = True

            sys.path.insert(0, str(source))
            from fagao.store import Store
            from fagao.submissions import SubmissionDrafts
            store = Store(data)
            saved = store.save_snapshot({"platform": "fanqie", "board": "验收榜",
                                         "books": [{"id": "test-1", "title": "合成书目", "rank": 1}]})
            drafts = SubmissionDrafts(data)
            draft = drafts.save({"work_title": "测试草稿", "pen_name": "", "subject": "", "body": "合成稿。"})
            code, repeat = run_in_job(command, root / "repeat", allow_breakaway=True, timeout=180)
            need(code == 0 and repeat["status"] == "ready" and repeat["service"] == "reused"
                 and server_pid(data) == owned.pid and store.snapshot(saved["id"]) == saved
                 and drafts.read({"id": draft["id"]}) == draft, "Repeat install changed the service or local data")
            receipt["repeat_preserved_data"] = True
            verify_platforms(source, receipt)
        finally:
            if owned is None:
                observed = health(PORT)
                if observed and Path(observed.get("data_dir", "")).resolve() == data and Path(
                        observed.get("source_root", "")).resolve() == source:
                    owned = OwnedProcess(server_pid(data))
            if owned is not None:
                owned.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = {"status": "running", "cleanup_completed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="发糕 Windows 长篇 ") as fixture:
            root = Path(fixture).resolve()
            verify_install(root, receipt)
        receipt["cleanup_completed"] = not root.exists()
        need(receipt["cleanup_completed"], "Temporary installation still exists")
        receipt["status"] = "passed"
    except Exception as error:
        receipt.update(status="failed", error_type=type(error).__name__, error=str(error)[:2000])
        raise
    finally:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.receipt, receipt)


if __name__ == "__main__":
    main()
