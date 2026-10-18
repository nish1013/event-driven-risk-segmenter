import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

RECORDING_URL = os.environ.get("RECORDING_URL", "http://localhost:8001")
SEGMENTER_URL = os.environ.get("SEGMENTER_URL", "http://localhost:8002")
NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://localhost:8003")
REPO_ROOT = Path(__file__).parent.parent


def post_activity(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{RECORDING_URL}/activities",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _send(request)


def get_segment(merchant_id: str) -> tuple[int, dict[str, Any]]:
    return _send(urllib.request.Request(f"{SEGMENTER_URL}/merchants/{merchant_id}/segment"))


def get_alerts(merchant_id: str, status: str | None = None) -> list[dict[str, Any]]:
    query = "" if status is None else f"?status={status}"
    with urllib.request.urlopen(
        f"{NOTIFIER_URL}/merchants/{merchant_id}/alerts{query}", timeout=5
    ) as response:
        alerts: list[dict[str, Any]] = json.loads(response.read())
    return alerts


def post_resolution(check_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{SEGMENTER_URL}/risk-checks/{check_id}/resolution",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _send(request)


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=REPO_ROOT, check=True, capture_output=True)


def _send(request: urllib.request.Request) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
