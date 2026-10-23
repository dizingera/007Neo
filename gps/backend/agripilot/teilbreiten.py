"""Lokaler Teilbreiten-Adapter: UDP mit expliziter Rückmeldung und Watchdog.

Kein Herstellerprotokoll. Der passende Relais-Adapter muss das in
TEILBREITEN.md beschriebene Protokoll und die eigene Abschaltüberwachung umsetzen.
"""
import asyncio
import json
import time
import uuid

PROTOKOLL="agripilot-teilbreiten/1"

class TeilbreitenAusgang(asyncio.DatagramProtocol):
    def __init__(self,config):
        self.config=config;self.transport=None;self.task=None;self.seq=0
        self.sitzung=uuid.uuid4().hex;self.ziel=0;self.anzahl=1;self.gewuenscht=0.
        self.wire=None;self.epoch=0;self.pending={};self.ack=0;self.ack_zeit=0.
        self.fehler="";self.laeuft=False;self.geschlossen=None
        if config.output not in ("aus","udp"):raise ValueError("Teilbreitenausgang: aus oder udp")
    async def start(self):
        if self.config.output=="aus":return
        try:
            loop=asyncio.get_running_loop()
            self.geschlossen=loop.create_future()
            self.transport,_=await loop.create_datagram_endpoint(lambda:self,
                remote_addr=(self.config.host,self.config.port))
        except OSError as exc:
            self.fehler=f"Teilbreiten nicht verbunden: {exc}";return
        self.laeuft=True;self.task=asyncio.create_task(self._run())
    def wunsch(self,maske:int,anzahl:int):
        if (type(maske) is not int or type(anzahl) is not int or not 1<=anzahl<=32
                or not 0<=maske<(1<<anzahl)):
            raise ValueError("Teilbreiten: 1 bis 32 und dazu passende Bitmaske nötig")
        self.ziel=maske;self.anzahl=anzahl;self.gewuenscht=time.monotonic()
        self._aktuell()
    def _aktuell(self):
        maske=self.ziel if time.monotonic()-self.gewuenscht<=.5 else 0
        wire=(maske,self.anzahl)
        if wire!=self.wire:
            self.wire=wire;self.epoch+=1;self.ack=0;self.ack_zeit=0;self.pending.clear()
        return wire
    def _senden(self):
        maske,anzahl=self._aktuell();self.seq+=1
        p={"protokoll":PROTOKOLL,"sitzung":self.sitzung,"seq":self.seq,
           "maske":maske,"anzahl":anzahl,"watchdog_ms":500}
        self.pending[self.seq]=(maske,self.epoch,time.monotonic())
        if len(self.pending)>32:self.pending.pop(next(iter(self.pending)))
        if self.transport is not None:self.transport.sendto(json.dumps(p).encode('utf-8'))
    async def _run(self):
        while self.laeuft:
            self._senden()
            await asyncio.sleep(.1)
    def datagram_received(self,data,addr):
        if len(data)>2048:return
        try:
            p=json.loads(data)
            if not isinstance(p,dict):return
            seq=p.get("seq");maske=p.get("maske")
            if type(seq) is not int or type(maske) is not int:return
            self._aktuell();gesendet=self.pending.get(seq)
            if (p.get("protokoll")!=PROTOKOLL or p.get("sitzung")!=self.sitzung
                    or gesendet is None or gesendet[1]!=self.epoch or time.monotonic()-gesendet[2]>=.4 or maske<0
                    or maske & ~gesendet[0]):return
            # Alte Telegramme dürfen eine neuere Rückmeldung nicht zurückdrehen.
            if seq<getattr(self,"ack_seq",0):return
            self.ack_seq=seq;self.ack=maske;self.ack_zeit=gesendet[2];self.fehler=""
        except (ValueError,TypeError,UnicodeDecodeError):return
    def connection_lost(self,exc):
        if self.geschlossen is not None and not self.geschlossen.done():self.geschlossen.set_result(None)
        self.ack=0;self.ack_zeit=0
    def error_received(self,exc):
        self.fehler=f"Teilbreitenverbindung: {exc}";self.ack=0;self.ack_zeit=0
    def bestaetigt(self):
        maske,_=self._aktuell()
        return self.ack & maske if time.monotonic()-self.ack_zeit<.4 else 0
    def status_dict(self):
        maske,_=self._aktuell();bestaetigt=self.bestaetigt()
        return {"ausgang":self.config.output,"angefordert":maske,"bestaetigt":bestaetigt,
                "verbunden":self.transport is not None and time.monotonic()-self.ack_zeit<.4,
                "anzahl":self.anzahl,"fehler":self.fehler}
    async def stop(self):
        self.laeuft=False
        if self.task is not None:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
            self.task=None
        if self.transport is not None:
            self.wunsch(0,self.anzahl);self._senden()
            # Dem letzten AUS-Datagramm einen I/O-Takt geben; anschließend auch
            # unter Windows ausstehende Proactor-Lese-/Schreiboperationen beenden.
            await asyncio.sleep(.02)
            self.transport.abort();self.transport=None
            if self.geschlossen is not None:await asyncio.wait_for(self.geschlossen,1.)
        self.ack=0;self.ack_zeit=0
