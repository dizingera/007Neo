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
      const mitte = seitlich(neu.pos, neu.kurs, (links + rechts) / 2);
      return { ecken, mitte };
    });
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
        c.beginPath();
        ecken.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
        c.closePath();
        c.fill();
        // Ein Hauch Kontur schließt die Haarfugen zwischen zwei Vierecken.
        c.lineWidth = 0.6 / PX_PRO_M;
        c.stroke();
      });
    }

    zellen(liste, groesse) {
      for (const [ix, iy] of liste) {
        const x = ix * groesse, y = iy * groesse;
        this.jeKachel(x, y, x + groesse * 0.999, y + groesse * 0.999,
          (c) => c.fillRect(x, y, groesse, groesse));
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

    zeichnen(ctx, x0, y0, x1, y1, alpha) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.imageSmoothingEnabled = true;
      for (const k of this.kacheln.values()) {
        const kx0 = k.tx * KACHEL_M, ky0 = k.ty * KACHEL_M;
        if (kx0 > x1 || kx0 + KACHEL_M < x0 || ky0 > y1 || ky0 + KACHEL_M < y0) continue;
        ctx.save();
        ctx.translate(kx0, ky0 + KACHEL_M);
        ctx.scale(1 / PX_PRO_M, -1 / PX_PRO_M);
        ctx.drawImage(k.leinwand, 0, 0);
        ctx.restore();
      }
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
      if (!lage.aktiv || !alt || !lage.stuecke.length) return [];
      const weg = Math.hypot(lage.pos[0] - alt.pos[0], lage.pos[1] - alt.pos[1]);
      if (weg > MAX_SCHRITT_M || weg < 1e-4) {
        if (weg < 1e-4) this.letzte = alt;
        return [];
      }
      const liste = vierecke(alt, lage, lage.stuecke);
      for (const v of liste) {
        v.doppelt = this.istDoppelt(v.mitte, jetzt);
        this.bedeckt.viereck(v.ecken);
        if (v.doppelt) this.doppelt.viereck(v.ecken);
      }
      return liste;
    }

    /* Das letzte Stück bis zur angezeigten Lage, nur auf den Bildschirm - so
     * wächst der Streifen mit dem Gerät statt in Schritten hinterher. */
    zeichnen(ctx, sicht, anzeige) {
      const [x0, y0, x1, y1] = sicht;
      this.bedeckt.zeichnen(ctx, x0, y0, x1, y1, this.farben.bedeckt.alpha);
      this.doppelt.zeichnen(ctx, x0, y0, x1, y1, this.farben.doppelt.alpha);
      if (!anzeige || !this.letzte || !anzeige.stuecke.length) return;
      const weg = Math.hypot(anzeige.pos[0] - this.letzte.pos[0], anzeige.pos[1] - this.letzte.pos[1]);
      if (weg > 3 || weg < 1e-3) return;
      ctx.save();
      ctx.globalAlpha = this.farben.bedeckt.alpha;
      ctx.fillStyle = this.farben.bedeckt.deckend;
      for (const v of vierecke(this.letzte, anzeige, this.letzte.stuecke)) {
        ctx.beginPath();
        v.ecken.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    }
  }

  const ausfuhr = { Flaeche, querStuecke, vierecke, farbeZerlegen, zellSchluessel, FRISCH_MS };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.FlaecheModul = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
