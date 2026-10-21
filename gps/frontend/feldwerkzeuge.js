/* Feldwerkzeuge: Entwürfe bleiben an Feld/Aufzeichnung gebunden. */
(function(ziel){
'use strict';
const kopie=p=>p.map(v=>[...v]);
function vorgangskennung(zufall=globalThis.crypto){
  if(zufall.randomUUID)return zufall.randomUUID();
  const b=zufall.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;
  const h=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}
function kuerzen(punkte,anfang=0,ende=0){
  const laenge=punkte.slice(1).reduce((s,p,i)=>s+Math.hypot(p[0]-punkte[i][0],p[1]-punkte[i][1]),0);
  if(!Number.isFinite(anfang)||!Number.isFinite(ende)||anfang<0||ende<0||laenge-anfang-ende<20)return [];
  const aus=[];let weg=0;const bis=laenge-ende;
  for(let i=1;i<punkte.length;i++){const a=punkte[i-1],b=punkte[i],d=Math.hypot(b[0]-a[0],b[1]-a[1]);
    if(d>1e-9&&weg+d>=anfang&&weg<=bis)for(const f of [Math.max(0,(anfang-weg)/d),Math.min(1,(bis-weg)/d)]){
      const p=[a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f];
      if(!aus.length||Math.hypot(aus.at(-1)[0]-p[0],aus.at(-1)[1]-p[1])>1e-8)aus.push(p);
    }weg+=d;
  }return aus;
}
class Grenzentwurf {
  constructor(punkte){this.punkte=kopie(punkte);this.verlauf=[];this.auswahl=0;}
  merken(){this.verlauf.push({punkte:kopie(this.punkte),auswahl:this.auswahl});if(this.verlauf.length>100)this.verlauf.shift();}
  setzen(p){if(!p.every(Number.isFinite))return false;this.merken();this.punkte[this.auswahl]=p;return true;}
  verschieben(dx,dy){const p=this.punkte[this.auswahl];return this.setzen([p[0]+dx,p[1]+dy]);}
  einfuegen(){this.merken();const a=this.punkte[this.auswahl],b=this.punkte[(this.auswahl+1)%this.punkte.length];this.punkte.splice(++this.auswahl,0,[(a[0]+b[0])/2,(a[1]+b[1])/2]);}
  entfernen(){if(this.punkte.length<=3)return;this.merken();this.punkte.splice(this.auswahl,1);this.auswahl=Math.min(this.auswahl,this.punkte.length-1);}
  zurueck(){const alt=this.verlauf.pop();if(alt){this.punkte=alt.punkte;this.auswahl=alt.auswahl;}}
  ersetzen(p){this.merken();this.punkte=kopie(p);this.auswahl=0;}
}
class Feldwerkzeuge {
  constructor({el,api,aktualisieren,profil}){
    this.el=el;this.api=api;this.aktualisieren=aktualisieren;this.profil=profil;this.dialog=el('feldWerkzeug');this.folge=0;this.dirty=false;this.busy=false;
    el('fwSchliessen').onclick=()=>this.schliessen();
    this.dialog.addEventListener('close',()=>document.querySelector('.tabs .tab.active')?.focus());
    this.dialog.addEventListener('cancel',e=>{e.preventDefault();this.schliessen();});
    el('fwPunkt').onchange=()=>{this.entwurf.auswahl=Number(el('fwPunkt').value);this.koordinatenUngueltig=false;this.zeichnen();};
    for(const id of ['fwOst','fwNord'])el(id).oninput=()=>{
      if(this.busy)return;const p=[el('fwOst').valueAsNumber,el('fwNord').valueAsNumber];
      if(this.entwurf.setzen(p))this.geaendert();else {
        this.dirty=true;this.koordinatenUngueltig=true;this.el('fwSpeichern').disabled=true;
        this.status('Bitte gültige Koordinaten eingeben.');
      }
    };
    this.dialog.querySelectorAll('[data-fw-richtung]').forEach(b=>b.onclick=()=>{
      if(this.busy)return;const d=el('fwSchritt').valueAsNumber;
      if(!Number.isFinite(d)||d<.05||d>20){this.status('Schrittweite zwischen 0,05 und 20 m wählen.');return;}
      const [x,y]={n:[0,d],s:[0,-d],o:[d,0],w:[-d,0]}[b.dataset.fwRichtung];
      this.entwurf.verschieben(x,y);this.geaendert();
    });
    for(const [id,fn] of [['fwEinfuegen',()=>this.entwurf.einfuegen()],['fwEntfernen',()=>this.entwurf.entfernen()],['fwUndo',()=>this.entwurf.zurueck()],['fwOriginal',()=>this.entwurf.ersetzen(this.feld.boundary)]]){
      el(id).onclick=()=>{if(this.busy)return;fn();this.geaendert();};
    }
    el('fwSegment').onchange=()=>{this.dirty=true;el('fwAlt').checked=false;el('fwAnfang').value=0;el('fwEnde').value=0;this.zeichnen();};
    for(const id of ['fwName','fwAbstand','fwAlt'])el(id).oninput=()=>{this.dirty=true;};
    for(const id of ['fwAnfang','fwEnde'])el(id).oninput=()=>{this.dirty=true;this.zeichnen();};
    el('fwSpeichern').onclick=()=>this.speichern();
  }
  status(text){this.el('fwStatus').textContent=text;}
  schliessen(){if(this.busy)return;if(this.dirty&&!confirm('Nicht gespeicherten Entwurf verwerfen?'))return;this.folge++;this.dialog.close();}
  oeffnen(titel,beschreibung){
    this.dirty=false;this.busy=false;this.koordinatenUngueltig=false;this.el('fwTitel').textContent=titel;this.el('fwBeschreibung').textContent=beschreibung;
    this.el('fwGrenze').hidden=this.modus!=='grenze';this.el('fwSpur').hidden=this.modus!=='spur';
    this.el('fwSpeichern').textContent=this.modus==='grenze'?'Arbeitsgrenze speichern':'Als neue Referenz speichern';
    this.status('');if(!this.dialog.open)this.dialog.showModal();this.el('fwSchliessen').focus();
  }
  grenze(feld){
    this.folge++;this.modus='grenze';this.feld=structuredClone(feld);
    this.entwurf=new Grenzentwurf(feld.working_boundary?.length?feld.working_boundary:feld.boundary);
    this.rahmen(feld.boundary);
    this.oeffnen('Arbeitsgrenze · '+feld.name,'Gestrichelt: ursprüngliche Feldgrenze. Durchgezogen: Arbeitsgrenze. Punkt auf der Karte oder in der Liste wählen und mit den Pfeilen verschieben. Die ursprüngliche Grenze bleibt gespeichert.');
    this.zeichnen();
  }
  async referenz(job){
    const folge=++this.folge;const r=await this.api('GET',`/api/jobs/${job.id}/referenz`);
    if(folge!==this.folge)return;
    if(!r.ok){this.modus="spur";this.plan={segmente:[],grenze:[]};this.oeffnen("Arbeitslinie übernehmen","Aufzeichnung konnte nicht geladen werden.");this.rahmen([]);this.zeichnen();this.status(r.detail||"Gerät nicht erreichbar");return;}
    this.modus='spur';this.plan=r.data;this.job=job;this.requestId=vorgangskennung();this.el('fwAnfang').value=0;this.el('fwEnde').value=0;
    this.oeffnen('Arbeitslinie übernehmen · '+this.plan.feldname,'Einen geeigneten gefahrenen Abschnitt prüfen und als zusätzliche Referenz speichern.');
    const select=this.el('fwSegment');select.replaceChildren();
    this.plan.segmente.forEach((s,i)=>select.add(new Option(`Abschnitt ${i+1} · ${s.laenge_m.toFixed(1)} m${s.altaufzeichnung?' · Antennenspur':''}`,String(i))));
    this.el('fwName').value=(job.operation||'Arbeitslinie')+' · '+new Date(job.started_at*1000).toLocaleDateString('de-DE');
    this.el('fwAbstand').value=this.plan.abstand_m||this.profil()?.width_m||3;
    this.el('fwAlt').checked=false;
    this.rahmen([...this.plan.grenze,...this.plan.segmente.flatMap(s=>s.punkte)]);
    this.zeichnen();this.status(this.plan.hinweis||'Abschnitt auswählen und Verlauf auf der Karte prüfen.');
  }
  rahmen(punkte){
    if(!punkte.length)punkte=[[0,0],[100,100]];
    let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
    for(const [x,y] of punkte){xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y);}
    this.mass=Math.min(560/Math.max(1,xmax-xmin),280/Math.max(1,ymax-ymin));
    this.mitte=[(xmin+xmax)/2,(ymin+ymax)/2];
  }
  bild(p){return [320+(p[0]-this.mitte[0])*this.mass,180-(p[1]-this.mitte[1])*this.mass];}
  geaendert(){this.koordinatenUngueltig=false;this.dirty=true;this.status('Entwurf geändert – Vorschau prüfen und speichern.');this.zeichnen();}
  zeichnen(){
    this.dialog.querySelectorAll('button,input,select').forEach(n=>n.disabled=this.busy);
    const svg=this.el('fwKarte');svg.replaceChildren();
    const erstellen=(tag,attrs)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));svg.appendChild(n);return n;};
    const linie=(pts,cls,geschlossen)=>{if(!pts.length)return;erstellen(geschlossen?'polygon':'polyline',{points:pts.map(p=>this.bild(p).join(',')).join(' '),class:cls});};
    const nord=erstellen('text',{x:20,y:25,class:'fw-nord'});nord.textContent='N ↑';
    if(this.modus==='grenze'){
      linie(this.feld.boundary,'fw-original',true);linie(this.entwurf.punkte,'fw-entwurf',true);
      const select=this.el('fwPunkt');select.replaceChildren();
      this.entwurf.punkte.forEach((p,i)=>{
        select.add(new Option(`Punkt ${i+1}`,String(i)));
        const [cx,cy]=this.bild(p);const an=i===this.entwurf.auswahl;
        const kreis=erstellen('circle',{cx,cy,r:an?9:5,class:an?'fw-punkt an':'fw-punkt'});
        kreis.addEventListener('click',()=>{if(this.busy)return;this.entwurf.auswahl=i;this.koordinatenUngueltig=false;this.zeichnen();});
        if(an){const nr=erstellen('text',{x:cx+13,y:cy-12,class:'fw-nord'});nr.textContent=String(i+1);}
      });
      select.value=String(this.entwurf.auswahl);const p=this.entwurf.punkte[this.entwurf.auswahl];
      if(document.activeElement!==this.el('fwOst'))this.el('fwOst').value=p[0].toFixed(2);
      if(document.activeElement!==this.el('fwNord'))this.el('fwNord').value=p[1].toFixed(2);
      this.el('fwUndo').disabled=this.busy||!this.entwurf.verlauf.length;
      this.el('fwEntfernen').disabled=this.busy||this.entwurf.punkte.length<=3;
      this.el('fwEinfuegen').disabled=this.busy||this.entwurf.punkte.length>=2000;
      this.el('fwSpeichern').disabled=this.busy||!this.dirty||this.koordinatenUngueltig;
    }else{
      linie(this.plan.grenze,'fw-original',true);
      const index=Number(this.el('fwSegment').value),aktuell=this.plan.segmente[index];
      this.plan.segmente.forEach((s,i)=>linie(s.punkte,'fw-spur-andere',false));
      const teil=aktuell?kuerzen(aktuell.punkte,this.el('fwAnfang').valueAsNumber,this.el('fwEnde').valueAsNumber):[];
      linie(teil,'fw-spur',false);
      this.el('fwAltHinweis').hidden=!aktuell?.altaufzeichnung;
      this.el('fwSpeichern').disabled=this.busy||teil.length<2;
      if(aktuell&&teil.length<2)this.status('Nach dem Kürzen müssen mindestens 20 m bleiben.');
      else if(aktuell?.altaufzeichnung)this.status('Altaufzeichnung: Antennenposition. Den Verlauf und Geräteversatz vor dem Speichern prüfen.');
    }
  }
  async speichern(){
    if(this.busy)return;let url,body;
    if(this.modus==='grenze'){
      if(this.koordinatenUngueltig||!['fwOst','fwNord'].every(id=>Number.isFinite(this.el(id).valueAsNumber))){
        this.status('Bitte gültige Koordinaten eingeben.');return;
      }
      url=`/api/fields/${this.feld.id}/arbeitsgrenze`;
      const punkte=this.entwurf.punkte;
      body={stand:this.feld.updated_at,punkte:JSON.stringify(punkte)===JSON.stringify(this.feld.boundary)?[]:kopie(punkte)};
    }else{
      if(!this.el('fwName').value.trim()||!this.el('fwAbstand').checkValidity()){this.status('Spurnamen und gültigen Abstand eingeben.');return;}
      url=`/api/jobs/${this.job.id}/referenz`;
      body={request_id:this.requestId,anfang_m:this.el("fwAnfang").valueAsNumber,ende_m:this.el("fwEnde").valueAsNumber,stand:this.plan.stand,segment:Number(this.el('fwSegment').value),name:this.el('fwName').value,abstand_m:this.el('fwAbstand').valueAsNumber,antenne_bestaetigt:this.el('fwAlt').checked};
    }
    this.busy=true;this.zeichnen();this.status('Wird gespeichert …');
    const r=await this.api('POST',url,body);this.busy=false;
    if(r.ok){this.dirty=false;this.dialog.close();this.aktualisieren();}
    else {this.zeichnen();this.status(r.detail||'Nicht gespeichert. Dein Entwurf bleibt geöffnet.');}
  }
}
const modul={Grenzentwurf,Feldwerkzeuge,kuerzen,vorgangskennung};
if(typeof module!=='undefined'&&module.exports)module.exports=modul;else ziel.FeldwerkzeugeModul=modul;
})(typeof window!=='undefined'?window:globalThis);
