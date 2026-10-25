"""Geführte, rein beobachtende Vergleichsfahrt; schaltet keinen Motor ein."""
from __future__ import annotations

import bisect
import copy
import json
import math

MESSDAUER_S = 30.0


def _endlich(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def kennzahlen(punkte):
    quer = sorted(abs(p['quer']) * 100 for p in punkte)
    paare = [p for p in punkte if _endlich(p.get('ist')) and _endlich(p.get('soll'))]
    motor = [p for p in punkte if _endlich(p.get('leistung')) and _endlich(p.get('grenze')) and p['grenze'] > 0]
    nachlauf = None
    if len(paare) >= 40:
        zeiten = [p['t'] for p in paare]
        soll = [p['soll'] for p in paare]
        ist = [p['ist'] for p in paare]
        ms, mi = sum(soll)/len(soll), sum(ist)/len(ist)
        if max(soll)-min(soll) >= 2 and max(ist)-min(ist) >= 1:
            kosten = []
            for schritt in range(21):
                delay = schritt / 10
                fehler = []
                for j, p in enumerate(paare):
                    t = p['t']-delay
                    k = bisect.bisect_left(zeiten, t)
                    if k == 0 or k >= len(zeiten) or zeiten[k]-zeiten[k-1] > .5:
                        continue
                    anteil = (t-zeiten[k-1])/(zeiten[k]-zeiten[k-1])
                    ziel = soll[k-1] + (soll[k]-soll[k-1])*anteil
                    fehler.append(((ziel-ms)-(ist[j]-mi))**2)
                kosten.append(sum(fehler)/len(fehler) if fehler else math.inf)
            nachlauf = kosten.index(min(kosten)) / 10
    imu = [p for p in punkte if isinstance(p.get('imu_frisch'), bool)]
    return {
        'imu_anteil': sum(p['imu_frisch'] for p in imu)/len(imu) if imu else None,
        'dauer_s': round(punkte[-1]['t']-punkte[0]['t'], 1),
        'rms_cm': math.sqrt(sum(p['quer']**2 for p in punkte)/len(punkte))*100,
        'p95_cm': quer[int((len(quer)-1)*.95)],
        'mittel_cm': sum(p['quer'] for p in punkte)/len(punkte)*100,
        'tempo_kmh': sum(p['tempo'] for p in punkte)/len(punkte)*3.6,
        'gps_cm': sum(p['gps'] for p in punkte)/len(punkte)*100,
        'roll_deg': sum(p['roll'] for p in punkte)/len(punkte),
        'motor_grenze_anteil': sum(abs(p['leistung']) >= p['grenze']*.95 for p in motor)/len(motor) if motor else None,
        'radfehler_deg': sum(abs(p['ist']-p['soll']) for p in paare)/len(paare) if paare else None,
        'nachlauf_s': nachlauf,
        'proben': len(punkte),
        'rad_proben': len(paare),
        'motor_proben': len(motor),
    }


class Probefahrt:
    def __init__(self, speichern, stand=None):
        self.speichern = speichern
        self.phase = 'aus'
        self.hinweis = 'Vorher messen, Einstellung verbessern, dieselbe Spur nochmals messen.'
        self.vorher = None
        self.nachher = None
        self.bericht = copy.deepcopy(stand) if isinstance(stand, dict) and stand.get('vorher') else None
        self._punkte = []
        self._kontext = None
        self._startkonfig = None
        self._vor_kontext = None

    @property
    def sammelt(self):
        return self.phase in ('vorher', 'nachher')

    def pruefen(self, aktion, konfig):
        if aktion not in ('vorher', 'nachher', 'abbrechen'):
            raise ValueError('Unbekannte Probefahrt-Aktion')
        if aktion == 'nachher':
            if not self.vorher:
                raise ValueError('Zuerst die Vorher-Messung abschließen')
            alt = self.vorher['einstellungen']
            if any(alt.get(k) != konfig.get(k) for k in ('traktor', 'geraet')):
                raise ValueError('Für den Vergleich dasselbe Gespann wählen')

    def bedienen(self, aktion, konfig):
        self.pruefen(aktion, konfig)
        if aktion == 'vorher':
            self.vorher = self.nachher = None
            self._vor_kontext = None
            self.phase = 'vorher'
        elif aktion == 'nachher':
            self.nachher = None
            self.phase = 'nachher'
        elif aktion == 'abbrechen':
            self.phase = 'aus'
            self._punkte.clear()
            self.hinweis = 'Messung beendet. Einstellungen wurden nicht verändert.'
            return self.status()
        else:
            raise ValueError('Unbekannte Probefahrt-Aktion')
        self._punkte.clear()
        self._kontext = None
        self._startkonfig = copy.deepcopy(konfig)
        self.hinweis = 'Wartet auf dieselbe gerade Spur, RTK und eingeschaltete Lenkung.'
        return self.status()

    def unterbrechen(self, grund):
        if self.sammelt:
            self._punkte.clear()
            self.hinweis = grund + ' – 30 Sekunden neu messen.'

    def beobachten(self, t, quer, tempo, gueltig, kontext, konfig, gps=.03,
                   roll=0., soll=None, ist=None, leistung=None, grenze=None, imu_frisch=None):
        if not self.sammelt:
            return
        if not gueltig or not all(_endlich(x) for x in (t, quer, tempo, gps, roll)) or not .8 <= tempo <= 6 or gps < 0:
            self.unterbrechen('Wartet auf ruhige RTK-Geradeausfahrt')
            return
        key = json.dumps(kontext, sort_keys=True)
        if self.phase == 'nachher' and key != self._vor_kontext:
            self.unterbrechen('Vorher-Spur und Fahrtrichtung wählen')
            return
        if konfig != self._startkonfig:
            self.phase = 'bereit' if self.vorher else 'aus'
            self._punkte.clear()
            self.hinweis = 'Einstellungen während Messung geändert. Messung erneut starten.'
            return
        if self._kontext != key:
            self.unterbrechen('Spur geändert')
            self._kontext = key
        if self._punkte:
            erster, letzter = self._punkte[0], self._punkte[-1]
            if (not 0 < t-letzter['t'] <= 1.5 or abs(tempo-erster['tempo']) > max(.2, erster['tempo']*.15)
                    or abs(roll-erster['roll']) > 2):
                self.unterbrechen('Messlücke, Tempo oder Hang verändert')
        self._punkte.append(dict(t=t, quer=quer, tempo=tempo, gps=gps, roll=roll,
                                 soll=soll, ist=ist, leistung=leistung, grenze=grenze, imu_frisch=imu_frisch))
        if len(self._punkte) > 3000:
            self.unterbrechen('Zu viele Messwerte')
            return
        self.hinweis = 'Misst ' + ('vorher' if self.phase == 'vorher' else 'nachher') + ' – gleichmäßig weiterfahren.'
        if t-self._punkte[0]['t'] < MESSDAUER_S or len(self._punkte) < 40:
            return
        messung = {'werte': kennzahlen(self._punkte), 'einstellungen': copy.deepcopy(konfig), 'zeit': t}
        self._punkte.clear()
        if self.phase == 'vorher':
            self.vorher = messung
            self._vor_kontext = key
            self.phase = 'bereit'
            self.hinweis = 'Vorher fertig. Einstellung verbessern, dann dieselbe Spur in gleicher Richtung messen.'
            return
        self.nachher = messung
        a, b = self.vorher['werte'], self.nachher['werte']
        vergleichbar = (abs(a['tempo_kmh']-b['tempo_kmh']) <= max(.72, a['tempo_kmh']*.15)
                        and abs(a['roll_deg']-b['roll_deg']) <= 2
                        and abs(a['gps_cm']-b['gps_cm']) <= max(1., a['gps_cm']*.5))
        geometrie = ('wheelbase_m', 'antenna_height_m', 'antenna_forward_m', 'antenna_right_m',
                     'width_m', 'overlap_m', 'tool_offset_m', 'tool_trailing_m', 'trailed',
                     'hitch_length_m', 'bauform', 'traktor_breite_m', 'traktor_vorn_m', 'traktor_hinten_m')
        vorprofil = self.vorher['einstellungen'].get('profil', {})
        nachprofil = self.nachher['einstellungen'].get('profil', {})
        vergleichbar = vergleichbar and all(vorprofil.get(k) == nachprofil.get(k) for k in geometrie)
        besser = vergleichbar and b['rms_cm'] < a['rms_cm']*.95 and b['p95_cm'] <= a['p95_cm']*1.05
        self.bericht = {'uebung': bool(self.vorher['einstellungen'].get('uebung') or konfig.get('uebung')),
                        'vorher': copy.deepcopy(self.vorher), 'nachher': copy.deepcopy(self.nachher),
                        'vergleichbar': vergleichbar, 'besser': besser,
                        'rms_aenderung_prozent': (1-b['rms_cm']/a['rms_cm'])*100 if a['rms_cm'] > .01 else None,
                        'urteil': ('Messbar genauer' if besser else 'Keine bestätigte Verbesserung') if vergleichbar else 'Bedingungen nicht vergleichbar'}
        self.speichern(copy.deepcopy(self.bericht))
        self.phase = 'fertig'
        self.hinweis = self.bericht['urteil'] + '. Einstellungen wurden durch die Messung nicht geändert.'

    def status(self):
        return {'phase': self.phase, 'sammelt': self.sammelt, 'hinweis': self.hinweis,
                'sammelt_s': round(self._punkte[-1]['t']-self._punkte[0]['t'], 1) if len(self._punkte)>1 else 0,
                'dauer_s': MESSDAUER_S, 'vorher': copy.deepcopy(self.vorher),
                'bericht': copy.deepcopy(self.bericht)}
