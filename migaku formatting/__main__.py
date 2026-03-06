from __future__ import annotations

import argparse
import re
from typing import Any

from anki_connect import AnkiConnectClient
from operations import resolve_field_name
from workflows import (
    add_furigana_to_field,
    format_single_lemma_cloze_notes,
    link_heisig_with_media,
    load_notes,
    load_notes_from_cards,
    suspend_new_duplicates_by_lemma,
    tag_notes_containing_square_furigana,
)

SNAPSHOT_HEADER_RE = re.compile(r"^--(.+?)--$", re.MULTILINE)
DEFAULT_QUERY = 'deck:"JP"'


class MigakuFormattingRunner:
    def __init__(self, notes: list[dict[str, Any]], client: AnkiConnectClient) -> None:
        self.notes = notes
        self.client = client

    def run(
        self,
        workflow: str,
        dry_run: bool = True,
        source_field: str = "Lemma",
        reading_field: str = "Reading",
        target_field: str = "Furigana",
    ) -> dict[str, int]:
        if workflow == "format_cloze":
            return format_single_lemma_cloze_notes(self.client, self.notes, dry_run=dry_run)

        if workflow == "suspend_duplicates":
            return suspend_new_duplicates_by_lemma(self.client, self.notes, dry_run=dry_run)

        if workflow == "add_furigana":
            return add_furigana_to_field(
                self.client,
                self.notes,
                dry_run=dry_run,
                source_field=source_field,
                reading_field=reading_field,
                target_field=target_field,
            )

        if workflow == "tag_contains_furigana":
            return tag_notes_containing_square_furigana(
                self.client,
                self.notes,
                dry_run=dry_run,
                target_field=target_field,
            )

        raise ValueError(f"Unknown workflow: {workflow}")


def _serialize_field_snapshot(field_name: str, value: str) -> str:
    return f"--{field_name}--\n{value or ''}"


def _parse_field_snapshot(snapshot: str) -> tuple[str, str] | None:
    matches = list(SNAPSHOT_HEADER_RE.finditer(snapshot or ""))
    if not matches:
        return None
    first = matches[0]
    field_name = first.group(1)
    content_start = first.end()
    if content_start < len(snapshot) and snapshot[content_start] == "\n":
        content_start += 1
    return field_name, snapshot[content_start:]


def _print_progress(prefix: str, done: int, total: int) -> None:
    print(f"\r{prefix}: {done}/{total}", end="", flush=True)
    if done >= total:
        print()


def backup_target_field_to_extra(
    client: AnkiConnectClient,
    notes: list[dict[str, Any]],
    target_field: str,
    target_aliases: list[str],
) -> tuple[list[int], dict[str, int]]:
    backed_up_note_ids: list[int] = []
    skipped_missing_extra = 0
    skipped_missing_target = 0
    actions: list[dict[str, Any]] = []
    total = len(notes)
    for idx, note in enumerate(notes, start=1):
        fields = note.get("fields", {})
        extra_field = resolve_field_name(fields, "Extra", ["extra"])
        if not extra_field:
            skipped_missing_extra += 1
            _print_progress("Backup scan", idx, total)
            continue
        resolved_target = resolve_field_name(fields, target_field, target_aliases)
        if not resolved_target:
            skipped_missing_target += 1
            _print_progress("Backup scan", idx, total)
            continue
        target_value = fields.get(resolved_target, {}).get("value", "")
        snapshot = _serialize_field_snapshot(resolved_target, target_value)
        actions.append(
            {
                "action": "updateNoteFields",
                "params": {"note": {"id": note["noteId"], "fields": {extra_field: snapshot}}},
            }
        )
        backed_up_note_ids.append(note["noteId"])
        _print_progress("Backup scan", idx, total)

    written = 0
    for batch in client.chunked(actions, size=100):
        client.invoke("multi", actions=batch)
        written += len(batch)
        _print_progress("Backup write", written, len(actions))

    return backed_up_note_ids, {
        "backup_notes_saved_to_extra": len(backed_up_note_ids),
        "backup_skipped_missing_extra_field": skipped_missing_extra,
        "backup_skipped_missing_target_field": skipped_missing_target,
    }


def restore_target_field_from_extra(client: AnkiConnectClient, note_ids: list[int]) -> dict[str, int]:
    restored_notes = 0
    skipped_missing_extra = 0
    skipped_invalid_snapshot = 0
    actions: list[dict[str, Any]] = []
    total = len(note_ids)
    for idx, note in enumerate(client.get_notes_info(note_ids), start=1):
        fields = note.get("fields", {})
        extra_field = resolve_field_name(fields, "Extra", ["extra"])
        if not extra_field:
            skipped_missing_extra += 1
            _print_progress("Restore scan", idx, total)
            continue
        snapshot = fields.get(extra_field, {}).get("value", "")
        parsed = _parse_field_snapshot(snapshot)
        if not parsed:
            skipped_invalid_snapshot += 1
            _print_progress("Restore scan", idx, total)
            continue
        field_name, value = parsed
        resolved_name = resolve_field_name(fields, field_name, [field_name])
        if not resolved_name:
            skipped_invalid_snapshot += 1
            _print_progress("Restore scan", idx, total)
            continue
        actions.append(
            {
                "action": "updateNoteFields",
                "params": {"note": {"id": note["noteId"], "fields": {resolved_name: value}}},
            }
        )
        _print_progress("Restore scan", idx, total)

    written = 0
    for batch in client.chunked(actions, size=100):
        client.invoke("multi", actions=batch)
        restored_notes += len(batch)
        written += len(batch)
        _print_progress("Restore write", written, len(actions))

    return {
        "restored_notes": restored_notes,
        "skipped_missing_extra_field": skipped_missing_extra,
        "skipped_invalid_snapshot": skipped_invalid_snapshot,
    }


