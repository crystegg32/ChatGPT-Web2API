"""Full-text correspondence for observed ProseMirror send serialization.

Accept only inserted Markdown escapes and identical-label URL autolinks.
Original backslashes, whitespace, URL targets and all text must be preserved.
No prefix, substring, fuzzy or generic Markdown-to-plain-text matching.
"""
from __future__ import annotations

import re
import string
import unicodedata

_ESCAPABLE = frozenset(string.punctuation)
_LINK = re.compile(r"(?<!\\)\[(https?://[^\]]+)\]\(((?:\\.|[^)])*)\)")
_ESCAPE = re.compile(r"\\([" + re.escape(string.punctuation) + r"])")
_MAX_TEXT = 1024 * 1024


def _canonical(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " "))


def submitted_text_matches(actual: str, expected: str) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
        return False
    if max(len(actual), len(expected)) > _MAX_TEXT:
        return False
    actual, expected = _canonical(actual), _canonical(expected)
    if actual == expected:
        return True

    def unlink(match):
        # The serializer escapes the target's parentheses; never discard a
        # changed destination or a differently named link.
        label, raw_target = match[1], match[2]
        # JSON string URLs can include original trailing backslashes. When
        # serialization duplicates the same escaped URL in label and target,
        # preserve those backslashes for the full-text automaton below.
        if label == raw_target:
            return label
        target = _ESCAPE.sub(r"\1", raw_target)
        return label if label == target else match[0]

    actual = _LINK.sub(unlink, actual)
    # Small bounded automaton handles literal original backslashes without
    # stripping them. A regex with thousands of optional escapes backtracks.
    positions = {0}
    for offset, char in enumerate(actual):
        following = actual[offset + 1] if offset + 1 < len(actual) else ""
        next_positions = set()
        for pos in positions:
            if pos < len(expected):
                if char == expected[pos]:
                    next_positions.add(pos + 1)
                if char == "\\" and following in _ESCAPABLE and following == expected[pos]:
                    next_positions.add(pos)
        if not next_positions or len(next_positions) > 16:
            return False
        positions = next_positions
    return len(expected) in positions
