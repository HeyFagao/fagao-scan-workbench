#!/usr/bin/env python3
# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Install the pinned Windows client and verify ranking sources with isolated data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from unittest.mock import patch

from windows_install_smoke import (
    ROOT, OwnedProcess, free_port, health, installed_service, need, official_ci_repository, reference_archive,
    run_in_job, server_pid, start_in_job, write_json,
)

PORT = 18376
PROTECTED_MODULES = {"fagao/membership.py", "fagao/runtime.py", "fagao/network.py"}


def captured(command, cwd, *, env=None):
    run = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", timeout=210, env=env)
    if run.returncode:
        raise RuntimeError("Acceptance command failed: " + run.stdout[-2000:])
    return run.stdout


def isolated_probe(source, body, *arguments):
    """Application imports must end with this child, releasing its shared lease."""
    output = captured([sys.executable, "-I", "-B", "-c",
                       "import sys;sys.path.insert(0,sys.argv[1])\n" + body,
                       str(source), *map(str, arguments)], source)
    value = json.loads(output)
    need(isinstance(value, dict), "Isolated acceptance probe returned no object")
    return value


def verify_platforms(source, receipt, *, test_source=None):
    backend_source = test_source or source
    registries = isolated_probe(source, """
import json
from fagao.long_rankings import COLLECTORS
from fagao.discovery import COLLECTORS as DISCOVERY
print(json.dumps({'long': sorted(COLLECTORS), 'discovery': sorted(DISCOVERY)}))
""")
    registered = registries["long"]
    need(registered, "No new long ranking collector installed")
    tests = source / "tests"
    need(all((tests / f"test_{name}_rank.py").is_file() for name in registered),
         "An enabled platform has no parser fixture")
    discovery = registries["discovery"]
    need(set(discovery) == {"hongguo_official", "fanqie_recommend", "dianzhong", "shuqi", "zhihu_story"},
         "The five discovery collectors were not installed")
    backend_tests = 0
    backend_checks = []
    for pattern in ("test_long_platform_flow.py", "test_*_rank.py", "test_discovery_flow.py",
                    "test_fanqie_recommend.py", "test_recommendation_runtime.py",
                    "test_insight_history_store.py", "test_ranking_trends.py", "test_zhihu_story_rank.py",
                    "test_credential_store.py", "test_membership_credential_sealing.py", "test_code_seal.py",
                    "test_integrity.py", "test_bootstrap_repair.py", "test_bootstrap.py", "test_launch.py"):
        # Opt in only for this isolated fixture process; its browser document is
        # supplied locally and every upstream request is intercepted by the test.
        environment = ({**os.environ, "FAGAO_SYNTHETIC_BROWSER_TEST": "1"}
                       if pattern == "test_fanqie_recommend.py" else None)
        if pattern == "test_credential_store.py":
            environment = {**os.environ, "FAGAO_SYNTHETIC_NATIVE_CREDENTIALS": "1"}
        output = captured([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
                           "-p", pattern, "-v"], backend_source, env=environment)
        result = re.search(r"^Ran (\d+) tests? in ", output, re.MULTILINE)
        need(result and int(result[1]) > 0
             and re.search(r"^OK(?: \(skipped=\d+\))?$", output, re.MULTILINE),
             f"The backend regression {pattern} did not run cleanly")
        skipped = re.search(r"^OK \(skipped=(\d+)\)$", output, re.MULTILINE)
        if pattern == "test_fanqie_recommend.py":
            need(re.search(r"^test_normal_page_dom_worker_has_no_profile_and_extracts_cards "
                           r"\([^\n]+\) \.\.\. ok$", output, re.MULTILINE),
                 "The isolated recommendation browser fixture did not pass")
            receipt["synthetic_browser"] = True
            receipt["synthetic_browser_tests"] = 1
            receipt["synthetic_browser_skipped"] = 0
        if pattern == "test_credential_store.py":
            native_ok = bool(re.search(r"^test_native_windows_dpapi_roundtrip_uses_only_synthetic_bytes "
                                      r"\([^\n]+\) \.\.\. ok$", output, re.MULTILINE))
            need(native_ok if os.name == "nt" else True, "The native Windows DPAPI fixture did not pass")
            receipt["synthetic_native_credentials"] = {"passed": native_ok, "native_windows": os.name == "nt",
                                                        "tests": 1 if native_ok else 0,
                                                        "skipped": 0 if native_ok else 1}
        backend_checks.append({"pattern": pattern, "tests": int(result[1]),
                               "skipped": int(skipped[1]) if skipped else 0})
        backend_tests += int(result[1])
    receipt["backend_tests"] = backend_tests
    receipt["backend_checks"] = backend_checks
    receipt["backend_source_kind"] = "verified_unsealed_archive" if test_source is not None else "source_preview"
    receipt["ui_source_kind"] = "official_sealed_installation" if test_source is not None else "source_preview"

    # Both the live loopback fixture and its DOM run the installed release.
    # The harness supplies an isolated account and intercepts every upstream fetch.
    ui_output = captured([sys.executable, "-B", "tests/run_ui_smoke.py"], source)
    reports = [json.loads(line) for line in ui_output.splitlines() if line.startswith("{")]
    ui, = [row for row in reports if row.get("passed") is True]
    need(sorted(ui["long_platforms"]) == registered and ui["long_platform_checks"] > 0
         and ui["external_platform_requests"] == 0, "New platform UI coverage incomplete")
    receipt.update(ui=ui, platforms=registered)

    # This DOM suite checks the installed five sources, fifty-book settings,
    # drama metadata, account changes and read-only access with intercepted fetches.
    source_output = captured(["node", "--test", "tests/source-upgrade-ui.cjs"], source)
    source_tests = re.search(r"^# tests (\d+)\s*$", source_output, re.MULTILINE)
    need(source_tests and int(source_tests[1]) >= 12
         and re.search(r"^# fail 0\s*$", source_output, re.MULTILINE),
         "New source UI regression did not run cleanly")
    receipt.update(discovery_platforms=discovery,
                   source_ui={"passed": True, "tests": int(source_tests[1]),
                              "external_platform_requests": 0})


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal_fingerprint(source):
    return {"receipt": sha(source / ".fagao-release.json"), "pack": sha(source / ".fagao-code-seal.json"),
            "stubs": {name: sha(source / name) for name in sorted(PROTECTED_MODULES)}}


