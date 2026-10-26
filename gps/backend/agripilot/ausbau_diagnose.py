"""Beobachtende Diagnose; Integration und Routen ausschließlich durch Main.

beobachten() höchstens im Server-Hintergrund, niemals im Lenkregelkreis.
Keine Motorfreigabe, keine Einstellungsänderung und keine Fahrbarkeitsfreigabe.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
import threading
import time

from . import planpruefung
from .storage import Storage

ETAPPEN = ("gerade", "langsam", "arbeitsgeschwindigkeit", "hang", "wiederanfahrt")
ETAPPEN_TEXTE = {"gerade": "Geradeausfahrt", "langsam": "Langsame Fahrt (2,88–7,2 km/h)",
    "arbeitsgeschwindigkeit": "Fahrt mit Arbeitsgeschwindigkeit", "hang": "Fahrt am geeigneten Hang",
    "wiederanfahrt": "Stabile Fahrt nach Wiederanfahrt"}
KANAL_TEXTE = {"gps": "GPS-Position", "rtk": "RTK-Fix", "imu": "Neigungssensor (IMU)",
               "master": "Abgleich mit dem Master"}
VORSCHRIFTEN = [
    "Nur durch den Fahrer beaufsichtigt auf freier, geeigneter Fläche prüfen.",
    "Lenkung und Notabschaltung zuvor prüfen; keine Personen im Fahrbereich.",
    "Dasselbe Gespann, dieselbe gerade Spur und Fahrtrichtung für A/B verwenden.",
    "30 Sekunden gleichmäßig mit RTK; vorhandene Probefahrt-Grenzen bleiben aktiv.",
    "Hang nur innerhalb der zulässigen Fahrzeuggrenzen; keine riskante Schrägfahrt erzwingen.",
    "Wiederanfahrt: nach manuellem Anfahren den stabilen Abschnitt messen; keine Anfahrdynamik-Abnahme.",
]
KEY = "ausbau_diagnose16"
MAX_FELDER = 500
MAX_KANTEN = 500
MAX_BERICHTE = 50
MAX_PLAENE_PRO_FELD = 1000


def _zahl(x):
    try:
        return type(x) in (int, float) and math.isfinite(x)
    except (OverflowError, ValueError):
        return False


def _daten(x):
    return asdict(x) if is_dataclass(x) else dict(x or {})


def _relevant(x):
    # Strikte Wertauswahl: keine freien Fehlertexte, URLs oder Geheimnisse speichern.
    return {k: v for k, v in _daten(x).items()
            if not any(w in k.lower() for w in ("token", "pass", "secret", "host", "name", "port", "uid", "bibliothek"))
            and (type(v) is bool or _zahl(v) or (k in ("regelart", "bauform", "feedback", "control", "source", "axis_map", "output") and isinstance(v, str)))}


class DiagnoseService:
    def __init__(self, app):
        self.app = app
        self.engine = app.engine
        self.store = app.store
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._geschlossen = False
        self._dirty = False
        self._flush_zeit = time.monotonic()
        stand = self.store.get_setting(KEY, {})
        self._stand = deepcopy(stand) if isinstance(stand, dict) else {}
        self._stand.setdefault("fahrten", [])
        self._stand.setdefault("kanten", [])
        self._aktiv = None  # Neustart setzt keine Messung fort.
        self._standzeit = None
        self._letzte = {}   # Ausfall während Neustart bleibt unbekannt.
        self._job = {"status": "bereit", "gesamt": 0, "fertig": 0, "felder": [],
                     "hinweis": "Nur lokale Geometrieplausibilität; keine vollständige Fahrbarkeit."}

    def _uebung(self):
        cfg = self.app.config
        return (getattr(self.engine, "simulator", None) is not None
                or cfg.gnss.source in ("simulator", "replay", "aus")
                or cfg.imu.source == "simulator" or cfg.steering.output == "none"
                or cfg.network.device_id == "uebung-lokal"
                or bool(getattr(self.engine, "steering", None)
                        and self.engine.steering.status().get("simulator")))

    def _kontext(self):
        cfg = self.app.config
        profil = _relevant(self.engine.profile)
        # Reine Lack-/Anzeigeänderungen machen eine Prüfung nicht ungültig.
        for k in ("spurweite_m",):
            profil.pop(k, None)
        offsets = self.store.get_setting("imu_offsets", {}) or {}
        traktor = (self.engine.aktiver_traktor()["id"] if hasattr(self.engine, "aktiver_traktor")
                   else self.store.get_setting("traktor_aktiv"))
        return {"traktor": traktor,
                "geraet": self.store.get_setting("vehicle_profile_active"),
                "profil": profil, "motor": _relevant(cfg.phidget),
                "lenkung": _relevant(cfg.steering), "imu": _relevant(cfg.imu),
                "kalibrierung": {k: offsets.get(k) for k in ("roll_offset", "pitch_offset")},
                "geraete": {"gnss": cfg.gnss.source, "gnss_port": cfg.gnss.port,
                            "gnss_host": cfg.gnss.host, "gnss_tcp_port": cfg.gnss.tcp_port,
                            "lenkung_host": cfg.steering.host, "lenkung_port": cfg.steering.port,
                            "imu_host": cfg.imu.host, "imu_port": cfg.imu.port,
                            "motor_bibliothek": cfg.phidget.bibliothek, "imu_uid": cfg.imu.uid,
                            "motor_serial": cfg.phidget.serial_number,
                            "motor_kanal": cfg.phidget.motor_channel,
                            "geraet_id": cfg.network.device_id}}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self._kontext(), sort_keys=True,
                              ensure_ascii=True, allow_nan=False).encode()).hexdigest()

    def fahrserie_status(self):
        with self._lock:
            fp = self.fingerprint()
            fahrten = deepcopy(self._stand["fahrten"][-MAX_BERICHTE:])
            return {"etappen": list(ETAPPEN), "etappen_info": [{"id": e, "text": ETAPPEN_TEXTE[e]} for e in ETAPPEN], "vorschriften": list(VORSCHRIFTEN),
                    "aktiv": deepcopy(self._aktiv), "letzte_etappe": deepcopy(self._stand.get("letzte_etappe")), "ergebnisse": fahrten,
                    "aktuell": [f["etappe"] for f in fahrten if f["fingerprint"] == fp],
                    "uebung": self._uebung(), "probefahrt": self.engine.probefahrt.status()}

    def fahrserie_start(self, etappe, aktion="vorher", bestaetigt=False):
        with self._lock:
            if self._geschlossen:
                raise RuntimeError("Diagnose beendet")
            if etappe not in ETAPPEN or aktion not in ("vorher", "nachher"):
                raise ValueError("Etappe oder Messphase unbekannt")
            if bestaetigt is not True:
                raise ValueError("Vorschriften vor jeder Messung ausdrücklich bestätigen")
            if self.engine.probefahrt.sammelt:
                raise ValueError("Zuerst laufende Probefahrt beenden")
            fp = self.fingerprint()
            if aktion == "nachher" and (not self._aktiv or self._aktiv["etappe"] != etappe
                    or self._aktiv.get("phase") != "bereit"):
                raise ValueError("Zuerst Vorher-Messung dieser Etappe abschließen")
            if aktion == "nachher":
                vorher = self.engine.probefahrt.status().get("vorher") or {}
                if vorher.get("zeit") != self._aktiv.get("vorher_zeit"):
                    raise ValueError("Vorher-Messung wurde ersetzt; Etappe erneut starten")
            # Ausschließlich bestehender Prüfweg; niemals Probefahrt.bedienen direkt.
            if etappe == "wiederanfahrt":
                fix = getattr(self.engine, "fix", None)
                empfangen = getattr(fix, "received_at", None)
                steht = (fix is not None and _zahl(fix.speed_ms) and abs(fix.speed_ms) <= .15
                         and _zahl(empfangen) and 0 <= time.time()-empfangen <= 1.5)
                if not steht and (self._standzeit is None or time.time()-self._standzeit > 120):
                    raise ValueError("Wiederanfahrt zuerst im beobachteten Stillstand vorbereiten")
            self.engine.probefahrt_bedienen(aktion)
            if aktion == "vorher":
                self._aktiv = {"etappe": etappe, "phase": "vorher", "start": time.time(),
                               "uebung": self._uebung(), "fingerprint_vorher": fp}
            else:
                self._aktiv.update(phase="nachher", fingerprint_nachher=fp,
                                   uebung=self._aktiv["uebung"] or self._uebung())
            return self.fahrserie_status()

    def fahrserie_abbrechen(self):
        with self._lock:
            self.engine.probefahrt_bedienen("abbrechen")
            self._aktiv = None
            return self.fahrserie_status()

    def _fahrt_beobachten(self):
        if not self._aktiv:
            return
        p = self.engine.probefahrt.status()
        a = self._aktiv
        if p["phase"] == "bereit" and a["phase"] == "vorher" and p.get("vorher"):
            a["phase"] = "bereit"
            a["vorher_zeit"] = p["vorher"].get("zeit")
            self._stand["letzte_etappe"] = {**deepcopy(a), "vorher": deepcopy(p["vorher"].get("werte", {}))}
            self._dirty = True
        elif p["phase"] == "bereit" and a["phase"] == "nachher" and p.get("vorher"):
            # Einstellungswechsel brechen Nachher im Messkern ab, erhalten aber
            # Vorher. Die geführte Serie muss den erneuten Nachher-Start erlauben.
            if p["vorher"].get("zeit") == a.get("vorher_zeit"):
                a["phase"] = "bereit"
            else:
                self._aktiv = None
        elif p["phase"] == "fertig" and a["phase"] == "nachher":
            b = p.get("bericht") or {}
            # Niemals einen alten Bericht einer neu gestarteten Messung zuordnen.
            nach = b.get("nachher") or {}
            if not _zahl(nach.get("zeit")) or nach["zeit"] < a["start"]:
                return
            if (b.get("vorher") or {}).get("zeit") != a.get("vorher_zeit"):
                self._aktiv = None
                return
            fp = self.fingerprint()
            if fp != a.get("fingerprint_nachher"):
                self._aktiv = None
                return
            self._stand["fahrten"].append({"etappe": a["etappe"], "zeit": time.time(),
                "fingerprint": fp, "fingerprint_vorher": a["fingerprint_vorher"],
                "uebung": bool(a["uebung"] or self._uebung() or b.get("uebung")),
                "vergleichbar": b.get("vergleichbar") is True,
                "etappe_gueltig": self._etappe_gueltig(a["etappe"], b),
                "vorher": deepcopy((b.get("vorher") or {}).get("werte", {})),
                "nachher": deepcopy(nach.get("werte", {}))})
            self._stand["fahrten"] = self._stand["fahrten"][-MAX_BERICHTE:]
            self._aktiv = None
            self._dirty = True
        elif p["phase"] not in ("vorher", "nachher", "bereit"):
            self._aktiv = None

    @staticmethod
    def _etappe_gueltig(etappe, bericht):
        for key in ("vorher", "nachher"):
            w = (bericht.get(key) or {}).get("werte") or {}
            if not _zahl(w.get("dauer_s")) or w["dauer_s"] < 30 or w.get("proben", 0) < 40:
                return False
            if etappe == "langsam" and not (_zahl(w.get("tempo_kmh")) and 2.88 <= w["tempo_kmh"] <= 7.2):
                return False
            if etappe == "hang" and not (_zahl(w.get("roll_deg")) and abs(w["roll_deg"]) >= 2
                    and _zahl(w.get("imu_anteil")) and w["imu_anteil"] >= .95):
                return False
        return True

    def abnahme_status(self):
        with self._lock:
            fp = self.fingerprint()
            a = deepcopy(self._stand.get("abnahme"))
            fehlend = [e for e in ETAPPEN if not any(f["etappe"] == e
                       and f["fingerprint"] == fp and not f["uebung"] and f["vergleichbar"] and f.get("etappe_gueltig")
                       for f in self._stand["fahrten"])]
            aktuell = bool(a and a["fingerprint"] == fp and not self._uebung())
            return {"status": "geprueft" if aktuell else "veraltet" if a else "ungeprueft",
                    "real_geprueft": aktuell, "abnahme": a, "fehlende_etappen": fehlend,
                    "uebung": self._uebung(),
                    "hinweis": "Fahrerbestätigung am realen Gespann; keine automatische Sicherheitsfreigabe."}

    def abnahme_bestaetigen(self, bestaetigt=False):
        with self._lock:
            if bestaetigt is not True or self._uebung():
                raise ValueError("Abnahme nur ausdrücklich am realen Gespann bestätigen")
            if self._aktiv or self.engine.probefahrt.sammelt:
                raise ValueError("Laufende Messung zuerst beenden")
            status = self.abnahme_status()
            if status["fehlende_etappen"]:
                raise ValueError("Aktuelle reale A/B-Ergebnisse für alle Etappen fehlen")
            self._stand["abnahme"] = {"fingerprint": self.fingerprint(), "zeit": time.time(),
                                       "real_bestaetigt": True}
            self._dirty = True
            self.flush()
            return self.abnahme_status()

    def planpruefung_start(self):
        with self._lock:
            if self._geschlossen:
                raise RuntimeError("Diagnose beendet")
            if self._thread and self._thread.is_alive():
                raise ValueError("Planprüfung läuft bereits")
            self._stop.clear()
            self._job = {"status": "laeuft", "gesamt": 0, "fertig": 0, "felder": [],
                         "hinweis": "Nur gespeicherte lokale Pläne; keine vollständige Fahrbarkeit."}
            self._thread = threading.Thread(target=self._plaene, name="Saatplan-Diagnose", daemon=True)
            self._thread.start()
            return deepcopy(self._job)

    def _plaene(self):
        db = None
        try:
            # Eigene SQLite-Verbindung; keine gleichzeitig benutzte Engine-Verbindung.
            db = Storage(self.store.path, schema=False)
            felder = db.list_fields()
            with self._lock:
                self._job["gesamt"] = min(len(felder), MAX_FELDER)
                self._job["begrenzt"] = len(felder) > MAX_FELDER
            for feld in felder[:MAX_FELDER]:
                if self._stop.is_set():
                    break
                eintrag = {"feld_id": feld["id"], "feld": feld.get("name", ""),
                           "datum": [feld.get("datum_lat"), feld.get("datum_lon")]}
                try:
                    gespeichert = db.get_setting("saatplan:" + feld["id"], {}) or {}
                    kandidaten = []
                    if gespeichert.get("plan"):
                        plan = gespeichert["plan"]
                        vorgabe = gespeichert.get("vorgabe") or {}
                        kandidaten.append(("entwurf", None, plan,
                                           vorgabe.get("breite_m", plan.get("breite_m"))))
                    for linie in db.list_lines(feld["id"]):
                        if linie.get("mode") != "plan":
                            continue
                        if len(kandidaten) > MAX_PLAENE_PRO_FELD:
                            break
                        plan = {"spuren": [], "ringe": []}
                        spuren = linie.get("spuren") or []
                        if len(spuren) > planpruefung.MAX_SEGMENTE:
                            plan["spuren"] = [{}] * (planpruefung.MAX_SEGMENTE + 1)
                        else:
                            for spur in spuren:
                                punkte = spur.get("punkte", [])
                                if len(punkte) > planpruefung.MAX_PUNKTE:
                                    plan["spuren"].append({"stuecke": [None] * (planpruefung.MAX_PUNKTE + 1)})
                                    continue
                                if spur.get("art") == "V" or spur.get("geschlossen"):
                                    plan["ringe"].append({"punkte": punkte})
                                else:
                                    plan["spuren"].append({"stuecke": list(zip(punkte, punkte[1:]))})
                        kandidaten.append((linie["id"], linie.get("saison"), plan, linie.get("spacing_m")))
                    ergebnisse = []
                    for kennung, saison, plan, breite in kandidaten[:MAX_PLAENE_PRO_FELD]:
                        if self._stop.is_set():
                            break
                        ergebnisse.append({"plan_id": kennung, "saison": saison, "breite_m": breite,
                            **planpruefung.pruefen(plan, feld, breite)})
                    if not kandidaten:
                        eintrag.update(status="kein_plan", befunde=[], plaene=[])
                    else:
                        eintrag.update(status="geprueft", plaene=ergebnisse,
                            breite_m=ergebnisse[0]["breite_m"] if ergebnisse else None,
                            befunde=[{**b, "plan_id": r["plan_id"]} for r in ergebnisse for b in r["befunde"]],
                            vollstaendig=(len(ergebnisse) == len(kandidaten)
                                          and all(r["vollstaendig"] for r in ergebnisse)),
                            begrenzt=len(kandidaten) > MAX_PLAENE_PRO_FELD,
                            hinweis="Entwürfe und gespeicherte Planspuren gegen heutige Feldgrenze; keine volle Fahrbarkeit.")
                except Exception:
                    # Keine Ausnahmefreitexte persistieren (mögliche Geheimnisse).
                    eintrag.update(status="fehler", befunde=[], vollstaendig=False,
                                   hinweis="Plan konnte nicht geprüft werden")
                with self._lock:
                    self._job["felder"].append(eintrag)
                    self._job["fertig"] += 1
                self._stop.wait(.01)  # begrenzte Last und kooperatives Abbrechen
            with self._lock:
                self._job["status"] = "abgebrochen" if self._stop.is_set() else "fertig"
        except Exception:
            with self._lock:
                self._job["status"] = "fehler"
                self._job["hinweis"] = "Gespeicherte Felder konnten nicht gelesen werden"
        finally:
            if db is not None:
                db.close()

    def planpruefung_status(self):
        with self._lock:
            return deepcopy(self._job)

    def planpruefung_abbrechen(self):
        self._stop.set()
        return self.planpruefung_status()

    def beobachte(self, state, system=None, jetzt=None):
        jetzt = time.time() if jetzt is None else jetzt
        if not _zahl(jetzt):
            raise ValueError("Ungültiger Beobachtungszeitpunkt")
        with self._lock:
            if self._geschlossen:
                return
            self._fahrt_beobachten()
            system = system or {}
            fix = state.get("fix") or {}
            gnss = system.get("gnss") or {}
            frisch = gnss.get("healthy")
            koordinaten = all(_zahl(fix.get(k)) for k in ("lat", "lon"))
            gueltig = (koordinaten and -90 <= fix["lat"] <= 90 and -180 <= fix["lon"] <= 180
                       and _zahl(fix.get("fix_quality")) and fix["fix_quality"] > 0)
            empfangen = fix.get("received_at")
            alter = fix.get("age_s")
            if _zahl(empfangen) and empfangen > 0:
                alter = jetzt - empfangen
            messung_frisch = (0 <= alter <= 1.5) if _zahl(alter) else None
            gps = (False if frisch is False or messung_frisch is False or not gueltig
                   else True if messung_frisch is True and frisch is True else None)
            if gps is True and _zahl(fix.get("speed_ms")) and abs(fix["speed_ms"]) <= .15:
                self._standzeit = jetzt
            imu = state.get("imu")
            sync = system.get("sync") or {}
            rolle = system.get("role", self.app.config.network.role)
            letzter = sync.get("last_success")
            master = (None if rolle == "master" else
                      bool(0 <= jetzt-letzter <= 30) if _zahl(letzter) and letzter > 0 else False if sync else None)
            werte = {"gps": gps,
                     "rtk": (gps and fix.get("fix_quality") == 4) if gps is not None else None,
                     "imu": (imu.get("fresh") is True and imu.get("healthy") is True) if isinstance(imu, dict) else None,
                     "master": master}
            for kanal, wert in werte.items():
                if kanal not in self._letzte or self._letzte[kanal] != wert:
                    self._stand["kanten"].append({"kanal": kanal, "bezeichnung": KANAL_TEXTE[kanal], "zeit": jetzt,
                        "zustand": "ok" if wert is True else "ausfall" if wert is False else "unbekannt",
                        "uebung": self._uebung(), "neustart": kanal not in self._letzte})
                    self._letzte[kanal] = wert
                    self._dirty = True
            self._stand["kanten"] = self._stand["kanten"][-MAX_KANTEN:]
            if self._dirty and time.monotonic()-self._flush_zeit >= 10:
                self.flush()

    def beobachten(self):
        """Main ruft dies im Server-Hintergrund (1 Hz), nicht im Regelkreis auf."""
        imu = getattr(self.engine, "imu", None)
        sync = getattr(self.app, "sync_client", None)
        quelle = getattr(self.app, "source", None)
        fix = ({**self.engine.fix.to_dict(), "received_at": getattr(self.engine.fix, "received_at", None)}
               if self.engine.fix else None)
        self.beobachte({"fix": fix,
                       "imu": {**imu.attitude.to_dict(), "healthy": imu.healthy} if imu else None},
                      {"gnss": {"healthy": quelle.healthy if quelle else None},
                       "role": self.app.config.network.role,
                       "sync": {"last_success": sync.last_success} if sync else {}})

    def verlauf(self):
        with self._lock:
            return {"kanten": deepcopy(self._stand["kanten"]), "lokal": True,
                    "max_eintraege": MAX_KANTEN,
                    "hinweis": "Beobachtete Zustandswechsel; Neustart- und Beobachtungslücken bleiben unbekannt. Master nach 30 s ohne erfolgreichen Abgleich auffällig."}

    def uebersicht(self):
        return {"fahrserie": self.fahrserie_status(), "abnahme": self.abnahme_status(),
                "plaene": self.planpruefung_status(), "verlauf": self.verlauf()}

    def flush(self):
        with self._lock:
            if self._dirty:
                self.store.set_setting(KEY, deepcopy(self._stand))
                self._dirty = False
                self._flush_zeit = time.monotonic()

    def schliessen(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            self._geschlossen = True
            self.flush()
