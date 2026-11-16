# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""发糕扫榜工作台。"""
__version__ = '0.7.0-dev.20'

# Official installations validate before importing membership, storage or workers.
# Development/source directories keep their ordinary deterministic test workflow.
import atexit as _atexit
import hashlib as _hashlib
import json as _json
import sys as _sys
import types as _types
from pathlib import Path as _Path
_sys.dont_write_bytecode = True
_package_root = _Path(__file__).resolve().parents[1]
if _package_root.parent.name == 'releases':
    # Do not import a cached verifier before its contents have been checked.
    # The fixed official launcher additionally checks __init__ caches before
    # importing this package; plain Python -m remains a compatibility entry.
    try:
        _receipt_path = _package_root / '.fagao-release.json'
        _verifier_path = _package_root / 'fagao/integrity.py'
        if (_receipt_path.is_symlink() or _verifier_path.is_symlink()
                or _receipt_path.stat().st_size > 4 * 1024 * 1024
                or _verifier_path.stat().st_size > 1024 * 1024):
            raise ValueError('invalid verifier')
        _initial_receipt = _json.loads(_receipt_path.read_bytes())
        _verifier_source = _verifier_path.read_bytes()
        if _initial_receipt.get('files', {}).get('fagao/integrity.py') != _hashlib.sha256(_verifier_source).hexdigest():
            raise ValueError('verifier mismatch')
        _verifier = _types.ModuleType('fagao.integrity')
        _verifier.__file__ = str(_verifier_path)
        _verifier.__package__ = 'fagao'
        exec(compile(_verifier_source, str(_verifier_path), 'exec', dont_inherit=True), _verifier.__dict__)
        _sys.modules['fagao.integrity'] = _verifier
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        print(_json.dumps({'error':{'code':'installation_integrity_failed','reason':'invalid_integrity_verifier',
            'message':'安装完整性检查未完成，请让 WorkBuddy 用原安装说明修复；已有资料保留。',
            'repair_required':True,'data_preserved':True}}, ensure_ascii=True), file=_sys.stderr)
        raise SystemExit(1) from None
from .integrity import IntegrityError as _IntegrityError, ReleaseLease as _ReleaseLease, verify_installation as _verify_installation, read_receipt as _read_receipt
_release_lease = _ReleaseLease(_Path(__file__).resolve().parents[1])
try:
    _release_lease.__enter__()
    _verify_installation()
    _receipt = _read_receipt(_Path(__file__).resolve().parents[1])
    if _receipt is not None and 'code_seal' in _receipt:
        from .code_seal import CodeSealError as _CodeSealError, verify_seal as _verify_seal
        try:
            _verify_seal(_Path(__file__).resolve().parents[1], _receipt['code_seal'],
                         _receipt['original_files'], _receipt['code_seal']['data_root'], cache=True)
        except _CodeSealError as _seal_error:
            raise _IntegrityError(_seal_error.reason, diagnostic=_seal_error.diagnostic()) from None
except _IntegrityError as _error:
    _release_lease.__exit__(None, None, None)
    print(_json.dumps({'error': _error.diagnostic()}, ensure_ascii=True), file=_sys.stderr)
    raise SystemExit(1) from None
_atexit.register(_release_lease.__exit__, None, None, None)
