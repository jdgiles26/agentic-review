"""Minimal unified-diff parsing so findings can cite file:line."""

from __future__ import annotations

import re
from typing import NamedTuple

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class AddedLine(NamedTuple):
    file_path: str | None
    line_no: int | None
    text: str


def _target_path(header: str) -> str | None:
    path = header[4:].split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    return path[2:] if path.startswith("b/") else path


def parse_added_lines(diff: str) -> list[AddedLine]:
    """Return every added line with its target file and new-file line number.

    Diffs without file or hunk headers (a bare list of `+` lines) are accepted;
    those lines carry no file or line number.
    """
    added: list[AddedLine] = []
    file_path: str | None = None
    line_no: int | None = None
    lines = diff.splitlines()
    for i, raw in enumerate(lines):
        # File headers come as a `--- old` / `+++ new` pair; a lone `+++`/`---` is content.
        if raw.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            continue
        if raw.startswith("+++ ") and i > 0 and lines[i - 1].startswith("--- "):
            file_path = _target_path(raw)
            line_no = None
            continue
        if raw.startswith("diff --git "):
            continue
        hunk = _HUNK.match(raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if raw.startswith("+"):
            added.append(AddedLine(file_path, line_no, raw[1:]))
            if line_no is not None:
                line_no += 1
        elif raw.startswith(" ") and line_no is not None:
            line_no += 1
    return added


def changed_files(diff: str) -> list[str]:
    """Target paths named in `+++` headers, in order, without duplicates."""
    seen: dict[str, None] = {}
    lines = diff.splitlines()
    for prev, raw in zip(lines, lines[1:], strict=False):
        if raw.startswith("+++ ") and prev.startswith("--- "):
            path = _target_path(raw)
            if path:
                seen.setdefault(path, None)
    return list(seen)
