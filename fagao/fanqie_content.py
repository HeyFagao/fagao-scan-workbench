"""Read normal public Fanqie chapters and retain the exact downloaded scope.

The user-supplied toolkit demonstrated the public directory/reader SSR path.
This adapter keeps the workbench's identity, permission and completeness checks.
It never logs in, purchases chapters or uses a separate decryption service.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .fanqie_codec import decode_text, has_unmapped_pua

BASE = "https://fanqienovel.com"
CATALOG_TTL = 30 * 60
CHAPTER_TTL = 6 * 60 * 60
REQUEST_INTERVAL = 0.35
MAX_CHAPTERS = 5000
MAX_TEXT_CHARACTERS = 8_000_000
INTEGRITY_VERSION = 1
_ID = re.compile(r"\d{5,30}\Z")
_RESTRICTED = ("needPay", "isChapterLock", "isPaidPublication", "isPaidStory")


def _error(message, state="unavailable"):
    # Delayed imports allow collectors.fetch_text to delegate to this module.
    from .collectors import CollectorError
    return CollectorError(message, state)


def _identity(value, kind="作品"):
    value = str(value)
    if not _ID.fullmatch(value):
        raise _error(f"无效的番茄{kind} ID。")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _flag(value):
    return value is True or (isinstance(value, (int, float)) and value != 0) or str(value).lower() in ("true", "1", "yes")


def _restricted(item):
    return any(_flag(item.get(key)) for key in _RESTRICTED)


def _fetch(url):
    from .collectors import _fetch as fetch
    # A minimum gap also applies when callers download many cached-directory chapters.
    time.sleep(REQUEST_INTERVAL)
    return fetch(url)


def _state(html):
    from .collectors import _state as parse
    return parse(html)


def _cache_path(data_dir, kind, key):
    return Path(data_dir) / "cache" / "fanqie-content" / kind / (key + ".json") if data_dir else None


def _cache_read(data_dir, kind, key, ttl):
    path = _cache_path(data_dir, kind, key)
    if path is None:
        return None
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if 0 <= time.time() - float(record["saved_at"]) < ttl and isinstance(record.get("value"), dict):
            return record["value"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def _cache_write(data_dir, kind, key, value):
    path = _cache_path(data_dir, kind, key)
    if path is None:
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"saved_at": time.time(), "value": value}, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        # A read-only/full cache must not turn an otherwise valid chapter into a failure.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _readable(value):
    value = decode_text(unescape(str(value or ""))).strip()
    return "" if has_unmapped_pua(value) else value


def _word_count(value):
    """Only exact positive source counts are evidence; display units are not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        return int(value) if int(value) > 0 else None
    return None


def _content_integrity(text, declared):
    # Count only body characters. Formatting, title and inserted newlines must
    # never fill a shortfall. Source counts are retained rather than rounded.
    actual = sum(not char.isspace() for char in text)
    declared = _word_count(declared)
    if declared is None:
        status, reason = "unverified", "来源未提供可核对的章节字数，全文完整性未确认"
    elif actual < declared:
        status, reason = "partial", f"来源标注 {declared} 字，实际取得 {actual} 个非空白字符；保存为公开片段"
    else:
        status, reason = "verified", "已核对章节身份、访问状态及来源字数"
    return {"version": INTEGRITY_VERSION, "status": status,
            "declared_word_count": declared, "actual_characters": actual, "reason": reason}