def record_probe(source, data, sentinel, *, create=False):
    return isolated_probe(source, """
import json
from pathlib import Path
from fagao.store import Store
from fagao.submissions import SubmissionDrafts
data, sentinel = Path(sys.argv[2]), Path(sys.argv[3])
store, drafts = Store(data), SubmissionDrafts(data)
if sys.argv[4] == 'create':
    saved = store.save_snapshot({'platform': 'fanqie', 'board': '验收榜',
                                'books': [{'id': 'test-1', 'title': '合成书目', 'rank': 1}]})
    draft = drafts.save({'work_title': '测试草稿', 'pen_name': '', 'subject': '', 'body': '合成稿。'})
    sentinel.write_text(json.dumps({'snapshot': saved, 'draft': draft}), encoding='utf-8')
expected = json.loads(sentinel.read_text(encoding='utf-8'))
assert store.snapshot(expected['snapshot']['id']) == expected['snapshot'], 'Snapshot sentinel changed'
assert drafts.read({'id': expected['draft']['id']}) == expected['draft'], 'Draft sentinel changed'
print(json.dumps({'passed': True, 'snapshots': 1, 'drafts': 1}))
""", data, sentinel, "create" if create else "verify")


def program_probe(source, data, originals):
    metadata = json.loads((source / ".fagao-release.json").read_text(encoding="utf-8"))
    need(metadata.get("runtime_integrity") == 1 and metadata.get("original_files") == originals,
         "Installed original inventory differs from the verified source archive")
    seal = metadata.get("code_seal", {})
    need(set(seal.get("modules", {})) == PROTECTED_MODULES
         and seal.get("data_root") == str(data), "The three local modules were not sealed for this installation")
    for name in PROTECTED_MODULES:
        stub = (source / name).read_text(encoding="utf-8")
        need("load_module" in stub and sha(source / name) == seal["modules"][name]["stub_sha256"]
             and sha(source / name) != originals[name], "A protected module is not its expected loader stub")
    result = isolated_probe(source, """
import importlib, json
from pathlib import Path
from fagao import code_seal, integrity
root, data = Path(sys.argv[1]), Path(sys.argv[2])
receipt = integrity.read_receipt(root)
assert integrity.verify_installation(root)['checked']
assert code_seal.verify_metadata(root, receipt['code_seal'], receipt['original_files'], data)
assert code_seal.verify_seal(root, receipt['code_seal'], receipt['original_files'], data, cache=False)
pack = json.loads((root / code_seal.PACK_FILE).read_text(encoding='utf-8'))
assert pack['key_envelope']['provider'] == 'windows-dpapi'
assert 'secret' not in pack and 'key' not in pack
for relative, name in code_seal.PROTECTED_MODULES.items():
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve() == (root / relative).resolve()
assert hasattr(importlib.import_module('fagao.membership'), 'MembershipClient')
assert hasattr(importlib.import_module('fagao.runtime'), 'WorkbenchRuntime')
print(json.dumps({'passed': True, 'provider': 'windows-dpapi', 'module_count': 3,
                  'imported_in_separate_process': True, 'effective_inventory_verified': True,
                  'original_inventory_verified': True, 'os_key_unsealed': True}))
""", data)
    need(result.get("passed") is True and result.get("module_count") == 3, "Native code sealing probe failed")
    result.update(original_inventory_files=len(originals), effective_inventory_files=len(metadata["files"]))
    return result


