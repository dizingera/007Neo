/* Kartenfarben gehören zur Ansicht dieses Browsers, getrennt für Tag und Nacht. */
(function (ziel) {
  'use strict';
  const SCHLUESSEL = 'agripilot.kartenfarben.v1';
  const ARTEN = ['grenze', 'fahrgasse', 'flaeche', 'doppelt'];
  const STANDARD = {
    nacht: { grenze: '#ffb020', fahrgasse: '#ff5c5c', flaeche: '#48cc77', doppelt: '#ffae30' },
    tag: { grenze: '#b57c0e', fahrgasse: '#d8362f', flaeche: '#178c46', doppelt: '#c8680c' },
  };
  const deckkraft = { nacht: { flaeche: 0.4, doppelt: 0.72 }, tag: { flaeche: 0.38, doppelt: 0.64 } };
  function ansichtsfarbe(art, farbe, thema) {
    if (!['flaeche', 'doppelt'].includes(art)) return farbe;
    return `rgba(${parseInt(farbe.slice(1, 3), 16)},${parseInt(farbe.slice(3, 5), 16)},${parseInt(farbe.slice(5, 7), 16)},${deckkraft[thema][art]})`;
  }
  const farbeGueltig = (wert) => typeof wert === 'string' && /^#[0-9a-f]{6}$/i.test(wert);
  class Kartenfarben {
    constructor({ el, speicher, stil, geaendert }) {
      this.el = el;
      this.speicher = speicher;
      this.stil = stil;
      this.geaendert = geaendert;
      this.thema = 'nacht';
      this.farben = { nacht: {}, tag: {} };
      this.entwuerfe = {};
      let lesefehler = false;
      try {
        const roh = speicher.getItem(SCHLUESSEL);
        if (roh) {
          const daten = JSON.parse(roh);
          for (const thema of ['tag', 'nacht'])
            for (const art of ARTEN) {
              if (farbeGueltig(daten?.[thema]?.[art]))
                this.farben[thema][art] = daten[thema][art].toLowerCase();
            }
        }
      } catch {
        lesefehler = true;
      }
      el('kartenfarbenThema').onchange = () => this.themaWaehlen(el('kartenfarbenThema').value);
      for (const art of ARTEN) {
        el('kartenfarbe_' + art).oninput = () => this.entwurfMerken();
      }
      el('kartenfarbenSpeichern').onclick = () => this.speichern();
      el('kartenfarbenStandard').onclick = () => this.zuruecksetzen();
      for (const id of ['kartenfarbenPalette', 'kartenfarbenMehrPalette'])
        el(id)
          ?.querySelectorAll('button[data-farbe]')
          .forEach((knopf) => {
            knopf.onclick = () => {
              el('kartenfarbe_' + knopf.dataset.art).value = knopf.dataset.farbe;
              this.entwurfMerken();
            };
          });
      this.themaSetzen('nacht');
      if (lesefehler)
        this.melden(
          'Gespeicherte Kartenfarben konnten nicht gelesen werden. Standardfarben werden verwendet.',
        );
    }
    melden(text) {
      this.el('kartenfarbenInfo').textContent = text;
    }
    entwurfMerken() {
      this.entwuerfe[this.thema] = Object.fromEntries(
        ARTEN.map((art) => [art, this.el('kartenfarbe_' + art).value]),
      );
      this.vorschau();
      this.melden('Farbauswahl geändert – bitte übernehmen.');
    }
    themaWaehlen(thema) {
      if (!['tag', 'nacht'].includes(thema) || thema === this.thema) return;
      // Der Ansichtswechsel verwendet denselben öffentlichen Schalter wie die Karte.
      this.el('tagNacht').click();
    }
    themaSetzen(thema) {
      this.thema = thema;
      this.el('kartenfarbenThema').value = thema;
      const werte = this.entwuerfe[thema] || { ...STANDARD[thema], ...this.farben[thema] };
      for (const art of ARTEN) this.el('kartenfarbe_' + art).value = werte[art];
      this.vorschau();
      this.anwenden();
      if (this.entwuerfe[thema]) this.melden('Farbauswahl geändert – bitte übernehmen.');
      else this.melden('');
    }
    vorschau() {
      const box = this.el('kartenfarbenVorschau');
      if (!box) return;
      for (const art of ARTEN) {
        const farbe = this.el('kartenfarbe_' + art).value;
        if (farbeGueltig(farbe)) box.style.setProperty('--v-' + art, ansichtsfarbe(art, farbe, this.thema));
      }
    }
    anwenden() {
      for (const art of ARTEN) {
        const farbe = this.farben[this.thema][art];
        if (farbe) this.stil.setProperty('--f-' + art, ansichtsfarbe(art, farbe, this.thema));
        else this.stil.removeProperty('--f-' + art);
      }
    }
    ablegen() {
      try {
        this.speicher.setItem(SCHLUESSEL, JSON.stringify(this.farben));
        return true;
      } catch {
        return false;
      }
    }
    speichern() {
      const werte = Object.fromEntries(ARTEN.map((art) => [art, this.el('kartenfarbe_' + art).value]));
      if (!Object.values(werte).every(farbeGueltig)) {
        this.melden('Bitte gültige Farben auswählen.');
        return;
      }
      this.farben[this.thema] = werte;
      delete this.entwuerfe[this.thema];
      const gespeichert = this.ablegen();
      this.anwenden();
      this.geaendert();
      this.melden(
        gespeichert
          ? 'Kartenfarben in diesem Browser gespeichert.'
          : 'Für diese Sitzung übernommen; der Browser konnte die Farben nicht speichern.',
      );
    }
    zuruecksetzen() {
      this.farben[this.thema] = {};
      delete this.entwuerfe[this.thema];
      const gespeichert = this.ablegen();
      this.themaSetzen(this.thema);
      this.geaendert();
      this.melden(
        gespeichert
          ? 'Standardfarben für diese Ansicht wiederhergestellt.'
          : 'Standardfarben für diese Sitzung; der Browser konnte die Änderung nicht speichern.',
      );
    }
  }
  const modul = { Kartenfarben };
  if (typeof module !== 'undefined' && module.exports) module.exports = modul;
  else ziel.KartenfarbenModul = modul;
})(typeof window !== 'undefined' ? window : globalThis);
