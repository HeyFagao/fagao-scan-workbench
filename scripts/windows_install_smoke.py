#!/usr/bin/env python3
"""Native Windows acceptance for isolated installs and command-host lifetimes.

This is a CI fixture, not an installation entry point. It creates only temporary
accounts/data, never logs in, and never changes a user's existing installation.
Job limits apply only to this fixture's gated children. They are not adjusted on
the runner or on WorkBuddy. A permitted breakaway is tested separately from the
documented, host-managed foreground fallback.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
APP = "fagao-scan-workbench"
MAX_ARCHIVE = 50 * 1024 * 1024
CHECKS: list[str] = []


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def passed(name):
    CHECKS.append(name)
    print("PASS " + name, flush=True)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utf8_env(enabled=True):
    environment = os.environ.copy()
    if enabled:
        environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    else:
        environment.pop("PYTHONIOENCODING", None)
        environment["PYTHONUTF8"] = "0"
    return environment


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def health(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            need(response.status == 200, "health status")
            return json.loads(response.read(65537))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def matching_health(port, data, source=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = health(port)
        if (isinstance(result, dict) and result.get("app") == APP
                and Path(result.get("data_dir", "")).resolve() == data.resolve()
                and (source is None or Path(result.get("source_root", "")).resolve() == source.resolve())):
            return result
        time.sleep(0.2)
    raise RuntimeError("matching isolated service did not become healthy")


def server_pid(data, timeout=8):
    # HTTP becomes healthy just before the supervisor atomically records the PID.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pid = int((data / "server.pid").read_text(encoding="ascii"))
            if pid > 0:
                return pid
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("ready fixture service did not record its process ID")


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "GetSystemDirectoryW": ([wintypes.LPWSTR, wintypes.UINT], wintypes.UINT),
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (argtypes, restype) in signatures.items():
        method = getattr(api, name)
        method.argtypes, method.restype = argtypes, restype
    return api


def system_cmd():
    api = kernel()
    directory = ctypes.create_unicode_buffer(32768)
    length = api.GetSystemDirectoryW(directory, len(directory))
    need(0 < length < len(directory), "Windows system directory unavailable")
    command = Path(directory.value) / "cmd.exe"
    need(command.is_file(), "Windows system command processor unavailable")
    return command


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class OwnedProcess:
    """Hold the original process handle, never terminate by a reusable PID alone."""
    def __init__(self, pid):
        self.api = kernel()
        self.pid = int(pid)
        self.handle = self.api.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD(32768)
            image = ctypes.create_unicode_buffer(size.value)
            if not self.api.QueryFullProcessImageNameW(self.handle, 0, image, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            need(Path(image.value).resolve() == Path(sys.executable).resolve(), "owned test PID is not this Python")
        except BaseException:
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise

    def in_job(self, job):
        result = wintypes.BOOL()
        if not self.api.IsProcessInJob(self.handle, job.handle, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(result.value)

    def close(self):
        if self.handle:
            state = self.api.WaitForSingleObject(self.handle, 0)
            if state == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            need(state in (0, 258), "unexpected owned process wait result")
            if state == 258:
                terminated = self.api.TerminateProcess(self.handle, 0)
                termination_error = ctypes.get_last_error() if not terminated else None
                # Closing the fixture Job initiates asynchronous termination.
                # The process may exit between the first wait and TerminateProcess,
                # which then reports ACCESS_DENIED. Accept that race only when
                # this same, already-owned handle becomes signaled within 10 s.
                ended = self.api.WaitForSingleObject(self.handle, 10000)
                if ended == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if ended != 0:
                    if termination_error is not None:
                        raise ctypes.WinError(termination_error)
                    raise RuntimeError("owned process cleanup timed out")
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


class OwnedJob:
    def __init__(self, *, allow_breakaway):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | (0x0800 if allow_breakaway else 0)
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise error

    def assign(self, process):
        # The child waits for a private gate. It cannot launch descendants before
        # this assignment, avoiding the usual Popen/AssignProcess race.
        handle = self.api.OpenProcess(0x0100 | 0x1000 | 0x0001, False, process.pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            inside = wintypes.BOOL()
            if not self.api.IsProcessInJob(handle, self.handle, ctypes.byref(inside)):
                raise ctypes.WinError(ctypes.get_last_error())
            need(inside.value, "gated child was not assigned to the fixture job")
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def start_in_job(command, folder, *, allow_breakaway, utf8=True, cwd=ROOT):
    folder.mkdir(parents=True, exist_ok=True)
    command_file, gate = folder / "command.json", folder / "assigned.gate"
    write_json(command_file, {"argv": command, "cwd": str(cwd), "utf8": utf8})
    job = OwnedJob(allow_breakaway=allow_breakaway)
    process = None
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--gated-child",
                                    str(command_file), str(gate)], cwd=ROOT, env=utf8_env(),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8")
        job.assign(process)
        gate.write_text("assigned", encoding="ascii")
        return job, process
    except BaseException:
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        finally:
            job.close()
        raise


def run_in_job(command, folder, *, allow_breakaway, utf8=True, timeout=150, cwd=ROOT):
    job, process = start_in_job(command, folder, allow_breakaway=allow_breakaway, utf8=utf8, cwd=cwd)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        try:
            result = json.loads(stdout)
        except ValueError as error:
            raise RuntimeError("fixture command returned no JSON (exit " + str(process.returncode)
                               + "): " + stderr[-1200:]) from error
        return process.returncode, result
    except BaseException:
        job.close()
        process.communicate(timeout=10)
        raise
    finally:
        # This is the real command-host cleanup boundary under test.
        job.close()


def independent_probe(port, data, source):
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe",
                             str(port), str(data), str(source)], cwd=ROOT, env=utf8_env(),
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    need(result.returncode == 0, "independent command could not reach service after host cleanup")
    value = json.loads(result.stdout)
    need(value["healthy"] and value["html_status"] == 200, "independent health/page checks failed")
    return value


def reference_archive(base, repo, commit):
    need(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "invalid repository")
    need(re.fullmatch(r"[a-f0-9]{40}", commit), "full commit required")
    tree = subprocess.run(["git", "ls-tree", "-rz", "--full-tree", commit], cwd=ROOT,
                          capture_output=True, check=True).stdout
    expected = {}
    for row in tree.split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            mode, kind, oid = meta.split()
            need(kind == b"blob" and mode in (b"100644", b"100755"), "unexpected source tree entry")
            expected[path.decode("utf-8")] = oid.decode("ascii")
    archive = base / "fixed-source.zip"
    url = f"https://codeload.github.com/{repo}/zip/{commit}"
    request = urllib.request.Request(url, headers={"User-Agent": "Fagao-Windows-CI/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        need(response.status == 200 and response.geturl() == url, "unexpected archive response")
        content = response.read(MAX_ARCHIVE + 1)
        need(len(content) <= MAX_ARCHIVE, "reference archive too large")
    archive.write_bytes(content)
    prefix = repo.split("/")[1] + "-" + commit + "/"
    observed = {}
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            need(info.filename.startswith(prefix), "archive root differs from fixed commit")
            if info.is_dir():
                continue
            name = info.filename[len(prefix):]
            need(name not in observed and name in expected, "unexpected archive file")
            data = source.read(info)
            observed[name] = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    need(expected == observed, "downloaded fixed archive differs from Git source tree")
    passed("fixed_source_archive_matches_every_git_blob")
    return archive, hashlib.sha256(content).hexdigest(), len(observed)


def real_schannel_fallback(base, repo, commit, archive_sha):
    spec = importlib.util.spec_from_file_location("windows_ci_bootstrap", ROOT / "scripts/bootstrap.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    native = installer._windows_system_curl()
    need(native is not None, "Windows system Schannel curl is unavailable on this runner")
    target = base / "native-schannel-source.zip"
    # Only the Python transport failure is injected. The system curl executable,
    # TLS validation, public GitHub transfer, limits and SHA comparison are real.
    failure = urllib.error.URLError(ssl.SSLCertVerificationError(1, "synthetic CI issuer failure"))
    with patch.object(installer, "_download_urllib", side_effect=failure):
        result = installer.download_archive(repo, commit, target, archive_sha256=archive_sha)
    need(result["status"] == "verified" and result["transport"] == "windows_system_curl",
         "installer did not use the real native TLS fallback")
    need(result["attempts"][0].get("code") == "download_tls_verification_failed"
         and len(result["attempts"]) <= installer.DOWNLOAD_ATTEMPTS, "native fallback diagnostics differ")
    need(hashlib.sha256(target.read_bytes()).hexdigest() == archive_sha, "native archive hash differs")
    passed("injected_python_tls_error_recovers_with_real_schannel_download")
    return {"python_certificate_failure_injected": True, "native_transfer_real": True,
            "backend": "Schannel", "system_trust_settings_changed": False, **result}


def installed_service(result, data, port):
    need(result.get("status") == "ready" and result.get("readiness", {}).get("status") == "ready", "installer not ready")
    source = Path(result["release_dir"])
    observed = matching_health(port, data, source)
    need(observed.get("network", {}).get("policy") == "isolated-system-proxy-v1", "runtime network isolation changed")
    need(observed.get("version"), "service version missing")
    pid = server_pid(data)
    return source, OwnedProcess(pid), observed


def cleanup_error(stage, error, *, pid=None):
    detail = {"stage": stage, "error_type": type(error).__name__}
    if getattr(error, "winerror", None) is not None:
        detail["winerror"] = error.winerror
    if getattr(error, "errno", None) is not None:
        detail["errno"] = error.errno
    if pid is not None:
        detail["owned_pid"] = pid
    return detail


def run_smoke(args):
    need(os.name == "nt", "This acceptance fixture requires actual Windows, not platform mocks")
    raw_base = Path(tempfile.mkdtemp(prefix="发糕 Windows 验证 "))
    base = raw_base.resolve()
    owned, jobs, fixture_services = [], [], []
    report = {"status": "running", "platform": platform.platform(), "python": platform.python_version(),
              "source_commit": args.commit, "repository": args.repo, "checks": CHECKS,
              "synthetic_fixture_paths": {"created": str(raw_base), "canonical": str(base)},
              "real_account_used": False, "real_payment_performed": False,
              "workbuddy_client_exercised": False, "cleanup": {"completed": False}}
    try:
        archive, archive_sha, file_count = reference_archive(base, args.repo, args.commit)
        report["source_archive"] = {"sha256": archive_sha, "files_compared": file_count}
        report["native_tls_fallback"] = real_schannel_fallback(base, args.repo, args.commit, archive_sha)
        bootstrap = str(ROOT / "scripts/bootstrap.py")
        port, data, app = free_port(), base / "中文 空格资料", base / "程序 安装"
        fixture_services.append((data, port))
        remote_command = [sys.executable, bootstrap, "--repo", args.repo, "--commit", args.commit,
                          "--archive-sha256", archive_sha, "--install-root", str(app),
                          "--data-dir", str(data), "--port", str(port), "--no-browser"]
        code, first = run_in_job(remote_command, base / "allow first", allow_breakaway=True)
        need(code == 0 and first.get("release_id") == args.commit, "permitted-job pinned install failed")
        source, server, observed = installed_service(first, data, port)
        owned.append(server)
        independent_probe(port, data, source)
        passed("permitted_breakaway_survives_command_and_job_close")
        passed("real_pinned_install_with_unicode_and_space_paths")
        report["installed_version"] = observed["version"]
        report["download"] = first.get("download", first.get("download_diagnostics", {}))
        need((app / "current.json").is_file() and Path(first["project_dir"]).joinpath("AGENTS.md").is_file(), "stable project/pointer missing")
        need(Path(first["shortcut"]).suffix == ".bat" and Path(first["shortcut"]).is_file(), "Windows shortcut missing")
        passed("fixed_windows_shortcut_and_workspace_created")
        repeat_command = remote_command + ["--archive-file", str(archive)]
        code, repeat = run_in_job(repeat_command, base / "allow repeat", allow_breakaway=True)
        need(code == 0 and repeat.get("service") == "reused", "repeated install did not reuse healthy service")
        need(int((data / "server.pid").read_text()) == server.pid, "repeat created a second server")
        independent_probe(port, data, source)
        passed("verified_local_archive_repeat_reuses_same_process")
        code, ordinary_encoding = run_in_job(repeat_command, base / "default Windows encoding",
                                             allow_breakaway=True, utf8=False)
        need(code == 0 and ordinary_encoding.get("service") == "reused", "default Windows encoding broke JSON with Chinese paths")
        passed("unicode_paths_work_without_python_utf8_environment")
        code, opened = run_in_job([sys.executable, str(app / "open-workbench.py")],
                                  base / "fixed entry", allow_breakaway=False)
        need(code == 0 and opened.get("status") == "reused", "stable entry did not reuse the installed service")
        need(server_pid(data) == server.pid, "stable entry created a duplicate server")
        independent_probe(port, data, source)
        report["fixed_entry"] = {"service_reused": True, "browser_request_accepted": bool(opened.get("browser_opened")),
                                 "browser_window_observed": False}
        passed("fixed_entry_reuses_service_after_a_separate_command")
        code, opened_bat = run_in_job([str(system_cmd()), "/d", "/c", first["shortcut"]],
                                      base / "generated BAT entry", allow_breakaway=False,
                                      utf8=False, timeout=35)
        need(code == 0 and opened_bat.get("status") == "reused", "generated BAT entry did not reuse installed service")
        need(server_pid(data) == server.pid, "BAT entry created a duplicate server")
        independent_probe(port, data, source)
        report["fixed_entry"]["generated_bat_executed_via_cmd"] = True
        passed("generated_bat_runs_with_unicode_space_paths_and_reuses_same_pid")

        # These are synthetic local data, never a real account's store.
        preserved = data / "资料 保留.sqlite"
        with closing(sqlite3.connect(preserved)) as db:
            db.execute("CREATE TABLE fixture(value TEXT)")
            db.execute("INSERT INTO fixture VALUES (?)", ("原收藏与正文应保留",))
            db.commit()
        setting = data / "保留 设置.json"
        setting.write_text('{"fixture":"原设置"}', encoding="utf-8")
        server.close()
        upgrade = base / "升级 来源"
        shutil.copytree(ROOT, upgrade, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", ".venv"))
        (upgrade / "WINDOWS_QA_UPGRADE_FIXTURE.txt").write_text("synthetic upgrade only\n", encoding="utf-8")
        upgrade_command = [sys.executable, bootstrap, "--source-dir", str(upgrade), "--install-root", str(app),
                           "--data-dir", str(data), "--port", str(port), "--no-browser"]
        code, updated = run_in_job(upgrade_command, base / "allow upgrade", allow_breakaway=True)
        need(code == 0 and updated.get("release_id") != args.commit, "upgrade did not select new code")
        new_source, new_server, _ = installed_service(updated, data, port)
        owned.append(new_server)
        independent_probe(port, data, new_source)
        with closing(sqlite3.connect(preserved)) as db:
            need(db.execute("SELECT value FROM fixture").fetchone() == ("原收藏与正文应保留",), "existing data changed")
        need(setting.read_text(encoding="utf-8") == '{"fixture":"原设置"}', "existing settings changed")
        need(source.is_dir(), "prior release removed")
        # A synthetic source variant makes a real running-version difference.
        # Return to the exact GitHub commit through the ordinary remote update
        # path; it must hold the downloaded archive while requesting a restart.
        old_pointer = (app / "current.json").read_bytes()
        code, restarting = run_in_job(remote_command, base / "running version update", allow_breakaway=True)
        need(code == 2 and restarting.get("status") == "restart_required", "running version change did not request verified restart")
        need((app / "current.json").read_bytes() == old_pointer, "update switched pointer before old service stopped")
        matching_health(port, data, new_source)
        update_resume = restarting.get("resume", {})
        need(update_resume.get("type") == "complete_install", "version update has no exact resume instruction")
        update_argv = update_resume["argv"]
        need(update_argv[0] == sys.executable and "--archive-file" in update_argv, "version update would repeat network download")
        saved_archive = Path(update_argv[update_argv.index("--archive-file") + 1])
        report["pending_update_archive_paths"] = {"returned": str(saved_archive), "canonical": str(saved_archive.resolve()),
                                                  "install_root": str(app), "canonical_install_root": str(app.resolve())}
        need(saved_archive.is_file(), "pending update archive is missing")
        need(saved_archive.resolve().is_relative_to(app.resolve()), "pending update archive not owned by this install")
        need(hashlib.sha256(saved_archive.read_bytes()).hexdigest() == archive_sha, "pending update archive differs")
        new_server.close()
        code, upgraded = run_in_job(update_argv, base / "version update resumed", allow_breakaway=True,
                                    cwd=update_resume["cwd"])
        need(code == 0 and upgraded.get("release_id") == args.commit, "resumed version update did not select fixed commit")
        need(upgraded.get("download", {}).get("transport") == "verified_pending_archive", "resumed update did not reuse verified archive")
        restored_source, restored_server, _ = installed_service(upgraded, data, port)
        owned.append(restored_server)
        independent_probe(port, data, restored_source)
        with closing(sqlite3.connect(preserved)) as db:
            need(db.execute("SELECT value FROM fixture").fetchone() == ("原收藏与正文应保留",), "remote update changed existing data")
        need(setting.read_text(encoding="utf-8") == '{"fixture":"原设置"}', "remote update changed settings")
        need(new_source.is_dir() and archive.is_file() and not saved_archive.exists(), "update archive cleanup or previous release retention differs")
        report["upgrade"] = {"fixture": "synthetic source variant to pinned GitHub commit", "restart_required_observed": True,
                             "pointer_unchanged_until_restart": True, "verified_archive_reused": True,
                             "existing_sqlite_and_settings_preserved": True, "owned_pending_archive_removed": True}
        passed("running_version_update_resumes_verified_archive_without_redownload")
        passed("upgrade_keeps_existing_sqlite_settings_and_previous_release")
        restored_server.close()

        denied_port, denied_data, denied_app = free_port(), base / "受限 资料", base / "受限 程序"
        fixture_services.append((denied_data, denied_port))
        denied_command = [sys.executable, bootstrap, "--repo", args.repo, "--commit", args.commit,
                          "--archive-sha256", archive_sha,
                          "--install-root", str(denied_app), "--data-dir", str(denied_data),
                          "--port", str(denied_port), "--no-browser"]
        code, pending = run_in_job(denied_command, base / "deny first", allow_breakaway=False)
        need(code == 3 and pending.get("status") == "background_required", "restricted job did not request native background task")
        need(not (denied_app / "current.json").exists() and health(denied_port) is None, "restricted install falsely marked ready")
        passed("restricted_job_returns_background_required_without_current_switch")
        action = pending["action"]
        need(action["type"] == "run_in_background" and action["argv"][0] == sys.executable,
             "background action is not an exact argv instruction")
        expected_source = Path(pending["release_dir"]).resolve()
        need(Path(action["cwd"]).resolve() == expected_source and Path(action["argv"][1]).resolve() == expected_source / "scripts/launch.py",
             "background action targets another release")
        need("--foreground" in action["argv"] and "--no-browser" in action["argv"], "foreground/background contract differs")
        managed_job, supervisor = start_in_job(action["argv"], base / "native managed task", allow_breakaway=False,
                                               cwd=action["cwd"])
        jobs.append((managed_job, supervisor))
        matching_health(denied_port, denied_data, expected_source, timeout=30)
        managed_pid = server_pid(denied_data)
        managed_server = OwnedProcess(managed_pid)
        owned.append(managed_server)
        need(managed_server.in_job(managed_job), "foreground server escaped its managing task")
        need(supervisor.poll() is None, "foreground task exited before managed lifetime ended")
        passed("foreground_service_remains_owned_by_native_background_task")
        resume = pending.get("resume")
        need(isinstance(resume, dict) and resume.get("type") == "complete_install",
             "restricted install did not preserve an exact resume instruction")
        resume_command = resume["argv"]
        need(resume_command[0] == sys.executable and "--archive-file" in resume_command,
             "recovery would unnecessarily redownload an already verified archive")
        code, recovered = run_in_job(resume_command, base / "deny completion", allow_breakaway=False,
                                     cwd=resume["cwd"])
        need(code == 0 and recovered.get("status") == "ready" and recovered.get("service") == "reused",
             "same installer did not finish after native background start")
        need(recovered["release_id"] == args.commit and (denied_app / "current.json").is_file(), "recovered pointer incorrect")
        independent_probe(denied_port, denied_data, expected_source)
        need(int((denied_data / "server.pid").read_text()) == managed_pid, "recovery started duplicate service")
        passed("restricted_installer_finishes_by_reusing_managed_service")
        report["native_job_cases"] = {"permitted_breakaway": "passed", "denied_breakaway": "passed",
                                       "managed_foreground_recovery": "passed"}
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error)[:1000])
    finally:
        cleanup_errors = []
        # Capture any detached test server that reached ready immediately before
        # an assertion failed, so failure paths cannot leave an untracked service.
        for data, port in fixture_services:
            observed = health(port)
            if not (isinstance(observed, dict) and observed.get("app") == APP
                    and Path(observed.get("data_dir", "")).resolve() == data.resolve()
                    and Path(observed.get("source_root", "")).resolve().is_relative_to(base)):
                continue
            try:
                pid = int((data / "server.pid").read_text(encoding="ascii"))
                if not any(process.pid == pid and process.handle for process in owned):
                    owned.append(OwnedProcess(pid))
            except (OSError, ValueError, RuntimeError) as error:
                cleanup_errors.append(cleanup_error("capture_owned_process", error))
        for job, process in reversed(jobs):
            try:
                job.close()
                process.communicate(timeout=10)
            except BaseException as error:
                cleanup_errors.append(cleanup_error("close_managed_job", error, pid=process.pid))
        for process in reversed(owned):
            try:
                process.close()
            except BaseException as error:
                cleanup_errors.append(cleanup_error("close_owned_process", error, pid=process.pid))
        # Only this mkdtemp-created fixture is removed. No user install path is used.
        try:
            shutil.rmtree(base)
        except OSError as error:
            cleanup_errors.append(cleanup_error("remove_fixture_directory", error))
        report["cleanup"] = {"completed": not cleanup_errors, "owned_processes": len(owned),
                              "fixture_removed": not base.exists(), "errors": cleanup_errors}
        if cleanup_errors:
            report["status"] = "failed"
        report["passed"] = len(CHECKS)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.receipt, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--gated-child":
        command_file, gate = map(Path, sys.argv[2:])
        deadline = time.monotonic() + 15
        while not gate.is_file():
            need(time.monotonic() < deadline, "job assignment gate timed out")
            time.sleep(0.05)
        command = json.loads(command_file.read_text(encoding="utf-8"))
        return subprocess.call(command["argv"], cwd=command["cwd"], env=utf8_env(command.get("utf8", True)))
    if len(sys.argv) == 5 and sys.argv[1] == "--probe":
        port, data, source = int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
        matching_health(port, data, source)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
            body, status = response.read(2 * 1024 * 1024), response.status
        need(status == 200 and "发糕" in body.decode("utf-8"), "local page response differs")
        print(json.dumps({"healthy": True, "html_status": status}))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), required=not os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--commit", default=os.environ.get("GITHUB_SHA"), required=not os.environ.get("GITHUB_SHA"))
    parser.add_argument("--receipt", type=Path, required=True)
    return run_smoke(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
