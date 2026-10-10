import json
from pathlib import Path
import httpx
import pytest
from conftest import call,open_demo
from rubrika.models import SearchQuery
from rubrika.reporting import export_report
from rubrika.demo import run_demo

@pytest.mark.asyncio
async def test_demo_is_not_a_fake_genealogy_evaluation(auto_service):
    service,_=auto_service
    sid,trace,report=await run_demo(service)
    assert all(t['ok'] for t in trace) and len(trace)==8
    assert report['source_mode']=='synthetic_demo' and report['candidates']==[]
    assert report['coverage']['full_book_search_verified'] is False
    assert report['coverage']['reviewed_regions'][0]['status']=='overview_only'

@pytest.mark.asyncio
async def test_candidate_bound_to_real_image_and_older_query_flagged(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    obs={'kind':'record','image_id':img.data['image']['image_id'],'text':'TEST ONLY, no actual reading',
        'is_candidate':True,'fields':{'name':'synthetic example'},'uncertainties':['Technical fixture only']}
    a=await call(service,'record_finding',research_id=sid,observation=obs,idempotency_key='candidate-1')
    b=await call(service,'record_finding',research_id=sid,observation=obs,idempotency_key='candidate-1')
    assert b.data['deduplicated']
    report=await call(service,'finish_research',research_id=sid,stop_reason='candidate_found')
    c=report.data['candidates'][0]
    assert c['authorship']=='model_interpretation_not_human_verified'
    assert c['evidence']['viewer_url']==img.data['image']['viewer_url']
    assert not c['requires_reassessment']
    await call(service,'revise_research',research_id=sid,query={'raw_query':'Changed task'},expected_revision=1)
    state=await call(service,'get_research_status',research_id=sid)
    assert state.data['candidate_summaries'][0]['requires_reassessment']
    assert state.data['usage']['archive_images']==1

@pytest.mark.asyncio
async def test_ownership_at_tools_not_just_database(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    for name,args in [
        ('get_research_status',{}),('show_saved_evidence',{'image_id':img.data['image']['image_id']}),
        ('open_source',{'source_id':'900001'}),('list_findings',{}),
        ('record_finding',{'idempotency_key':'1','observation':{'kind':'next_step','text':'x'}}),
    ]:
        result=await service.call(name,'bob',{'research_id':sid,**args})
        assert result.is_error and result.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_foreign_image_cannot_be_used_as_evidence(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    other,_=await open_demo(service)
    out=await service.call('record_finding','alice',{'research_id':other,'idempotency_key':'1',
         'observation':{'kind':'record','image_id':img.data['image']['image_id'],'text':'x'}})
    assert out.is_error and out.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_unproven_record_and_hallucinated_book_rejected(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    out=await service.call('record_finding','alice',{'research_id':sid,'idempotency_key':'1','observation':{'kind':'record','text':'x'}})
    assert out.is_error
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'1234'})
    assert out.is_error

@pytest.mark.asyncio
async def test_ambiguous_place_preserved(auto_service):
    service,_=auto_service
    sid=service.store.create('alice',SearchQuery(raw_query='synthetic'))['research_id']
    out=await call(service,'find_place',research_id=sid,term='Demo',kind='parish_registers',archives=['mza'])
    assert out.data['ambiguous'] and len(out.data['places'])==3
    assert not service.store.links(sid,'alice','selected_place')

@pytest.mark.asyncio
async def test_corrupted_evidence_rejected(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    out=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    iid=out.data['image']['image_id']
    Path(service.store.image(sid,'alice',iid)['file']).write_bytes(b'broken')
    out=await service.call('show_saved_evidence','alice',{'research_id':sid,'image_id':iid})
    assert out.is_error and out.data['error']['code']=='INCOMPLETE_IMAGE'

@pytest.mark.asyncio
async def test_manifest_changes_not_silently_mixed(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    pid=data['pages'][0]['page_id']
    p=service.store.entity('page',pid);p['manifest_sha256']='changed'
    service.store.put_entity('page',pid,p)
    out=await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})
    assert out.is_error and out.data['error']['code']=='SOURCE_CHANGED'

@pytest.mark.asyncio
async def test_export_self_contained_and_escapes_text(auto_service,tmp_path):
    service,_=auto_service
    sid,_=await open_demo(service)
    await call(service,'record_finding',research_id=sid,idempotency_key='1',observation={'kind':'next_step','text':'<script>alert(1)</script>'})
    path=tmp_path/'report.html'
    export_report(service,sid,'alice',path,'synthetic_demo')
    text=path.read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in text
    assert 'synthetic_demo' in text
    assert path.with_suffix('.json').exists()

@pytest.mark.asyncio
async def test_second_catalog_page_is_not_dropped(cfg,store):
    import httpx
    from rubrika.demo import DemoArchive
    from rubrika.network import SafeSource
    from rubrika.service import ResearchService
    archive=DemoArchive()
    def handler(req):
        r=archive.handle(req)
        if req.url.path.endswith('/matrika/hledani'):
            if req.url.params.get('page')=='2':
                html=r.text.replace('900001','900011').replace('900002','900012').replace('DEMO-1','DEMO-11').replace('DEMO-2','DEMO-12')
            else:
                html=r.text.replace('</html>','<div class="pagination"><a href="?typ=obec&obec_id=900001&page=2">2</a></div></html>')
            return httpx.Response(200,text=html,headers={'content-type':'text/html'})
        return r
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),source_mode='synthetic_demo')
    service=ResearchService(cfg,store,source)
    try:
        sid=store.create('alice',SearchQuery(raw_query='Synthetic'))['research_id']
        p=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='parish_registers',archives=['mza'])
        b=await call(service,'list_sources',research_id=sid,place_ids=[p.data['places'][0]['source_id']],kind='parish_registers')
        assert b.data['total']==4 and b.data['listing_complete']
        state=await call(service,'get_research_status',research_id=sid)
        assert state.data['coverage']['catalog_listings'][0]['book_count']==4
    finally:await service.close()

