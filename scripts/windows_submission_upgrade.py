#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Exercise the released dev.14 → candidate update on a clean, native Windows runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from windows_install_smoke import (
    ROOT, OwnedProcess, health, matching_health, need, run_in_job, server_pid, write_json,
)

sys.path.insert(0, str(ROOT))
from fagao.store import Store

PREVIOUS = "31fe8d65effa0d36b04f353dfdfa3a9e7e80a4fa"
PREVIOUS_INSTALLER = "ce83225cce4c6f69d21ea6dec18e788adc31daac698a4c7c37c659a7e00cb0fa"
PREVIOUS_SOURCE = "8f844fd7d17d0d90cde0f929a1a39af244163386d1c49f63ddfd60014a33110c"
PORT = 18376
RUNTIME_DLL = Path("FagaoSubmission/ffmpeg.dll")


def local_json(path, payload=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", data=None if payload is None else json.dumps(payload).encode(),
        headers={"Origin": f"http://127.0.0.1:{PORT}", "X-Fagao-Request": "1", "Content-Type": "application/json"})
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
        need(hashlib.sha256(old_script).hexdigest() == PREVIOUS_INSTALLER, "dev.14 installer SHA differs")
        old_file = root / "dev14-bootstrap.py"
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
                            "--archive-sha256", PREVIOUS_SOURCE], "install dev14")
        previous_source = Path(previous["release_dir"])
        old_process = capture(previous_source)
        component = data / "components/submission"
        runtime_dll = component / RUNTIME_DLL
        need(previous["release_id"] == PREVIOUS and local_json("/api/submissions/component")["available"],
             "fixture is not the publicly released dev.14 with its personal submission component")
        dll_sha = digest(runtime_dll)
        older_draft = local_json("/api/submissions/save", {
            "work_title": "升级前草稿", "pen_name": "测试笔名", "subject": "合成主题", "body": "升级前已经保存。"
        })["draft"]
        older_draft_path = "/api/submissions/draft?id=" + older_draft["id"]
        rankings = Store(data)
        ranking = rankings.save_snapshot({"platform": "fanqie", "board": "验收榜", "data_date": "2026-11-11",
                                          "books": [{"id": "fixture-book-1", "title": "测试榜单作品", "rank": 1}]})
        runtime_dll.unlink()
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
        need(digest(runtime_dll) == dll_sha, "upgrade did not restore the missing dev.14 runtime DLL")
        need(local_json(older_draft_path)["draft"] == older_draft, "upgrade changed the preexisting draft")
        need(rankings.snapshot(ranking["id"]) == ranking, "upgrade changed the preexisting workbench ranking")
        receipt.update(previous_damaged_component_repaired=True, previous_draft_preserved=True,
                       previous_ranking_preserved=True, runtime_dll=RUNTIME_DLL.as_posix(),
                       runtime_dll_sha256=dll_sha)
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
        need(receipt["native_ui"]["checks"]["fixture_native_processes_closed"] is True,
             "fixture editor must be closed before simulating a damaged DLL")
        drafts = local_json("/api/submissions/drafts")["drafts"]
        selected = [row for row in drafts if row["work_title"] == "发糕 Windows 试投稿"]
        need(len(selected) == 1, "expected the saved draft from the visible workbench")
        draft_path = "/api/submissions/draft?id=" + selected[0]["id"]
        original = local_json(draft_path)["draft"]
        need(original["work_title"] == "发糕 Windows 试投稿" and original["body"], "expected synthetic draft missing")
        personal_state = data / "submission-native-profile/novel-submission-state.json"
        previous_personal_state = digest(personal_state)
        editors = json.loads(personal_state.read_text(encoding="utf-8"))["recipients"]
        need(len(editors) == 226 and sum(bool(row["enabled"]) for row in editors) == 225,
             "user's single contact change was not saved before update")
        runtime_dll.unlink()
        broken = local_json("/api/submissions/component")
        need(broken["platform"] == "win32" and broken["available"] is False and "修复" in broken["message"],
             "component status did not identify the missing runtime DLL as repairable")
        repeated = install(upgrade_cmd, "repair using the ordinary installer")
        need(repeated["status"] == "ready" and repeated["service"] == "reused"
             and server_pid(data) == current.pid, "repeat did not reuse official workbench")
        need(local_json(draft_path)["draft"] == original, "repeat changed the saved manuscript")
        need(digest(personal_state) == previous_personal_state, "repeat changed local contact settings or personal records")
        need(digest(runtime_dll) == dll_sha and local_json("/api/submissions/component")["available"],
             "ordinary installer did not restore a ready personal component and the original DLL")
        matching_health(PORT, data, candidate_source)
        need(rankings.snapshot(ranking["id"]) == ranking, "repair changed the existing workbench ranking")
        receipt.update(missing_dll_reported_repairable=True, repair_restored_original_dll=True,
                       workbench_healthy_after_repair=True)
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
