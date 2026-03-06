from __future__ import annotations

import json
import urllib.request
from typing import Any

ANKI_CONNECT_URL = "http://127.0.0.1:8765"
ANKI_CONNECT_VERSION = 6


class AnkiConnectClient:
    def __init__(self, url: str = ANKI_CONNECT_URL, version: int = ANKI_CONNECT_VERSION) -> None:
        self.url = url
        self.version = version

    def invoke(self, action: str, **params: Any) -> Any:
        payload = json.dumps({"action": action, "version": self.version, "params": params}).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("error"):
            raise RuntimeError(f"AnkiConnect error in {action}: {body['error']}")
        return body.get("result")

    def invoke_multi(self, actions: list[dict[str, Any]], batch_size: int = 100) -> list[Any]:
        out: list[Any] = []
        for i in range(0, len(actions), batch_size):
            batch = actions[i : i + batch_size]
            out.extend(self.invoke("multi", actions=batch))
        return out

    def chunked(self, items: list[Any], size: int = 500):
        for i in range(0, len(items), size):
            yield items[i : i + size]

    def get_notes_info(self, note_ids: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for batch in self.chunked(note_ids):
            out.extend(self.invoke("notesInfo", notes=batch))
        return out

    def get_cards_info(self, card_ids: list[int]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for batch in self.chunked(card_ids):
            out.extend(self.invoke("cardsInfo", cards=batch))
        return out
