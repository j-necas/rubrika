import pytest
from conftest import call, open_demo
from rubrika import demo_census as D


async def census_flow(service,owner='alice',sid=None,years=(1880,1900),house='15'):
    if sid is None:
        out=await call(service,'start_research',owner,query={'raw_query':'SYNTHETIC census test','year_from':years[0],
            'year_to':years[1],'person':{'house_number':house}})
        sid=out.data['research_id']
    places=await call(service,'find_place',owner,research_id=sid,term='Demo Lhota',kind='census',archives=['mza'])
    sets=await call(service,'list_sources',owner,research_id=sid,place_ids=['census:obec:Demo Lhota'],kind='census')
    return sid,places.data,sets.data


@pytest.mark.asyncio
async def test_find_places_warns_about_districts(auto_service):
    service,_=auto_service
    _,places,_=await census_flow(service)
    assert places['kind']=='census' and places['places'][0]['place_kind']=='obec' and places['places'][0]['archive']=='mza'
    assert any('district_archive' in w for w in places['warnings'])

@pytest.mark.asyncio
async def test_sets_ranked_by_year_then_house_coverage(auto_service):
    service,_=auto_service
    _,_,sets=await census_flow(service)
    assert [s['source_id'] for s in sets['sources']]==['census:9001','census:9004','census:9005','census:9002','census:9003']
    assert sets['counts']=={'candidate':4,'excluded':1}
    assert sets['sources'][0]['relevance']['house_number_covered'] is True
    assert any('district_archive' in w for w in sets['warnings'])

@pytest.mark.asyncio
async def test_archive_and_year_filters(auto_service):
    service,archive=auto_service
    sid,_,_=await census_flow(service)
    out=await call(service,'list_sources',research_id=sid,place_ids=['census:obec:Demo Lhota'],kind='census',district_archive='SOkA Blansko',year_from=1890,year_to=1890)
    assert [s['source_id'] for s in out.data['sources']]==['census:9001','census:9002'] and out.data['warnings']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('args',[{'place_ids':[]},{'place_ids':['census:4422']},{'place_ids':['aron:x']},
    {'place_ids':['census:obec:Demo Lhota'],'district_archive':'Praha'},{'place_ids':['census:obec:Demo Lhota'],'year_from':1700,'year_to':1700},
    {'place_ids':['census:obec:Demo Lhota'],'year_from':1890},{'place_ids':['census:obec:Demo Lhota'],'fund_marks':['C 17']},
    {'place_ids':['census:obec:Demo Lhota']*6}])
async def test_list_sources_rejects_bad_arguments(auto_service,args):
    service,_=auto_service
    sid,_,_=await census_flow(service)
    out=await service.call('list_sources','alice',{'research_id':sid,'kind':'census',**args})
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_place_must_come_from_this_search(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC'})
    res=await service.call('list_sources','alice',{'research_id':out.data['research_id'],'kind':'census','place_ids':['census:obec:Demo Lhota']})
    assert res.is_error and res.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_combined_search_links_census_to_register_evidence(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    reg=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    reg_obs=await call(service,'record_finding',research_id=sid,idempotency_key='reg',
        observation={'kind':'record','image_id':reg.data['image']['image_id'],'text':'SYNTHETIC'})
    await census_flow(service,sid=sid)
    opened=await call(service,'open_source',research_id=sid,source_id='census:9001')
    assert opened.data['archive']=='mza' and opened.data['kind']=='census' and opened.data['total']==3 and opened.data['citation']
    img=await call(service,'view_scan',research_id=sid,page_id=opened.data['pages'][0]['page_id'])
    ref=img.data['image']
    assert ref['source']=='census' and ref['citation']==opened.data['citation']
    crop=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                    bbox_px={'x':0,'y':0,'width':ref['width']/2,'height':ref['height']/2})
    await call(service,'record_finding',research_id=sid,idempotency_key='census',observation={
        'kind':'record','image_id':crop.data['image']['image_id'],'text':'SYNTHETIC','is_candidate':True,
        'links':[{'observation_id':reg_obs.data['observation_id'],'relation':'same_person'}]})
    report=await call(service,'finish_research',research_id=sid,stop_reason='candidate_found')
    assert report.data['candidates'][0]['book_id']=='census:9001'
    opened_sources={b['book_id']:b['source'] for b in report.data['coverage']['opened_books']}
    assert opened_sources['census:9001']=='census'
    not_opened={b['book_id']:b for b in report.data['coverage']['catalog_books_not_opened']}
    assert not_opened['census:9004']['source']=='census' and not_opened['census:9004']['number']=='HB9004'
    assert not_opened['census:9004']['images_available'] is True
    assert not_opened['census:9003']['images_available'] is False
    assert any(c.get('source')=='census' for c in report.data['coverage']['catalog_listings'])

@pytest.mark.asyncio
async def test_restricted_year_is_not_charged_and_downloads_nothing(auto_service):
    service,archive=auto_service
    sid,_,_=await census_flow(service)
    n=len(archive.requests)
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'census:9003'})
    assert out.is_error and out.data['error']['code']=='UNSUPPORTED_SOURCE'
    assert out.data['error']['details']['reason']=='reading_room_only'
    state=await call(service,'get_research_status',research_id=sid)
    assert len(state.data['coverage']['opened_books'])==0
    assert not any('iipsrv' in r for r in archive.requests)
    # The listing already knows this set has no images, so no detail fetch either.
    assert len(archive.requests)==n

