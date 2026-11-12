# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Download the pinned desktop component from the official GitHub release."""

from __future__ import annotations

import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 512 * 1024 * 1024
ATTEMPTS = 3
SOCKET_TIMEOUT = 30
ATTEMPT_TIMEOUT = 15 * 60
REDIRECT_LIMIT = 4
ORIGIN = "https://github.com/HeyFagao/fagao-scan-workbench/releases/download/"
PREVIOUS_ORIGIN = "https://github.com/ShiQi-Han03/fagao-scan-workbench/releases/download/"
# Only these original, pinned assets exist under the pre-rename distribution.
MIGRATED_ASSETS = frozenset("v0.7.0-dev.13/FagaoSubmission-" + target + ".zip"
                            for target in ("macos-arm64", "macos-x64", "windows-x64"))
REPOSITORY_CDN_PATH = re.compile(r"/github-production-release-asset(?:-[a-f0-9]+)?/1452172195/[A-Za-z0-9_-]+")
ASSET_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9_.+-]{0,190}\.zip")
CDN_HOSTS = frozenset({"release-assets.githubusercontent.com", "objects.githubusercontent.com"})


class ComponentDownloadError(ValueError):
    def __init__(self, message, *, transient=False, tls=False, not_found=False):
        super().__init__(message)
        self.transient = transient
        self.tls = tls
        self.not_found = not_found


def _validate_source(url: str) -> None:
    if isinstance(url, str):
        if url.startswith(ORIGIN) and ASSET_PATH.fullmatch(url[len(ORIGIN):]):
            return
        if url.startswith(PREVIOUS_ORIGIN) and url[len(PREVIOUS_ORIGIN):] in MIGRATED_ASSETS:
            return
    raise ComponentDownloadError("投稿组件下载地址不属于指定的 GitHub 发行。")


def _validate_redirect(url: str) -> None:
    try:
        if not isinstance(url, str) or not url.isascii() or re.search(r"[\x00-\x20\x7f\\]", url):
            raise ValueError
        target = urlsplit(url)
        if (target.scheme != "https" or target.hostname not in CDN_HOSTS
                or target.port not in (None, 443) or target.username is not None
                or target.password is not None or target.fragment
                or not REPOSITORY_CDN_PATH.fullmatch(target.path)):
            raise ValueError
    except ValueError:
        raise ComponentDownloadError("投稿组件下载发生了不允许的跳转。") from None


class _ReleaseRedirect(HTTPRedirectHandler):
    max_redirections = REDIRECT_LIMIT

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_redirect(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _write_bounded(stream, path: Path, limit: int) -> int:
    size = 0
    with path.open("xb") as output:
        while chunk := stream.read(256 * 1024):
            size += len(chunk)
            if size > limit:
                raise ComponentDownloadError("投稿组件超过发行记录中的字节数。")
            output.write(chunk)
    return size


def _fetch_python(url: str, path: Path, size: int) -> None:
    req = Request(url, headers={"User-Agent": "FagaoWorkbench-Installer", "Accept-Encoding": "identity",
                                "Cache-Control": "no-cache"})
    with build_opener(_ReleaseRedirect()).open(req, timeout=SOCKET_TIMEOUT) as response:
        if response.geturl() != url or url.startswith(PREVIOUS_ORIGIN):
            _validate_redirect(response.geturl())
        if response.status != 200:
            raise ComponentDownloadError("投稿组件下载未返回完整文件。")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isascii() or not length.isdecimal()
                                   or int(length) != size):
            raise ComponentDownloadError("投稿组件响应长度与发行记录不符。")
        _write_bounded(response, path, size)


