"""Split one long model answer into ordered QQ-sized text chunks.

The OneBot adapter sends every chunk as its own message.  This module only
decides where to cut; it never sends, stores, or rewrites a reply.

Rules
-----
* A short answer (``<= SOFT_LIMIT`` characters) is returned unchanged as one
  chunk.  Tiny replies are never split.
* Ordinary prose is packed into chunks of about ``SOFT_LIMIT`` (180)
  characters and never exceeds ``HARD_LIMIT`` (350) except for an intact
  fenced code block, which may use the full QQ message ceiling.
* Paragraphs, list items, Chinese/English sentence endings, and closing
  quotes/brackets are preferred boundaries.  When a single sentence is too
  long, softer punctuation, then whitespace, then a grapheme-safe hard cut is
  used.  English words are kept whole whenever a whitespace boundary exists.
* A closed fenced code block that fits in one QQ message is kept intact.  A
  larger block is split by lines with its fence re-opened and closed in each
  chunk, so every chunk is still a valid fenced block.  In that one case the
  fence marker lines are repeated; the code body itself is not dropped or
  reordered.
* Concatenating the chunks of any non-code input reproduces the input exactly.
  No whitespace is normalised, trimmed, or collapsed at a chunk boundary.
  (The cores already normalise CRLF to LF and strip leading/trailing control
  characters before this module sees a reply.)
* ``split_reply`` never raises for a string: invalid/empty input and input
  longer than ``MAX_REPLY_CHARS`` return an empty list, which the adapter
  treats as an invalid reply (nothing is sent, no partial output).
* Up to ``MAX_REPLY_CHARS`` (12000) input always yields at most
  ``MAX_CHUNKS`` (96) non-empty chunks, each at most ``MAX_CHUNK_CHARS``
  (2000) characters.  The output is deterministic.

Only the Python standard library is used.  The grapheme protection is a
small, deliberately conservative check for combining marks, variation
selectors, zero-width joiners, skin-tone modifiers, keycaps, and regional
indicator pairs; it is not a full Unicode grapheme-cluster implementation.
"""
from __future__ import annotations

import re
import unicodedata


__all__ = [
    'split_reply', 'MAX_REPLY_CHARS', 'MAX_CHUNK_CHARS',
    'SOFT_LIMIT', 'HARD_LIMIT', 'MAX_CHUNKS',
]

# The complete model answer may be up to this size (the request layer and the
# private/group cores already bound replies to the same value).
MAX_REPLY_CHARS = 12000
# NapCat/OneBot accepts a single text segment up to this size.
MAX_CHUNK_CHARS = 2000
# Preferred and maximum size for ordinary prose chunks.
SOFT_LIMIT = 120
HARD_LIMIT = 220
# 12000 characters reach at most ceil(12000 / SOFT_LIMIT) + a small margin
# when every semantic unit is packed greedily.  A hard fallback never needs
# more than six chunks.
MAX_CHUNKS = 96

# Sentence endings.  English '.' is handled separately because a decimal
# point or an abbreviation must not be treated as a full stop.
_SENTENCE_END = frozenset('。！？!?；;…\n')
_SOFT_BREAK = frozenset('，,、：:—–')
_CLOSERS = frozenset("”’\"')）)]】》」』}〕｝")
_CLOSING_CHARS = _CLOSERS | _SENTENCE_END

_FENCE_LINE = re.compile(r'[ \t]{0,3}(`{3,}|~{3,})')
_FENCE_CLOSE = re.compile(r'[ \t]{0,3}(`{3,}|~{3,})[ \t]*\Z')
_LIST_MARKER = re.compile(
    r'[ \t]{0,3}(?:[-*+•]|[0-9]{1,3}[.)、]|[（(][0-9一二三四五六七八九十]{1,3}[）)]'
    r'|[一二三四五六七八九十]{1,3}[、.])\s+'
)

_ZWJ = chr(0x200D)
_VARIATION_SELECTORS = frozenset((chr(0xFE0E), chr(0xFE0F)))
_SKIN_TONES = range(0x1F3FB, 0x1F400)


def _is_extend(char):
    """Whether *char* must stay attached to the preceding code point."""
    code = ord(char)
    if code in _SKIN_TONES or char in _VARIATION_SELECTORS or char == _ZWJ:
        return True
    if code == 0x20E3:  # combining enclosing keycap
        return True
    return (
        unicodedata.combining(char) != 0
        or unicodedata.category(char) in ('Mn', 'Mc', 'Me')
    )


