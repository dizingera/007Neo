"""Symbole für die Kabinenanzeige erzeugen - ohne Fremdpakete.

Android verlangt für ein Symbol auf dem Startbildschirm echte PNG-Dateien in
mindestens 192 und 512 Pixeln. Ein Zeichenprogramm dafür vorauszusetzen wäre
gegen die Linie dieses Projekts: der Kern rechnet ohne Fremdpakete, und ein
Symbol ist nun wirklich kein Grund, davon abzuweichen. PNG ist mit ``zlib`` aus
der Standardbibliothek in wenigen Zeilen geschrieben.

Das Bild ist bewusst dasselbe wie das Kartenbild in der Kabine: dunkler Grund,
grüne Spur, weißes Fahrzeugdreieck. Wer die Kachel auf dem Tablet sieht, hat
die Anzeige schon einmal gesehen.

    python3 scripts/make_icons.py

Schreibt frontend/icon-192.png, frontend/icon-512.png und
frontend/icon-maskable-512.png (letzteres mit Rand, damit Android es
beschneiden darf, ohne das Fahrzeug abzuschneiden).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ZIEL = Path(__file__).resolve().parents[1] / "frontend"

GRUND = (0x0D, 0x11, 0x17)      # --bg
KACHEL = (0x16, 0x1B, 0x22)     # --panel
SPUR = (0x3F, 0xB9, 0x50)       # --green-bright
FAHRZEUG = (0xE6, 0xED, 0xF3)   # --text


def schreibe_png(pfad: Path, breite: int, hoehe: int, pixel: list[list[tuple]]) -> None:
    """Ein PNG in Wahrfarben schreiben (8 Bit je Kanal, ohne Filter)."""
    roh = bytearray()
    for zeile in pixel:
        roh.append(0)                      # Filtertyp 0 = keiner
        for r, g, b in zeile:
            roh += bytes((r, g, b))

    def block(kennung: bytes, inhalt: bytes) -> bytes:
        return (struct.pack(">I", len(inhalt)) + kennung + inhalt
                + struct.pack(">I", zlib.crc32(kennung + inhalt) & 0xFFFFFFFF))

    kopf = struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0)
    pfad.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + block(b"IHDR", kopf)
        + block(b"IDAT", zlib.compress(bytes(roh), 9))
        + block(b"IEND", b"")
    )


def zeichne(groesse: int, rand: float = 0.0) -> list[list[tuple]]:
    """Das Kartenbild als Kachel. ``rand`` hält Platz für Androids Beschnitt frei."""
    bild = [[GRUND for _ in range(groesse)] for _ in range(groesse)]
    innen = rand * groesse
    ecke = groesse * 0.18                      # abgerundete Ecken der Kachel

    for y in range(groesse):
        for x in range(groesse):
            if not (innen <= x < groesse - innen and innen <= y < groesse - innen):
                continue
            # Ecken abrunden: Abstand zum nächsten Eckmittelpunkt
            dx = min(x - innen, (groesse - innen - 1) - x)
            dy = min(y - innen, (groesse - innen - 1) - y)
            if dx < ecke and dy < ecke:
                if ((ecke - dx) ** 2 + (ecke - dy) ** 2) > ecke ** 2:
                    continue
            bild[y][x] = KACHEL

    mitte = groesse / 2.0
    breite_spur = max(1.0, groesse * 0.035)
    oben = innen + groesse * 0.12
    unten = groesse - innen - groesse * 0.12
    for y in range(groesse):
        if not oben <= y <= unten:
            continue
        for x in range(groesse):
            if abs(x - mitte) <= breite_spur / 2.0:
                bild[y][x] = SPUR

    # Fahrzeugdreieck, Spitze nach oben - wie im Kartenbild.
    spitze_y = mitte - groesse * 0.20
    basis_y = mitte + groesse * 0.16
    halb = groesse * 0.17
    for y in range(int(spitze_y), int(basis_y) + 1):
        if not 0 <= y < groesse:
            continue
        anteil = (y - spitze_y) / max(1e-6, basis_y - spitze_y)
        weite = halb * anteil
        for x in range(int(mitte - weite), int(mitte + weite) + 1):
            if 0 <= x < groesse:
                bild[y][x] = FAHRZEUG
    return bild


def main() -> None:
    ZIEL.mkdir(parents=True, exist_ok=True)
    for name, groesse, rand in (
        ("icon-192.png", 192, 0.0),
        ("icon-512.png", 512, 0.0),
        # Maskierbar: Android darf bis zu 20 % ringsum abschneiden.
        ("icon-maskable-512.png", 512, 0.12),
    ):
        pfad = ZIEL / name
        schreibe_png(pfad, groesse, groesse, zeichne(groesse, rand))
        print(f"{pfad.name}: {pfad.stat().st_size} Bytes")


if __name__ == "__main__":
    main()
