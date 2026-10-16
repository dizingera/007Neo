/* Parallele Spuren für die Anzeige. Die Rechnung bleibt identisch zur bisherigen
 * Darstellung; nur das aktuelle Muster wird zwischen den Kartenbildern gehalten.
 * Zustände kommen als neue, unveränderliche Objekte aus dem WebSocket. */
(function (wurzel) {
  'use strict';

  function versetzen(line, shift) {
    const points = line.points;
    if (points.length < 2) return [];
    if (line.mode === 'ab') {
      const [a, b] = [points[0], points[points.length - 1]];
      const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
      const forward = [Math.sin(heading), Math.cos(heading)];
      const right = [Math.cos(heading), -Math.sin(heading)];
      const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const half = Math.max(600, Math.hypot(b[0] - a[0], b[1] - a[1])) / 2;
      return [-half, half].map((t) => [
        mid[0] + forward[0] * t + right[0] * shift,
        mid[1] + forward[1] * t + right[1] * shift,
      ]);
    }
    if (line.mode === 'contour') {
      // Geschlossener Ring, versetzt nach innen. Der Umlaufsinn der Grenze wird
      // herausgerechnet – sonst läge Ring 1 mal drinnen und mal draußen, je
      // nachdem, wie herum jemand das Feld abgefahren hat.
      const n = points.length;
      let area = 0;
      for (let i = 0; i < n; i++) {
        const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % n];
        area += x1 * y2 - x2 * y1;
      }
      const inward = area > 0 ? -1 : 1;
      return points.map((p, index) => {
        const a = points[(index - 1 + n) % n], b = points[(index + 1) % n];
        const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
        return [p[0] + Math.cos(heading) * shift * inward,
                p[1] - Math.sin(heading) * shift * inward];
      });
    }
    return points.map((p, index) => {
      const a = points[Math.max(0, index - 1)];
      const b = points[Math.min(points.length - 1, index + 1)];
      const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
      return [p[0] + Math.cos(heading) * shift, p[1] - Math.sin(heading) * shift];
    });
  }

  class SpurPuffer {
    constructor() {
      this.linie = null;
      this.nummer = null;
      this.schluessel = null;
      this.muster = [];
    }

    spuren(linie, nummer) {
      if (!linie) {
        this.linie = null;
        this.schluessel = null;
        this.muster = [];
        return this.muster;
      }
      // Zwischen zwei Datenpaketen reicht der Objektvergleich. Neue Pakete mit
      // gleicher Geometrie behalten das Muster; neue Punkte und Versätze nicht.
      if (linie === this.linie && nummer === this.nummer) return this.muster;
      const schluessel = JSON.stringify([linie.mode, linie.points, linie.spacing_m,
        linie.nudge_m || 0, linie.raster_m || 0, nummer]);
      this.linie = linie;
      this.nummer = nummer;
      if (schluessel === this.schluessel) return this.muster;
      this.schluessel = schluessel;
      this.muster = [];
      for (let n = nummer - 6; n <= nummer + 6; n++) {
        if (linie.mode === 'contour' && n < 0) continue;
        this.muster.push({ nummer: n, punkte: versetzen(linie,
          n * linie.spacing_m + (linie.nudge_m || 0) + (linie.raster_m || 0)) });
      }
      return this.muster;
    }
  }

  const ausfuhr = { SpurPuffer, versetzen };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.SpurenModul = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