@pytest.mark.asyncio
async def test_checkpoint_pauses_images_until_the_user_agrees(auto_service):
    service,_=auto_service
    service.cfg.checkpoint_images=1
    sid,data=await open_demo(service)
    pid=data['pages'][0]['page_id']
    img=await call(service,'view_scan',research_id=sid,page_id=pid)
    for name,args in (('view_scan',{'page_id':pid}),
                      ('zoom_scan',{'image_id':img.data['image']['image_id'],'bbox_px':{'x':0,'y':0,'width':100,'height':100}})):
        out=await service.call(name,'alice',{'research_id':sid,**args})
        assert out.is_error and out.data['error']['code']=='CHECKPOINT'
    # Saved evidence, findings and status cost nothing and keep working at the checkpoint.
    again=await service.call('show_saved_evidence','alice',{'research_id':sid,'image_id':img.data['image']['image_id']})
    assert not again.is_error and again.image_b64
    await call(service,'record_finding',research_id=sid,idempotency_key='checkpoint',observation={'kind':'next_step','text':'Pending next page'})
    state=await call(service,'get_research_status',research_id=sid)
    assert state.data['usage']['archive_images']==1 and state.data['usage']['images_until_checkpoint']==0
    assert (await call(service,'continue_research',research_id=sid)).data['next_checkpoint_after']==1
    assert not (await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})).is_error
    assert (await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})).data['error']['code']=='CHECKPOINT'


