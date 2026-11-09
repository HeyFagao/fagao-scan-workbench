"""Local client for the proposed, narrowly scoped workbench authorization API.

The website endpoints below are an integration contract, not a claim that the
production website already implements them.  A transport can be injected for
isolated tests; there is deliberately no environment switch that disables auth.

Only the revocable device credential and account identity are persisted.  Every
new client/process verifies online before it can use data.  A successful HTTPS
response may authorize this running client until the earlier server deadline,
measured with a monotonic clock; a saved JSON file is never an offline permit.
This protects official application entrypoints, not a locally modified program.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import __version__, network
from .store import AppError


DEFAULT_ORIGIN = "https://www.fagaochuangzuo.com"
DEFAULT_ENDPOINTS = {
    "authorize": "/workbench/authorize", "token": "/api/workbench/token",
    "permit": "/api/workbench/permit", "revoke": "/api/workbench/revoke",
}
MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT = 8
AUTHORIZATION_TTL = 300
MAX_PERMIT_SECONDS = 900
_ACCESS_BASES = {"paid_membership", "experimental_membership", "experimental_account"}
_REASONS = {
    "active", "not_authenticated", "not_eligible", "expired", "suspended",
    "device_revoked", "device_limit", "verification_unavailable",
}
_MESSAGES = {
    "not_authenticated": "请先连接发糕账号。",
    "not_eligible": "当前账号没有有效的扫榜会员资格，请在官网查看会员权益。",
    "expired": "会员或使用许可已到期，请核验会员或前往官网续费。",
    "suspended": "此账号的扫榜工作台已停用，请联系管理员。",
    "device_revoked": "此设备授权已撤销，请重新连接发糕账号。",
    "device_limit": "已达到设备数量上限，请在官网移除旧设备。",
    "verification_unavailable": "暂时无法核验会员，已有资料仍保留，请稍后重试。",
    "account_changed": "登录账号或会话已改变，请重新发起操作。",
    "invalid_callback": "授权回调无效，请重新连接发糕账号。",
    "authorization_expired": "本次授权已过期，请重新连接发糕账号。",
    "storage_error": "本机授权资料暂时无法安全读取，请让维护助手检查。",
}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _error(reason, status=403):
    return AppError("membership_" + reason, _MESSAGES.get(reason, _MESSAGES["verification_unavailable"]), status)


class _Unavailable(Exception):
    pass


class _InvalidResponse(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _InvalidResponse()


def _text(value, maximum=200):
    if (not isinstance(value, str) or not value or len(value) > maximum
            or re.search(r"[\x00-\x1f\x7f]", value)):
        raise _InvalidResponse()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _InvalidResponse() from None
    return value


def _credential(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~\-]{32,2048}", value):
        raise _InvalidResponse()
    return value


def _account(value):
    if not isinstance(value, dict):
        raise _InvalidResponse()
    return {"id": _text(value.get("id")), "display_name": _text(value.get("display_name"), 120)}


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        raise _InvalidResponse()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        result = parsed.timestamp()
        if not math.isfinite(result):
            raise ValueError()
        return result
    except (ValueError, OverflowError, OSError):
        raise _InvalidResponse() from None


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _redirect(value):
    try:
        if not isinstance(value, str) or len(value) > 200 or re.search(r"[\s\\\x00-\x1f]", value):
            raise ValueError()
        parts = urllib.parse.urlsplit(value)
        if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "::1")
                or parts.username is not None or parts.password is not None
                or not parts.port or parts.path != "/auth/callback" or parts.query or parts.fragment
                or not 1 <= parts.port <= 65535):
            raise ValueError()
        # Exact canonical syntax also rules out unusual parser interpretations.
        host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
        if value != f"http://{host}:{parts.port}/auth/callback":
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise _error("invalid_callback", 400) from None


@contextmanager
def _file_lock(path):
    key = str(path)
    with _LOCKS_GUARD:
        thread_lock = _LOCKS.setdefault(key, threading.RLock())
    if not thread_lock.acquire(timeout=12):
        raise _error("storage_error", 503)
    fd = None
    acquired = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError()
        os.chmod(path, 0o600)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        deadline = time.monotonic() + 12
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.025)
        yield
    except OSError:
        raise _error("storage_error", 503) from None
    finally:
        if fd is not None:
            if acquired:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        thread_lock.release()


class MembershipClient:
    """One runtime's verified identity; local files coordinate HTTP and CLI.

    ``transport(method, url, payload, token)`` returns decoded JSON.  Token
    exchange returns ``{device_token, scope: 'workbench', status: {...}}``;
    permit returns the unified status, optionally with a replacement
    ``device_token`` and ``scope``.  The transport must enforce HTTPS and must
    not follow redirects.  The built-in transport does so using network.py.

    ``clock`` supplies ``time()`` and ``monotonic()``.  Constructor-only origin
    and endpoint overrides support controlled integration without a runtime
    auth-disable setting.  Credentials are never included in public results.
    """

    def __init__(self, root_dir, transport=None, clock=None, *, origin=DEFAULT_ORIGIN, endpoints=None):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.directory = self.root_dir / ".membership"
        self.clock = clock or time
        self.transport = transport or self._http_transport
        self._mutex = threading.RLock()
        self._cache = None
        self._identity_cache = None
        self._cache_generation = None
        self._cache_pid = None
        self._observed_mono = self._observed_wall = self._server_epoch = None
        self._last_result = None
        self._last_checked = None
        self._last_generation = None
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or re.search(r"[\s\\]", origin)):
            raise ValueError("Membership origin must be an HTTPS origin.")
        self.origin = origin.rstrip("/")
        paths = dict(DEFAULT_ENDPOINTS)
        paths.update(endpoints or {})
        if set(paths) != set(DEFAULT_ENDPOINTS) or any(
                not isinstance(p, str) or not re.fullmatch(r"/[A-Za-z0-9/_-]+", p) or p.startswith("//")
                for p in paths.values()):
            raise ValueError("Invalid membership endpoint configuration.")
        self.endpoints = paths
        try:
            if self.directory.is_symlink():
                raise OSError()
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.directory, 0o700)
            self._state_path = self.directory / "session.json"
            with self._state_lock():
                if not self._state_path.exists():
                    self._save(self._empty())
                else:
                    self._read()
        except (OSError, _InvalidResponse):
            raise _error("storage_error", 503) from None

    def _empty(self, device_id=None):
        return {"version": 1, "generation": secrets.token_hex(32),
                "device_id": device_id or secrets.token_urlsafe(32),
                "account": None, "device_token": None, "pending": None, "exchange_id": None}

    def _state_lock(self):
        return _file_lock(self.directory / "session.lock")

    def _read(self):
        try:
            if self._state_path.is_symlink():
                raise OSError()
            fd = os.open(self._state_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RESPONSE_BYTES:
                    raise ValueError()
                value = json.load(handle)
            if (not isinstance(value, dict) or value.get("version") != 1
                    or not re.fullmatch(r"[a-f0-9]{64}", value.get("generation", ""))):
                raise ValueError()
            _credential(value.get("device_id"))
            if value.get("device_token") is not None:
                _credential(value["device_token"])
                value["account"] = _account(value.get("account"))
            elif value.get("account") is not None:
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError, _InvalidResponse):
            raise _error("storage_error", 503) from None

    def _save(self, value):
        temporary = None
        try:
            fd, filename = tempfile.mkstemp(prefix=".session-", dir=self.directory)
            temporary = Path(filename)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.chmod(temporary, 0o600)
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._state_path)
        except (OSError, ValueError, TypeError):
            raise _error("storage_error", 503) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @property
    def active_generation(self):
        with self._state_lock():
            return self._read()["generation"]

    @property
    def current_account_id(self):
        with self._state_lock():
            account = self._read().get("account")
            return account["id"] if account else None

    def account_directory(self, account_id):
        try:
            account_id = _text(account_id)
        except _InvalidResponse:
            raise _error("account_changed") from None
        parent = self.root_dir / "accounts"
        result = parent / hashlib.sha256(account_id.encode("utf-8")).hexdigest()
        if parent.is_symlink() or result.is_symlink():
            raise _error("storage_error", 503)
        return result

    def _base_status(self, state, reason):
        account = deepcopy(state.get("account"))
        return {"allowed": False, "reason": reason, "account": account,
                "account_id": account["id"] if account else None,
                "display_name": account["display_name"] if account else None,
                "generation": state["generation"], "membership_expires_at": None,
                "permit_expires_at": None, "identity_expires_at": None,
                "access_basis": None,
                "server_time": None, "policy_version": None,
                "history_eligible": False, "history_membership_expires_at": None,
                "can_read_history": False, "access_mode": "locked"}

    def _access_fields(self, result):
        """The approved expired-member policy allows existing history only."""
        read_only = (result["reason"] == "expired" and result.get("account") is not None
                     and result.get("membership_expires_at") is not None and result.get("server_time") is not None
                     and _timestamp(result["membership_expires_at"]) <= _timestamp(result["server_time"]))
        prior_paid_history = (result["reason"] == "not_eligible" and result.get("account") is not None
                              and result.get("history_eligible") is True
                              and result.get("history_membership_expires_at") is not None
                              and result.get("identity_expires_at") is not None and result.get("server_time") is not None
                              and _timestamp(result["history_membership_expires_at"]) <= _timestamp(result["server_time"])
                              < _timestamp(result["identity_expires_at"]))
        read_only = read_only or prior_paid_history
        result["can_read_history"] = bool(result["allowed"] or read_only)
        result["access_mode"] = "active" if result["allowed"] else "read_only" if read_only else "locked"
        return result

    def _validate_status(self, raw, expected_account=None):
        if not isinstance(raw, dict) or type(raw.get("allowed")) is not bool or raw.get("reason") not in _REASONS:
            raise _InvalidResponse()
        allowed = raw["allowed"]
        if allowed != (raw["reason"] == "active"):
            raise _InvalidResponse()
        # Only a known basis in a verified server response can detach an
        # account-specific experiment from the real membership deadline.
        # Older servers omitted this field and retain the paid-member rules.
        access_basis = raw.get("access_basis", "paid_membership")
        if not isinstance(access_basis, str) or access_basis not in _ACCESS_BASES:
            raise _InvalidResponse()
        account_experiment = access_basis == "experimental_account"
        server = _timestamp(raw.get("server_time"))
        policy = _text(raw.get("policy_version"), 100)
        account = _account(raw["account"]) if raw.get("account") is not None else None
        if expected_account and account is not None and account["id"] != expected_account:
            raise _InvalidResponse()
        if expected_account and (raw["reason"] == "expired" or raw.get("history_eligible") is True) and account is None:
            # Expired history access needs a server-confirmed identity; local
            # display metadata alone cannot become a read authorization.
            raise _InvalidResponse()
        membership = _timestamp(raw["membership_expires_at"]) if raw.get("membership_expires_at") is not None else None
        permit = _timestamp(raw["permit_expires_at"]) if raw.get("permit_expires_at") is not None else None
        identity = _timestamp(raw["identity_expires_at"]) if raw.get("identity_expires_at") is not None else None
        history_eligible = raw.get("history_eligible", False)
        if type(history_eligible) is not bool:
            raise _InvalidResponse()
        history_end = (_timestamp(raw["history_membership_expires_at"])
                       if raw.get("history_membership_expires_at") is not None else None)
        if history_eligible and (account is None or history_end is None or history_end > server
                                 or identity is None or identity <= server):
            raise _InvalidResponse()
        if identity is not None and identity <= server:
            raise _InvalidResponse()
        if permit is not None and not server < permit <= server + MAX_PERMIT_SECONDS:
            raise _InvalidResponse()
        if not account_experiment and permit is not None and membership is not None and permit > membership:
            raise _InvalidResponse()
        if allowed and (not account or permit is None
                        or (not account_experiment and (membership is None or permit > membership))):
            raise _InvalidResponse()
        return {"allowed": allowed, "reason": raw["reason"], "account": account,
                "access_basis": access_basis,
                "account_id": account["id"] if account else None,
                "display_name": account["display_name"] if account else None,
                "membership_expires_at": _iso(membership) if membership is not None else None,
                "permit_expires_at": _iso(permit) if permit is not None else None,
                "identity_expires_at": _iso(identity) if identity is not None else None,
                "history_eligible": history_eligible,
                "history_membership_expires_at": _iso(history_end) if history_end is not None else None,
                "server_time": _iso(server), "policy_version": policy}

    def _http_transport(self, method, url, payload, token):
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "FagaoWorkbench/" + __version__}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method)
        try:
            response = network.build_opener(_NoRedirect()).open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                exc.close()
                raise _InvalidResponse() from None
            if exc.code not in (400, 401, 403, 409):
                exc.close()
                raise _Unavailable() from None
            response = exc
        try:
            with response:
                http_status = getattr(response, "status", getattr(response, "code", 200))
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
                    raise _InvalidResponse()
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise _InvalidResponse()
                value = json.loads(body.decode("utf-8"))
                if not 200 <= http_status < 300 and isinstance(value, dict):
                    nested = value.get("status")
                    candidates = (value, nested) if isinstance(nested, dict) else (value,)
                    if any(item.get("allowed") is True or item.get("reason") == "expired"
                           or item.get("history_eligible") is True for item in candidates):
                        # Expired is an authenticated history-read grant in the
                        # approved policy, so it too must come over HTTP success.
                        raise _InvalidResponse()
                return value
        except (UnicodeError, ValueError):
            raise _InvalidResponse() from None

    def _request(self, name, payload, token=None):
        try:
            with network.without_authorization():
                value = self.transport("POST", self.origin + self.endpoints[name], payload, token)
            # Injected transports obey the same bounded JSON contract.
            if not isinstance(value, dict) or len(json.dumps(value).encode("utf-8")) > MAX_RESPONSE_BYTES:
                raise _InvalidResponse()
            return value
        except (_InvalidResponse, _Unavailable):
            raise
        except Exception:
            # Network implementations may include URLs or credentials in their
            # exceptions.  None of that text crosses the public API boundary.
            raise _Unavailable() from None

    def _clear_cache(self):
        self._cache = self._cache_generation = self._cache_pid = None
        self._identity_cache = None
        self._last_result = self._last_checked = self._last_generation = None

    def _estimated_time(self, state):
        if self._cache_pid != os.getpid() or self._cache_generation != state["generation"]:
            return None
        elapsed = self.clock.monotonic() - self._observed_mono
        wall_elapsed = self.clock.time() - self._observed_wall
        if not math.isfinite(elapsed) or not math.isfinite(wall_elapsed) or elapsed < 0 or wall_elapsed < -5:
            return None
        return self._server_epoch + max(elapsed, wall_elapsed)

    def _cached(self, state):
        if (not self._cache or self._cache_pid != os.getpid()
                or self._cache_generation != state["generation"]):
            return None
        current = self._estimated_time(state)
        if current is None:
            return None
        deadline = _timestamp(self._cache["permit_expires_at"])
        if self._cache["access_basis"] != "experimental_account":
            deadline = min(deadline, _timestamp(self._cache["membership_expires_at"]))
        if current >= deadline:
            return None
        result = deepcopy(self._cache)
        result["server_time"] = _iso(current)
        return result

    def _history_cached(self, state):
        if self._identity_cache is None:
            return None
        current = self._estimated_time(state)
        proof = self._identity_cache
        if proof["allowed"] and proof.get("access_basis") == "experimental_account":
            # An experiment is not evidence of a paid-membership history grant.
            # After its short permit expires, obtain a new server decision.
            return None
        prior_paid = proof["reason"] == "not_eligible" and proof.get("history_eligible") is True
        history_boundary = proof["history_membership_expires_at"] if prior_paid else proof["membership_expires_at"]
        if (current is None or current >= _timestamp(proof["identity_expires_at"])
                or current < _timestamp(history_boundary)):
            return None
        result = deepcopy(proof)
        result.update(allowed=False, reason="not_eligible" if prior_paid else "expired", server_time=_iso(current))
        return self._access_fields(result)

    def _remember(self, result, state, started):
        result = deepcopy(result)
        result["generation"] = state["generation"]
        self._access_fields(result)
        self._cache = None
        self._identity_cache = None
        if result["can_read_history"]:
            elapsed_mono = self.clock.monotonic() - started[0]
            elapsed_wall = self.clock.time() - started[1]
            if (not math.isfinite(elapsed_mono) or not math.isfinite(elapsed_wall)
                    or elapsed_mono < 0 or elapsed_wall < -5):
                result = self._base_status(state, "verification_unavailable")
                self._last_result, self._last_checked, self._last_generation = deepcopy(result), self.clock.monotonic(), state["generation"]
                return result
            # Wall time covers a laptop sleeping during the HTTPS request on
            # systems whose monotonic clock pauses. Never restart a stale lease
            # from the moment the delayed response finally reaches the client.
            elapsed = max(0, elapsed_mono, elapsed_wall)
            self._server_epoch = _timestamp(result["server_time"]) + elapsed
            self._observed_mono, self._observed_wall = self.clock.monotonic(), self.clock.time()
            self._cache_generation, self._cache_pid = state["generation"], os.getpid()
            if (result.get("identity_expires_at") is not None
                    and not (result["allowed"] and result.get("access_basis") == "experimental_account")
                    and _timestamp(result["identity_expires_at"]) > self._server_epoch):
                self._identity_cache = deepcopy(result)
            if result["allowed"]:
                self._cache = deepcopy(result)
                current = self._cached(state)
                if current is None:
                    self._cache = None
                    # A slow exchange may consume the entire short permit. It
                    # does not establish that an account experiment has ended
                    # or that the user's real membership has expired.
                    fallback = "verification_unavailable" if result.get("access_basis") == "experimental_account" else "expired"
                    result = self._history_cached(state) or self._base_status(state, fallback)
                else:
                    result = current
            else:
                result["server_time"] = _iso(self._server_epoch)
                if result.get("identity_expires_at") is not None and self._identity_cache is None:
                    result = self._base_status(state, "verification_unavailable")
        self._last_result, self._last_checked, self._last_generation = deepcopy(result), self.clock.monotonic(), state["generation"]
        return result

    def status(self, refresh=False):
        """Return a sanitized decision; routine denied/network states do not raise."""
        with self._mutex:
            try:
                with self._state_lock():
                    state = self._read()
                if not state.get("device_token"):
                    self._clear_cache()
                    return self._base_status(state, "not_authenticated")
                cached = self._cached(state) or self._history_cached(state)
                if cached and not refresh:
                    return cached
                if (not refresh and self._last_generation == state["generation"] and self._last_result
                        and not self._last_result["allowed"] and self._last_checked is not None
                        and not self._last_result.get("can_read_history")
                        and 0 <= self.clock.monotonic() - self._last_checked < 2):
                    return deepcopy(self._last_result)
                # Only one process may renew/rotate at a time.  Logout uses the
                # separate short state lock and can invalidate an in-flight call.
                with _file_lock(self.directory / "request.lock"):
                    with self._state_lock():
                        state = self._read()
                    if not state.get("device_token"):
                        self._clear_cache()
                        return self._base_status(state, "not_authenticated")
                    started = (self.clock.monotonic(), self.clock.time())
                    malformed = False
                    try:
                        raw = self._request("permit", {"device_id": state["device_id"], "client_version": __version__}, state["device_token"])
                        result = self._validate_status(raw, state["account"]["id"])
                        if result["account"] is None:
                            # A revoked token may no longer resolve to a website
                            # account.  Retain only local display identity; this
                            # can never turn a denied decision into permission.
                            result.update(account=deepcopy(state["account"]), account_id=state["account"]["id"],
                                          display_name=state["account"]["display_name"])
                        rotated = raw.get("device_token")
                        if rotated is not None:
                            rotated = _credential(rotated)
                            if raw.get("scope") != "workbench":
                                raise _InvalidResponse()
                    except _InvalidResponse:
                        malformed = True
                        result, rotated = None, None
                    except _Unavailable:
                        result, rotated = None, None
                    with self._state_lock():
                        current = self._read()
                        stale = (current["generation"] != state["generation"]
                                 or current.get("device_token") != state["device_token"])
                        if not stale and result is not None:
                            if rotated:
                                current["device_token"] = rotated
                                self._save(current)
                            return self._remember(result, current, started)
                    if stale:
                        # Logout may have revoked the old credential while the
                        # server rotated it.  Revoke the late replacement too,
                        # outside the state lock, rather than orphan a device.
                        if rotated and rotated != state["device_token"]:
                            try:
                                self._request("revoke", {"device_id": state["device_id"]}, rotated)
                            except (_Unavailable, _InvalidResponse):
                                pass
                        self._clear_cache()
                        return self._base_status(current, "account_changed" if current.get("account") else "not_authenticated")
                    if not malformed:
                        cached = self._cached(state) or self._history_cached(state)
                        if cached:
                            cached["verification_pending"] = True
                            return cached
                    self._cache = None
                    return self._remember(self._base_status(state, "verification_unavailable"), state, started)
            except AppError:
                self._clear_cache()
                # Storage failure must not make an old permit authoritative.
                return self._base_status({"account": None, "generation": None}, "verification_unavailable")

    def require(self, account_id=None, operation="use", generation=None):
        """Check every official data/operation boundary, including worker pages."""
        result = self.status()
        if ((account_id is not None and result["account_id"] != account_id)
                or (generation is not None and result["generation"] != generation)):
            raise _error("account_changed")
        if not result["allowed"]:
            if operation in ("read", "history") and result.get("can_read_history"):
                return result
            reason = result["reason"]
            raise _error(reason, 503 if reason == "verification_unavailable" else 403)
        return result

    def _expect_identity(self, current, expected_account, expected_generation):
        account = current.get("account")
        account_id = account["id"] if account else None
        if ((expected_generation is not None and current["generation"] != expected_generation)
                or ((expected_account is not None or expected_generation is not None) and account_id != expected_account)):
            raise _error("account_changed")

    def begin_authorization(self, redirect_uri, *, expected_account=None, expected_generation=None):
        redirect_uri = _redirect(redirect_uri)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        with self._state_lock():
            current = self._read()
            self._expect_identity(current, expected_account, expected_generation)
            current["pending"] = {"state": state, "verifier": verifier, "redirect_uri": redirect_uri,
                                  "created_at": self.clock.time()}
            current["exchange_id"] = None
            self._save(current)
        query = urllib.parse.urlencode({"client_id": "fagao-workbench", "response_type": "code", "state": state,
                                      "code_challenge": challenge, "code_challenge_method": "S256",
                                      "redirect_uri": redirect_uri, "device_id": current["device_id"],
                                      "device_name": "发糕扫榜工作台", "client_version": __version__})
        return {"authorization_url": self.origin + self.endpoints["authorize"] + "?" + query, "state": state}

    def complete_authorization(self, code, state, redirect_uri):
        redirect_uri = _redirect(redirect_uri)
        if (not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9._~\-]{1,4096}", code)
                or not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state)):
            raise _error("invalid_callback", 400)
        with self._state_lock():
            current = self._read()
            pending = current.get("pending")
            if (not isinstance(pending, dict) or not isinstance(pending.get("state"), str)
                    or not secrets.compare_digest(state, pending["state"])
                    or pending.get("redirect_uri") != redirect_uri):
                raise _error("invalid_callback", 400)
            created = pending.get("created_at")
            if (not isinstance(created, (int, float)) or not math.isfinite(created)
                    or not -5 <= self.clock.time() - created < AUTHORIZATION_TTL):
                current["pending"] = None
                self._save(current)
                raise _error("authorization_expired", 400)
            current["pending"] = None
            exchange_id = current["exchange_id"] = secrets.token_hex(32)
            self._save(current)
        started = (self.clock.monotonic(), self.clock.time())
        try:
            raw = self._request("token", {"grant_type": "authorization_code", "code": code,
                                          "code_verifier": pending["verifier"], "redirect_uri": redirect_uri,
                                          "device_id": current["device_id"], "client_id": "fagao-workbench"})
            result = self._validate_status(raw.get("status"))
            self._access_fields(result)
            if not result["can_read_history"]:
                raise _error(result["reason"])
            token = _credential(raw.get("device_token"))
            if raw.get("scope") != "workbench":
                raise _InvalidResponse()
        except (_Unavailable, _InvalidResponse):
            raise _error("verification_unavailable", 503) from None
        with self._mutex:
            with self._state_lock():
                latest = self._read()
                if latest["generation"] != current["generation"] or latest.get("exchange_id") != exchange_id:
                    stale = True
                else:
                    stale = False
                    latest.update({"generation": secrets.token_hex(32), "account": result["account"],
                                   "device_token": token, "pending": None, "exchange_id": None})
                    self._save(latest)
            if stale:
                try:
                    self._request("revoke", {"device_id": current["device_id"]}, token)
                except (_Unavailable, _InvalidResponse):
                    pass
                raise _error("account_changed")
            return self._remember(result, latest, started)

    def cancel_authorization(self, state, redirect_uri, *, expected_account=None, expected_generation=None):
        """Consume a verified denial callback without changing the device session.

        The caller supplies only the state and canonical local callback, never a
        provider's free-form error message.  A returned cancellation notice is
        separate from the still-current member's access decision.
        """
        redirect_uri = _redirect(redirect_uri)
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
            raise _error("invalid_callback", 400)
        with self._state_lock():
            current = self._read()
            self._expect_identity(current, expected_account, expected_generation)
            pending = current.get("pending")
            if (not isinstance(pending, dict) or not isinstance(pending.get("state"), str)
                    or not secrets.compare_digest(state, pending["state"])
                    or pending.get("redirect_uri") != redirect_uri):
                raise _error("invalid_callback", 400)
            created = pending.get("created_at")
            if (not isinstance(created, (int, float)) or not math.isfinite(created)
                    or not -5 <= self.clock.time() - created < AUTHORIZATION_TTL):
                current["pending"] = None
                self._save(current)
                raise _error("authorization_expired", 400)
            current["pending"] = None
            self._save(current)
        result = self.status()
        result.update(authorization_status="authorization_cancelled", authorization_cancelled=True)
        return result

    def logout(self, *, expected_account=None, expected_generation=None):
        # Clear local identity before a possibly slow/offline revocation request.
        with self._state_lock():
            previous = self._read()
            self._expect_identity(previous, expected_account, expected_generation)
            current = self._empty(previous["device_id"])
            self._save(current)
        result = self._base_status(current, "not_authenticated")
        if previous.get("device_token"):
            try:
                self._request("revoke", {"device_id": previous["device_id"]}, previous["device_token"])
            except (_Unavailable, _InvalidResponse):
                result["revocation_pending"] = True
        # Avoid waiting on an instance's in-flight network request: generation
        # mismatch makes its cached permit unusable even before cache clearing.
        return result
