# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
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
