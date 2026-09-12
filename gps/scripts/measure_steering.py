#!/usr/bin/env python3
"""Zählwerte je Grad Radeinschlag ausmessen.

Der Wert `phidget.counts_per_deg` sagt, wie viele Zählwerte des Drehgebers
einem Grad Einschlag der Räder **am Boden** entsprechen. Er ist die Umrechnung
zwischen dem, was die Spurführung rechnet, und dem, was der Motor dreht - stimmt
er nicht, lenkt die Anlage systematisch zu viel oder zu wenig.

Ausmessen geht am ehrlichsten von Anschlag zu Anschlag:

    python3 measure_steering.py

Der Motor wird dabei **nicht** bestromt. Gelenkt wird von Hand, das Werkzeug
zählt nur mit.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def frage(text: str) -> str:
    try:
        return input(text)
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)


def zahl(text: str, standard: float) -> float:
    antwort = frage(f"{text} [{standard:g}]: ").strip().replace(",", ".")
    if not antwort:
        return standard
    try:
        return float(antwort)
    except ValueError:
        print("  Keine Zahl - nehme den Vorgabewert.")
        return standard


def main() -> None:
    from agripilot.actuators import phidget_available

    nutzbar, problem = phidget_available()
    if not nutzbar:
        print(problem)
        print("Windows: Phidgets-Installer von phidgets.com installieren.")
        return

    from Phidget22.Devices.Encoder import Encoder

    seriennummer = int(zahl("Seriennummer der Steuerung (-1 = erste gefundene)", -1))
    kanal = int(zahl("Kanal des Drehgebers", 0))

    geber = Encoder()
    if seriennummer >= 0:
        geber.setDeviceSerialNumber(seriennummer)
    geber.setChannel(kanal)
    print("\nVerbinde ...")
    try:
        geber.openWaitForAttachment(5000)
        geber.setEnabled(True)
    except Exception as fehler:  # noqa: BLE001
        print(f"Drehgeber nicht erreichbar: {fehler}")
        return

    print(f"Verbunden: {geber.getDeviceName()}, Seriennummer "
          f"{geber.getDeviceSerialNumber()}, Kanal {geber.getChannel()}\n")

    print("Der Motor bleibt stromlos. Gelenkt wird von Hand.\n")
    frage("Räder ganz nach LINKS einschlagen, dann Eingabetaste ...")
    links = geber.getPosition()
    frage("Räder ganz nach RECHTS einschlagen, dann Eingabetaste ...")
    rechts = geber.getPosition()
    spanne = abs(rechts - links)

    print(f"\n  Zählwerte von Anschlag zu Anschlag: {spanne:.0f}")
    if spanne < 10:
        print("  Das ist zu wenig - zählt der Drehgeber überhaupt mit?")
        geber.close()
        return

    print("\nJetzt der Einschlagwinkel der Räder. Am einfachsten am gelenkten Rad")
    print("messen: Winkel ganz links plus Winkel ganz rechts. Bei den meisten")
    print("Traktoren sind das zusammen 50 bis 60 Grad.")
    winkel = zahl("Gesamter Einschlagbereich in Grad", 55.0)

    if winkel <= 0:
        print("  Der Winkel muss größer als null sein.")
        geber.close()
        return

    je_grad = spanne / winkel
    richtung = "positiv" if rechts > links else "negativ"

    print("\n" + "=" * 58)
    print(f"  counts_per_deg: {je_grad:.2f}")
    print(f"  RescaleFactor der Platine: {1 / je_grad:.6f}")
    print(f"  Zählrichtung nach rechts: {richtung}")
    print("=" * 58)
    print("\nIn die Konfiguration übernehmen:\n")
    print("phidget:")
    print("  control: position")
    print(f"  counts_per_deg: {je_grad:.2f}")
    print(f"  max_wheel_angle_deg: {winkel / 2:.0f}")
    if rechts < links:
        print("  invert_motor: true      # Drehgeber zählt nach rechts abwärts")
    print("\nDanach den Dienst neu starten - der RescaleFactor wird beim")
    print("Verbinden gesetzt und ändert sich im Betrieb nicht mehr.")

    geber.close()


if __name__ == "__main__":
    main()
