from __future__ import annotations

import argparse
import concurrent.futures
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .agent import AgentIterationLimitExceeded, AskBeaconConversation, OllamaChatAdapter, ProviderUnavailable, _safe_log_event, new_thread_id
from .pipeline import build_model


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
CHECKPOINT_PATH = ROOT / "data" / "runtime" / "ask_beacon_checkpoints.sqlite"
MODEL_CACHE_DIR = ROOT / ".tmp-agent-debug"
MODEL_STORE_PATH = MODEL_CACHE_DIR / "beacon.duckdb"
API_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ASK_BEACON_API_TIMEOUT_SECONDS", "60"))


def provider_name() -> str:
    return os.getenv("AI_PROVIDER", "ollama").strip().lower()


def model_name() -> str:
    return os.getenv("AI_MODEL") or os.getenv("OLLAMA_MODEL", "qwen3:1.7b")


def create_conversation() -> AskBeaconConversation:
    model = build_model(ROOT / "Data", MODEL_CACHE_DIR, MODEL_STORE_PATH)
    provider = provider_name()
    if provider not in {"ollama", "local"}:
        raise ProviderUnavailable(f"Ask Beacon provider '{provider}' is not configured for this local server.")
    adapter = OllamaChatAdapter(model_name())
    return AskBeaconConversation(adapter=adapter, checkpoint_path=CHECKPOINT_PATH, model=model)


class BeaconRequestHandler(SimpleHTTPRequestHandler):
    conversation: AskBeaconConversation
    executor: concurrent.futures.ThreadPoolExecutor

    def __init__(self, *args: Any, directory: str | None = None, **kwargs: Any):
        super().__init__(*args, directory=directory or str(ROOT), **kwargs)

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/ask-beacon":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self._read_json()
            message = str(payload.get("message") or "").strip()
            if not message:
                self._send_json({"ok": False, "error": {"code": "empty_message", "message": "Message is required."}}, HTTPStatus.BAD_REQUEST)
                return
            thread_id = str(payload.get("thread_id") or new_thread_id())
            context = payload.get("application_context") or {}
            future = self.executor.submit(self.conversation.ask, thread_id, message, context)
            try:
                result = future.result(timeout=API_REQUEST_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                future.cancel()
                self._send_json(
                    {
                        "ok": False,
                        "error": {
                            "code": "request_timeout",
                            "message": f"Ask Beacon exceeded the {API_REQUEST_TIMEOUT_SECONDS:g}s API timeout while waiting for the local model.",
                        },
                    },
                    HTTPStatus.GATEWAY_TIMEOUT,
                )
                return
            _safe_log_event({"event": "response_serialization"})
            self._send_json({"ok": True, **result})
        except ProviderUnavailable as exc:
            self._send_json({"ok": False, "error": {"code": "provider_unavailable", "message": str(exc)}}, HTTPStatus.SERVICE_UNAVAILABLE)
        except AgentIterationLimitExceeded as exc:
            self._send_json({"ok": False, "error": {"code": "max_iterations_reached", "message": str(exc)}}, HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            if _is_provider_error(exc):
                self._send_json({"ok": False, "error": {"code": "provider_unavailable", "message": _provider_error_message(exc)}}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._send_json({"ok": False, "error": {"code": "server_error", "message": str(exc)}}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/api/ask-beacon/thread":
            self._send_json({"ok": True, "thread_id": new_thread_id()})
            return
        super().do_GET()

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        super().end_headers()

    def guess_type(self, path: str) -> str:
        if path.endswith(".js"):
            return "text/javascript"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    BeaconRequestHandler.conversation = create_conversation()
    BeaconRequestHandler.executor = concurrent.futures.ThreadPoolExecutor(max_workers=int(os.getenv("ASK_BEACON_API_WORKERS", "2")))
    server = ThreadingHTTPServer((host, port), BeaconRequestHandler)
    try:
        print(f"Ask Beacon provider: {BeaconRequestHandler.conversation.adapter.provider_name}")
        print(f"Ask Beacon model: {BeaconRequestHandler.conversation.adapter.model_name}")
        print(f"Beacon Intelligence running at http://{host}:{port}")
        server.serve_forever()
    finally:
        BeaconRequestHandler.executor.shutdown(wait=False, cancel_futures=True)
        BeaconRequestHandler.conversation.close()
        server.server_close()


def _is_provider_error(exc: Exception) -> bool:
    module = exc.__class__.__module__
    return module.startswith("ollama") or module.startswith("httpx") or "langchain_ollama" in module


def _provider_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "connection" in text or "connect" in text:
        return "Local Ollama model is unavailable. Start Ollama and try again."
    if "not found" in text or "model" in text:
        return f"Local Ollama model '{model_name()}' is unavailable. Pull the model and try again."
    return "Local Ollama provider failed while generating the Ask Beacon response."


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Beacon Intelligence local app server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
