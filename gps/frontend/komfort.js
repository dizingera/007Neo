/* Arbeitszentrale: bestehende Bedienwege zusammenführen, keine Lenkfreigabe. */
(function(root,factory){'use strict';if(typeof module==='object'&&module.exports)module.exports=factory();else root.Komfort=factory();})(typeof window!=='undefined'?window:globalThis,function(){
'use strict';
const namen={fields:'Felder',lines:'Spuren',jobs:'Arbeiten',arbeitstag:'Arbeitstag',machine:'Traktor & Gerät',lenkung:'Lenkung',lenkregler:'Lenkregler',saatplan:'Saatplan',headland:'Vorgewende'};
const norm=v=>String(v??'').toLocaleLowerCase('de-DE');
function felderFiltern(felder,suche,favoriten,nur,zuletzt){let r=felder.filter(f=>(!nur||favoriten.includes(f.id))&&norm(f.name).includes(norm(suche)));return r.slice().sort((a,b)=>{const ai=zuletzt.indexOf(a.id),bi=zuletzt.indexOf(b.id);return (ai<0?1e6:ai)-(bi<0?1e6:bi)||String(a.name).localeCompare(String(b.name),'de');});}
function naechsterTag(v){const d=new Date(v+'T00:00:00');d.setDate(d.getDate()+1);return d.getTime()/1000;}
function jobsFiltern(jobs,f){return jobs.filter(j=>(!f.feld||j.field_id===f.feld)&&(!f.text||norm([j.operation,j.vehicle,j.tractor].join(' ')).includes(norm(f.text)))&&(!f.status||(f.status==='fertig'?!!j.ended_at:f.status==='pausiert'?!!j.pausiert&&!j.ended_at:!j.ended_at))&&(!f.von||j.started_at>=new Date(f.von+'T00:00:00').getTime()/1000)&&(!f.bis||j.started_at<naechsterTag(f.bis)));}
function format(v){return v===null||v===undefined?'–':typeof v==='boolean'?(v?'Ja':'Nein'):String(v);}
let instanz=null,dialogOffen=false;
async function vorschau(url,body){
 if(!['/api/profile','/api/traktor','/api/settings'].includes(url)||!body)return body;
 if(dialogOffen)throw new Error('Zuerst die geöffnete Änderungsvorschau schließen');
 dialogOffen=true;let d;try{const r=await fetch('/api/aenderungsvorschau',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ziel:url,werte:body})});d=await r.json();if(!r.ok||!d.ok)throw new Error(d.detail||'Vorschau nicht verfügbar');}catch(e){dialogOffen=false;throw e;}
 if(!d.data.aenderungen.length){dialogOffen=false;return {...body,_vorschau:d.data.stand};}
 dialogOffen=true;
 return new Promise(resolve=>{
  const doc=document,dlg=doc.createElement('dialog');dlg.className='sheet komfort-vorschau';
  const h=doc.createElement('h2');h.textContent='Änderungen prüfen';dlg.appendChild(h);
  const p=doc.createElement('p');p.textContent='Diese Werte werden mit Übernehmen gespeichert.';dlg.appendChild(p);
  const table=doc.createElement('table');const head=doc.createElement('tr');['Einstellung','Bisher','Neu'].forEach(t=>{const th=doc.createElement('th');th.textContent=t;head.appendChild(th);});table.appendChild(head);
  d.data.aenderungen.forEach(x=>{const tr=doc.createElement('tr');[x.label,format(x.vorher),format(x.nachher)].forEach(t=>{const td=doc.createElement('td');td.textContent=t;tr.appendChild(td);});table.appendChild(tr);});dlg.appendChild(table);
  const row=doc.createElement('div');row.className='row';const nein=doc.createElement('button'),ja=doc.createElement('button');nein.textContent='Weiter bearbeiten';ja.textContent='Änderungen übernehmen';ja.className='primary';
  let erledigt=false;function fertig(v){if(erledigt)return;erledigt=true;dialogOffen=false;dlg.close();dlg.remove();resolve(v);}
  nein.onclick=()=>fertig(null);ja.onclick=()=>fertig({...body,_vorschau:d.data.stand});dlg.addEventListener('cancel',e=>{e.preventDefault();fertig(null);});row.append(nein,ja);dlg.appendChild(row);try{doc.body.appendChild(dlg);dlg.showModal();nein.focus();}catch(e){dialogOffen=false;dlg.remove();resolve(null);}
 });
}
function installieren(o){
 const doc=document,A=window.Arbeitsansicht,host=doc.getElementById('komfortInhalt');if(!host)return;
 let kontext={felder:[],jobs:[],traktoren:[],profile:[],vorlieben:{favoriten:[],zuletzt:[],vorlagen:[],schnellzugriffe:[]}},folge=0,restToken=0,planLaeuft=false,diagnoseAbruf=false;
 const n=(tag,text,cl)=>{const e=doc.createElement(tag);if(text!==undefined)e.textContent=text;if(cl)e.className=cl;return e;};
 const meld=n('p','Arbeitszentrale wird geladen …','komfort-meldung');meld.setAttribute('role','status');
 const req=async(method,url,body)=>{const r=await o.api(method,url,body);if(!r||r.ok===false)throw new Error(r?.detail||'Vorgang nicht übernommen');return Object.prototype.hasOwnProperty.call(r,'data')?r.data:r;};
 function b(z,t,fn,bereit=()=>true){const e=n('button',t);e.type='button';e.onclick=async()=>{if(e.disabled)return;e.disabled=true;try{await fn();}catch(x){meld.textContent=x.message;o.melden(x.message,true);}finally{e.disabled=!bereit();}};e.disabled=!bereit();z.appendChild(e);return e;}
 const abschnitt=(titel,offen=false)=>{const e=n('details',undefined,'komfort-gruppe');e.open=offen;e.appendChild(n('summary',titel));const x=n('div',undefined,'komfort-innen');e.appendChild(x);host.appendChild(e);return x;};
 function auswahl(z,titel){const l=n('label',titel),s=n('select');s.setAttribute('aria-label',titel);l.appendChild(s);z.appendChild(l);return s;}
 function input(z,titel,typ='text'){const l=n('label',titel),i=n('input');i.type=typ;l.appendChild(i);z.appendChild(l);return i;}
 function optionen(s,items,leer){const alt=s.value;s.replaceChildren();if(leer!==undefined){const op=n('option',leer);op.value='';s.appendChild(op);}items.forEach(x=>{const op=n('option',x.name||x.operation||x.feld||x.id);op.value=x.id;s.appendChild(op);});if([...s.options].some(x=>x.value===alt))s.value=alt;}
 host.replaceChildren();host.appendChild(meld);
 const start=abschnitt('Arbeitsstart · Feld, Gespann und Tätigkeit',true),r1=n('div',undefined,'komfort-raster');start.appendChild(r1);
 const feld=auswahl(r1,'1 · Feld'),traktor=auswahl(r1,'2 · Traktor'),geraet=auswahl(r1,'Gerät'),vorlage=auswahl(r1,'3 · Tätigkeitsvorlage'),taetigkeit=input(r1,'Tätigkeit');taetigkeit.maxLength=120;
 const breite=n('p','Arbeitsbreite beim Gerät prüfen.','komfort-breite');start.appendChild(breite);
 const pruefl=n('label',undefined,'komfort-check'),pruef=n('input');pruef.type='checkbox';pruefl.append(pruef,doc.createTextNode('Gespann und eingetragene Arbeitsbreite geprüft'));start.appendChild(pruefl);
 const startinfo=n('p','Die Lenkung bleibt ausgeschaltet.');start.appendChild(startinfo);
 function zusammen(){pruef.checked=false;const g=kontext.profile.find(x=>x.id===geraet.value);breite.textContent=g?`${kontext.felder.find(x=>x.id===feld.value)?.name||'Feld auswählen'} · ${kontext.traktoren.find(x=>x.id===traktor.value)?.name||'Traktor auswählen'} · ${g.name} · Arbeitsbreite ${A.zahl(g.width_m)} m`:'Gerät auswählen und Arbeitsbreite prüfen.';}
 function geraeteFuellen(vorzug=geraet.value){const t=kontext.traktoren.find(x=>x.id===traktor.value),passend=kontext.profile.filter(x=>(x.geraet_art==='schneidwerk')===(t?.bauform==='maehdrescher'));optionen(geraet,passend);geraet.value=passend.find(x=>x.id===vorzug)?.id||passend.find(x=>x.aktiv)?.id||passend[0]?.id||'';}
 [feld,geraet].forEach(x=>x.addEventListener('change',zusammen));
 traktor.addEventListener('change',()=>{geraeteFuellen();zusammen();});
 vorlage.onchange=()=>{const v=kontext.vorlieben.vorlagen.find(x=>x.id===vorlage.value);if(!v)return;taetigkeit.value=v.operation;if(v.traktor_id&&kontext.traktoren.some(x=>x.id===v.traktor_id))traktor.value=v.traktor_id;geraeteFuellen(v.profile_id||geraet.value);zusammen();};
 const starten=b(start,'Arbeit starten',async()=>{if(!pruef.checked)throw new Error('Gespann und Arbeitsbreite zuerst prüfen');const f=kontext.felder.find(x=>x.id===feld.value),g=kontext.profile.find(x=>x.id===geraet.value);if(!f||!g)throw new Error('Feld und Gerät auswählen');await req('POST','/api/arbeitsstart',{field_id:f.id,feld_stand:f.updated_at,traktor_id:traktor.value,profile_id:g.id,breite_m:g.width_m,operation:taetigkeit.value.trim()});o.melden('Arbeit gestartet – Lenkung bleibt aus');await laden();o.aktualisieren();});starten.className='primary';
 const betrieb=abschnitt('Laufende Arbeit · Pause und Fortsetzen'),aktuell=n('p');betrieb.appendChild(aktuell);
 const pauseTaste=b(betrieb,'Arbeit pausieren',async()=>{await req('POST','/api/arbeitstag/pause',{});o.melden('Arbeit pausiert');await laden();o.aktualisieren();});
 const abschlussTaste=b(betrieb,'Arbeit abschließen',async()=>{const ergebnis=await req('POST','/api/job/stop',{});if(!ergebnis?.id)throw new Error('Keine laufende Aufzeichnung – Auftrag ist bereits pausiert oder beendet');o.melden(ergebnis.verworfen?'Kurze Aufzeichnung verworfen – keine abgeschlossene Arbeit gespeichert':'Arbeit abgeschlossen');await laden();o.aktualisieren();});
 const offen=auswahl(betrieb,'Offenen Auftrag fortsetzen');
 b(betrieb,'Ausgewählten Auftrag fortsetzen',async()=>{if(!offen.value)throw new Error('Offenen Auftrag auswählen');await req('POST','/api/arbeitstag/fortsetzen',{job_id:offen.value});o.melden('Auftrag fortgesetzt – Lenkung bleibt aus');await laden();o.aktualisieren();});
 const auswertung=abschnitt('Feldchronik · Restfläche, Bericht und Kosten');
 const afeld=auswahl(auswertung,'Feld für Chronik und Bericht'),jahr=input(auswertung,'Saison','number');jahr.value=String(new Date().getFullYear());jahr.min='1970';jahr.max='2200';
 const chronik=n('ol',undefined,'komfort-chronik');auswertung.appendChild(chronik);
 const job=auswahl(auswertung,'Arbeitsgang für Restfläche und Kosten');
 const rest=n('div',undefined,'komfort-rest');auswertung.appendChild(rest);
 const pdf=n('a','Feldbericht als PDF','button');pdf.setAttribute('download','');auswertung.appendChild(pdf);
 const kostenbox=n('div',undefined,'komfort-raster');auswertung.appendChild(kostenbox);
 const liter=input(kostenbox,'Diesel (Liter)','number'),preis=input(kostenbox,'Dieselpreis (€/Liter)','number'),mittel=input(kostenbox,'Betriebsmittel gesamt (€)','number'),stunde=input(kostenbox,'Arbeitskosten (€/Stunde)','number');
 [liter,preis,mittel,stunde].forEach(x=>{x.min='0';x.step='0.01';});
 const kosteninfo=n('p','Verbrauch manuell eintragen; Kosten sind daraus berechnet.');auswertung.appendChild(kosteninfo);
 let kostenJob='',kostenSpeichert=false;
 const kostenBereit=()=>!!job.value&&kostenJob===job.value&&!kostenSpeichert;
 const kostenTaste=b(auswertung,'Verbrauch und Kosten speichern',async()=>{
  if(!kostenBereit())throw new Error('Kosten für diesen Arbeitsgang zuerst vollständig laden');
  const id=kostenJob,token=restToken,d={diesel_liter:liter.value===''?null:Number(liter.value),diesel_preis_eur_l:preis.value===''?null:Number(preis.value),betriebsmittel_eur:mittel.value===''?null:Number(mittel.value),stundensatz_eur:stunde.value===''?null:Number(stunde.value)};
  kostenSpeichert=true;kostenSperre();
  try{await req('POST',`/api/jobs/${encodeURIComponent(id)}/kosten`,d);o.melden('Verbrauch und Kosten gespeichert');if(id===job.value&&token===restToken)await restLaden(true);}
  finally{kostenSpeichert=false;kostenSperre();}
 },kostenBereit);
 function kostenSperre(){kostenTaste.disabled=!kostenBereit();kostenNeuLaden.disabled=kostenSpeichert;[liter,preis,mittel,stunde].forEach(x=>{x.disabled=!kostenBereit();});[job,afeld,jahr].forEach(x=>{x.disabled=kostenSpeichert;});}
 function kostenLeeren(){kostenJob='';[liter,preis,mittel,stunde].forEach(x=>{x.value='';});kosteninfo.textContent='Arbeitsgang auswählen und Kosten laden.';kostenSperre();}
 const kostenNeuLaden=b(auswertung,'Restfläche und Kosten neu laden',()=>restLaden(),()=>!kostenSpeichert);
 kostenLeeren();
 const muell=abschnitt('Papierkorb · Arbeiten wiederherstellen'),muellliste=n('div');muell.appendChild(muellliste);
 b(muell,'Papierkorb laden',papierkorb);
 const diagnose=abschnitt('Prüfung · Probefahrt, Saatpläne und Verbindungen'),di=n('div');diagnose.appendChild(di);
 const etappe=auswahl(diagnose,'Etappe der geführten Probefahrt');optionen(etappe,[{id:'gerade',name:'Geradeausfahrt'},{id:'langsam',name:'Langsame Fahrt'},{id:'arbeitsgeschwindigkeit',name:'Arbeitsgeschwindigkeit'},{id:'hang',name:'Fahrt am Hang'},{id:'wiederanfahrt',name:'Wiederanfahrt'}]);
 const anleitung=n('p','Sichere freie Strecke wählen. Dieselbe Spur und dasselbe Gespann verwenden. Lenkung und Geschwindigkeit bedienst du selbst. Messung nur unter geeigneten Bedingungen starten.');diagnose.appendChild(anleitung);
 const messCheck=n('label',undefined,'komfort-check'),messOk=n('input');messOk.type='checkbox';messCheck.append(messOk,doc.createTextNode('Geeignete Strecke und Hinweise für diese Messung geprüft'));diagnose.appendChild(messCheck);
 async function messen(aktion){if(!messOk.checked)throw new Error('Strecke und Messhinweise zuerst bestätigen');await req('POST','/api/ausbau/fahrserie',{aktion,etappe:etappe.value,bestaetigt:true});messOk.checked=false;await diagnoseLaden();}
 b(diagnose,'Vorher messen',()=>messen('vorher'));
 b(diagnose,'Nachher messen',()=>messen('nachher'));
 b(diagnose,'Messung abbrechen',async()=>{await req('POST','/api/ausbau/fahrserie',{aktion:'abbrechen'});await diagnoseLaden();});
 etappe.addEventListener('change',()=>{const hinweise={gerade:'Gleichmäßig auf einer geraden Spur fahren und die Spurabweichung vor und nach der Änderung vergleichen.',langsam:'Für die langsame Vergleichsfahrt gleichmäßig zwischen etwa 3 und 7 km/h fahren.',arbeitsgeschwindigkeit:'Mit der für diese Tätigkeit üblichen sicheren Arbeitsgeschwindigkeit gleichmäßig fahren.',hang:'Nur auf zulässiger sicherer Hanglage messen; aktuelle IMU-Daten sind erforderlich. Keine riskante Schrägfahrt für den Test erzwingen.',wiederanfahrt:'Zuerst im Stillstand vorbereiten. Nach manuellem Anfahren wird der stabile Fahrabschnitt gemessen; keine automatische Anfahrt.'};anleitung.textContent=hinweise[etappe.value];messOk.checked=false;});
 const abnahme=n('label',undefined,'komfort-check'),ab=n('input');ab.type='checkbox';abnahme.append(ab,doc.createTextNode('Dieses Gespann wurde mit den aktuellen Werten am echten Fahrzeug geprüft'));diagnose.appendChild(abnahme);
 b(diagnose,'Prüfung am echten Gespann bestätigen',async()=>{if(!ab.checked)throw new Error('Tatsächliche Prüfung zuerst bestätigen');await req('POST','/api/ausbau/abnahme',{bestaetigt:true});ab.checked=false;await diagnoseLaden();});
 b(diagnose,'Alle gespeicherten Saatpläne prüfen',async()=>{await req('POST','/api/ausbau/plaene/start',{});await plaeneLaden();});
 b(diagnose,'Planprüfung abbrechen',async()=>{await req('POST','/api/ausbau/plaene/abbrechen',{});await plaeneLaden();});
 b(diagnose,'Prüfstand aktualisieren',async()=>{await diagnoseLaden();await plaeneLaden();});
 const planinfo=n('div'),verbindungen=n('div');diagnose.append(planinfo,verbindungen);
 const vorlieben=abschnitt('Bedienung · Vorlagen und Schnellzugriffe'),vlname=input(vorlieben,'Name der neuen Tätigkeitsvorlage');vlname.maxLength=120;
 b(vorlieben,'Aktuelle Startauswahl als Vorlage speichern',async()=>{if(!vlname.value.trim()||!taetigkeit.value.trim())throw new Error('Vorlagenname und Tätigkeit angeben');const v={id:'v'+Date.now().toString(36),name:vlname.value.trim(),operation:taetigkeit.value.trim(),traktor_id:traktor.value,profile_id:geraet.value};kontext.vorlieben=await req('POST','/api/komfort',{vorlagen:[...kontext.vorlieben.vorlagen,v]});vlname.value='';fuellen();o.melden('Tätigkeitsvorlage gespeichert');});
 const vlliste=n('div');vorlieben.appendChild(vlliste);
 const kurz=[1,2,3].map(i=>auswahl(vorlieben,'Schnellzugriff '+i));kurz.forEach(x=>optionen(x,Object.entries(namen).map(([id,name])=>({id,name}))));
 b(vorlieben,'Schnellzugriffe speichern',async()=>{kontext.vorlieben=await req('POST','/api/komfort',{schnellzugriffe:kurz.map(x=>x.value)});schnell();o.melden('Schnellzugriffe gespeichert');});
 b(vorlieben,'Alles aktualisieren',laden);
 function schnell(){const z=doc.getElementById('komfortSchnell');if(!z)return;z.replaceChildren();kontext.vorlieben.schnellzugriffe.forEach(k=>b(z,namen[k]||k,()=>o.oeffnen(k)));}
 function fuellen(){
  optionen(feld,kontext.felder,'Feld auswählen');optionen(afeld,kontext.felder,'Feld auswählen');const vorherTraktor=traktor.value;optionen(traktor,kontext.traktoren);if(!kontext.traktoren.some(x=>x.id===vorherTraktor))traktor.value=kontext.traktoren.find(x=>x.aktiv)?.id||kontext.traktoren[0]?.id||'';geraeteFuellen();optionen(vorlage,kontext.vorlieben.vorlagen,'Eigene Tätigkeit');
  const live=o.getState()||{};if(!traktor.value)traktor.value=kontext.traktoren.find(x=>x.aktiv)?.id||'';if(!geraet.value)geraet.value=kontext.profile.find(x=>x.aktiv)?.id||'';
  if(!feld.value&&live.field)feld.value=live.field.id;if(!afeld.value&&live.field)afeld.value=live.field.id;
  optionen(offen,kontext.jobs.filter(j=>!j.ended_at&&j.device_id===live.device?.id&&(j.pausiert||j.id!==live.job?.id)).map(j=>({...j,name:`${j.operation||'Arbeit'} · ${kontext.felder.find(f=>f.id===j.field_id)?.name||'Feld'}`})),'Offenen Auftrag auswählen');
  kurz.forEach((x,i)=>{x.value=kontext.vorlieben.schnellzugriffe[i]||'';});
  vlliste.replaceChildren();kontext.vorlieben.vorlagen.forEach(v=>{const zeile=n('div',undefined,'row');zeile.appendChild(n('span',v.name));b(zeile,'Vorlage entfernen',async()=>{kontext.vorlieben=await req('POST','/api/komfort',{vorlagen:kontext.vorlieben.vorlagen.filter(x=>x.id!==v.id)});fuellen();o.melden('Vorlage entfernt');});vlliste.appendChild(zeile);});
  zusammen();chronikZeigen();schnell();filterFelderFuellen();
 }
 function chronikZeilen(s=o.getState()){const jobs=kontext.jobs.filter(j=>(!afeld.value||j.field_id===afeld.value)&&new Date(j.started_at*1000).getFullYear()===Number(jahr.value));chronik.replaceChildren();if(!jobs.length)chronik.appendChild(n('li','Keine Arbeiten in dieser Auswahl.'));jobs.forEach(j=>{const li=n('li'),v=A.arbeit(j,!j.pausiert&&s?.job?.id===j.id);li.append(n('strong',j.operation||'Arbeit'),n('span',`${v.datum} · ${v.status} · ${v.flaeche} · ${j.vehicle||'Gerät unbekannt'}`));chronik.appendChild(li);});return jobs;}
 function chronikZeigen(){const jobs=chronikZeilen();optionen(job,jobs.map(j=>({...j,name:`${j.operation||'Arbeit'} · ${A.arbeit(j,false).datum}`})),'Arbeitsgang auswählen');pdf.hidden=!afeld.value;pdf.href=afeld.value?`/api/fields/${encodeURIComponent(afeld.value)}/bericht.pdf?jahr=${encodeURIComponent(jahr.value)}`:'#';restToken++;rest.replaceChildren();kostenLeeren();}
 afeld.onchange=chronikZeigen;jahr.onchange=chronikZeigen;
 async function restLaden(nachSpeichern=false){
  if(kostenSpeichert&&nachSpeichern!==true)return;
  const id=job.value,token=++restToken;kostenLeeren();
  if(!id){rest.replaceChildren();return;}
  rest.textContent='Restfläche und Kosten werden geladen …';
  try{
   const [r,k]=await Promise.all([req('GET',`/api/jobs/${encodeURIComponent(id)}/restflaeche`),req('GET',`/api/jobs/${encodeURIComponent(id)}/kosten`)]);
   if(token!==restToken||id!==job.value)return;
   rest.replaceChildren();rest.appendChild(n('p',r.hinweis||''));rest.appendChild(A.kennzahlen(doc,[['Feldfläche',A.zahl(r.gesamt_ha)+' ha'],['Bearbeitet',A.zahl(r.bearbeitet_ha)+' ha'],['Noch offen',A.zahl(r.rest_ha)+' ha']]));if(r.bekannt&&Number.isFinite(r.prozent))rest.appendChild(n('p','Bearbeitet in diesem Arbeitsgang: '+A.zahl(r.prozent,1)+' %'));const t=n('p',r.restzeit_min===null||r.restzeit_min===undefined?'Restzeit: noch keine belastbare Schätzung':`Geschätzte Restzeit: ${A.zahl(r.restzeit_min,0)} min`);rest.appendChild(t);restKarte(r);const w=k.eingaben||k;liter.value=w.diesel_liter??'';preis.value=w.diesel_preis_eur_l??'';mittel.value=w.betriebsmittel_eur??'';stunde.value=w.stundensatz_eur??'';kosteninfo.textContent=(k.hinweis||'Manuelle Verbrauchsangaben; Kosten daraus berechnet.')+' · Gesamtkosten: '+A.zahl(k.gesamt_eur)+' € · je Hektar: '+A.zahl(k.kosten_eur_ha)+' €';
   kostenJob=id;kostenSperre();
  }catch(x){if(token!==restToken||id!==job.value)return;rest.textContent=x.message;kosteninfo.textContent='Kosten nicht geladen – erneut laden, bevor du etwas änderst.';kostenSperre();}
 }
 job.onchange=restLaden;
 function restKarte(r){if(!Array.isArray(r.grenze)||r.grenze.length<3)return;const can=n('canvas');can.width=720;can.height=320;can.setAttribute('aria-label','Restflächenkarte: Grenze und bearbeitete Zellen des ausgewählten Arbeitsgangs');rest.appendChild(can);const ctx=can.getContext('2d'),p=r.grenze.filter(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite));if(p.length<3)return;const xs=p.map(x=>x[0]),ys=p.map(x=>x[1]),xmin=Math.min(...xs),ymin=Math.min(...ys),w=Math.max(...xs)-xmin,h=Math.max(...ys)-ymin,k=Math.min(680/Math.max(w,1),280/Math.max(h,1)),pos=([x,y])=>[20+(x-xmin)*k,300-(y-ymin)*k];ctx.fillStyle='#25372c';ctx.strokeStyle='#b7cbbc';ctx.lineWidth=2;ctx.beginPath();p.forEach((v,i)=>{const q=pos(v);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.closePath();ctx.fill();ctx.stroke();ctx.save();ctx.clip();ctx.fillStyle='#51c780';(r.kartenZellen||[]).filter(v=>v.status==='bearbeitet').forEach(v=>{if(!Array.isArray(v.polygon))return;ctx.beginPath();v.polygon.forEach((p,i)=>{const q=pos(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.closePath();ctx.fill();});ctx.restore();rest.appendChild(n('p','Grün: bearbeitet · dunkle Fläche: '+(r.karten_vollstaendig?'noch offen':'Kartenstand unbekannt, Darstellung begrenzt')+'. Räumliche Überlappung ist in alten Rasterdaten nicht enthalten.'));}
 async function papierkorb(){const r=await req('GET','/api/papierkorb');muellliste.replaceChildren();const jobs=r.jobs||r.arbeiten||[];if(!jobs.length)muellliste.appendChild(n('p','Papierkorb ist leer.'));jobs.forEach(j=>{const row=n('div',undefined,'komfort-papier');row.appendChild(n('p',`${j.operation||'Arbeit'} · ${A.arbeit(j,false).datum} · ${A.zahl(j.area_ha)} ha`));b(row,'Arbeit wiederherstellen',async()=>{await req('POST',`/api/papierkorb/${encodeURIComponent(j.id)}/wiederherstellen`,{});o.melden('Arbeit wiederhergestellt');await laden();await papierkorb();o.aktualisieren();});muellliste.appendChild(row);});}
 async function diagnoseLaden(){const r=await req('GET','/api/ausbau/diagnose');di.replaceChildren();di.appendChild(n('p',r.hinweis||'Prüfungen ersetzen keine echte Messfahrt.'));diagnoseRender(di,r);}
 const etappenNamen={gerade:'Geradeausfahrt',langsam:'Langsame Fahrt',arbeitsgeschwindigkeit:'Arbeitsgeschwindigkeit',hang:'Fahrt am Hang',wiederanfahrt:'Wiederanfahrt'};
 const uhr=t=>typeof t==='number'&&Number.isFinite(t)?new Date(t*1000).toLocaleString('de-DE'):'Zeit unbekannt';
 function diagnoseRender(z,r){
  const f=r.fahrserie||{},a=r.abnahme||{},pb=f.probefahrt||{};
  z.appendChild(n('h3',f.uebung?'Probefahrt · Übung':'Geführte Probefahrt'));
  z.appendChild(n('p',pb.hinweis||'Etappe und Messphase auswählen.'));
  if(f.aktiv)z.appendChild(n('p',`${etappenNamen[f.aktiv.etappe]||'Messung'} · ${A.zahl(pb.sammelt_s,0)} von ${A.zahl(pb.dauer_s,0)} Sekunden`));
  (f.vorschriften||[]).forEach(t=>z.appendChild(n('p',t)));
  const liste=n('div',undefined,'komfort-messungen');
  (f.ergebnisse||[]).slice(-10).reverse().forEach(e=>{const v=e.vorher||{},nach=e.nachher||{};const box=n('article',undefined,'komfort-diagnose');box.append(n('strong',`${etappenNamen[e.etappe]||e.etappe}${e.uebung?' · Übung':''}`),n('p',uhr(e.zeit)),A.kennzahlen(doc,[['Spurabweichung vorher',A.zahl(v.rms_cm,1)+' cm'],['Spurabweichung nachher',A.zahl(nach.rms_cm,1)+' cm'],['Motorverzögerung nachher',A.zahl(nach.nachlauf_s,1)+' s']]));box.appendChild(n('p',e.vergleichbar?(e.etappe_gueltig?'Vergleich unter passenden Bedingungen aufgezeichnet':'Bedingungen dieser Etappe nicht vollständig erreicht'):'Messungen nicht ausreichend vergleichbar'));liste.appendChild(box);});z.appendChild(liste);
  const box=n('div',undefined,'komfort-diagnose');box.append(n('h3','Prüfstand dieses Gespanns'),n('p',({geprueft:'Am echten Gespann geprüft',veraltet:'Seit der letzten Prüfung geändert',ungeprueft:'Noch nicht am echten Gespann bestätigt'})[a.status]||'Prüfstand unbekannt'));
  if(a.abnahme?.zeit)box.appendChild(n('p','Letzte Bestätigung: '+uhr(a.abnahme.zeit)));
  if(a.fehlende_etappen?.length)box.appendChild(n('p','Noch erforderlich: '+a.fehlende_etappen.map(k=>etappenNamen[k]||k).join(', ')));
  box.appendChild(n('p',a.hinweis||''));z.appendChild(box);
  const v=r.verlauf||r.verbindungen||{};verbindungen.replaceChildren(n('h3','Verbindungsverlauf'),n('p',v.hinweis||''));
  const ul=n('ol',undefined,'komfort-chronik');(v.kanten||[]).slice(-40).reverse().forEach(e=>{const li=n('li');li.append(n('strong',({gps:'GPS-Empfang',rtk:'RTK-Genauigkeit',imu:'Neigungssensor',master:'Verbindung zum Master'})[e.kanal]||'Verbindung'),n('span',`${uhr(e.zeit)} · ${({ok:'Verbindung vorhanden',ausfall:'Unterbrochen',unbekannt:'Nicht beurteilt'})[e.zustand]||e.zustand}${e.uebung?' · Übung':''}${e.neustart?' · erster Zustand nach Start':''}`));ul.appendChild(li);});verbindungen.appendChild(ul);
 }
 async function plaeneLaden(){const r=await req('GET','/api/ausbau/plaene');planLaeuft=r.status==='laeuft';planinfo.replaceChildren(n('h3','Gespeicherte Saatpläne'),n('p',`${({laeuft:'Prüfung läuft',fertig:'Prüfung beendet',bereit:'Noch nicht gestartet',abgebrochen:'Prüfung abgebrochen',fehler:'Prüfung fehlgeschlagen'})[r.status]||r.status} · ${r.fertig||0} von ${r.gesamt||0} Feldern`),n('p',r.hinweis||''));
  (r.felder||[]).forEach(f=>{const d=n('details',undefined,'komfort-planfeld');d.appendChild(n('summary',`${f.feld||'Feld'} · ${f.status==='kein_plan'?'Kein gespeicherter Plan':(f.befunde?.length||0)+' Befunde'}${f.vollstaendig===false?' · Prüfung unvollständig':''}`));d.appendChild(n('p',f.status==='kein_plan'?'Für dieses Feld ist kein Saatplan gespeichert.':f.hinweis||''));(f.befunde||[]).slice(0,100).forEach(x=>d.appendChild(n('p',x.text||x.art||'Hinweis')));
   if(f.befunde?.some(x=>x.punkte?.length)){b(d,'Problemstellen auf der Feldkarte zeigen',()=>planKarte(d,f));}planinfo.appendChild(d);});}
 function planKarte(z,f){z.querySelector('canvas')?.remove();const grenze=kontext.felder.find(x=>x.id===f.feld_id)?.boundary||[],punkte=(f.befunde||[]).flatMap(x=>x.punkte||[]),alle=[...grenze,...punkte].filter(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite));if(!alle.length)return;const c=n('canvas');c.width=720;c.height=360;c.setAttribute('aria-label','Problemstellen der Saatplanprüfung');z.appendChild(c);const ctx=c.getContext('2d'),xs=alle.map(p=>p[0]),ys=alle.map(p=>p[1]),xmin=Math.min(...xs),ymin=Math.min(...ys),k=Math.min(680/Math.max(1,Math.max(...xs)-xmin),320/Math.max(1,Math.max(...ys)-ymin)),p=([x,y])=>[20+(x-xmin)*k,340-(y-ymin)*k];ctx.strokeStyle='#b7cbbc';ctx.lineWidth=2;ctx.beginPath();grenze.forEach((q,i)=>i?ctx.lineTo(...p(q)):ctx.moveTo(...p(q)));ctx.closePath();ctx.stroke();ctx.fillStyle='#ffb020';punkte.forEach(q=>{ctx.beginPath();ctx.arc(...p(q),6,0,Math.PI*2);ctx.fill();});}
 const fs=doc.getElementById('komfortFeldSuche'),ff=doc.getElementById('komfortNurFavoriten'),js=doc.getElementById('komfortJobSuche'),jf=doc.getElementById('komfortJobFeld'),jstatus=doc.getElementById('komfortJobStatus'),jvon=doc.getElementById('komfortJobVon'),jbis=doc.getElementById('komfortJobBis');
 function filterFelderFuellen(){if(jf)optionen(jf,kontext.felder,'Alle Felder');}
 [fs,ff,js,jf,jstatus,jvon,jbis].filter(Boolean).forEach(x=>x.addEventListener('input',()=>o.aktualisieren()));
 async function laden(){const token=++folge;const r=await req('GET','/api/ausbau/kontext');if(token!==folge)return;kontext=r;fuellen();meld.textContent='Feld und Arbeitsgang auswählen. Änderungen werden erst mit der jeweiligen Taste übernommen.';live(o.getState());}
 let liveArbeitsstand='';
 function live(s){s=s||{};const stand=JSON.stringify([s.job?.id,s.pausierte_arbeit?.id]);if(stand!==liveArbeitsstand){liveArbeitsstand=stand;optionen(offen,kontext.jobs.filter(j=>!j.ended_at&&j.device_id===s.device?.id&&(j.pausiert||j.id!==s.job?.id)).map(j=>({...j,name:`${j.operation||'Arbeit'} · ${kontext.felder.find(f=>f.id===j.field_id)?.name||'Feld'}`})),'Offenen Auftrag auswählen');chronikZeilen(s);}aktuell.textContent=s.job?`${s.job.operation||'Arbeit'} · ${A.zahl(s.job.area_ha)} ha · Aufzeichnung aktiv`:s.pausierte_arbeit?`Pausiert: ${s.pausierte_arbeit.operation||'Arbeit'} · ausdrücklich fortsetzen`:'Keine laufende Aufzeichnung';starten.disabled=!!s.job;pauseTaste.disabled=!s.job;abschlussTaste.disabled=!s.job;}
 instanz={laden,live,geladen:id=>{if(!id)return;const d=kontext.vorlieben;d.zuletzt=[id,...d.zuletzt.filter(x=>x!==id)].slice(0,12);},feldfilter:fs?()=>({suche:fs.value,nur:!!ff.checked}):()=>({}),jobsfilter:()=>({text:js?.value||'',feld:jf?.value||'',status:jstatus?.value||'',von:jvon?.value||'',bis:jbis?.value||''}),vorlieben:()=>kontext.vorlieben,favorit:async id=>{const f=kontext.vorlieben.favoriten;kontext.vorlieben=await req('POST','/api/komfort',{favoriten:f.includes(id)?f.filter(x=>x!==id):[...f,id]});o.aktualisieren();o.melden('Favoriten gespeichert');}};
 window.setInterval(async()=>{if(diagnoseAbruf||doc.hidden||!diagnose.parentElement.open||!host.getClientRects().length)return;diagnoseAbruf=true;try{await diagnoseLaden();if(planLaeuft)await plaeneLaden();}catch(e){meld.textContent=e.message;}finally{diagnoseAbruf=false;}},2000);
 laden().catch(x=>{meld.textContent=x.message;});
 return instanz;
}
return {installieren,vorschau,felderFiltern,jobsFiltern,namen,get instanz(){return instanz;}};
});