def catalog(book_id, data_dir=None):
    """Return the source directory in source order, using one-based positions."""
    book_id = _identity(book_id)
    cached = _cache_read(data_dir, "catalogs", book_id, CATALOG_TTL)
    if (cached and cached.get("book_id") == book_id and isinstance(cached.get("chapters"), list)
            and cached.get("integrity_version") == INTEGRITY_VERSION):
        return cached
    source = f"{BASE}/page/{book_id}"
    initial = _state(_fetch(source))
    page = initial.get("page", {}) if isinstance(initial, dict) else {}
    if not isinstance(page, dict) or str(page.get("bookId")) != book_id:
        raise _error("作品详情身份不匹配，未读取目录。")
    try:
        response = json.loads(_fetch(f"{BASE}/api/reader/directory/detail?bookId={book_id}"))
    except (ValueError, TypeError) as exc:
        raise _error("目录接口未返回有效 JSON，已有正文仍保留。") from exc
    if not isinstance(response, dict) or response.get("code") != 0 or not isinstance(response.get("data"), dict):
        raise _error("公开目录暂时不可用，已有正文仍保留。")
    data = response["data"]
    groups = data.get("chapterListWithVolume")
    if groups is not None and not isinstance(groups, list):
        raise _error("公开目录格式发生变化，未开始下载。")
    raw = [item for group in groups or [] if isinstance(group, list)
           for item in group if isinstance(item, dict)]
    lookup = {}
    warnings = []
    for item in raw:
        item_id = str(item.get("itemId") or "")
        if not _ID.fullmatch(item_id):
            warnings.append("目录含无效章节 ID，完整性未确认。")
            continue
        if item_id in lookup:
            warnings.append("目录返回重复章节，已按来源 ID 去重。")
            continue
        lookup[item_id] = item
    all_ids = data.get("allItemIds")
    if all_ids is not None and not isinstance(all_ids, list):
        raise _error("公开目录的章节 ID 列表格式发生变化，未开始下载。")
    if isinstance(all_ids, list) and all_ids:
        ordered_ids = [str(value) for value in all_ids]
        if any(not _ID.fullmatch(value) for value in ordered_ids) or len(set(ordered_ids)) != len(ordered_ids):
            raise _error("公开目录的章节 ID 缺失或重复，未开始下载。")
        if set(lookup) - set(ordered_ids):
            raise _error("公开目录两个来源的章节身份不一致，未开始下载。")
    else:
        ordered_ids = list(lookup)
    if not ordered_ids:
        raise _error("公开目录没有可读取章节。")
    if len(ordered_ids) > MAX_CHAPTERS:
        raise _error(f"公开目录超过 {MAX_CHAPTERS} 章，请按卷导入或缩小来源范围。")
    chapters = []
    declared_book_words = _word_count(page.get("wordNumber"))
    for index, item_id in enumerate(ordered_ids, 1):
        item = lookup.get(item_id, {})
        chapters.append({"item_id": item_id, "index": index,
                         "title": _readable(item.get("title")) or f"目录第 {index} 项",
                         "volume": _readable(item.get("volume_name")),
                         "declared_word_count": _word_count(item.get("chapterWordNumber")) or
                             (declared_book_words if len(ordered_ids) == 1 else None),
                         "need_pay": _flag(item.get("needPay")), "is_locked": _flag(item.get("isChapterLock")),
                         "restricted": _restricted(item), "url": f"{BASE}/reader/{item_id}"})
    result = {"platform": "fanqie", "book_id": book_id, "title": _readable(page.get("bookName")) or f"作品 {book_id}",
              "source": source, "source_url": source, "status": {"0": "已完结", "1": "连载中"}.get(str(page.get("creationStatus")), ""),
              "catalog_chapters": len(chapters), "catalog_complete": isinstance(all_ids, list) and bool(all_ids) and not warnings,
              "chapters": chapters, "warnings": warnings, "collected_at": _now(),
              "declared_book_word_count": declared_book_words, "integrity_version": INTEGRITY_VERSION}
    _cache_write(data_dir, "catalogs", book_id, result)
    return result


class _ChapterText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "br", "div") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("p", "div") and not self.hidden:
            self.parts.append("\n")

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def _plain_content(raw):
    parser = _ChapterText()
    parser.feed(str(raw or ""))
    parser.close()
    text = decode_text("".join(parser.parts)).replace("\u00a0", " ")
    if has_unmapped_pua(text):
        raise _error("章节仍含未识别的字体字符，未将乱码保存为正文。")
    text = "\n\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) < 30:
        raise _error("章节正文为空或过短，未确认取得完整章节。")
    if re.search(r"请完成安全验证|访问过于频繁|登录后阅读|购买本章|Access Denied|403 Forbidden", text[:500], re.I):
        raise _error("章节返回验证或权限提示，已停止读取。")
    return text