def credential_probe(source, root, *, create=False):
    """Only synthetic credentials are opened; the real website is never contacted."""
    return isolated_probe(source, """
import base64, json, secrets
from pathlib import Path
from fagao.credential_store import CredentialStoreError, SecretProtector
from fagao.membership import MembershipClient
from tests.test_membership import Clock, Transport
root = Path(sys.argv[2]); root.mkdir(parents=True, exist_ok=True)
clock = Clock(); transport = Transport(clock)
protector = SecretProtector(root, purpose='membership-device-v1', native=True)
client = MembershipClient(root, transport, clock, credential_store=protector)
path = root / '.membership/session.json'
if sys.argv[3] == 'create':
    transport.token = 'synthetic-native-device-' + secrets.token_hex(32)
    legacy = json.loads(path.read_text(encoding='utf-8'))
    legacy.update(account={'id': 'account-a', 'display_name': '合成会员'}, device_token=transport.token)
    path.write_text(json.dumps(legacy), encoding='utf-8')
    assert client.status(refresh=True)['allowed'], 'Synthetic legacy authorization failed'
raw = json.loads(path.read_text(encoding='utf-8'))
assert raw['device_token'] is None
envelope = raw['protected_device_token']
assert envelope['provider'] == 'windows-dpapi'
transport.token = protector.open(envelope).decode('utf-8')
assert transport.token.startswith('synthetic-native-device-')
assert transport.token not in path.read_text(encoding='utf-8')
before = path.read_bytes()
peer = MembershipClient(root, transport, clock,
                        credential_store=SecretProtector(root, purpose='membership-device-v1', native=True))
status = peer.status()
assert status['allowed'] and status['account_id'] == 'account-a'
assert status['credential_storage'] == {'protected': True, 'provider': 'windows-dpapi', 'legacy_plaintext': False}
assert path.read_bytes() == before, 'Reopening a native credential rewrote its envelope'
wrong = root / 'different-binding'; wrong.mkdir(exist_ok=True)
try:
    SecretProtector(wrong, purpose='membership-device-v1', native=True).open(envelope)
except CredentialStoreError:
    pass
else:
    raise AssertionError('A different data root opened the native credential')
altered = dict(envelope)
blob = bytearray(base64.urlsafe_b64decode(altered['blob'])); blob[-1] ^= 1
altered['blob'] = base64.urlsafe_b64encode(blob).decode('ascii')
try:
    protector.open(altered)
except CredentialStoreError:
    pass
else:
    raise AssertionError('A damaged native credential was accepted')
assert path.read_bytes() == before
print(json.dumps({'passed': True, 'provider': 'windows-dpapi', 'legacy_migrated': True,
                  'reopened_in_new_client': True, 'plaintext_absent': True,
                  'wrong_root_rejected': True, 'tamper_rejected': True, 'failure_preserved_state': True}))
""", root, "create" if create else "verify")


def expected_failure(command, folder, code):
    job, process = start_in_job(command, folder, allow_breakaway=False)
    try:
        stdout, stderr = process.communicate(timeout=90)
        streams = [output for output in (stdout.strip(), stderr.strip()) if output]
        need(process.returncode == 1 and len(streams) == 1, "A rejected fixture command did not fail closed")
        value = json.loads(streams[0])
        need(value.get("error", {}).get("code") == code, "The fixture returned an unexpected failure category")
        return value["error"]
    finally:
        job.close()
        if process.poll() is None:
            process.communicate(timeout=10)


def port_released(port):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with socket.socket() as connection:
            connection.settimeout(0.3)
            if connection.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise RuntimeError("The fixture-owned service did not release its port")


