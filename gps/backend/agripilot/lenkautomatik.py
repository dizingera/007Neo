"""Automatische Spurabstimmung mit Vergleich, Rücknahme und dauerhaftem Protokoll.

Jeder Versuch: 20 s Basis -> kleine Änderung -> 8 s einschwingen -> 20 s Vergleich.
Nur vergleichbare RTK-Geradeausfahrt zählt. Keine Hardware-/Geometrieänderung und
keine Freigabe des Motors. Der tatsächliche Regler bleibt cerearegler.py.
"""
from __future__ import annotations

import math
from .abstimmung import bewerte

FELDER = ('regelart', 'cerea_lenk_aggr', 'cerea_auffahr_aggr', 'cerea_l',
          'cerea_i', 'cerea_i2', 'cerea_d')
FENSTER_S = 20.0
EINSCHWINGEN_S = 8.0
MAX_VERSUCHE = 24


class Lenkautomatik:
    def __init__(self, lesen, setzen, speichern, stand=None):
        self.lesen, self.setzen, self.speichern = lesen, setzen, speichern
        self.aktiv = False
        self.phase = 'aus'
        self.hinweis = 'Bereit zum automatischen Abstimmen.'
        self.ursprung = None
        self.basis = None
        self.letzte = None
        self.versuch = None
        self.protokoll = []
        self.versuche = 0
        self.ziel_cm = 3.0
        self._daten = []
        self._kontext = None
        self._warten = None
        self._verworfen = set()
        self._gut = 0
        self._erwartet = dict(lesen())
        if stand:
            self.ursprung = stand.get('ursprung')
            self.protokoll = list(stand.get('protokoll') or [])[-30:]
            self.ziel_cm = float(stand.get('ziel_cm', 3.0))
            pending = stand.get('versuch')
            if pending and all(self._erwartet.get(k) == v for k, v in pending['nach'].items()):
                self.setzen(pending['vor'])
                self.hinweis = 'Unbestätigten Versuch nach Neustart zurückgenommen.'
                self._erwartet = dict(lesen())
            elif stand.get('aktiv'):
                self.hinweis = 'Nach Neustart bereit – Automatik erneut starten.'

    def _sichern(self):
        self.speichern({'aktiv': self.aktiv, 'ursprung': self.ursprung,
                        'versuch': self.versuch, 'protokoll': self.protokoll[-30:],
                        'ziel_cm': self.ziel_cm})

    def starten(self, ziel_cm=3.0):
        if not math.isfinite(ziel_cm) or not 1.0 <= ziel_cm <= 10.0:
            raise ValueError('Zielabweichung muss zwischen 1 und 10 cm liegen')
        if self.aktiv:
            return
        aktuell = dict(self.lesen())
        if any(not isinstance(aktuell.get(k), (int, float)) or not math.isfinite(aktuell[k])
               for k in FELDER if k != 'regelart'):
            raise ValueError('Die gespeicherten Reglerwerte müssen gültige Zahlen sein')
        self.ursprung = aktuell
        self.ziel_cm = ziel_cm
        self.versuche = 0
        self.protokoll = []
        self.basis = self.letzte = self.versuch = None
        self._gut = 0
        self._verworfen.clear()
        self._daten.clear()
        self._kontext = None
        # Wahl ist in der Bedienung ausdrücklich angekündigt. Es wird nie scharf geschaltet.
        self.setzen({'regelart': 'cerea'})
        self._erwartet = dict(self.lesen())
        self.aktiv, self.phase = True, 'basis'
        self.hinweis = 'Wartet auf RTK und ruhige Geradeausfahrt.'
        self._sichern()

    def _zurueck(self, grund):
        if self.versuch:
            self.setzen(self.versuch['vor'])
            self.protokoll.append({'aktion': 'zurück', 'grund': grund,
                                   'werte': self.versuch['vor']})
            self._erwartet = dict(self.lesen())
        self.versuch = None
        self._daten.clear()
        self._warten = None
        self.phase = 'basis' if self.aktiv else 'aus'

    def unterbrechen(self, grund):
        if not self.aktiv:
            return
        hatte = self.versuch is not None
        self._zurueck(grund)
        self.basis = None
        self.hinweis = grund
        if hatte:
            self._sichern()

    def stoppen(self, zurueck=False):
        if zurueck and not self.ursprung:
            raise ValueError('Noch keine Ausgangswerte gesichert')
        self._zurueck('Optimierung gestoppt')
        if zurueck and self.ursprung:
            self.setzen(self.ursprung)
            self._erwartet = dict(self.lesen())
        self.aktiv, self.phase = False, 'aus'
        self.hinweis = ('Ausgangswerte wiederhergestellt.' if zurueck else
                        'Optimierung aus. Bestätigte Einstellungen bleiben erhalten.')
        self._sichern()

    def beobachten(self, t, quer, tempo, gueltig, kontext, genauigkeit=0.02, neigung=0.0):
        if not self.aktiv:
            return
        zahlen = (t, quer, tempo, genauigkeit, neigung)
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in zahlen):
            self.unterbrechen('Wartet auf gültige Messwerte.')
            return
        if dict(self.lesen()) != self._erwartet:
            # Externe Änderungen haben Vorrang; ein alter Versuch darf sie nicht überschreiben.
            self.versuch = None
            self.aktiv = False
            self.phase = 'aus'
            self._daten.clear()
            self.hinweis = 'Wegen geänderter Einstellungen gestoppt.'
            self._sichern()
            return
        if not gueltig or not 0.8 <= tempo <= 6.0 or abs(quer) > 0.7:
            self.unterbrechen('Wartet: RTK, Lenkung an und gerade Spur erforderlich.')
            return
        if kontext != self._kontext:
            self.unterbrechen('Neue Spur – Vergleich beginnt neu.')
            self._kontext = kontext
            self._verworfen.clear()
        if self._daten and not 0 < t - self._daten[-1][0] <= 1.5:
            self.unterbrechen('Messung unterbrochen – Vergleich beginnt neu.')
        if self.phase == 'einschwingen':
            if self._warten is None:
                self._warten = t
            if t - self._warten < EINSCHWINGEN_S:
                self.hinweis = 'Neue Einstellung schwingt ein.'
                return
            self.phase = 'vergleich'
        if self._daten:
            referenz = self._daten[0]
            if abs(tempo - referenz[2]) > max(0.2, referenz[2] * 0.15) or abs(neigung - referenz[4]) > 2:
                self.unterbrechen('Tempo oder Hang geändert – neue Vergleichsmessung.')
        self._daten.append((t, quer, tempo, genauigkeit, neigung))
        if len(self._daten) > 4000:
            self.unterbrechen('Zu viele Messungen – Vergleich beginnt neu.')
            return
        if self._daten[-1][0] - self._daten[0][0] < FENSTER_S:
            if self.phase != 'überwacht':
                self.hinweis = ('Vergleicht neue Einstellung.' if self.versuch else 'Misst die aktuelle Spurführung.')
            return
        daten, self._daten = self._daten, []
        befund = bewerte([x[0] for x in daten], [x[1] for x in daten])
        q = sorted(abs(x[1]) for x in daten)
        m = {'effektiv_cm': befund.effektiv_m * 100,
             'p95_cm': q[int((len(q)-1)*0.95)] * 100,
             'mittel_cm': befund.mittel_m * 100,
             'amplitude_cm': befund.amplitude_m * 100,
             'art': befund.art, 'tempo': sum(x[2] for x in daten)/len(daten),
             'rauschen': sum(x[3] for x in daten)/len(daten),
             'neigung': sum(x[4] for x in daten)/len(daten)}
        m['score'] = m['effektiv_cm'] + 0.25*m['p95_cm'] + 0.5*m['amplitude_cm']
        self.letzte = m
        ziel = max(self.ziel_cm, m['rauschen'] * 150)
        if self.versuch:
            basis = self.versuch['basis']
            vergleichbar = (abs(m['tempo']-basis['tempo']) <= max(0.2,basis['tempo']*0.15)
                            and abs(m['neigung']-basis['neigung']) <= 2
                            and abs(m['rauschen']-basis['rauschen']) <= max(0.01,basis['rauschen']*0.5))
            besser = vergleichbar and m['score'] < basis['score']*0.95 and m['p95_cm'] <= basis['p95_cm']*1.05
            if besser:
                self.protokoll.append({'aktion':'übernommen','grund':'Messbar ruhiger und genauer',
                                       'werte':self.versuch['nach'], 'vor_cm':basis['effektiv_cm'],
                                       'nach_cm':m['effektiv_cm']})
                self.versuch = None
                self.basis = m
                self._verworfen.clear()
                self.phase = 'basis'
                self.hinweis = 'Verbesserung bestätigt und gespeichert.'
            else:
                if vergleichbar:
                    self._verworfen.add(self.versuch['signatur'])
                self._zurueck('Keine bestätigte Verbesserung' if vergleichbar else 'Bedingungen nicht vergleichbar')
                self.basis = None
                self.hinweis = 'Vorige Einstellung wiederhergestellt – misst neu.'
            self._sichern()
            return
        self.basis = m
        if m['effektiv_cm'] <= ziel and m['p95_cm'] <= 2*ziel and m['art'] != 'schwingt':
            self._gut += 1
            self.phase = 'überwacht'
            self.hinweis = 'Ziel erreicht – überwacht weiter.' if self._gut >= 2 else 'Gute Spur – bestätigt das Ergebnis.'
            return
        self._gut = 0
        if self.versuche >= MAX_VERSUCHE:
            self.phase = 'überwacht'
            self.hinweis = 'Versuchsgrenze erreicht – überwacht mit bestätigten Werten weiter.'
            return
        self._versuchen(m)

    def _versuchen(self, m):
        w = dict(self.lesen())
        aggr, d, i, i2, l = (float(w[k]) for k in
                             ('cerea_lenk_aggr','cerea_d','cerea_i','cerea_i2','cerea_l'))
        if m['art'] == 'schwingt':
            kandidaten = [('cerea_lenk_aggr',max(30.,aggr*0.85)), ('cerea_d',min(.7,d+.05)),
                          ('cerea_l',min(1.,l+.1))]
        elif abs(m['mittel_cm']) > max(5.,m['effektiv_cm']*.7):
            kandidaten = [('cerea_i',min(.2,i+.01)), ('cerea_i2',min(.1,i/2,i2+.005)),
                          ('cerea_lenk_aggr',min(150.,aggr*1.08))]
        else:
            kandidaten = [('cerea_lenk_aggr',min(150.,aggr*1.08)),
                          ('cerea_d',max(.1,d-.05)), ('cerea_lenk_aggr',max(30.,aggr*.9))]
        for feld, v in kandidaten:
            v = round(v,4)
            sig = f'{feld}:{v}'
            grenze = {'cerea_lenk_aggr':max(1.0,aggr*.15), 'cerea_d':.05, 'cerea_l':.1,
                      'cerea_i':.01, 'cerea_i2':.005}[feld]
            if abs(float(w[feld])-v) > grenze + 1e-5 or abs(float(w[feld])-v) < 1e-5 or sig in self._verworfen:
                continue
            self.versuch = {'vor':{feld:w[feld]}, 'nach':{feld:v}, 'basis':m, 'signatur':sig}
            self.versuche += 1
            self._sichern()  # Rücknahme vor der Änderung sichern (auch bei Programmabbruch).
            self.setzen({feld:v})
            self._erwartet = dict(self.lesen())
            self.phase, self._warten = 'einschwingen', None
            self.hinweis = 'Probiert eine kleine Änderung.'
            self.protokoll.append({'aktion':'Versuch','grund':m['art'],'werte':{feld:v}})
            return
        self.phase = 'überwacht'
        self.hinweis = 'Keine weitere bestätigte Stellmöglichkeit – überwacht weiter.'

    def status(self):
        return {'aktiv':self.aktiv, 'phase':self.phase, 'hinweis':self.hinweis,
                'ziel_cm':self.ziel_cm, 'rueckkehr_moeglich':bool(self.ursprung), 'effektives_ziel_cm':max(self.ziel_cm,(self.letzte or {}).get('rauschen',.02)*150),
                'messung':self.letzte, 'versuche':self.versuche, 'max_versuche':MAX_VERSUCHE,
                'sammelt_s':round(self._daten[-1][0]-self._daten[0][0],1) if len(self._daten)>1 else 0,
                'fenster_s':FENSTER_S, 'protokoll':self.protokoll[-10:]}