def _chapter(book_id, item, data_dir):
    item_id = item["item_id"]
    if item.get("restricted"):
        raise _error("此章需要相应正文权限，已停止读取。", "forbidden")
    cached = _cache_read(data_dir, "chapters", book_id + "-" + item_id, CHAPTER_TTL)
    if cached and cached.get("book_id") == book_id and cached.get("item_id") == item_id:
        text = cached.get("text")
        integrity = cached.get("integrity", {})
        if (isinstance(text, str) and len(text) >= 30 and not has_unmapped_pua(text)
                and hashlib.sha256(text.encode()).hexdigest() == cached.get("sha256")
                and isinstance(integrity, dict) and integrity.get("version") == INTEGRITY_VERSION):
            # Recompute even current cache claims; old entries without source
            # counts are fetched again rather than inheriting a false full flag.
            cached["integrity"] = _content_integrity(text, integrity.get("declared_word_count"))
            return cached
    initial = _state(_fetch(item["url"]))
    reader = initial.get("reader") if isinstance(initial, dict) else None
    chapter = reader.get("chapterData") if isinstance(reader, dict) else None
    if not isinstance(chapter, dict) or str(chapter.get("bookId")) != book_id or str(chapter.get("itemId")) != item_id:
        raise _error("章节与固定作品身份不匹配，未保存该章。")
    if _restricted(chapter):
        raise _error("此章需要相应正文权限，已停止读取。", "forbidden")
    text = _plain_content(chapter.get("content"))
    declared = _word_count(chapter.get("chapterWordNumber")) or item.get("declared_word_count")
    result = {"book_id": book_id, "item_id": item_id, "title": _readable(chapter.get("title")) or item["title"],
              "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
              "integrity": _content_integrity(text, declared)}
    _cache_write(data_dir, "chapters", book_id + "-" + item_id, result)
    return result


