/* Lenkung live: was die Lenkung gerade tut, als Kurve und in Worten.
 *
 * Aus jedem Zustandsbild (5-10 je Sekunde) kommt ein Messpunkt in einen
 * Ringpuffer: Abweichung von der Spur, verlangter und tatsächlicher
 * Einschlag, Motorleistung. Daraus entstehen
 *
 *   - zwei Kurven (oben die Abweichung, unten Soll/Ist und die Leistung),
 *     mit einem Strich, wo der Lenk-Assistent etwas verstellt hat,
 *   - Kennzahlen über die letzten 30 s Fahrt mit Lenkung,
 *   - ein Befund in Klartext: pendelt, bleibt daneben, kommt zu langsam
 *     zurück, Rad hinkt hinterher - und was man dagegen dreht.
 *
 * Gerechnet und gezeichnet wird nur hier in der Oberfläche; der Server
 * liefert, was er ohnehin schickt.
 */
(function (wurzel) {
  'use strict';

  const PUFFER_S = 180;          // so viel Vergangenheit wird gehalten
  const BEWERTUNG_S = 30;        // Kennzahlen über dieses Fenster
  const MIN_PUNKTE = 40;         // darunter keine Aussage
  const HYSTERESE_CM = 2;        // Rauschen um die Mitte ist kein Pendeln
  const GLAETTEN_S = 0.4;
  const PENDEL_DURCHGAENGE = 4;
  const PENDEL_AMPLITUDE_CM = 4;
  const VERSATZ_CM = 3;
  const TRAEGE_CM = 5;
  const NACHLAUF_MAX_S = 2;
  const NACHLAUF_MELDEN_S = 0.3;
  // Ab so viel Nachlauf schaukelt sich Stanley auf, egal wie sanft (abstimmung.py).
  const NACHLAUF_VORAUS_S = 0.6;
  const VOLLE_LEISTUNG = 0.95;

  /* Ein Messpunkt aus dem Zustand des Servers - oder null, wenn nichts zu messen ist. */
  function messpunkt(daten) {
    if (!daten || !daten.guidance) return null;
    const lenkung = daten.steering || {};
    const befehl = lenkung.command || {};
    const fix = daten.fix || {};
    // Im Simulator gibt es keinen Drehgeber: dort folgt das Rad dem Befehl sofort.
    const ist = lenkung.wheel_angle_deg != null ? lenkung.wheel_angle_deg
      : (lenkung.simulator && befehl.engaged ? befehl.angle_deg : null);
    return {
      t: daten.time || Date.now() / 1000,
      quer: (daten.guidance.cross_track_m || 0) * 100,
      aktiv: !!daten.guidance.active,
      lenkt: !!(lenkung.armed && befehl.engaged),
      soll: befehl.engaged ? befehl.angle_deg : (daten.guidance.steer_angle_deg || 0),
      ist,
      leistung: lenkung.duty != null ? lenkung.duty : null,
      tempo: fix.speed_ms || 0,
    };
  }

  class Aufzeichnung {
    constructor() { this.punkte = []; }

    aufnehmen(daten) {
      const p = messpunkt(daten);
      if (!p) return;
      const letzter = this.punkte[this.punkte.length - 1];
      if (letzter && p.t <= letzter.t) return;            // doppelt oder Uhr zurück
      this.punkte.push(p);
      const grenze = p.t - PUFFER_S;
      while (this.punkte.length && this.punkte[0].t < grenze) this.punkte.shift();
    }

    /* Nur Fahrt mit Lenkung zählt: im Stand oder von Hand gelenkt sagt die Kurve nichts über die Regelung. */
    bewertbar(sekunden = BEWERTUNG_S) {
      if (!this.punkte.length) return [];
      const bis = this.punkte[this.punkte.length - 1].t;
      return this.punkte.filter((p) => p.t >= bis - sekunden && p.lenkt && p.aktiv && p.tempo > 0.5);
    }

    kennzahlen(sekunden = BEWERTUNG_S) {
      return kennzahlen(this.bewertbar(sekunden));
    }
  }

  function kennzahlen(punkte) {
    const n = punkte.length;
    if (n < MIN_PUNKTE) return { n, genug: false };
    const quer = punkte.map((p) => p.quer);
    const mittel = quer.reduce((s, q) => s + q, 0) / n;
    const effektiv = Math.sqrt(quer.reduce((s, q) => s + q * q, 0) / n);
    const max = Math.max(...quer.map(Math.abs));
    // Pendeln: Nulldurchgänge um die Dauerabweichung, mit Hysterese - über eine
    // geglättete Kurve, damit das Zittern des Empfängers nicht als Pendeln zählt.
    let seite = 0;
    let durchgaenge = 0;
    let spitze = 0;
    const spitzen = [];
    glaetten(punkte).forEach((q) => {
      const r = q - mittel;
      if (seite >= 0 && r < -HYSTERESE_CM) {
        if (seite === 1) { durchgaenge += 1; spitzen.push(spitze); }
        seite = -1; spitze = Math.abs(r);
      } else if (seite <= 0 && r > HYSTERESE_CM) {
        if (seite === -1) { durchgaenge += 1; spitzen.push(spitze); }
        seite = 1; spitze = Math.abs(r);
      } else spitze = Math.max(spitze, Math.abs(r));
    });
    const amplitude = spitzen.length ? [...spitzen].sort((a, b) => a - b)[Math.floor(spitzen.length / 2)] : 0;
    const dauer = punkte[n - 1].t - punkte[0].t;
    const periode = durchgaenge >= 2 ? 2 * dauer / durchgaenge : 0;
    const leistungen = punkte.filter((p) => p.leistung != null);
    const vollAnteil = leistungen.length
      ? leistungen.filter((p) => Math.abs(p.leistung) >= VOLLE_LEISTUNG).length / leistungen.length : 0;
    return {
      n, genug: true, dauer, mittel, effektiv, max, durchgaenge, amplitude, periode,
      nachlauf: nachlauf(punkte), vollAnteil,
    };
  }

  /* Gleitender Mittelwert über ±GLAETTEN_S - Pendeln dauert Sekunden, Rauschen Zehntel. */
  function glaetten(punkte) {
    let a = 0;
    let b = 0;
    let summe = 0;
    return punkte.map((p) => {
      while (b < punkte.length && punkte[b].t <= p.t + GLAETTEN_S) { summe += punkte[b].quer; b += 1; }
      while (punkte[a].t < p.t - GLAETTEN_S) { summe -= punkte[a].quer; a += 1; }
      return summe / (b - a);
    });
  }

  /* Wie viel später folgt das Rad dem Befehl? Die Verschiebung, bei der Ist und
   * Soll am besten übereinanderliegen. null, wenn es nichts zu vergleichen gibt. */
  function nachlauf(punkte) {
    const paare = punkte.filter((p) => p.ist != null);
    if (paare.length < MIN_PUNKTE) return null;
    const soll = paare.map((p) => p.soll);
    const ist = paare.map((p) => p.ist);
    const mw = (a) => a.reduce((s, x) => s + x, 0) / a.length;
    const ms = mw(soll);
    const streuung = Math.sqrt(soll.reduce((s, x) => s + (x - ms) ** 2, 0) / soll.length);
    if (streuung < 0.3) return null;                      // geradeaus ohne Lenkbewegung
    const takt = (paare[paare.length - 1].t - paare[0].t) / (paare.length - 1);
    if (!(takt > 0)) return null;
    const maxVersatz = Math.min(Math.round(NACHLAUF_MAX_S / takt), Math.floor(paare.length / 3));
    let beste = 0;
    let besteGuete = -Infinity;
    for (let k = 0; k <= maxVersatz; k += 1) {
      let summe = 0;
      for (let i = 0; i + k < paare.length; i += 1) summe -= (soll[i] - ist[i + k]) ** 2;
      const guete = summe / (paare.length - k);
      if (guete > besteGuete) { besteGuete = guete; beste = k; }
    }
    return beste * takt;
  }

  const zahl = (x, n = 0) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: n, minimumFractionDigits: n });

  /* Was die Zahlen heißen, und was man dreht. Das Wichtigste zuerst. */
  function befund(k, regelart) {
    if (!k || !k.genug) {
      return { art: 'sammelt', text: 'Sammelt Messwerte – Lenkung einschalten und auf der Spur fahren.' };
    }
    const tipps = [];
    let art = 'gut';
    let text;
    const pendelt = k.durchgaenge >= PENDEL_DURCHGAENGE && k.amplitude >= PENDEL_AMPLITUDE_CM;
    if (pendelt) {
      art = 'pendelt';
      text = `Pendelt alle ${zahl(k.periode, 1)} s um ±${zahl(k.amplitude)} cm.`;
      tipps.push(regelart === 'voraus'
        ? 'Trägheit der Lenkung etwas erhöhen oder Lenkschärfe senken (Traktor & Gerät).'
        : 'Lenkschärfe etwas senken (Traktor & Gerät).');
    } else if (Math.abs(k.mittel) >= VERSATZ_CM && Math.abs(k.mittel) >= 0.7 * k.effektiv) {
      art = 'versatz';
      text = `Bleibt im Mittel ${zahl(Math.abs(k.mittel))} cm ${k.mittel > 0 ? 'rechts' : 'links'} der Spur.`;
      tipps.push('Antenne „nach rechts“ nachmessen – oder die Spur mit den 10-cm-Tasten verschieben.');
    } else if (k.effektiv >= TRAEGE_CM && k.durchgaenge <= 2) {
      art = 'traege';
      text = `Kommt nur langsam zurück (${zahl(k.effektiv)} cm effektiv).`;
      tipps.push('Lenkschärfe etwas erhöhen (Traktor & Gerät).');
    } else {
      text = `Gut: ${zahl(k.effektiv, 1)} cm effektiv, höchstens ${zahl(k.max)} cm.`;
    }
    if (k.nachlauf != null && k.nachlauf >= NACHLAUF_MELDEN_S) {
      const s = zahl(k.nachlauf, 1);
      if (regelart === 'voraus') {
        tipps.push(`Das Rad folgt dem Befehl ${s} s später – Trägheit der Lenkung auf ${s} s stellen.`);
      } else if (k.nachlauf >= NACHLAUF_VORAUS_S) {
        tipps.push(`Das Rad folgt dem Befehl erst ${s} s später – dafür ist Stanley zu träge: Regelart ` +
          `„Vorausschauend“ mit Trägheit ${s} s (Traktor & Gerät), oder den Lenk-Assistenten „Stellt selbst nach“ machen lassen.`);
      } else {
        tipps.push(`Das Rad folgt dem Befehl ${s} s später – Latenz auf ${zahl(k.nachlauf * 1000)} ms stellen.`);
      }
    }
    if (k.vollAnteil >= 0.2) {
      tipps.push(`Motor ${zahl(k.vollAnteil * 100)} % der Zeit bei voller Leistung – er kommt nicht hinterher (Motorregler, Mindestleistung).`);
    }
    return { art, text, tipps };
  }

  /* ---------------------------------------------------------- Zeichnen */

  function farben(element) {
    const s = getComputedStyle(element);
    const v = (name, ersatz) => (s.getPropertyValue(name) || '').trim() || ersatz;
    return {
      text: v('--text', '#e8eef2'), leise: v('--muted', '#8b98a3'), linie: v('--line', '#2a343c'),
      blau: v('--blau', '#4da3ff'), amber: v('--amber', '#ffb020'), gruen: v('--green', '#3ddc84'),
      rot: v('--rot', '#ff6b61'),
    };
  }

  function zeichnen(leinwand, aufzeichnung, { sekunden = 60, schritte = [], kompakt = false } = {}) {
    const dpr = window.devicePixelRatio || 1;
    const breite = leinwand.clientWidth;
    const hoehe = leinwand.clientHeight;
    if (!breite || !hoehe) return;
    if (leinwand.width !== Math.round(breite * dpr) || leinwand.height !== Math.round(hoehe * dpr)) {
      leinwand.width = Math.round(breite * dpr);
      leinwand.height = Math.round(hoehe * dpr);
    }
    const ctx = leinwand.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, breite, hoehe);
    const f = farben(leinwand);
    const schrift = kompakt ? 11 : 12;
    ctx.font = `${schrift}px system-ui, sans-serif`;
    const punkte = aufzeichnung.punkte;
    if (!punkte.length) {
      ctx.fillStyle = f.leise;
      ctx.fillText('Noch keine Daten', 10, hoehe / 2);
      return;
    }
    const bis = punkte[punkte.length - 1].t;
    const von = bis - sekunden;
    const links = kompakt ? 30 : 40;
    const rechts = 8;
    const x = (t) => links + (t - von) / sekunden * (breite - links - rechts);
    const sichtbar = punkte.filter((p) => p.t >= von);

    const oben = { y0: 4, h: Math.round((hoehe - 8) * 0.55) };
    const unten = { y0: oben.y0 + oben.h + 6, h: hoehe - 8 - oben.h - 6 };

    // Zeiten ohne Lenkung leicht abgetönt: dort regelt nichts.
    ctx.fillStyle = 'rgba(128,128,128,0.10)';
    let start = null;
    sichtbar.forEach((p, i) => {
      if (!p.lenkt && start === null) start = p.t;
      const ende = i === sichtbar.length - 1;
      if (start !== null && (p.lenkt || ende)) {
        ctx.fillRect(x(start), 0, Math.max(1, x(p.t) - x(start)), hoehe);
        start = null;
      }
    });

    // Oben: Abweichung (cm), mit grünem Band ±2,5 cm.
    const maxQuer = Math.max(10, Math.ceil(Math.max(...sichtbar.map((p) => Math.abs(p.quer))) / 5) * 5);
    const yQ = (cm) => oben.y0 + oben.h / 2 - cm / maxQuer * (oben.h / 2);
    ctx.fillStyle = 'rgba(61,220,132,0.14)';
    ctx.fillRect(links, yQ(2.5), breite - links - rechts, yQ(-2.5) - yQ(2.5));
    achse(ctx, f, links, breite - rechts, yQ(0));
    ctx.fillStyle = f.leise;
    ctx.fillText(`${maxQuer} cm`, 2, yQ(maxQuer) + schrift);
    ctx.fillText('0', 2, yQ(0) + 4);
    ctx.fillText(`-${maxQuer}`, 2, yQ(-maxQuer) - 2);
    linie(ctx, sichtbar, x, (p) => yQ(p.quer), f.text, 2);
    ctx.fillStyle = f.text;
    if (!kompakt) ctx.fillText('Abweichung von der Spur (+ rechts)', links + 6, oben.y0 + schrift);

    // Unten: Einschlag Soll/Ist (°) und Motorleistung.
    const winkel = sichtbar.flatMap((p) => [p.soll, p.ist]).filter((w) => w != null);
    const maxW = Math.max(5, Math.ceil(Math.max(...winkel.map(Math.abs)) / 5) * 5);
    const yW = (g) => unten.y0 + unten.h / 2 - g / maxW * (unten.h / 2);
    achse(ctx, f, links, breite - rechts, yW(0));
    if (sichtbar.some((p) => p.leistung != null)) {
      ctx.fillStyle = 'rgba(139,152,163,0.28)';
      sichtbar.forEach((p, i) => {
        if (p.leistung == null || i === 0) return;
        const x0 = x(sichtbar[i - 1].t);
        const y = unten.y0 + unten.h / 2 - p.leistung * (unten.h / 2);
        ctx.fillRect(x0, Math.min(y, yW(0)), Math.max(1, x(p.t) - x0), Math.abs(y - yW(0)));
      });
    }
    linie(ctx, sichtbar, x, (p) => yW(p.soll), f.blau, 2);
    linie(ctx, sichtbar.filter((p) => p.ist != null), x, (p) => yW(p.ist), f.amber, 2);
    ctx.fillStyle = f.leise;
    ctx.fillText(`${maxW}°`, 2, yW(maxW) + schrift);
    ctx.fillText(`-${maxW}°`, 2, yW(-maxW) - 2);
    if (!kompakt) {
      let lx = links + 6;
      const legende = (farbe, text) => {
        ctx.fillStyle = farbe; ctx.fillRect(lx, unten.y0 + 4, 12, 3);
        ctx.fillStyle = f.text; ctx.fillText(text, lx + 16, unten.y0 + schrift);
        lx += ctx.measureText(text).width + 34;
      };
      legende(f.blau, 'Einschlag verlangt');
      legende(f.amber, 'Rad tatsächlich');
      legende('rgba(139,152,163,0.7)', 'Motorleistung');
    }

    // Wo der Lenk-Assistent etwas verstellt hat.
    (schritte || []).forEach((s) => {
      if (s.zeit < von || s.zeit > bis) return;
      const sx = x(s.zeit);
      ctx.strokeStyle = f.gruen;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, hoehe); ctx.stroke();
      ctx.setLineDash([]);
      if (!kompakt) {
        ctx.fillStyle = f.gruen;
        ctx.fillText(schrittKurz(s), Math.min(sx + 4, breite - 120), oben.y0 + oben.h - 4);
      }
    });
  }

  function achse(ctx, f, x0, x1, y) {
    ctx.strokeStyle = f.linie;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
  }

  function linie(ctx, punkte, x, y, farbe, staerke) {
    if (punkte.length < 2) return;
    ctx.strokeStyle = farbe;
    ctx.lineWidth = staerke;
    ctx.beginPath();
    punkte.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p)) : ctx.moveTo(x(p.t), y(p))));
    ctx.stroke();
  }

  function schrittKurz(s) {
    const was = { steer_gain: 'Schärfe', actuator_latency_ms: 'Latenz', lenk_traegheit_s: 'Trägheit',
      regelart: 'Regelart' }[s.was] || s.was;
    if (typeof s.nach === 'string') return `${was} ${{ voraus: 'Vorausschauend', stanley: 'Stanley' }[s.nach] || s.nach}`;
    return s.von || s.nach ? `${was} ${zahl(s.von, 2)}→${zahl(s.nach, 2)}` : was;
  }

  const ausfuhr = { Aufzeichnung, messpunkt, kennzahlen, nachlauf, befund, zeichnen, schrittKurz };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.LenkLive = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
