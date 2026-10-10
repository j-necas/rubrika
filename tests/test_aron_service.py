import pytest
from conftest import call, open_demo
from rubrika import demo_aron as D


async def aron_flow(service,owner='alice',sid=None,years=(1740,1760)):
    if sid is None:
        out=await call(service,'start_research',owner,query={'raw_query':'SYNTHETIC ARON test','place_hint':'Demo Lhota',
                                                             'year_from':years[0],'year_to':years[1]})
        sid=out.data['research_id']
    places=await call(service,'find_place',owner,research_id=sid,term='Demo Lhota',kind='land_records',archives=['mza'])
    units=await call(service,'list_sources',owner,research_id=sid,place_ids=[p['source_id'] for p in places.data['places']],kind='land_records')
    return sid,places.data,units.data


@pytest.mark.asyncio
async def test_places_are_namespaced_and_duplicates_flagged(auto_service):
    service,_=auto_service
    _,places,_=await aron_flow(service)
    assert {p['source_id'] for p in places['places']}=={'aron:'+D.PLACE_LHOTA,'aron:'+D.PLACE_LHOTA_DUP}
    assert places['kind']=='land_records' and places['searched']==['mza'] and {p['archive'] for p in places['places']}=={'mza'} and places['ambiguous'] and places['warnings']

@pytest.mark.asyncio
async def test_units_ranked_by_relevance_then_category_then_date(auto_service):
    service,archive=auto_service
    _,_,units=await aron_flow(service)
    assert [u['source_id'] for u in units['sources']]==['aron:'+x for x in
        (D.UNIT_LAND,D.UNIT_CADASTRE,D.UNIT_LINK_ONLY,D.UNIT_UNDATED,D.UNIT_LATE)]
    # The listing only has the textual dating, so the estimated 1760–1770 unit is 'known' here
    # (the detail later marks it uncertain); it overlaps 1740–1760.
    assert units['counts']=={'candidate':3,'needs_review':1,'excluded':1}
    assert units['listing_complete'] is True and units['total']==5
    land=units['sources'][0]
    assert land['fund']['mark']=='C 17' and land['relevance']['category']=='land_book'
    body=archive.posts[-1]
    assert body['filters'][0]=={'field':'type','operation':'EQ','value':'ARCH_DESC'}
    assert not any(f.get('field')=='UNIT~DATE' for f in body['filters'])

@pytest.mark.asyncio
async def test_year_filter_is_explicit_and_warned(auto_service):
    service,archive=auto_service
    sid,places,_=await aron_flow(service)
    out=await call(service,'list_sources',kind='land_records',research_id=sid,place_ids=[places['places'][0]['source_id']],year_from=1700)
    rng=[f for f in archive.posts[-1]['filters'] if f.get('field')=='UNIT~DATE'][0]
    assert rng['gte']=='1700-01-01T00:00:00.000Z' and rng['lte']=='2200-12-31T23:59:59.999Z'
    assert any('bez datace' in w for w in out.data['warnings'])

@pytest.mark.asyncio
async def test_list_sources_paginates(auto_service):
    service,_=auto_service
    sid,places,_=await aron_flow(service)
    ids=[p['source_id'] for p in places['places']]
    first=await call(service,'list_sources',kind='land_records',research_id=sid,place_ids=ids,limit=2)
    second=await call(service,'list_sources',kind='land_records',research_id=sid,place_ids=ids,cursor=first.data['next_cursor'],limit=2)
    assert first.data['next_cursor']==2 and len(second.data['sources'])==2

