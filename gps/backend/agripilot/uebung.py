"""Eigenständige Übung mit eigener Datenbank und ohne Abgleich oder Motorausgang."""
from __future__ import annotations
import os
from pathlib import Path

PORT = 18089


def konfigurieren(daten):
    from .config import Config
    cfg = Config()
    cfg.path = Path(daten).resolve()/"config.yaml"
    cfg.server.data_dir = str(Path(daten).resolve())
    cfg.server.host = "127.0.0.1"
    cfg.server.port = PORT
    cfg.network.role = "master"
    cfg.network.device_id = "uebung-lokal"
    cfg.network.device_name = "Übung – keine echte Maschine"
    cfg.network.master_url = ""
    cfg.network.sync_token = ""
    cfg.network.use_master_rtcm = False
    cfg.gnss.source = "simulator"
    cfg.imu.source = "simulator"
    cfg.corrections.source = "aus"
    cfg.steering.output = "none"
    cfg.steering.enabled = True
    cfg.teilbreiten.output = "aus"
    cfg.server.backup_dir = ""
    return cfg


def main(daten=None):
    import uvicorn
    from .server import create_app
    daten = Path(daten) if daten else Path.home()/"AgriPilot-Uebung"
    daten.mkdir(parents=True, exist_ok=True)
    cfg = konfigurieren(daten)
    app = create_app(cfg)
    e = app.state.app.engine
    if not e.store.list_fields():
        f = e.store.save_field(dict(name="Übungsfeld – keine echte Arbeit", datum_lat=48., datum_lon=11.,
            boundary=[[0,0],[160,0],[160,220],[0,220]],area_ha=3.52))
        e.load_field(f["id"])
        l = e.store.save_line(dict(field_id=f["id"],name="Übungsspur",mode="ab",points=[[10,10],[10,210]],spacing_m=3.))
        e.load_line(l["id"])
        e._remember_session()
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
