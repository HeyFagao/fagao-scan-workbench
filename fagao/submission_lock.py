# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Synchronize a local submission editor launch with a component upgrade."""
import os
from pathlib import Path
import stat
from contextlib import contextmanager


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


@contextmanager
def component_lock(parent: Path, *, shared: bool = False):
    lock = parent / ".submission-install.lock"
    if os.path.lexists(lock) and (is_link(lock) or not lock.is_file()):
        raise ValueError("组件安装锁无效")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)
