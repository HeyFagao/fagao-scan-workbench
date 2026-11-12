#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Exercise the observed GitHub namespace and a real installed Windows workbench."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import struct
import sys
import tempfile
from urllib import error, request
from urllib.parse import urlsplit
from unittest.mock import patch

from windows_install_smoke import (
    ROOT, OwnedProcess, health, installed_service, need, reference_archive, run_in_job,
    server_pid, write_json,
)
from windows_public_submission_install import public_installer

OLD, NEW = "ShiQi-Han03", "HeyFagao"
WORKBENCH, FEED = "fagao-scan-workbench", "fagao-ranking-feed"
OWNER_ID, WORKBENCH_ID, FEED_ID = 153971533, 1452172195, 1453901671
PREVIOUS = "a643a95d48f7c1b182f31e32f814d9a6091dbe37"
OLD_BOOTSTRAP = "ce83225cce4c6f69d21ea6dec18e788adc31daac698a4c7c37c659a7e00cb0fa"
OLD_SOURCE = "6d796ebb28a93fa3cd222f7365d3be6d39e301d19860d4d00091b3b04126a6e1"
PORT = 18376
ASSETS = {"FagaoSubmission-macos-arm64.zip": 736048269, "FagaoSubmission-macos-x64.zip": 736048287,
          "FagaoSubmission-windows-x64.zip": 736048271}
# Public encrypted publication receipts. These fixtures contain no account information or keys.
FEEDS = {
    "douyin": ("4378471a2079c03f53ddba585b9a42e405edfd8c", "b7819c275c43db4b17a7a365d1dd6b90828e41e85c3e0136894592f9b1f4841d", 7652,
               100, '2026-11-12', '2026-11-12T10:02:03+00:00'),
    "hongguo": ("708022c1872ec4177eac48f6ff107d5fae8b6f9a", "854924ad7ecadaa95753acd28aabfee39f3887e985fc71a97b640fbede45259e", 9356,
                96, '2026-11-12', '2026-11-12T10:05:25+00:00'),
    "zhihu": ("2afc843f753680f00b96725ce721c24d30011384", "8725cd49be420fb2dd9e35d83cc9fd2ecf376391a611797f37ac41d5c5e94c31", 4664,
              83, '2026-11-06', '2026-11-12T04:35:02+00:00'),
    "fanqie_story": ("ac14e41690c180587fb1943fb62cf2a04944512e", "217deacfc40ca0439f9bfaa66ae64ee82a3a35e92f3122e5e1dcdba4fcb3381a", 4748,
                     100, '2026-11-11', '2026-11-12T02:09:09+00:00'),
    "adapt": ("7d4e7277a64b207e9d2823b65df4e00677951e1e", "e4572c7fcddf45f54301744f04e5a74d4dab7a9ca0c5fe18c1e65a0da9dec755", 8632,
              100, None, '2026-11-12T02:12:41+00:00'),
}


