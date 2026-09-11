"""Das System am Schreibtisch starten - Simulator, ohne jede Hardware.

    python scripts/run_sim.py [--port 8088] [--daten ORDNER]

Gedacht für den ersten Schritt der Inbetriebnahme ("Simulator am Schreibtisch")
und für jede Änderung an der Oberfläche, die man ansehen will, bevor sie in die
Kabine kommt.

Warum dieses Skript im Repo liegt und nicht in einem Sitzungsordner: ein
Startskript, das irgendwo im Temp-Verzeichnis liegt, ist beim nächsten Mal weg -
und der Eintrag, der darauf zeigt, läuft ins Leere. Genau das ist am 09.09.2026
passiert.

Die Konfigurationsdatei wird neben die Daten gelegt, nicht nach
``C:\\ProgramData`` bzw. ``/etc``: dort schreiben zu dürfen ist nicht garantiert,
und ein Simulatorlauf soll die Einstellungen der echten Anlage nicht anfassen.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--daten", default="",
                        help="Datenordner; leer = ein Ordner neben dem Repo")
    parser.add_argument("--abspielen", default="", metavar="DATEI",
                        help="Statt des Simulators eine Aufzeichnung abspielen "
                             "(Dateiname aus dem Ordner 'rohdaten' oder ein Pfad)")
    parser.add_argument("--tempo", type=float, default=1.0,
                        help="Abspieltempo; 1,0 ist der Takt der Aufzeichnung")
    args = parser.parse_args()

    daten = Path(args.daten) if args.daten else HIER.parent / ".simulator"
    daten.mkdir(parents=True, exist_ok=True)

    from agripilot import config as config_module

    pfad = daten / "config.yaml"
    config = config_module.load(pfad)
    if args.abspielen:
        config.gnss.source = "replay"
        config.gnss.replay_file = args.abspielen
        config.gnss.replay_speed = args.tempo
    elif config.gnss.source != "replay":
        # Eine in der Oberfläche eingestellte Aufzeichnung nicht wegbügeln:
        # sonst überlebt "Abspielen" keinen Neustart, und genau nach einem
        # Neustart soll es wirken.
        config.gnss.source = "simulator"
    config.imu.source = "simulator"
    config.corrections.source = "aus"
    config.steering.output = "none"
    config.server.host = args.host
    config.server.port = args.port
    config.server.data_dir = str(daten)
    config.network.device_name = "Simulator"
    config.save(pfad)
    os.environ["AGRIPILOT_CONFIG"] = str(pfad)

    from agripilot.server import create_app
    import uvicorn

    art = ("Abspielen: " + config.gnss.replay_file
           if config.gnss.source == "replay" else "Simulator")
    print(f"AgriPilot ({art})  http://{args.host}:{args.port}")
    print(f"Daten und Konfiguration: {daten}")
    uvicorn.run(create_app(config), host=args.host, port=args.port,
                log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
