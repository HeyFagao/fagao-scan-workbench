# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Read official, account-authorized daily ranking bundles and cache verified snapshots."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
import hashlib
import hmac
import http.client
import json
import re
import time
import urllib.error
import urllib.request
import zlib

from . import network
from ._vendor.pyaes import AESModeOfOperationCBC
from .store import AppError

BOARDS = ('douyin', 'hongguo', 'zhihu', 'fanqie_story', 'adapt')
FORMAT = 'json+gzip+fernet-v1'
MAX_TOKEN_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
REVISION = re.compile('[a-f0-9]{64}')
FEED_REPOSITORY = 'ShiQi-Han03/fagao-ranking-feed'
RAW_ROOT = f'https://raw.githubusercontent.com/{FEED_REPOSITORY}/'
CDN_HOSTS = ('cdn.jsdelivr.net', 'gcore.jsdelivr.net')
OBJECT_URL = re.compile(re.escape(RAW_ROOT) +
                        r'(?P<commit>[a-f0-9]{40})/objects/(?P<board>[a-z_]+)/(?P<revision>[a-f0-9]{64})\.bin')
DOWNLOAD_SECONDS = 36
ROUTE_SECONDS = 12
SOCKET_TIMEOUT = 5


def _invalid():
    return AppError('daily_feed_invalid', '榜单数据校验失败，已保存的版本仍可查看。', 502)


def board_id(value):
    if not isinstance(value, str) or value not in BOARDS:
        raise AppError('daily_feed_board', '请选择有效的每日榜单。', 400)
    return value


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 48:
        raise _invalid()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise _invalid() from None


def _valid_date(value):
    if value is None:
        return True
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_manifest(manifest, board):
    board_id(board)
    if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest['version'] != 1:
        raise _invalid()
    if manifest.get('board') != board or manifest.get('format') != FORMAT:
        raise _invalid()
    revision = manifest.get('sha256')
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise _invalid()
    url = manifest.get('artifact_url')
    obj = OBJECT_URL.fullmatch(url) if isinstance(url, str) else None
    if not obj or obj['board'] != board or obj['revision'] != revision:
        raise _invalid()
    if type(manifest.get('byte_length')) is not int or not 100 <= manifest['byte_length'] <= MAX_TOKEN_BYTES:
        raise _invalid()
    if type(manifest.get('row_count')) is not int or not 1 <= manifest['row_count'] <= 100_000:
        raise _invalid()
    if 'source_date' not in manifest or not _valid_date(manifest['source_date']):
        raise _invalid()
    _timestamp(manifest.get('observed_at'))
    return manifest


def _base64url(value):
    try:
        data = base64.b64decode(value, altchars=b'-_', validate=True)
        if base64.urlsafe_b64encode(data) != value:
            raise ValueError
        return data
    except (ValueError, binascii.Error):
        raise _invalid() from None


def decrypt_bundle(token, key_b64):
    """Decode standard Fernet v0x80 (github.com/fernet/spec) after checking its MAC.

    The website grants access to each immutable file. Its Fernet creation time is
    not an account expiry; the existing device entitlement controls every sync.
    """
    if not isinstance(token, bytes) or not 100 <= len(token) <= MAX_TOKEN_BYTES:
        raise _invalid()
    if not isinstance(key_b64, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}=', key_b64):
        raise _invalid()
    key = _base64url(key_b64.encode('ascii'))
    data = _base64url(token)
    if len(key) != 32 or len(data) < 73 or data[0] != 0x80 or (len(data) - 57) % 16:
        raise _invalid()
    expected = hmac.digest(key[:16], data[:-32], 'sha256')
    if not hmac.compare_digest(expected, data[-32:]):
        raise _invalid()
    decryptor = AESModeOfOperationCBC(key[16:], iv=data[9:25])
    out = bytearray()
    for pos in range(25, len(data) - 32, 16):
        out.extend(decryptor.decrypt(data[pos:pos + 16]))
    pad = out[-1]
    if not 1 <= pad <= 16 or not hmac.compare_digest(out[-pad:], bytes([pad]) * pad):
        raise _invalid()
    return bytes(out[:-pad])


def decode_snapshot(grant, board, token):
    if not isinstance(grant, dict) or grant.get('ok') is not True:
        raise _invalid()
    manifest = validate_manifest(grant.get('manifest'), board)
    if not isinstance(token, bytes) or len(token) != manifest['byte_length'] or hashlib.sha256(token).hexdigest() != manifest['sha256']:
        raise _invalid()
    compressed = decrypt_bundle(token, grant.get('key_b64'))
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = inflater.decompress(compressed, MAX_JSON_BYTES + 1)
        if len(decoded) > MAX_JSON_BYTES or not inflater.eof or inflater.unused_data:
            raise _invalid()
        snapshot = json.loads(decoded.decode('utf-8'))
    except (zlib.error, UnicodeError, ValueError, RecursionError):
        raise _invalid() from None
    if not isinstance(snapshot, dict) or snapshot.get('board') != board:
        raise _invalid()
    if 'source_date' not in snapshot or snapshot['source_date'] != manifest['source_date']:
        raise _invalid()
    if _timestamp(snapshot.get('observed_at')) != _timestamp(manifest['observed_at']):
        raise _invalid()
    url = snapshot.get('source_url')
    if not isinstance(url, str) or len(url) > 2048 or not re.fullmatch(r'https://(?:www\.)?wangwendashuju\.com/[^\s\\#]*', url):
        raise _invalid()
    scope, columns, rows = snapshot.get('scope'), snapshot.get('columns'), snapshot.get('rows')
    if not isinstance(scope, str) or not 0 < len(scope) <= 4096:
        raise _invalid()
    if not isinstance(columns, list) or not 1 <= len(columns) <= 128 or not all(isinstance(c, str) and 0 < len(c) <= 256 for c in columns):
        raise _invalid()
    if not isinstance(rows, list) or len(rows) != manifest['row_count']:
        raise _invalid()
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns) or not all(isinstance(cell, str) and len(cell) <= 65536 for cell in row):
            raise _invalid()
    result = {field: snapshot[field] for field in ('board', 'source_date', 'observed_at', 'source_url', 'scope', 'columns', 'rows')}
    if 'coverage' in snapshot:
        coverage = snapshot['coverage']
        if not isinstance(coverage, dict):
            raise _invalid()
        result['coverage'] = {k: coverage[k] for k in ('mode', 'rows_observed', 'source_total', 'complete', 'limit') if k in coverage}
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise _invalid()