def verify_identity(identities, expected, receipt):
    account, wb, feed, release = (identities[k] for k in ("account", "workbench", "feed", "release"))
    owner = account["login"]
    need(owner in (OLD, NEW) and account["id"] == OWNER_ID, "Unexpected GitHub account")
    for repo, name, repo_id in ((wb, WORKBENCH, WORKBENCH_ID), (feed, FEED, FEED_ID)):
        need(repo["id"] == repo_id and repo["owner"]["id"] == OWNER_ID
             and repo["full_name"] == f"{owner}/{name}" and not repo["private"], "Unexpected public repository")
    need(int(os.environ["GITHUB_REPOSITORY_ID"]) == WORKBENCH_ID
         and os.environ["GITHUB_REPOSITORY"].lower() == wb["full_name"].lower(), "Unexpected CI checkout")
    phase = "before" if owner == OLD else "after"
    ref = os.environ["GITHUB_REF_NAME"]
    if "-pre-" in ref:
        need(phase == "before", "Pre-rename ref requires the old live owner")
    elif "-after-" in ref:
        need(phase == "after", "Post-rename ref requires the new live owner")
    if expected != "from-ref":
        need(expected == phase, "Requested and observed phases differ")
    need(release["id"] == 441893302 and release["tag_name"] == "v0.7.0-dev.13" and not release["draft"],
         "Unexpected submission asset release")
    assets = {a["name"]: a for a in release["assets"]}
    need({name: a["id"] for name, a in assets.items()} == ASSETS, "Original public assets changed")
    receipt.update(phase=phase, owner=owner, owner_id=OWNER_ID, workbench_repository_id=WORKBENCH_ID,
                   feed_repository_id=FEED_ID, submission_release_id=release["id"])
    return assets


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_link(url, *, digest=None, limit=0):
    # A successful alternate path does not prove the originally requested URL still serves bytes.
    method = "GET" if digest else "HEAD"
    req = request.Request(url, method=method, headers={"User-Agent": "Fagao-Public-Rename-Acceptance"})
    try:
        with request.build_opener(NoRedirect()).open(req, timeout=18) as response:
            result = {"direct_http": response.status}
            if digest:
                data = response.read(limit + 1)
                result["direct_matches"] = len(data) <= limit and hashlib.sha256(data).hexdigest() == digest
            return result
    except error.HTTPError as exc:
        with exc:
            location = exc.headers.get("Location", "")
            return {"direct_http": exc.code, "direct_matches": False, "redirect_host": urlsplit(location).hostname}
    except OSError as exc:
        return {"direct_http": None, "direct_matches": False, "error_type": type(exc).__name__}


def check_pins(source, assets):
    builds = json.loads((source / "scripts/submission-component-builds.json").read_bytes())["builds"]
    need(len(builds) == 3, "Expected three pinned submission components")
    for pin in builds:
        asset = assets[pin["archive_url"].rsplit("/", 1)[-1]]
        need(asset["state"] == "uploaded" and asset["size"] == pin["size_bytes"]
             and asset["digest"] == "sha256:" + pin["archive_sha256"], "Release asset pin changed")
    return next(p for p in builds if p["platform"] == "win32" and p["arch"] == "x64")


def test_feeds(source, data, owner, receipt):
    sys.path.insert(0, str(source))
    from fagao import daily_feeds, network
    network.configure(data)
    opener_factory = network.build_opener
    for board, (commit, checksum, size, rows, day, observed) in FEEDS.items():
        aliases = {}
        receipt["feeds"][board] = {"sha256": checksum, "byte_length": size, "commit": commit, "aliases": aliases}
        for alias in (owner, NEW if owner == OLD else OLD):
            url = f"https://raw.githubusercontent.com/{alias}/{FEED}/{commit}/objects/{board}/{checksum}.bin"
            evidence = aliases[alias] = probe_link(url, digest=checksum, limit=size)
            manifest = dict(version=1, format=daily_feeds.FORMAT, board=board, sha256=checksum,
                            artifact_url=url, byte_length=size, row_count=rows, source_date=day, observed_at=observed)
            daily_feeds.validate_manifest(manifest, board)
            routes = []

            def observing(*handlers):
                opener = opener_factory(*handlers)
                real_open = opener.open

                def logged(req, timeout):
                    need(not any(k.lower() in ("authorization", "cookie", "proxy-authorization")
                                 for k, _ in req.header_items()), "Unexpected credential header")
                    response = real_open(req, timeout=timeout)
                    dest = urlsplit(response.geturl())
                    resolved_owner = dest.path.split("/")[2 if dest.path.startswith("/gh/") else 1]
                    routes.append({"host": dest.hostname, "owner": resolved_owner})
                    return response

                opener.open = logged
                return opener
            with patch.object(network, "build_opener", observing):
                token = daily_feeds._download(url, size)
            need(routes and len(token) == size and hashlib.sha256(token).hexdigest() == checksum,
                 "Candidate did not retrieve the fixed public ciphertext")
            evidence.update(candidate_verified=True, candidate_route=routes[-1])


