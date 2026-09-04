"""SQLite storage.

SQLite because it needs no server, survives a Raspberry Pi losing power mid-row
(WAL mode), and copies to a USB stick as one file.  Every record carries a UUID
and an `updated_at` stamp so the same rows can be merged between machines
without a central id counter - see sync.py.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS fields (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    datum_lat REAL NOT NULL,
    datum_lon REAL NOT NULL,
    boundary TEXT NOT NULL DEFAULT '[]',
    area_ha REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lines (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    points TEXT NOT NULL,
    spacing_m REAL NOT NULL,
    nudge_m REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL,
    line_id TEXT,
    device_id TEXT NOT NULL,
    vehicle TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    ended_at REAL,
    distance_m REAL NOT NULL DEFAULT 0,
    area_ha REAL NOT NULL DEFAULT 0,
    overlap_ha REAL NOT NULL DEFAULT 0,
    working_time_s REAL NOT NULL DEFAULT 0,
    coverage BLOB,
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS track_points (
    job_id TEXT NOT NULL,
    t REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    altitude REAL,
    speed_ms REAL,
    heading REAL,
    fix_quality INTEGER,
    cross_track_m REAL
);
CREATE INDEX IF NOT EXISTS idx_track_job ON track_points(job_id, t);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'client',
    last_seen REAL NOT NULL,
    software TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

SYNCED_TABLES = ("fields", "lines", "jobs", "devices")


def new_id() -> str:
    return uuid.uuid4().hex


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # WAL keeps readers going while a job writes, and survives a hard power
        # cut - which in a tractor is the normal way to switch the system off.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # -- fields -----------------------------------------------------------

    def save_field(self, record: dict) -> dict:
        record = dict(record)
        record.setdefault("id", new_id())
        record.setdefault("boundary", [])
        record.setdefault("note", "")
        record.setdefault("area_ha", 0.0)
        record["updated_at"] = time.time()
        self.db.execute(
            """INSERT INTO fields (id, name, datum_lat, datum_lon, boundary, area_ha,
                                   note, updated_at, deleted)
               VALUES (:id, :name, :datum_lat, :datum_lon, :boundary, :area_ha,
                       :note, :updated_at, 0)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, boundary=excluded.boundary,
                   area_ha=excluded.area_ha, note=excluded.note,
                   datum_lat=excluded.datum_lat, datum_lon=excluded.datum_lon,
                   updated_at=excluded.updated_at, deleted=0""",
            {**record, "boundary": json.dumps(record["boundary"])},
        )
        self.db.commit()
        return self.get_field(record["id"])

    def get_field(self, field_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM fields WHERE id=?", (field_id,)).fetchone()
        return _field_row(row) if row else None

    def list_fields(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM fields WHERE deleted=0 ORDER BY name"
        ).fetchall()
        return [_field_row(r) for r in rows]

    def delete_field(self, field_id: str) -> None:
        self._soft_delete("fields", field_id)

    # -- guidance lines ---------------------------------------------------

    def save_line(self, record: dict) -> dict:
        record = dict(record)
        record.setdefault("id", new_id())
        record.setdefault("nudge_m", 0.0)
        record["updated_at"] = time.time()
        self.db.execute(
            """INSERT INTO lines (id, field_id, name, mode, points, spacing_m,
                                  nudge_m, updated_at, deleted)
               VALUES (:id, :field_id, :name, :mode, :points, :spacing_m,
                       :nudge_m, :updated_at, 0)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, mode=excluded.mode, points=excluded.points,
                   spacing_m=excluded.spacing_m, nudge_m=excluded.nudge_m,
                   updated_at=excluded.updated_at, deleted=0""",
            {**record, "points": json.dumps(record["points"])},
        )
        self.db.commit()
        return self.get_line(record["id"])

    def get_line(self, line_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
        return _line_row(row) if row else None

    def list_lines(self, field_id: Optional[str] = None) -> list[dict]:
        if field_id:
            rows = self.db.execute(
                "SELECT * FROM lines WHERE deleted=0 AND field_id=? ORDER BY name",
                (field_id,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM lines WHERE deleted=0 ORDER BY name"
            ).fetchall()
        return [_line_row(r) for r in rows]

    def delete_line(self, line_id: str) -> None:
        self._soft_delete("lines", line_id)

    # -- jobs -------------------------------------------------------------

    def start_job(self, field_id: str, device_id: str, vehicle: str = "",
                  operation: str = "", line_id: Optional[str] = None) -> dict:
        job = {
            "id": new_id(),
            "field_id": field_id,
            "line_id": line_id,
            "device_id": device_id,
            "vehicle": vehicle,
            "operation": operation,
            "started_at": time.time(),
            "updated_at": time.time(),
        }
        self.db.execute(
            """INSERT INTO jobs (id, field_id, line_id, device_id, vehicle, operation,
                                 started_at, updated_at)
               VALUES (:id, :field_id, :line_id, :device_id, :vehicle, :operation,
                       :started_at, :updated_at)""",
            job,
        )
        self.db.commit()
        return self.get_job(job["id"])

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{k}=:{k}" for k in values)
        self.db.execute(
            f"UPDATE jobs SET {assignments} WHERE id=:id", {**values, "id": job_id}
        )
        self.db.commit()

    def get_job(self, job_id: str) -> Optional[dict]:
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return _job_row(row) if row else None

    def get_job_coverage(self, job_id: str) -> Optional[bytes]:
        row = self.db.execute(
            "SELECT coverage FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        return row["coverage"] if row else None

    def list_jobs(self, field_id: Optional[str] = None, limit: int = 200) -> list[dict]:
        if field_id:
            rows = self.db.execute(
                """SELECT * FROM jobs WHERE deleted=0 AND field_id=?
                   ORDER BY started_at DESC LIMIT ?""",
                (field_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM jobs WHERE deleted=0 ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_job_row(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        self._soft_delete("jobs", job_id)

    # -- track points -----------------------------------------------------

    def add_track_points(self, job_id: str, points: Iterable[tuple]) -> None:
        """Bulk insert. Called with a buffered batch, not per fix."""
        self.db.executemany(
            """INSERT INTO track_points
               (job_id, t, lat, lon, altitude, speed_ms, heading, fix_quality, cross_track_m)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(job_id, *p) for p in points],
        )
        self.db.commit()

    def track_points(self, job_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM track_points WHERE job_id=? ORDER BY t", (job_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- devices ----------------------------------------------------------

    def touch_device(self, device_id: str, name: str, role: str = "client",
                     software: str = "") -> None:
        now = time.time()
        self.db.execute(
            """INSERT INTO devices (id, name, role, last_seen, software, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, role=excluded.role,
                   last_seen=excluded.last_seen, software=excluded.software,
                   updated_at=excluded.updated_at""",
            (device_id, name, role, now, software, now),
        )
        self.db.commit()

    def list_devices(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM devices WHERE deleted=0 ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- settings ---------------------------------------------------------

    def set_setting(self, key: str, value: Any) -> None:
        self.db.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                             updated_at=excluded.updated_at""",
            (key, json.dumps(value), time.time()),
        )
        self.db.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # -- sync helpers -----------------------------------------------------

    def changes_since(self, table: str, since: float) -> list[dict]:
        if table not in SYNCED_TABLES:
            raise ValueError(f"Tabelle {table} wird nicht synchronisiert")
        rows = self.db.execute(
            f"SELECT * FROM {table} WHERE updated_at > ? ORDER BY updated_at", (since,)
        ).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            if "coverage" in record and record["coverage"] is not None:
                import base64
                record["coverage"] = base64.b64encode(record["coverage"]).decode()
            out.append(record)
        return out

    def apply_remote(self, table: str, records: list[dict]) -> int:
        """Merge rows from another machine.

        Last write wins per row: the machine that touched a field most recently
        has the most current picture of it.  Coverage is the exception - two
        tractors on one field both did real work, so their maps are unioned in
        sync.py before the row lands here.
        """
        if table not in SYNCED_TABLES:
            raise ValueError(f"Tabelle {table} wird nicht synchronisiert")
        applied = 0
        for record in records:
            record = dict(record)
            if table == "jobs" and record.get("coverage"):
                import base64
                record["coverage"] = base64.b64decode(record["coverage"])
            existing = self.db.execute(
                f"SELECT updated_at FROM {table} WHERE id=?", (record["id"],)
            ).fetchone()
            if existing and existing["updated_at"] >= record.get("updated_at", 0):
                continue
            columns = [c[1] for c in self.db.execute(f"PRAGMA table_info({table})")]
            record = {k: v for k, v in record.items() if k in columns}
            placeholders = ", ".join(f":{k}" for k in record)
            updates = ", ".join(f"{k}=excluded.{k}" for k in record if k != "id")
            self.db.execute(
                f"""INSERT INTO {table} ({', '.join(record)}) VALUES ({placeholders})
                    ON CONFLICT(id) DO UPDATE SET {updates}""",
                record,
            )
            applied += 1
        self.db.commit()
        return applied

    def _soft_delete(self, table: str, record_id: str) -> None:
        # Soft delete, because a hard delete would come back on the next sync
        # from a machine that has not heard about it yet.
        self.db.execute(
            f"UPDATE {table} SET deleted=1, updated_at=? WHERE id=?",
            (time.time(), record_id),
        )
        self.db.commit()


def _field_row(row: sqlite3.Row) -> dict:
    record = dict(row)
    record["boundary"] = json.loads(record["boundary"] or "[]")
    return record


def _line_row(row: sqlite3.Row) -> dict:
    record = dict(row)
    record["points"] = json.loads(record["points"] or "[]")
    return record


def _job_row(row: sqlite3.Row) -> dict:
    record = dict(row)
    record.pop("coverage", None)  # blob stays out of JSON responses
    return record