def _is_regional_indicator(char):
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _safe_cut(text, cut):
    """Move *cut* left so no known grapheme sequence is split."""
    if cut <= 0:
        return 0
    if cut >= len(text):
        return len(text)
    while 0 < cut < len(text):
        if _is_extend(text[cut]) or text[cut - 1] == _ZWJ:
            cut -= 1
            continue
        if (_is_regional_indicator(text[cut - 1])
                and _is_regional_indicator(text[cut])):
            # A flag is a pair of regional indicators.  A cut inside such a
            # run is safe only when an even number of indicators precedes it,
            # so step left by one instead of collapsing to the run start.
            run_start = cut - 1
            while (run_start > 0
                   and _is_regional_indicator(text[run_start - 1])):
                run_start -= 1
            if (cut - run_start) % 2:
                cut -= 1
                continue
        break
    return max(1, cut)


def _split_sentences(text):
    """Split *text* after sentence endings, keeping every character."""
    pieces = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        boundary = False
        if char in _SENTENCE_END:
            boundary = True
        elif char == '.':
            following = text[index + 1] if index + 1 < length else ''
            if following == '' or following.isspace() or following in _CLOSERS:
                boundary = True
        if not boundary:
            index += 1
            continue
        end = index + 1
        # Keep repeated punctuation, closing quotes, and spaces with the
        # sentence that just ended.  A newline is already consumed.
        while end < length and text[end] in _CLOSING_CHARS:
            end += 1
        while end < length and text[end] in ' \t':
            end += 1
        pieces.append(text[start:end])
        start = end
        index = end
    if start < length:
        pieces.append(text[start:])
    return pieces


def _choose_cut(text, limit):
    """Return a deterministic cut index at most *limit* for oversized text.

    The rightmost punctuation, whitespace, or newline in the window wins, so
    the chunk is as full as possible without cutting inside an English word.
    Punctuation and newline boundaries are already part of that same search.
    """
    window = text[:limit]
    best = -1
    for position, char in enumerate(window):
        if char in _SOFT_BREAK or char in ' \t\n':
            best = position
    if best > 0:
        return best + 1
    return _safe_cut(text, limit)


def _split_oversized(text, limit=HARD_LIMIT):
    """Split one over-long unit into pieces no longer than *limit*."""
    pieces = []
    remaining = text
    while len(remaining) > limit:
        cut = _choose_cut(remaining, limit)
        if cut <= 0:
            cut = _safe_cut(remaining, limit)
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def _fence_of(line):
    match = _FENCE_LINE.match(line)
    return match.group(1) if match is not None else None


def _closed_code(block):
    """Return ``(code_part, trailing_blank_lines)`` for a closed fence.

    ``None`` is returned when the block does not begin with a closed fenced
    code block, so malformed or unclosed fences stay ordinary text.
    """
    lines = block.splitlines(keepends=True)
    if not lines:
        return None
    opening = _fence_of(lines[0])
    if opening is None:
        return None
    marker = opening[0]
    for index in range(1, len(lines)):
        match = _FENCE_CLOSE.match(lines[index].rstrip('\r\n'))
        if (match is not None
                and match.group(1)[0] == marker
                and len(match.group(1)) >= len(opening)):
            return (
                ''.join(lines[:index + 1]),
                ''.join(lines[index + 1:]),
            )
    return None


def _split_code_block(block, limit=MAX_CHUNK_CHARS):
    """Split a closed fenced block, keeping a valid fence in every chunk."""
    lines = block.splitlines(keepends=True)
    opening_line = lines[0]
    marker = _fence_of(opening_line)[0]
    close_index = None
    for index in range(1, len(lines)):
        match = _FENCE_CLOSE.match(lines[index].rstrip('\r\n'))
        if (match is not None
                and match.group(1)[0] == marker
                and len(match.group(1)) >= len(_fence_of(opening_line))):
            close_index = index
            break
    if close_index is None:
        return None

    closing_line = lines[close_index]
    closing_text = closing_line.rstrip('\n')
    closing_newline = '\n' if closing_line.endswith('\n') else ''
    header = opening_line if opening_line.endswith('\n') else opening_line + '\n'
    body_lines = lines[1:close_index]

    fixed = len(header) + len(closing_text) + len(closing_newline) + 1
    budget = max(1, limit - fixed)

    chunks = []
    current = []

    def build(body):
        if body and not body.endswith('\n'):
            body += '\n'
        return header + body + closing_text + closing_newline

    def flush():
        if current:
            chunks.append(build(''.join(current)))
            current.clear()

    for line in body_lines:
        if len(line) <= budget:
            if current and len(''.join(current)) + len(line) > budget:
                flush()
            current.append(line)
            continue
        # A single code line is longer than one whole QQ message.  It is cut
        # into message-sized pieces; the body characters are preserved.
        flush()
        remaining = line
        while len(remaining) > budget:
            cut = _safe_cut(remaining, budget)
            if cut <= 0:
                cut = budget
            piece = remaining[:cut]
            if not piece.endswith('\n'):
                piece += '\n'
            chunks.append(build(piece))
            remaining = remaining[cut:]
        if remaining:
            current.append(remaining)
    flush()
    if not chunks:
        chunks.append(build(''))
    return chunks