def old_links(receipt):
    oldrepo = f"{OLD}/{WORKBENCH}"
    asset_label = "FagaoSubmission-windows-x64.zip"
    receipt["dev16_old_links"] = {
        "installer": probe_link(f"https://raw.githubusercontent.com/{oldrepo}/{PREVIOUS}/scripts/bootstrap.py",
                                digest=OLD_BOOTSTRAP, limit=1024 * 1024),
        "source": probe_link(f"https://codeload.github.com/{oldrepo}/zip/{PREVIOUS}",
                             digest=OLD_SOURCE, limit=50 * 1024 * 1024),
        "component_initial_head": probe_link(f"https://github.com/{oldrepo}/releases/download/v0.7.0-dev.13/{asset_label}"),
    }


def install(command, step, *, expected=0):
    code, result = run_in_job(command, step, allow_breakaway=True, timeout=420)
    need(code == expected, f"{step.name} failed: {code}, {result.get('code')}, {result.get('status')}")
    return result


@contextmanager
def serving(result, data):
    owned = None
    try:
        source, owned, observed = installed_service(result, data, PORT)
        yield source, observed, owned.pid
    finally:
        if owned is None:
            observed = health(PORT)
            if observed and Path(observed.get("data_dir", "")).resolve() == data:
                owned = OwnedProcess(server_pid(data))
        if owned is not None:
            owned.close()


def draft_request(path, payload=None):
    url = f"http://127.0.0.1:{PORT}"
    req = request.Request(url + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers={"X-Fagao-Request": "1", "Content-Type": "application/json", "Origin": url})
    with request.build_opener(request.ProxyHandler({})).open(req, timeout=25) as response:
        return json.load(response)


