"""Bedienwege für Tagesstart, Verlauf, Sicherungsprüfung und Übung."""
from __future__ import annotations
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from fastapi import Body, HTTPException, Request
from fastapi.responses import Response
from . import arbeitstag, backup


def einbauen(api, app, ok, guard):
    e, store = app.engine, app.store

    def stand_pruefen():
        try: arbeitstag.im_stand(e)
        except RuntimeError as exc: raise HTTPException(400,str(exc)) from exc

    @api.get("/api/arbeitstag")
    async def tag():
        return ok(arbeitstag.status(app))

    @api.get("/api/betriebsbuch")
    async def buch():
        return ok(e.betriebsbuch.verlauf())

    @api.post("/api/arbeitstag/einfahrt")
    async def einfahrt(payload: dict = Body(...)):
        return guard(arbeitstag.einfahrt_setzen, e, payload.get("aktion"))

    @api.post("/api/arbeitstag/fortsetzen")
    async def weiter(payload: dict = Body(...)):
        return guard(arbeitstag.fortsetzen, e, str(payload.get("job_id") or ""))

    @api.get("/api/archiv")
    async def archiv(jahr: int | None = None):
        return guard(arbeitstag.archiv, store, jahr, app.config.network.device_id)

    @api.get("/api/archiv/bericht.csv")
    async def bericht(jahr: int | None = None):
        return Response(arbeitstag.bericht_csv(store,jahr),media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":'attachment; filename="AgriPilot-Feldbericht.csv"'})

    @api.get("/api/arbeitstag/planpruefung")
    async def plan():
        from . import planpruefung
        if not e.field:
            raise HTTPException(400,"Erst ein Feld laden")
        gespeichert = store.get_setting("saatplan:"+e.field["id"]) or {}
        p = gespeichert.get("plan")
        if not p:
            raise HTTPException(400,"Für dieses Feld liegt noch kein Saatplan vor")
        feld = dict(e.field)
        ergebnis = await asyncio.to_thread(planpruefung.pruefen, p, feld, e.profile.width_m)
        return ok({**ergebnis,"field_id":feld["id"],"grenze":feld.get("boundary",[]),"plan":p})

    @api.post("/api/backup/pruefen")
    async def sicherung_pruefen():
        stand_pruefen()
        kandidaten = [p for o in app.backup_ordner() for p in backup.liste(o)]
        if not kandidaten:
            raise HTTPException(400,"Noch keine Sicherung vorhanden – zuerst sichern")
        pfad = max(kandidaten,key=lambda p:p.name)
        ergebnis = await asyncio.to_thread(backup.pruefen,pfad)
        store.set_setting("sicherung_pruefung",ergebnis)
        return ok(ergebnis)

    @api.post("/api/betriebsbuch/wiederherstellen")
    async def vorher(payload: dict = Body(...)):
        stand_pruefen()
        if e.job:
            raise HTTPException(400,"Erst die laufende Arbeit beenden")
        eintraege = e.betriebsbuch.verlauf().get("einstellungen",[])
        eintrag = next((v for v in eintraege if v.get("id")==payload.get("id")),None)
        if not eintrag:
            raise HTTPException(404,"Einstellungsstand fehlt")
        kontext = eintrag.get("kontext") or {}
        aktuell = {"traktor_id":store.get_setting("traktor_aktiv"),"geraet_id":store.get_setting("vehicle_profile_active")}
        if any(k in kontext and kontext[k] != aktuell.get(k) for k in ("traktor_id","geraet_id")):
            raise HTTPException(400,"Erst das zugehörige Gespann auswählen")
        werte = {k: v["vorher"] for k,v in (eintrag.get("aenderungen") or {}).items()}
        if not werte:
            raise HTTPException(400,"Dieser Eintrag enthält keinen wiederherstellbaren Stand")
        if any("." in k for k in werte):
            # Einstellungen werden über die bestehende Validierung erneut übernommen.
            from . import settings
            alt = settings.werte(app.config)
            try: settings.uebernehmen(app.config,werte,False)
            except settings.EinstellungsFehler as exc: raise HTTPException(400,str(exc)) from exc
            e._einstellung_buchen(alt, settings.werte(app.config), "wiederherstellung")
        else:
            try: e.update_profile(werte, quelle="wiederherstellung")
            except (ValueError,RuntimeError,KeyError) as exc: raise HTTPException(400,str(exc)) from exc
        return ok({"text":"Vorherige Werte wiederhergestellt; Lenkung bleibt aus"})

    @api.get("/api/sync/konflikte")
    async def konflikte():
        return ok({"konflikte":store.get_setting("sync_konflikte",[])})

    @api.post("/api/sync/konflikte/loesen")
    async def loesen(payload: dict = Body(...)):
        from . import zusatzsync
        stand_pruefen()
        if e.job:
            raise HTTPException(400,"Erst die laufende Arbeit beenden")
        ergebnis = guard(zusatzsync.loesen,store,str(payload.get("id") or ""),payload.get("auswahl"))
        if e.field: e.load_field(e.field["id"])
        return ergebnis

    @api.post("/api/uebung/oeffnen")
    async def uebung(request: Request):
        if request.client and request.client.host not in ("127.0.0.1","::1","localhost","testclient"):
            raise HTTPException(400,"Die Übung am PC selbst öffnen; sie bleibt getrennt auf diesem Rechner")
        from . import uebung as u
        if app.config.network.device_id == "uebung-lokal":
            return ok({"url":f"http://127.0.0.1:{u.PORT}/#menu=arbeitstag"})
        prozess = getattr(app,"uebungsprozess",None)
        if prozess is None or prozess.poll() is not None:
            daten = Path(app.config.server.data_dir)/"uebung-getrennt"
            env = os.environ.copy()
            env.pop("AGRIPILOT_CONFIG",None)
            if getattr(sys,"frozen",False):
                befehl = [sys.executable,"--uebung","--uebungsdaten",str(daten)]
            else:
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
                env["AGRIPILOT_UEBUNG_DATEN"] = str(daten)
                befehl = [sys.executable,"-c","import os; from agripilot.uebung import main; main(os.environ['AGRIPILOT_UEBUNG_DATEN'])"]
            flags = subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
            app.uebungsprozess = subprocess.Popen(befehl,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        import httpx
        async with httpx.AsyncClient(timeout=1.) as client:
            for _ in range(20):
                try:
                    antwort = await client.get(f"http://127.0.0.1:{u.PORT}/api/state")
                    d = antwort.json()
                    if d.get("system",{}).get("uebung") and d.get("device",{}).get("id") == "uebung-lokal":
                        return ok({"url":f"http://127.0.0.1:{u.PORT}/#menu=arbeitstag","text":"Getrennte Übung ist gestartet; keine echten Felddaten oder Motoren."})
                    raise HTTPException(409,"Der Übungsanschluss wird von einem anderen Programm verwendet")
                except (httpx.HTTPError,ValueError):
                    await asyncio.sleep(.25)
        raise HTTPException(503,"Die getrennte Übung konnte nicht gestartet werden; der echte Betrieb bleibt unverändert")
