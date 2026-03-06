from __future__ import annotations

import re
from typing import Any

from anki_connect import AnkiConnectClient
from furigana import build_furigana, build_furigana_text, has_square_furigana
from operations import (
    extract_kanji_chars,
    group_notes_by_lemma,
    make_cloze,
    make_link,
    normalize_plain_text,
    resolve_field_name,
)

CLOZE_PATTERN_RE = re.compile(r"\{\{c\d+::", re.IGNORECASE)


def _print_progress(prefix: str, done: int, total: int) -> None:
    print(f"\r{prefix}: {done}/{total}", end="", flush=True)
    if done >= total:
        print()


def _run_batched_note_updates(
    client: AnkiConnectClient,
    updates: list[dict[str, Any]],
    progress_label: str,
    batch_size: int = 100,
) -> None:
    written = 0
    total = len(updates)
    if total == 0:
        return
    for batch in client.chunked(updates, size=batch_size):
        actions = [{"action": "updateNoteFields", "params": {"note": note_update}} for note_update in batch]
        client.invoke_multi(actions, batch_size=batch_size)
        written += len(batch)
        _print_progress(progress_label, written, total)


def _run_chunked_note_tag_action(
    client: AnkiConnectClient,
    note_ids: list[int],
    action: str,
    tags: str,
    progress_label: str,
    batch_size: int = 500,
) -> None:
    done = 0
    total = len(note_ids)
    if total == 0:
        return
    for batch in client.chunked(note_ids, size=batch_size):
        client.invoke(action, notes=batch, tags=tags)
        done += len(batch)
        _print_progress(progress_label, done, total)


def _sanitize_tag_component(value: str) -> str:
    normalized = re.sub(r"\s+", "_", (value or "").strip().casefold())
    normalized = re.sub(r"[^\w:-]", "_", normalized)
    return normalized or "unknown_field"


def format_single_lemma_cloze_notes(
    client: AnkiConnectClient,
    notes: list[dict[str, Any]],
    dry_run: bool = True,
    success_tag: str = "meta::single_lemma_success",
    fail_tag: str = "meta::single_lemma_failed",
) -> dict[str, int]:
    success = 0
    failed = 0
    skipped_multi = 0
    skipped_existing_cloze = 0
    updates: list[dict[str, Any]] = []
    success_note_ids: list[int] = []
    failed_note_ids: list[int] = []

    total = len(notes)
    for idx, note in enumerate(notes, start=1):
        fields = note.get("fields", {})
        cloze = fields.get("Cloze", {}).get("value", "")
        lemma = fields.get("Lemma", {}).get("value", "")
        definition = fields.get("Word Definition", {}).get("value", "")

        if CLOZE_PATTERN_RE.search(cloze):
            skipped_existing_cloze += 1
            _print_progress("Cloze scan", idx, total)
            continue

        new_cloze, generated = make_cloze(cloze, lemma, definition)
        if generated is None:
            skipped_multi += 1
            _print_progress("Cloze scan", idx, total)
            continue

        if not dry_run:
            updates.append({"id": note["noteId"], "fields": {"Cloze": new_cloze}})

        if generated:
            success += 1
            if not dry_run:
                success_note_ids.append(note["noteId"])
        else:
            failed += 1
            if not dry_run:
                failed_note_ids.append(note["noteId"])
        _print_progress("Cloze scan", idx, total)

    if not dry_run:
        _run_batched_note_updates(client, updates, progress_label="Cloze write")
        _run_chunked_note_tag_action(client, success_note_ids, "addTags", success_tag, "Tag success add")
        _run_chunked_note_tag_action(client, success_note_ids, "removeTags", fail_tag, "Tag success remove")
        _run_chunked_note_tag_action(client, failed_note_ids, "addTags", fail_tag, "Tag fail add")
        _run_chunked_note_tag_action(client, failed_note_ids, "removeTags", success_tag, "Tag fail remove")

    return {
        "total_notes": len(notes),
        "single_success": success,
        "single_failed": failed,
        "skipped_multi_lemma": skipped_multi,
        "skipped_existing_cloze": skipped_existing_cloze,
        "dry_run": int(dry_run),
    }


