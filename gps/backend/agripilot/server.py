"""HTTP + WebSocket server, and the process that wires the system together.

The cab display is a web page, which is deliberate: any tablet, phone or laptop
in the yard can open it, the Pi needs no screen driver stack, and a second
person can watch a machine's progress from the office without extra software.

The WebSocket carries the live picture at 10 Hz.  Coverage is sent as deltas -
only the cells marked since the last frame - because sending the whole worked
map ten times a second would saturate the link within a few hectares.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config as config_module
from . import checklist as checklist_module
from . import settings as settings_module
from . import export, imu as imu_module, recorder as recorder_module, sync
from .actuators import build_output
from .coverage import CoverageMap
from .engine import Engine
from .gnss import SimulatorSource, build_source
from .ntrip import CorrectionSource, RtcmRelay, RtcmRelayClient, build_corrections
from .steering import SteeringController
from .storage import Storage

from . import __version__ as VERSION

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


class Application:
    """Owns every long-running part and the shared engine."""

    def __init__(self, config) -> None:
        self.config = config
        self.store = Storage(config.db_path)
        self.engine = Engine(config, self.store)
        # Rohdaten: eine Aufzeichnung, an beiden Quellen angehängt.
        self.aufzeichnung = recorder_module.Aufzeichnung(
            Path(config.server.data_dir) / "rohdaten")
        self.source = None
        self.imu = None
        self._quellen_tasks: list[asyncio.Task] = []
        self._quellen_aufbauen()
        self.steering = SteeringController(
            config.steering, build_output(config, self.imu)
        )
        self.engine.steering = self.steering
        # Führt Buch über den Einbau und liest dabei mit, was sich erst
        # über die Zeit zeigt - etwa ob "RTK fix" dauerhaft steht.
        self.checklist = checklist_module.Checkliste(self.store)
        self.relay: Optional[RtcmRelay] = None
        self.corrections: Optional[CorrectionSource] = None
        self.rtcm_client: Optional[RtcmRelayClient] = None
        self.sync_client: Optional[sync.SyncClient] = None
        self.tasks: list[asyncio.Task] = []
        self.clients: set[WebSocket] = set()

    def _quellen_aufbauen(self) -> None:
        """Empfänger und Neigungssensor aus der Konfiguration bauen.

        Getrennt vom Rest, weil das auch im Betrieb passieren darf: wer eine
        Aufzeichnung ansehen will, soll dafür nicht den Dienst durchstarten.
        """
        config = self.config
        self.source = build_source(config, self.engine.on_fix)
        self.engine.simulator = (self.source if isinstance(self.source, SimulatorSource)
                                 else None)
        # Beim Abspielen führt die Aufzeichnung beide Ströme: Position und Lage
        # kommen aus derselben Datei und bleiben damit im selben Takt. Ein
        # zweiter, eigener Sensor daneben würde davonlaufen - und dann läge die
        # Schräglage neben der Position, also genau der Fehler, den man mit der
        # Aufzeichnung untersuchen wollte.
        if isinstance(self.source, recorder_module.ReplaySource):
            self.imu = self.source.imu
        else:
            # Der Neigungssensor kennt beim Simulator das virtuelle Fahrzeug,
            # damit Hangausgleich und Drehrate auch ohne Hardware sichtbar werden.
            self.imu = imu_module.build_source(config, self.engine.simulator)
        self.engine.imu = self.imu
        self.source.recorder = self.aufzeichnung
        if self.imu is not None:
            self.imu.recorder = self.aufzeichnung
            # Die Nullung des Sensors gehört zum Einbau und darf einen Neustart
            # überleben - sonst muss nach jedem Ausschalten neu genullt werden.
            offsets = self.store.get_setting("imu_offsets") or {}
            self.imu.roll_offset = float(offsets.get("roll_offset", 0.0))
            self.imu.pitch_offset = float(offsets.get("pitch_offset", 0.0))

    def _quellen_starten(self) -> None:
        self._quellen_tasks = [asyncio.create_task(self.source.run())]
        if self.imu is not None:
            self._quellen_tasks.append(asyncio.create_task(self.imu.run()))

    async def quelle_wechseln(self) -> dict:
        """Empfänger und Sensor im laufenden Betrieb neu aus der Konfiguration bauen.

        Die Reihenfolge ist Sicherheitssache: erst die Lenkung aus und eine
        laufende Wende beendet - einer Maschine, die gerade lenkt, darf keine
        andere Positionsquelle untergeschoben werden. Dann eine laufende
        Aufzeichnung beenden, denn was danach hereinkommt, ist eine andere
        Fahrt. Erst dann die alte Quelle anhalten und die neue starten.
        """
        self.steering.disarm("Positionsquelle gewechselt")
        self.engine.stop_turn("Positionsquelle gewechselt")
        if self.aufzeichnung.laeuft:
            self.aufzeichnung.stop()
            self.engine.note("Aufzeichnung beendet: Quelle gewechselt")
        alte_quelle, alter_sensor = self.source, self.imu
        for task in self._quellen_tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await alte_quelle.stop()
        if alter_sensor is not None:
            with contextlib.suppress(Exception):
                await alter_sensor.stop()
        self._quellen_aufbauen()
        # Der Lenkausgang hängt am Sensor (Drehrate als Rückmeldung): neu bauen.
        await self.steering.stop()
        self.steering = SteeringController(
            self.config.steering, build_output(self.config, self.imu))
        self.engine.steering = self.steering
        await self.steering.start()
        # Kurs und Nachlauf gehören zur alten Fahrt; der erste Schritt der neuen
        # wird von der Plausibilitätsprüfung ohnehin verworfen.
        self.engine.heading_filter.value = None
        self.engine.implement_heading.reset(None)
        self._quellen_starten()
        self.engine.note(f"Quelle: {self.config.gnss.source}")
        return {"gnss": self.config.gnss.source, "imu": self.config.imu.source}

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        cfg = self.config
        self.store.touch_device(
            cfg.network.device_id, cfg.network.device_name,
            cfg.network.role, VERSION,
        )
        await self.steering.start()
        self._quellen_starten()

        if cfg.is_master:
            # The master owns the caster connection and re-serves it.
            self.relay = RtcmRelay(cfg.network.rtcm_relay_port)
            await self.relay.start()
            self.corrections = build_corrections(
                cfg, self._on_rtcm, self._current_position)
            if self.corrections is not None:
                self.tasks.append(asyncio.create_task(self.corrections.run()))
        else:
            if cfg.network.use_master_rtcm:
                host = _host_of(cfg.network.master_url)
                self.rtcm_client = RtcmRelayClient(
                    host, cfg.network.rtcm_relay_port,
                    # nicht die gebundene Methode: die Quelle kann wechseln
                    lambda daten: self.source.write_rtcm(daten)
                )
                self.tasks.append(asyncio.create_task(self.rtcm_client.run()))
            self.sync_client = sync.SyncClient(cfg, self.store)
            self.tasks.append(asyncio.create_task(self.sync_client.run()))

        self.tasks.append(asyncio.create_task(self._broadcast_loop()))

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            self.engine.stop_job()
        # Eine halb geschriebene Aufzeichnung ist trotzdem eine: was auf der
        # Karte steht, bleibt lesbar - der letzte Block gehört noch dazu.
        with contextlib.suppress(Exception):
            self.aufzeichnung.stop()
        await self.steering.stop()
        await self.source.stop()
        if self.imu is not None:
            await self.imu.stop()
        for component in (self.corrections, self.rtcm_client, self.sync_client):
            if component is not None:
                await component.stop()
        if self.relay is not None:
            await self.relay.stop()
        for task in self.tasks + self._quellen_tasks:
            task.cancel()
        self.store.close()

    def _on_rtcm(self, data: bytes) -> None:
        """Corrections go to our own receiver and out to the other machines."""
        self.source.write_rtcm(data)
        if self.relay is not None:
            self.relay.broadcast(data)

    def _current_position(self):
        fix = self.engine.fix
        return (fix.lat, fix.lon) if fix and fix.valid else None

    # -- live broadcast ---------------------------------------------------

    async def _broadcast_loop(self) -> None:
        interval = 1.0 / max(1.0, self.config.server.update_hz)
        while True:
            await asyncio.sleep(interval)
            # Auch ohne Zuschauer: die Lenkung muss überwacht bleiben.
            self.engine.tick()
            # Und die Inbetriebnahme liest mit, auch wenn niemand hinsieht:
            # ein Fix, der nur beim Hinschauen steht, ist keiner.
            self.checklist.beobachten(self.checklist_input())
            if not self.clients:
                # Nobody is looking: drop the delta so a later viewer gets a
                # full map instead of a half one.
                self.engine.coverage.drain_new_cells()
                continue
            message = json.dumps({
                "type": "state",
                **self.engine.state(),
                "system": self.system_status(),
                "new_cells": self.engine.coverage.drain_new_cells(),
            })
            for websocket in list(self.clients):
                try:
                    await websocket.send_text(message)
                except Exception:  # noqa: BLE001
                    self.clients.discard(websocket)

    def checklist_input(self) -> dict:
        """Das Wenige, das die Beobachtung braucht - zehnmal je Sekunde.

        Bewusst nicht der volle Zustand: der kostet einen Datenbankzugriff für
        die Geräteliste, und das zehnmal je Sekunde für zwei Zahlen wäre
        Verschwendung.
        """
        fix = self.engine.fix
        return {
            "fix": fix.to_dict() if fix else None,
            "imu": ({"healthy": self.imu.healthy,
                     "roll_deg": self.imu.attitude.roll_deg}
                    if self.imu is not None else None),
        }

    def checklist_state(self) -> dict:
        """Vollbild für die Liste selbst - hier darf es etwas kosten."""
        return {
            **self.engine.state(),
            "system": self.system_status(),
            "fahrzeit_s": checklist_module.fahrzeit_s(self.store),
        }

    def system_status(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "role": self.config.network.role,
            "gnss": {
                "source": self.config.gnss.source,
                "status": self.source.status,
                "healthy": self.source.healthy,
                "lines": self.source.lines_received,
                # Beim Abspielen: wo in der Aufzeichnung wir gerade sind.
                "replay_s": getattr(self.source, "position_s", None),
            },
            "rohdaten": self.aufzeichnung.status(),
            "corrections": {
                "source": self.config.corrections.source,
                "status": self.corrections.status if self.corrections else (
                    self.rtcm_client.status if self.rtcm_client else "aus"),
                "healthy": bool(self.corrections and self.corrections.healthy) or
                           bool(self.rtcm_client and self.rtcm_client.healthy),
                "bytes": (self.corrections.bytes_received if self.corrections else
                          self.rtcm_client.bytes_received if self.rtcm_client else 0),
                # Das Alter der Korrekturen meldet der Empfänger selbst im
                # GGA-Satz. Es ist die ehrlichste Aussage darüber, ob der Weg
                # von der Basis zum Traktor gerade wirklich trägt.
                "age_s": (self.engine.fix.age_of_corrections
                          if self.engine.fix else None),
            },
            "imu": {
                "source": self.config.imu.source,
                "status": self.imu.status if self.imu else "aus",
                "healthy": bool(self.imu and self.imu.healthy),
                "compensation": self.config.imu.terrain_compensation,
            },
            "steering_output": self.steering.output.status_dict(),
            "relay": {
                "running": bool(self.relay and self.relay.server is not None),
                "status": self.relay.status if self.relay else "aus",
                "clients": self.relay.client_count if self.relay else 0,
            },
            "sync": self.sync_client.status_dict() if self.sync_client else {
                "role": "master", "status": "Master"
            },
            "devices": self.store.list_devices(),
        }


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or url


# Einstellungen an Empfänger und Sensor, die nur einen Wert ändern und keine
# neue Verbindung brauchen - alle anderen gnss./imu.-Werte bauen die Quelle neu.
NUR_WERT = ("imu.roll_sign", "imu.terrain_compensation", "imu.use_for_heading")


def create_app(config=None) -> FastAPI:
    config = config or config_module.load()
    application = Application(config)
    api = FastAPI(title="AgriPilot", version=VERSION)
    api.state.app = application
    engine = application.engine
    store = application.store

    @api.on_event("startup")
    async def _startup() -> None:
        await application.start()

    @api.on_event("shutdown")
    async def _shutdown() -> None:
        await application.stop()

    def ok(payload: Any = None) -> JSONResponse:
        return JSONResponse({"ok": True, "data": payload})

    def guard(fn, *args, **kwargs):
        """Turn an engine refusal into a message the driver can read."""
        try:
            return ok(fn(*args, **kwargs))
        except (RuntimeError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -- live state -------------------------------------------------------

    @api.get("/api/state")
    async def get_state() -> dict:
        return {**engine.state(), "system": application.system_status()}

    @api.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        application.clients.add(websocket)
        # First frame carries the full coverage map; after that only deltas.
        await websocket.send_text(json.dumps({
            "type": "init",
            **engine.state(),
            "system": application.system_status(),
            "cells": engine.coverage.cells_for_display(),
        }))
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            application.clients.discard(websocket)

    # -- fields -----------------------------------------------------------

    @api.get("/api/fields")
    async def list_fields() -> list[dict]:
        return store.list_fields()

    @api.post("/api/fields")
    async def create_field(payload: dict = Body(...)):
        return guard(engine.create_field, payload.get("name", "Neues Feld"))

    @api.post("/api/fields/{field_id}/load")
    async def load_field(field_id: str, with_coverage: bool = True):
        result = guard(engine.load_field, field_id)
        if with_coverage:
            # Pick up what has already been worked here today, including work
            # another tractor synced over - that is the point of the master.
            merged = sync.merge_field_coverage(store, field_id, max_age_s=86_400)
            engine.coverage.merge(merged)
        return result

    @api.delete("/api/fields/{field_id}")
    async def delete_field(field_id: str):
        store.delete_field(field_id)
        return ok()

    @api.get("/api/fields/{field_id}/coverage")
    async def field_coverage(field_id: str, hours: float = 24.0) -> dict:
        merged = sync.merge_field_coverage(store, field_id, max_age_s=hours * 3600)
        return {
            "cell_size": merged.cell_size,
            "area_ha": merged.area_ha,
            "cells": merged.cells_for_display(),
        }

    # -- guidance lines ---------------------------------------------------

    @api.get("/api/lines")
    async def list_lines(field_id: Optional[str] = None) -> list[dict]:
        return store.list_lines(field_id)

    @api.post("/api/lines/{line_id}/load")
    async def load_line(line_id: str):
        return guard(engine.load_line, line_id)

    @api.delete("/api/lines/{line_id}")
    async def delete_line(line_id: str):
        store.delete_line(line_id)
        if engine.line and engine.line.id == line_id:
            engine.clear_line()
        return ok()

    @api.post("/api/guidance/contour")
    async def use_contour():
        """Die Feldgrenze als Ringspur nehmen - ohne A- und B-Punkt."""
        return guard(engine.use_contour)

    @api.post("/api/guidance/a")
    async def set_a():
        return guard(engine.set_a)

    @api.post("/api/guidance/b")
    async def set_b(payload: dict = Body(default={})):
        return guard(engine.set_b, payload.get("name", ""))

    @api.post("/api/guidance/a-plus")
    async def set_a_plus(payload: dict = Body(default={})):
        heading = payload.get("heading")
        if heading is None:
            heading = engine.heading or 0.0
        return guard(engine.set_ab_from_heading, float(heading), payload.get("name", ""))

    @api.post("/api/guidance/nudge")
    async def nudge(payload: dict = Body(...)):
        return guard(engine.nudge, float(payload.get("metres", 0.0)))

    @api.post("/api/guidance/clear")
    async def clear_line():
        engine.clear_line()
        return ok()

    # -- recording --------------------------------------------------------

    @api.post("/api/record/start")
    async def start_recording(payload: dict = Body(...)):
        return guard(engine.start_recording, payload.get("mode", "boundary"))

    @api.post("/api/record/stop")
    async def stop_recording(payload: dict = Body(default={})):
        return guard(engine.stop_recording, payload.get("name", ""))

    # -- Rohdaten: aufzeichnen und abspielen -------------------------------

    @api.get("/api/rohdaten")
    async def list_rohdaten() -> dict:
        return {
            "aufzeichnung": application.aufzeichnung.status(),
            "dateien": recorder_module.liste(application.aufzeichnung.ordner),
            "abspielen": {
                "aktiv": config.gnss.source == "replay",
                "datei": config.gnss.replay_file,
                "tempo": config.gnss.replay_speed,
                "schleife": config.gnss.replay_loop,
                "position_s": getattr(application.source, "position_s", None),
            },
        }

    @api.post("/api/rohdaten/start")
    async def start_rohdaten():
        """Mitschreiben, was hereinkommt - roh, vor jeder Auswertung."""
        # Gefragt wird die *laufende* Quelle, nicht die Einstellung: die Quelle
        # wechselt erst beim Neustart. Wer in der Oberfläche zurück auf
        # "Simulator" stellt, läuft bis dahin weiter auf der Aufzeichnung - und
        # bekäme sonst eine Kopie der Kopie, die aussieht wie eine echte Fahrt.
        if isinstance(application.source, recorder_module.ReplaySource):
            raise HTTPException(400, "Es läuft gerade eine Aufzeichnung ab - "
                                     "eine Kopie davon wäre keine neue Messung")
        return ok(application.aufzeichnung.start())

    @api.post("/api/rohdaten/stop")
    async def stop_rohdaten():
        return ok(application.aufzeichnung.stop())

    def _rohdatei(name: str) -> Path:
        # Der Name kommt von außen und wird zu einem Pfad: nur die selbst
        # vergebenen Namen sind gültig, sonst wäre '../../etc/passwd' einer.
        if not recorder_module.ist_gueltiger_name(name):
            raise HTTPException(400, "Kein gültiger Name einer Aufzeichnung")
        pfad = application.aufzeichnung.ordner / name
        if not pfad.exists():
            raise HTTPException(404, "Aufzeichnung gibt es nicht")
        return pfad

    @api.get("/api/rohdaten/{name}")
    async def download_rohdaten(name: str):
        pfad = _rohdatei(name)
        return PlainTextResponse(
            pfad.read_text(encoding="ascii", errors="replace"),
            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @api.delete("/api/rohdaten/{name}")
    async def delete_rohdaten(name: str):
        pfad = _rohdatei(name)
        if (application.aufzeichnung.laeuft
                and application.aufzeichnung.pfad == pfad):
            raise HTTPException(400, "Diese Aufzeichnung läuft gerade")
        if config.gnss.source == "replay" and config.gnss.replay_file == name:
            raise HTTPException(400, "Diese Aufzeichnung wird gerade abgespielt")
        pfad.unlink()
        return ok()

    # -- Vorgewende und Wende ---------------------------------------------

    @api.get("/api/headland")
    async def get_headland() -> dict:
        return {
            **engine.headland.to_dict(),
            **engine.headland_status.to_dict(),
            "arbeitsbreite_m": engine.profile.width_m,
            "spurabstand_m": engine.profile.spacing_m,
        }

    @api.post("/api/headland")
    async def set_headland(payload: dict = Body(...)):
        return guard(lambda: engine.update_headland(payload).to_dict())

    @api.post("/api/turn/plan")
    async def plan_turn(payload: dict = Body(default={})):
        """Route berechnen und gegen die Feldgrenze prüfen - noch nicht fahren."""
        return guard(engine.plan_turn, str(payload.get("muster", "")),
                     str(payload.get("richtung", "")))

    @api.post("/api/turn/start")
    async def start_turn():
        return guard(engine.start_turn)

    @api.post("/api/turn/stop")
    async def stop_turn():
        engine.stop_turn()
        return ok()

    # -- jobs -------------------------------------------------------------

    @api.post("/api/job/start")
    async def start_job(payload: dict = Body(default={})):
        return guard(engine.start_job, payload.get("operation", ""))

    @api.post("/api/job/stop")
    async def stop_job():
        return guard(engine.stop_job)

    @api.get("/api/jobs")
    async def list_jobs(field_id: Optional[str] = None) -> list[dict]:
        return store.list_jobs(field_id=field_id)

    @api.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str):
        store.delete_job(job_id)
        return ok()

    @api.get("/api/jobs/{job_id}/gpx")
    async def job_gpx(job_id: str):
        try:
            body = export.job_gpx(store, job_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(body, media_type="application/gpx+xml", headers={
            "Content-Disposition": f'attachment; filename="fahrt-{job_id[:8]}.gpx"'})

    @api.get("/api/jobs/{job_id}/geojson")
    async def job_geojson(job_id: str):
        try:
            return export.job_geojson(store, job_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @api.get("/api/jobs/{job_id}/csv")
    async def job_csv(job_id: str):
        return PlainTextResponse(export.job_csv(store, job_id), headers={
            "Content-Disposition": f'attachment; filename="fahrt-{job_id[:8]}.csv"'})

    @api.get("/api/jobs.csv")
    async def jobs_csv(field_id: Optional[str] = None):
        return PlainTextResponse(
            export.jobs_summary_csv(store, field_id),
            headers={"Content-Disposition": 'attachment; filename="arbeiten.csv"'},
        )

    # -- machine ----------------------------------------------------------

    @api.post("/api/profile")
    async def update_profile(payload: dict = Body(...)):
        from dataclasses import asdict
        return ok(asdict(engine.update_profile(payload)))

    @api.post("/api/sections/auto")
    async def set_auto_sections(payload: dict = Body(...)):
        engine.auto_sections = bool(payload.get("enabled", True))
        if not engine.auto_sections:
            for section in engine.sections:
                section.enabled = True
        return ok({"auto_sections": engine.auto_sections})

    @api.post("/api/sections/{index}")
    async def set_section(index: int, payload: dict = Body(...)):
        if not 0 <= index < len(engine.sections):
            raise HTTPException(404, "Sektion gibt es nicht")
        section = engine.sections[index]
        if "forced_off" in payload:
            section.forced_off = bool(payload["forced_off"])
        if "auto" in payload:
            section.auto = bool(payload["auto"])
        return ok()

    @api.post("/api/imu/level")
    async def level_imu():
        """Aktuelle Lage als eben merken - auf ebenem Boden ausführen."""
        if application.imu is None:
            raise HTTPException(400, "Kein Neigungssensor eingerichtet")
        result = application.imu.level_here()
        store.set_setting("imu_offsets", result)
        engine.note("Neigungssensor genullt")
        return ok(result)

    @api.post("/api/imu/compensation")
    async def set_compensation(payload: dict = Body(...)):
        config.imu.terrain_compensation = bool(payload.get("enabled", True))
        return ok({"compensation": config.imu.terrain_compensation})

    @api.post("/api/steering/arm")
    async def arm_steering():
        return ok({"message": application.steering.arm(),
                   "armed": application.steering.armed})

    @api.post("/api/steering/centre")
    async def learn_steering_centre():
        """Aktuelle Radstellung als Geradeaus lernen.

        Der Drehgeber zählt relativ und kennt keine Geradeausstellung; sie wird
        beim Scharfschalten gelernt und lässt sich hier ausdrücklich neu setzen,
        etwa nach einem Eingriff von Hand.
        """
        output = application.steering.output
        if not hasattr(output, "learn_centre"):
            raise HTTPException(400, "Dieser Lenkausgang lernt keine Mitte")
        try:
            result = output.learn_centre()
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        engine.note("Lenkung: Mitte gelernt")
        return ok(result)

    @api.post("/api/steering/disarm")
    async def disarm_steering():
        application.steering.disarm()
        return ok({"armed": False})

    # -- system -----------------------------------------------------------

    @api.get("/api/system")
    async def system_status() -> dict:
        return application.system_status()

    # -- Inbetriebnahme ---------------------------------------------------

    @api.get("/api/checklist")
    async def checklist() -> dict:
        return application.checklist.zusammenfassung(application.checklist_state())

    @api.post("/api/checklist/{schritt_id}")
    async def tick_checklist(schritt_id: str, payload: dict = Body(default={})):
        """Einen Schritt abhaken - oder die Bestätigung zurücknehmen.

        Abgelehnt wird, was die laufende Anlage gerade widerlegt: ein Schritt,
        dessen Prüfung nicht trägt, lässt sich nicht wegklicken.
        """
        return guard(application.checklist.abhaken, schritt_id,
                     application.checklist_state(),
                     bool(payload.get("erledigt", True)),
                     config.network.device_name or config.network.device_id)

    @api.delete("/api/checklist")
    async def reset_checklist():
        """Nach einem Umbau fängt die Inbetriebnahme von vorn an."""
        engine.note("Inbetriebnahme zurückgesetzt")
        return ok(application.checklist.zuruecksetzen(application.checklist_state()))

    @api.get("/api/config")
    async def get_config() -> dict:
        return config.to_dict()

    # -- Einstellungen ----------------------------------------------------

    @api.get("/api/settings")
    async def get_settings() -> dict:
        """Beschreibung aller Einstellungen und ihr aktueller Stand."""
        return {
            "gruppen": settings_module.schema(),
            "werte": settings_module.werte(config),
            "datei": str(config.path or ""),
        }

    @api.post("/api/settings")
    async def post_settings(payload: dict = Body(...)):
        """Geänderte Werte prüfen, setzen und in die Konfigurationsdatei schreiben.

        Erst prüfen, dann setzen: eine halb übernommene Konfiguration wäre
        schlimmer als eine abgelehnte.
        """
        aenderungen = payload.get("aenderungen")
        if not isinstance(aenderungen, dict):
            raise HTTPException(400, "Keine Änderungen übergeben")
        # Die Freigabe der Lenkung hängt an der Inbetriebnahme - der Schritt
        # mit dem Not-Aus muss abgehakt sein, bevor sich hier etwas bewegt.
        stand = application.checklist.zusammenfassung(application.checklist_state())
        motor_fertig = next(
            (s["fertig"] for s in stand["schritte"] if s["id"] == "lenkmotor"), True)
        try:
            ergebnis = settings_module.uebernehmen(config, aenderungen, motor_fertig)
        except settings_module.EinstellungsFehler as exc:
            raise HTTPException(400, str(exc)) from exc
        if ergebnis["gespeichert"]:
            engine.note(f"Einstellungen geändert ({ergebnis['gespeichert']})")
        # Empfänger und Sensor werden im Betrieb neu gebaut - ein Neustart
        # dafür wäre auf dem Feld eine Zumutung und beim Abspielen ein Umweg.
        if any(k.startswith(("gnss.", "imu.")) and k not in NUR_WERT
               for k in aenderungen):
            ergebnis["quelle"] = await application.quelle_wechseln()
        return ok({**ergebnis, "werte": settings_module.werte(config)})

    @api.post("/api/quelle/neustart")
    async def quelle_neustart():
        """Empfänger und Sensor neu verbinden - etwa nach dem Umstecken."""
        return ok(await application.quelle_wechseln())

    @api.post("/api/simulator")
    async def simulator(payload: dict = Body(...)):
        simulator_source = engine.simulator
        if simulator_source is None:
            raise HTTPException(400, "Der Simulator läuft nicht")
        if "speed_kmh" in payload:
            simulator_source.set_speed_kmh(float(payload["speed_kmh"]))
        if "steer_deg" in payload and not application.steering.command.engaged:
            simulator_source.set_steer(float(payload["steer_deg"]))
        if "fix_quality" in payload:
            simulator_source.fix_quality = int(payload["fix_quality"])
        if "east" in payload and "north" in payload:
            simulator_source.teleport(
                float(payload["east"]), float(payload["north"]),
                float(payload.get("heading", simulator_source.heading)),
            )
        return ok({"speed_kmh": simulator_source.speed_ms * 3.6,
                   "steer_deg": simulator_source.steer_deg})

    # -- master sync endpoint ---------------------------------------------

    @api.post("/api/sync")
    async def sync_endpoint(payload: dict = Body(...)) -> dict:
        """Called by client tractors. Push their changes, hand back ours."""
        if not config.is_master:
            raise HTTPException(400, "Dieses Gerät ist kein Master")
        device = payload.get("device") or {}
        if device.get("id"):
            store.touch_device(device["id"], device.get("name", device["id"]),
                               device.get("role", "client"))
        applied = sync.apply_changes(store, payload.get("changes", {}))
        since = float(payload.get("since", 0.0))
        return {
            "server_time": time.time(),
            "applied": applied,
            "changes": sync.collect_changes(store, since),
        }

    @api.get("/ca.crt")
    async def ca_zertifikat():
        """Das CA-Zertifikat zum Einrichten eines Tablets.

        Henne und Ei: das Tablet braucht die Papiere, bevor es der
        verschlüsselten Verbindung trauen kann - also liegen sie hier offen.
        Das ist unbedenklich, es ist der öffentliche Teil. Der Schlüssel
        (ca.key) wird nie ausgeliefert.
        """
        pfad = Path(config.server.tls_cert).parent / "ca.crt" if config.server.tls_cert \
            else Path("/etc/agripilot/tls/ca.crt")
        if not pfad.exists():
            raise HTTPException(404, "Kein CA-Zertifikat vorhanden "
                                     "(scripts/make_cert.sh auf dem Pi ausführen)")
        return Response(pfad.read_bytes(), media_type="application/x-x509-ca-cert",
                        headers={"Content-Disposition": 'attachment; filename="agripilot-ca.crt"'})

    # -- static frontend --------------------------------------------------

    # Mounted last so every /api route above wins; everything else is the
    # cab display, served straight from disk.
    if FRONTEND_DIR.exists():
        api.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="app")

    return api


def main() -> None:  # pragma: no cover - entry point
    import uvicorn
    config = config_module.load()
    Path(config.server.data_dir).mkdir(parents=True, exist_ok=True)
    tls = {}
    if config.server.tls_aktiv:
        # Ohne HTTPS bleibt ein Android-Tablet eine Webseite; mit HTTPS wird es
        # eine Kachel, die den Bildschirm wachhält.
        tls = {"ssl_certfile": config.server.tls_cert,
               "ssl_keyfile": config.server.tls_key}
    uvicorn.run(
        create_app(config),
        host=config.server.host,
        port=config.server.port,
        log_level="info",
        **tls,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