def archive_inventory(archive, repository, commit):
    prefix = repository.split("/")[1] + "-" + commit + "/"
    with zipfile.ZipFile(archive) as bundle:
        return {entry.filename[len(prefix):]: hashlib.sha256(bundle.read(entry)).hexdigest()
                for entry in bundle.infolist() if not entry.is_dir()}


def test_source_archive(archive, destination, repository, commit):
    prefix = repository.split("/")[1] + "-" + commit + "/"
    destination.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            need(entry.filename.startswith(prefix), "Unexpected test archive root")
            if entry.is_dir():
                continue
            relative = Path(entry.filename[len(prefix):])
            need(not relative.is_absolute() and ".." not in relative.parts, "Unsafe test archive path")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(entry))
    return destination


def verify_hardening(root, source, data, command, originals, owned, services, receipt):
    program = program_probe(source, data, originals)
    first_seal = seal_fingerprint(source)
    sentinel = root / "record-sentinels.json"
    record_probe(source, data, sentinel, create=True)
    credentials = credential_probe(source, data / "native synthetic credentials", create=True)
    credential_path = data / "native synthetic credentials/.membership/session.json"
    credential_before = sha(credential_path)
    code, repeat = run_in_job(command, root / "repeat", allow_breakaway=True, timeout=240)
    need(code == 0 and repeat["status"] == "ready" and repeat["service"] == "reused"
         and server_pid(data) == owned.pid and seal_fingerprint(source) == first_seal,
         "Healthy repeat changed the service or its sealed program")
    record_probe(source, data, sentinel)
    need(sha(credential_path) == credential_before, "Healthy repeat rewrote a synthetic credential")
    receipt["repeat_preserved_data"] = True
    program["healthy_repeat_preserved_seal"] = True

    current = data / "app/current.json"
    pointer_before = current.read_bytes()
    wrong_digest = list(command)
    wrong_digest[wrong_digest.index("--archive-sha256") + 1] = "0" * 64
    expected_failure(wrong_digest, root / "wrong archive", "archive_checksum_mismatch")
    need(current.read_bytes() == pointer_before and seal_fingerprint(source) == first_seal,
         "Wrong archive changed the selected program")

    target = source / "fagao/network.py"
    marker = root / "unexpected-import-marker"
    damaged = target.read_bytes() + ("\nfrom pathlib import Path as _NativeMarker\n_NativeMarker("
                                    + repr(str(marker)) + ").write_text('unexpected')\n").encode("utf-8")
    target.write_bytes(damaged)
    code, deferred = run_in_job(command, root / "live repair", allow_breakaway=True, timeout=240)
    need(code == 2 and deferred.get("status") == "restart_required"
         and deferred.get("current_switched") is False
         and deferred.get("repair") == {"status": "blocked_running", "repair_required": True, "data_preserved": True}
         and current.read_bytes() == pointer_before and target.read_bytes() == damaged,
         "Repair changed a live program instead of deferring")
    entry = [sys.executable, "-I", "-B", str(data / "app/open-workbench.py")]
    expected_failure(entry, root / "tampered entry", "installation_integrity_failed")
    cli = [sys.executable, "-I", "-B", str(data / "app/open-cli.py"), "--help"]
    expected_failure(cli, root / "tampered cli", "installation_integrity_failed")
    need(not marker.exists(), "A modified protected module executed before integrity rejection")
    owned.close()  # Only the fixture's original Windows process handle is ended.
    port_released(PORT)
    code, repaired = run_in_job(command, root / "trusted repair", allow_breakaway=True, timeout=360)
    need(code == 0 and repaired.get("repair", {}).get("status") == "repaired"
         and repaired["repair"].get("data_preserved") is True, "Trusted same-SHA repair did not complete")
    _, after, _ = installed_service(repaired, data, PORT)
    services.append(after)
    backup = Path(repaired["repair"]["backup_dir"])
    need(backup.parent == data / "app/quarantine" and not backup.is_symlink()
         and backup.is_dir() and backup.name.startswith(source.name + "-")
         and (backup / "fagao/network.py").read_bytes() == damaged
         and sha(backup / ".fagao-code-seal.json") == first_seal["pack"], "Original damaged program was not retained")
    program_probe(source, data, originals)
    record_probe(source, data, sentinel)
    need(sha(credential_path) == credential_before, "Repair changed the synthetic credential file")
    credential_probe(source, data / "native synthetic credentials")
    repaired_seal = seal_fingerprint(source)
    code, opened = run_in_job(entry, root / "repaired entry", allow_breakaway=False)
    need(code == 0 and opened.get("status") == "reused" and server_pid(data) == after.pid,
         "Repaired fixed entry did not reuse its exact service")
    need("usage:" in captured(cli, source).lower(), "The official CLI wrapper did not reopen after repair")
    code, repeated = run_in_job(command, root / "repeat repaired", allow_breakaway=True, timeout=240)
    need(code == 0 and repeated.get("service") == "reused" and server_pid(data) == after.pid
         and seal_fingerprint(source) == repaired_seal, "Post-repair repeat changed the sealed program")
    need(not marker.exists(), "The tampered module marker executed")
    receipt["hardening"] = {
        "program_seal": program, "credentials": credentials,
        "integrity": {"passed": True, "tampered_fixed_entry_rejected": True, "tampered_cli_rejected": True,
                      "preimport_marker_not_executed": True, "wrong_archive_rejected": True},
        "repair": {"passed": True, "live_deferred": True, "current_preserved_while_deferred": True,
                   "trusted_same_sha": True, "data_preserved": True, "quarantine_preserved": True,
                   "repaired_entry_reused": True, "healthy_post_repair_repeat": True},
    }