def _classify(error: Exception) -> ComponentDownloadError:
    if isinstance(error, ComponentDownloadError):
        return error
    if isinstance(error, HTTPError):
        return ComponentDownloadError(f"投稿组件下载返回 HTTP {error.code}。",
                                      transient=error.code in (408, 429, 500, 502, 503, 504),
                                      not_found=error.code == 404)
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError) or (
            isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)):
        return ComponentDownloadError("投稿组件 HTTPS 证书验证失败。", tls=True)
    if isinstance(reason, (TimeoutError, socket.timeout, subprocess.TimeoutExpired)):
        return ComponentDownloadError("投稿组件下载超时。", transient=True)
    if isinstance(reason, PermissionError):
        return ComponentDownloadError("无法写入投稿组件安装目录。")
    return ComponentDownloadError("投稿组件下载连接中断。",
                                  transient=isinstance(error, (OSError, URLError, IncompleteRead)))


def _python_download(url: str, path: Path, expected_size: int) -> None:
    # Bound DNS, handshake and any stalled body read with one process deadline.
    result = subprocess.run([sys.executable, "-IB", str(Path(__file__).resolve()), "--fetch",
                             url, str(path.resolve()), str(expected_size)], timeout=ATTEMPT_TIMEOUT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        try:
            failure = json.loads(result.stdout)
            raise ComponentDownloadError(failure["message"], transient=failure.get("transient") is True,
                                         tls=failure.get("tls") is True, not_found=failure.get("not_found") is True)
        except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            raise ComponentDownloadError("投稿组件下载进程未能完成。") from None


def _bootstrap_module():
    # Both the pinned bootstrap and the extracted component installer load this
    # module, with different entry points. Use the helpers from the same release.
    if __package__:
        from . import bootstrap
    else:
        import bootstrap
    return bootstrap


def _windows_system_curl():
    return _bootstrap_module()._windows_system_curl()


def _curl_environment():
    return _bootstrap_module()._curl_environment()


def _curl_head(url: str, executable: str, timeout: int) -> tuple[int, str]:
    result = subprocess.run([executable, "--disable", "--silent", "--head", "--globoff",
                             "--proto", "=https", "--header", "Cache-Control: no-cache",
                             "--connect-timeout", str(SOCKET_TIMEOUT),
                             "--max-time", str(timeout), "--output", os.devnull,
                             "--write-out", "%{http_code}\n%{redirect_url}", "--url", url],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            timeout=timeout + 2, env=_curl_environment())
    _check_curl(result.returncode)
    status, _, redirect = result.stdout.partition(b"\n")
    if not re.fullmatch(rb"[0-9]{3}", status) or len(redirect) > 16384:
        raise ComponentDownloadError("系统下载通道返回了无效响应。")
    try:
        return int(status), redirect.decode("ascii")
    except UnicodeError:
        raise ComponentDownloadError("系统下载通道返回了无效跳转。") from None


def _check_curl(code: int) -> None:
    if code in (51, 60, 77, 82, 83):
        raise ComponentDownloadError("系统 HTTPS 证书验证失败。", tls=True)
    if code:
        raise ComponentDownloadError(f"系统下载通道未完成投稿组件下载（{code}）。",
                                     transient=code in (5, 6, 7, 18, 22, 28, 35, 52, 55, 56, 92))


def _curl_download(url: str, path: Path, expected_size: int, executable: str) -> None:
    # curl -L cannot enforce a destination host allowlist. Resolve each HEAD
    # redirect first, then issue ONE GET with redirects disabled.
    deadline = time.monotonic() + ATTEMPT_TIMEOUT
    for hop in range(REDIRECT_LIMIT + 1):
        remaining = max(1, int(deadline - time.monotonic()))
        status, redirect = _curl_head(url, executable, remaining)
        if status == 200:
            if url.startswith(PREVIOUS_ORIGIN):
                _validate_redirect(url)  # The previous name must resolve to this repository’s CDN object.
            break
        if status not in (301, 302, 303, 307, 308):
            raise _classify(HTTPError(url, status, "", None, None))
        if hop == REDIRECT_LIMIT:
            raise ComponentDownloadError("投稿组件下载跳转次数过多。")
        _validate_redirect(redirect)
        url = redirect

    remaining = max(1, int(deadline - time.monotonic()))
    command = [executable, "--disable", "--fail", "--silent", "--globoff", "--proto", "=https",
               "--connect-timeout", str(SOCKET_TIMEOUT), "--max-time", str(remaining),
               "--max-filesize", str(expected_size), "--url", url]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env=_curl_environment()) as process:
        try:
            _write_bounded(process.stdout, path, expected_size)
            _check_curl(process.wait(timeout=5))
        finally:
            if process.poll() is None:
                process.kill()