@pytest.mark.asyncio
@pytest.mark.parametrize('args',[
    {'place_ids':[]},{'place_ids':['123']},{'place_ids':'aron:x'},
    {'place_ids':['aron:'+D.PLACE_LHOTA]*6},
    {'place_ids':['aron:'+D.PLACE_LHOTA],'fund_marks':['C 17; DROP']},
    {'place_ids':['aron:'+D.PLACE_LHOTA],'fund_marks':['C 17']*11},
    {'place_ids':['aron:'+D.PLACE_LHOTA],'year_from':1800,'year_to':1700},
    {'place_ids':['aron:'+D.PLACE_LHOTA],'year_from':99},
    {'place_ids':['aron:'+D.PLACE_LHOTA],'digitized_only':'yes'},
])
async def test_list_sources_rejects_bad_arguments(auto_service,args):
    service,_=auto_service
    sid,_,_=await aron_flow(service)
    out=await service.call('list_sources','alice',{'research_id':sid,'kind':'land_records',**args})
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_place_must_come_from_this_search(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC'})
    res=await service.call('list_sources','alice',{'research_id':out.data['research_id'],'kind':'land_records','place_ids':['aron:'+D.PLACE_LHOTA]})
    assert res.is_error and res.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_open_unit_and_reopen_charges_books_once(auto_service):
    service,_=auto_service
    sid,_,_=await aron_flow(service)
    a=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_LAND)
    b=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_LAND,cursor=2,limit=1)
    assert a.data['archive']=='mza' and a.data['kind']=='land_records' and a.data['total']==3 and a.data['unit']['places']==['Demo Lhota']
    assert b.data['pages'][0]['scan_number']==3 and b.data['next_cursor'] is None
    state=await call(service,'get_research_status',research_id=sid)
    assert len(state.data['coverage']['opened_books'])==1

@pytest.mark.asyncio
async def test_combined_search_links_land_book_to_register_evidence(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    reg_img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    reg_obs=await call(service,'record_finding',research_id=sid,idempotency_key='reg-1',
        observation={'kind':'record','image_id':reg_img.data['image']['image_id'],'text':'SYNTHETIC, no reading'})
    await aron_flow(service,sid=sid)
    unit=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_LAND)
    img=await call(service,'view_scan',research_id=sid,page_id=unit.data['pages'][0]['page_id'])
    ref=img.data['image']
    assert ref['source']=='aron' and ref['citation']==unit.data['citation'] and ref['image_key'].endswith('.jp2')
    crop=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                    bbox_px={'x':0,'y':0,'width':ref['width']/2,'height':ref['height']/2})
    assert crop.data['image']['source']=='aron' and crop.data['image']['citation']==ref['citation']
    await call(service,'record_finding',research_id=sid,idempotency_key='land-1',observation={
        'kind':'record','image_id':crop.data['image']['image_id'],'text':'SYNTHETIC, no reading','is_candidate':True,
        'links':[{'observation_id':reg_obs.data['observation_id'],'relation':'same_person'}]})
    report=await call(service,'finish_research',research_id=sid,stop_reason='candidate_found')
    cand=report.data['candidates'][0]
    assert cand['book_id']=='aron:'+D.UNIT_LAND and cand['evidence']['citation']==ref['citation']
    assert cand['links']==[{'observation_id':reg_obs.data['observation_id'],'relation':'same_person'}]
    opened={b['book_id']:b['source'] for b in report.data['coverage']['opened_books']}
    assert opened=={data['unit']['source_id']:'acta_publica','aron:'+D.UNIT_LAND:'aron'}
    not_opened={b['book_id']:b for b in report.data['coverage']['catalog_books_not_opened']}
    assert not_opened['aron:'+D.UNIT_LATE]['source']=='aron' and not_opened['aron:'+D.UNIT_LATE]['relevance']['status']=='excluded'
    assert any(b['source']=='acta_publica' for b in not_opened.values())
    assert len(report.data['coverage']['opened_books'])==2

@pytest.mark.asyncio
async def test_links_must_point_into_the_same_search(auto_service):
    service,_=auto_service
    sid,_=await open_demo(service)
    other,_=await open_demo(service)
    note=await call(service,'record_finding',research_id=other,idempotency_key='n',observation={'kind':'next_step','text':'x'})
    out=await service.call('record_finding','alice',{'research_id':sid,'idempotency_key':'l','observation':{
        'kind':'next_step','text':'y','links':[{'observation_id':note.data['observation_id'],'relation':'related'}]}})
    assert out.is_error and out.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_unsupported_unit_is_citable_but_not_charged(auto_service):
    service,_=auto_service
    sid,_,_=await aron_flow(service)
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'aron:'+D.UNIT_LINK_ONLY})
    assert out.is_error and out.data['error']['code']=='UNSUPPORTED_SOURCE'
    assert out.data['error']['details']['citation']
    state=await call(service,'get_research_status',research_id=sid)
    assert len(state.data['coverage']['opened_books'])==0