def cleanup_services(services, data, source, port):
    observed = health(port)
    if (observed and Path(observed.get("data_dir", "")).resolve() == data
            and Path(observed.get("source_root", "")).resolve() == source):
        pid = server_pid(data)
        if not any(service.pid == pid and service.handle for service in services):
            services.append(OwnedProcess(pid))
    for service in reversed(services):
        service.close()


def verify_parent_upgrade(root, bootstrap, archive, archive_sha, repository, commit, originals, receipt):
    """Use the candidate's unchanged published parent pointer, never a moving tag."""
    prior = json.loads((ROOT / "install-release.json").read_text(encoding="utf-8"))
    old = prior.get("source_commit", "")
    need(prior.get("status") == "published" and prior.get("app_version") == "0.7.0-dev.19"
         and prior.get("repository", "").lower() == repository.lower()
         and re.fullmatch(r"[a-f0-9]{40}", old) and old != commit
         and prior.get("source_archive_url") == f"https://codeload.github.com/{prior['repository']}/zip/{old}"
         and prior.get("installer_url") == f"https://raw.githubusercontent.com/{prior['repository']}/{old}/scripts/bootstrap.py",
         "The candidate has no exact published dev19 parent pointer")
    available = subprocess.run(["git", "cat-file", "-e", old + "^{commit}"], cwd=ROOT, capture_output=True)
    if available.returncode:
        subprocess.run(["git", "fetch", "--no-tags", "--depth=1", f"https://github.com/{repository}.git", old],
                       cwd=ROOT, capture_output=True, check=True, timeout=90)
        need(subprocess.check_output(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT).decode().strip() == old,
             "Parent fetch returned another source")
    base = root / "parent upgrade"
    base.mkdir()
    previous_archive, previous_sha, _ = reference_archive(base, repository, old)
    need(previous_sha == prior["source_archive_sha256"], "Published parent archive digest changed")
    previous_bootstrap = base / "parent-bootstrap.py"
    previous_bootstrap.write_bytes(subprocess.check_output(["git", "show", old + ":scripts/bootstrap.py"], cwd=ROOT))
    need(sha(previous_bootstrap) == prior["installer_sha256"], "Published parent installer digest changed")
    data, port = base / "旧版资料", free_port()
    old_source, source = data / "app/releases" / old, data / "app/releases" / commit
    common = ["--repo", repository, "--install-root", str(data / "app"), "--data-dir", str(data),
              "--port", str(port), "--no-browser"]
    previous_command = [sys.executable, str(previous_bootstrap), *common, "--commit", old,
                        "--archive-file", str(previous_archive), "--archive-sha256", previous_sha]
    command = [sys.executable, str(bootstrap), *common, "--commit", commit,
               "--archive-file", str(archive), "--archive-sha256", archive_sha]
    services = []
    try:
        code, previous = run_in_job(previous_command, base / "install parent", allow_breakaway=True, timeout=360)
        need(code == 0 and previous.get("release_id") == old, "Published parent did not install")
        _, old_process, observed = installed_service(previous, data, port)
        services.append(old_process)
        need(observed.get("version") == "0.7.0-dev.19", "Parent service version differs")
        sentinel = base / "parent-record-sentinels.json"
        record_probe(old_source, data, sentinel, create=True)
        old_receipt = sha(old_source / ".fagao-release.json")
        current = data / "app/current.json"
        pointer_before = current.read_bytes()
        code, prepared = run_in_job(command, base / "prepare upgrade", allow_breakaway=True, timeout=360)
        need(code == 2 and prepared.get("status") == "restart_required"
             and prepared.get("current_switched") is False and current.read_bytes() == pointer_before
             and server_pid(data) == old_process.pid, "Upgrade did not retain the live parent and its pointer")
        program_probe(source, data, originals)
        prepared_seal = seal_fingerprint(source)
        old_process.close()
        port_released(port)
        code, upgraded = run_in_job(command, base / "complete upgrade", allow_breakaway=True, timeout=360)
        need(code == 0 and upgraded.get("status") == "ready" and upgraded.get("release_id") == commit,
             "Prepared parent upgrade did not finish")
        _, selected, observed = installed_service(upgraded, data, port)
        services.append(selected)
        need(observed.get("version") == "0.7.0-dev.20" and seal_fingerprint(source) == prepared_seal,
             "Completing the upgrade changed its prepared seal")
        need(json.loads(current.read_text(encoding="utf-8"))["release_id"] == commit
             and old_source.is_dir() and sha(old_source / ".fagao-release.json") == old_receipt,
             "Upgrade changed the old release or selected another source")
        record_probe(source, data, sentinel)
        code, entry = run_in_job([sys.executable, "-I", "-B", str(data / "app/open-workbench.py")],
                                 base / "upgraded fixed entry", allow_breakaway=False)
        need(code == 0 and entry.get("status") == "reused" and server_pid(data) == selected.pid,
             "Upgraded fixed entry did not reuse the selected service")
        receipt["parent_upgrade"] = {"passed": True, "parent_version": "0.7.0-dev.19", "parent_source": old,
                                     "prepared_current_unchanged": True, "live_parent_preserved": True,
                                     "prepared_seal_preserved": True, "old_release_preserved": True,
                                     "record_ids_preserved": True, "fixed_entry_reused": True}
    finally:
        # A partial preparation may leave the parent, while a ready upgrade owns the new source.
        observed = health(port)
        selected_source = Path(observed.get("source_root", "")) if observed else source
        cleanup_services(services, data, selected_source, port)


