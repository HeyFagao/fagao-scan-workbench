# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""The device's offline submission drafts, independent of ranking accounts."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import sqlite3
import stat
from uuid import uuid4

from .store import AppError, now


LIMITS = {"work_title": 300, "pen_name": 100, "subject": 500, "body": 200_000}
APPEARANCES = {"system", "light", "eye-care", "dark"}
SUMMARY = "id, work_title, pen_name, subject, revision, updated_at"
FIELDS = SUMMARY + ", body"


def invalid(message="投稿草稿格式不正确。"):
    return AppError("submission_invalid", message, 400)


def _identity(data):
    draft_id = data.get("id")
    if not isinstance(draft_id, str) or not re.fullmatch(r"[0-9a-f]{32}", draft_id):
        raise invalid("草稿编号不正确。")
    return draft_id


def _revision(data):
    revision = data.get("revision")
    if type(revision) is not int or not 1 <= revision < 2**63 - 1:
        raise invalid("请重新打开这份草稿后再试。")
    return revision


def _contents(data):
    fields = {}
    for name, limit in LIMITS.items():
        value = data.get(name)
        if not isinstance(value, str):
            raise invalid()
        # Use the UTF-16 limits used by browser maxlength and the native editor.
        try:
            units = len(value.encode("utf-16-le")) // 2
        except UnicodeEncodeError as exc:
            raise invalid("草稿包含无法保存的字符。") from exc
        if units > limit:
            raise invalid("草稿文字超出长度限制。")
        forbidden = r"[\x00-\x1f\x7f]" if name != "body" else r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
        if re.search(forbidden, value):
            raise invalid("草稿包含不支持的控制字符。")
        fields[name] = value if name == "body" else value.strip()
    if not any(value.strip() for value in fields.values()):
        raise invalid("先写点内容，再保存草稿。")
    return fields


def _private_entry(info, *, directory=False):
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (correct_type and not reparse and
            (os.name != "posix" or (info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0)))


class SubmissionDrafts:
    def __init__(self, root):
        self.directory = Path(root) / "local-submissions"

    @contextmanager
    def _db(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = self.directory.lstat()
        if not _private_entry(folder, directory=True):
            raise AppError("submission_storage", "请检查本机草稿资料夹的访问权限。", 503)
        path = self.directory / "drafts.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not _private_entry(os.fstat(fd)) or not _private_entry(path.lstat()):
                raise AppError("submission_storage", "草稿资料文件不可用。", 503)
        finally:
            os.close(fd)
        db = sqlite3.connect(path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, work_title TEXT NOT NULL, pen_name TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                yield db
        finally:
            db.close()

    def summaries(self):
        with self._db() as db:
            return [dict(row) for row in db.execute(
                f"SELECT {SUMMARY} FROM drafts ORDER BY updated_at DESC, id"
            )]

    @staticmethod
    def _read(db, draft_id):
        row = db.execute(f"SELECT {FIELDS} FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None:
            raise AppError("submission_not_found", "这份草稿已删除。", 404)
        return dict(row)

    @staticmethod
    def _conflict():
        return AppError("submission_conflict", "草稿已在其他窗口更改。请重新打开后再试。", 409)

    def read(self, data):
        draft_id = _identity(data)
        with self._db() as db:
            row = self._read(db, draft_id)
            if "revision" in data and row["revision"] != _revision(data):
                raise self._conflict()
            return row

    def save(self, data):
        editing = "id" in data or "revision" in data
        if data.keys() != (set(LIMITS) | ({"id", "revision"} if editing else set())):
            raise invalid()
        contents = _contents(data)
        draft_id = _identity(data) if editing else uuid4().hex
        values = tuple(contents[key] for key in LIMITS)
        with self._db() as db:
            if editing:
                changed = db.execute("""UPDATE drafts SET work_title=?,pen_name=?,subject=?,
                    body=?,updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                    (*values, now(), draft_id, _revision(data)))
                if not changed.rowcount:
                    self._read(db, draft_id)
                    raise self._conflict()
            else:
                db.execute("""INSERT INTO drafts (work_title,pen_name,subject,body,
                    updated_at,id,revision) VALUES (?,?,?,?,?,?,1)""", (*values, now(), draft_id))
            return self._read(db, draft_id)

    def delete(self, data):
        if data.keys() != {"id", "revision"}:
            raise invalid()
        draft_id = _identity(data)
        with self._db() as db:
            changed = db.execute("DELETE FROM drafts WHERE id=? AND revision=?",
                                 (draft_id, _revision(data)))
            if not changed.rowcount:
                self._read(db, draft_id)
                raise self._conflict()

    def handoff(self, data):
        from .submission_component import open_component
        if set(data) not in ({"appearance"}, {"appearance", "id", "revision"}):
            raise invalid()
        appearance = data["appearance"]
        if not isinstance(appearance, str) or appearance not in APPEARANCES:
            raise invalid("外观设置无效。")
        # Only hand over the saved revision the operator selected.
        draft = self.read(data) if "id" in data else None
        return open_component(self.directory.parent, appearance, draft)
