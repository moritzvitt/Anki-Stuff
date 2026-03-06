#!/usr/bin/env python3
import re
from typing import Any, Dict, List, Tuple

import requests

ANKI_CONNECT_URL = "http://localhost:8765"
ANKI_CONNECT_VERSION = 6

TARGET_QUERY = 'deck:"Japanese Media::Filme & Serien::ジブリ::海が聞こえる" tag:multi_lemma'

SUCCESS_TAG = "multi_lemma_cloze_fixed"
FAIL_TAG = "multi_lemma_cloze_fix_failed"

# Matches clozes like:
# {{c1::lemma::hint}}
# {{c2::lemma}}
CLOZE_RE = re.compile(r"\{\{(c\d+)::(.*?)(?:::(.*?))?\}\}", re.DOTALL)


class AnkiConnectError(RuntimeError):
    pass


def invoke(action: str, **params: Any) -> Any:
    payload = {"action": action, "version": ANKI_CONNECT_VERSION, "params": params}
    resp = requests.post(ANKI_CONNECT_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise AnkiConnectError(f"{action} failed: {data['error']}")
    return data.get("result")


def replace_cloze_hint_with_definition(cloze_text: str, definition: str) -> Tuple[str, int]:
    """Replace cloze hint (::...) with the note's Word Definition.

    Keeps the original cloze index and answer text.
    """

    def _repl(match: re.Match[str]) -> str:
        cloze_index = match.group(1)
        answer = match.group(2)
        return f"{{{{{cloze_index}::{answer}::{definition}}}}}"

    return CLOZE_RE.subn(_repl, cloze_text)


def main() -> int:
    try:
        note_ids: List[int] = invoke("findNotes", query=TARGET_QUERY)
    except Exception as exc:
        print(f"ERROR: Could not search notes with query '{TARGET_QUERY}': {exc}")
        return 1

    if not note_ids:
        print(f"No notes found for query: {TARGET_QUERY}")
        return 0

    print(f"Found {len(note_ids)} notes for query: {TARGET_QUERY}")

    notes_info = invoke("notesInfo", notes=note_ids)

    success_note_ids: List[int] = []
    failed_note_ids: List[int] = []

    changed_count = 0
    unchanged_count = 0

    for note in notes_info:
        nid = note["noteId"]
        fields: Dict[str, Dict[str, str]] = note.get("fields", {})

        cloze = fields.get("Cloze", {}).get("value", "")
        word_def = fields.get("Word Definition", {}).get("value", "")

        if not cloze.strip():
            print(f"FAIL {nid}: missing/empty Cloze")
            failed_note_ids.append(nid)
            continue

        if not word_def.strip():
            print(f"FAIL {nid}: missing/empty Word Definition")
            failed_note_ids.append(nid)
            continue

        new_cloze, replacements = replace_cloze_hint_with_definition(cloze, word_def)

        if replacements == 0:
            print(f"FAIL {nid}: no cloze pattern found in Cloze field")
            failed_note_ids.append(nid)
            continue

        try:
            if new_cloze != cloze:
                invoke(
                    "updateNoteFields",
                    note={
                        "id": nid,
                        "fields": {
                            "Cloze": new_cloze,
                        },
                    },
                )
                changed_count += 1
                print(f"OK   {nid}: updated ({replacements} cloze replacement(s))")
            else:
                unchanged_count += 1
                print(f"OK   {nid}: already up to date")

            success_note_ids.append(nid)
        except Exception as exc:
            print(f"FAIL {nid}: update error: {exc}")
            failed_note_ids.append(nid)

    # Tag notes by result.
    if success_note_ids:
        invoke("addTags", notes=success_note_ids, tags=SUCCESS_TAG)
        invoke("removeTags", notes=success_note_ids, tags=FAIL_TAG)

    if failed_note_ids:
        invoke("addTags", notes=failed_note_ids, tags=FAIL_TAG)
        invoke("removeTags", notes=failed_note_ids, tags=SUCCESS_TAG)

    print("\nSummary")
    print(f"- Total notes:      {len(note_ids)}")
    print(f"- Success:          {len(success_note_ids)}")
    print(f"- Failed:           {len(failed_note_ids)}")
    print(f"- Updated Cloze:    {changed_count}")
    print(f"- Already matching: {unchanged_count}")
    print(f"- Success tag:      {SUCCESS_TAG}")
    print(f"- Failure tag:      {FAIL_TAG}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"Network/HTTP error while talking to AnkiConnect: {exc}")
        raise SystemExit(1)
    except AnkiConnectError as exc:
        print(f"AnkiConnect error: {exc}")
        raise SystemExit(1)
