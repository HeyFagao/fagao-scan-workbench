#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Exercise the released dev.12 → candidate update on a clean, native Windows runner."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

from windows_install_smoke import (
    ROOT, OwnedProcess, health, matching_health, need, run_in_job, server_pid, write_json,
)

PREVIOUS = "0529ddd44b84b581cdf296fe4aa140edb2c9382f"
PREVIOUS_INSTALLER = "47c2bb1472f78006aa4a2f3262e30559843bd32af0161bfe05129a821f6b15c5"
PREVIOUS_SOURCE = "f6efbdc826a8dc0e493dc82feeec6ec672f8f23cc9e78a9a9d66c1dc3a858c00"
PORT = 18376


def local_json(path):
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", headers={"X-Fagao-Request": "1"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=25) as response:
        return json.load(response)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, receipt):
    need(os.name == "nt", "requires a real Windows host")
    need(not health(PORT), "official port already has a service")
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = os.environ["GITHUB_SHA"]
    first = json.loads(args.install_receipt.read_text(encoding="utf-8"))
    need(first["status"] == "passed" and first["source_commit"] == commit, "first installation evidence missing")
    receipt.update(source_commit=commit, source_archive_sha256=first["source_archive"]["sha256"],
                   previous_commit=PREVIOUS, fresh_install_component_ready=first["submission_component_ready"],
                   native_ui={}, platform=platform.platform(), python=platform.python_version())
    need(receipt["fresh_install_component_ready"], "clean install did not include the submission component")
    root = Path(tempfile.mkdtemp(prefix="投稿 Windows upgrade ")).resolve()
    data = root / "稿件资料"
    app = data / "app"
    old_data_env = os.environ.get("FAGAO_DATA_DIR")
    os.environ["FAGAO_DATA_DIR"] = str(data)
    owned = []
    try:
        request = urllib.request.Request(f"https://raw.githubusercontent.com/{repository}/{PREVIOUS}/scripts/bootstrap.py",
                                        headers={"User-Agent":"Fagao-Workbench-Windows-Acceptance"})
        with urllib.request.urlopen(request, timeout=35) as response:
            old_script = response.read(1024 * 1024)
        need(hashlib.sha256(old_script).hexdigest() == PREVIOUS_INSTALLER, "dev.12 installer SHA differs")
        old_file = root / "dev12-bootstrap.py"
        old_file.write_bytes(old_script)

        def install(command, stage, expect=0, cwd=ROOT):
            code, result = run_in_job(list(map(str, command)), root / stage, allow_breakaway=True,
                                      timeout=300, cwd=cwd)
            if code != expect:
                raise RuntimeError(f"{stage}: {code}: {result.get('status')} {result.get('error')}")
            return result

        def capture(source):
            matching_health(PORT, data, source)
            process = OwnedProcess(server_pid(data))
            owned.append(process)
            return process

        common = ["--repo", repository, "--no-browser"]
        previous = install([sys.executable, old_file, *common, "--commit", PREVIOUS,
                            "--archive-sha256", PREVIOUS_SOURCE], "install dev12")
        previous_source = Path(previous["release_dir"])
        old_process = capture(previous_source)
        need(previous["release_id"] == PREVIOUS and not (data / "components/submission").exists(),
             "fixture is not the publicly released pre-submission workbench")
        sentinel = data / "existing-ranking-data.sqlite3"
        with closing(sqlite3.connect(sentinel)) as db:
            db.execute("CREATE TABLE existing_favorite (title TEXT NOT NULL)")
            db.execute("INSERT INTO existing_favorite VALUES (?)", ("原有测试收藏",))
            db.commit()
        prior_pointer = (app / "current.json").read_bytes()
        upgrade_cmd = [sys.executable, ROOT / "scripts/bootstrap.py", *common, "--commit", commit,
                       "--archive-sha256", receipt["source_archive_sha256"]]
        pending = install(upgrade_cmd, "upgrade while running", expect=2)
        need(pending.get("status") == "restart_required" and (app / "current.json").read_bytes() == prior_pointer,
             "update did not preserve the active release pointer")
        matching_health(PORT, data, previous_source)
        old_process.close()
        resumed = install(pending["resume"]["argv"], "complete official upgrade", cwd=pending["resume"]["cwd"])
        candidate_source = Path(resumed["release_dir"])
        current = capture(candidate_source)
        need(resumed["release_id"] == commit and resumed["status"] == "ready", "candidate did not become ready")
        need(local_json("/api/submissions/component")["available"], "component missing after normal update")
        component = data / "components/submission"
        pin = json.loads((component / "receipt.json").read_text(encoding="utf-8"))
        archive = Path(os.environ["FAGAO_SUBMISSION_ARCHIVE"])
        receipt.update(component_archive_sha256=digest(archive), component_asar_sha256=pin["asar_sha256"],
                       ordinary_bootstrap_upgrade=True)
        need(digest(component / "FagaoSubmission/resources/app.asar") == pin["asar_sha256"],
             "installed component ASAR differs from receipt")
        native_receipt = root / "composer-ui.json"
        node = shutil.which("node")
        need(node, "Node.js required for the acceptance browser")
        result = subprocess.run(
            [node, str(ROOT / "scripts/windows_submission_ui.cjs"), "--data-dir", str(data),
             "--url", f"http://127.0.0.1:{PORT}/", "--receipt", str(native_receipt)],
            cwd=ROOT, timeout=240)
        if native_receipt.exists():
            receipt["native_ui"] = json.loads(native_receipt.read_text(encoding="utf-8"))
        need(result.returncode == 0 and receipt["native_ui"].get("status") == "passed",
             "actual Windows workbench and native composer did not complete the UI checks")
        drafts = local_json("/api/submissions/drafts")["drafts"]
        need(len(drafts) == 1, "expected one draft created by the visible workbench")
        draft_path = "/api/submissions/draft?id=" + drafts[0]["id"]
        original = local_json(draft_path)["draft"]
        need(original["work_title"] == "发糕 Windows 试投稿" and original["body"], "expected synthetic draft missing")
        personal_state = data / "submission-native-profile/novel-submission-state.json"
        previous_personal_state = digest(personal_state)
        editors = json.loads(personal_state.read_text(encoding="utf-8"))["recipients"]
        need(len(editors) == 226 and sum(bool(row["enabled"]) for row in editors) == 225,
             "user's single contact change was not saved before update")
        repeated = install(upgrade_cmd, "repeat ordinary installation")
        need(repeated["status"] == "ready" and repeated["service"] == "reused"
             and server_pid(data) == current.pid, "repeat did not reuse official workbench")
        need(local_json(draft_path)["draft"] == original, "repeat changed the saved manuscript")
        need(digest(personal_state) == previous_personal_state, "repeat changed local contact settings or personal records")
        with closing(sqlite3.connect(sentinel)) as db:
            need(db.execute("SELECT title FROM existing_favorite").fetchall() == [("原有测试收藏",)],
                 "upgrade changed the existing ranking data")
        receipt.update(original_data_preserved=True, repeat_preserves_draft=True, repeat_preserves_native_state=True,
                       automatic_component_receipt=True, website_account_used=False, mail_sent=False)
    finally:
        if old_data_env is None:
            os.environ.pop("FAGAO_DATA_DIR", None)
        else:
            os.environ["FAGAO_DATA_DIR"] = old_data_env
        observed = health(PORT)
        if observed and Path(observed.get("data_dir", "")).resolve() == data:
            pid = server_pid(data)
            if not any(p.pid == pid and p.handle for p in owned):
                owned.append(OwnedProcess(pid))
        for proc in reversed(owned):
            proc.close()
        for attempt in range(10):
            try:
                shutil.rmtree(root)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.5)
        receipt["cleanup_completed"] = not root.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-receipt", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = {"status":"running", "cleanup_completed":False}
    try:
        run(args, receipt)
        receipt["status"] = "passed"
    except Exception as error:
        receipt.update(status="failed", error=str(error)[:1200], error_type=type(error).__name__)
        raise
    finally:
        write_json(args.receipt, receipt)


if __name__ == "__main__":
    main()