def _read_object(url, expected_length, revision, deadline, require):
    request = urllib.request.Request(url, headers={'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity'})
    timeout = min(SOCKET_TIMEOUT, deadline - time.monotonic())
    if timeout <= 0:
        raise TimeoutError
    with network.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        if response.geturl() != url or response.status != 200:
            raise _invalid()
        length = response.headers.get('Content-Length')
        if length is not None and length != str(expected_length):
            raise _invalid()
        content = bytearray()
        read_chunk = getattr(response, 'read1', response.read)
        next_check = time.monotonic() + 1
        while len(content) <= expected_length:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError
            if now >= next_check:
                require()
                next_check = now + 1
            chunk = read_chunk(min(65536, expected_length + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) != expected_length or hashlib.sha256(content).hexdigest() != revision:
            raise _invalid()
        return bytes(content)


def _download(url, expected_length, *, require=network.check_authorization):
    obj = OBJECT_URL.fullmatch(url) if isinstance(url, str) else None
    if not obj or obj['board'] not in BOARDS or type(expected_length) is not int or not 100 <= expected_length <= MAX_TOKEN_BYTES:
        raise _invalid()
    # All routes carry the same public ciphertext. The manifest and its key come
    # only from the website, and each URL is built from the immutable object ID.
    path = f"/gh/{FEED_REPOSITORY}@{obj['commit']}/objects/{obj['board']}/{obj['revision']}.bin"
    urls = [url] + [f'https://{host}{path}' for host in CDN_HOSTS]
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    invalid_response = False
    for address in urls:
        require()
        now = time.monotonic()
        if now >= deadline:
            break
        try:
            return _read_object(address, expected_length, obj['revision'], min(deadline, now + ROUTE_SECONDS), require)
        except AppError as exc:
            if exc.code != 'daily_feed_invalid':
                raise
            invalid_response = True
        except urllib.error.HTTPError as exc:
            exc.close()
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            continue
    if invalid_response:
        raise _invalid()
    raise AppError('daily_feed_download', '暂时无法下载新版榜单，请稍后重试。', 503)


def _envelope(store, connection, board, revision, status):
    prefix = f'daily-board:{board}:'
    history = store._kv(connection, prefix + 'history', [])
    if revision is not None:
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise AppError('daily_feed_revision', '榜单版本无效。', 400)
    else:
        revision = history[0]['revision'] if history else None
    snapshot = store._kv(connection, prefix + revision) if revision else None
    if revision and snapshot is None:
        raise AppError('daily_feed_not_cached', '这份榜单尚未保存在当前账号。', 404)
    return {'board': board, 'status': status if snapshot else 'empty', 'revision': revision,
            'snapshot': snapshot, 'history': history}


def read_cache(store, board, revision=None):
    board_id(board)
    with store.connect() as connection:
        return _envelope(store, connection, board, revision, 'cached')


def sync_board(ctx, membership, board, download=None):
    board_id(board)
    if ctx.operation in ('read', 'history'):
        raise AppError('daily_feed_read_only', '当前账号仅可查看已保存的榜单。', 403)
    ctx.require()
    grant = membership.feed_grant(board, ctx.account_id, ctx.generation)
    ctx.require()
    if not isinstance(grant, dict) or grant.get('ok') is not True:
        raise _invalid()
    manifest = validate_manifest(grant.get('manifest'), board)
    revision = manifest['sha256']
    cached = read_cache(ctx.store, board)
    if any(entry['revision'] == revision for entry in cached['history']):
        cached['status'] = 'unchanged'
        return cached
    if download is None:
        token = _download(manifest['artifact_url'], manifest['byte_length'], require=ctx.require)
    else:
        token = download(manifest['artifact_url'], manifest['byte_length'])
    ctx.require()
    snapshot = decode_snapshot(grant, board, token)
    ctx.require()
    # Account-bound SQLite transaction commits the index and content together.
    # A slow, older request cannot replace a newer successfully saved edition.
    prefix = f'daily-board:{board}:'
    store = ctx.store
    with store.connect() as connection:
        history = store._kv(connection, prefix + 'history', [])
        existing = any(entry['revision'] == revision for entry in history)
        if not existing:
            item = {'revision': revision, 'source_date': snapshot['source_date'],
                    'observed_at': snapshot['observed_at'], 'row_count': len(snapshot['rows'])}
            history.append(item)
            history.sort(key=lambda h: (h['source_date'] or _timestamp(h['observed_at']).date().isoformat(),
                                        _timestamp(h['observed_at']), h['revision']), reverse=True)
            store._set(connection, prefix + revision, snapshot)
            store._set(connection, prefix + 'history', history)
        result = _envelope(store, connection, board, None, 'unchanged' if existing else 'updated')
        ctx.require()
    return result
