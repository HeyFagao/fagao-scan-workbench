"""Decode public Fanqie font glyphs with the user-supplied character table.

Only known glyphs are substituted. Unknown glyphs remain visible to callers so
they can reject incomplete text instead of quietly inventing or dropping words.
The table comes from the supplied fanqie_tool/assets/charmap.json package.
"""
from functools import lru_cache
import json
from pathlib import Path
import re


_MAP_PATH = Path(__file__).with_name("assets") / "fanqie-charmap.json"
_PUA = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


@lru_cache(maxsize=1)
def _charmap():
    try:
        raw = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
        return {int(key): value for key, value in raw.items()
                if str(key).isdigit() and 0xE000 <= int(key) <= 0xF8FF
                and isinstance(value, str) and len(value) == 1 and not _PUA.search(value)}
    except (OSError, ValueError, TypeError, AttributeError):
        # The missing asset is a recoverable limitation: leave PUA unchanged.
        return {}


def decode_text(value):
    """Return known glyphs as text; preserve ordinary and unknown characters."""
    if value is None:
        return ""
    return str(value).translate(_charmap())


def has_unmapped_pua(text):
    """True when a decoded string still contains private-use characters."""
    return bool(_PUA.search(str(text or "")))
