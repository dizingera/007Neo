/* Fernansicht: Hilfen für die Übersicht aller Maschinen (Menü → Maschinen).
 *
 * Die Maschinen melden ihre Lage in WGS84; jede kann auf einem anderen Feld
 * stehen, mit eigener Feldebene. Für die Übersicht reicht eine gemeinsame,
 * flache Projektion um den Mittelpunkt aller Punkte - auf ein paar Kilometer
 * ist der Fehler kleiner als ein Pixel. Hier stehen nur Rechnung und Text;
 * gezeichnet wird in app.js. Geprüft mit node --test tests/fernansicht.test.js.
 */
(function (wurzel) {
  'use strict';

  const ERDRADIUS_M = 6371000;

  /* Aus allen [lat, lon] eine Projektion nach Metern um ihren Mittelpunkt,
   * dazu die Ausdehnung. Ohne Punkte: null. */
  function projektion(punkte) {
    const gueltig = (punkte || []).filter((p) => p && Number.isFinite(p[0]) && Number.isFinite(p[1]));
    if (!gueltig.length) return null;
    const lat0 = gueltig.reduce((s, p) => s + p[0], 0) / gueltig.length;
    const lon0 = gueltig.reduce((s, p) => s + p[1], 0) / gueltig.length;
    const k = Math.cos((lat0 * Math.PI) / 180);
    const nachMetern = ([lat, lon]) => [
      ((lon - lon0) * Math.PI) / 180 * ERDRADIUS_M * k,
      ((lat - lat0) * Math.PI) / 180 * ERDRADIUS_M,
    ];
    const xy = gueltig.map(nachMetern);
    const xs = xy.map((p) => p[0]);
    const ys = xy.map((p) => p[1]);
    return {
      nachMetern,
      min: [Math.min(...xs), Math.min(...ys)],
      max: [Math.max(...xs), Math.max(...ys)],
    };
  }

  /* Maßstab und Verschiebung, damit die Ausdehnung mit Rand in die Fläche passt. */
  function einpassen(proj, breite, hoehe, rand = 40, mindestM = 300) {
    const w = Math.max(mindestM, proj.max[0] - proj.min[0]);
    const h = Math.max(mindestM, proj.max[1] - proj.min[1]);
    const massstab = Math.min((breite - 2 * rand) / w, (hoehe - 2 * rand) / h);
    const mitte = [(proj.min[0] + proj.max[0]) / 2, (proj.min[1] + proj.max[1]) / 2];
    return (p) => [breite / 2 + (p[0] - mitte[0]) * massstab, hoehe / 2 - (p[1] - mitte[1]) * massstab];
  }

  function alterText(sekunden) {
    if (!(sekunden >= 0)) return '';
    if (sekunden < 15) return 'gerade eben';
    if (sekunden < 90) return `vor ${Math.round(sekunden)} s`;
    return `vor ${Math.round(sekunden / 60)} min`;
  }

  /* Was die Maschine gerade tut, in einer Zeile. */
  function zustandText(m) {
    const teile = [];
    if (m.feld) teile.push(m.feld);
    if (m.markiert) {
      const ha = Number(m.flaeche_ha || 0).toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      teile.push(`${m.arbeit || 'Arbeit'} · ${ha} ha`);
    }
    const kmh = (Number(m.tempo_ms || 0) * 3.6);
    teile.push(kmh >= 0.5 ? `${kmh.toLocaleString('de-DE', { maximumFractionDigits: 1 })} km/h` : 'steht');
    if (m.lenkt) teile.push('lenkt');
    if (m.fix) teile.push(m.fix);
    return teile.join(' · ');
  }

  /* Liegt die Meldung so lange zurück, dass die Lage nicht mehr gilt? */
  function veraltet(m) { return !(m.alter_s < 60); }

  const ausfuhr = { projektion, einpassen, alterText, zustandText, veraltet };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.Fernansicht = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
