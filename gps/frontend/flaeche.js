/* Bearbeitete Fläche: ein glatter Streifen hinter dem Gerät, so breit wie es arbeitet.
 *
 * Vorher kam die Fläche als 0,5-m-Rasterzellen vom Server und wurde Pixel für
 * Pixel hochgezogen - an den Rändern Treppen, und sie wuchs nur in Sprüngen.
 * Jetzt zeichnet die Oberfläche die eigene Fahrt selbst: zwischen zwei Lagen
 * des Geräts je Teilbreite ein Viereck von Kante zu Kante. Der Server rechnet
 * die Fläche (Hektar, Überlappung) weiter mit seinen Zellen; hier geht es nur
 * ums Bild.
 *
 * Gemalt wird in Kacheln (128 m, 4 Pixel je Meter), die erst entstehen, wenn
 * dort gearbeitet wird. Eine einzige große Leinwand für das ganze Feld wäre bei
 * dieser Auflösung Hunderte Megabyte groß.
 *
 * Überfährt das Gerät schon Bearbeitetes, bekommt der Streifen dort eine
 * zweite Farbe. Entschieden wird je halbem Meter Arbeitsbreite, damit ein
 * schmaler Anschlussfehler am Rand auch nur dort zu sehen ist.
 */
(function (wurzel) {
  'use strict';

  const KACHEL_M = 128;
  const PX_PRO_M = 4;
  const KACHEL_PX = KACHEL_M * PX_PRO_M;
  const QUER_STUECK_M = 0.5;      // so fein wird die Überlappung quer zur Fahrt aufgelöst
  const FRISCH_MS = 2500;         // eigene Spur von eben ist keine Überlappung
  const MAX_SCHRITT_M = 15;       // weiter springt kein Gerät zwischen zwei Lagen

  function zellSchluessel(ix, iy) {
    return (ix + 1048576) * 2097152 + (iy + 1048576);
  }

  /* Quer zur Fahrt: wo das Gerät arbeitet, in Stücken von höchstens einem halben
   * Meter. Ohne Teilbreiten arbeitet die ganze Breite. */
  function querStuecke(teilbreiten, breite) {
    const aktiv = (teilbreiten || []).filter((t) => t.enabled && !t.forced_off);
    const bereiche = (teilbreiten && teilbreiten.length)
      ? aktiv.map((t) => [t.left_m, t.right_m])
      : [[-breite / 2, breite / 2]];
    const stuecke = [];
    for (const [links, rechts] of bereiche) {
      const anzahl = Math.max(1, Math.ceil((rechts - links) / QUER_STUECK_M - 1e-9));
      for (let i = 0; i < anzahl; i++) {
        stuecke.push([links + (rechts - links) * i / anzahl, links + (rechts - links) * (i + 1) / anzahl]);
      }
    }
    return stuecke;
  }

  function seitlich(pos, kursGrad, quer) {
    const h = kursGrad * Math.PI / 180;
    return [pos[0] + Math.cos(h) * quer, pos[1] - Math.sin(h) * quer];
  }

  /* Die Vierecke zwischen zwei Lagen. Jede Lage bringt ihren eigenen Kurs mit -
   * in der Kurve fächert der Streifen so auf, statt Lücken zu lassen. */
  function vierecke(alt, neu, stuecke) {
    return stuecke.map(([links, rechts]) => {
      const ecken = [
        seitlich(alt.pos, alt.kurs, links), seitlich(alt.pos, alt.kurs, rechts),
        seitlich(neu.pos, neu.kurs, rechts), seitlich(neu.pos, neu.kurs, links),
      ];
      const mitte = [0, 1].map((achse) => ecken.reduce((summe, p) => summe + p[achse], 0) / 4);
      return { ecken, mitte };
    });
  }

  /* Nur Breiten, die an beiden Messpunkten eingeschaltet waren. Neue
   * Teilbreiten beginnen hier; sie färben die vergangene Strecke nicht nach. */
  function gemeinsameStuecke(alt, neu) {
    const a = [...alt.stuecke].sort((x, y) => x[0] - y[0]);
    const b = [...neu.stuecke].sort((x, y) => x[0] - y[0]);
    const gemeinsam = [];
    for (let i = 0, j = 0; i < a.length && j < b.length;) {
      const links = Math.max(a[i][0], b[j][0]), rechts = Math.min(a[i][1], b[j][1]);
      if (rechts > links) gemeinsam.push([links, rechts]);
      if (a[i][1] <= b[j][1]) i++; else j++;
    }
    return gemeinsam;
  }

  /* Auch längs höchstens ein halber Meter: ein Anschlussfehler bekommt nur
   * dort Bernstein, wo er tatsächlich auf schon bearbeitete Zellen trifft. */
  function streifenVierecke(alt, neu) {
    const stuecke = gemeinsameStuecke(alt, neu);
    const winkel = ((neu.kurs - alt.kurs + 180) % 360 + 360) % 360 - 180;
    const weg = Math.hypot(neu.pos[0] - alt.pos[0], neu.pos[1] - alt.pos[1]);
    const anzahl = Math.max(1, Math.ceil(weg / .5), Math.ceil(Math.abs(winkel) / 5));
    const lage = (t) => ({ pos: alt.pos.map((p, i) => p + (neu.pos[i] - p) * t), kurs: alt.kurs + winkel * t });
    const liste = [];
    for (let i = 0; i < anzahl; i++) liste.push(...vierecke(lage(i / anzahl), lage((i + 1) / anzahl), stuecke));
    return liste;
  }

  function viereckMalen(c, ecken) {
    c.beginPath();
    ecken.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
    c.closePath(); c.fill();
    c.lineWidth = 0.6 / PX_PRO_M;
    c.stroke();
  }

  /* 'rgba(93,255,142,.22)' oder '#5dff8e' -> deckende Farbe und Deckkraft. */
  function farbeZerlegen(text) {
    const t = (text || '').trim();
    let r = 0, g = 0, b = 0, a = 1;
    const rgba = /^rgba?\(([^)]+)\)$/i.exec(t);
    if (rgba) {
      const teile = rgba[1].split(',').map((x) => parseFloat(x));
      [r, g, b] = teile;
      if (teile.length > 3 && !Number.isNaN(teile[3])) a = teile[3];
    } else if (/^#[0-9a-f]{6}$/i.test(t)) {
      r = parseInt(t.slice(1, 3), 16); g = parseInt(t.slice(3, 5), 16); b = parseInt(t.slice(5, 7), 16);
    }
    return { deckend: `rgb(${r},${g},${b})`, alpha: a };
  }

  class Ebene {
    constructor(leinwandBauen) {
      this.bauen = leinwandBauen;
      this.kacheln = new Map();
      this.farbe = '#000';
    }

    leeren() { this.kacheln.clear(); }

    kachel(tx, ty) {
      const schluessel = tx + ',' + ty;
      let k = this.kacheln.get(schluessel);
      if (!k) {
        const leinwand = this.bauen(KACHEL_PX);
        const c = leinwand.getContext('2d');
        k = { tx, ty, leinwand, c };
        this.kacheln.set(schluessel, k);
      }
      return k;
    }

    /* Ruft malen(c) für jede Kachel auf, die das Rechteck berührt - mit einer
     * Transformation, unter der in Weltmetern gezeichnet wird. */
    jeKachel(x0, y0, x1, y1, malen) {
      for (let tx = Math.floor(x0 / KACHEL_M); tx <= Math.floor(x1 / KACHEL_M); tx++) {
        for (let ty = Math.floor(y0 / KACHEL_M); ty <= Math.floor(y1 / KACHEL_M); ty++) {
          const k = this.kachel(tx, ty);
          k.c.setTransform(PX_PRO_M, 0, 0, -PX_PRO_M, -tx * KACHEL_M * PX_PRO_M, (ty + 1) * KACHEL_M * PX_PRO_M);
          k.c.fillStyle = this.farbe;
          k.c.strokeStyle = this.farbe;
          malen(k.c);
        }
      }
    }

    viereck(ecken) {
      const xs = ecken.map((p) => p[0]), ys = ecken.map((p) => p[1]);
      this.jeKachel(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys), (c) => {
        viereckMalen(c, ecken);
      });
    }

    zellen(liste, groesse) {
      const zeilen = new Map();
      for (const [ix, iy] of liste) {
        if (!zeilen.has(iy)) zeilen.set(iy, new Set());
        zeilen.get(iy).add(ix);
      }
      for (const [iy, zellen] of zeilen) {
        const xs = [...zellen].sort((a, b) => a - b);
        let anfang = xs[0], ende = anfang;
        const malen = () => {
          const x = anfang * groesse, y = iy * groesse, breite = (ende - anfang + 1) * groesse;
          this.jeKachel(x, y, x + breite - 1e-9, y + groesse - 1e-9,
            (c) => c.fillRect(x, y, breite, groesse));
        };
        for (const x of xs.slice(1)) {
          if (x === ende + 1) ende = x;
          else { malen(); anfang = ende = x; }
        }
        malen();
      }
    }

    umfaerben(farbe) {
      this.farbe = farbe;
      for (const k of this.kacheln.values()) {
        k.c.setTransform(1, 0, 0, 1, 0, 0);
        k.c.globalCompositeOperation = 'source-in';
        k.c.fillStyle = farbe;
        k.c.fillRect(0, 0, KACHEL_PX, KACHEL_PX);
        k.c.globalCompositeOperation = 'source-over';
      }
    }

    zeichnen(ctx, x0, y0, x1, y1, alpha, ecken = []) {
      const vorschau = new Map();
      for (const e of ecken) {
        const xs = e.map(p => p[0]), ys = e.map(p => p[1]);
        for (let tx = Math.floor(Math.min(...xs) / KACHEL_M); tx <= Math.floor(Math.max(...xs) / KACHEL_M); tx++) {
          for (let ty = Math.floor(Math.min(...ys) / KACHEL_M); ty <= Math.floor(Math.max(...ys) / KACHEL_M); ty++) {
            const key = tx + ',' + ty;
            if (!vorschau.has(key)) vorschau.set(key, { tx, ty, ecken: [] });
            vorschau.get(key).ecken.push(e);
          }
        }
      }
      ctx.save(); ctx.globalAlpha = alpha; ctx.imageSmoothingEnabled = true;
      const malen = (tx, ty, quelle, extra) => {
        const x = tx * KACHEL_M, y = ty * KACHEL_M;
        if (x > x1 || x + KACHEL_M < x0 || y > y1 || y + KACHEL_M < y0) return;
        if (extra) {
          // Eine wiederverwendete Arbeitskachel: Vorschau und gespeicherte
          // Fläche werden zusammen nur einmal transparent aufs Bild gelegt.
          if (!this.arbeitskachel) this.arbeitskachel = this.bauen(KACHEL_PX);
          const c = this.arbeitskachel.getContext('2d');
          c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, KACHEL_PX, KACHEL_PX);
          if (quelle) c.drawImage(quelle, 0, 0);
          c.setTransform(PX_PRO_M, 0, 0, -PX_PRO_M, -x * PX_PRO_M, (y + KACHEL_M) * PX_PRO_M);
          c.fillStyle = this.farbe; c.strokeStyle = this.farbe;
          for (const e of extra.ecken) viereckMalen(c, e);
          quelle = this.arbeitskachel;
        }
        ctx.save(); ctx.translate(x, y + KACHEL_M); ctx.scale(1 / PX_PRO_M, -1 / PX_PRO_M);
        ctx.drawImage(quelle, 0, 0); ctx.restore();
      };
      for (const [key, k] of this.kacheln) {
        malen(k.tx, k.ty, k.leinwand, vorschau.get(key)); vorschau.delete(key);
      }
      for (const extra of vorschau.values()) malen(extra.tx, extra.ty, null, extra);
      ctx.restore();
    }
  }

  class Flaeche {
    constructor(leinwandBauen) {
      this.bedeckt = new Ebene(leinwandBauen);
      this.doppelt = new Ebene(leinwandBauen);
      this.zellen = new Map();          // Schlüssel -> Zeitpunkt (0 = von früher)
      this.zellGroesse = 0.5;
      this.letzte = null;               // letzte Lage, bis zu der gemalt ist
      this.farben = { bedeckt: farbeZerlegen('rgba(93,255,142,.22)'), doppelt: farbeZerlegen('rgba(255,176,32,.5)') };
      this.bedeckt.farbe = this.farben.bedeckt.deckend;
      this.doppelt.farbe = this.farben.doppelt.deckend;
    }

    leeren() {
      this.bedeckt.leeren();
      this.doppelt.leeren();
      this.zellen.clear();
      this.letzte = null;
    }

    farbenSetzen(bedeckt, doppelt) {
      this.farben = { bedeckt: farbeZerlegen(bedeckt), doppelt: farbeZerlegen(doppelt) };
      this.bedeckt.umfaerben(this.farben.bedeckt.deckend);
      this.doppelt.umfaerben(this.farben.doppelt.deckend);
    }

    /* Fläche aus Zellen, die nicht diese Fahrt gemalt hat: der Stand beim
     * Verbinden, oder was ein anderer Traktor herübergeschickt hat. */
    zellenMalen(liste, groesse) {
      this.zellGroesse = groesse || this.zellGroesse;
      for (const [ix, iy] of liste) this.zellen.set(zellSchluessel(ix, iy), 0);
      this.bedeckt.zellen(liste, this.zellGroesse);
    }

    /* Zellen der eigenen Fahrt: gemalt ist schon, gemerkt wird für die Überlappung. */
    zellenMerken(liste, jetzt) {
      for (const [ix, iy] of liste) {
        const k = zellSchluessel(ix, iy);
        if (!this.zellen.has(k)) this.zellen.set(k, jetzt);
      }
    }

    istDoppelt(punkt, jetzt) {
      const g = this.zellGroesse;
      const seit = this.zellen.get(zellSchluessel(Math.floor(punkt[0] / g), Math.floor(punkt[1] / g)));
      return seit !== undefined && jetzt - seit > FRISCH_MS;
    }

    /* Eine neue Lage des Geräts. lage = { pos, kurs, stuecke, aktiv }. Gibt die
     * gemalten Vierecke zurück (für Tests und die Anzeige der Überlappung). */
    fahrt(lage, jetzt) {
      const alt = this.letzte;
      this.letzte = lage.aktiv ? lage : null;
      if (!lage.aktiv || !alt || alt.auftrag !== lage.auftrag || !lage.stuecke.length) return [];
      const weg = Math.hypot(lage.pos[0] - alt.pos[0], lage.pos[1] - alt.pos[1]);
      if (weg > MAX_SCHRITT_M || weg < 1e-4) {
        return [];
      }
      const liste = streifenVierecke(alt, lage);
      for (const v of liste) {
        v.doppelt = this.istDoppelt(v.mitte, jetzt);
        this.bedeckt.viereck(v.ecken);
        if (v.doppelt) this.doppelt.viereck(v.ecken);
      }
      return liste;
    }

    /* Das letzte Stück bis zur angezeigten Lage, nur auf den Bildschirm - so
     * wächst der Streifen mit dem Gerät statt in Schritten hinterher. */
    zeichnen(ctx, sicht, anzeige, jetzt = performance.now()) {
      const [x0, y0, x1, y1] = sicht;
      let ende = [];
      if (anzeige && anzeige.aktiv && this.letzte && anzeige.auftrag === this.letzte.auftrag) {
        const weg = Math.hypot(anzeige.pos[0] - this.letzte.pos[0], anzeige.pos[1] - this.letzte.pos[1]);
        if (weg <= 3 && weg >= 1e-3) ende = streifenVierecke(this.letzte, anzeige);
      }
      this.bedeckt.zeichnen(ctx, x0, y0, x1, y1, this.farben.bedeckt.alpha, ende.map(v => v.ecken));
      this.doppelt.zeichnen(ctx, x0, y0, x1, y1, this.farben.doppelt.alpha,
        ende.filter(v => this.istDoppelt(v.mitte, jetzt)).map(v => v.ecken));
    }
  }

  const ausfuhr = { Flaeche, querStuecke, vierecke, farbeZerlegen, zellSchluessel, FRISCH_MS };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.FlaecheModul = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