def _text_units(block):
    """Split one non-code block into semantic units without losing text."""
    lines = block.splitlines(keepends=True)
    if any(line.strip() and _LIST_MARKER.match(line) for line in lines):
        units = []
        current = []
        for line in lines:
            if line.strip() and _LIST_MARKER.match(line) and current:
                units.append(''.join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            units.append(''.join(current))
        return units
    if len(block)<=HARD_LIMIT:
        return [block]
    return _split_sentences(block)


def _semantic_units(text):
    """Return ``(unit, is_code)`` pairs whose concatenation is *text*."""
    lines = text.splitlines(keepends=True)
    units = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == '':
            start = index
            while index < len(lines) and lines[index].strip() == '':
                index += 1
            units.append((''.join(lines[start:index]), False))
            continue
        start = index
        index += 1
        while index < len(lines) and lines[index].strip() != '':
            index += 1
        while index < len(lines) and lines[index].strip() == '':
            index += 1
        block = ''.join(lines[start:index])
        code = _closed_code(block)
        if code is not None:
            code_part, trailing = code
            units.append((code_part, True))
            if trailing:
                units.append((trailing, False))
            continue
        for unit in _text_units(block):
            units.append((unit, False))
    return units


def _pack(units):
    """Greedily pack units into ordered chunks; return ``(chunks, expanded)``."""
    chunks = []
    current = ''
    expanded = False

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ''

    def add(piece):
        nonlocal current
        if not piece:
            return
        if piece.strip() == '':
            # Pure separator runs are kept, but never allowed to form an
            # over-hard chunk of their own.
            if len(piece) > SOFT_LIMIT:
                for part in _split_oversized(piece, SOFT_LIMIT):
                    add(part)
                return
            if current and len(current) + len(piece) <= SOFT_LIMIT:
                current += piece
            else:
                flush()
                current = piece
            return
        if current and len(current) + len(piece) <= SOFT_LIMIT:
            current += piece
            return
        if len(piece) <= HARD_LIMIT:
            flush()
            current = piece
            return
        flush()
        for part in _split_oversized(piece, HARD_LIMIT):
            if current and len(current) + len(part) <= SOFT_LIMIT:
                current += part
            else:
                flush()
                current = part

    for unit, is_code in units:
        if not unit:
            continue
        if is_code:
            flush()
            if len(unit) <= MAX_CHUNK_CHARS:
                chunks.append(unit)
            else:
                pieces = _split_code_block(unit)
                if pieces is None:
                    pieces = _split_oversized(unit, MAX_CHUNK_CHARS)
                else:
                    expanded = True
                chunks.extend(pieces)
            continue
        if len(unit) > HARD_LIMIT:
            for sentence in _split_sentences(unit):
                if len(sentence) > HARD_LIMIT:
                    for part in _split_oversized(sentence, HARD_LIMIT):
                        add(part)
                else:
                    add(sentence)
            continue
        add(unit)
    flush()
    return chunks, expanded


def _hard_fallback(text):
    """Last-resort bounded split used only if packing fails its invariants."""
    return [text[start:start + MAX_CHUNK_CHARS]
            for start in range(0, len(text), MAX_CHUNK_CHARS)]


def split_reply(text):
    """Return ordered chunks for *text*; see the module docstring for rules."""
    if not isinstance(text, str) or not text:
        return []
    if len(text) > MAX_REPLY_CHARS:
        return []
    if len(text) <= SOFT_LIMIT:
        return [text]

    chunks, expanded = _pack(_semantic_units(text))
    valid = (
        bool(chunks)
        and len(chunks) <= MAX_CHUNKS
        and all(0 < len(chunk) <= MAX_CHUNK_CHARS for chunk in chunks)
    )
    if valid and not expanded and ''.join(chunks) != text:
        valid = False
    if not valid:
        fallback = _hard_fallback(text)
        if not fallback or len(fallback) > MAX_CHUNKS:
            return []
        return fallback
    return chunks
