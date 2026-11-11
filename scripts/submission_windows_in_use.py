# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Query live use of the exact Windows submission component files before replacement."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def _check(code: int) -> None:
    if code:
        # Restart Manager returns a Win32 status directly, independent of GetLastError.
        raise ctypes.WinError(code)


def _resources_in_use(api, paths: list[str]) -> bool:
    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)  # CCH_RM_SESSION_KEY + 1
    _check(api.RmStartSession(ctypes.byref(handle), 0, key))
    try:
        names = (wintypes.LPCWSTR * len(paths))(*paths)
        _check(api.RmRegisterResources(handle, len(paths), names, 0, None, 0, None))
        needed = wintypes.UINT()
        supplied = wintypes.UINT()
        reboot_reasons = wintypes.DWORD()
        # With no output array, ERROR_MORE_DATA already proves resources are in use.
        # A process list and any user-specific process details are unnecessary.
        code = api.RmGetList(handle, ctypes.byref(needed), ctypes.byref(supplied), None,
                             ctypes.byref(reboot_reasons))
        if code == 234:  # ERROR_MORE_DATA
            return True
        _check(code)
        return bool(needed.value or supplied.value or reboot_reasons.value)
    finally:
        _check(api.RmEndSession(handle))


def component_in_use(executable: Path) -> bool:
    """Ask Windows Restart Manager about the installed program and its application archive."""
    if os.name != "nt":
        raise OSError("Windows Restart Manager requires Windows")
    exe = executable.resolve(strict=True)
    asar = (exe.parent / "resources" / "app.asar").resolve(strict=True)
    # LOAD_LIBRARY_SEARCH_SYSTEM32 is sufficient for this system component.
    api = ctypes.WinDLL("rstrtmgr.dll", winmode=0x800)
    dword, uint, ptr = wintypes.DWORD, wintypes.UINT, ctypes.POINTER
    api.RmStartSession.argtypes = (ptr(dword), dword, wintypes.LPWSTR)
    api.RmRegisterResources.argtypes = (dword, uint, ptr(wintypes.LPCWSTR),
                                        uint, ctypes.c_void_p, uint, ptr(wintypes.LPCWSTR))
    api.RmGetList.argtypes = (dword, ptr(uint), ptr(uint), ctypes.c_void_p, ptr(dword))
    api.RmEndSession.argtypes = (dword,)
    for fn in (api.RmStartSession, api.RmRegisterResources, api.RmGetList, api.RmEndSession):
        fn.restype = dword
    return _resources_in_use(api, [str(exe), str(asar)])