@pytest.mark.asyncio
async def test_family_tree_cites_only_the_owners_saved_findings(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    saved=await call(service,'record_finding',research_id=sid,idempotency_key='tree-1',
        observation={'kind':'record','image_id':img.data['image']['image_id'],'text':'TEST ONLY, no actual reading'})
    ref={'research_id':sid,'observation_id':saved.data['observation_id']}
    tree={'title':'Syntetická linie','root_person':'a','persons':[
        {'id':'a','given_name':'Test','surname':'Synthetic','events':[
            {'type':'birth','date':{'year':1841},'confidence':'documented','sources':[ref]}]}]}
    out=await call(service,'export_family_tree',tree=tree)
    ged=out.data['gedcom']
    assert img.data['image']['viewer_url'] in ged and 'SYNTETICKÁ TESTOVACÍ DATA' in ged
    assert '1 TITL Acta Publica, Testov, kniha DEMO-1' in ged
    assert out.data['summary']['citations']==1 and out.data['mermaid'].startswith('flowchart')
    foreign=await service.call('export_family_tree','bob',{'tree':tree})
    assert foreign.is_error and foreign.data['error']['code']=='NOT_FOUND'
    broken=await service.call('export_family_tree','alice',{'tree':{**tree,'root_person':'x'}})
    assert broken.is_error and broken.data['error']['code']=='INVALID_ARGUMENT'
    variant=await service.call('export_family_tree','alice',{'tree':tree,'variant':'everything'})
    assert variant.is_error and variant.data['error']['code']=='INVALID_ARGUMENT'


def _with_robots(archive,text):
    inner=archive.transport
    async def handle(request):
        if request.url.path=='/robots.txt':
            return httpx.Response(200,text=text,headers={'content-type':'text/plain'})
        return await inner.handle_async_request(request)
    return httpx.MockTransport(handle)

@pytest.mark.asyncio
@pytest.mark.parametrize('robots,expected',[('User-agent: *\nDisallow: /iipsrv/\n',True),('User-agent: *\nAllow: /\n',False)])
async def test_robots_disallowed_flag_travels_with_the_evidence(cfg,store,monkeypatch,robots,expected):
    import dataclasses
    from rubrika import archives as A
    from rubrika.network import SafeSource
    from rubrika.service import ResearchService
    from rubrika.demo import DemoArchive
    monkeypatch.setitem(A.ARCHIVES,'mza',dataclasses.replace(A.ARCHIVES['mza'],robots_mode='user_triggered'))
    archive=DemoArchive()
    service=ResearchService(cfg,store,SafeSource(cfg,store,transport=_with_robots(archive,robots),source_mode='synthetic_demo'))
    try:
        sid,data=await open_demo(service)
        img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
        assert img.data['image']['robots_disallowed'] is expected
        iid=img.data['image']['image_id']
        saved=await call(service,'show_saved_evidence',research_id=sid,image_id=iid)
        assert saved.data['image']['robots_disallowed'] is expected
        obs={'kind':'record','image_id':iid,'text':'TEST ONLY','fields':{'name':'synthetic example'}}
        await call(service,'record_finding',research_id=sid,observation=obs,idempotency_key='r1')
        found=await call(service,'list_findings',research_id=sid)
        assert found.data['observations'][0]['evidence']['robots_disallowed'] is expected
    finally:await service.close()

@pytest.mark.asyncio
async def test_book_daily_image_cap_holds_across_searches(cfg,store):
    from rubrika.network import SafeSource
    from rubrika.service import ResearchService
    from rubrika.demo import DemoArchive
    cfg.book_daily_images=2
    archive=DemoArchive()
    service=ResearchService(cfg,store,SafeSource(cfg,store,transport=archive.transport,source_mode='synthetic_demo'))
    try:
        sid,data=await open_demo(service)
        pid=data['pages'][0]['page_id']
        img=await call(service,'view_scan',research_id=sid,page_id=pid)
        await call(service,'zoom_scan',research_id=sid,image_id=img.data['image']['image_id'],bbox_px={'x':0,'y':0,'width':100,'height':100})
        out=await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})
        assert out.is_error and out.data['error']['code']=='BUDGET_EXCEEDED'
        sid2,data2=await open_demo(service)
        out=await service.call('view_scan','alice',{'research_id':sid2,'page_id':data2['pages'][0]['page_id']})
        assert out.is_error and out.data['error']['code']=='BUDGET_EXCEEDED'
    finally:await service.close()


