/* Sichtbare Bestätigung erst nach angenommener Antwort; keine Meldungen für Fahrdaten. */
(function(root,factory){
  'use strict';
  if(typeof module==='object'&&module.exports)module.exports=factory();
  else root.Rueckmeldung=factory();
})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  function erfolgstext(method,url,body,antwort){
    if(String(method).toUpperCase()!=='POST'||!antwort||antwort.ok!==true)return null;
    const d=antwort.data;
    if(d&&typeof d==='object'&&(d.ok===false||d.gut===false||d.erfolg===false||d.fehler))return null;
    const p=String(url).split('?')[0], a=body&&body.aktion;
    if(p==='/api/settings')return d&&d.gespeichert>0?'Einstellungen gespeichert':null;
    if(p==='/api/komfort')return 'Bedienvorlieben gespeichert';
    if(p==='/api/arbeitsstart')return 'Arbeit gestartet – Lenkung bleibt aus';
    if(p==='/api/arbeitstag/pause')return 'Arbeit pausiert';
    if(p==='/api/arbeitstag/fortsetzen')return 'Auftrag fortgesetzt – Lenkung bleibt aus';
    if(/^\/api\/jobs\/[^/]+\/kosten$/.test(p))return 'Verbrauch und Kosten gespeichert';
    if(/^\/api\/papierkorb\/[^/]+\/wiederherstellen$/.test(p))return 'Arbeit wiederhergestellt';
    if(p==='/api/ausbau/fahrserie')return a==='abbrechen'?'Messung abgebrochen':'Messung vorbereitet – Fahrbedingungen beachten';
    if(p==='/api/ausbau/abnahme')return 'Prüfstand bestätigt';
    if(p==='/api/ausbau/plaene/start')return 'Planprüfung gestartet';
    if(p==='/api/ausbau/plaene/abbrechen')return 'Abbruch der Planprüfung angefordert';
    if(p==='/api/traktor')return 'Traktorwerte übernommen';
    if(p==='/api/profile')return 'Gerätewerte übernommen';
    if(p==='/api/headland')return 'Vorgewende-Einstellungen übernommen';
    if(/^\/api\/fields\/[^/]+\/arbeitsgrenze$/.test(p))return 'Grenze gespeichert';
    if(/^\/api\/jobs\/[^/]+\/referenz$/.test(p))return 'Referenzspur gespeichert';
    if(/^\/api\/lines\/[^/]+$/.test(p))return 'Spur gespeichert';
    if(/^\/api\/fields\/[^/]+\/saatplan\/uebernehmen$/.test(p))return 'Saatplan übernommen';
    if(/^\/api\/checklist\/[^/]+$/.test(p))return body&&body.erledigt?'Schritt bestätigt':'Bestätigung zurückgenommen';
    if(p==='/api/lenkung/kalibrierung')return ({geometrie:'Gemessene Maße gespeichert',sichern:'Traktorwerte gesichert',wiederherstellen:'Traktorwerte wiederhergestellt',protokoll:'Prüfstand dokumentiert'})[a]||null;
    if(p==='/api/lenkung/einrichten/erledigt')return 'Einrichtungsschritt bestätigt';
    if(p==='/api/lenkung/einrichten/richtung')return 'Drehrichtung übernommen';
    if(p==='/api/imu/level')return 'Neigungssensor genullt und gespeichert';
    if(p==='/api/imu/kalibrieren/m1')return 'Erste Messung bestätigt';
    if(p==='/api/imu/kalibrieren/m2')return 'Neigungskalibrierung gespeichert';
    if(p==='/api/fahrgassen')return 'Fahrgassenweite gespeichert';
    if(p==='/api/backup')return d&&d.ok===true&&(!Array.isArray(d.ziele)||d.ziele.every(z=>z.ok))?'Sicherung erfolgreich erstellt':null;
    if(p==='/api/record/stop')return 'Aufzeichnung gespeichert';
    if(p==='/api/arbeitstag/einfahrt')return a==='loeschen'?'Einfahrt gelöscht':'Einfahrt gespeichert';
    if(p==='/api/betriebsbuch/wiederherstellen')return 'Vorherige Werte wiederhergestellt';
    if(p==='/api/sync/konflikte/loesen')return 'Ausgewählter Stand übernommen';
    return null;
  }
  function art(text,fehler,erwartet){
    if(fehler)return 'fehler';
    const t=String(text||'');
    if(/nicht gespeichert|fehlgeschlagen|nicht übernommen|keine änderung|nichts geändert|fehler|gesperrt|abgebrochen/i.test(t))return 'hinweis';
    return erwartet||/gespeichert|übernommen|bestätigt|gesichert|wiederhergestellt|genullt|dokumentiert/i.test(t)?'erfolg':'hinweis';
  }
  function erstellen(doc,zeit){
    const uhr=zeit||{setTimeout:(fn,ms)=>setTimeout(fn,ms),clearTimeout:id=>clearTimeout(id)};
    let pending=null,wartend=null,timer=null,aktiverTyp=null;
    const schlange=[];
    const box=()=>doc.getElementById('toast');
    function platzieren(){
      const b=box();if(!b||b.hidden)return;
      const dialog=doc.querySelector('dialog[open]');
      const ziel=dialog||doc.body;
      if(b.parentElement!==ziel)ziel.appendChild(b);
    }
    doc.addEventListener('close',platzieren,true);
    function abbrechen(){if(wartend!==null)uhr.clearTimeout(wartend);wartend=null;pending=null;}
    function anzeigen(text,fehler){
      const typ=art(text,fehler,!!pending);abbrechen();
      const b=box();if(!b)return;
      if(aktiverTyp==='fehler'&&!b.hidden&&typ!=='fehler'){
        if(!schlange.some(v=>v.text===String(text)&&v.typ===typ))schlange.push({text:String(text),typ});
        if(schlange.length>5)schlange.shift();
        return;
      }
      ausgabe(text,typ);
    }
    function weiter(){
      const b=box();if(timer!==null)uhr.clearTimeout(timer);timer=null;
      if(schlange.length){const v=schlange.shift();ausgabe(v.text,v.typ);}
      else {if(b)b.hidden=true;aktiverTyp=null;}
    }
    function ausgabe(text,typ){
      const b=box();if(!b)return;aktiverTyp=typ;
      b.replaceChildren();b.className='toast rueckmeldung '+(typ==='fehler'?'error':typ);
      b.setAttribute('role',typ==='fehler'?'alert':'status');
      b.setAttribute('aria-live',typ==='fehler'?'assertive':'polite');b.setAttribute('aria-atomic','true');
      const icon=doc.createElement('span');icon.className='rueckmeldung-symbol';icon.setAttribute('aria-hidden','true');icon.textContent=typ==='erfolg'?'✓':typ==='fehler'?'!':'i';
      const inhalt=doc.createElement('span');inhalt.className='rueckmeldung-text';inhalt.textContent=String(text||'');
      const zu=doc.createElement('button');zu.type='button';zu.className='rueckmeldung-zu';zu.setAttribute('aria-label','Meldung schließen');zu.textContent='×';
      zu.onclick=weiter;
      b.append(icon,inhalt,zu);b.hidden=false;platzieren();
      if(timer!==null)uhr.clearTimeout(timer);
      timer=uhr.setTimeout(weiter,typ==='fehler'?9000:typ==='erfolg'?6000:4000);
    }
    function vormerken(method,url,body,antwort){
      const text=erfolgstext(method,url,body,antwort);if(!text)return;
      abbrechen();pending=text;
      // Vorhandene genauere Meldungen desselben Bedienwegs haben Vorrang.
      wartend=uhr.setTimeout(()=>{const t=pending;wartend=null;if(t)anzeigen(t,false);},180);
    }
    return {anzeigen,vormerken,abbrechen,platzieren};
  }
  let instanz=null;
  const holen=()=>instanz||(instanz=erstellen(document));
  return {erfolgstext,art,erstellen,anzeigen:(...a)=>holen().anzeigen(...a),vormerken:(...a)=>holen().vormerken(...a),abbrechen:()=>holen().abbrechen()};
});