@pytest.mark.asyncio
async def test_source_ids_cannot_cross_kinds(auto_service):
    service,_=auto_service
    sid,places,_=await aron_flow(service)
    aron_place=places['places'][0]['source_id']
    for kind,ids in [('parish_registers',[aron_place]),('land_records',['900001']),('census',[aron_place]),
                     ('land_records',['census:obec:Demo Lhota'])]:
        out=await service.call('list_sources','alice',{'research_id':sid,'kind':kind,'place_ids':ids})
        assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT',(kind,ids)

@pytest.mark.asyncio
async def test_unit_from_query_link_but_not_arbitrary(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC',
        'book_url':f'https://www.mza.cz/aron/apu/{D.UNIT_CADASTRE.upper()}/'})
    sid=out.data['research_id']
    ok=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_CADASTRE)
    assert ok.data['unit']['category']=='cadastre' and ok.data['unit']['places']==[]
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'aron:'+D.UNIT_LAND})
    assert out.is_error and out.data['error']['code']=='NOT_FOUND'
    bad=await service.call('start_research','alice',{'query':{'raw_query':'x','book_url':'https://evil.example/aron/apu/'+D.UNIT_LAND}})
    assert bad.is_error and bad.data['error']['code']=='INVALID_SOURCE'
    acta=await call(service,'start_research',query={'raw_query':'x','book_url':'https://www.mza.cz/actapublica/matrika/detail/900001'})
    assert acta.data['research_id']

@pytest.mark.asyncio
async def test_aron_disabled_keeps_registers_working(auto_service):
    service,_=auto_service
    service.cfg.aron_enabled=False
    sid,data=await open_demo(service)
    assert data['pages']
    out=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='land_records',archives=['mza'])
    assert out.data['places']==[] and out.data['searched']==[]
    assert out.data['not_searched'][0]['archive']=='mza' and 'vypnutý' in out.data['not_searched'][0]['reason']

@pytest.mark.asyncio
async def test_aron_tools_respect_ownership(auto_service):
    service,_=auto_service
    sid,_,_=await aron_flow(service)
    for name,args in [('find_place',{'term':'Demo Lhota','kind':'land_records','archives':['mza']}),
                      ('list_sources',{'place_ids':['aron:'+D.PLACE_LHOTA],'kind':'land_records'}),
                      ('open_source',{'source_id':'aron:'+D.UNIT_LAND})]:
        out=await service.call(name,'bob',{'research_id':sid,**args})
        assert out.is_error and out.data['error']['code']=='NOT_FOUND',name

@pytest.mark.asyncio
async def test_disabling_aron_blocks_page_access_but_not_stored_evidence(auto_service):
    # ARON scans are served by mza_images (iipsrv), which the switch did not gate before.
    service,archive=auto_service
    sid,_,_=await aron_flow(service)
    unit=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_LAND)
    page_id=unit.data['pages'][0]['page_id']
    img=await call(service,'view_scan',research_id=sid,page_id=page_id)
    service.cfg.aron_enabled=False
    before=len(archive.requests)
    out=await service.call('view_scan','alice',{'research_id':sid,'page_id':page_id})
    assert out.is_error and out.data['error']['code']=='LIVE_DISABLED'
    assert len(archive.requests)==before
    out=await service.call('zoom_scan','alice',{'research_id':sid,'image_id':img.data['image']['image_id'],
        'bbox_px':{'x':0,'y':0,'width':1,'height':1}})
    assert out.is_error and out.data['error']['code']=='LIVE_DISABLED'
    assert len(archive.requests)==before
    ev=await call(service,'show_saved_evidence',research_id=sid,image_id=img.data['image']['image_id'])
    assert ev.data['ok']

