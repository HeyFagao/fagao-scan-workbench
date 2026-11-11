# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Independent, documented RST public snapshots for Royal Road Rising Stars.

This reads an existing third-party dataset, not Royal Road through a proxy.
One request returns at most 50 rows. Source capture time and source identity
remain attached to the result; missing author/synopsis data are never inferred.
API contract: https://rst.doomlabs.net/openapi.json
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
import time
import urllib.error
import urllib.request

from . import network
from .collectors import CollectorError, _cover_url, _notify, _notify_snapshot, _page_options


SOURCE_ID = "rst:doomlabs"
SOURCE_NAME = "RST 新星榜公开快照"
SOURCE_URL = "https://rst.doomlabs.net/"
API_URL = "https://rst.doomlabs.net/api/snapshots/latest"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 12


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectorError("RST 公开数据地址发生跳转，已停止读取。", "source_changed")


def _fetch_document():
    request = urllib.request.Request(API_URL, headers={
        "User-Agent": "FagaoWorkbench-RST/1.0", "Accept": "application/json",
        "Accept-Encoding": "identity",
    })
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with network.build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.geturl() != API_URL:
                raise CollectorError("RST 公开数据地址与请求不一致。", "source_changed")
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_BYTES:
                raise CollectorError("RST 公开数据超过大小上限。", "source_changed")
            chunks, size = [], 0
            read = getattr(response, "read1", response.read)
            while size <= MAX_BYTES:
                if time.monotonic() >= deadline:
                    raise CollectorError("RST 公开数据读取超时。", "network_error")
                chunk = read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > MAX_BYTES:
                raise CollectorError("RST 公开数据超过大小上限。", "source_changed")
        return json.loads(b"".join(chunks).decode("utf-8"))
    except CollectorError:
        raise
    except urllib.error.HTTPError as exc:
        state = "rate_limited" if exc.code == 429 else "network_error"
        raise CollectorError(f"RST 公开数据返回 HTTP {exc.code}。", state) from exc
    except network.NetworkError as exc:
        raise CollectorError(str(exc), "network_error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectorError("RST 公开数据暂时无法连接。", "network_error") from exc
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CollectorError("RST 返回的内容不是有效的 JSON 数据。", "source_changed") from exc


def _integer(value, minimum=0, maximum=10**15):
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _capture_time(value):
    if not isinstance(value, str) or len(value) > 60:
        raise CollectorError("RST 快照缺少明确的来源时间。", "source_changed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.year < 2023:
            raise ValueError("invalid timestamp")
        parsed = parsed.astimezone(timezone.utc)
        if parsed > datetime.now(timezone.utc) + timedelta(minutes=10):
            raise ValueError("future timestamp")
        return parsed
    except (ValueError, OverflowError) as exc:
        raise CollectorError("RST 快照的来源时间无效。", "source_changed") from exc


def normalize_rst_snapshot(document, options=None, *, reason=None, fetched_at=None):
    """Validate the complete returned feed before publishing any of its rows."""
    from .overseas import translated_tags

    options = options or {}
    max_pages, limit = _page_options(options, 50)
    if not isinstance(document, dict) or not isinstance(document.get("snapshot"), dict):
        raise CollectorError("RST 数据缺少快照信息。", "source_changed")
    meta = document["snapshot"]
    rows = document.get("rankings")
    if (not _integer(meta.get("id"), 1) or meta.get("source") not in ("rising_stars", "historical_import")
            or not isinstance(rows, list) or not 1 <= len(rows) <= 50
            or not _integer(document.get("total"), 1, 50) or document["total"] != len(rows)
            or not _integer(meta.get("total_items"), 1, 50) or meta["total_items"] != len(rows)):
        raise CollectorError("RST 快照数量或来源范围不一致。", "source_changed")
    captured = _capture_time(meta.get("captured_at"))
    source_date = captured.isoformat().replace("+00:00", "Z")
    books, ids, ranks = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise CollectorError("RST 书目字段无效。", "source_changed")
        ident, rank, title, url = (row.get(key) for key in ("fiction_id", "rank", "title", "url"))
        if (not isinstance(ident, str) or not re.fullmatch(r"[1-9]\d{0,14}", ident)
                or not _integer(rank, 1, 50) or ident in ids or rank in ranks
                or not isinstance(title, str) or not title.strip() or len(title) > 500
                or not isinstance(url, str) or len(url) > 2000
                or not re.fullmatch(r"https://www\.royalroad\.com/fiction/" + ident + r"(?:/[^?#\s]*)?", url)):
            raise CollectorError("RST 作品标识、名次或原文地址不一致。", "source_changed")
        tags = row.get("tags") or []
        if not isinstance(tags, list) or len(tags) > 100 or any(not isinstance(tag, str) or len(tag) > 100 for tag in tags):
            raise CollectorError("RST 作品标签格式无效。", "source_changed")
        metrics = []
        for key, name, unit in (("followers", "关注", "人"), ("views", "浏览", "次")):
            value = row.get(key)
            if value is None:
                continue
            if not _integer(value):
                raise CollectorError("RST 作品指标格式无效。", "source_changed")
            metrics.append({"name": name, "value": value, "unit": unit})
        books.append({"id": ident, "title": title.strip(), "url": url,
                      "author": "", "summary": "", "status": "",
                      "cover_url": _cover_url(row.get("cover_url")),
                      "rank": rank, "source_position": rank, "rank_semantics": "original_rank",
                      "tags": translated_tags(tags), "source_tags": list(tags), "metrics": metrics})
        ids.add(ident)
        ranks.add(rank)
    # A truncated or inconsistent list must not look like an intact top-N list.
    if ranks != set(range(1, len(books) + 1)):
        raise CollectorError("RST 快照名次缺失，已停止拼接。", "source_changed")
    books.sort(key=lambda book: book["rank"])
    warnings = ["正在显示 RST 保存的新星榜快照；名次、关注和浏览对应下方来源时间。",
                "RST 榜单接口未提供作者和简介，当前书目只含实际取得的封面、英文书名、标签与指标。"]
    if reason:
        warnings.insert(0, f"Royal Road 官网读取失败：{_reason_text(reason)} 已改用独立的 RST 公开快照。")
    if datetime.now(timezone.utc) - captured > timedelta(days=1):
        warnings.append("RST 最新快照已超过 24 小时，请按显示的来源时间使用。")
    if len(books) < 50:
        warnings.append(f"RST 此次仅提供 {len(books)} 本，尚不足完整 Top 50。")
    truncated = len(books) > limit
    selected = books[:limit]
    revision = f"rst-snapshot:{meta['id']}"
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    return {"platform": "royalroad", "mode": "overseas", "channel": "全部", "category": "全部",
            "board": "新星榜", "period": "当前", "categories": ["全部"],
            "source_id": SOURCE_ID, "source_url": SOURCE_URL, "source_revision": revision,
            "data_date": source_date, "collected_at": fetched_at, "books": selected, "warnings": warnings,
            "requested_count": limit, "available_count": len(selected), "source_total": len(books),
            "complete": len(books) == 50 or truncated,
            "pagination": {"pages_fetched": 1, "max_pages": max_pages, "page_size": 50,
                           "stop_reason": "requested_limit" if truncated else "source_end" if len(books) == 50 else "source_incomplete"},
            "provenance": {"source_id": SOURCE_ID, "source_name": SOURCE_NAME, "source_url": SOURCE_URL,
                           "source_revision": revision, "source_date": source_date, "fetched_at": fetched_at,
                           "scope": "RST 保存的 Royal Road 新星榜 Top 50；按来源捕获时间展示，作者和简介未提供",
                           "rank_semantics": "rst_recorded_rank", "metric_precision": "source_integer",
                           "sample_limit": 50}}


def _reason_text(reason):
    # Collector errors contain safe diagnostics; bound them before persisting
    # them in a user-visible snapshot or composing two source failures.
    return " ".join(str(reason).split())[:240]


def collect_royalroad_fallback(options=None, progress=None, reason=None):
    _notify(progress, "正在读取 RST 新星榜公开快照…")
    try:
        result = normalize_rst_snapshot(_fetch_document(), options, reason=reason)
    except CollectorError as error:
        if not reason:
            raise
        raise CollectorError(
            f"Royal Road 官网读取失败：{_reason_text(reason)} "
            f"备用 RST 公开快照也未取得：{_reason_text(error)} 已有榜单仍保留。",
            error.state) from error
    _notify_snapshot(progress, result, False)
    return result