def verify_install(root, receipt):
    commit = os.environ["GITHUB_SHA"]
    repository = official_ci_repository()
    need(os.name == "nt" and struct.calcsize("P") == 8 and platform.machine().upper() in ("AMD64", "X86_64"),
         "A native Windows x64 runner is required")
    need(health(PORT) is None, "Workbench port is occupied")
    archive, source_sha, files = reference_archive(root, repository, commit)
    bootstrap = root / "bootstrap.py"
    bootstrap.write_bytes(subprocess.check_output(["git", "show", commit + ":scripts/bootstrap.py"], cwd=ROOT))
    data = root / "本机资料"
    source = data / "app" / "releases" / commit
    receipt.update(source_commit=commit, source_archive_sha256=source_sha, source_tree_files=files,
                   installer_sha256=hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
                   platform=platform.platform(), python=platform.python_version())
    command = [sys.executable, str(bootstrap), "--repo", repository, "--commit", commit,
               "--archive-file", str(archive), "--archive-sha256", source_sha, "--no-browser"]
    environment = {k: v for k, v in os.environ.items() if k.upper() not in
                   {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_PAT", "FAGAO_SUBMISSION_ARCHIVE"}}
    environment.update(FAGAO_DATA_DIR=str(data), FAGAO_QA_NODE_MODULES=str(ROOT / "node_modules"))
    services = []
    with patch.dict(os.environ, environment, clear=True):
        try:
            code, installed = run_in_job(command, root / "first install", allow_breakaway=True, timeout=360)
            need(code == 0 and installed.get("release_id") == commit and Path(installed["release_dir"]) == source,
                 "Pinned candidate installation failed: " + str(installed.get("message", ""))[:500])
            _, owned, observed = installed_service(installed, data, PORT)
            services.append(owned)
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

            originals = archive_inventory(archive, repository, commit)
            verify_hardening(root, source, data, command, originals, owned, services, receipt)
            test_source = test_source_archive(archive, root / "unsealed test source", repository, commit)
            verify_platforms(source, receipt, test_source=test_source)
            verify_parent_upgrade(root, bootstrap, archive, source_sha, repository, commit, originals, receipt)
        finally:
            cleanup_services(services, data, source, PORT)


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