@pytest.mark.asyncio
async def test_list_archives_is_local_routes_districts_and_honours_switches(auto_service):
    service,archive=auto_service
    before=len(archive.requests)
    out=await call(service,'list_archives',district='Havlíčkův Brod')
    assert len(archive.requests)==before
    rows={a['code']:a for a in out.data['archives']}
    assert list(rows)[:2]==['mza','vca'] and rows['zao']['link_only'] and 'botům' in rows['zao']['reason']
    assert rows['mza']['available_kinds']==['parish_registers','land_records','census']
    assert out.data['district']=={'query':'Havlíčkův Brod','matched':'Havlíčkův Brod','archives':['vca','mza'],'cadastral_maps':['cuzk']}
    service.cfg=service.cfg.model_copy(update={'census_enabled':False})
    out=await call(service,'list_archives')
    assert {a['code']:a for a in out.data['archives']}['mza']['available_kinds']==['parish_registers','land_records']


@pytest.mark.asyncio
async def test_kind_mismatch_and_parish_years_are_invalid(auto_service):
    service,_=auto_service
    sid,_=await open_demo(service)
    aron=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='land_records',archives=['mza'])
    out=await service.call('list_sources','alice',dict(research_id=sid,place_ids=[aron.data['places'][0]['source_id']],kind='parish_registers'))
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'
    acta=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='parish_registers',archives=['mza'])
    out=await service.call('list_sources','alice',dict(research_id=sid,place_ids=[acta.data['places'][0]['source_id']],
                                                         kind='parish_registers',year_from=1800))
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_find_place_by_district_reports_link_only_archives(auto_service):
    service,archive=auto_service
    sid,_=await open_demo(service)
    before=len(archive.requests)
    out=await call(service,'find_place',research_id=sid,term='Opava',kind='parish_registers',district='Opava')
    assert out.data['places']==[] and out.data['searched']==[] and len(archive.requests)==before
    assert out.data['not_searched'][0]['archive']=='zao' and 'botům' in out.data['not_searched'][0]['reason']

@pytest.mark.asyncio
async def test_disabled_kind_is_not_searched_but_reported(auto_service):
    service,archive=auto_service
    service.cfg=service.cfg.model_copy(update={'census_enabled':False})
    sid,_=await open_demo(service)
    before=len(archive.requests)
    out=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='census',archives=['mza'])
    assert out.data['searched']==[] and out.data['not_searched'][0]['archive']=='mza' and len(archive.requests)==before
    auto=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='census')
    assert 'mza' not in auto.data['searched']      # VČA also serves census; only the MZA switch is off

@pytest.mark.asyncio
async def test_other_owner_cannot_use_generic_tools(auto_service):
    service,_=auto_service
    sid,opened=await open_demo(service)
    for name,args in (('find_place',dict(term='Demo Lhota',kind='parish_registers',archives=['mza'])),
                      ('open_source',dict(source_id=opened['unit']['source_id']))):
        out=await service.call(name,'bob',dict(research_id=sid,**args))
        assert out.is_error and out.data['error']['code'] in ('NOT_FOUND','SEARCH_EXPIRED')


@pytest.mark.asyncio
async def test_failed_render_does_not_consume_the_book_cap(cfg,store):
    from rubrika.network import SafeSource
    from rubrika.service import ResearchService
    from rubrika.demo import DemoArchive
    cfg.book_daily_images=1
    archive=DemoArchive()
    service=ResearchService(cfg,store,SafeSource(cfg,store,transport=archive.transport,source_mode='synthetic_demo'))
    try:
        sid,data=await open_demo(service)
        pid=data['pages'][0]['page_id']
        big=cfg.payload_max_chars
        cfg.payload_max_chars=2100                       # the image cannot fit -> PAYLOAD_TOO_LARGE
        out=await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})
        assert out.is_error and out.data['error']['code']=='PAYLOAD_TOO_LARGE'
        cfg.payload_max_chars=big
        await call(service,'view_scan',research_id=sid,page_id=pid)          # cap of 1 still available
        out=await service.call('view_scan','alice',{'research_id':sid,'page_id':pid})
        assert out.is_error and out.data['error']['code']=='BUDGET_EXCEEDED'
    finally:await service.close()
