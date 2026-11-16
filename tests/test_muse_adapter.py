"""Tests for OtterNest Muse Cloud machine task dispatch adapter."""
from __future__ import annotations

import hashlib
import httpx
import pytest

from otternest.muse_adapter import (
    MuseAdapterError,
    dispatch_to_muse,
    validated_muse_endpoint,
)

PACKET = {
    "task": {
        "id": "t-muse-1",
        "project_id": "p-1",
        "title": "Muse 部署任务",
        "instruction": "在测试环境完成服务配置与构建",
        "acceptance": "构建成功并输出部署清单",
        "risk": "R2",
    },
    "goal": {"title": "自动化持续交付"},
}


def test_muse_endpoint_validation():
    valid = "https://muse.example.com/api"
    assert validated_muse_endpoint(valid) == "https://muse.example.com/api"

    with pytest.raises(MuseAdapterError, match="HTTPS Muse endpoint required"):
        validated_muse_endpoint("http://muse.example.com/api")

    assert validated_muse_endpoint("http://127.0.0.1:9000/api", allow_local_http=True) == "http://127.0.0.1:9000/api"


def test_muse_dispatch_and_artifact_verification(tmp_path):
    artifact_content = b"otternest deploy summary: ok\n"
    artifact_sha = hashlib.sha256(artifact_content).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/v1/tasks/run":
            return httpx.Response(200, json={"run_id": "run-123"})
        if path == "/v1/tasks/runs/run-123":
            return httpx.Response(200, json={
                "status": "completed",
                "summary": "Muse 执行成功：已在云端机器完成构建部署。",
                "artifacts": [
                    {
                        "name": "deploy-report.txt",
                        "download_path": "/v1/artifacts/download/deploy-report.txt",
                        "sha256": artifact_sha,
                    }
                ],
            })
        if path == "/v1/artifacts/download/deploy-report.txt":
            return httpx.Response(200, content=artifact_content)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    manifest = dispatch_to_muse(
        PACKET,
        endpoint="https://muse.example.com",
        api_key="test-muse-token",
        workspace_dir=str(tmp_path),
        poll_interval=0.01,
        transport=transport,
    )

    assert "Muse 执行成功" in manifest["summary"]
    assert len(manifest["artifacts"]) == 1
    assert manifest["artifacts"][0]["path"] == "deploy-report.txt"
    assert (tmp_path / "deploy-report.txt").read_bytes() == artifact_content


def test_muse_dispatch_failed_status():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/v1/tasks/run":
            return httpx.Response(200, json={"run_id": "run-failed"})
        if path == "/v1/tasks/runs/run-failed":
            return httpx.Response(200, json={"status": "failed"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with pytest.raises(MuseAdapterError, match="did not complete successfully"):
        dispatch_to_muse(
            PACKET,
            endpoint="https://muse.example.com",
            api_key="test-muse-token",
            poll_interval=0.01,
            transport=transport,
        )
