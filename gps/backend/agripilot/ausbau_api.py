"""Zusammengeführte Bedienwege der Erweiterungen 1–16."""
from __future__ import annotations
from fastapi import Body
from . import arbeitskomfort

def einbauen(api,app,ok,guard):
    e,s=app.engine,app.store
    @api.get('/api/ausbau/kontext')
    async def kontext():
        return ok({'felder':s.list_fields(),'traktoren':e.list_traktoren(),'profile':e.list_profiles(),'jobs':s.list_jobs(limit=100000),'vorlieben':arbeitskomfort.vorlieben(s)})
    @api.get('/api/komfort')
    async def komfort():return ok(arbeitskomfort.vorlieben(s))
    @api.post('/api/komfort')
    async def komfort_speichern(payload:dict=Body(...)):return guard(arbeitskomfort.speichern,app,payload)
    @api.post('/api/arbeitsstart')
    async def arbeitsstart(payload:dict=Body(...)):return guard(arbeitskomfort.starten,app,payload)
    @api.post('/api/aenderungsvorschau')
    async def aenderungsvorschau(payload:dict=Body(...)):return guard(arbeitskomfort.vorschau,app,payload.get('ziel'),payload.get('werte'))

    import asyncio
    from fastapi import HTTPException
    from fastapi.responses import Response
    from . import arbeitstag,arbeitsbericht
    from .ausbau_diagnose import DiagnoseService
    app.diagnose16=DiagnoseService(app)
    async def bericht(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except (ValueError,KeyError) as exc:raise HTTPException(400,str(exc)) from exc
        except RuntimeError as exc:raise HTTPException(503,str(exc)) from exc
    @api.post('/api/arbeitstag/pause')
    async def pause():return guard(e.pause_job)
    @api.get('/api/papierkorb')
    async def papierkorb():return guard(arbeitstag.papierkorb,s,app.config.network.device_id)
    @api.post('/api/papierkorb/{job_id}/wiederherstellen')
    async def wiederherstellen(job_id:str):
        def herstellen():
            j=arbeitstag.wiederherstellen(e,job_id)
            if e.field and e.field['id']==j['field_id']:
                from . import sync
                from .coverage import CoverageMap
                karte=sync.merge_field_coverage(s,j['field_id'],max_age_s=86400)
                blob=s.get_job_coverage(job_id)
                if blob:karte.merge(CoverageMap.unpack(blob))
                e.coverage=karte
            return j
        return guard(herstellen)
    @api.get('/api/jobs/{job_id}/restflaeche')
    async def restflaeche(job_id:str):return ok(await bericht(arbeitsbericht.restflaeche,s,job_id))
    @api.get('/api/jobs/{job_id}/kosten')
    async def kosten(job_id:str):return ok(await bericht(arbeitsbericht.kosten_lesen,s,job_id))
    @api.post('/api/jobs/{job_id}/kosten')
    async def kosten_speichern(job_id:str,payload:dict=Body(...)):
        def speichern():
            arbeitstag.im_stand(e)
            j=s.get_job(job_id)
            if not j or j.get('deleted') or j['device_id']!=app.config.network.device_id:raise ValueError('Kosten für diesen Auftrag am ursprünglichen Gerät eintragen')
            return arbeitsbericht.kosten_speichern(s,job_id,payload)
        return guard(speichern)
    @api.get('/api/fields/{field_id}/bericht.pdf')
    async def pdf(field_id:str,jahr:int|None=None,job_id:str|None=None):
        body=await bericht(arbeitsbericht.bericht_pdf,s,field_id,jahr,job_id)
        return Response(body,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="AgriPilot-Feldbericht.pdf"'})
    @api.get('/api/ausbau/diagnose')
    async def diagnose():return guard(app.diagnose16.uebersicht)
    @api.post('/api/ausbau/fahrserie')
    async def fahrserie(payload:dict=Body(...)):
        if payload.get('aktion')=='abbrechen':return guard(app.diagnose16.fahrserie_abbrechen)
        return guard(app.diagnose16.fahrserie_start,payload.get('etappe'),payload.get('aktion'),payload.get('bestaetigt') is True)
    @api.post('/api/ausbau/abnahme')
    async def abnahme(payload:dict=Body(...)):
        def bestaetigen():
            arbeitstag.im_stand(e)
            if e.job:raise ValueError('Erst die laufende Arbeit pausieren oder abschließen')
            return app.diagnose16.abnahme_bestaetigen(payload.get('bestaetigt') is True)
        return guard(bestaetigen)
    @api.post('/api/ausbau/plaene/start')
    async def plaene_start():return guard(app.diagnose16.planpruefung_start)
    @api.get('/api/ausbau/plaene')
    async def plaene():return guard(app.diagnose16.planpruefung_status)

    @api.post('/api/ausbau/plaene/abbrechen')
    async def plaene_abbrechen():return guard(app.diagnose16.planpruefung_abbrechen)