def _verify(path: Path, expected_sha256: str, expected_size: int) -> None:
    if path.stat().st_size != expected_size:
        raise ComponentDownloadError("投稿组件长度与发行记录不符。")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256.lower():
        raise ComponentDownloadError("投稿组件 SHA-256 与发行记录不符。")


def download_component(url: str, destination: Path, expected_sha256: str, expected_size: int) -> None:
    _validate_source(url)
    if (not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
            or type(expected_size) is not int or not 0 < expected_size <= MAX_BYTES):
        raise ComponentDownloadError("投稿组件发行记录缺少有效的大小或 SHA-256。")
    asset = url[len(PREVIOUS_ORIGIN):] if url.startswith(PREVIOUS_ORIGIN) else url[len(ORIGIN):]
    url = ORIGIN + asset
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ComponentDownloadError("投稿组件下载位置已存在。")
    with tempfile.TemporaryDirectory(prefix=".submission-download-", dir=destination.parent) as temp:
        staged = Path(temp) / "download.zip"
        native = None
        for attempt in range(1, ATTEMPTS + 1):
            try:
                if native:
                    _curl_download(url, staged, expected_size, native)
                else:
                    _python_download(url, staged, expected_size)
                _verify(staged, expected_sha256, expected_size)
                break
            except (OSError, URLError, IncompleteRead, ComponentDownloadError, subprocess.TimeoutExpired) as exc:
                staged.unlink(missing_ok=True)
                failure = _classify(exc)
                if failure.not_found and url == ORIGIN + asset and asset in MIGRATED_ASSETS and attempt < ATTEMPTS:
                    # Before the username change the new URL does not exist yet.
                    # The fallback must resolve to this repository ID and match the same digest.
                    url = PREVIOUS_ORIGIN + asset
                    continue
                if attempt < ATTEMPTS and sys.platform == "win32" and not native and (failure.tls or failure.transient):
                    native = _windows_system_curl()
                    if native:
                        continue
                if not failure.transient or attempt == ATTEMPTS:
                    raise failure from None
                time.sleep(attempt)
        # The caller owns a private installation staging directory on the same
        # filesystem. Link without replacing a competing writer's completed file.
        try:
            os.link(staged, destination)
        except FileExistsError:
            raise ComponentDownloadError("投稿组件下载位置已存在。") from None
        except OSError:
            # FAT and some network shares do not support hard links.
            output = destination.open("xb")
            try:
                with output:
                    with staged.open("rb") as src:
                        while chunk := src.read(1024 * 1024):
                            output.write(chunk)
            except OSError:
                destination.unlink(missing_ok=True)
                raise


def _main(args: list[str]) -> int:
    try:
        if len(args) != 4 or args[0] != "--fetch":
            raise ComponentDownloadError("无效的投稿组件下载任务。")
        url, output, size_text = args[1:]
        _validate_source(url)
        expected_size = int(size_text)
        path = Path(output)
        if not path.is_absolute() or not 0 < expected_size <= MAX_BYTES or os.path.lexists(path):
            raise ComponentDownloadError("无效的投稿组件下载目标。")
        _fetch_python(url, path, expected_size)
        return 0
    except (OSError, ValueError, IncompleteRead) as exc:
        failure = _classify(exc)
        print(json.dumps({"message": str(failure), "transient": failure.transient, "tls": failure.tls,
                          "not_found": failure.not_found}))
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
