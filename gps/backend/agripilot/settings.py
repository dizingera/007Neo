"""Einstellungen: alles, was bisher nur in der Konfigurationsdatei stand.

Die Konfiguration war von Anfang an vollständig - aber nur als Datei. Wer den
Empfängerport ändern, die Basis eintragen oder den Lenkmotor abstimmen wollte,
musste ``config.yaml`` in einem Texteditor öffnen. Auf einem Tablet in der
Werkstatt, mit Handschuhen, ist das keine Bedienung, sondern eine Hürde.

Diese Datei beschreibt jede Einstellung einmal: Beschriftung, Einheit, Bereich,
Erklärung - und vor allem, **wann sie wirkt**. Das ist der Teil, den eine
Oberfläche üblicherweise verschweigt und der im Feld Zeit kostet: wer den
Empfängerport ändert und sich wundert, dass nichts passiert, sucht am falschen
Ende. Deshalb steht an jedem Feld, ob es sofort greift oder erst nach einem
Neustart, und die Oberfläche sagt es hin.

Warum eine Tabelle statt handgeschriebener Formulare: es sind rund siebzig
Werte. Eine Tabelle bleibt mit dem Datenmodell in einer Datei zusammen, lässt
sich prüfen, und die Oberfläche kann daraus nicht auseinanderlaufen.

Grenzen kommen aus der Sache, nicht aus dem Bauchgefühl: ein Lenkeinschlag über
dem mechanischen Anschlag ist nicht "mutig", sondern beschädigt die Lenkung; ein
Stromgrenzwert, bei dem das Lenkrad nicht mehr von Hand zu übersteuern ist, ist
kein gültiger Wert für dieses System.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Sinnbild für ein gespeichertes Passwort. Das echte verlässt den Rechner nie;
# kommt dieser Wert zurück, bleibt das gespeicherte Passwort stehen.
PASSWORT_PLATZHALTER = "***"


@dataclass
class Feld:
    """Eine Einstellung, so wie sie in der Oberfläche erscheint."""

    schluessel: str              # "gnss.port"
    label: str
    typ: str = "text"            # text | zahl | ganzzahl | schalter | auswahl | passwort
    einheit: str = ""
    auswahl: tuple[tuple[str, str], ...] = ()   # (Wert, Beschriftung)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    hilfe: str = ""
    sofort: bool = False         # wirkt ohne Neustart
    warnung: str = ""            # roter Hinweis in der Oberfläche
    als_zahl: bool = False       # Auswahl, die im Datenmodell eine Zahl ist

    @property
    def abschnitt(self) -> str:
        return self.schluessel.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.schluessel.split(".", 1)[1]

    def to_dict(self) -> dict:
        return {
            "schluessel": self.schluessel,
            "label": self.label,
            "typ": self.typ,
            "einheit": self.einheit,
            "auswahl": [{"wert": w, "label": t} for w, t in self.auswahl],
            "minimum": self.minimum,
            "maximum": self.maximum,
            "hilfe": self.hilfe,
            "sofort": self.sofort,
            "warnung": self.warnung,
        }


@dataclass
class Gruppe:
    id: str
    titel: str
    hinweis: str = ""
    felder: list[Feld] = field(default_factory=list)


GRUPPEN: list[Gruppe] = [
    Gruppe(
        id="gnss",
        titel="Empfänger",
        hinweis="Woher die Position kommt. Ohne Konfigurationsdatei läuft das "
                "System im Simulator - ein virtueller Traktor mit RTK-Fix.",
        felder=[
            Feld("gnss.source", "Quelle", "auswahl", auswahl=(
                ("serial", "Seriell/USB (F9P am Rechner)"),
                ("tcp", "TCP (Empfänger im Netz)"),
                ("udp", "UDP (Empfänger im Netz)"),
                ("simulator", "Simulator (keine Hardware)"),
                ("replay", "Aufzeichnung abspielen (Fehlersuche)"),
            ), hilfe="Der übliche Fall ist ein F9P per USB: seriell. "
                     "'Aufzeichnung abspielen' fährt eine mitgeschriebene Fahrt "
                     "noch einmal - zum Untersuchen am Schreibtisch, nicht zum "
                     "Arbeiten auf dem Feld."),
            Feld("gnss.port", "Anschluss", hilfe="Windows z. B. COM3, Linux /dev/ttyACM0. "
                                                 "Die Geräteerkennung findet ihn."),
            Feld("gnss.baudrate", "Baudrate", "ganzzahl", minimum=4800, maximum=921600,
                 hilfe="Beim F9P mit 10 Hz gehören 115200 dazu - langsamer reißen die Sätze ab."),
            Feld("gnss.host", "Adresse", hilfe="Nur bei TCP/UDP."),
            Feld("gnss.tcp_port", "Port", "ganzzahl", minimum=1, maximum=65535,
                 hilfe="Nur bei TCP/UDP."),
            Feld("gnss.rtcm_out", "Korrekturen zum Empfänger", hilfe=
                 "Wohin die RTK-Korrekturen zurückgeschrieben werden. 'auto' nimmt "
                 "denselben Anschluss; leer lassen heißt: gar nicht einspeisen."),
            Feld("gnss.replay_file", "Abzuspielende Aufzeichnung", hilfe=
                 "Nur beim Abspielen. Ein bloßer Dateiname meint eine eigene "
                 "Aufzeichnung (Menü → System → Rohdaten); ein vollständiger Pfad "
                 "wird genommen, wie er dasteht."),
            Feld("gnss.replay_speed", "Abspieltempo", "zahl", einheit="×",
                 minimum=0.05, maximum=20.0, hilfe=
                 "1,0 spielt die Fahrt in ihrem eigenen Takt ab - nur dort läuft "
                 "alles so wie im Feld. Schneller ist zum Durchspulen gedacht: "
                 "die Positionen rücken vor, während in den Sätzen weiterhin die "
                 "aufgezeichnete Geschwindigkeit steht."),
            Feld("gnss.replay_loop", "Aufzeichnung wiederholen", "schalter",
                 hilfe="Am Ende wieder von vorn, statt stehen zu bleiben."),
        ],
    ),
    Gruppe(
        id="corrections",
        titel="Korrekturdaten (RTK)",
        hinweis="Zentimeter gibt es nur mit Korrekturen von einer Basis. Der Weg "
                "entscheidet, was einzutragen ist. Hängt das Funkmodem unmittelbar "
                "am Empfänger, gehört hier 'aus' hin - dann sieht die Software den "
                "Strom gar nicht, und das ist richtig so.",
        felder=[
            Feld("corrections.source", "Weg zur Basis", "auswahl", auswahl=(
                ("ntrip", "Caster (NTRIP, z. B. RTKBase oder ein Dienst)"),
                ("tcp", "Roher RTCM3-Strom auf einem Port"),
                ("serial", "Funkmodem am Rechner"),
                ("aus", "Aus (Funkmodem hängt direkt am Empfänger)"),
            )),
            Feld("corrections.host", "Adresse", hilfe="Bei NTRIP und TCP: die eigene Basis "
                                                      "oder der Caster."),
            Feld("corrections.port", "Port", "ganzzahl", minimum=1, maximum=65535,
                 hilfe="NTRIP meist 2101; bei rohem Strom, was die Basis öffnet."),
            Feld("corrections.mountpoint", "Mountpoint", hilfe="Nur bei NTRIP."),
            Feld("corrections.username", "Benutzer", hilfe="Nur bei NTRIP mit Anmeldung."),
            Feld("corrections.password", "Passwort", "passwort",
                 hilfe="Wird gespeichert, aber nie zurückgegeben."),
            Feld("corrections.serial_port", "Anschluss des Funkmodems",
                 hilfe="Nur bei 'Funkmodem am Rechner', z. B. COM4."),
            Feld("corrections.baudrate", "Baudrate des Funkmodems", "ganzzahl",
                 minimum=1200, maximum=921600),
            Feld("corrections.send_gga", "Eigene Position zum Caster senden", "schalter",
                 hilfe="Netz-RTK (VRS) braucht das, sonst kommt nichts. Eine einzelne "
                       "eigene Basis braucht es nicht."),
            Feld("corrections.gga_interval_s", "Abstand der Positionsmeldung", "ganzzahl",
                 einheit="s", minimum=1, maximum=120),
        ],
    ),
    Gruppe(
        id="imu",
        titel="Neigungssensor",
        hinweis="Die Antenne sitzt drei Meter über dem Boden. Steht der Traktor "
                "schräg, steht sie neben dem Punkt, den sie zu messen glaubt - "
                "bei 4° Seitenhang sind das 21 cm. Ohne Ausgleich wandert die Spur, "
                "ohne dass der Empfänger etwas davon merkt.",
        felder=[
            Feld("imu.source", "Sensor", "auswahl", auswahl=(
                ("aus", "Keiner"),
                ("tinkerforge", "Tinkerforge IMU Brick"),
                ("simulator", "Simulator"),
            )),
            Feld("imu.host", "Brick Daemon", hilfe="Rechner, auf dem brickd läuft."),
            Feld("imu.port", "Port des Brick Daemon", "ganzzahl", minimum=1, maximum=65535),
            Feld("imu.uid", "UID des Bricks", hilfe="Leer = erstes gefundenes Gerät."),
            Feld("imu.axis_map", "Einbaulage", "auswahl", auswahl=(
                ("standard", "Standard"),
                ("swapped", "Achsen vertauscht (quer eingebaut)"),
                ("inverted", "Umgedreht"),
                ("swapped_inverted", "Vertauscht und umgedreht"),
            ), hilfe="Zeigt die Anzeige Nicken statt Neigung, ist der Sensor quer eingebaut."),
            Feld("imu.roll_sign", "Vorzeichen des Hangausgleichs", "auswahl", auswahl=(
                ("1.0", "Normal (+1)"),
                ("-1.0", "Umgekehrt (−1)"),
            ), als_zahl=True, sofort=True, hilfe=
                 "Durch Fahren prüfen, nicht durch Ansehen: über eine Furche fahren. "
                 "Bleibt die Abweichung ruhig, stimmt es. Wird sie beim Kippeln größer, "
                 "gehört hier −1 hin. Ein falsches Vorzeichen verdoppelt den Fehler, "
                 "statt ihn aufzuheben."),
            Feld("imu.terrain_compensation", "Hangausgleich aktiv", "schalter", sofort=True),
            Feld("imu.use_for_heading", "Kurs vom Sensor im Stand", "schalter", sofort=True,
                 hilfe="Im Stand und im Schritttempo liefert GPS keinen brauchbaren Kurs."),
        ],
    ),
    Gruppe(
        id="steering",
        titel="Lenkautomatik",
        hinweis="Die Freigabe hier ersetzt weder Not-Aus noch Fahrer auf dem Sitz. "
                "Auf öffentlichen Straßen hat die Lenkautomatik nichts zu suchen.",
        felder=[
            Feld("steering.enabled", "Lenkung freigegeben", "schalter", sofort=True,
                 warnung="Nur mit eingebautem Lenkmotor, Not-Aus in der Motorleitung "
                         "und abgeschlossener Inbetriebnahme.",
                 hilfe="Ohne Freigabe rechnet das System mit und zeigt an, bewegt aber nichts."),
            Feld("steering.output", "Ausgang", "auswahl", auswahl=(
                ("none", "Nur Anzeige (bewegt nichts)"),
                ("phidget", "Phidget-Motorsteuerung"),
                ("udp", "Externe Lenkplatine über UDP"),
            )),
            Feld("steering.host", "Adresse der Lenkplatine", hilfe="Nur bei UDP."),
            Feld("steering.port", "Port der Lenkplatine", "ganzzahl", minimum=1, maximum=65535),
            Feld("steering.require_rtk", "Nur mit RTK-Fix lenken", "schalter", sofort=True,
                 hilfe="'RTK float' ist nicht 'RTK fix' - Float springt um Dezimeter."),
            Feld("steering.min_speed_ms", "Mindestgeschwindigkeit", "zahl", einheit="m/s",
                 minimum=0.0, maximum=3.0, sofort=True,
                 hilfe="Darunter wird nicht gelenkt: im Stand ist der Kurs aus GPS wertlos."),
            Feld("steering.max_speed_ms", "Höchstgeschwindigkeit", "zahl", einheit="m/s",
                 minimum=1.0, maximum=15.0, sofort=True),
            Feld("steering.max_cross_track_m", "Größte Abweichung zur Spur", "zahl",
                 einheit="m", minimum=0.1, maximum=10.0, sofort=True,
                 hilfe="Weiter entfernt gibt die Automatik ab, statt quer aufs Feld zu ziehen."),
            Feld("steering.watchdog_ms", "Wachhund", "ganzzahl", einheit="ms",
                 minimum=100, maximum=5000, sofort=True,
                 hilfe="Ohne frischen Befehl in dieser Zeit stellt die Platine gerade."),
        ],
    ),
    Gruppe(
        id="phidget",
        titel="Lenkmotor (Phidget)",
        hinweis="Erst nach dem Einbau abstimmen, und immer nur einen Wert je Fahrt "
                "ändern. Die Stromgrenze bleibt niedrig: das Lenkrad muss von Hand "
                "zu übersteuern sein.",
        felder=[
            Feld("phidget.serial_number", "Seriennummer", "ganzzahl", minimum=-1,
                 hilfe="−1 = erstes gefundenes Gerät."),
            Feld("phidget.motor_channel", "Motorkanal", "ganzzahl", minimum=0, maximum=7),
            Feld("phidget.invert_motor", "Drehrichtung umkehren", "schalter",
                 hilfe="Wenn die Lenkung in die falsche Richtung zieht."),
            Feld("phidget.control", "Regelungsart", "auswahl", auswahl=(
                ("position", "Positionsregler der Platine (empfohlen)"),
                ("velocity", "Eigener Regelkreis in der Software"),
            ), hilfe="Der Positionsregler läuft in der Firmware und hängt damit weder "
                     "an den zehn Positionen je Sekunde noch an der Laufzeit des Programms."),
            Feld("phidget.feedback", "Rückmeldung", "auswahl", auswahl=(
                ("was", "Radwinkelsensor (die saubere Lösung)"),
                ("yaw_rate", "Drehrate vom Neigungssensor"),
                ("encoder", "Drehgeber am Motor"),
            ), hilfe="Nur beim eigenen Regelkreis. Ohne Radwinkelsensor sieht das "
                     "Programm einen Fahrereingriff nicht - dann sind niedrige "
                     "Stromgrenze und Not-Aus Bedingung, keine Empfehlung."),
            Feld("phidget.counts_per_deg", "Zählwerte je Grad", "zahl", minimum=0.1,
                 maximum=10000.0,
                 hilfe="Zählwerte des Drehgebers je Grad Einschlag der Räder AM BODEN. "
                       "Mit scripts/measure_steering.py ausmessen, nicht schätzen."),
            Feld("phidget.max_wheel_angle_deg", "Größter Radeinschlag", "zahl", einheit="°",
                 minimum=5.0, maximum=60.0,
                 hilfe="Der mechanische Anschlag. Harte Grenze - höher stellen beschädigt "
                       "die Lenkung."),
            Feld("phidget.dead_band_deg", "Totband", "zahl", einheit="°",
                 minimum=0.0, maximum=5.0, hilfe="Darunter wird nicht nachgeregelt."),
            Feld("phidget.current_limit_a", "Stromgrenze", "zahl", einheit="A",
                 minimum=0.1, maximum=8.0,
                 warnung="Niedrig halten - das Lenkrad muss von Hand übersteuerbar bleiben.",
                 hilfe="Die dritte Rückfallebene neben Not-Aus und Wachhund."),
            Feld("phidget.max_duty", "Größte Leistung", "zahl", minimum=0.05, maximum=1.0,
                 hilfe="Anteil der vollen Motorleistung."),
            Feld("phidget.acceleration", "Beschleunigung", "zahl", minimum=0.1, maximum=100.0),
            Feld("phidget.failsafe_ms", "Failsafe der Platine", "ganzzahl", einheit="ms",
                 minimum=100, maximum=5000,
                 hilfe="Die Platine hält den Motor selbst an, wenn das Programm verstummt - "
                       "abgestürztes Tablet, abgezogenes USB-Kabel."),
            Feld("phidget.override_deg", "Eingriffserkennung", "zahl", einheit="°",
                 minimum=0.5, maximum=20.0,
                 hilfe="Dauerabweichung bei hoher Leistung: Fahrer greift ein oder die "
                       "Lenkung blockiert - dann wird abgegeben."),
            Feld("phidget.position_kp", "Positionsregler P", "zahl", minimum=0.0),
            Feld("phidget.position_ki", "Positionsregler I", "zahl", minimum=0.0,
                 hilfe="Zunächst klein lassen: I kann aufschaukeln."),
            Feld("phidget.position_kd", "Positionsregler D", "zahl", minimum=0.0),
            Feld("phidget.was_channel", "Kanal des Radwinkelsensors", "ganzzahl",
                 minimum=0, maximum=7),
            Feld("phidget.was_centre_ratio", "Radwinkelsensor: Geradeaus", "zahl",
                 minimum=0.0, maximum=1.0, hilfe="Spannungsverhältnis bei geraden Rädern."),
            Feld("phidget.was_deg_per_ratio", "Radwinkelsensor: Grad je Einheit", "zahl",
                 minimum=1.0, maximum=1000.0),
            Feld("phidget.was_invert", "Radwinkelsensor umkehren", "schalter"),
            Feld("phidget.encoder_channel", "Kanal des Drehgebers", "ganzzahl",
                 minimum=0, maximum=7),
            Feld("phidget.encoder_counts_per_deg", "Drehgeber: Zählwerte je Grad", "zahl",
                 minimum=0.1, maximum=10000.0),
            Feld("phidget.gain_p", "Eigener Regler P", "zahl", minimum=0.0, maximum=10.0),
            Feld("phidget.gain_i", "Eigener Regler I", "zahl", minimum=0.0, maximum=10.0),
            Feld("phidget.gain_d", "Eigener Regler D", "zahl", minimum=0.0, maximum=10.0),
        ],
    ),
    Gruppe(
        id="network",
        titel="Netzwerk und Abgleich",
        hinweis="Der Master hält die eine Verbindung zur Basis und gibt den "
                "Korrekturstrom an die anderen Traktoren weiter - die brauchen "
                "dann weder Mobilfunk noch ein zweites Konto.",
        felder=[
            Feld("network.role", "Rolle", "auswahl", auswahl=(
                ("master", "Master (hält die Verbindung zur Basis)"),
                ("client", "Client (holt sich alles vom Master)"),
            )),
            Feld("network.device_name", "Name dieses Geräts",
                 hilfe="Erscheint in der Geräteliste und an abgehakten Einbauschritten."),
            Feld("network.master_url", "Adresse des Masters", hilfe="Nur beim Client."),
            Feld("network.sync_interval_s", "Abgleich alle", "ganzzahl", einheit="s",
                 minimum=5, maximum=3600),
            Feld("network.rtcm_relay_port", "Port der Korrektur-Weitergabe", "ganzzahl",
                 minimum=1, maximum=65535),
            Feld("network.use_master_rtcm", "Korrekturen vom Master holen", "schalter",
                 hilfe="Nur beim Client."),
        ],
    ),
    Gruppe(
        id="server",
        titel="Anzeige und Daten",
        felder=[
            Feld("server.host", "Adresse des Servers",
                 hilfe="0.0.0.0 = von jedem Tablet im Netz erreichbar."),
            Feld("server.port", "Port", "ganzzahl", minimum=1, maximum=65535),
            Feld("server.update_hz", "Bildrate der Anzeige", "zahl", einheit="Hz",
                 minimum=1.0, maximum=25.0,
                 hilfe="Der Empfänger liefert zehnmal je Sekunde; mehr bringt nichts."),
            Feld("server.data_dir", "Datenordner",
                 hilfe="Hier liegt die Datenbank. Änderung heißt: neue, leere Datenbank."),
            Feld("server.tls_cert", "Zertifikat (HTTPS)",
                 hilfe="Beide Pfade gesetzt = verschlüsselte Verbindung. Ein "
                       "Android-Tablet bekommt Kachel, Zwischenspeicher und "
                       "Bildschirm-Wachhalten nur so. Papiere erzeugt "
                       "scripts/make_cert.sh auf dem Pi."),
            Feld("server.tls_key", "Schlüssel (HTTPS)"),
        ],
    ),
]

ALLE_FELDER: dict[str, Feld] = {
    feld.schluessel: feld for gruppe in GRUPPEN for feld in gruppe.felder
}


class EinstellungsFehler(ValueError):
    """Ein Wert, der so nicht gespeichert wird - mit Begründung im Klartext."""


def schema() -> list[dict]:
    """Die Beschreibung aller Einstellungen für die Oberfläche."""
    return [
        {
            "id": gruppe.id,
            "titel": gruppe.titel,
            "hinweis": gruppe.hinweis,
            "felder": [feld.to_dict() for feld in gruppe.felder],
        }
        for gruppe in GRUPPEN
    ]


def werte(config) -> dict[str, Any]:
    """Der aktuelle Stand, so wie die Oberfläche ihn anzeigt."""
    ausgabe: dict[str, Any] = {}
    for schluessel, feld in ALLE_FELDER.items():
        wert = getattr(getattr(config, feld.abschnitt), feld.name)
        if feld.typ == "passwort":
            # Das Passwort verlässt den Rechner nicht. Die Oberfläche erfährt
            # nur, ob eins gesetzt ist.
            ausgabe[schluessel] = PASSWORT_PLATZHALTER if wert else ""
        elif feld.typ == "auswahl":
            ausgabe[schluessel] = str(wert)
        else:
            ausgabe[schluessel] = wert
    return ausgabe


def _pruefe(feld: Feld, roh: Any) -> Any:
    """Einen einzelnen Wert prüfen und in den richtigen Typ bringen."""
    if feld.typ == "schalter":
        if isinstance(roh, bool):
            return roh
        if isinstance(roh, str):
            return roh.strip().lower() in ("1", "true", "ja", "an", "on")
        return bool(roh)

    if feld.typ in ("zahl", "ganzzahl"):
        try:
            zahl = float(roh)
        except (TypeError, ValueError):
            raise EinstellungsFehler(f"{feld.label}: '{roh}' ist keine Zahl") from None
        if zahl != zahl or zahl in (float("inf"), float("-inf")):
            raise EinstellungsFehler(f"{feld.label}: keine gültige Zahl")
        if feld.minimum is not None and zahl < feld.minimum:
            raise EinstellungsFehler(
                f"{feld.label}: {zahl:g} ist kleiner als {feld.minimum:g}"
                + (f" {feld.einheit}" if feld.einheit else ""))
        if feld.maximum is not None and zahl > feld.maximum:
            raise EinstellungsFehler(
                f"{feld.label}: {zahl:g} ist größer als {feld.maximum:g}"
                + (f" {feld.einheit}" if feld.einheit else ""))
        return int(zahl) if feld.typ == "ganzzahl" else zahl

    if feld.typ == "auswahl":
        erlaubt = [wert for wert, _ in feld.auswahl]
        text = str(roh)
        if text not in erlaubt:
            raise EinstellungsFehler(
                f"{feld.label}: '{text}' gibt es nicht ({', '.join(erlaubt)})")
        # Manche Auswahl ist im Datenmodell eine Zahl - roll_sign etwa.
        return float(text) if feld.als_zahl else text

    return str(roh)


def uebernehmen(config, aenderungen: dict, lenkung_freigabe_erlaubt: bool = True) -> dict:
    """Geänderte Werte prüfen, setzen und die Datei schreiben.

    Erst wird alles geprüft, dann wird gesetzt: eine halb übernommene
    Konfiguration wäre schlimmer als eine abgelehnte.
    """
    geprueft: dict[str, Any] = {}
    for schluessel, roh in aenderungen.items():
        feld = ALLE_FELDER.get(schluessel)
        if feld is None:
            raise EinstellungsFehler(f"Unbekannte Einstellung: {schluessel}")
        if feld.typ == "passwort" and roh == PASSWORT_PLATZHALTER:
            continue    # unverändert - das gespeicherte Passwort bleibt stehen
        geprueft[schluessel] = _pruefe(feld, roh)

    if geprueft.get("steering.enabled") and not config.steering.enabled:
        # Die eine Einstellung, die eine Maschine in Bewegung setzt. Sie hängt
        # an der Inbetriebnahme, damit sie nicht nebenbei umgelegt wird.
        if not lenkung_freigabe_erlaubt:
            raise EinstellungsFehler(
                "Lenkung freigeben: der Einbau ist noch nicht abgeschlossen. "
                "Menü → Einbau, Schritt 'Lenkmotor anbauen' abhaken.")

    neustart: list[str] = []
    for schluessel, wert in geprueft.items():
        feld = ALLE_FELDER[schluessel]
        abschnitt = getattr(config, feld.abschnitt)
        if getattr(abschnitt, feld.name) == wert:
            continue
        setattr(abschnitt, feld.name, wert)
        if not feld.sofort:
            neustart.append(feld.label)

    pfad = config.save()
    return {
        "gespeichert": len(geprueft),
        "datei": str(pfad),
        "neustart_noetig": neustart,
    }