def _ask_keep_changes() -> bool:
    while True:
        answer = input("Keep changes? [y/n]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer with 'y' or 'n'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migaku formatting workflows")
    parser.add_argument(
        "workflow",
        choices=["format_cloze", "suspend_duplicates", "link_heisig", "add_furigana", "tag_contains_furigana"],
        help="Workflow to run",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="Anki search query for the base notes",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show summary without writing to Anki",
    )
    parser.add_argument(
        "--media-query",
        default=DEFAULT_QUERY,
        help="Media query used by link_heisig",
    )
    parser.add_argument(
        "--heisig-query",
        default='deck:"Japanese Heisig"',
        help="Heisig query used by link_heisig",
    )
    parser.add_argument(
        "--source-field",
        default="Notes",
        help="Source field used by add_furigana",
    )
    parser.add_argument(
        "--reading-field",
        default="Reading",
        help="Reading field used by add_furigana",
    )
    parser.add_argument(
        "--target-field",
        default="Notes",
        help="Target field used by add_furigana and tag_contains_furigana",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = AnkiConnectClient()
    interactive_restore_workflow = args.workflow in {"format_cloze", "add_furigana", "link_heisig"}

    if args.workflow == "link_heisig":
        media_notes = load_notes(client, args.media_query)
        heisig_notes = load_notes(client, args.heisig_query)
        all_notes = list({n["noteId"]: n for n in (media_notes + heisig_notes)}.values())
        backed_up_note_ids: list[int] = []
        if not args.dry_run and interactive_restore_workflow:
            backed_up_note_ids, backup_summary = backup_target_field_to_extra(
                client,
                all_notes,
                target_field="Link",
                target_aliases=["Link", "link"],
            )
            print(backup_summary)
        summary = link_heisig_with_media(client, media_notes, heisig_notes, dry_run=args.dry_run)
        if not args.dry_run and backed_up_note_ids:
            if _ask_keep_changes():
                summary["backup_notes_saved_to_extra"] = len(backed_up_note_ids)
                summary["changes_reverted"] = 0
            else:
                restore_summary = restore_target_field_from_extra(client, backed_up_note_ids)
                summary["backup_notes_saved_to_extra"] = len(backed_up_note_ids)
                summary["changes_reverted"] = 1
                summary.update({f"restore_{k}": v for k, v in restore_summary.items()})
        print(summary)
        return 0

    base_query = args.query
    # Compatibility: if format_cloze is run with --media-query only, use it.
    if args.workflow == "format_cloze" and base_query == DEFAULT_QUERY and args.media_query != DEFAULT_QUERY:
        base_query = args.media_query

    if args.workflow == "suspend_duplicates":
        notes = load_notes_from_cards(client, base_query)
    else:
        notes = load_notes(client, base_query)

    backed_up_note_ids = []
    if not args.dry_run and interactive_restore_workflow:
        if args.workflow == "add_furigana":
            backup_target = args.target_field
            backup_aliases = [args.target_field, "Furigana", "Ruby", "Notes", "Grammatik"]
        elif args.workflow == "format_cloze":
            backup_target = "Cloze"
            backup_aliases = ["Cloze", "cloze"]
        else:
            backup_target = args.target_field
            backup_aliases = [args.target_field]

        backed_up_note_ids, backup_summary = backup_target_field_to_extra(
            client,
            notes,
            target_field=backup_target,
            target_aliases=backup_aliases,
        )
        print(backup_summary)
        if backed_up_note_ids:
            backed_up_set = set(backed_up_note_ids)
            notes = [n for n in notes if n.get("noteId") in backed_up_set]

    runner = MigakuFormattingRunner(notes=notes, client=client)
    summary = runner.run(
        args.workflow,
        dry_run=args.dry_run,
        source_field=args.source_field,
        reading_field=args.reading_field,
        target_field=args.target_field,
    )
    if not args.dry_run and backed_up_note_ids:
        if _ask_keep_changes():
            summary["backup_notes_saved_to_extra"] = len(backed_up_note_ids)
            summary["changes_reverted"] = 0
        else:
            restore_summary = restore_target_field_from_extra(client, backed_up_note_ids)
            summary["backup_notes_saved_to_extra"] = len(backed_up_note_ids)
            summary["changes_reverted"] = 1
            summary.update({f"restore_{k}": v for k, v in restore_summary.items()})
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
