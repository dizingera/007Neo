/* Warntöne und Vibration.

   In der Kabine schaut niemand ständig aufs Tablet. Was die Lenkung abschaltet
   oder den Fix verschlechtert, muss deshalb hörbar sein - und zwar nur das, was
   der Fahrer nicht selbst ausgelöst hat. Wer die Lenkung mit dem Daumen
   ausschaltet, braucht dazu keinen Ton.

   Die Entscheidung "welcher Ton wozu" steckt in ereignisse() und kennt keinen
   Browser: sie bekommt zwei Zustände und liefert Ereignisse. Das lässt sich mit
   Node prüfen (tests/alerts.test.js), der Ton selbst nicht. */

(function (root) {
  'use strict';

  /* stufe: 'alarm' = dringend, mehrfach; 'warn' = zweimal; 'info' = kurz. */
  function ereignisse(vorher, jetzt, verbunden) {
    const liste = [];
    if (!jetzt) return liste;
    const vorSteuer = vorher && vorher.steering;
    const jetztSteuer = jetzt.steering;

    // Die Lenkung hat geführt und tut es nicht mehr.
    if (vorSteuer && vorSteuer.command && vorSteuer.command.engaged &&
        jetztSteuer && jetztSteuer.command && !jetztSteuer.command.engaged) {
      const grund = jetztSteuer.command.reason || '';
      if (grund !== 'vom Fahrer ausgeschaltet') {
        liste.push({
          id: 'lenkung-aus',
          stufe: grund === 'Fahrer hat eingegriffen' ? 'info'
            : grund === 'Rückwärtsfahrt' ? 'warn' : 'alarm',
          text: `Lenkung aus: ${grund}`,
          ansage: `Lenkung aus. ${grund}`,
        });
      }
    }

    // Fix schlechter als RTK-fix, während es auf Genauigkeit ankommt.
    const vorFix = vorher && vorher.fix;
    const fix = jetzt.fix;
    const braucht = (jetztSteuer && jetztSteuer.armed) || jetzt.job;
    if (braucht && vorFix && vorFix.rank >= 4 && (!fix || fix.rank < 4)) {
      // Wird der Ausfall überbrückt, lenkt die Maschine weiter: Hinweis statt Alarm.
      const bruecke = jetzt.rtk_bruecke && jetzt.rtk_bruecke.aktiv;
      liste.push({
        id: 'fix-verloren',
        stufe: bruecke ? 'warn' : jetztSteuer && jetztSteuer.armed ? 'alarm' : 'warn',
        text: !fix ? 'Kein GPS mehr' : bruecke
          ? `Kein RTK – überbrückt, noch ${Math.round(jetzt.rtk_bruecke.rest_s)} s`
          : `Kein RTK mehr: ${fix.fix_label}`,
        ansage: !fix ? 'Kein GPS mehr' : bruecke
          ? `RTK weg. Überbrückt, noch ${Math.round(jetzt.rtk_bruecke.rest_s)} Sekunden`
          : 'RTK weg',
      });
    }
    // RTK wieder da, während gearbeitet wird: nur angesagt.
    if (braucht && vorFix && vorFix.rank < 4 && fix && fix.rank >= 4) {
      liste.push({ id: 'fix-zurueck', stufe: 'still', text: 'RTK wieder da', ansage: 'RTK wieder da' });
    }
    // Fahrgasse: beim Einfahren in eine Fahrgassenspur - nur angesagt.
    if (jetzt.fahrgasse_aktiv && !(vorher && vorher.fahrgasse_aktiv)) {
      liste.push({ id: 'fahrgasse', stufe: 'still', text: 'Fahrgasse', ansage: 'Fahrgasse' });
    }

    // Hindernis voraus: einmal je Hindernis, sobald es in Reichweite kommt.
    const hd = jetzt.hindernis_warnung;
    const vorHd = vorher && vorher.hindernis_warnung;
    if (hd && (!vorHd || vorHd.id !== hd.id)) {
      liste.push({ id: 'hindernis-' + hd.id, stufe: 'alarm',
        text: `Achtung: ${hd.name} in ${Math.round(hd.abstand_m)} m`,
        ansage: `Achtung. ${hd.name} in ${Math.round(hd.abstand_m)} Metern` });
    }

    // Vorgewende kommt näher: einmal, beim Überschreiten der Schwelle.
    const vorAlarm = !!(vorher && vorher.headland && vorher.headland.alarm);
    const jetztAlarm = !!(jetzt.headland && jetzt.headland.alarm);
    if (jetztAlarm && !vorAlarm && jetzt.headland.rest_m > 0) {
      liste.push({ id: 'vorgewende', stufe: 'warn', text: 'Vorgewende naht',
        ansage: `Vorgewende in ${Math.round(jetzt.headland.rest_m)} Metern` });
    }

    // Verbindung zum Gerät weg, während gelenkt wurde.
    if (verbunden === false && vorSteuer && vorSteuer.command && vorSteuer.command.engaged) {
      liste.push({ id: 'verbindung', stufe: 'alarm', text: 'Verbindung zum Gerät verloren',
        ansage: 'Verbindung verloren' });
    }
    return liste;
  }

  /* Töne: Frequenz (Hz), Dauer (s), Pausen. Mehr und höher = dringender. */
  const MUSTER = {
    alarm: { toene: [880, 660, 880, 660, 880], dauer: 0.22, pause: 0.08, vibration: [300, 100, 300, 100, 300] },
    warn: { toene: [740, 740], dauer: 0.18, pause: 0.1, vibration: [200, 100, 200] },
    info: { toene: [600], dauer: 0.15, pause: 0, vibration: [120] },
  };

  let kontext = null;
  let an = true;
  let sprache = true;
  let laut = 0.5;
  const zuletzt = {};          // id -> Zeit, damit ein flackernder Zustand nicht dauerpiept
  const SPERRE_MS = 8000;

  function freischalten() {
    // Browser starten Ton erst nach einer Berührung. Die erste genügt.
    try {
      const Ctx = root.AudioContext || root.webkitAudioContext;
      if (!kontext && Ctx) kontext = new Ctx();
      if (kontext && kontext.state === 'suspended') kontext.resume();
    } catch (e) { /* ohne Ton weiterarbeiten */ }
  }

  /* Sprachansage über die Stimme des Geräts (Windows: z.B. „Hedda“, Android: Google).
     Ohne Netz, ohne Zusatzprogramm. Ein Alarm unterbricht, was gerade gesprochen wird. */
  function stimme() {
    const s = root.speechSynthesis;
    if (!s) return null;
    const stimmen = s.getVoices() || [];
    return stimmen.find((v) => /^de(-|_|$)/i.test(v.lang) && /hedda|katja|anna|google/i.test(v.name))
      || stimmen.find((v) => /^de(-|_|$)/i.test(v.lang)) || null;
  }

  function sprechen(text, dringend) {
    const s = root.speechSynthesis;
    if (!sprache || !s || !text || typeof root.SpeechSynthesisUtterance !== 'function') return false;
    try {
      if (dringend) s.cancel();
      const satz = new root.SpeechSynthesisUtterance(text);
      satz.lang = 'de-DE';
      const v = stimme();
      if (v) satz.voice = v;
      satz.rate = 1.05;
      satz.volume = Math.max(0.2, laut * 2);
      s.speak(satz);
      return true;
    } catch (e) { return false; }
  }

  function abspielen(stufe) {
    if (stufe === 'still') return;
    const muster = MUSTER[stufe] || MUSTER.warn;
    try {
      if (root.navigator && root.navigator.vibrate) root.navigator.vibrate(muster.vibration);
    } catch (e) { /* nicht jedes Gerät kann es */ }
    if (!an || !kontext) return;
    let t = kontext.currentTime + 0.02;
    muster.toene.forEach((hz) => {
      const osz = kontext.createOscillator();
      const pegel = kontext.createGain();
      osz.type = 'square';
      osz.frequency.value = hz;
      pegel.gain.setValueAtTime(0.0001, t);
      pegel.gain.exponentialRampToValueAtTime(Math.max(0.01, laut * 0.4), t + 0.01);
      pegel.gain.exponentialRampToValueAtTime(0.0001, t + muster.dauer);
      osz.connect(pegel);
      pegel.connect(kontext.destination);
      osz.start(t);
      osz.stop(t + muster.dauer + 0.02);
      t += muster.dauer + muster.pause;
    });
  }

  /* Gibt die ausgelösten Ereignisse zurück, damit die Oberfläche denselben Text
     als Meldung zeigen kann. */
  function pruefen(vorher, jetzt, verbunden, jetztMs) {
    const nun = jetztMs != null ? jetztMs : Date.now();
    const ausgeloest = [];
    ereignisse(vorher, jetzt, verbunden).forEach((e) => {
      if (zuletzt[e.id] && nun - zuletzt[e.id] < SPERRE_MS) return;
      zuletzt[e.id] = nun;
      abspielen(e.stufe);
      sprechen(e.ansage, e.stufe === 'alarm');
      ausgeloest.push(e);
    });
    return ausgeloest;
  }

  const api = {
    ereignisse, pruefen, freischalten,
    einstellen(neu) {
      if (neu.an != null) an = !!neu.an;
      if (neu.sprache != null) sprache = !!neu.sprache;
      if (neu.laut != null) laut = Math.max(0, Math.min(1, neu.laut));
    },
    probe(stufe) {
      freischalten();
      abspielen(stufe || 'alarm');
      return sprechen('Sprachansage. Vorgewende in 20 Metern', true);
    },
    sprechen,
    hatStimme() { return Boolean(root.speechSynthesis && stimme()); },
  };
  root.Warnungen = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
