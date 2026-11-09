"""Read the documented public FanqieRankTracker JSON feed, without side effects.

This is an independently written adapter, not an upstream scraper. Only the
two new-book feeds are allowed. Source dates and displayed precision survive
normalization; list positions are deliberately not labelled official ranks.
"""
from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from . import network

from .collectors import CollectorError
from .store import safe_cover_url


SOURCE_ID = "github:scoful/FanqieRankTracker"
SOURCE_NAME = "FanqieRankTracker 公开新书榜样本"
REPO_URL = "https://github.com/scoful/FanqieRankTracker"
RAW_ROOT = "https://raw.githubusercontent.com/scoful/FanqieRankTracker/main/"
CHANNELS = {"female": "女频", "male": "男频"}
SAMPLE_LIMIT = 20
MAX_BYTES = 3 * 1024 * 1024
TIMEOUT_SECONDS = 12
MAX_WORKERS = 4
_PATH = re.compile(r"(?:api/(?:female|male)/new/index\.json|data/(?:female|male)/new/\d{8}\.json)\Z")
_BOOK_URL = re.compile(r"https://fanqienovel\.com/page/(\d{5,30})/?\Z")
_READS = re.compile(r"(?:\d+(?:\.\d+)?)(?:万|亿)?(?:人(?:在读)?)?\Z")


def _now():
    return datetime.now(timezone.utc).isoformat()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectorError("公开数据源发生跳转，已停止读取。", "source_changed")


