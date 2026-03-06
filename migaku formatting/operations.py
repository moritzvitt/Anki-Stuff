from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

TAG_RE = re.compile(r"<[^>]+>")
KANJI_RE = re.compile(r"[\u4e00-\u9fff]")


def resolve_field_name(fields: dict[str, Any], preferred: str, aliases: list[str]) -> str | None:
    if preferred in fields:
        return preferred
    folded = {name.casefold(): name for name in fields}
    if preferred.casefold() in folded:
        return folded[preferred.casefold()]
    for alias in aliases:
        if alias in fields:
            return alias
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return None


def normalize_plain_text(raw: str) -> str:
    text = TAG_RE.sub("", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def make_cloze(cloze_field: str, lemma: str, definition: str) -> tuple[str, bool | None]:
    if "," in lemma:
        return cloze_field, None

    new_cloze, replacements = re.subn(
        r"<strong>\s*(.*?)\s*</strong>",
        lambda m: f"{{{{c1::{m.group(1)}::{definition}}}}}",
        cloze_field,
        count=1,
    )
    return new_cloze, replacements > 0


def group_notes_by_lemma(notes: list[dict[str, Any]], lemma_aliases: list[str] | None = None) -> dict[str, list[int]]:
    lemma_aliases = lemma_aliases or ["Lemma", "lemma", "Word", "Expression"]
    out: dict[str, list[int]] = defaultdict(list)
    for note in notes:
        fields = note.get("fields", {})
        lemma_field = resolve_field_name(fields, "Lemma", lemma_aliases)
        if not lemma_field:
            continue
        lemma = normalize_plain_text(fields[lemma_field].get("value", ""))
        if lemma:
            out[lemma].append(note["noteId"])
    return out


def extract_kanji_chars(text: str) -> set[str]:
    return set(KANJI_RE.findall(text or ""))


def sanitize_link_title(text: str) -> str:
    return (text or "").replace("[", "(").replace("]", ")").replace("|", "/").strip()


def make_link(title: str, nid: int) -> str:
    return f"[{sanitize_link_title(title)}|nid{nid}]"
