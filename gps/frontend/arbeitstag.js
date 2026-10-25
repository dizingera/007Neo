/* Arbeitstag: eigenständige Ansicht; Navigation und Freigaben bleiben bei der Anwendung. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Arbeitstag = factory();
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';
  const liste = v => Array.isArray(v) ? v : [];
  const text = (v, ersatz = 'Nicht bekannt') => v === null || v === undefined || v === '' ? ersatz :
    typeof v === 'boolean' ? (v ? 'Ja' : 'Nein') : typeof v === 'object' ? Object.entries(v).map(([k, x]) => `${k}: ${text(x, '–')}`).join(' · ') : String(v);
  const zahl = (v, stellen = 1) => v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? '–' :
    Number(v).toLocaleString('de-DE', { maximumFractionDigits: stellen });
  const zeit = v => {
    if (v === null || v === undefined || v === '') return 'Zeit unbekannt';
    const d = new Date(typeof v === 'number' && v < 1e12 ? v * 1000 : v);
    return Number.isNaN(d.getTime()) ? 'Zeit unbekannt' : d.toLocaleString('de-DE');
  };
  const dauer = v => {
    const n = Number(v);
    if (v === null || v === undefined || v === '' || !Number.isFinite(n) || n < 0) return 'Dauer unbekannt';
    return n < 60 ? `${zahl(n, 0)} s` : `${Math.floor(n / 60)} min ${Math.floor(n % 60)} s`;
  };
  function auspacken(v) {
    if (!v || typeof v !== 'object' || (v.ok === false && !Array.isArray(v.befunde) && !('felder' in v && 'auftraege' in v))) throw new Error(text(v && (v.detail || v.fehler), 'Keine gültige Antwort erhalten.'));
    return Object.prototype.hasOwnProperty.call(v, 'data') ? v.data : v;
  }
  function uebungsUrl(v, basis) {
    try {
      if (typeof v !== 'string' || !v.trim()) return null;
      const u = new URL(v, basis);
      return /^https?:$/.test(u.protocol) && !u.username && !u.password ? u.href : null;
    } catch (_) { return null; }
  }
  function kartePunkte(v) {
    return liste(v).filter(p => p && typeof p === 'object').map(p => Array.isArray(p) ? [p[0], p[1]] :
      [p.x ?? p.lon ?? p.lng, p.y ?? p.lat])
      .filter(p => p.every(x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x))))
      .map(p => p.map(Number));
  }
  const beschriftung = {antenna_height_m: 'Antennenhöhe (m)', antenna_forward_m: 'Antenne vor Achse (m)',
    antenna_right_m: 'Antenne rechts (m)', geraet_breite_m: 'Gerätebreite (m)', traktor_breite_m: 'Traktorbreite (m)',
    traktor_vorn_m: 'Traktor vorn (m)', traktor_hinten_m: 'Traktor hinten (m)', geraet_vorn_m: 'Gerät vorn (m)', geraet_hinten_m: 'Gerät hinten (m)'};
  const koordinaten = v => {
    const q = Array.isArray(v) ? v : v && [v.lat, v.lon];
    return q && kartePunkte([q]).length ? `${zahl(q[0], 7)}° N / ${zahl(q[1], 7)}° O` : 'Position unbekannt';
  };
  function arbeitsZeilen(j) {
    return j ? {Arbeit: text(j.operation), 'Bearbeitet (ha)': zahl(j.area_ha, 3), 'Überlappung (ha)': zahl(j.overlap_ha, 3),
      'Strecke (m)': zahl(j.distance_m), Arbeitszeit: dauer(j.working_time_s)} : {Arbeit: 'Keine laufende Arbeit'};
  }
  function aenderungsZeilen(e) {
    return Object.entries(e.aenderungen || {}).map(([k, v]) => `${beschriftung[k] || k}: ${text(v.vorher, '–')} → ${text(v.nachher, '–')}`);
  }
  function sicherungsZeilen(s = {}) {
    const r = s.pruefung;
    return {Prüfung: !r ? 'Noch nicht geprüft' : r.ok === true ? 'Wiederherstellbarkeit geprüft' : 'Prüfung fehlgeschlagen',
      Prüfzeit: zeit(r && r.zeit), Felder: r ? zahl(r.felder, 0) : '–', Aufträge: r ? zahl(r.auftraege, 0) : '–',
      Fehler: r ? text(r.fehler, 'Keine gemeldet') : '–', 'Letzter Lauf': s.letzter_lauf ? zeit(s.letzter_lauf.zeit) : 'Noch kein Lauf gemeldet'};
  }
  function konfliktZeilen(k) {
    const lokal = k.lokal || {}, eingehend = k.eingehend || {};
    const keys = [...new Set([...Object.keys(lokal), ...Object.keys(eingehend)])].sort();
    return keys.map(key => `${beschriftung[key] || key}: dieses Gerät = ${text(lokal[key], 'Nicht vorhanden')}; eingehend = ${text(eingehend[key], 'Nicht vorhanden')}`);
  }
  const planHinweis = v => `${v.vollstaendig === true ? 'Prüfung vollständig' : 'Prüfung unvollständig'} · ${liste(v.befunde || v.marker).length} Befunde. ${text(v.hinweis, '')}`;
  let instanz = null;
  function installieren(optionen) {
    if (instanz) instanz.entfernen();
    const doc = typeof document !== 'undefined' ? document : null;
    const host = doc && doc.getElementById('arbeitstagInhalt');
    if (!host) throw new Error('Arbeitstag benötigt #arbeitstagInhalt.');
    if (!optionen || typeof optionen.api !== 'function') throw new Error('Arbeitstag benötigt api(method, url, body).');
    const win = doc.defaultView;
    const cache = {}, fehler = {}, bereiche = {}, offen = {};
    let letzterState = optionen.getState ? optionen.getState() : {}, planToken = 0;
    const feldId = () => { const s = optionen.getState ? optionen.getState() : letzterState; return s && s.field ? s.field.id : null; };
    let kontextFeld = feldId();
    function kontextPruefen() {
      const id = feldId();
      if (id !== kontextFeld) { kontextFeld = id; planToken++; plan = null; zeichnePlan(); }
    }
    let letzterVersuch = -Infinity, gerenderterStand = null;
    let ausstehend = false, fokusTimer = null, laeuft = false, beendet = false, letzteZeit = null, plan = null, beschaeftigt = false;
    const n = (tag, inhalt, klasse) => {
      const el = doc.createElement(tag);
      if (inhalt !== undefined) el.textContent = text(inhalt, '');
      if (klasse) el.className = klasse;
      return el;
    };
    const p = (ziel, inhalt, klasse) => ziel.appendChild(n('p', inhalt, klasse));
    function button(ziel, titel, fn) {
      const b = n('button', titel); b.type = 'button';
      b.addEventListener('click', async () => {
        if (beschaeftigt) return;
        beschaeftigt = true; b.disabled = true;
        try { await fn(); }
        catch (e) { status.textContent = `Aktion fehlgeschlagen: ${e.message}`; status.classList.add('at-fehler'); }
        finally { beschaeftigt = false; b.disabled = false; render(); }
      });
      ziel.appendChild(b); return b;
    }
    const details = (ziel, id, titel) => {
      const d = n('details'); d.dataset.atDetail = id; d.open = !!offen[id];
      d.appendChild(n('summary', titel));
      d.addEventListener('toggle', () => { offen[id] = d.open; }); ziel.appendChild(d); return d;
    };
    function felder(ziel, obj, namen) {
      const dl = n('dl');
      Object.entries(namen).forEach(([key, label]) => {
        if (obj && obj[key] !== undefined && obj[key] !== null) {
          dl.appendChild(n('dt', label)); dl.appendChild(n('dd', text(obj[key])));
        }
      });
      ziel.appendChild(dl);
    }
    function zeilen(ziel, daten) {
      const dl = n('dl'); Object.entries(daten).forEach(([k, v]) => { dl.appendChild(n('dt', k)); dl.appendChild(n('dd', v)); }); ziel.appendChild(dl);
    }
    function sichtbar() {
      return !beendet && !doc.hidden && host.isConnected && host.getClientRects().length > 0;
    }
    async function abruf(method, url, body) { return auspacken(await optionen.api(method, url, body)); }
    async function aktion(url, body, erfolg) {
      const antwort = await abruf('POST', url, body);
      status.textContent = text(antwort && (antwort.text || antwort.meldung), erfolg);
      status.classList.remove('at-fehler');
      if (optionen.melden) optionen.melden(status.textContent);
      await laden(true);
      return antwort;
    }
    function hinweis(klasse, meldung) { const e = n('span', meldung, `at-status at-${klasse}`); return e; }
    function statusKlasse(v) {
      return ['ok', 'bereit', 'gut', 'aktuell'].includes(v) || v === true ? 'ok' :
        ['fehler', 'kritisch', 'gesperrt', 'error'].includes(v) || v === false ? 'fehler' : 'warnung';
    }
    const chipZeilen = new Map(), checkZeilen = new Map();
    const setzen = (el, wert) => { if (el.textContent !== wert) el.textContent = wert; };
    function bereitschaft(tag) {
      const checks = liste(tag.checks);
      ['GPS', 'IMU', 'Motor', 'Gespann', 'Spur'].forEach(name => {
        let chip = chipZeilen.get(name);
        if (!chip) { const box = n('div', undefined, 'at-kurz'); box.appendChild(n('strong', name));
          chip = hinweis('warnung', 'Unbekannt'); box.appendChild(chip); schnell.appendChild(box); chipZeilen.set(name, chip); }
        const c = checks.find(x => text(x.titel, '').toLowerCase().includes(name.toLowerCase()));
        setzen(chip, c ? text(c.status) : 'Unbekannt'); chip.className = `at-status at-${statusKlasse(c && c.status)}`;
      });
      checkZeilen.forEach(r => { r.li.hidden = true; });
      checks.forEach(c => {
        let r = checkZeilen.get(c.titel);
        if (!r) { const li = n('li'); li.appendChild(n('strong', c.titel));
          r = {li, status: hinweis('warnung', ''), beschreibung: n('p'), check: c};
          li.append(r.status, r.beschreibung);
          if (optionen.oeffnen) r.button = button(li, 'Dazu öffnen', () => optionen.oeffnen(r.check.ziel));
          checkBox.appendChild(li); checkZeilen.set(c.titel, r); }
        r.check = c; r.li.hidden = false;
        setzen(r.status, text(c.status, 'Unbekannt')); r.status.className = `at-status at-${statusKlasse(c.status)}`;
        setzen(r.beschreibung, text(c.text, '')); if (r.button) r.button.hidden = typeof c.ziel !== 'string';
      });
      keineChecks.hidden = checks.length > 0;
    }
    host.replaceChildren(); host.classList.add('arbeitstag');
    const kopf = n('div', undefined, 'at-kopf'); kopf.appendChild(n('h2', 'Arbeitstag'));
    button(kopf, 'Jetzt prüfen', () => laden(true));
    const gross = n('label', undefined, 'at-kabine'); const schalter = n('input'); schalter.type = 'checkbox';
    try { schalter.checked = win.localStorage.getItem('agripilot.kabine-gross') === '1'; } catch (_) { /* Browser ohne Speicher */ }
    doc.body.classList.toggle('kabine-gross', schalter.checked);
    schalter.addEventListener('change', () => {
      doc.body.classList.toggle('kabine-gross', schalter.checked);
      try { win.localStorage.setItem('agripilot.kabine-gross', schalter.checked ? '1' : '0'); } catch (_) { /* bleibt für diese Sitzung */ }
    });
    gross.append(schalter, doc.createTextNode('Große Tasten')); kopf.appendChild(gross); host.appendChild(kopf);
    const status = n('p', 'Wird geladen …', 'at-hinweis'); status.setAttribute('role', 'status'); host.appendChild(status);
    const uebung = n('p', undefined, 'at-uebung'); uebung.hidden = true; host.appendChild(uebung);
    const aktuell = n('section', undefined, 'aw-aktuell'); host.appendChild(aktuell);
    const schnell = n('div', undefined, 'at-schnell'); host.appendChild(schnell);
    const checkBox = n('ul', undefined, 'at-checks'), keineChecks = n('p', 'Noch keine Bereitschaftsprüfung verfügbar.');
    ['Bereit zur Arbeit', 'Arbeit abschließen & Archiv', 'Verlauf & Datenschutz / Abgleich'].forEach((titel, i) => {
      const g = details(host, `gruppe${i}`, titel); if (i === 0) g.open = true;
      if (i === 0) g.append(checkBox, keineChecks);
      const inhalt = n('div', undefined, 'at-gruppe'); g.appendChild(inhalt); bereiche[i] = inhalt;
    });
    function render() {
      if (beendet) return;
      kontextPruefen();
      const tag = cache.tag || {};
      bereitschaft(tag);
      if (typeof window !== 'undefined' && window.Arbeitsansicht) {
        const A=window.Arbeitsansicht,j=tag.arbeit;
        aktuell.replaceChildren(n('span',j?'Aktuelle Feldarbeit':'Feldarbeit','aw-status'),n('h3',j?j.operation||'Arbeit':'Noch keine Arbeit gestartet'));
        if(j){const v=A.arbeit(j,true);aktuell.appendChild(A.kennzahlen(doc,[['Bearbeitet',v.flaeche],['Arbeitsdauer',v.dauer],['Strecke',v.strecke]]));}
        else p(aktuell,'Feld und Tätigkeit auswählen. Mit Markieren in der Kabine beginnt die Aufzeichnung.');
      }
      // Nur angezeigte Daten vergleichen. Ladezeit und reine Hintergrundwerte sind kein Rendergrund.
      const anzeige = {tag: Object.fromEntries(['gespann', 'offline', 'einstieg', 'arbeit', 'sicherung', 'abgleich', 'uebung'].map(k => [k, tag[k]])),
        buch: cache.buch, archiv: cache.archiv, konflikte: cache.konflikte, plan};
      const stand = JSON.stringify(anzeige, (k, v) => v && typeof v === 'object' && !Array.isArray(v)
        ? Object.fromEntries(Object.keys(v).sort().map(key => [key, v[key]])) : v);
      if (stand === gerenderterStand) { ausstehend = false; return; }
      if (beschaeftigt || host.contains(doc.activeElement)) { ausstehend = true; return; }
      ausstehend = false;
      const scroller = host.closest('.tab-panel') || doc.scrollingElement;
      const scrollTop = scroller && scroller.scrollTop;
      try {
      host.querySelectorAll('details[data-at-detail]').forEach(d => { offen[d.dataset.atDetail] = d.open; });
      const a = cache.tag || {};
      uebung.hidden = !a.uebung;
      uebung.textContent = 'Übungsmodus – keine echte Traktorfahrt. Daten dieser Umgebung getrennt behandeln.';
      Object.values(bereiche).forEach(x => x.replaceChildren());
      const bereit = bereiche[0], arbeit = bereiche[1], verlauf = bereiche[2];
      const gespann = details(bereit, 'gespann', 'Gespann, Maße & Kalibrierung');
      felder(gespann, a.gespann, {traktor: 'Traktor', geraet: 'Gerät', name: 'Gespann', maschine: 'Maschine',
        bauform: 'Bauform', geraet_art: 'Geräteart', anbau: 'Anbau', breite_m: 'Arbeitsbreite (m)',
        versatz_m: 'Versatz (m)', vollstaendig: 'Maße vollständig', kalibrierung: 'Kalibrierung'});
      felder(gespann, a.gespann && a.gespann.antenne, beschriftung);
      felder(gespann, a.gespann && a.gespann.masse, beschriftung);
      p(gespann, 'Die maßstäbliche Kontur beruht auf den eingetragenen echten Maschinen- und Gerätemaßen. Sie ist eine Orientierung, kein Kollisionsnachweis. Fehlende oder falsche Maße machen die Darstellung unzuverlässig.');
      p(gespann, 'Vor dem Start Maße, Front-/Heckanbau und passende Kalibrierung am tatsächlichen Gespann prüfen.');
      if (optionen.oeffnen) button(gespann, 'Gespann prüfen', () => optionen.oeffnen('machine'));
      const offline = details(bereit, 'offline', 'Offline-Bereitschaft & Feldeinstieg');
      felder(offline, a.offline, {bereit: 'Offline bereit', feld: 'Feld', spur: 'Spur', gespann: 'Gespann', karten: 'Karten', internet: 'Internet', gps: 'GPS', rtk: 'RTK', text: 'Hinweis'});
      const einstieg = a.einstieg || {};
      felder(offline, einstieg, {spur: 'Erste Spur', richtung: 'Fahrtrichtung (°)', text: 'Hinweis'});
      zeilen(offline, {Spuranfang: koordinaten(einstieg.start), Einfahrt: !einstieg.einfahrt || einstieg.einfahrt.geloescht ? 'Keine Einfahrt gespeichert' : koordinaten(einstieg.einfahrt),
        'Einfahrt gespeichert': einstieg.einfahrt && !einstieg.einfahrt.geloescht ? zeit(einstieg.einfahrt.zeit) : '–'});
      karte(offline, einstieg.grenze, [{name: 'Start', punkte: [einstieg.start_xy]}, {name: 'Einfahrt', punkte: einstieg.einfahrt && !einstieg.einfahrt.geloescht ? [einstieg.einfahrt_xy] : []}], 'Feldeinstieg in lokalen XY-Koordinaten');
      const ein = n('div', undefined, 'at-aktionen'); offline.appendChild(ein);
      button(ein, 'Hier Einfahrt speichern', () => aktion('/api/arbeitstag/einfahrt', {aktion: 'setzen'}, 'Einfahrt gespeichert.'));
      button(ein, 'Einfahrt löschen', () => aktion('/api/arbeitstag/einfahrt', {aktion: 'loeschen'}, 'Einfahrt gelöscht.'));
      button(offline, 'Feldplan prüfen', async () => {
        kontextPruefen(); const feld = feldId(), token = ++planToken;
        if (!feld) throw new Error('Erst ein Feld laden.');
        const antwort = await abruf('GET', '/api/arbeitstag/planpruefung');
        kontextPruefen();
        if (beendet || token !== planToken || feld !== feldId() || antwort.field_id !== feld)
          throw new Error('Feldkontext geändert – Planprüfung für das aktuelle Feld erneut starten.');
        plan = antwort; zeichnePlan();
      });
      const planbox = n('div'); planbox.dataset.plan = '1'; offline.appendChild(planbox);
      zeilen(arbeit, arbeitsZeilen(a.arbeit));
      p(arbeit, 'In der Kabine beendet die Markierungstaste die laufende Arbeit. Sie bietet keine gesonderte Unterbrechungsfunktion. Noch offene gespeicherte Aufträge lassen sich unten fortsetzen.');
      if (optionen.oeffnen) button(arbeit, 'Zur Arbeitsansicht', () => optionen.oeffnen('kabine'));
      const archiv = details(arbeit, 'archiv', 'Saisonarchiv, Feldbericht & Wiederaufnahme');
      const ar = cache.archiv || {};
      const jahrl = n('label', 'Saison'); const jahr = n('select'); jahr.setAttribute('aria-label', 'Saison für den Feldbericht');
      const saisonen = liste(ar.saisons); if (!saisonen.length) saisonen.push(new Date().getFullYear());
      saisonen.forEach(s => { const v = typeof s === 'object' ? s.jahr : s; const o = n('option', v); o.value = text(v, ''); jahr.appendChild(o); });
      if (offen.jahr && Array.from(jahr.options).some(x => x.value === offen.jahr)) jahr.value = offen.jahr;
      jahrl.appendChild(jahr); archiv.appendChild(jahrl);
      const csv = n('a', 'Feldbericht als CSV herunterladen'); csv.className = 'at-link'; csv.download = '';
      const csvUrl = () => { offen.jahr = jahr.value; csv.href = `/api/archiv/bericht.csv?jahr=${encodeURIComponent(jahr.value)}`; };
      jahr.addEventListener('change', csvUrl); csvUrl(); archiv.appendChild(csv);
      p(archiv, 'CSV lässt sich in einer Tabellenkalkulation öffnen und drucken.');
      const feldLabel = n('label', 'Feld'); const feld = n('select'); feld.setAttribute('aria-label', 'Feld für gespeicherte Aufträge');
      const alle = n('option', 'Alle Felder'); alle.value = ''; feld.appendChild(alle);
      liste(ar.felder).forEach(f => { const o = n('option', f.name || f.titel || f.id); o.value = text(f.id, ''); feld.appendChild(o); });
      feld.value = offen.feld || ''; feldLabel.appendChild(feld); archiv.appendChild(feldLabel);
      const jobbox = n('div'); archiv.appendChild(jobbox);
      function jobsZeigen() {
        offen.feld = feld.value; jobbox.replaceChildren();
        const jobs = liste(ar.jobs).filter(j => !feld.value || String(j.field_id ?? j.feld_id) === feld.value);
        if (!jobs.length) p(jobbox, 'Keine gespeicherten Aufträge für diese Auswahl.');
        jobs.forEach(j => { const zeile = n('div', undefined, 'at-eintrag');
          p(zeile, `${text(j.name || j.feld || j.id)} · ${zeit(j.started_at)} · ${zahl(j.area_ha)} ha`);
          if (j.id !== undefined && j.fortsetzbar === true) button(zeile, 'Diesen Auftrag fortsetzen', () => aktion('/api/arbeitstag/fortsetzen', {job_id: j.id}, 'Auftrag wieder aufgenommen.'));
          else p(zeile, j.ended_at ? 'Abgeschlossen – keine Wiederaufnahme.' : 'Auf diesem Gerät nicht fortsetzbar.');
          jobbox.appendChild(zeile);
        });
      }
      feld.addEventListener('change', jobsZeigen); jobsZeigen();
      const buch = cache.buch || {};
      const stopps = details(verlauf, 'stopps', `Lenkunterbrechungen (${liste(buch.unterbrechungen).length})`);
      if (!liste(buch.unterbrechungen).length) p(stopps, 'Keine Unterbrechungen aufgezeichnet.');
      liste(buch.unterbrechungen).forEach(e => { const z = n('article', undefined, 'at-eintrag');
        p(z, `${zeit(e.zeitpunkt)} · ${dauer(e.dauer_s ?? e.dauer)} · ${text(e.klasse)}`);
        p(z, e.text || e.grund || e.titel); if (e.snapshot) p(z, text(e.snapshot)); if (e.hilfe) p(z, `Abhilfe: ${text(e.hilfe)}`); stopps.appendChild(z);
      });
      const werte = details(verlauf, 'werte', 'Einstellungsverlauf & bewährten Stand wiederherstellen');
      if (!liste(buch.einstellungen).length) p(werte, 'Noch keine Einstellungsänderungen aufgezeichnet.');
      liste(buch.einstellungen).forEach(e => { const z = n('article', undefined, 'at-eintrag');
        p(z, `${zeit(e.zeitpunkt)} · ${text(e.quelle, 'Quelle unbekannt')}`);
        if (e.kontext) p(z, `Kontext: ${text(e.kontext)}`);
        aenderungsZeilen(e).forEach(v => p(z, v));
        if (e.id !== undefined) button(z, 'Diesen Stand wiederherstellen', () => aktion('/api/betriebsbuch/wiederherstellen', {id: e.id}, 'Einstellung wiederhergestellt.'));
        werte.appendChild(z);
      });
      p(werte, 'Wiederherstellen nur im Stillstand. Die Anwendung prüft die Freigabe.');
      const sync = details(verlauf, 'sync', 'Abgleich, Sicherung & Datenschutz');
      felder(sync, a.abgleich, {status: 'Abgleich', ausstehend: 'Ausstehend', letzte_zeit: 'Letzter Abgleich', grenzverlauf: 'Grenzverlauf', berichte: 'Berichte', text: 'Hinweis'});
      zeilen(sync, sicherungsZeilen(a.sicherung || {}));
      liste((a.sicherung || {}).orte).forEach(o => p(sync, `${text(o.ordner)} · ${o.vorhanden ? 'Ordner vorhanden' : 'Ordner fehlt'} · Letzte Sicherung: ${o.letzte ? zeit(o.letzte.zeit) : 'Keine'}`));
      p(sync, 'Berichte und Verläufe können Positions- und Maschinendaten enthalten. Nur bewusst weitergeben.');
      button(sync, 'Sicherung auf Wiederherstellbarkeit prüfen', async () => {
        const r = await aktion('/api/backup/pruefen', {}, 'Sicherungsprüfung abgeschlossen.');
        cache.tag = {...(cache.tag || {}), sicherung: {...((cache.tag || {}).sicherung || {}), pruefung: r.pruefung || r}};
        status.textContent = sicherungsZeilen(cache.tag.sicherung).Prüfung;
      });
      const konflikte = liste((cache.konflikte || {}).konflikte).filter(k => k.status === 'offen');
      if (!konflikte.length) p(sync, 'Keine offenen Abgleichkonflikte.');
      konflikte.forEach(k => { const z = n('article', undefined, 'at-eintrag'); p(z, `${text(k.tabelle)} · Datensatz ${text(k.datensatz_id)} · ${zeit(k.zeit)}`);
        konfliktZeilen(k).forEach(v => p(z, v));
        ['lokal', 'eingehend'].forEach(wahl => { if (k.id !== undefined) button(z, wahl === 'lokal' ? 'Stand dieses Geräts behalten' : 'Stand des anderen Geräts übernehmen',
          () => aktion('/api/sync/konflikte/loesen', {id: k.id, auswahl: wahl}, 'Konflikt gelöst.')); }); sync.appendChild(z);
      });
      button(sync, 'Getrennten Übungsmodus öffnen', async () => {
        // Fenster im direkten Klick öffnen: spätere Netzwerkantworten sind keine Nutzergeste mehr.
        const fenster = win.open('about:blank', '_blank');
        if (!fenster) throw new Error('Fenster wurde blockiert. Pop-up für diese Anwendung erlauben und erneut öffnen.');
        try {
          fenster.opener = null;
          const r = await abruf('POST', '/api/uebung/oeffnen', {});
          const url = uebungsUrl(r.url, win.location.href);
          if (!url) throw new Error('Keine gültige Adresse für den Übungsmodus erhalten.');
          fenster.location.replace(url);
        } catch (e) { fenster.close(); throw e; }
      });
      const fahrt = details(bereit, 'probefahrt', 'Echte Probefahrt & Rohdaten');
      p(fahrt, 'Auf freier Fläche bei sicherer Geradeausfahrt denselben Abschnitt mit gleicher Geschwindigkeit vor und nach einer Änderung messen. Erst niedrig beginnen, dann weitere Geschwindigkeiten prüfen. Hangfahrt sowie Anfahren und Anhalten getrennt beurteilen.');
      p(fahrt, 'Simulatorwerte belegen keine Lenkgenauigkeit am Traktor. Rohdaten vor der Fahrt starten und nach der Fahrt stoppen.');
      if (optionen.oeffnen) { button(fahrt, 'Vergleichsfahrt öffnen', () => optionen.oeffnen('lenkregler')); button(fahrt, 'Rohdatenaufzeichnung öffnen', () => optionen.oeffnen('system')); }
      zeichnePlan();
      gerenderterStand = stand;
      } finally {
        if (scroller) scroller.scrollTop = scrollTop;
      }
    }
    function zeichnePlan() {
      const box = host.querySelector('[data-plan]'); if (!box) return;
      box.replaceChildren(); if (!plan) return;
      const befunde = liste(plan.befunde || plan.marker);
      p(box, planHinweis(plan));
      if (plan.text) p(box, plan.text);
      karte(box, plan.grenze || (cache.tag && cache.tag.einstieg && cache.tag.einstieg.grenze), befunde.map((b, i) => ({name: String(i + 1), punkte: b.punkte || [b.punkt || b.position]})), 'Feldplan und nummerierte Befunde in lokalen XY-Koordinaten');
      const ol = n('ol'); befunde.forEach(b => { const li = n('li', `${text(b.titel || b.art || b.klasse, 'Hinweis')}: ${text(b.text || b.hilfe, '')}`); ol.appendChild(li); }); box.appendChild(ol);
    }
    function karte(box, polygon, eintraege, titel) {
      const grenze = kartePunkte(polygon);
      const marker = eintraege.map(m => ({name: m.name, pts: kartePunkte(m.punkte)}));
      const alle = grenze.concat(...marker.map(m => m.pts));
      if (alle.length) {
        const c = n('canvas'); c.width = 680; c.height = 300;
        c.setAttribute('role', 'img'); c.setAttribute('aria-label', titel);
        box.appendChild(c); const ctx = c.getContext('2d');
        if (ctx) {
          const xs = alle.map(x => x[0]), ys = alle.map(x => x[1]);
          const minX = Math.min(...xs), minY = Math.min(...ys), dx = Math.max(...xs) - minX, dy = Math.max(...ys) - minY;
          const faktor = Math.min(620 / (dx || 1), 240 / (dy || 1));
          const xy = q => [30 + (q[0] - minX) * faktor, 270 - (q[1] - minY) * faktor];
          const stil = win.getComputedStyle(host); ctx.strokeStyle = stil.getPropertyValue('--text').trim() || '#14201a'; ctx.lineWidth = 2;
          if (grenze.length) { ctx.beginPath(); grenze.forEach((q, i) => { const [x, y] = xy(q); if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.closePath(); ctx.stroke(); }
          ctx.fillStyle = ctx.strokeStyle; ctx.font = 'bold 18px sans-serif';
          marker.forEach((m, i) => m.pts.forEach(q => { const [x, y] = xy(q); ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.fill(); ctx.fillText(m.name || String(i + 1), x + 9, y - 8); }));
        }
      } else p(box, 'Keine lokalen Kartenkoordinaten verfügbar.');
    }
    async function laden(erzwungen = false) {
      const jetzt = Date.now();
      if (laeuft || beendet || (!erzwungen && jetzt - letzterVersuch < 10000)) return;
      // Schon der Versuch zählt: ein fehlgeschlagenes GET darf keine Tick-Schleife auslösen.
      letzterVersuch = jetzt;
      laeuft = true;
      const endpunkte = {tag: '/api/arbeitstag', buch: '/api/betriebsbuch', archiv: '/api/archiv', konflikte: '/api/sync/konflikte'};
      try {
        await Promise.all(Object.entries(endpunkte).map(async ([key, url]) => {
          try { const neu = await abruf('GET', url); if (!neu || typeof neu !== 'object') throw new Error('Antwort enthält keine Daten.');
            if (!beendet) {
              if (key === 'tag' && cache.tag && JSON.stringify(cache.tag.einstieg) !== JSON.stringify(neu.einstieg)) plan = null;
              cache[key] = neu; delete fehler[key];
            }
          } catch (e) { if (!beendet) fehler[key] = `${url}: ${e.message}`; }
        }));
        if (beendet) return;
        const probleme = Object.values(fehler);
        if (!probleme.length) letzteZeit = new Date();
        status.textContent = probleme.length ? `Nicht aktuell – letzte Daten bleiben sichtbar. ${probleme.join(' · ')}` : `Aktualisiert: ${zeit(letzteZeit)}.`;
        status.classList.toggle('at-fehler', !!probleme.length); render();
      } finally { laeuft = false; }
    }
    const beiFokusEnde = () => {
      if (fokusTimer !== null) win.clearTimeout(fokusTimer);
      fokusTimer = win.setTimeout(() => { fokusTimer = null; if (ausstehend && !host.contains(doc.activeElement)) render(); }, 0);
    };
    host.addEventListener('focusout', beiFokusEnde);
    const timer = win.setInterval(() => { if (sichtbar()) laden(); }, 10000);
    const beiSicht = () => { if (sichtbar()) laden(); };
    doc.addEventListener('visibilitychange', beiSicht);
    const observer = new win.MutationObserver(beiSicht);
    for (let el = host; el; el = el.parentElement) observer.observe(el, {attributes: true, attributeFilter: ['class', 'hidden', 'style']});
    instanz = {
      aktualisieren(neu) { letzterState = neu || {}; kontextPruefen(); if (sichtbar() && !laeuft && !cache.tag) laden(); },
      entfernen() { beendet = true; win.clearInterval(timer); if (fokusTimer !== null) win.clearTimeout(fokusTimer); host.removeEventListener('focusout', beiFokusEnde); observer.disconnect(); doc.removeEventListener('visibilitychange', beiSicht); },
      neuLaden: () => laden(true)
    };
    if (sichtbar()) laden();
    return instanz;
  }
  function aktualisieren(state) { if (instanz) instanz.aktualisieren(state); }
  return { installieren, aktualisieren, text, zahl, zeit, dauer, auspacken, uebungsUrl, kartePunkte, koordinaten, arbeitsZeilen, aenderungsZeilen, sicherungsZeilen, konfliktZeilen, planHinweis };
});