def suspend_new_duplicates_by_lemma(
    client: AnkiConnectClient,
    notes: list[dict[str, Any]],
    dry_run: bool = True,
    tag: str = "~suspended_duplicate_lemma_review_is_new",
) -> dict[str, int]:
    grouped = group_notes_by_lemma(notes)
    duplicate_note_ids = sorted({nid for _, nids in grouped.items() if len(nids) > 1 for nid in nids})
    if not duplicate_note_ids:
        return {"duplicate_notes": 0, "cards_suspended": 0, "dry_run": int(dry_run)}

    note_by_id = {note["noteId"]: note for note in notes}
    card_ids: set[int] = set()
    missing_note_ids: list[int] = []

    for nid in duplicate_note_ids:
        note = note_by_id.get(nid)
        cards = note.get("cards", []) if note else []
        if cards:
            card_ids.update(cards)
        else:
            missing_note_ids.append(nid)

    # Fallback if cards were not included in the already loaded notes.
    if missing_note_ids:
        for info in client.get_notes_info(missing_note_ids):
            card_ids.update(info.get("cards", []))

    card_ids_sorted = sorted(card_ids)

    if not dry_run:
        done_cards = 0
        for batch in client.chunked(card_ids_sorted):
            client.invoke("suspend", cards=batch)
            done_cards += len(batch)
            _print_progress("Suspend cards", done_cards, len(card_ids_sorted))
        done_notes = 0
        for batch in client.chunked(duplicate_note_ids):
            client.invoke("addTags", notes=batch, tags=tag)
            done_notes += len(batch)
            _print_progress("Tag duplicates", done_notes, len(duplicate_note_ids))

    return {
        "duplicate_notes": len(duplicate_note_ids),
        "cards_suspended": len(card_ids_sorted),
        "dry_run": int(dry_run),
    }


def link_heisig_with_media(
    client: AnkiConnectClient,
    media_notes: list[dict[str, Any]],
    heisig_notes: list[dict[str, Any]],
    dry_run: bool = True,
    media_field: str = "Lemma",
    heisig_field: str = "Kanji",
    link_field: str = "Link",
) -> dict[str, int]:
    media_kanji: dict[int, set[str]] = {}
    all_kanji: set[str] = set()

    for idx, note in enumerate(media_notes, start=1):
        value = note.get("fields", {}).get(media_field, {}).get("value", "")
        chars = extract_kanji_chars(value)
        media_kanji[note["noteId"]] = chars
        all_kanji.update(chars)
        _print_progress("Media scan", idx, len(media_notes))

    heisig_links_written = 0
    updates: list[dict[str, Any]] = []
    for idx, note in enumerate(heisig_notes, start=1):
        fields = note.get("fields", {})
        value = fields.get(heisig_field, {}).get("value", "")
        chars = extract_kanji_chars(value)
        shared = chars & all_kanji
        if not shared:
            _print_progress("Heisig scan", idx, len(heisig_notes))
            continue

        linked_media = []
        for mnid, mchars in media_kanji.items():
            if mchars & shared:
                linked_media.append(mnid)

        if not linked_media:
            _print_progress("Heisig scan", idx, len(heisig_notes))
            continue

        links = "<br>".join(make_link(f"Media {nid}", nid) for nid in sorted(set(linked_media)))
        if not dry_run:
            updates.append({"id": note["noteId"], "fields": {link_field: links}})
        heisig_links_written += 1
        _print_progress("Heisig scan", idx, len(heisig_notes))

    if not dry_run:
        _run_batched_note_updates(client, updates, progress_label="Heisig write")

    return {
        "media_notes": len(media_notes),
        "heisig_notes": len(heisig_notes),
        "heisig_link_updates": heisig_links_written,
        "dry_run": int(dry_run),
    }


