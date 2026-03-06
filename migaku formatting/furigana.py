from __future__ import annotations

import re
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
KANJI_RE = re.compile(r"[\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
LATIN_DIGIT_RE = re.compile(r"[A-Za-z0-9]")
BRACKET_RE = re.compile(r"(\[[^\]]*\])")
HTML_SPLIT_RE = re.compile(r"(<[^>]+>)")
FURIGANA_MARKUP_RE = re.compile(r"[\u4e00-\u9fff]+\[[^\[\]\s][^\[\]]*\]")


def _normalize_plain_text(raw: str) -> str:
    text = TAG_RE.sub("", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_kanji_chars(text: str) -> set[str]:
    return set(KANJI_RE.findall(text or ""))


def _katakana_to_hiragana(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            chars.append(chr(code - 0x60))
        else:
            chars.append(ch)
    return "".join(chars)


def _is_kanji_char(ch: str) -> bool:
    return bool(ch) and bool(KANJI_RE.fullmatch(ch))


def _is_kana_char(ch: str) -> bool:
    return bool(ch) and bool(KANA_RE.fullmatch(ch))


def _is_spacing_trigger_char(ch: str) -> bool:
    if not ch:
        return False
    return bool(KANA_RE.fullmatch(ch) or LATIN_DIGIT_RE.fullmatch(ch))


def _should_insert_space_before_token(text: str, idx: int, token_text: str) -> bool:
    if idx <= 0:
        return False
    if not _extract_kanji_chars(token_text):
        return False

    prev = text[idx - 1]
    if prev.isspace():
        return False
    if _is_kanji_char(prev):
        return False
    # Keep punctuation and opening brackets attached to the following token.
    if prev in "([<{\"'「『（［【｛《〈〔｟、。・!?！？:：;；":
        return False
    return _is_spacing_trigger_char(prev)


def _annotate_surface_with_reading(surface: str, reading: str) -> str:
    if not surface or not reading:
        return surface
    if not _extract_kanji_chars(surface):
        return surface

    s_chars = list(surface)
    r_chars = list(reading)

    # Strip matching kana prefix.
    s_left = 0
    r_left = 0
    while s_left < len(s_chars) and r_left < len(r_chars):
        s_ch = s_chars[s_left]
        if not _is_kana_char(s_ch):
            break
        if _katakana_to_hiragana(s_ch) != _katakana_to_hiragana(r_chars[r_left]):
            break
        s_left += 1
        r_left += 1

    # Strip matching kana suffix.
    s_right = len(s_chars)
    r_right = len(r_chars)
    while s_right > s_left and r_right > r_left:
        s_ch = s_chars[s_right - 1]
        if not _is_kana_char(s_ch):
            break
        if _katakana_to_hiragana(s_ch) != _katakana_to_hiragana(r_chars[r_right - 1]):
            break
        s_right -= 1
        r_right -= 1

    core_surface = "".join(s_chars[s_left:s_right])
    core_reading = "".join(r_chars[r_left:r_right])
    if not core_surface or not core_reading or not _extract_kanji_chars(core_surface):
        return f"{surface}[{reading}]"

    prefix = "".join(s_chars[:s_left])
    suffix = "".join(s_chars[s_right:])
    return f"{prefix}{core_surface}[{core_reading}]{suffix}"


def _extract_reading_from_fugashi_token(token: Any) -> str:
    feat = getattr(token, "feature", None)
    if feat is not None:
        for attr in ("kana", "reading", "pron", "kanaBase", "pronBase"):
            value = getattr(feat, attr, None)
            if isinstance(value, str) and value and value != "*":
                return _katakana_to_hiragana(value)
        if isinstance(feat, (list, tuple)):
            for idx in (7, 6):
                if idx < len(feat):
                    value = feat[idx]
                    if isinstance(value, str) and value and value != "*":
                        return _katakana_to_hiragana(value)
    return ""


class _JapaneseTokenizer:
    def __init__(self) -> None:
        self._fugashi_tagger = None
        self._janome_tokenizer = None
        self._backend = "none"

        try:
            import fugashi  # type: ignore

            self._fugashi_tagger = fugashi.Tagger()
            self._backend = "fugashi"
            return
        except Exception:
            pass

        try:
            from janome.tokenizer import Tokenizer  # type: ignore

            self._janome_tokenizer = Tokenizer()
            self._backend = "janome"
        except Exception:
            self._backend = "none"

    @property
    def available(self) -> bool:
        return self._backend != "none"

    def tokenize(self, text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        if self._backend == "fugashi" and self._fugashi_tagger is not None:
            out: list[tuple[str, str]] = []
            for token in self._fugashi_tagger(text):
                surface = getattr(token, "surface", "")
                reading = _extract_reading_from_fugashi_token(token)
                out.append((surface, reading))
            return out
        if self._backend == "janome" and self._janome_tokenizer is not None:
            out = []
            for token in self._janome_tokenizer.tokenize(text):
                surface = getattr(token, "surface", "")
                reading = getattr(token, "reading", "") or ""
                if reading and reading != "*":
                    reading = _katakana_to_hiragana(reading)
                else:
                    reading = ""
                out.append((surface, reading))
            return out
        return []


_TOKENIZER: _JapaneseTokenizer | None = None


def _get_tokenizer() -> _JapaneseTokenizer:
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = _JapaneseTokenizer()
    return _TOKENIZER


def _annotate_plain_japanese_segment(text: str, tokenizer: _JapaneseTokenizer) -> str:
    if not text or not _extract_kanji_chars(text):
        return text

    tokens = tokenizer.tokenize(text)
    if not tokens:
        return text

    cursor = 0
    out: list[str] = []
    for surface, reading in tokens:
        if not surface:
            continue
        idx = text.find(surface, cursor)
        if idx < 0:
            idx = cursor
        if idx > len(text):
            idx = len(text)

        out.append(text[cursor:idx])
        end = min(idx + len(surface), len(text))
        token_text = text[idx:end]
        already_has_reading = end < len(text) and text[end] == "["
        should_annotate = (
            bool(_extract_kanji_chars(token_text))
            and bool(reading)
            and not already_has_reading
            and reading != token_text
        )
        if should_annotate:
            if _should_insert_space_before_token(text, idx, token_text):
                out.append(" ")
            out.append(_annotate_surface_with_reading(token_text, reading))
        else:
            out.append(token_text)
        cursor = end

    out.append(text[cursor:])
    return "".join(out)


def build_furigana_text(text: str) -> str:
    tokenizer = _get_tokenizer()
    if not tokenizer.available:
        return text

    pieces: list[str] = []
    for part in HTML_SPLIT_RE.split(text or ""):
        if not part:
            continue
        if TAG_RE.fullmatch(part):
            pieces.append(part)
            continue

        bracketed_parts = BRACKET_RE.split(part)
        for chunk in bracketed_parts:
            if not chunk:
                continue
            if chunk.startswith("[") and chunk.endswith("]"):
                pieces.append(chunk)
                continue
            pieces.append(_annotate_plain_japanese_segment(chunk, tokenizer))

    return "".join(pieces)


def build_furigana(term: str, reading: str) -> str:
    clean_term = _normalize_plain_text(term)
    clean_reading = _normalize_plain_text(reading)
    if not clean_term:
        return ""
    if not _extract_kanji_chars(clean_term):
        return clean_term
    if not clean_reading or clean_reading == clean_term:
        return clean_term
    return _annotate_surface_with_reading(clean_term, clean_reading)


def has_square_furigana(text: str) -> bool:
    return bool(FURIGANA_MARKUP_RE.search(text or ""))
