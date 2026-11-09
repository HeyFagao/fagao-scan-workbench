"""Resolve conversational book references against immutable local snapshots.

This module deliberately performs no I/O and never falls back from original rank
into the displayed row index. Its output pins both the book and its snapshot.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone as fixed_timezone
from typing import Any
from zoneinfo import ZoneInfo


class ResolutionError(ValueError):
    """A reference is incomplete, ambiguous, unavailable, or internally stale."""

    def __init__(self, code: str, message: str, candidates: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.candidates = candidates or []

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "candidates": self.candidates}


PLATFORM_NAMES = {
    "fanqie": "番茄", "qidian": "起点", "ciweimao": "刺猬猫",
    "heiyan": "黑岩故事会", "fanqie_short": "番茄短故事",
}
PLATFORM_ALIASES = {
    "fanqie_short": ("番茄短故事", "番茄短篇", "fanqie_short"),
    "fanqie": ("番茄", "fanqie"), "qidian": ("起点", "qidian"),
    "ciweimao": ("刺猬猫", "ciweimao"),
    "heiyan": ("黑岩", "heiyan"),
}
CHANNEL_ALIASES = {
    "female": ("女频", "女生", "female", "women", "girl"),
    "male": ("男频", "男生", "male", "men", "boy"),
}
BOARD_ALIASES = {
    "signed_new": ("签约新书榜", "签约新书", "signed_new", "signnewbook"),
    "public_new": ("公众新书榜", "公众新书", "public_new", "pubnewbook"),
    "newcomer_new": ("新人新书榜", "新人新书", "newcomer_new", "newauthor"),
    "recommendation": ("推荐榜", "推荐票榜", "recommendation", "recom"),
    "new": ("新书榜", "新书", "new", "newbook", "new_book", "xinxiu"),
    "reading": ("阅读榜", "阅读", "在读榜", "read", "reading", "readership"),
    "monthly": ("月票榜", "月票", "monthly", "yuepiao", "month"),
    "sales": ("畅销榜", "畅销", "销售榜", "sales", "bestseller", "hot-sales"),
    "subscription": ("订阅榜", "订阅", "subscription", "subscribe"),
    "library": ("书库选题", "书库", "library", "catalog"),
    "hot": ("热门故事", "热门榜", "hot"),
}
NUMBER_PATTERN = r"(?:[0-9０-９]+|[零〇一二两三四五六七八九十百]+)"


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _canonical(value: Any, groups: dict[str, tuple[str, ...]]) -> str:
    normalized = _normalize(value)
    for key, aliases in groups.items():
        if normalized in aliases or normalized == key:
            return key
    return normalized


def _contains(query: str, alias: str) -> bool:
    # Prevent “male” matching “female” and “read” matching unrelated words.
    if alias.isascii():
        return bool(re.search(r"(?<![a-z_])" + re.escape(alias) + r"(?![a-z_])", query))
    return alias in query


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if all(c in digits for c in value):
        return int("".join(str(digits[c]) for c in value))
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in ("十", "百"):
            total += (current or 1) * {"十": 10, "百": 100}[char]
            current = 0
        else:
            raise ResolutionError("invalid_number", "无法识别作品序号。")
    return total + current


def _timestamp(value: Any, timezone) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif value:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone)
    return result.astimezone(timezone)


def _clock(context: dict):
    try:
        zone = ZoneInfo(context.get("timezone") or "Asia/Shanghai")
    except (ValueError, KeyError):
        # Windows Python may lack an IANA database. A local aware timestamp below
        # still supplies the correct user-device offset without another dependency.
        zone = fixed_timezone(timedelta(hours=8))
    raw_now = context.get("now")
    if raw_now and not context.get("timezone"):
        try:
            aware = raw_now if isinstance(raw_now, datetime) else datetime.fromisoformat(str(raw_now).replace("Z", "+00:00"))
            if aware.tzinfo:
                zone = aware.tzinfo
        except ValueError:
            pass
    return zone, _timestamp(raw_now, zone) or datetime.now(zone)


def _scope(snapshot: dict) -> str:
    return " · ".join(str(x) for x in (
        PLATFORM_NAMES.get(snapshot.get("platform"), snapshot.get("platform")),
        "长篇" if snapshot.get("mode") == "long" else "短篇",
        snapshot.get("channel"), snapshot.get("category"), snapshot.get("board"), snapshot.get("period"),
        snapshot.get("data_date") or snapshot.get("collected_at"),
    ) if x)


def _candidate(snapshot: dict, book: dict) -> dict:
    return {"snapshot_id": snapshot["id"], "book_id": book["id"],
            "title": book.get("title", "未命名作品"), "scope_description": _scope(snapshot)}


def _result(snapshot: dict, book: dict) -> dict:
    if not book.get("id") or not snapshot.get("id"):
        raise ResolutionError("invalid_record", "作品缺少稳定标识，请重新采集该来源。")
    return {"snapshot_id": snapshot["id"], "book_id": book["id"],
            "book": deepcopy(book), "scope_description": _scope(snapshot)}


def _book(snapshot: dict, book_id: Any) -> dict:
    matches = [book for book in snapshot.get("books", []) if str(book.get("id")) == str(book_id)]
    if not matches:
        raise ResolutionError("book_not_found", "这份快照中没有找到对应作品，请更新或重新选择作品。")
    if len(matches) != 1:
        raise ResolutionError("invalid_record", "同一快照出现重复作品标识，无法可靠定位。")
    return matches[0]


def _constraints(query: str, snapshots: list[dict]) -> dict:
    constraints: dict[str, str] = {}
    if re.search(r"长篇|(?<![a-z])long(?![a-z])", query):
        constraints["mode"] = "long"
    if re.search(r"短篇|短故事|(?<![a-z])short(?![a-z])", query):
        if constraints.get("mode") == "long":
            raise ResolutionError("ambiguous_mode", "请指定要分析长篇还是短篇。")
        constraints["mode"] = "short"
    for key, aliases in PLATFORM_ALIASES.items():
        if any(_contains(query, alias) for alias in aliases):
            if constraints.get("platform") == "fanqie_short" and key == "fanqie":
                continue
            if "platform" in constraints and constraints["platform"] != key:
                raise ResolutionError("ambiguous_platform", "请指定这次分析的平台。")
            constraints["platform"] = key
    if constraints.get("platform") == "fanqie" and constraints.get("mode") == "short":
        constraints["platform"] = "fanqie_short"
    if constraints.get("platform") in ("fanqie", "qidian", "ciweimao") and "mode" not in constraints:
        constraints["mode"] = "long"
    if constraints.get("platform") in ("heiyan", "fanqie_short"):
        if constraints.get("mode") == "long":
            raise ResolutionError("incompatible_scope", "该来源是短篇书库，请指定对应的短篇作品。")
        constraints["mode"] = "short"
    for field, groups in (("channel", CHANNEL_ALIASES), ("board", BOARD_ALIASES)):
        matching_aliases = [(key, alias) for key, aliases in groups.items() for alias in aliases if _contains(query, alias)]
        matching_aliases = [(key, alias) for key, alias in matching_aliases if not any(alias != longer and alias in longer for _, longer in matching_aliases)]
        detected = sorted({key for key, _ in matching_aliases})
        if len(detected) > 1:
            raise ResolutionError("ambiguous_" + field, "请指定一个频道或榜型。")
        if detected:
            constraints[field] = detected[0]
    categories = {_normalize(s.get("category")) for s in snapshots if s.get("category")}
    categories = {c for c in categories if not c.isdigit() and c not in ("全部", "all", "不限")}
    detected = [c for c in categories if _contains(query, c)]
    # Prefer the full category over a category name contained within it.
    detected = [c for c in detected if not any(c != other and c in other for other in detected)]
    if len(detected) > 1:
        raise ResolutionError("ambiguous_category", "请指定一个作品分类。")
    if detected:
        constraints["category"] = detected[0]
    return constraints


def _matches(snapshot: dict, constraints: dict) -> bool:
    for field, value in constraints.items():
        found = snapshot.get(field)
        if field == "channel":
            found = _canonical(found, CHANNEL_ALIASES)
        elif field == "board":
            found = _canonical(found, BOARD_ALIASES)
        else:
            found = _normalize(found)
        if field == "board" and value == "new" and found in ("signed_new", "public_new", "newcomer_new"):
            continue
        if found != value:
            return False
    return True


def _latest_per_scope(snapshots: list[dict], context: dict) -> list[dict]:
    zone, _ = _clock(context)
    grouped = {}
    for s in snapshots:
        key = (_normalize(s.get("platform")), _normalize(s.get("mode")),
               _canonical(s.get("channel"), CHANNEL_ALIASES), _normalize(s.get("category")),
               _canonical(s.get("board"), BOARD_ALIASES))
        previous = grouped.get(key)
        current_time = _timestamp(s.get("collected_at"), zone)
        previous_time = _timestamp((previous or {}).get("collected_at"), zone)
        if previous is None or (current_time is not None and (previous_time is None or current_time > previous_time)):
            grouped[key] = s
    return list(grouped.values())


def resolve_reference(query: str, context: dict) -> dict:
    """Resolve one book, or raise a specific error rather than silently guessing.

    ``views[mode].ordered_ids`` must contain the full filtered/sorted list across
    all pages. ``page`` is one-based. ``analysis_target`` is supplied by the
    calling conversation so pronouns cannot drift between conversations.
    """
    if not isinstance(query, str) or not query.strip():
        raise ResolutionError("empty_query", "请说要分析哪本作品。")
    if not isinstance(context, dict):
        raise ResolutionError("invalid_context", "无法读取工作台上下文。")
    text = query.strip().lower()
    snapshots = context.get("snapshots", [])
    if isinstance(snapshots, dict):
        snapshots = list(snapshots.values())
    if not snapshots:
        raise ResolutionError("no_snapshots", "还没有保存的榜单或书库，请先在工作台采集或导入。")
    by_id = {s.get("id"): s for s in snapshots if s.get("id")}
    constraints = _constraints(text, snapshots)
    mode = constraints.get("mode", context.get("active_mode", "long"))
    views = context.get("views", {})
    view = views.get(mode) or {}
    current = by_id.get(view.get("snapshot_id"))
    candidates = [s for s in snapshots if _matches(s, constraints)]
    if not candidates:
        raise ResolutionError("scope_not_found", "没有找到符合指定平台、频道和榜型的已保存记录，请先采集该来源。")

    def resolve_pair(pair: dict, require_match: bool = True) -> dict:
        snapshot = by_id.get(pair.get("snapshot_id"))
        if not snapshot:
            raise ResolutionError("snapshot_not_found", "这次选择对应的历史快照已不可用，不能用今天的同名次替代。")
        if require_match and not _matches(snapshot, constraints):
            raise ResolutionError("selection_scope_mismatch", "已选择作品与您指定的平台或榜型不一致，请在对应来源选书或指定名次。")
        return _result(snapshot, _book(snapshot, pair.get("book_id")))

    # A historical selection is immutable, including selections not yet analysed.
    if "昨天" in text:
        zone, now = _clock(context)
        day = (now - timedelta(days=1)).date()
        choices: dict[tuple, dict] = {}
        earliest = datetime.min.replace(tzinfo=zone)
        history = sorted(context.get("history", []), key=lambda h: _timestamp(h.get("selected_at"), zone) or earliest, reverse=True)
        historical_title = re.search(r"《([^》]+)》", query)
        for entry in history:
            when = _timestamp(entry.get("selected_at"), zone)
            if not when or when.date() != day:
                continue
            s = by_id.get(entry.get("snapshot_id"))
            if s and _matches(s, constraints):
                if historical_title and _normalize(_book(s, entry.get("book_id")).get("title")) != _normalize(historical_title.group(1)):
                    continue
                key = (s.get("platform"), str(entry.get("book_id")))
                choices.setdefault(key, entry)
        if not choices:
            raise ResolutionError("history_not_found", "昨天没有符合条件的选书记录；不会替换成今天同名次的作品。")
        if len(choices) > 1:
            items = [_candidate(by_id[e["snapshot_id"]], _book(by_id[e["snapshot_id"]], e["book_id"])) for e in choices.values()]
            raise ResolutionError("ambiguous_history", "昨天选过多本作品，请指定其中的书名。", items)
        return resolve_pair(next(iter(choices.values())))

    rank_match = re.search(r"第\s*(" + NUMBER_PATTERN + r")\s*(?:名|位)", text)
    list_match = re.search(r"第\s*(" + NUMBER_PATTERN + r")\s*(?:本|部|条|个)", text)
    title_match = re.search(r"《([^》]+)》", query)
    if rank_match and list_match:
        raise ResolutionError("conflicting_reference", "请使用列表序号或原榜名次中的一个来指定作品。")

    if rank_match:
        rank = _number(rank_match.group(1))
        if rank < 1:
            raise ResolutionError("invalid_rank", "原榜名次从第 1 名开始。")
        # The current compatible snapshot supplies missing category/channel/date.
        pool = [current] if current and _matches(current, constraints) else _latest_per_scope(candidates, context)
        if len(pool) > 1:
            scopes = [{"snapshot_id": s["id"], "scope_description": _scope(s)} for s in pool]
            raise ResolutionError("ambiguous_scope", "有多个符合条件的榜单，请补充频道或分类。", scopes)
        snapshot = pool[0]
        books = [b for b in snapshot.get("books", []) if str(b.get("rank")) == str(rank)]
        if not books:
            raise ResolutionError("rank_not_found", f"这份榜单没有采到原榜第 {rank} 名；不会用列表第 {rank} 条替代。")
        if len(books) != 1:
            raise ResolutionError("ambiguous_rank", "此快照包含重复名次，请指定作品分类或书名。", [_candidate(snapshot, b) for b in books])
        return _result(snapshot, books[0])

    if list_match:
        index = _number(list_match.group(1))
        if index < 1:
            raise ResolutionError("invalid_index", "列表序号从第 1 本开始。")
        if not current or not _matches(current, constraints):
            raise ResolutionError("view_not_found", "指定来源没有可用的最近列表，请打开该榜单后再指定列表序号。")
        ordered = view.get("ordered_ids")
        if not isinstance(ordered, list):
            raise ResolutionError("view_not_found", "当前列表的显示顺序尚未保存，请在工作台刷新后重试。")
        if re.search(r"这一页|当前页|本页", text):
            try:
                page, size = int(view.get("page", 1)), int(view.get("page_size", 20))
            except (ValueError, TypeError):
                raise ResolutionError("invalid_view", "当前分页信息无效，请刷新工作台。")
            if page < 1 or size < 1 or index > size:
                raise ResolutionError("index_not_found", "这一页没有指定的作品序号。")
            index += (page - 1) * size
        if index > len(ordered):
            raise ResolutionError("index_not_found", f"当前列表只有 {len(ordered)} 本，没有第 {index} 本。")
        return _result(current, _book(current, ordered[index - 1]))

    if title_match:
        title = _normalize(title_match.group(1))
        pool = [current] if current and _matches(current, constraints) else _latest_per_scope(candidates, context)
        items = [(s, b) for s in pool for b in s.get("books", []) if _normalize(b.get("title")) == title]
        if not items:
            raise ResolutionError("title_not_found", "已保存的对应来源中没有找到这本书。")
        if len(items) != 1:
            raise ResolutionError("ambiguous_title", "找到多本同名作品，请补充平台或作者。", [_candidate(s, b) for s, b in items])
        return _result(*items[0])

    # Explicit recent selection beats an ongoing analysis target.
    explicit_selection = bool(re.search(r"这本|刚(?:才)?选|选中|当前.*作品", text))
    followup = bool(re.search(r"继续|它|那本|开头|金手指|反转|情绪|人物|伏笔|结局", text))
    target = context.get("analysis_target")
    if followup and not explicit_selection and target:
        target_snapshot = by_id.get(target.get("snapshot_id"))
        if target_snapshot and _matches(target_snapshot, constraints):
            return resolve_pair(target)
        if not constraints:
            return resolve_pair(target)
    if current and _matches(current, constraints) and view.get("selected_id"):
        return _result(current, _book(current, view["selected_id"]))
    if explicit_selection:
        raise ResolutionError("selection_not_found", "尚未在对应列表选中作品，请先点选一本。")
    raise ResolutionError("reference_missing", "请指定作品，例如“长篇第二本”“番茄新书榜第 14 名”或“刚选的这本”。")
