/* Feldkarte: den Schlag auf der Karte auswählen statt aus der Liste (Menü → Felder).
 *
 * Alle Felder kommen in WGS84 (/api/feldkarte) und werden mit der flachen
 * Projektion der Fernansicht in Meter umgerechnet. Hier stehen nur Rechnung
 * und Treffer; gezeichnet wird in app.js. Geprüft mit node --test tests/feldkarte.test.js.
 */
(function (wurzel) {
  'use strict';

  const MIN_MASSSTAB = 0.002;   // Bildpunkte je Meter: ganz weit weg (viele Kilometer)
  const MAX_MASSSTAB = 8;       // ganz nah (ein Bildpunkt je 12 cm)

  /* Ansicht: welche Meterkoordinate in der Bildmitte liegt, und wie viele Bildpunkte ein Meter ist. */
  function einpassen(min, max, breite, hoehe, rand = 30, mindestM = 200) {
    const w = Math.max(mindestM, max[0] - min[0]);
    const h = Math.max(mindestM, max[1] - min[1]);
    const massstab = Math.min((breite - 2 * rand) / w, (hoehe - 2 * rand) / h);
    return {
      mitte: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2],
      massstab: Math.max(MIN_MASSSTAB, Math.min(MAX_MASSSTAB, massstab)),
    };
  }

  function nachBild(ansicht, breite, hoehe, p) {
    return [breite / 2 + (p[0] - ansicht.mitte[0]) * ansicht.massstab,
      hoehe / 2 - (p[1] - ansicht.mitte[1]) * ansicht.massstab];
  }

  function nachMetern(ansicht, breite, hoehe, px) {
    return [ansicht.mitte[0] + (px[0] - breite / 2) / ansicht.massstab,
      ansicht.mitte[1] - (px[1] - hoehe / 2) / ansicht.massstab];
  }

  /* Zoomen um einen Bildpunkt: was unter dem Finger liegt, bleibt unter dem Finger. */
  function zoomen(ansicht, faktor, px, breite, hoehe) {
    const massstab = Math.max(MIN_MASSSTAB, Math.min(MAX_MASSSTAB, ansicht.massstab * faktor));
    const vorher = nachMetern(ansicht, breite, hoehe, px);
    const neu = { mitte: ansicht.mitte, massstab };
    const nachher = nachMetern(neu, breite, hoehe, px);
    return { mitte: [ansicht.mitte[0] + vorher[0] - nachher[0], ansicht.mitte[1] + vorher[1] - nachher[1]], massstab };
  }

  /* Verschieben um so viele Bildpunkte, wie der Finger gewandert ist. */
  function verschieben(ansicht, dx, dy) {
    return { mitte: [ansicht.mitte[0] - dx / ansicht.massstab, ansicht.mitte[1] + dy / ansicht.massstab],
      massstab: ansicht.massstab };
  }

  function imPolygon(p, polygon) {
    let drin = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
      if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) drin = !drin;
    }
    return drin;
  }

  function flaeche(polygon) {
    let summe = 0;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
      summe += polygon[j][0] * polygon[i][1] - polygon[i][0] * polygon[j][1];
    }
    return Math.abs(summe) / 2;
  }

  function abstandZurKante(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const l2 = dx * dx + dy * dy;
    const t = l2 > 0 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }

  /* Welches Feld liegt unter dem Punkt (Meter)? Liegen zwei übereinander, das
   * kleinere - das größere trifft man auch daneben. Knapp daneben (``fangM``,
   * mit dem Handschuh) zählt die nächste Grenze. Kein Treffer: null. */
  function feldUnter(felder, p, fangM = 0) {
    const drin = felder.filter((f) => imPolygon(p, f.xy));
    if (drin.length) return drin.reduce((a, b) => (flaeche(b.xy) < flaeche(a.xy) ? b : a)).id;
    let bester = null, besterAbstand = fangM;
    felder.forEach((f) => {
      for (let i = 0; i < f.xy.length; i++) {
        const d = abstandZurKante(p, f.xy[i], f.xy[(i + 1) % f.xy.length]);
        if (d <= besterAbstand) { besterAbstand = d; bester = f.id; }
      }
    });
    return bester;
  }

  function schwerpunkt(polygon) {
    return [polygon.reduce((s, p) => s + p[0], 0) / polygon.length,
      polygon.reduce((s, p) => s + p[1], 0) / polygon.length];
  }

  const modul = { einpassen, nachBild, nachMetern, zoomen, verschieben, feldUnter, imPolygon, flaeche, schwerpunkt };
  if (typeof module !== 'undefined' && module.exports) module.exports = modul;
  else wurzel.Feldkarte = modul;
})(typeof window !== 'undefined' ? window : globalThis);
