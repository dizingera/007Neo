"""AgriPilot – GPS-Spurführung für Traktoren.

Die Module lassen sich einzeln benutzen und prüfen:

    geo        Projektion in lokale Meter, Flächen, Strecken
    nmea       NMEA-0183-Auswertung inklusive Fix-Qualität
    gnss       Empfänger (seriell, TCP, UDP) und der Traktor-Simulator
    ntrip      RTK-Korrekturen vom Caster und deren Weitergabe
    guidance   AB-Linien, Kurven, Abweichung, Lenkwinkel
    coverage   Bearbeitete Fläche als Raster, Überlappung, Sektionen
    steering   Lenkbefehl mit allen Sicherheitsbedingungen
    storage    SQLite-Ablage
    sync       Abgleich zwischen Master und Traktoren
    export     GPX, GeoJSON, CSV
    engine     führt alles zusammen
    server     Weboberfläche und Schnittstelle
"""

__version__ = "1.0.0"