def run_windows(fixture, identities, phase, receipt):
    need(os.name == "nt" and platform.machine().lower() in ("amd64", "x86_64") and struct.calcsize("P") == 8,
         "This acceptance requires a native Windows x64 runner")
    need(health(PORT) is None, "Workbench port already in use")
    assets = verify_identity(identities, phase, receipt)
    commit, owner = os.environ["GITHUB_SHA"], receipt["owner"]
    repo = f"{owner}/{WORKBENCH}"
    receipt.update(platform=platform.platform(), python=platform.python_version(), source_commit=commit)
    old_links(receipt)
    _, checksum, files = reference_archive(fixture, repo, commit)
    bootstrap = fixture / "candidate.py"
    installer_sha = public_installer(repo, commit, bootstrap)
    receipt.update(source_archive_sha256=checksum, source_tree_files=files, installer_sha256=installer_sha)
    clean, data = fixture / "全新安装", fixture / "已有资料"
    env = {k: v for k, v in os.environ.items() if k.upper() not in
           {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT", "GH_ENTERPRISE_TOKEN", "FAGAO_SUBMISSION_ARCHIVE"}}
    cmd = [sys.executable, str(bootstrap), "--repo", repo, "--commit", commit, "--archive-sha256", checksum, "--no-browser"]
    with patch.dict(os.environ, {**env, "FAGAO_DATA_DIR": str(clean)}, clear=True):
        first = install(cmd, fixture / "new")
        with serving(first, clean) as (source, observed, pid):
            need(first["release_id"] == commit and first["submission_component"]["status"] == "installed",
                 "Fresh candidate and its submission component must install from public downloads")
            pin = check_pins(source, assets)
            component = json.loads((clean / "components/submission/receipt.json").read_bytes())
            need(component["asar_sha256"] == pin["asar_sha256"] and draft_request("/api/submissions/component")["available"],
                 "Fresh submission component incomplete")
            opened = install([sys.executable, str(clean / "app/open-workbench.py")], fixture / "default-entry")
            need(opened["status"] == "reused" and server_pid(clean) == pid, "Normal workbench entry did not reuse service")
            receipt.update(clean_install=True, app_version=observed["version"], default_workbench_entry=True,
                           component_archive_sha256=pin["archive_sha256"], component_archive_override=False)
            test_feeds(source, clean, owner, receipt)

    # Seed a genuinely installed dev.16 with the identical verified dev.13 component an existing
    # user would already have, independently of any redirects supported by the OLD bootstrap.
    shutil.copytree(clean / "components/submission", data / "components/submission")
    prior = fixture / "dev16"
    prior.mkdir()
    archive, previous_sha, _ = reference_archive(prior, repo, PREVIOUS)
    old_installer = prior / "bootstrap.py"
    need(previous_sha == OLD_SOURCE and public_installer(repo, PREVIOUS, old_installer) == OLD_BOOTSTRAP,
         "The public dev.16 source or installer differs from its receipt")
    previous_cmd = [sys.executable, str(old_installer), "--repo", repo, "--commit", PREVIOUS, "--archive-file", str(archive),
                    "--archive-sha256", OLD_SOURCE, "--no-browser"]
    with patch.dict(os.environ, {**env, "FAGAO_DATA_DIR": str(data)}, clear=True):
        previous = install(previous_cmd, fixture / "baseline")
        with serving(previous, data) as (previous_source, old_status, _):
            need(old_status["version"] == "0.7.0-dev.16" and previous["submission_component"]["status"] == "already_installed",
                 "The dev.16 baseline does not contain the original component")
            # Use dev.16's own data format to create the historical records.
            seed = """import json, sys
from fagao.network import save_settings
from fagao.store import Store
store = Store(sys.argv[1])
save_settings(store.directory, 'direct')
print(json.dumps(store.save_snapshot({'platform':'fanqie', 'board':'验收榜',
      'books':[{'id':'test-1', 'rank':1, 'title':'合成书目'}]})))
"""
            saved = json.loads(subprocess.check_output([sys.executable, "-B", "-c", seed, str(data)], cwd=previous_source))
            draft = draft_request("/api/submissions/save", {"work_title": "旧版草稿", "pen_name": "合成笔名",
                                                            "subject": "投稿验收", "body": "升级前已保存在本机。"})["draft"]
            settings = (data / "network.json").read_bytes()
            pending = install(cmd, fixture / "upgrade-while-running", expected=2)
            need(pending["status"] == "restart_required" and pending["current_switched"] is False,
                 "The active dev.16 installation was prematurely replaced")
        resumed = install(pending["resume"]["argv"], fixture / "resume")
        with serving(resumed, data) as (_, current, _):
            from fagao.store import Store
            store = Store(data)
            need(resumed["release_id"] == commit and current["version"] == receipt["app_version"]
                 and resumed["submission_component"]["status"] == "already_installed", "Candidate upgrade failed")
            need(draft_request("/api/submissions/draft?id=" + draft["id"])["draft"] == draft
                 and store.snapshot(saved["id"]) == saved and (data / "network.json").read_bytes() == settings,
                 "The user's local ranking, draft or settings changed during the upgrade")
            receipt.update(upgrade_preserved_data=True, previous_commit=PREVIOUS, previous_app_version=old_status["version"],
                           previous_component_seed="copy_of_verified_intact_release_tree", upgraded_reused_component=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identities", required=True, type=Path)
    parser.add_argument("--phase", choices=("from-ref", "before", "after"), required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    receipt = {"status": "running", "cleanup_completed": False, "feeds": {}}
    try:
        identities = json.loads(args.identities.read_bytes())
        with tempfile.TemporaryDirectory(prefix="发糕 Windows GitHub ") as temp:
            fixture = Path(temp).resolve()
            run_windows(fixture, identities, args.phase, receipt)
        receipt.update(cleanup_completed=not fixture.exists(), status="passed")
        need(receipt["cleanup_completed"], "Temporary installation not cleaned up")
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
        raise
    finally:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.receipt, receipt)


if __name__ == "__main__":
    main()
