"""Unit and integration tests for Google Gemini / Google AI text worker."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from otternest.gemini_worker import (
    GeminiWorkerError,
    generate_gemini_summary,
    make_gemini_payload,
    validated_endpoint,
)
from otternest.runner import execute_command

PACKET = {
    "task": {
        "id": "t-gemini-1",
        "project_id": "p-1",
        "title": "项目架构评估",
        "instruction": "分析交付物并列出要点",
        "acceptance": "列出至少两点建议",
        "risk": "R1",
    },
    "goal": {"title": "高质量测试交付"},
    "memories": [{"title": "架构准则", "content": "采用无状态控制面"}],
    "dependency_handoffs": [{"result_summary": "上游任务已通过验收"}],
}


def test_gemini_payload_formatting():
    payload = make_gemini_payload(PACKET, model="gemini-2.5-flash", max_tokens=1000)
    assert "system_instruction" in payload
    assert "contents" in payload
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert "AUTHORIZED TASK REQUEST" in user_text
    assert "UNTRUSTED REFERENCE MATERIAL" in user_text
    assert "采用无状态控制面" in user_text


def test_gemini_endpoint_validation():
    valid = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert validated_endpoint(valid) == valid

    with pytest.raises(GeminiWorkerError, match="HTTPS Gemini endpoint required"):
        validated_endpoint("http://example.com/generateContent")

    assert validated_endpoint("http://127.0.0.1:8080/generateContent", allow_local_http=True)


def test_gemini_response_parsing_and_finish_reason():
    packet = PACKET

    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Gemini 分析完成：架构无状态，建议完善备份。"}]
                    },
                    "finishReason": "STOP",
                }
            ]
        }
        return httpx.Response(200, json=body)

    summary = generate_gemini_summary(
        packet,
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        api_key="fake-gemini-key",
        transport=httpx.MockTransport(handler),
    )
    assert "Gemini 分析完成" in summary


def test_gemini_response_safety_block():
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "promptFeedback": {"blockReason": "SAFETY"}
        }
        return httpx.Response(200, json=body)

    with pytest.raises(GeminiWorkerError, match="Gemini prompt blocked: SAFETY"):
        generate_gemini_summary(
            PACKET,
            endpoint="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            api_key="fake-gemini-key",
            transport=httpx.MockTransport(handler),
        )


def test_gemini_worker_subprocess_execution(monkeypatch):
    recorded = []

    class FakeGeminiHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(content_len).decode("utf-8"))
            recorded.append(data)
            auth_key = self.headers.get("x-goog-api-key")
            resp = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": f"Gemini 响应已确认，API Key: {auth_key}"}]
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGeminiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("OTTERNEST_GEMINI_API_KEY", "gemini-secret-test")
    try:
        result = execute_command(
            [
                sys.executable, "-m", "otternest.gemini_worker",
                "--endpoint", f"http://127.0.0.1:{server.server_port}/v1beta/models/gemini-2.5-flash:generateContent",
                "--allow-local-http",
                "--model", "gemini-2.5-flash",
            ],
            PACKET,
            30,
            lambda: None,
            pass_env=("OTTERNEST_GEMINI_API_KEY",),
        )
        assert "Gemini 响应已确认" in (result.summary if hasattr(result, "summary") else result)
        assert len(recorded) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
