/* Lesbare Arbeitsdaten; Fläche mehrerer Durchgänge ist kein Feldfortschritt. */
(function(root,factory){'use strict';if(typeof module==='object'&&module.exports)module.exports=factory();else root.Arbeitsansicht=factory();})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const wert=v=>v===null||v===undefined||v===''||!Number.isFinite(Number(v))||Number(v)<0?null:Number(v);
  const zahl=(v,n=2)=>wert(v)===null?'–':Number(v).toLocaleString('de-DE',{minimumFractionDigits:n,maximumFractionDigits:n});
  function dauer(s){const v=wert(s);if(v===null)return '–';const m=Math.floor(v/60);return m<60?`${m} min`:`${Math.floor(m/60)} h ${m%60} min`;}
  function sekunden(j){if(wert(j.working_time_s)!==null)return Number(j.working_time_s);return wert(j.ended_at)!==null&&wert(j.started_at)!==null?Math.max(0,j.ended_at-j.started_at):null;}
  function arbeit(j,aktiv){return {status:j.ended_at?'Abgeschlossen':aktiv?'In Arbeit':'Offener Auftrag',aktiv:!j.ended_at&&!!aktiv,datum:wert(j.started_at)===null?'Datum unbekannt':new Date(j.started_at*1000).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}),flaeche:zahl(j.area_ha)+' ha',dauer:dauer(sekunden(j)),strecke:zahl(wert(j.distance_m)===null?null:j.distance_m/1000,1)+' km',doppelt:zahl(j.overlap_ha)+' ha'};}
  function summe(jobs){return {anzahl:jobs.length,offen:jobs.filter(j=>!j.ended_at).length,flaeche:jobs.reduce((s,j)=>s+(wert(j.area_ha)||0),0)};}
  function umriss(grenze){if(!Array.isArray(grenze))return null;const p=grenze.filter(p=>Array.isArray(p)&&p.length>=2&&p.slice(0,2).every(v=>v!==null&&v!==''&&Number.isFinite(Number(v)))).map(p=>p.slice(0,2).map(Number));if(p.length<3)return null;const xs=p.map(p=>p[0]),ys=p.map(p=>p[1]),xmin=Math.min(...xs),ymin=Math.min(...ys),w=Math.max(...xs)-xmin,h=Math.max(...ys)-ymin,k=72/Math.max(w,h);if(!Number.isFinite(k))return null;return p.map(([x,y])=>`${(14+(72-w*k)/2+(x-xmin)*k).toFixed(2)},${(14+(72-h*k)/2+(ymin+h-y)*k).toFixed(2)}`).join(' ');}
  function kennzahlen(doc,werte){const box=doc.createElement('dl');box.className='aw-werte';werte.forEach(([titel,wert])=>{const g=doc.createElement('div'),dt=doc.createElement('dt'),dd=doc.createElement('dd');dt.textContent=titel;dd.textContent=wert;g.append(dt,dd);box.appendChild(g);});return box;}
  return {wert,zahl,dauer,sekunden,arbeit,summe,umriss,kennzahlen};
});