@pytest.mark.asyncio
async def test_a_research_opens_any_number_of_units(auto_service):
    # A research is a long project (a line across generations): it is not rationed by books.
    service,_=auto_service
    sid,_,_=await aron_flow(service)
    for unit in (D.UNIT_LAND,D.UNIT_CADASTRE):
        await call(service,'open_source',research_id=sid,source_id='aron:'+unit)
    again=await call(service,'open_source',research_id=sid,source_id='aron:'+D.UNIT_LAND)
    assert again.data['unit']['source_id']=='aron:'+D.UNIT_LAND
    state=await call(service,'get_research_status',research_id=sid)
    assert len(state.data['coverage']['opened_books'])==2

@pytest.mark.asyncio
async def test_old_catalog_listing_rows_without_source_default_to_acta_publica(auto_service):
    # Backward compatibility for rows saved before "source" existed.
    service,_=auto_service
    sid,data=await open_demo(service)
    service.store.link(sid,'alice','catalog_listing','legacy',{'listing_complete':True,'warnings':[],'book_count':0})
    state=await call(service,'get_research_status',research_id=sid)
    legacy=next(c for c in state.data['coverage']['catalog_listings'] if c['id']=='legacy')
    assert legacy['source']=='acta_publica'


async def d10_flow(service, fund_marks=None):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC D 10 test', 'place_hint': 'Demovice', 'year_from': 1830, 'year_to': 1840})
    sid = out.data['research_id']
    places = await call(service, 'find_place', research_id=sid, term='Demovice', kind='land_records', archives=['mza'])
    units = await call(service, 'list_sources', research_id=sid, place_ids=[places.data['places'][0]['source_id']], kind='land_records',
                       **({'fund_marks': fund_marks} if fund_marks else {}))
    return sid, units.data


@pytest.mark.asyncio
async def test_stable_cadastre_parcel_protocols_are_listed_titled_by_their_katastr(auto_service):
    service, _ = auto_service
    _, units = await d10_flow(service, ['D 10'])
    (row,) = units['sources']
    assert row['source_id'] == 'aron:' + D.UNIT_D10 and row['fund']['mark'] == 'D 10' and row['category'] == 'cadastre'
    assert row['title'] == 'Duplikát – Demovice (Demowitz), obec: Testov, okres: TEST SYNTHETIC'      # not just "Duplikát"
    assert row['relevance']['status'] == 'candidate' and row['relevance']['category'] == 'cadastre' and row['storage'] == '1250 fascikl'
    assert 'D 10' in units['note'] and 'parcelní protokoly' in units['note'] and 'německého názvu' in units['note']
    assert 'cadastral_maps' in units['note']


@pytest.mark.asyncio
async def test_other_funds_get_no_stable_cadastre_note(auto_service):
    service, _ = auto_service
    _, _, units = await aron_flow(service)
    assert 'D 10' not in units['note']


@pytest.mark.asyncio
async def test_stable_cadastre_unit_opens_and_renders_like_a_register(auto_service):
    service, _ = auto_service
    sid, units = await d10_flow(service, ['D 10'])
    out = await call(service, 'open_source', research_id=sid, source_id=units['sources'][0]['source_id'])
    assert out.data['kind'] == 'land_records' and out.data['unit']['category'] == 'cadastre' and out.data['total'] == 3
    assert out.data['unit']['title'].startswith('Duplikát – Demovice (Demowitz)')
    assert out.data['ancestors'][-1]['name'].startswith('Demovice (Demowitz)')
    assert out.data['pages'][0]['source'] == 'aron' and out.data['pages'][0]['source_type'] in ('dzi', 'iiif')
    view = await call(service, 'view_scan', research_id=sid, page_id=out.data['pages'][0]['page_id'])
    assert view.image_b64 and view.data['image']['source'] == 'aron' and view.data['image']['citation']
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=view.data['image']['image_id'],
                      bbox_px={'x': 0, 'y': 0, 'width': view.data['image']['width'] / 2, 'height': view.data['image']['height'] / 2})
    assert zoom.data['image']['role'] == 'detail'
