"""Explicit, recoverable copy of anonymous data into one member account.

This module never opens the old database through Store, copies browser sessions,
or decides ownership automatically. A caller must show an explicit claim action
and pass confirmed=True. The original database remains usable and untouched;
later writes by an old application are not synchronized into the claimed copy.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .store import AppError


TABLES = {
    'snapshots': ('id', 'created_at', 'body'),
    'snapshot_metadata': ('id', 'created_at', 'body'),
    'favorites': ('platform', 'book_id', 'snapshot_id', 'created_at'),
    'history': ('id', 'snapshot_id', 'book_id', 'selected_at'),
    'texts': ('id', 'snapshot_id', 'book_id', 'created_at', 'body', 'content'),
    'tasks': ('id', 'created_at', 'body'),
    'reports': ('id', 'created_at', 'body', 'content'),
    'jobs': ('id', 'created_at', 'body'),
    'kv': ('key', 'body'),
}
DATA_TABLES = tuple(name for name in TABLES if name != 'kv')
MANIFEST = 'legacy-claim.json'
DECISIONS = 'legacy-decisions.json'
MARKER = 'legacy_claim_v1'
_LOCKS = {}
_LOCKS_MUTEX = threading.Lock()


def _error(code, message, status=409):
    return AppError('legacy_' + code, message, status)


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _safe_path(root, name):
    path = root / name
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise _error('unsafe_path', '旧资料路径不安全，请先由维护助手检查。')
    return path


@contextmanager
def _read_only(path, *, immutable=False):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''),
                                 uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        yield connection
    finally:
        connection.close()


def _validate_schema(connection):
    objects = {row['name']: row for row in connection.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
    if any(row['type'] in ('trigger', 'view') for row in objects.values()):
        raise _error('unsupported_schema', '旧资料包含不支持的数据库结构，未导入。')
    for table, columns in TABLES.items():
        item = objects.get(table)
        if (not item or item['type'] != 'table'
                or 'VIRTUAL' in str(item['sql']).upper()
                or tuple(row['name'] for row in connection.execute('PRAGMA table_info("' + table + '")')) != columns):
            raise _error('unsupported_schema', '旧资料结构与此版本不兼容，未导入。')


def _counts(connection):
    return {table: connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
            for table in DATA_TABLES}


def _read_manifest(root):
    path = _safe_path(root, MANIFEST)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError('oversize')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or value.get('status') not in ('prepared', 'completed')
                or not re.fullmatch('[0-9a-f]{32}', value.get('claim_id', ''))
                or not re.fullmatch('[0-9a-f]{64}', value.get('account_digest', ''))):
            raise ValueError('invalid manifest')
        return value
    except (ValueError, TypeError, OSError):
        raise _error('receipt_unreadable', '旧资料认领回执无法读取，请先由维护助手检查。') from None


def inspect_legacy(root_dir):
    """Return counts only; never initialize or repair the anonymous database."""
    root = Path(root_dir).expanduser().resolve()
    source = _safe_path(root, 'workbench.sqlite3')
    manifest = _read_manifest(root)
    result = {'available': False, 'counts': {}, 'claim_status': manifest['status'] if manifest else 'unclaimed',
              'requires_confirmation': True, 'original_preserved': True}
    if not source.is_file():
        return result
    try:
        with _read_only(source) as connection:
            _validate_schema(connection)
            result['counts'] = _counts(connection)
            result['available'] = any(result['counts'].values())
        return result
    except sqlite3.Error:
        raise _error('unreadable', '旧资料无法只读检查，请先由维护助手检查。') from None


@contextmanager
def _claim_lock(root):
    path = _safe_path(root, '.legacy-claim.lock')
    with _LOCKS_MUTEX:
        thread_lock = _LOCKS.setdefault(str(path), threading.Lock())
    if not thread_lock.acquire(timeout=20):
        raise _error('busy', '另一项资料认领正在进行，请稍后重试。')
    handle = None
    locked = False
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.chmod(path, 0o600)
        handle = os.fdopen(descriptor, 'r+b')
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
        else:
            import fcntl
        deadline = time.monotonic() + 20
        while True:
            try:
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise _error('busy', '另一项资料认领正在进行，请稍后重试。')
                time.sleep(.05)
        yield
    finally:
        if handle:
            if locked:
                if os.name == 'nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        thread_lock.release()


def _write_private_json(root, filename, value):
    path = _safe_path(root, filename)
    temporary = _safe_path(root, '.legacy-claim-' + uuid.uuid4().hex + '.tmp')
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(root, value):
    _write_private_json(root, MANIFEST, value)


def _read_decisions(root):
    path = _safe_path(root, DECISIONS)
    if not path.exists():
        return {'version': 1, 'accounts': {}}
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('oversize')
        result = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(result, dict) or result.get('version') != 1
                or not isinstance(result.get('accounts'), dict)):
            raise ValueError('invalid decisions')
        for digest, value in result['accounts'].items():
            if (not re.fullmatch('[0-9a-f]{64}', digest) or not isinstance(value, dict)
                    or value.get('decision') != 'skipped' or not isinstance(value.get('recorded_at'), str)):
                raise ValueError('invalid decision')
        return result
    except (ValueError, TypeError, OSError):
        raise _error('decision_unreadable', '旧资料选择记录无法读取，请先由维护助手检查。') from None


def _verified_identity(runtime, expected_account=None, expected_generation=None, *, operation='use'):
    status = runtime.membership.require(account_id=expected_account, generation=expected_generation,
                                        operation=operation)
    account_id = status.get('account_id') or (status.get('account') or {}).get('id')
    generation = status.get('generation')
    if not isinstance(account_id, str) or not account_id or not isinstance(generation, str) or not generation:
        raise AppError('membership_account_changed', '账号身份已变化，请重新连接后重试。', 403)
    return account_id, generation


def _decision_for(root, account_id):
    digest = hashlib.sha256(account_id.encode('utf-8')).hexdigest()
    manifest = _read_manifest(root)
    result = {'available': False, 'counts': {}, 'claim_status': 'unclaimed', 'decision': 'none',
              'decision_required': False, 'can_skip': False, 'can_claim': False, 'original_preserved': True}
    if manifest:
        if manifest['account_digest'] != digest:
            return {**result, 'claim_status': 'claimed_elsewhere', 'decision': 'unavailable'}
        prepared = manifest['status'] == 'prepared'
        return {**result, 'available': prepared, 'counts': manifest.get('counts', {}),
                'claim_status': manifest['status'], 'decision': 'resume_required' if prepared else 'claimed',
                'decision_required': prepared, 'can_claim': prepared}
    skipped = digest in _read_decisions(root)['accounts']
    if skipped:
        # Neither startup nor each subsequent page request should depend on the
        # database the member explicitly elected to leave behind staying valid.
        return {**result, 'decision': 'skipped'}
    try:
        summary = inspect_legacy(root)
    except AppError as exc:
        if exc.code not in ('legacy_unreadable', 'legacy_unsupported_schema', 'legacy_unsafe_path'):
            raise
        return {**result, 'available': True, 'decision': 'inspection_unavailable',
                'decision_required': True, 'can_skip': True,
                'inspection_error': {'code': exc.code, 'message': '旧资料暂时无法安全读取；可保留原资料并跳过，认领需先检查。'}}
    except OSError:
        return {**result, 'available': True, 'decision': 'inspection_unavailable',
                'decision_required': True, 'can_skip': True,
                'inspection_error': {'code': 'legacy_unreadable', 'message': '旧资料暂时无法安全读取；可保留原资料并跳过，认领需先检查。'}}
    available = summary['available']
    return {**result, 'available': available, 'counts': summary['counts'], 'can_skip': available, 'can_claim': available,
            'decision': 'skipped' if skipped else 'unclaimed' if available else 'none',
            'decision_required': available and not skipped}


def legacy_decision(runtime, *, expected_account=None, expected_generation=None):
    """Authenticated first-use summary; does not initialize an account Store."""
    account_id, generation = _verified_identity(runtime, expected_account, expected_generation)
    result = _decision_for(Path(runtime.root_dir).expanduser().resolve(), account_id)
    _verified_identity(runtime, account_id, generation)
    return result


def require_legacy_decision(runtime, account_id, generation, *, operation='use'):
    """Fence new work until old-data ownership was explicitly handled.

    `operation='read'` is for an already-authorized history-only route whose view
    persistence would otherwise make an empty account database unclaimable. It
    does not expose legacy counts and never permits a new source request.
    """
    current, current_generation = _verified_identity(runtime, account_id, generation, operation=operation)
    result = _decision_for(Path(runtime.root_dir).expanduser().resolve(), current)
    _verified_identity(runtime, current, current_generation, operation=operation)
    if result['decision_required']:
        raise _error('decision_required', '发现旧资料，请先选择认领或跳过，再开始新任务。')
    return None


def skip_legacy(runtime, confirmed=False, *, expected_account=None, expected_generation=None):
    """Persist an explicit account-scoped decision without touching either DB."""
    if confirmed is not True:
        raise _error('confirmation_required', '请明确确认跳过旧资料；原资料仍会保留。', 400)
    account_id, generation = _verified_identity(runtime, expected_account, expected_generation)
    root = Path(runtime.root_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _claim_lock(root):
        _verified_identity(runtime, account_id, generation)
        decision = _decision_for(root, account_id)
        if decision['decision_required'] and not decision['can_skip']:
            raise _error('claim_in_progress', '上次旧资料认领尚未完成，请继续认领以恢复，暂不能跳过。')
        if not decision['can_skip'] or decision['decision'] == 'skipped':
            return decision
        value = _read_decisions(root)
        digest = hashlib.sha256(account_id.encode('utf-8')).hexdigest()
        value['accounts'][digest] = {'decision': 'skipped', 'recorded_at': _utc()}
        _verified_identity(runtime, account_id, generation)
        _write_private_json(root, DECISIONS, value)
        _verified_identity(runtime, account_id, generation)
        return _decision_for(root, account_id)


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _check_backup(path, expected_hash=None):
    if not path.is_file() or path.is_symlink():
        raise _error('backup_unreadable', '认领备份缺失或不安全，未继续导入。')
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise _error('backup_changed', '认领备份出现额外的数据库日志，未继续导入。')
    digest = _sha256(path)
    if expected_hash is not None and digest != expected_hash:
        raise _error('backup_changed', '认领备份的校验值已变化，未继续导入。')
    # A hash of the main file must represent exactly the rows we will import;
    # immutable mode cannot silently overlay an un-hashed WAL/journal sidecar.
    with _read_only(path, immutable=True) as connection:
        _validate_schema(connection)
        if connection.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
            raise _error('backup_invalid', '认领备份完整性检查未通过，未导入。')
        counts = _counts(connection)
    if _sha256(path) != digest:
        raise _error('backup_changed', '认领备份在检查期间发生变化，未导入。')
    return digest, counts


def _backup(source, destination, require):
    if destination.exists() or destination.is_symlink():
        raise _error('backup_exists', '备份路径已存在，未覆盖。')
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with _read_only(source) as original:
            _validate_schema(original)
            target = sqlite3.connect(destination)
            try:
                original.backup(target, pages=128, progress=lambda *args: require(), sleep=.05)
                target.execute('PRAGMA journal_mode=DELETE')
            finally:
                target.close()
        with destination.open('rb') as handle:
            os.fsync(handle.fileno())
        return _check_backup(destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(destination) + suffix).unlink(missing_ok=True)
        raise


def _kv_allowed(key):
    return (key in ('active_mode', 'view:long', 'view:short', 'view:overseas', 'analysis_target')
            or key.startswith('analysis_target:') or key.startswith('translation_target:'))


def _empty_target(connection):
    if any(_counts(connection).values()):
        return False
    allowed_defaults = {
        'config': {'main_site': 'https://www.fagaochuangzuo.com/', 'points_site': ''},
        'active_mode': 'long', 'download_integrity_v1': True,
    }
    for row in connection.execute('SELECT key,body FROM kv'):
        if row['key'] not in allowed_defaults or json.loads(row['body']) != allowed_defaults[row['key']]:
            return False
    return True


def _interrupted_job(body):
    job = json.loads(body)
    if not isinstance(job, dict):
        raise ValueError('invalid job')
    if job.get('status') in ('pending', 'running', 'cancelling'):
        job.update(status='interrupted', message='旧资料已复制；此任务需要重新发起。',
                   finished_at=_utc(), waiting_for_more=False)
    if isinstance(job.get('public_source'), dict) and job['public_source'].get('status') == 'running':
        job['public_source'] = {**job['public_source'], 'status': 'interrupted', 'finished_at': _utc()}
    return json.dumps(job, ensure_ascii=False)


def _copy_data(source, target, require):
    for table, columns in TABLES.items():
        selected = ','.join('"' + column + '"' for column in columns)
        insert = 'INSERT INTO "' + table + '" (' + selected + ') VALUES (' + ','.join('?' for _ in columns) + ')'
        if table == 'kv':
            insert += ' ON CONFLICT(key) DO UPDATE SET body=excluded.body'
        cursor = source.execute('SELECT ' + selected + ' FROM "' + table + '"')
        while True:
            rows = cursor.fetchmany(128)
            if not rows:
                break
            require()
            records = []
            for row in rows:
                if table == 'kv' and not _kv_allowed(row['key']):
                    continue
                record = tuple(row)
                if table == 'jobs':
                    record = (row['id'], row['created_at'], _interrupted_job(row['body']))
                records.append(record)
            target.executemany(insert, records)


def _receipt(manifest, repeated=False):
    return {key: manifest[key] for key in ('version', 'claim_id', 'status', 'snapshot_at', 'counts',
            'source_backup', 'source_sha256', 'target_backup', 'target_sha256')} | {
        'repeated': repeated, 'original_preserved': True, 'later_legacy_writes_included': False,
        'excluded': ['browser_sessions', 'source_auth_state', 'network_configuration', 'source_configuration', 'unknown_settings'],
    }


def _target_path(root, context):
    digest = hashlib.sha256(context.account_id.encode('utf-8')).hexdigest()
    accounts = _safe_path(root, 'accounts')
    directory = _safe_path(accounts, digest)
    expected = _safe_path(directory, 'workbench.sqlite3')
    if Path(context.store.db).absolute() != expected:
        raise _error('unsafe_target', '账号资料目录不正确，未导入。')
    return expected, digest


def claim_legacy(runtime, confirmed=False, *, expected_account=None, expected_generation=None):
    """Copy frozen legacy data once, into the explicitly confirmed active account.

    A prepared root manifest reserves ownership before import. The transaction's
    own marker makes a crash after commit recoverable without importing twice.
    Backups are retained so a maintainer can restore either pre-import database;
    this function never replaces or rolls back an account database itself.
    """
    if confirmed is not True:
        raise _error('confirmation_required', '请先确认这些旧资料属于当前账号，再认领。', 400)
    context = runtime.capture(operation='claim_legacy', expected_account=expected_account,
                              expected_generation=expected_generation)
    root = Path(runtime.root_dir).expanduser().resolve()
    source = _safe_path(root, 'workbench.sqlite3')
    target_path, account_digest = _target_path(root, context)
    try:
        with _claim_lock(root), context.jobs.lock:
            context.require()
            if context.jobs.active:
                raise _error('jobs_active', '请先停止当前账号的任务，再认领旧资料。')
            manifest = _read_manifest(root)
            if manifest and manifest['account_digest'] != account_digest:
                raise _error('already_claimed', '这份旧资料已关联其他账号，未导入。')
            if not manifest:
                with _read_only(target_path) as existing:
                    _validate_schema(existing)
                    if not _empty_target(existing):
                        raise _error('target_not_empty', '当前账号已有资料，不能覆盖或自动合并旧资料。')
                if not source.is_file():
                    raise _error('missing', '没有可认领的旧资料。', 404)
                backups = _safe_path(root, 'legacy-claims')
                backups.mkdir(mode=0o700, exist_ok=True)
                os.chmod(backups, 0o700)
                claim_id = uuid.uuid4().hex
                source_backup = _safe_path(backups, claim_id + '-source.sqlite3')
                target_backup = _safe_path(backups, claim_id + '-target-before.sqlite3')
                source_hash, counts = _backup(source, source_backup, context.require)
                if not any(counts.values()):
                    raise _error('empty', '旧资料中没有可认领的内容。', 404)
                target_hash, _ = _backup(target_path, target_backup, context.require)
                manifest = {'version': 1, 'status': 'prepared', 'claim_id': claim_id,
                            'account_digest': account_digest, 'snapshot_at': _utc(), 'counts': counts,
                            'source_backup': str(source_backup.relative_to(root)), 'source_sha256': source_hash,
                            'target_backup': str(target_backup.relative_to(root)), 'target_sha256': target_hash}
                _write_manifest(root, manifest)
            expected_source = 'legacy-claims/' + manifest['claim_id'] + '-source.sqlite3'
            expected_target = 'legacy-claims/' + manifest['claim_id'] + '-target-before.sqlite3'
            if manifest.get('source_backup') != expected_source or manifest.get('target_backup') != expected_target:
                raise _error('receipt_unreadable', '旧资料认领回执路径不正确，未导入。')
            backups = _safe_path(root, 'legacy-claims')
            source_backup = _safe_path(backups, Path(expected_source).name)
            target_backup = _safe_path(backups, Path(expected_target).name)
            _check_backup(source_backup, manifest['source_sha256'])
            _check_backup(target_backup, manifest['target_sha256'])
            _target_path(root, context)
            target = sqlite3.connect(target_path, timeout=15)
            target.row_factory = sqlite3.Row
            repeated = False
            try:
                target.execute('PRAGMA trusted_schema=OFF')
                target.execute('BEGIN IMMEDIATE')
                _validate_schema(target)
                marker = target.execute('SELECT body FROM kv WHERE key=?', (MARKER,)).fetchone()
                if marker:
                    if json.loads(marker['body']).get('claim_id') != manifest['claim_id']:
                        raise _error('receipt_mismatch', '账号资料认领回执不一致，未修改。')
                    repeated = True
                else:
                    if manifest['status'] == 'completed':
                        raise _error('receipt_mismatch', '账号资料认领标记缺失，未重复导入。')
                    if not _empty_target(target):
                        raise _error('target_not_empty', '当前账号已有资料，不能覆盖或自动合并旧资料。')
                    with _read_only(source_backup, immutable=True) as frozen:
                        _copy_data(frozen, target, context.require)
                    # Recheck the bytes actually used before making the import
                    # durable; a backup edited after the earlier check cannot
                    # turn a successful hash readback into an unverified copy.
                    _check_backup(source_backup, manifest['source_sha256'])
                    # Empty Store initialization already sets this marker. Old
                    # records must still receive the existing integrity repair.
                    target.execute('DELETE FROM kv WHERE key=?', ('download_integrity_v1',))
                    context.store._repair_download_integrity(target)
                    target.execute('INSERT INTO kv(key,body) VALUES (?,?)',
                                   (MARKER, json.dumps({'claim_id': manifest['claim_id'], 'source_sha256': manifest['source_sha256']})))
                context.require()
                target.commit()
            except BaseException:
                target.rollback()
                raise
            finally:
                target.close()
            manifest = {**manifest, 'status': 'completed', 'completed_at': manifest.get('completed_at') or _utc()}
            _write_manifest(root, manifest)
            return _receipt(manifest, repeated)
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        raise _error('import_failed', '旧资料认领未完成；原资料与已生成备份仍保留，请重试或由维护助手检查。') from None