@pytest.mark.asyncio
async def test_restricted_open_attempt_is_recorded_in_report(auto_service):
    service,_=auto_service
    sid,_,_=await census_flow(service)
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'census:9003'})
    assert out.is_error
    report=await call(service,'finish_research',research_id=sid,stop_reason='user_stopped')
    restricted={r['book_id']:r for r in report.data['coverage']['restricted_sets']}
    assert restricted['census:9003']['reason']=='reading_room_only'

@pytest.mark.asyncio
async def test_list_and_open_omit_house_number_list(auto_service):
    service,_=auto_service
    sid,_,sets=await census_flow(service)
    for s in sets['sources']:
        assert 'numbers' not in s['house_numbers'] and 'raw' in s['house_numbers']
    opened=await call(service,'open_source',research_id=sid,source_id='census:9001')
    assert 'numbers' not in opened.data['unit']['house_numbers']

@pytest.mark.asyncio
async def test_reopen_charges_books_once(auto_service):
    service,_=auto_service
    sid,_,_=await census_flow(service)
    await call(service,'open_source',research_id=sid,source_id='census:9001')
    again=await call(service,'open_source',research_id=sid,source_id='census:9001',cursor=2,limit=1)
    assert again.data['pages'][0]['scan_number']==3
    state=await call(service,'get_research_status',research_id=sid)
    assert len(state.data['coverage']['opened_books'])==1

@pytest.mark.asyncio
async def test_ids_cannot_cross_kinds(auto_service):
    service,_=auto_service
    sid,_,_=await census_flow(service)
    cn='census:obec:Demo Lhota'
    for kind,ids in [('parish_registers',[cn]),('land_records',[cn]),('census',['900001']),('census',['aron:x']),('census',[cn,'900001'])]:
        out=await service.call('list_sources','alice',{'research_id':sid,'kind':kind,'place_ids':ids})
        assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT',(kind,ids)

@pytest.mark.asyncio
async def test_set_from_query_link(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC',
        'book_url':'https://www.mza.cz/scitacioperaty/digisada/detail/9002'})
    sid=out.data['research_id']
    ok=await call(service,'open_source',research_id=sid,source_id='census:9002')
    assert ok.data['unit']['part']=='Demo Paseka'
    out=await service.call('open_source','alice',{'research_id':sid,'source_id':'census:9001'})
    assert out.is_error and out.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_switch_blocks_new_traffic_but_not_stored_evidence(auto_service):
    service,archive=auto_service
    sid,_,_=await census_flow(service)
    opened=await call(service,'open_source',research_id=sid,source_id='census:9001')
    img=await call(service,'view_scan',research_id=sid,page_id=opened.data['pages'][0]['page_id'])
    service.cfg.census_enabled=False
    n=len(archive.requests)
    out=await service.call('view_scan','alice',{'research_id':sid,'page_id':opened.data['pages'][1]['page_id']})
    assert out.is_error and out.data['error']['code']=='LIVE_DISABLED'
    found=await call(service,'find_place',research_id=sid,term='Demo',kind='census',archives=['mza'])
    assert found.data['searched']==[] and found.data['not_searched'][0]['archive']=='mza'
    assert len(archive.requests)==n
    await call(service,'show_saved_evidence',research_id=sid,image_id=img.data['image']['image_id'])

@pytest.mark.asyncio
async def test_census_tools_respect_ownership(auto_service):
    service,_=auto_service
    sid,_,_=await census_flow(service)
    for name,args in [('find_place',{'term':'Demo','kind':'census','archives':['mza']}),
                      ('list_sources',{'place_ids':['census:obec:Demo Lhota'],'kind':'census'}),
                      ('open_source',{'source_id':'census:9001'})]:
        out=await service.call(name,'bob',{'research_id':sid,**args})
        assert out.is_error and out.data['error']['code']=='NOT_FOUND',name