def add_furigana_to_field(
    client: AnkiConnectClient,
    notes: list[dict[str, Any]],
    dry_run: bool = True,
    source_field: str = "Lemma",
    reading_field: str = "Reading",
    target_field: str = "Furigana",
) -> dict[str, int]:
    reading_aliases = [reading_field, "Reading", "Kana", "Pronunciation"]
    source_aliases = [source_field, "Lemma", "Word", "Expression", "Notes", "Grammatik"]
    target_aliases = [target_field, "Furigana", "Ruby", "Notes", "Grammatik"]
    inline_text_fields = {"notes", "grammatik"}

    updated = 0
    skipped_missing_source = 0
    skipped_missing_target = 0
    skipped_missing_reading = 0
    skipped_existing_furigana = 0

    total = len(notes)
    updates: list[dict[str, Any]] = []
    for idx, note in enumerate(notes, start=1):
        fields = note.get("fields", {})
        source_name = resolve_field_name(fields, source_field, source_aliases)

        if not source_name:
            skipped_missing_source += 1
            _print_progress("Furigana scan", idx, total)
            continue

        source_value = fields[source_name].get("value", "")
        source_is_inline = source_name.casefold() in inline_text_fields
        target_name = resolve_field_name(fields, target_field, target_aliases)
        if source_is_inline and not target_name:
            target_name = source_name
        if not target_name:
            skipped_missing_target += 1
            _print_progress("Furigana scan", idx, total)
            continue

        existing_target_value = fields.get(target_name, {}).get("value", "")
        if has_square_furigana(existing_target_value):
            skipped_existing_furigana += 1
            _print_progress("Furigana scan", idx, total)
            continue

        if source_is_inline:
            furigana_value = build_furigana_text(source_value)
        else:
            reading_name = resolve_field_name(fields, reading_field, reading_aliases)
            if not reading_name:
                skipped_missing_reading += 1
                _print_progress("Furigana scan", idx, total)
                continue
            reading_value = fields[reading_name].get("value", "")
            furigana_value = build_furigana(source_value, reading_value)

        if not furigana_value:
            _print_progress("Furigana scan", idx, total)
            continue

        if furigana_value == existing_target_value:
            _print_progress("Furigana scan", idx, total)
            continue

        if not dry_run:
            updates.append({"id": note["noteId"], "fields": {target_name: furigana_value}})
        updated += 1
        _print_progress("Furigana scan", idx, total)

    if not dry_run:
        _run_batched_note_updates(client, updates, progress_label="Furigana write")

    return {
        "total_notes": len(notes),
        "updated_furigana": updated,
        "skipped_missing_source_field": skipped_missing_source,
        "skipped_missing_target_field": skipped_missing_target,
        "skipped_missing_reading_field": skipped_missing_reading,
        "skipped_existing_furigana": skipped_existing_furigana,
        "dry_run": int(dry_run),
    }


def load_notes(client: AnkiConnectClient, query: str) -> list[dict[str, Any]]:
    note_ids = client.invoke("findNotes", query=query)
    return client.get_notes_info(note_ids)


def load_notes_from_cards(client: AnkiConnectClient, query: str) -> list[dict[str, Any]]:
    card_ids = client.invoke("findCards", query=query)
    cards = client.get_cards_info(card_ids)
    note_ids = sorted({c["note"] for c in cards})
    return client.get_notes_info(note_ids)


def tag_notes_containing_square_furigana(
    client: AnkiConnectClient,
    notes: list[dict[str, Any]],
    dry_run: bool = True,
    target_field: str = "Furigana",
) -> dict[str, int]:
    tagged_note_ids: list[int] = []
    skipped_missing_target = 0
    tag_component = _sanitize_tag_component(target_field)
    total = len(notes)

    for idx, note in enumerate(notes, start=1):
        fields = note.get("fields", {})
        resolved_target = resolve_field_name(fields, target_field, [target_field])
        if not resolved_target:
            skipped_missing_target += 1
            _print_progress("Contains furigana scan", idx, total)
            continue

        target_value = fields.get(resolved_target, {}).get("value", "")
        if has_square_furigana(target_value):
            tagged_note_ids.append(note["noteId"])
            tag_component = _sanitize_tag_component(resolved_target)
        _print_progress("Contains furigana scan", idx, total)

    if not dry_run and tagged_note_ids:
        tag = f"meta::contains_furigana_in_{tag_component}"
        _run_chunked_note_tag_action(
            client,
            tagged_note_ids,
            action="addTags",
            tags=tag,
            progress_label="Contains furigana tag add",
        )

    return {
        "total_notes": len(notes),
        "tagged_contains_furigana": len(tagged_note_ids),
        "skipped_missing_target_field": skipped_missing_target,
        "dry_run": int(dry_run),
    }
