/* Zahleneingaben bleiben beim Rollen unverändert. Entwürfe bleiben sichtbar. */
(function (wurzel) {
  'use strict';
  function zahlenrad(event) {
    const feld = event.target;
    if (!feld || feld.tagName !== 'INPUT' || feld.type !== 'number' || event.ctrlKey) return false;
    event.preventDefault();
    const scroller = feld.closest('.tab-panel, dialog') || feld.ownerDocument.scrollingElement;
    if (scroller && typeof scroller.scrollBy === 'function') {
      const faktor = event.deltaMode === 1 ? 20 : event.deltaMode === 2 ? scroller.clientHeight : 1;
      scroller.scrollBy({ top: event.deltaY * faktor, left: event.deltaX * faktor, behavior: 'auto' });
    }
    return true;
  }
  function installieren(doc) {
    doc.addEventListener('wheel', zahlenrad, { capture: true, passive: false });
    const sheet = doc.getElementById('sheet');
    const hinweis = doc.getElementById('menueEntwurf');
    const zeigen = () => {
      const entwurf = sheet.querySelector('[data-geaendert="1"], input.geaendert, select.geaendert, .regler-zeile.geaendert, .setting-field.changed, .motorstaerke.entwurf');
      hinweis.hidden = !entwurf;
      if (entwurf) {
        const panel = entwurf.closest('.tab-panel');
        const name = panel && sheet.querySelector(`.tab[data-tab="${panel.dataset.panel}"] .tab-name`);
        const text = `Noch nicht übernommen: ${name ? name.textContent : 'Einstellungen'} – markierte Werte übernehmen.`;
        if (hinweis.textContent !== text) hinweis.textContent = text;
      }
    };
    const observer = new doc.defaultView.MutationObserver(zeigen);
    observer.observe(sheet, { subtree: true, attributes: true, attributeFilter: ['class', 'data-geaendert'], childList: true });
    zeigen();
    return observer;
  }
  const ausfuhr = { zahlenrad, installieren };
  if (typeof module !== 'undefined' && module.exports) module.exports = ausfuhr;
  else wurzel.Bedienung = ausfuhr;
})(typeof window !== 'undefined' ? window : globalThis);
