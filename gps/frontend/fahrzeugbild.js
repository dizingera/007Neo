/* Fahrzeugbild: Traktor, Mähdrescher, Knicklenker und Geräte von oben.
 *
 * Gezeichnet in Metern ab Mitte Hinterachse (+y vorn, +x rechts), in die
 * Zeichenfläche, die app.js schon gedreht und verschoben hat. Was hier anders
 * ist als früher:
 *
 *   - Farben je Marke (keine Logos - nur die Lackfarben, an denen man die
 *     eigenen Maschinen erkennt),
 *   - die Räder lenken mit: mit dem echten Radwinkel vom Drehgeber, sonst mit
 *     dem Befehl. So sieht man am Bild, ob der Lenkmotor nachkommt,
 *   - Spurweite aus den Maßen des Traktors,
 *   - das Gerät je Art von oben (Grubber, Egge, Sämaschine, Spritze …).
 *
 * Farben und Winkel sind reine Rechnung und mit node --test geprüft
 * (tests/fahrzeugbild.test.js); das Zeichnen braucht einen Canvas.
 */
(function (wurzel) {
  'use strict';

  /* Lackfarben: Haube, hell (Glanz), dunkel (Schatten), Akzent, Dach, Felge. */
  const MARKEN = {
    standard: { name: 'AgriPilot (Standard)' },
    johndeere: { name: 'John Deere', lack: '#367c2b', hell: '#55a443', dunkel: '#22521b', akzent: '#ffde00', dach: '#367c2b', felge: '#ffde00' },
    fendt: { name: 'Fendt', lack: '#4d8a36', hell: '#6fae4e', dunkel: '#2f5a22', akzent: '#b9bdbf', dach: '#2f3337', felge: '#9aa0a4' },
    claas: { name: 'Claas', lack: '#b3c41c', hell: '#cddb4a', dunkel: '#7f8c12', akzent: '#d20a11', dach: '#f2f2f2', felge: '#b3c41c' },
    case: { name: 'Case IH / Steyr', lack: '#c8102e', hell: '#e23a52', dunkel: '#8a0b20', akzent: '#1c1c1c', dach: '#1c1c1c', felge: '#b9bdbf' },
    newholland: { name: 'New Holland', lack: '#0a4da2', hell: '#2c6fc6', dunkel: '#073472', akzent: '#ffd100', dach: '#f2f2f2', felge: '#e8e8e8' },
    deutz: { name: 'Deutz-Fahr', lack: '#76b82a', hell: '#97cf53', dunkel: '#4c7d1a', akzent: '#3a3f44', dach: '#3a3f44', felge: '#3a3f44' },
    massey: { name: 'Massey Ferguson', lack: '#b0131d', hell: '#d03440', dunkel: '#780c13', akzent: '#8c9196', dach: '#8c9196', felge: '#8c9196' },
    valtra: { name: 'Valtra', lack: '#d6d8da', hell: '#f2f3f4', dunkel: '#9a9ea2', akzent: '#c8102e', dach: '#1c1c1c', felge: '#1c1c1c' },
    kubota: { name: 'Kubota', lack: '#f15a22', hell: '#f6824f', dunkel: '#b0400f', akzent: '#1c1c1c', dach: '#1c1c1c', felge: '#f15a22' },
  };

  const BAUFORMEN = { traktor: 'Traktor', maehdrescher: 'Mähdrescher', knicklenker: 'Knicklenker' };

  const GERAETE = {
    balken: 'Einfacher Balken',
    grubber: 'Grubber',
    scheibenegge: 'Scheibenegge',
    saemaschine: 'Sämaschine',
    spritze: 'Feldspritze',
    streuer: 'Düngerstreuer',
    pflug: 'Pflug',
    maehwerk: 'Mähwerk',
    walze: 'Walze',
    schneidwerk: 'Schneidwerk (Mähdrescher, vorn)',
  };

  /* Vorderkante des Mähdreschers ab Bezugsachse (m): bis hierher reicht der
     Schrägförderer vom Schneidwerk zurück. */
  const DRESCHER_VORN = 3.9;

  /* Die Farben für eine Marke; "standard" nimmt die Farben der Oberfläche (F). */
  function farben(marke, F) {
    const m = MARKEN[marke];
    if (!m || !m.lack) {
      return { lack: F.lack, hell: F['lack-hell'], dunkel: F['lack-dunkel'], akzent: F.akzent,
        dach: F.lack, felge: F.akzent };
    }
    return m;
  }

  /* Wie weit die Räder eingeschlagen sind (Grad, + rechts): gemessen vor befohlen. */
  function radwinkel(s) {
    const lenkung = s && s.steering;
    if (lenkung && Number.isFinite(lenkung.wheel_angle_deg)) return lenkung.wheel_angle_deg;
    if (lenkung && lenkung.command && lenkung.command.engaged) return lenkung.command.angle_deg || 0;
    return 0;
  }

  /* Gerätetiefe von oben (m) - grob, wie solche Geräte gebaut sind. */
  const TIEFE = { balken: 0, grubber: 2.4, scheibenegge: 2.6, saemaschine: 2.8, spritze: 2.0,
    streuer: 1.5, pflug: 0, maehwerk: 1.4, walze: 1.4 };

  /* --------------------------------------------------------- Grundformen */

  function rundRechteck(ctx, x, y, b, h, r) {
    const rr = Math.max(0, Math.min(r, b / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + b, y, x + b, y + h, rr);
    ctx.arcTo(x + b, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + b, y, rr);
    ctx.closePath();
  }

  function schatten(ctx, an) {
    ctx.shadowColor = an ? 'rgba(0,0,0,.5)' : 'transparent';
    ctx.shadowBlur = an ? 10 : 0;
  }

  /* Ein Reifen mit Stollen und Felgenmitte; ``winkel`` dreht ihn um seine Mitte. */
  function reifen(ctx, mx, my, breite, laenge, px, farbe, felge, winkel = 0) {
    ctx.save();
    ctx.translate(mx, my);
    if (winkel) ctx.rotate(-winkel * Math.PI / 180);   // Welt mit y oben: rechts = im Uhrzeigersinn
    rundRechteck(ctx, -breite / 2, -laenge / 2, breite, laenge, breite * 0.28);
    ctx.fillStyle = farbe;
    ctx.fill();
    ctx.save();
    ctx.clip();
    ctx.strokeStyle = 'rgba(255,255,255,.13)';
    ctx.lineWidth = Math.max(0.05, 1.2 * px);
    const schritt = laenge / 9;
    for (let s = -laenge / 2 - schritt; s <= laenge / 2 + schritt; s += schritt) {
      ctx.beginPath();
      ctx.moveTo(-breite / 2, s);
      ctx.lineTo(0, s + schritt * 0.45);
      ctx.lineTo(breite / 2, s);
      ctx.stroke();
    }
    ctx.restore();
    if (felge) {
      ctx.fillStyle = felge;
      ctx.globalAlpha *= 0.85;
      ctx.fillRect(-breite * 0.08, -laenge * 0.32, breite * 0.16, laenge * 0.64);
    }
    ctx.restore();
  }

  function kabine(ctx, x, y, b, h, p, px, F) {
    rundRechteck(ctx, x, y, b, h, 0.16);
    ctx.fillStyle = F.kabine;
    schatten(ctx, true); ctx.fill(); schatten(ctx, false);
    rundRechteck(ctx, x + 0.1, y + 0.12, b - 0.2, h - 0.24, 0.14);
    const dach = ctx.createRadialGradient(x + b * 0.35, y + h * 0.65, 0.1, x + b / 2, y + h / 2, Math.max(b, h));
    dach.addColorStop(0, p.dach === p.lack ? p.hell : p.dach);
    dach.addColorStop(1, p.dach);
    ctx.fillStyle = dach;
    ctx.fill();
    ctx.lineWidth = px; ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.stroke();
    ctx.beginPath();                                   // Rundumleuchte
    ctx.arc(x + b - 0.36, y + 0.36, 0.09, 0, Math.PI * 2);
    ctx.fillStyle = '#ffb020';
    ctx.fill();
  }

  function haube(ctx, von, bis, halb, p, px) {
    ctx.beginPath();
    ctx.moveTo(-halb, von);
    ctx.lineTo(-halb * 0.88, bis - 0.35);
    ctx.quadraticCurveTo(-halb * 0.84, bis - 0.04, 0, bis);
    ctx.quadraticCurveTo(halb * 0.84, bis - 0.04, halb * 0.88, bis - 0.35);
    ctx.lineTo(halb, von);
    ctx.closePath();
    const g = ctx.createLinearGradient(-halb, 0, halb, 0);
    g.addColorStop(0, p.dunkel); g.addColorStop(0.42, p.hell); g.addColorStop(0.58, p.hell); g.addColorStop(1, p.dunkel);
    ctx.fillStyle = g;
    schatten(ctx, true); ctx.fill(); schatten(ctx, false);
    ctx.lineWidth = 1.2 * px; ctx.strokeStyle = 'rgba(0,0,0,.55)'; ctx.stroke();
  }

  /* ------------------------------------------------------------ Traktor */

  function traktor(ctx, o) {
    const { profil, px, F, p, winkel } = o;
    const radstand = Math.max(2.2, Math.min(3.4, profil.wheelbase_m || 2.6));
    const spur = Math.max(0.7, Math.min(1.4, (profil.spurweite_m || 1.9) / 2));
    const hb = Math.max(0.45, Math.min(0.8, spur * 0.62));     // Hinterreifen breit
    reifen(ctx, -spur, 0, hb, 1.75, px, F.reifen, p.felge);
    reifen(ctx, spur, 0, hb, 1.75, px, F.reifen, p.felge);
    reifen(ctx, -spur + 0.08, radstand, hb * 0.76, 1.15, px, F.reifen, p.felge, winkel);
    reifen(ctx, spur - 0.08, radstand, hb * 0.76, 1.15, px, F.reifen, p.felge, winkel);
    ctx.fillStyle = p.dunkel;                                   // Vorderachse, Frontgewicht
    ctx.fillRect(-spur + 0.1, radstand - 0.09, 2 * spur - 0.2, 0.18);
    rundRechteck(ctx, -0.5, radstand + 0.78, 1.0, 0.38, 0.08);
    ctx.fillStyle = '#2b2f2d'; ctx.fill();
    haube(ctx, 0.75, radstand + 0.9, 0.5, p, px);
    ctx.fillStyle = p.akzent;                                   // Zierstreifen
    ctx.fillRect(-0.47, 0.95, 0.06, radstand - 0.6);
    ctx.fillRect(0.41, 0.95, 0.06, radstand - 0.6);
    ctx.strokeStyle = 'rgba(0,0,0,.35)'; ctx.lineWidth = Math.max(0.03, px);
    for (let i = 0; i < 4; i++) {
      const y = radstand + 0.1 + i * 0.12;
      ctx.beginPath(); ctx.moveTo(-0.22, y); ctx.lineTo(0.22, y); ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(0.3, 1.05, 0.075, 0, Math.PI * 2); ctx.fillStyle = '#111'; ctx.fill();
    [-1, 1].forEach((seite) => {                               // Kotflügel
      rundRechteck(ctx, seite * spur - 0.36, -0.82, 0.72, 1.64, 0.3);
      ctx.fillStyle = p.lack; schatten(ctx, true); ctx.fill(); schatten(ctx, false);
      ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 1.2 * px; ctx.stroke();
    });
    kabine(ctx, -0.78, -0.72, 1.56, 1.5, p, px, F);
    ctx.fillStyle = '#2b2f2d';                                  // Heckkraftheber
    ctx.fillRect(-0.35, -1.0, 0.7, 0.22);
    return radstand + 1.2;
  }

  /* Mähdrescher: vorn breite Achse mit Zwillingsreifen, hinten lenkt (gegenläufig). */
  function maehdrescher(ctx, o) {
    const { px, F, p, winkel } = o;
    const radstand = 3.6;
    reifen(ctx, -1.35, radstand, 0.75, 1.9, px, F.reifen, p.felge);
    reifen(ctx, 1.35, radstand, 0.75, 1.9, px, F.reifen, p.felge);
    reifen(ctx, -1.05, 0, 0.5, 1.2, px, F.reifen, p.felge, -winkel);
    reifen(ctx, 1.05, 0, 0.5, 1.2, px, F.reifen, p.felge, -winkel);
    rundRechteck(ctx, -1.0, -0.6, 2.0, radstand + 0.9, 0.25);  // Körper
    const g = ctx.createLinearGradient(-1, 0, 1, 0);
    g.addColorStop(0, p.dunkel); g.addColorStop(0.5, p.hell); g.addColorStop(1, p.dunkel);
    ctx.fillStyle = g; schatten(ctx, true); ctx.fill(); schatten(ctx, false);
    ctx.strokeStyle = 'rgba(0,0,0,.5)'; ctx.lineWidth = 1.2 * px; ctx.stroke();
    rundRechteck(ctx, -0.8, 0.6, 1.6, 1.6, 0.15);               // Korntank
    ctx.fillStyle = 'rgba(0,0,0,.22)'; ctx.fill();
    ctx.fillStyle = p.akzent;                                   // Abtankrohr links, eingeklappt
    ctx.fillRect(-1.0, 1.2, 0.18, 2.4);
    kabine(ctx, -0.75, radstand - 0.2, 1.5, 1.2, p, px, F);
    return radstand + 1.2;
  }

  /* Knicklenker: vorderer Teil knickt um den Gelenkpunkt in der Mitte. */
  function knicklenker(ctx, o) {
    const { px, F, p, winkel } = o;
    const radstand = 3.6, spur = 1.15, gelenk = radstand / 2;
    reifen(ctx, -spur, 0, 0.75, 1.9, px, F.reifen, p.felge);
    reifen(ctx, spur, 0, 0.75, 1.9, px, F.reifen, p.felge);
    rundRechteck(ctx, -0.7, -0.9, 1.4, gelenk + 0.6, 0.2);
    ctx.fillStyle = p.lack; ctx.fill();
    ctx.save();
    ctx.translate(0, gelenk);
    ctx.rotate(-winkel * Math.PI / 180);
    reifen(ctx, -spur, gelenk, 0.75, 1.9, px, F.reifen, p.felge);
    reifen(ctx, spur, gelenk, 0.75, 1.9, px, F.reifen, p.felge);
    ctx.translate(0, -gelenk);
    haube(ctx, gelenk + 0.1, radstand + 1.1, 0.6, p, px);
    ctx.restore();
    kabine(ctx, -0.8, -0.2, 1.6, 1.6, p, px, F);
    return radstand + 1.3;
  }

  function fahrzeug(ctx, o) {
    const form = o.profil.bauform;
    if (form === 'maehdrescher') return maehdrescher(ctx, o);
    if (form === 'knicklenker') return knicklenker(ctx, o);
    return traktor(ctx, o);
  }

  /* -------------------------------------------------------------- Geräte
   * Gezeichnet ab Arbeitspunkt (y = 0) nach vorn zur Maschine hin, über die
   * ganze Arbeitsbreite. Die Teilbreiten legt app.js darüber. Das Schneidwerk
   * sitzt vor dem Mähdrescher: es reicht vom Messerbalken nach hinten. */

  /* Schneidwerk von oben: Halmteiler, Messerbalken, Haspel, Mulde mit
     Einzugsschnecke, Schrägförderer zurück bis an den Mähdrescher (platz). */
  function schneidwerk(ctx, breite, px, platz, p) {
    const b = breite / 2, tief = 1.3, stahl = '#8f979e';
    const lack = (p && p.lack) || '#b3c41c', dunkel = (p && p.dunkel) || '#7f8c12';
    const akzent = (p && p.akzent) || '#d20a11';
    if (Number.isFinite(platz) && platz > tief) {                                           // Schrägförderer
      rundRechteck(ctx, -0.6, -platz - 0.1, 1.2, platz - tief + 0.2, 0.08);
      ctx.fillStyle = dunkel; ctx.fill();
    }
    rundRechteck(ctx, -b, -tief, breite, tief - 0.02, 0.18);          // Mulde
    ctx.fillStyle = lack; schatten(ctx, true); ctx.fill(); schatten(ctx, false);
    ctx.strokeStyle = 'rgba(0,0,0,.55)'; ctx.lineWidth = 1.2 * px; ctx.stroke();
    rundRechteck(ctx, -b + 0.12, -tief + 0.14, breite - 0.24, tief - 0.24, 0.1);
    ctx.fillStyle = 'rgba(0,0,0,.28)'; ctx.fill();
    ctx.lineCap = 'round';
    const linie = (x0, y0, x1, y1, w, farbe) => {
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      ctx.lineWidth = Math.max(w, 1.2 * px); ctx.strokeStyle = farbe; ctx.stroke();
    };
    linie(-b + 0.2, -1.0, b - 0.2, -1.0, 0.16, stahl);                // Einzugsschnecke
    for (let x = -b + 0.35; x < b - 0.2; x += 0.35) linie(x - 0.08, -1.08, x + 0.08, -0.92, 0.03, '#5d656c');
    [-0.2, -0.42, -0.64].forEach((y) => linie(-b + 0.3, y, b - 0.3, y, 0.06, akzent));   // Haspel
    [-b + 0.18, b - 0.18].forEach((x) => linie(x, -0.15, x, -0.8, 0.07, dunkel));
    linie(-b, 0, b, 0, 0.05, stahl);                                  // Messerbalken
    for (let x = -b + 0.08; x < b; x += 0.16) linie(x, 0, x, 0.08, 0.02, stahl);
    [-1, 1].forEach((seite) => {                                      // Halmteiler
      ctx.beginPath();
      ctx.moveTo(seite * b, -0.3); ctx.lineTo(seite * (b - 0.12), 0.45); ctx.lineTo(seite * (b - 0.25), -0.3);
      ctx.closePath(); ctx.fillStyle = dunkel; ctx.fill();
    });
  }

  function geraet(ctx, art, breite, px, F, platz = Infinity, p = null) {
    const b = breite / 2;
    const rahmen = '#3b4148', stahl = '#8f979e', rot = '#c62828';
    if (art === 'schneidwerk') {
      ctx.save(); schneidwerk(ctx, breite, px, platz, p); ctx.restore();
      return;
    }
    ctx.save();
    // Kurz angebaut: der Aufbau endet vorn am Heck des Traktors und reicht dafür
    // hinter den Arbeitspunkt - in echter Tiefe, nicht gestaucht.
    const tief = TIEFE[art] || (art === 'pflug' ? Math.max(2, Math.round(breite / 0.45)) * 0.6 + 0.2 : 0);
    if (tief > 0 && platz < tief) ctx.translate(0, platz - tief);
    ctx.lineCap = 'round';
    const linie = (x0, y0, x1, y1, w, farbe) => {
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      ctx.lineWidth = Math.max(w, 1.4 * px); ctx.strokeStyle = farbe; ctx.stroke();
    };
    const zugdreieck = (y0, y1) => { linie(-0.5, y0, 0, y1, 0.08, rahmen); linie(0.5, y0, 0, y1, 0.08, rahmen); };
    if (art === 'grubber') {
      [0.4, 1.2, 2.0].forEach((y) => linie(-b, y, b, y, 0.12, rahmen));
      for (let reihe = 0; reihe < 3; reihe++) {
        const y = 0.4 + reihe * 0.8, versatz = (reihe % 2) * 0.25;
        for (let x = -b + 0.15 + versatz; x <= b - 0.1; x += 0.5) {
          ctx.beginPath(); ctx.arc(x, y - 0.15, 0.07, 0, Math.PI * 2); ctx.fillStyle = stahl; ctx.fill();
        }
      }
      zugdreieck(2.0, 2.4);
    } else if (art === 'scheibenegge') {
      [0.5, 1.6].forEach((y, reihe) => {
        linie(-b, y, b, y, 0.1, rahmen);
        for (let x = -b + 0.12; x <= b - 0.1; x += 0.25) {
          ctx.save(); ctx.translate(x, y - 0.2); ctx.rotate((reihe ? -1 : 1) * 0.35);
          ctx.beginPath(); ctx.ellipse(0, 0, 0.05, 0.25, 0, 0, Math.PI * 2); ctx.fillStyle = stahl; ctx.fill();
          ctx.restore();
        }
      });
      zugdreieck(1.6, 2.6);
    } else if (art === 'saemaschine') {
      linie(-b, 0.2, b, 0.2, 0.08, rahmen);
      for (let x = -b + 0.125; x < b; x += 0.25) linie(x, 0.05, x, 0.45, 0.03, stahl);
      rundRechteck(ctx, -b + 0.1, 0.8, breite - 0.2, 1.4, 0.15);       // Saatgutbehälter
      ctx.fillStyle = '#d9a400'; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.lineWidth = px; ctx.stroke();
      zugdreieck(2.2, 2.8);
    } else if (art === 'spritze') {
      linie(-b, 0, b, 0, 0.1, rahmen);                                // Gestänge
      for (let x = -b + 0.25; x < b; x += 0.5) {
        ctx.beginPath(); ctx.arc(x, 0, 0.05, 0, Math.PI * 2); ctx.fillStyle = '#4b9dff'; ctx.fill();
      }
      ctx.beginPath(); ctx.ellipse(0, 1.1, 0.75, 1.0, 0, 0, Math.PI * 2);   // Tank
      ctx.fillStyle = '#e8eaec'; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.4)'; ctx.lineWidth = px; ctx.stroke();
      zugdreieck(1.8, 2.0);
    } else if (art === 'streuer') {
      ctx.beginPath(); ctx.moveTo(-0.9, 1.5); ctx.lineTo(0.9, 1.5); ctx.lineTo(0.5, 0.4); ctx.lineTo(-0.5, 0.4); ctx.closePath();
      ctx.fillStyle = '#3d6fb6'; ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,.45)'; ctx.lineWidth = px; ctx.stroke();
      [-0.32, 0.32].forEach((x) => { ctx.beginPath(); ctx.arc(x, 0.25, 0.22, 0, Math.PI * 2); ctx.fillStyle = stahl; ctx.fill(); });
      ctx.save(); ctx.globalAlpha *= 0.18;                            // Streufächer
      ctx.beginPath(); ctx.moveTo(0, 0.25); ctx.lineTo(-b, -0.6); ctx.lineTo(b, -0.6); ctx.closePath();
      ctx.fillStyle = '#3d6fb6'; ctx.fill(); ctx.restore();
    } else if (art === 'pflug') {
      const scharen = Math.max(2, Math.round(breite / 0.45));
      linie(-b + 0.2, 0.2, b - 0.2, 0.2 + scharen * 0.6, 0.12, rahmen);      // schräger Rahmen
      for (let i = 0; i < scharen; i++) {
        const x = -b + 0.2 + (i + 0.5) * (breite - 0.4) / scharen, y = 0.2 + (i + 0.5) * 0.6;
        ctx.save(); ctx.translate(x, y); ctx.rotate(0.6);
        rundRechteck(ctx, -0.12, -0.35, 0.24, 0.7, 0.06); ctx.fillStyle = rot; ctx.fill();
        ctx.restore();
      }
    } else if (art === 'maehwerk') {
      rundRechteck(ctx, -b, 0.1, breite, 0.5, 0.2); ctx.fillStyle = '#c62828'; ctx.fill();
      for (let x = -b + 0.3; x < b; x += 0.6) {
        ctx.beginPath(); ctx.arc(x, 0.35, 0.2, 0, Math.PI * 2); ctx.fillStyle = stahl; ctx.fill();
      }
      zugdreieck(0.6, 1.4);
    } else if (art === 'walze') {
      const segmente = Math.max(1, Math.round(breite / 3));
      for (let i = 0; i < segmente; i++) {
        const x0 = -b + i * breite / segmente;
        rundRechteck(ctx, x0 + 0.05, 0.1, breite / segmente - 0.1, 0.6, 0.25); ctx.fillStyle = stahl; ctx.fill();
      }
      linie(-b, 0.9, b, 0.9, 0.1, rahmen);
      zugdreieck(0.9, 1.4);
    }
    ctx.restore();
  }

  const ausfuhr = { MARKEN, BAUFORMEN, GERAETE, TIEFE, DRESCHER_VORN, farben, radwinkel, fahrzeug, geraet };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.Fahrzeugbild = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
