/* Einrichtung am gewählten Traktor. Entwürfe behalten ihren ursprünglichen Kontext. */
class LenkWerkstatt {
  constructor({ el, api, holen = (url) => fetch(url) }) {
    this.el = el;
    this.api = api;
    this.holen = holen;
    this.daten = null;
    this.live = null;
    this.dirty = false;
    this.busy = false;
    this.folge = 0;
    this.felder = [
      'wheelbase_m',
      'antenna_forward_m',
      'antenna_right_m',
      'antenna_height_m',
      'max_steer_left_deg',
      'max_steer_right_deg',
    ];
    el('werkstattForm').oninput = () => {
      this.dirty = true;
      this.kontrollen();
    };
    el('werkstattForm').onsubmit = async (event) => {
      event.preventDefault();
      await this.senden('geometrie');
    };
    el('werkstattLaden').onclick = () => this.laden(true);
    el('werkstattSichern').onclick = () => this.senden('sichern');
    el('werkstattZurueck').onclick = () => this.senden('wiederherstellen');
    el('werkstattProtokoll').onclick = () => this.senden('protokoll');
  }
  melden(text) {
    this.el('werkstattInfo').textContent = text;
  }
  async laden(erzwingen = false) {
    if (this.busy) return;
    const folge = ++this.folge;
    try {
      const r = await this.holen('/api/lenkung/kalibrierung');
      if (!r.ok) throw new Error('Einrichtung nicht erreichbar');
      const antwort = await r.json();
      if (folge !== this.folge) return;
      this.anzeigen(antwort.data, erzwingen);
      if (erzwingen) this.melden('Aktueller Traktor geladen.');
      this.kontrollen();
    } catch (error) {
      this.melden('Werkstatt konnte nicht geladen werden. Eingaben bleiben erhalten.');
    }
  }
  anzeigen(d, erzwingen = false) {
    this.live = d;
    if (!this.dirty || erzwingen) {
      this.daten = d;
      this.dirty = false;
      this.felder.forEach((k) => {
        this.el('wk_' + k).value = String(d.geometrie[k]);
      });
      this.el('werkstattVermessen').checked = false;
      if (this.vermessenStand !== d.vermessen) {
        this.vermessenStand = d.vermessen;
        this.el('werkstattMasseBox').open = !d.vermessen;
      }
    }
    this.el('werkstattTraktor').textContent = d.traktor.name;
    this.el('werkstattGeraet').textContent = (d.uebung ? 'Übungsmodus · ' : 'Traktor-Tablet · ') + d.geraet;
    const fertig = d.schritte.filter((s) => s.fertig).length;
    this.el('werkstattFortschritt').textContent =
      `${fertig} von ${d.schritte.length} Einrichtungsschritten · Geometrie ${d.vermessen ? 'bestätigt' : 'noch prüfen'}`;
    const p = d.protokoll;
    this.el('werkstattBefund').textContent = !p
      ? 'Noch kein Prüfstand'
      : !p.aktuell
        ? 'Seit der Prüfung geändert'
        : p.uebung
          ? 'Übung dokumentiert'
          : p.vollstaendig
            ? 'Einrichtung dokumentiert'
            : 'Einrichtung noch offen';
    this.el('werkstattBefund').dataset.art = p && p.aktuell && p.vollstaendig && !p.uebung ? 'gut' : 'offen';
    this.el('werkstattSicherung').textContent = d.sicherung
      ? 'Letzte Traktorsicherung: ' + new Date(d.sicherung.zeit * 1000).toLocaleString('de-DE')
      : 'Vor der ersten Änderung wird automatisch eine Traktorsicherung angelegt.';
  }
  kontrollen() {
    const d = this.live,
      alt = this.daten;
    const konflikt = !!(d && alt && d.kontext !== alt.kontext);
    const gruende = d ? d.sperren : ['Werkstatt wird geladen'];
    const gesperrt = this.busy || !d || konflikt || gruende.length > 0;
    this.el('werkstattFelder').disabled = this.busy || !d || konflikt;
    this.el('werkstattSpeichern').disabled = gesperrt;
    this.el('werkstattSichern').disabled = gesperrt || this.dirty;
    this.el('werkstattZurueck').disabled = gesperrt || this.dirty || !d.sicherung;
    this.el('werkstattProtokoll').disabled = gesperrt || this.dirty;
    this.el('werkstattLaden').disabled = this.busy;
    this.el('werkstattSperre').textContent = konflikt
      ? 'Traktor oder Einstellungen geändert. Entwurf bleibt erhalten; vor dem Speichern aktuellen Traktor neu laden.'
      : gruende.length
        ? gruende.join(' · ')
        : this.dirty
          ? 'Ungespeicherte Maße – übernehmen oder aktuellen Traktor neu laden.'
          : 'Bereit für die Einrichtung im Stand. Hier wird kein Motor bewegt.';
  }
  async senden(aktion) {
    if (
      this.busy ||
      !this.daten ||
      !this.live ||
      this.daten.kontext !== this.live.kontext ||
      this.live.sperren.length
    )
      return;
    if (aktion === 'geometrie' && !this.el('werkstattVermessen').checked) {
      this.melden('Bitte die am Traktor gemessenen Werte bestätigen.');
      return;
    }
    const d = this.daten;
    const payload = { aktion, traktor_id: d.traktor.id, kontext: d.kontext };
    if (aktion === 'geometrie') {
      payload.vermessen = true;
      payload.werte = {};
      for (const k of this.felder) payload.werte[k] = Number(this.el('wk_' + k).value);
    }
    if (aktion === 'wiederherstellen') payload.sicherung_id = d.sicherung?.id;
    this.busy = true;
    this.kontrollen();
    this.melden('Wird gespeichert …');
    try {
      const antwort = await this.api('POST', '/api/lenkung/kalibrierung', payload);
      if (!antwort) {
        this.melden('Nicht gespeichert. Eingaben bleiben erhalten; Rückmeldung prüfen.');
        return;
      }
      this.dirty = false;
      this.busy = false;
      this.folge++;
      this.anzeigen(antwort.data, true);
      this.melden(
        {
          geometrie: 'Geometrie gespeichert; vorherige Traktorwerte gesichert.',
          sichern: 'Aktuelle Traktorwerte gesichert.',
          wiederherstellen: 'Traktorwerte wiederhergestellt. Motorreglerwerte bleiben unverändert.',
          protokoll: 'Prüfstand dokumentiert; offene Schritte bleiben erkennbar.',
        }[aktion],
      );
    } catch (error) {
      this.melden('Nicht gespeichert. Eingaben bleiben erhalten; Verbindung prüfen.');
    } finally {
      this.busy = false;
      this.kontrollen();
    }
  }
}
if (typeof module !== 'undefined') module.exports = { LenkWerkstatt };
