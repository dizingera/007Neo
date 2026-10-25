"""Traktorbezogene Lenkwerkstatt: Geometrie, Sicherung und ehrlicher Prüfstand.

Die Werkstatt bewegt keinen Motor. Motor-/Richtungstests bleiben im vorhandenen
Einrichtungsablauf und werden an ihre tatsächlichen Einstellwerte gebunden.
"""
from dataclasses import asdict
import hashlib
import json
import math
import time
import uuid
from .guidance import TRAKTOR_FELDER

GEOMETRIE={"wheelbase_m":(.5,10),"antenna_forward_m":(-10,15),
           "antenna_right_m":(-5,5),"antenna_height_m":(0,6),
           "max_steer_left_deg":(5,85),"max_steer_right_deg":(5,85)}
SCHRITT_FELDER={
    "richtung":("invert_motor","encoder_invert"),
    "mindestleistung":("min_duty","invert_motor"),
    "zaehlwerte":("counts_per_deg","encoder_counts_per_deg","encoder_invert"),
    "feinabstimmung":("min_duty","max_duty","gain_p","gain_i","gain_d","encoder_counts_per_deg"),
    "probefahrt":(),
}

def _hash(daten):
    return hashlib.sha256(json.dumps(daten,sort_keys=True,ensure_ascii=True).encode()).hexdigest()

class KalibrierKonflikt(RuntimeError):pass

class Lenkwerkstatt:
    def __init__(self,engine):self.engine=engine;self.store=engine.store;self.config=engine.config
    def _key(self):return "lenkwerkstatt:"+self.engine.aktiver_traktor()["id"]
    def _lesen(self):return self.store.get_setting(self._key()) or {}
    def _merken(self,daten):self.store.set_setting(self._key(),daten)
    def traktorwerte(self):return {k:getattr(self.engine.profile,k) for k in TRAKTOR_FELDER}
    def geometrie(self):
        werte={k:getattr(self.engine.profile,k) for k in GEOMETRIE}
        for k in ("max_steer_left_deg","max_steer_right_deg"):
            if not werte[k]:werte[k]=self.engine.profile.max_steer_deg
        return werte
    def hardware(self):
        felder=("serial_number","motor_channel","encoder_channel","was_channel","control","feedback","bibliothek")
        return {"ausgang":self.config.steering.output,**{k:getattr(self.config.phidget,k) for k in felder}}
    def kontext(self):
        return _hash({"traktor":self.engine.aktiver_traktor()["id"],"werte":self.traktorwerte(),
                      "motor":asdict(self.config.phidget),"hardware":self.hardware()})
    def schritt_kennung(self,schritt):
        motor={k:getattr(self.config.phidget,k) for k in SCHRITT_FELDER.get(schritt,())}
        return _hash({"hardware":self.hardware(),"motor":motor,
                      "traktor":self.traktorwerte() if schritt=="probefahrt" else None})
    def sperren(self):
        grund=[];fix=self.engine.fix
        if self.config.is_master and self.config.gnss.source not in ("simulator","replay"):
            grund.append("Am Traktor-Tablet einrichten; dieser Rechner ist der Büro-Master")
        if self.engine.job is not None:grund.append("Laufende Arbeit zuerst beenden")
        if self.engine.steering is not None and self.engine.steering.armed:grund.append("Lenkung zuerst ausschalten")
        if fix is None or not fix.valid or time.time()-fix.received_at>1.0:
            grund.append("Auf aktuelle GPS-Position warten, damit Stillstand geprüft werden kann")
        elif abs(fix.speed_ms)>.5:grund.append("Fahrzeug zum Stillstand bringen")
        return grund
    def stand(self,schritte):
        daten=self._lesen();protokoll=daten.get("protokoll")
        if protokoll:protokoll={**protokoll,"aktuell":protokoll["kontext"]==self.kontext()}
        return {"traktor":{k:self.engine.aktiver_traktor()[k] for k in ("id","name")},
                "geraet":self.config.network.device_name,"kontext":self.kontext(),
                "geometrie":self.geometrie(),"sperren":self.sperren(),
                "vermessen":daten.get("geometrie",{}).get("werte")==self.geometrie(),
                "sicherung":({k:daten["sicherung"][k] for k in ("id","zeit")} if daten.get("sicherung") else None),
                "protokoll":protokoll,"schritte":schritte,
                "uebung":self.config.gnss.source in ("simulator","replay")}
    def _sichern(self,daten):
        daten["sicherung"]={"id":uuid.uuid4().hex,"zeit":time.time(),
                            "werte":self.traktorwerte(),"hardware":self.hardware()}
    def aktion(self,payload,schritte):
        if (payload.get("traktor_id")!=self.engine.aktiver_traktor()["id"]
                or payload.get("kontext")!=self.kontext()):
            raise KalibrierKonflikt("Traktor oder Einstellungen geändert. Aktuellen Stand neu laden.")
        gruende=self.sperren()
        if gruende:raise RuntimeError(" · ".join(gruende))
        daten=self._lesen();aktion=payload.get("aktion")
        if aktion=="geometrie":
            roh=payload.get("werte")
            if payload.get("vermessen") is not True:raise ValueError("Am Traktor gemessene Werte bestätigen")
            if not isinstance(roh,dict) or set(roh)!=set(GEOMETRIE):raise ValueError("Alle sechs Geometriemaße angeben")
            werte={}
            for k,(unten,oben) in GEOMETRIE.items():
                if isinstance(roh[k],bool):raise ValueError("Geometriemaße als Zahl eingeben")
                try:v=float(roh[k])
                except (ValueError,TypeError):raise ValueError("Geometriemaße als Zahl eingeben") from None
                if not math.isfinite(v) or not unten<=v<=oben:raise ValueError(f"{k}: zwischen {unten:g} und {oben:g}")
                werte[k]=v
            self._sichern(daten);self._merken(daten)
            self.engine.update_traktor(werte)
            daten["geometrie"]={"zeit":time.time(),"werte":werte}
        elif aktion=="sichern":self._sichern(daten)
        elif aktion=="wiederherstellen":
            sicherung=daten.get("sicherung")
            if not sicherung or payload.get("sicherung_id")!=sicherung["id"]:
                raise KalibrierKonflikt("Diese Sicherung ist nicht mehr vorhanden. Neu laden.")
            if sicherung["hardware"]!=self.hardware():raise KalibrierKonflikt("Lenkhardware geändert; Sicherung passt nicht zum aktuellen Anschluss")
            vorher=self.traktorwerte();self.engine.update_traktor(sicherung["werte"])
            # Auch die Wiederherstellung lässt sich über dieselbe Taste rückgängig machen.
            daten["sicherung"]={**sicherung,"id":uuid.uuid4().hex,"zeit":time.time(),"werte":vorher}
        elif aktion=="protokoll":
            daten["protokoll"]={"zeit":time.time(),"kontext":self.kontext(),
                "uebung":self.config.gnss.source in ("simulator","replay"),
                "vollstaendig":bool(schritte) and all(s["fertig"] for s in schritte)
                    and daten.get("geometrie",{}).get("werte")==self.geometrie(),
                "traktor":self.engine.aktiver_traktor()["name"],"traktorwerte":self.traktorwerte(),
                "motorwerte":asdict(self.config.phidget),"schritte":schritte}
        else:raise ValueError("Unbekannte Werkstattaktion")
        self._merken(daten)
        self.engine.note("Lenkwerkstatt: "+str(aktion))
        return self.stand(schritte)
