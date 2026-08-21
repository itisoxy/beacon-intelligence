from __future__ import annotations

import json
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from beacon_data.agent import AskBeaconConversation, ToolSelectingTestAdapter, new_thread_id
from beacon_data.pipeline import build_model


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path(tempfile.gettempdir()) / "beacon-intelligence-vercel"
MODEL_CACHE_DIR = RUNTIME_DIR / "model"
MODEL_STORE_PATH = MODEL_CACHE_DIR / "beacon.duckdb"
CHECKPOINT_PATH = RUNTIME_DIR / "ask_beacon_checkpoints.sqlite"

_conversation: AskBeaconConversation | None = None


def _conversation_instance() -> AskBeaconConversation:
    global _conversation
    if _conversation is None:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        model = build_model(ROOT / "Data", MODEL_CACHE_DIR, MODEL_STORE_PATH)
        _conversation = AskBeaconConversation(
            adapter=ToolSelectingTestAdapter(),
            checkpoint_path=CHECKPOINT_PATH,
            model=model,
        )
    return _conversation


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            payload = self._read_json()
            message = str(payload.get("message") or "").strip()
            if not message:
                self._send_json(
                    {"ok": False, "error": {"code": "empty_message", "message": "Message is required."}},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            thread_id = str(payload.get("thread_id") or new_thread_id())
            context = payload.get("application_context") or {}
            result = _conversation_instance().ask(thread_id, message, context)
            self._send_json({"ok": True, **result})
        except Exception as exc:
            self._send_json(
                {"ok": False, "error": {"code": "server_error", "message": str(exc)}},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_GET(self) -> None:
        self._send_json({"ok": True, "thread_id": new_thread_id()})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