def fetch_book_text(book_id, data_dir=None, max_chapters=3, start_chapter=1, progress=None):
    """Download a requested range; ``None`` requests the current full public directory.

    Cancellation raised by progress as ``code=cancelled`` returns obtained text,
    so a caller can save it with its true range instead of discarding useful work.
    ``complete`` describes a finished whole work; ``coverage_complete`` describes
    completion of the requested range, which may be only the first three chapters.
    """
    book_id = _identity(book_id)
    if isinstance(start_chapter, bool) or not isinstance(start_chapter, int) or not 1 <= start_chapter <= MAX_CHAPTERS:
        raise _error("起始目录序号必须是 1 到 5000 的整数。")
    if max_chapters is not None and (isinstance(max_chapters, bool) or not isinstance(max_chapters, int) or not 1 <= max_chapters <= MAX_CHAPTERS):
        raise _error("下载章节数必须是 1 到 5000 的整数。")
    cancelled = False

    def notify(message):
        nonlocal cancelled
        if progress:
            try:
                progress(message)
            except Exception as exc:
                if getattr(exc, "code", None) == "cancelled" or getattr(exc, "state", None) == "cancelled":
                    cancelled = True
                    return False
                raise
        return True

    initial = notify("正在读取这本书的公开目录…")
    if not initial:
        return {"platform": "fanqie", "book_id": book_id, "title": "", "text": "", "scope": "尚未取得正文", "complete": False,
                "coverage_complete": False, "cancelled": True, "chapters": [], "missing": [], "catalog_chapters": 0,
                "requested_chapters": max_chapters, "source": f"{BASE}/page/{book_id}", "collected_at": _now(), "status": "",
                "integrity": {"version": INTEGRITY_VERSION, "status": "unverified", "declared_book_word_count": None,
                              "actual_characters": 0, "reason": "尚未取得正文"}}
    info = catalog(book_id, data_dir)
    if start_chapter > info["catalog_chapters"]:
        raise _error("起始目录序号超过当前作品目录。")
    wanted = info["chapters"][start_chapter - 1:]
    if max_chapters is not None:
        wanted = wanted[:max_chapters]
    chapters, texts, missing = [], [], []
    total_characters = 0
    for item in wanted:
        if not notify(f"正在下载第 {item['index']} 章，已取得 {len(chapters)}/{len(wanted)} 章…"):
            break
        try:
            result = _chapter(book_id, item, data_dir)
            piece = result["title"] + "\n\n" + result["text"]
            if total_characters + len(piece) + 2 > MAX_TEXT_CHARACTERS:
                raise _error("本次文本达到 800 万字符上限，请分段下载。")
            total_characters += len(piece) + 2
            texts.append(piece)
            chapters.append({"id": item["item_id"], "item_id": item["item_id"], "index": item["index"],
                             "title": result["title"], "url": item["url"], "characters": len(result["text"]),
                             "integrity": result["integrity"]})
            if result["integrity"]["status"] == "partial":
                break
        except Exception as exc:
            from .collectors import CollectorError
            if not isinstance(exc, CollectorError):
                raise
            missing.append({"index": item["index"], "id": item["item_id"], "chapter": item["title"], "reason": str(exc)})
            break
    if not texts and not cancelled:
        raise _error(missing[0]["reason"] if missing else "当前没有取得可读正文。")
    whole_directory = start_chapter == 1 and len(chapters) == info["catalog_chapters"] and info["catalog_complete"]
    actual = sum(chapter["integrity"]["actual_characters"] for chapter in chapters)
    problem = next((c["integrity"] for c in chapters if c["integrity"]["status"] == "partial"), None)
    if problem is None:
        problem = next((c["integrity"] for c in chapters if c["integrity"]["status"] != "verified"), None)
    declared_book = info.get("declared_book_word_count")
    if whole_directory and declared_book and actual < declared_book:
        problem = {"status": "partial", "reason": f"全书标注 {declared_book} 字，实际取得 {actual} 个非空白字符；保存为公开片段"}
    integrity = {"version": INTEGRITY_VERSION, "status": problem["status"] if problem else ("verified" if chapters else "unverified"),
                 "declared_book_word_count": declared_book, "actual_characters": actual,
                 "reason": problem["reason"] if problem else ("所列章节已逐章核对来源字数" if chapters else "尚未取得正文")}
    coverage_complete = len(chapters) == len(wanted) and not missing and not cancelled and integrity["status"] == "verified"
    complete = coverage_complete and whole_directory and info["status"] == "已完结"
    if chapters:
        scope = f"公开目录第 {chapters[0]['index']}—{chapters[-1]['index']} 项，共 {len(chapters)} 章（当前目录 {info['catalog_chapters']} 章）"
        if integrity["status"] == "partial":
            scope = scope.replace(f"共 {len(chapters)} 章", f"片段涉及 {len(chapters)} 章")
            scope += "；公开片段，全文未取得"
        elif integrity["status"] == "unverified":
            scope += "；全文完整性未确认"
        elif whole_directory and info["status"] == "连载中":
            scope += "；已取得截至本次目录的已发布章节，作品仍连载"
    else:
        scope = "尚未取得正文"
    if cancelled:
        scope += "；下载已取消"
    elif missing:
        scope += "；后续章节未取得"
    if not cancelled:
        notify(integrity["reason"] if integrity["status"] != "verified" else f"本次已取得 {len(chapters)}/{len(wanted)} 章正文。")
        if cancelled:
            coverage_complete = complete = False
            scope += "；下载已取消"
    return {"platform": "fanqie", "book_id": book_id, "title": info["title"], "text": "\n\n".join(texts),
            "scope": scope, "complete": complete,
            "completeness_basis": "来源标为已完结，完整目录逐章核对字数，且无内容缺口" if complete else integrity["reason"],
            "integrity": integrity,
            "source": info["source"], "chapters": chapters, "missing": missing,
            "catalog_chapters": info["catalog_chapters"], "catalog_complete": info["catalog_complete"],
            "requested_chapters": len(wanted), "requested_start": start_chapter, "coverage_complete": coverage_complete,
            "cancelled": cancelled, "collected_at": _now(), "status": info["status"]}
