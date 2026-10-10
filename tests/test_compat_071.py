"""A search written by 0.7.1 (no `archive`, no `robots_disallowed` in stored JSON) must keep working with the new code."""
import hashlib
import json
import sqlite3
import pytest
from conftest import call
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store, dump

NEW_FIELDS=('archive','robots_disallowed')
TABLES=(('entities','payload'),('search_entities','payload'),('images','payload'),('observations','payload'))


def strip(x):
    # `archive` is new only on pages and image refs; a census set's own `archive` (the holding SOkA) is old data
    if isinstance(x,dict):
        return {k:strip(v) for k,v in x.items() if k!='robots_disallowed' and not (k=='archive' and ('page_id' in x or 'image_id' in x))}
    if isinstance(x,list): return [strip(v) for v in x]
    return x


def downgrade(path):
    """Strip the 0.8 fields from stored JSON so the DB looks as written by 0.7.1.
    A finding's body_hash is the hash of its stored body, so it is recomputed from the stripped body: 0.7.1 hashed
    a body that never had these fields and carried skill_version 0.7.1. Image files and their sha256 are untouched."""
    db=sqlite3.connect(path)
    for table,col in TABLES:
        for rowid,value in db.execute(f'SELECT rowid,{col} FROM {table}').fetchall():
            if not value: continue
            body=strip(json.loads(value))
            if table=='observations': body['skill_version']='0.7.1'   # the default that 0.7.1 stored and hashed
            db.execute(f'UPDATE {table} SET {col}=? WHERE rowid=?',(dump(body),rowid))
            if table=='observations':
                db.execute('UPDATE observations SET body_hash=? WHERE rowid=?',(hashlib.sha256(dump(body).encode()).hexdigest(),rowid))
    db.commit(); db.close()


def stored_json(path):
    db=sqlite3.connect(path)
    try: return [v for table,col in TABLES for (v,) in db.execute(f'SELECT {col} FROM {table}')]
    finally: db.close()


async def write_071_search(service):
    """Registers, land books and census through the generic tools, each with view, zoom and a recorded finding."""
    sid=(await call(service,'start_research',query={'raw_query':'Synthetic compat','place_hint':'Demo Lhota','event_type':'birth',
        'year_from':1840,'year_to':1845,'person':{'house_number':'15'}})).data['research_id']
    sources={}
    for kind,place_ids,pick in (('parish_registers',None,None),('land_records',None,None),('census',['census:obec:Demo Lhota'],'census:9001')):
        found=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind=kind,archives=['mza'])
        ids=place_ids or ([found.data['places'][0]['source_id']] if kind=='parish_registers' else [p['source_id'] for p in found.data['places']])
        listed=await call(service,'list_sources',research_id=sid,place_ids=ids,kind=kind)
        source_id=pick or listed.data['sources'][0]['source_id']
        opened=await call(service,'open_source',research_id=sid,source_id=source_id)
        view=await call(service,'view_scan',research_id=sid,page_id=opened.data['pages'][0]['page_id'])
        ref=view.data['image']
        zoom=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                        bbox_px={'x':0,'y':0,'width':ref['width']/2,'height':ref['height']/2})
        observation={'kind':'record','image_id':zoom.data['image']['image_id'],'text':'SYNTHETIC, no reading'}
        finding=await call(service,'record_finding',research_id=sid,observation=observation,idempotency_key='k-'+source_id)
        sources[source_id]=dict(kind=kind,page_id=opened.data['pages'][0]['page_id'],image_id=zoom.data['image']['image_id'],
                                observation=observation,observation_id=finding.data['observation_id'],citation=opened.data.get('citation'))
    return sid,sources


@pytest.mark.asyncio
async def test_search_written_by_071_keeps_working(auto_service,cfg):
    service,archive=auto_service
    sid,sources=await write_071_search(service)
    assert len(sources)==3
    await service.close(); service.store.close()
    assert any('"archive":"mza"' in v for v in stored_json(cfg.data_dir/'research.sqlite'))
    downgrade(cfg.data_dir/'research.sqlite')
    assert not any('"archive":"mza"' in v for v in stored_json(cfg.data_dir/'research.sqlite'))
    assert not any('"robots_disallowed"' in v for v in stored_json(cfg.data_dir/'research.sqlite'))

    store=Store(cfg)
    new=ResearchService(cfg,store,SafeSource(cfg,store,transport=archive.transport,source_mode='synthetic_demo'))
    try:
        for source_id,s in sources.items():
            opened=await call(new,'open_source',research_id=sid,source_id=source_id)
            assert opened.data['archive']=='mza' and opened.data['kind']==s['kind']
            assert {p['archive'] for p in opened.data['pages']}=={'mza'}
            view=await call(new,'view_scan',research_id=sid,page_id=s['page_id'])
            assert view.data['image']['archive']=='mza' and view.data['image']['robots_disallowed'] is False
            ref=view.data['image']
            zoom=await call(new,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                            bbox_px={'x':0,'y':0,'width':ref['width']/2,'height':ref['height']/2})
            assert zoom.data['image']['image_id']
            saved=await call(new,'show_saved_evidence',research_id=sid,image_id=s['image_id'])
            assert saved.data['cached_evidence'] and saved.image_b64
            again=await call(new,'record_finding',research_id=sid,observation=s['observation'],idempotency_key='k-'+source_id)
            assert again.data['deduplicated'] is True and again.data['observation_id']==s['observation_id']
        status=await call(new,'get_research_status',research_id=sid)
        assert {b['book_id']:b['source'] for b in status.data['coverage']['opened_books']}.keys()==sources.keys()
        assert all(b['source'] for b in status.data['coverage']['opened_books'])
        report=await call(new,'finish_research',research_id=sid,stop_reason='user_stopped')
        assert report.data['candidate_count']==0
        tree={'title':'Compat','root_person':'a','persons':[{'id':'a','given_name':'Test','events':[
            {'type':'birth','confidence':'documented','sources':[{'research_id':sid,'observation_id':s['observation_id']}]}
            for s in sources.values()]}]}
        exported=await call(new,'export_family_tree',tree=tree,variant='documented_only')
        assert '1 AUTH Moravský zemský archiv v Brně' in exported.data['gedcom']
        for name,args in (('open_source',dict(source_id=next(iter(sources)))),('view_scan',dict(page_id=next(iter(sources.values()))['page_id'])),
                          ('show_saved_evidence',dict(image_id=next(iter(sources.values()))['image_id'])),('get_research_status',{}),
                          ('finish_research',dict(stop_reason='user_stopped')),('list_findings',{})):
            out=await new.call(name,'bob',dict(research_id=sid,**args))
            assert out.is_error and out.data['error']['code'] in ('NOT_FOUND','SEARCH_EXPIRED'),name
    finally:
        await new.close(); store.close()
