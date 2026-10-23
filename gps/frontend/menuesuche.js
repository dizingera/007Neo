/* Menüsuche: findet Überschriften, Knöpfe und Einstellungen auf allen Seiten des Menüs.
   Wer "Teilbreiten" sucht, soll nicht wissen müssen, dass sie unter Traktor & Gerät stehen. */
(function (ziel) {
  'use strict';

  // Wörter, die auf einer Seite nicht wörtlich stehen, unter denen man sie aber sucht.
  const STICHWORTE = {
    fields: 'feld grenze kontur shapefile flaechenantrag luftbild hektar randspur',
    lines: 'spur ab linie kurve kontur fahrgasse versatz hindernis saisonspur',
    jobs: 'arbeit auftrag aufzeichnung export csv gpx myterra loeschen flaeche',
    maschinen: 'fernansicht flotte andere traktoren maschinen karte wo',
    saatplan: 'saat saen aussaat drille fahrgasse saespur',
    headland: 'vorgewende wende wendung u turn kehre',
    machine: 'traktor geraet arbeitsbreite teilbreiten sektion antenne radstand spritze',
    lenkung: 'lenkung lenkmotor motortest phidget mitte kalibrieren gyro neigung assistent',
    settings: 'einstellungen werte ntrip rtk gnss kartenfarben farbe',
    daten: 'abgleich sync master arbeiter datenpaket zip sicherung backup geraete',
    system: 'system version update aktualisieren neustart diagnose rohdaten zustand',
    setup: 'einbau inbetriebnahme checkliste haken',
  };

  // Was als Ziel taugt: Überschriften, Abschnitte, Beschriftungen und Knöpfe.
  const ZIELE = 'h2, h3, h4, summary, legend, label, button, a.button, .title, .k';
  const GEWICHT = { H2: 4, H3: 4, H4: 3, SUMMARY: 4, LEGEND: 3, LABEL: 2, BUTTON: 2, A: 1 };
  const NICHT_MITLESEN = new Set(['SELECT', 'OPTION', 'INPUT', 'TEXTAREA', 'SCRIPT', 'STYLE']);

  function normal(text) {
    return String(text || '').toLowerCase()
      .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
      .replace(/[^a-z0-9]+/g, ' ').trim();
  }

  // Nur der eigene Text: eine Beschriftung mit Auswahlfeld soll nicht alle Optionen mitbringen.
  function eigenerText(knoten) {
    let text = '';
    knoten.childNodes.forEach((kind) => {
      if (kind.nodeType === 3) text += kind.textContent;
      else if (kind.nodeType === 1 && !NICHT_MITLESEN.has(kind.tagName)) text += ' ' + eigenerText(kind);
    });
    return text.replace(/\s+/g, ' ').trim();
  }

  // Der Hilfetext unter einer Einstellung zählt mit: "NTRIP" steht dort, nicht im Namen.
  function hilfeVon(element) {
    const feld = element.closest('.setting-field');
    if (feld) return Array.from(feld.querySelectorAll('.help'), (h) => h.textContent).join(' ');
    if (element.tagName !== 'SUMMARY') return '';
    const note = element.parentElement.querySelector('.group-note');
    return note ? note.textContent : '';
  }

  function abschnittVon(element, panel) {
    const kasten = element.closest('details');
    if (kasten && panel.contains(kasten)) {
      const kopf = kasten.querySelector('summary');
      if (kopf && kopf !== element) return eigenerText(kopf);
    }
    const rahmen = element.closest('fieldset');
    const legende = rahmen && panel.contains(rahmen) && rahmen.querySelector('legend');
    if (legende && legende !== element) return eigenerText(legende);
    const block = element.closest('.abschnitt');
    const titel = block && block.querySelector('h3, h4');
    return titel && titel !== element ? eigenerText(titel) : '';
  }

  /* Liest die Seiten so, wie sie gerade im Menü stehen - auch was erst nachgeladen wurde. */
  function sammeln(panels, reiter) {
    const eintraege = [];
    const namen = {};
    reiter.forEach((tab) => {
      const name = tab.querySelector('.tab-name');
      const info = tab.querySelector('small');
      namen[tab.dataset.tab] = name ? name.textContent : tab.textContent;
      eintraege.push({
        text: namen[tab.dataset.tab],
        extra: `${info ? info.textContent : ''} ${STICHWORTE[tab.dataset.tab] || ''}`,
        panel: tab.dataset.tab, seite: namen[tab.dataset.tab], abschnitt: info ? info.textContent : '',
        element: null, gewicht: 5,
      });
    });
    panels.forEach((panel) => {
      const gesehen = new Set();
      panel.querySelectorAll(ZIELE).forEach((element) => {
        // Erklärtexte und die Knöpfe in Listenzeilen (jede Arbeit hat ihr "Löschen") nicht.
        if (element.closest('details.erklaerung, .item')) return;
        const versteckt = element.closest('[hidden]');
        if (versteckt && versteckt !== panel) return;
        const text = eigenerText(element);
        if (text.length < 3 || gesehen.has(text)) return;
        gesehen.add(text);
        eintraege.push({
          text, extra: hilfeVon(element), panel: panel.dataset.panel, seite: namen[panel.dataset.panel] || '',
          abschnitt: abschnittVon(element, panel), element, gewicht: GEWICHT[element.tagName] || 1,
        });
      });
    });
    return eintraege;
  }

  /* Jedes gesuchte Wort muss vorkommen; Treffer am Wortanfang und Überschriften zuerst. */
  function suchen(eintraege, frage, hoechstens = 12) {
    const woerter = normal(frage).split(' ').filter(Boolean);
    if (!woerter.length) return [];
    const treffer = [];
    eintraege.forEach((eintrag) => {
      const haupt = ' ' + normal(eintrag.text);
      const alles = `${haupt} ${normal(eintrag.abschnitt)} ${normal(eintrag.seite)} ${normal(eintrag.extra)}`;
      let punkte = eintrag.gewicht;
      for (const wort of woerter) {
        if (haupt.includes(' ' + wort)) punkte += 6;
        else if (haupt.includes(wort)) punkte += 3;
        else if (alles.includes(wort)) punkte += 1;
        else return;
      }
      treffer.push({ eintrag, punkte });
    });
    treffer.sort((a, b) => b.punkte - a.punkte || a.eintrag.text.length - b.eintrag.text.length);
    return treffer.slice(0, hoechstens).map((t) => t.eintrag);
  }

  const modul = { normal, eigenerText, sammeln, suchen, STICHWORTE };
  if (typeof module !== 'undefined' && module.exports) module.exports = modul;
  else ziel.MenueSucheModul = modul;
})(typeof window !== 'undefined' ? window : globalThis);