def _fetch_json(path):
    """Fetch a fixed feed path; never follow URLs supplied inside its JSON."""
    network.check_authorization()
    if not isinstance(path, str) or not _PATH.fullmatch(path):
        raise CollectorError("不支持的公开数据路径。", "invalid_source")
    url = RAW_ROOT + path
    request = urllib.request.Request(url, headers={
        "User-Agent": "FagaoWorkbench-PublicRankings/1.0",
        "Accept": "application/json", "Accept-Encoding": "identity",
    })
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        opener = network.build_opener(_NoRedirect())
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.geturl() != url:
                raise CollectorError("公开数据源地址与请求不一致。", "source_changed")
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise CollectorError("公开榜单响应超过大小上限。", "invalid_source")
            chunks, size = [], 0
            read = getattr(response, "read1", response.read)
            while size <= MAX_BYTES:
                if time.monotonic() >= deadline:
                    raise CollectorError("公开榜单读取超时，已有结果仍保留。", "network_error")
                chunk = read(min(65536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            content = b"".join(chunks)
        if len(content) > MAX_BYTES:
            raise CollectorError("公开榜单响应超过大小上限。", "invalid_source")
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("root is not an object")
    except CollectorError:
        raise
    except urllib.error.HTTPError as exc:
        state = "not_found" if exc.code == 404 else "network_error"
        raise CollectorError(f"公开榜单返回 HTTP {exc.code}，已有结果仍保留。", state) from exc
    except network.NetworkError as exc:
        raise CollectorError(str(exc), "network_error") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CollectorError("公开榜单暂时无法连接，已有结果仍保留。", "network_error") from exc
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CollectorError("公开榜单不是有效的 JSON 对象。", "source_changed") from exc
    # Git's blob object ID identifies the exact downloaded bytes. This is not
    # a commit ID, signature, authenticity claim or hash of reserialized JSON.
    blob = hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()
    return {"data": payload, "url": url, "fetched_at": _now(),
            "source_revision": "git-blob-sha1:" + blob, "bytes": len(content)}


def _source_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise CollectorError("公开榜单缺少明确来源日期。", "source_changed")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CollectorError("公开榜单来源日期无效。", "source_changed") from exc
    if parsed.year < 2020 or parsed > datetime.now(timezone.utc).date() + timedelta(days=1):
        raise CollectorError("公开榜单来源日期超出可接受范围。", "source_changed")
    return parsed


def _positive_int(value, maximum):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum


def _index(document, channel):
    if (document.get("channel") != channel or document.get("board") != "new"
            or not isinstance(document.get("types"), list)):
        raise CollectorError("公开索引频道或榜型发生变化。", "source_changed")
    source_date = _source_date(document.get("date"))
    all_rows = [entry for entry in document["types"] if isinstance(entry, dict) and entry.get("type") == "all"]
    if len(all_rows) != 1:
        raise CollectorError("公开榜单索引缺少唯一的样本覆盖记录。", "source_changed")
    row = all_rows[0]
    count, books = row.get("category_count"), row.get("book_count")
    if not _positive_int(count, 64) or not _positive_int(books, count * SAMPLE_LIMIT):
        raise CollectorError("公开榜单索引样本数量无效或超过 Top20 范围。", "source_changed")
    return source_date, count, books


def _text(value, maximum, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise CollectorError("公开榜单文本字段无效。", "source_changed")
    return value.strip()


def normalize_snapshot(document, channel, expected_date, *, source_url,
                       source_revision, fetched_at, expected_categories=None,
                       expected_entries=None):
    """Normalize one dated channel file into separate immutable category scopes.

    Return only actual records. No title-based identity fallback, no estimated
    word counts, no zero-filled history and no imported AI summaries.
    """
    if channel not in CHANNELS or not isinstance(document, dict):
        raise CollectorError("不支持的公开榜单频道。", "source_changed")
    source_date = _source_date(document.get("date")).isoformat()
    if source_date != expected_date or document.get("channel") != channel or document.get("board") != "new":
        raise CollectorError("公开快照的日期、频道或榜型与请求不一致。", "source_changed")
    categories = document.get("categories")
    if not isinstance(categories, list) or not 1 <= len(categories) <= 64:
        raise CollectorError("公开快照分类结构无效。", "source_changed")
    if expected_categories is not None and len(categories) != expected_categories:
        raise CollectorError("公开快照与索引的分类数量不一致。", "source_changed")
    snapshots, category_names, raw_entries = [], set(), 0
    for category in categories:
        if not isinstance(category, dict):
            raise CollectorError("公开快照分类记录无效。", "source_changed")
        name = _text(category.get("name"), 100, required=True)
        if name in category_names:
            raise CollectorError("公开快照出现重复分类。", "source_changed")
        category_names.add(name)
        rows = category.get("books")
        if not isinstance(rows, list) or not 1 <= len(rows) <= SAMPLE_LIMIT:
            raise CollectorError("公开分类样本为空或超过 Top20 范围。", "source_changed")
        raw_entries += len(rows)
        books, ids, warnings = [], {}, []
        for position, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                raise CollectorError("公开书目记录无效。", "source_changed")
            url = _text(row.get("url"), 200, required=True)
            match = _BOOK_URL.fullmatch(url)
            if not match:
                raise CollectorError("公开书目缺少可核对的番茄作品 ID。", "source_changed")
            book_id = match.group(1)
            title = _text(row.get("title"), 500, required=True)
            author = _text(row.get("author"), 500)
            summary = _text(row.get("intro"), 20000)
            reads = _text(row.get("reads"), 40)
            if reads and not _READS.fullmatch(reads):
                raise CollectorError("公开书目的在读字段格式发生变化。", "source_changed")
            metrics = [{"name": "在读", "value": reads, "unit": "人"}] if reads else []
            book = {"id": book_id, "title": title, "author": author,
                    "summary": summary, "url": "https://fanqienovel.com/page/" + book_id,
                    "cover_url": safe_cover_url(row.get("cover")), "status": "",
                    "rank": None, "source_position": position,
                    "rank_semantics": "source_list_position", "tags": [name], "metrics": metrics}
            if book_id in ids:
                previous = dict(ids[book_id]); previous.pop("source_position")
                comparison = dict(book); comparison.pop("source_position")
                if previous != comparison:
                    raise CollectorError("同一公开分类中的作品 ID 对应冲突数据。", "source_changed")
                warnings.append(f"来源列表重复作品 ID {book_id}，保留首次出现。")
                continue
            ids[book_id] = book
            books.append(book)
        provenance = {
            "source_id": SOURCE_ID, "source_name": SOURCE_NAME, "repo_url": REPO_URL,
            "source_url": source_url, "source_revision": source_revision,
            "source_date": source_date, "fetched_at": fetched_at,
            "scope": f"{channel}/new/{name}", "sample_limit": SAMPLE_LIMIT,
            "rank_semantics": "source_list_position", "metric_unit": "人",
            "metric_precision": "source_display_rounded", "raw_entries": len(rows),
            "coverage_kind": "public_category_top20", "includes_reading_board": False,
        }
        snapshots.append({
            "platform": "fanqie", "mode": "long", "channel": CHANNELS[channel], "board": "新书榜",
            "category": name, "books": books, "source_url": source_url,
            "source_id": SOURCE_ID, "source_revision": source_revision,
            "data_date": source_date, "collected_at": fetched_at,
            "rank_version": "public:" + source_date + ":" + source_revision.rsplit(":", 1)[-1],
            "period": None, "provenance": provenance,
            "complete": False, "requested_count": SAMPLE_LIMIT, "available_count": len(books),
            "source_total": None,
            "warnings": ["公开仓库每类至多 Top20 样本，非完整榜单；列表位置不是已核实的原榜名次。", *warnings],
        })
    if expected_entries is not None and raw_entries != expected_entries:
        raise CollectorError("公开快照与索引的书目条目数量不一致。", "source_changed")
    names = [category["name"].strip() for category in categories]
    for snapshot in snapshots:
        snapshot["categories"] = names
    return snapshots


def sync_public_rankings(progress=None):
    """Read two indexes, then current and exactly seven-days-earlier files.

    At most six fixed requests, four workers, no retries or filesystem writes.
    A missing history file does not discard valid current data. Every failure
    is returned explicitly for callers to retain caches or use direct sources.
    """
    snapshots, failures, warnings, indexes = [], [], [], {}
    interruption = None

    def interrupted(exc, pending):
        nonlocal interruption
        if not network.is_authorization_error(exc):
            return False
        interruption = interruption or exc
        for future in pending:
            future.cancel()
        return True

    def failure(channel, stage, exc, source_date=None):
        item = {"channel": channel, "date": source_date, "stage": stage,
                "message": str(exc), "state": getattr(exc, "state", "source_changed")}
        failures.append(item)
        warnings.append(f"{CHANNELS[channel]}公开{stage}读取失败：{exc}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fetch = network.authorized_callable(_fetch_json)
        pending = {}
        for channel in CHANNELS:
            try:
                network.check_authorization()
                pending[pool.submit(fetch, f"api/{channel}/new/index.json")] = channel
            except Exception as exc:
                if not interrupted(exc, pending):
                    raise
                break
        for future in as_completed(pending):
            channel = pending[future]
            try:
                record = future.result()
                source_date, count, books = _index(record["data"], channel)
                indexes[channel] = {"date": source_date, "categories": count, "entries": books}
            except CancelledError:
                continue
            except Exception as exc:
                if interrupted(exc, pending):
                    continue
                if not isinstance(exc, (CollectorError, ValueError, TypeError, KeyError)):
                    raise
                failure(channel, "index", exc)
        pending = {}
        for channel, meta in indexes.items():
            if interruption is not None:
                break
            for days in (0, 7):
                source_date = meta["date"] - timedelta(days=days)
                path = f"data/{channel}/new/{source_date:%Y%m%d}.json"
                try:
                    network.check_authorization()
                    pending[pool.submit(fetch, path)] = (channel, source_date.isoformat(), days)
                except Exception as exc:
                    if not interrupted(exc, pending):
                        raise
                    break
        for future in as_completed(pending):
            channel, source_date, days = pending[future]
            try:
                record = future.result()
                normalized = normalize_snapshot(
                    record["data"], channel, source_date,
                    source_url=record["url"], source_revision=record["source_revision"],
                    fetched_at=record["fetched_at"],
                    expected_categories=indexes[channel]["categories"] if days == 0 else None,
                    expected_entries=indexes[channel]["entries"] if days == 0 else None,
                )
                snapshots.extend(normalized)
            except CancelledError:
                continue
            except Exception as exc:
                if interrupted(exc, pending):
                    continue
                if not isinstance(exc, (CollectorError, ValueError, TypeError, KeyError)):
                    raise
                failure(channel, "history" if days else "current", exc, source_date)
                continue
            if progress and interruption is None:
                # Cancellation belongs to the caller; do not misreport it as
                # a malformed source and continue with more progress updates.
                try:
                    progress(f"已读取{CHANNELS[channel]} {source_date} 公开新书榜样本，共 {sum(len(s['books']) for s in normalized)} 条。")
                except Exception as exc:
                    if not interrupted(exc, pending):
                        raise
    # A history without its matching current feed must not replace the visible
    # current data when today's file is missing or inconsistent with its index.
    current_channels = {channel for channel, meta in indexes.items()
                        if any(s["channel"] == CHANNELS[channel] and s["data_date"] == meta["date"].isoformat() for s in snapshots)}
    snapshots = [s for s in snapshots if s["channel"] in {CHANNELS[c] for c in current_channels}]
    snapshots.sort(key=lambda s: (s["data_date"], s["channel"], s["category"]))
    coverage = []
    for channel in CHANNELS:
        if channel not in current_channels:
            continue
        meta = indexes[channel]
        current = [s for s in snapshots if s["channel"] == CHANNELS[channel] and s["data_date"] == meta["date"].isoformat()]
        old = [s for s in snapshots if s["channel"] == CHANNELS[channel] and s["data_date"] != meta["date"].isoformat()]
        coverage.append({"channel": channel, "channel_label": CHANNELS[channel],
                         "date": meta["date"].isoformat(), "categories": len(current),
                         "entries": sum(len(s["books"]) for s in current), "sample_limit": SAMPLE_LIMIT,
                         "history_date": (meta["date"] - timedelta(days=7)).isoformat() if old else None,
                         "history_categories": len(old), "complete_platform": False})
    return {"source_id": SOURCE_ID, "source_name": SOURCE_NAME, "source_url": REPO_URL,
            "fetched_at": _now(), "snapshots": snapshots, "coverage": coverage,
            "warnings": warnings, "failures": failures,
            **({"interrupted": True, "interrupted_reason": interruption.code,
                "interrupted_message": str(getattr(interruption, "message", "会员状态变化，更新已暂停。"))[:1200]}
               if interruption is not None else {})}
