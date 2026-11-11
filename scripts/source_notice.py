# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Check short source notices against the text in docs/ai-maintenance-boundary.md."""

import argparse
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs/ai-maintenance-boundary.md"
MARKER = "发糕团队维护声明。"
COMMENTS = {
    ".py": ("# ", ""), ".command": ("# ", ""), ".sh": ("# ", ""),
    ".yml": ("# ", ""), ".yaml": ("# ", ""), ".toml": ("# ", ""), ".ps1": ("# ", ""),
    ".bat": ("rem ", ""), ".cmd": ("rem ", ""),
    ".js": ("// ", ""), ".mjs": ("// ", ""), ".cjs": ("// ", ""),
    ".ts": ("// ", ""), ".tsx": ("// ", ""), ".jsx": ("// ", ""),
    ".css": ("/* ", " */"), ".html": ("<!-- ", " -->"), ".svg": ("<!-- ", " -->"),
}


def expected_text():
    paragraphs = [line[2:] for line in DOCUMENT.read_text(encoding="utf-8").splitlines()
                  if line.startswith("> " + MARKER)]
    if len(paragraphs) != 1:
        raise SystemExit(f"Expected one quoted source notice in {DOCUMENT.name}")
    return paragraphs[0]


def source_paths():
    contents = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached"], cwd=ROOT,
    )
    for name in contents.decode("utf-8").split("\0"):
        path = ROOT / name
        # Keep the upstream files covered by their original licenses and pinned hashes.
        if name.startswith("fagao/_vendor/") and name != "fagao/_vendor/__init__.py":
            continue
        if path.suffix in COMMENTS and not path.is_symlink() and path.is_file():
            yield path


def comment_offset(lines, suffix):
    # Keep executable and encoding directives in their original positions.
    if suffix in {".bat", ".cmd"} and lines and lines[0].lower().startswith(b"@echo off"):
        return 2 if len(lines) > 1 and lines[1].lower().startswith(b"chcp ") else 1
    if suffix == ".py":
        for index, line in enumerate(lines[:2]):
            if re.match(rb"^[ \t\f]*\#.*?coding[:=][ \t]*[-\w.]+", line):
                return index + 1
    if lines and lines[0].lower().startswith((b"#!", b"<!doctype", b"<?xml", b"@charset ")):
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="sync the reviewed notice into source comments")
    args = parser.parse_args()
    notice = expected_text()
    errors = []
    count = 0
    for path in sorted(source_paths()):
        prefix, suffix = COMMENTS[path.suffix]
        data = path.read_bytes()
        bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
        lines = data[len(bom):].splitlines(keepends=True)
        offset = comment_offset(lines, path.suffix)
        newline = b"\r\n" if b"\r\n" in data else b"\n"
        comment = (prefix + notice + suffix).encode("utf-8") + newline
        count += 1
        end = offset
        while end < len(lines) and lines[end].startswith((prefix + MARKER).encode("utf-8")):
            end += 1
        if lines[offset:end] == [comment]:
            continue
        relative = path.relative_to(ROOT)
        if not args.write:
            errors.append(relative)
            continue
        if offset and not lines[offset - 1].endswith(b"\n"):
            lines[offset - 1] += newline
        lines[offset:end] = [comment]
        path.write_bytes(bom + b"".join(lines))
    for path in errors:
        print(f"Missing or stale source notice: {path}")
    print(f"Source notices: {count - len(errors)}/{count}")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
