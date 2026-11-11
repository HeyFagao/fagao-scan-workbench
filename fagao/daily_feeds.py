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
RAW_ROOT = 'https://raw.githubusercontent.com/ShiQi-Han03/fagao-ranking-feed/'


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
    url_pattern = re.escape(RAW_ROOT) + r'[a-f0-9]{40}/objects/' + board + '/' + revision + r'\.bin'
    if not isinstance(manifest.get('artifact_url'), str) or not re.fullmatch(url_pattern, manifest['artifact_url']):
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
        raise _invalid()


def _download(url, expected_length):
    request = urllib.request.Request(url, headers={'Accept': 'application/octet-stream'})
    try:
        with network.build_opener(_NoRedirect()).open(request, timeout=12) as response:
            if response.geturl() != url or response.status != 200:
                raise _invalid()
            length = response.headers.get('Content-Length')
            if length is not None and length != str(expected_length):
                raise _invalid()
            deadline = time.monotonic() + 45
            content = bytearray()
            read_chunk = getattr(response, 'read1', response.read)
            while len(content) <= expected_length:
                if time.monotonic() > deadline:
                    raise TimeoutError
                chunk = read_chunk(min(65536, expected_length + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) != expected_length:
                raise _invalid()
            return bytes(content)
    except AppError:
        raise
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise AppError('daily_feed_download', '暂时无法下载新版榜单，请稍后重试。', 503) from None


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
    fetch = download or _download
    token = fetch(manifest['artifact_url'], manifest['byte_length'])
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
