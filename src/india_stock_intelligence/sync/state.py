from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from .models import ReportDescriptor, utc_now


class SyncStateError(RuntimeError):
    pass


def next_daily_run(daily_time: str, *, now: datetime | None = None) -> str:
    """Return the next local-machine daily schedule time as an ISO timestamp."""
    try:
        parsed = time.fromisoformat(daily_time)
    except ValueError as exc:
        raise ValueError("Daily sync time must use HH:MM or HH:MM:SS") from exc
    local_now = now or datetime.now().astimezone()
    if local_now.tzinfo is None:
        local_now = local_now.astimezone()
    candidate = local_now.replace(hour=parsed.hour, minute=parsed.minute,
                                  second=parsed.second, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _json_bytes(document: dict) -> bytes:
    return (json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")


class SyncStateStore:
    """File-backed synchronization state and artifact provenance; SQLite schema is untouched."""

    def __init__(self, root: Path | str, *, daily_time: str = "18:15") -> None:
        self.root = Path(root)
        self.daily_time = daily_time
        self.state_path = self.root / "state.json"
        self.history_path = self.root / "history.jsonl"
        self.artifact_dir = self.root / "artifacts"
        self.sidecar_dir = self.root / "manifests" / "artifacts"
        self.run_dir = self.root / "manifests" / "runs"
        self.lock_path = self.root / "nse_sync.lock"

    def default_state(self) -> dict:
        return {
            "state_version": 1,
            "source": "NSE CM UDiFF",
            "live_retrieval_enabled": False,
            "status": "NOT CONFIGURED",
            "last_attempt": None,
            "last_successful_sync": None,
            "latest_available_trading_date": None,
            "next_scheduled_sync": next_daily_run(self.daily_time),
            "agent_execution_status": "NOT CONFIGURED",
            "processed_artifacts": {},
        }

    def load(self) -> dict:
        if not self.state_path.exists():
            return self.default_state()
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncStateError("NSE synchronization state file is unreadable") from exc
        if not isinstance(document, dict) or document.get("state_version") != 1:
            raise SyncStateError("NSE synchronization state has an unsupported format")
        return self.default_state() | document

    def write_artifact(self, descriptor: ReportDescriptor, payload: bytes, digest: str) -> Path:
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Artifact digest does not match payload bytes")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", descriptor.filename).strip("._") or "report.bin"
        path = self.artifact_dir / digest / name
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise SyncStateError("Existing artifact path contains bytes with a different digest")
            return path
        _atomic_write(path, payload)
        return path

    def sidecar_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            raise ValueError("Artifact digest must be a SHA-256 hex string")
        return self.sidecar_dir / f"{digest.lower()}.json"

    def read_sidecar(self, digest: str) -> dict | None:
        path = self.sidecar_path(digest)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SyncStateError(f"Artifact sidecar is unreadable for SHA-256 {digest}") from exc
        if data.get("artifact_sha256") != digest.lower():
            raise SyncStateError("Artifact sidecar SHA-256 key does not match its content")
        return data

    def write_sidecar(self, digest: str, manifest: dict) -> Path:
        document = dict(manifest)
        document["artifact_sha256"] = digest.lower()
        path = self.sidecar_path(digest)
        _atomic_write(path, _json_bytes(document))
        return path

    def write_run_manifest(self, run_id: str, manifest: dict) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", run_id)
        path = self.run_dir / f"{safe_id}.json"
        _atomic_write(path, _json_bytes(manifest))
        return path

    def record(self, result: dict, *, successful: bool = False,
               latest_trading_date: str | None = None,
               processed_artifacts: dict[str, str] | None = None) -> dict:
        current = self.load()
        current.update({
            "status": result.get("status", "FAILED"),
            "last_attempt": result,
            "next_scheduled_sync": next_daily_run(self.daily_time),
            "live_retrieval_enabled": False,
            "agent_execution_status": result.get("agent_status", "NOT CONFIGURED"),
        })
        if successful:
            current["last_successful_sync"] = result.get("finished_at") or utc_now()
        if latest_trading_date:
            current["latest_available_trading_date"] = latest_trading_date
        if processed_artifacts:
            current["processed_artifacts"] = current.get("processed_artifacts", {}) | processed_artifacts
        _atomic_write(self.state_path, _json_bytes(current))
        self.root.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("ab") as stream:
            stream.write(_json_bytes(result))
        return current
