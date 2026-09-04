"""Master / client synchronisation.

One Pi in the yard is the master.  It holds the reference copy of the fields,
the guidance lines and the finished jobs, and it is the one machine that talks
to the outside world (the NTRIP caster, and the office computer that wants the
records).  Every tractor runs the same software as a client.

Clients keep working when the master is unreachable - that is the point of
putting a full database on every machine.  Sync is a background chore, not a
dependency: it exchanges rows changed since the last successful round and merges
by "newest wins" per record, which is safe because ids are UUIDs and each
machine only edits what its own driver touched.

Coverage is the one thing that must not be resolved by "newest wins": if two
tractors work the same field, both did real work.  Those maps are unioned.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Optional

from .coverage import CoverageMap
from .storage import SYNCED_TABLES, Storage


def merge_field_coverage(store: Storage, field_id: str,
                         max_age_s: Optional[float] = None,
                         exclude_job: Optional[str] = None) -> CoverageMap:
    """Union the coverage of every job on a field.

    This is what lets a second tractor join a field halfway through and see the
    ground the first one already worked - the grids share the field datum, so
    the union is exact rather than approximate.
    """
    merged = CoverageMap()
    cutoff = time.time() - max_age_s if max_age_s else None
    for job in store.list_jobs(field_id=field_id, limit=500):
        if exclude_job and job["id"] == exclude_job:
            continue
        if cutoff and (job.get("ended_at") or job["started_at"]) < cutoff:
            continue
        blob = store.get_job_coverage(job["id"])
        if not blob:
            continue
        try:
            merged.merge(CoverageMap.unpack(blob))
        except ValueError:
            continue  # different cell size: skip rather than corrupt the map
    return merged


def collect_changes(store: Storage, since: float) -> dict:
    return {table: store.changes_since(table, since) for table in SYNCED_TABLES}


def apply_changes(store: Storage, changes: dict) -> dict:
    """Apply a batch from the other side, unioning coverage where both have it."""
    applied = {}
    for table, records in (changes or {}).items():
        if table not in SYNCED_TABLES:
            continue
        if table == "jobs":
            records = [_merge_job_coverage(store, r) for r in records]
        applied[table] = store.apply_remote(table, records)
    return applied


def _merge_job_coverage(store: Storage, record: dict) -> dict:
    """Union an incoming job's coverage with whatever we already hold for it."""
    incoming = record.get("coverage")
    if not incoming:
        return record
    existing_blob = store.get_job_coverage(record["id"])
    if not existing_blob:
        return record
    try:
        merged = CoverageMap.unpack(existing_blob)
        merged.merge(CoverageMap.unpack(base64.b64decode(incoming)))
    except (ValueError, Exception):  # noqa: BLE001
        return record
    return {**record, "coverage": base64.b64encode(merged.pack()).decode()}


class SyncClient:
    """Runs on a client Pi: pushes local changes, pulls the master's."""

    def __init__(self, config, store: Storage) -> None:
        self.config = config
        self.store = store
        self.running = False
        self.status = "aus"
        self.last_success: Optional[float] = None
        self.last_error = ""
        self.rounds = 0

    @property
    def cursor(self) -> float:
        return float(self.store.get_setting("sync_cursor", 0.0))

    @cursor.setter
    def cursor(self, value: float) -> None:
        self.store.set_setting("sync_cursor", value)

    async def run(self) -> None:
        self.running = True
        while self.running:
            await self.sync_once()
            await asyncio.sleep(max(5, self.config.network.sync_interval_s))

    async def sync_once(self) -> dict:
        try:
            import httpx
        except ImportError:
            self.status = "httpx fehlt (pip install httpx)"
            return {"ok": False, "error": self.status}

        since = self.cursor
        payload = {
            "device": {
                "id": self.config.network.device_id,
                "name": self.config.network.device_name,
                "role": "client",
            },
            "since": since,
            "changes": collect_changes(self.store, since),
        }
        url = self.config.network.master_url.rstrip("/") + "/api/sync"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            apply_changes(self.store, data.get("changes", {}))
            # Advance only on success, so a failed round is simply retried with
            # the same window rather than losing rows.
            self.cursor = data.get("server_time", time.time())
            self.last_success = time.time()
            self.rounds += 1
            self.status = "synchron"
            self.last_error = ""
            return {"ok": True, **data.get("applied", {})}
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.status = f"offline ({exc.__class__.__name__})"
            return {"ok": False, "error": str(exc)}

    async def stop(self) -> None:
        self.running = False

    def status_dict(self) -> dict:
        return {
            "role": self.config.network.role,
            "master_url": self.config.network.master_url,
            "status": self.status,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "rounds": self.rounds,
            "age_s": (time.time() - self.last_success) if self.last_success else None,
        }
