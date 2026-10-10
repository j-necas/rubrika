import pytest
from PIL import Image, ImageChops
import io, base64
from conftest import call
from rubrika import demo_vca as V

async def vca_listing(service,kind='parish_registers',years=(1785,1790)):
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC VCA test','place_hint':'Demo Hradec',
                                                   'event_type':'birth','year_from':years[0],'year_to':years[1]})
    sid=out.data['research_id']
    places=await call(service,'find_place',research_id=sid,term='Demo Hradec',kind=kind,archives=['vca'])
    city=next(p for p in places.data['places'] if p['source_id']=='vca:place:'+V.PLACE_CITY)
    listed=await call(service,'list_sources',research_id=sid,place_ids=[city['source_id']],kind=kind)
    return sid,places.data,listed.data

@pytest.mark.asyncio
async def test_vca_parish_registers_end_to_end(auto_service):
    service,archive=auto_service
    sid,places,listed=await vca_listing(service)
    assert places['searched']==['vca'] and {p['archive'] for p in places['places']}=={'vca'}
    assert listed['archive']=='vca' and listed['sources'][0]['source_id']=='vca:unit:'+V.UNIT_BIRTHS
    assert listed['sources'][0]['relevance']['status']=='candidate'
    assert archive.posts[-1]['filters'][1]=={"field":"UNIT~TYPE","operation":"EQ","value":"matrika"}
    opened=await call(service,'open_source',research_id=sid,source_id=listed['sources'][0]['source_id'])
    assert opened.data['archive']=='vca' and opened.data['kind']=='parish_registers' and opened.data['total']==3
    assert opened.data['unit']['unit_type']=='matrika'
    assert opened.data['citation'].startswith('SYNTHETIC') and opened.data['unit']['fund']['source_id']=='vca:fund:'+V.FUND_REGISTERS
    assert all(a['id'].startswith('vca:') for a in opened.data['ancestors'])
    page=opened.data['pages'][0]
    assert page['page_id']=='vca:unit:'+V.UNIT_BIRTHS+':1' and page['source_type']=='dzi' and page['archive']=='vca'
    assert page['viewer_url'].startswith('https://aron.vychodoceskearchivy.cz/apu/'+V.UNIT_BIRTHS+'/dao/')
    view=await call(service,'view_scan',research_id=sid,page_id=page['page_id'])
    ref=view.data['image']
    assert ref['archive']=='vca' and (ref['source_width'],ref['source_height'])==(1281,897)
    # A crop across the 254 px tile border must reproduce the synthetic image (JPEG tolerance).
    box=(200,200,120,110)   # spans tiles 0|1 in x and 0|1 in y at full resolution
    sx,sy=ref['width']/ref['source_width'],ref['height']/ref['source_height']
    zoom=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                    bbox_px={'x':box[0]*sx,'y':box[1]*sy,'width':box[2]*sx,'height':box[3]*sy})
    z=zoom.data['image']
    assert z['role']=='detail'
    x,y,w,h=z['source_bbox']
    got=Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).convert('RGB').resize((w,h))
    want=archive.image.crop((x,y,x+w,y+h)).convert('RGB')
    diff=ImageChops.difference(got,want)
    assert max(max(c) for c in diff.getextrema())<=64
    edge=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],
                    bbox_px={'x':ref['width']-40,'y':ref['height']-40,'width':40,'height':40})
    ex,ey,ew,eh=edge.data['image']['source_bbox']
    assert ex+ew==1281 and ey+eh==897
    urls=archive.requests
    assert any(u.endswith('/image.dzi') for u in urls) and any('/image_files/' in u for u in urls)
    assert not any('storage.vychodoceskearchivy.cz' in u or '/mnt/images' in u or 'listresults' in u for u in urls)

@pytest.mark.asyncio
async def test_vca_missing_tile_is_incomplete(auto_service,monkeypatch):
    service,archive=auto_service
    sid,_,listed=await vca_listing(service)
    opened=await call(service,'open_source',research_id=sid,source_id=listed['sources'][0]['source_id'])
    monkeypatch.setattr(V,'MISSING_TILES',{(11,0,0)})    # demo_vca returns 404 for these (level,x,y)
    out=await service.call('view_scan','alice',dict(research_id=sid,page_id=opened.data['pages'][1]['page_id']))
    assert out.is_error and out.data['error']['code']=='INCOMPLETE_IMAGE'

@pytest.mark.asyncio
async def test_vca_land_records_geo_route_fund_route_and_other_books(auto_service):
    service,archive=auto_service
    sid,_,listed=await vca_listing(service,kind='land_records',years=(1600,1900))
    rows={r['source_id']:r for r in listed['sources']}
    lb,geo,chron,land='vca:unit:'+V.UNIT_LANDBOOK,'vca:unit:'+V.UNIT_LANDBOOK_GEO,'vca:unit:'+V.UNIT_CHRONICLE,'vca:unit:'+V.UNIT_LAND
    ids=[r['source_id'] for r in listed['sources']]
    assert set(ids[:2])=={lb,geo} and set(ids[2:])=={chron,land} and listed['total']==4
    assert rows[lb]['category']==rows[geo]['category']=='land_book' and rows[chron]['category']=='other'
    assert rows[lb]['match']=='fund_name' and 'match' not in rows[geo]
    assert any('názvu fondu' in w and 'Archiv města/obce Demo Hradec' in w for w in listed['warnings'])
    # decoy (fund "Archiv města Demo Hradecká Lhota") hit the phrase search but is dropped; registers/census never listed
    assert 'vca:unit:'+V.UNIT_LANDBOOK_DECOY not in ids
    assert not {'vca:unit:'+V.UNIT_BIRTHS,'vca:unit:'+V.UNIT_DEATHS,'vca:unit:'+V.UNIT_CENSUS}&set(ids)
    assert listed['listing_complete']
    geo_q,fund_q,other_q=archive.posts[-3:]
    land_type={"field":"UNIT~TYPE","operation":"EQ","value":"evidence nemovitostí"}
    assert land_type in geo_q['filters'] and land_type in fund_q['filters']
    assert any(f.get('operation')=='OR' and f['filters'][0].get('field')=='REG~GEO~REF' for f in geo_q['filters'])
    ftx=next(f for f in fund_q['filters'] if f.get('operation')=='OR')['filters']
    assert {'operation':'FTX','value':'"Archiv města Demo Hradec"'} in ftx and {'operation':'FTX','value':'"Archiv obce Demo Hradec"'} in ftx
    assert not any(f.get('field')=='REG~GEO~REF' for f in fund_q['filters'])
    nots=[f['filters'][0]['value'] for f in other_q['filters'] if f.get('operation')=='NOT']
    assert nots==['matrika','sčítací operát','evidence nemovitostí']
    out=await service.call('list_sources','alice',dict(research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],
                                                         kind='land_records',fund_marks=['C 17']))
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_vca_land_records_pagination_spans_all_queries(auto_service):
    service,_=auto_service
    sid,_,listed=await vca_listing(service,kind='land_records',years=(1600,1900))
    page=await call(service,'list_sources',research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='land_records',limit=3)
    assert page.data['total']==4 and len(page.data['sources'])==3 and page.data['next_cursor']==3
    rest=await call(service,'list_sources',research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='land_records',limit=3,cursor=3)
    assert len(rest.data['sources'])==1 and rest.data['next_cursor'] is None

@pytest.mark.asyncio
async def test_vca_land_book_opens_as_land_records(auto_service):
    service,_=auto_service
    sid,_,listed=await vca_listing(service,kind='land_records',years=(1600,1900))
    opened=await call(service,'open_source',research_id=sid,source_id='vca:unit:'+V.UNIT_LANDBOOK)
    assert opened.data['kind']=='land_records' and opened.data['total']==2
    assert opened.data['unit']['unit_type']=='evidence nemovitostí'

@pytest.mark.asyncio
async def test_vca_census_end_to_end(auto_service):
    service,archive=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC census','place_hint':'Demo Hradec',
                   'year_from':1890,'year_to':1890,'person':{'house_number':'17'}})
    sid=out.data['research_id']
    places=await call(service,'find_place',research_id=sid,term='Demo Hradec',kind='census',archives=['vca'])
    assert places.data['searched']==['vca']
    city=next(p for p in places.data['places'] if p['source_id']=='vca:place:'+V.PLACE_CITY)
    listed=await call(service,'list_sources',research_id=sid,place_ids=[city['source_id']],kind='census',year_from=1890,year_to=1890)
    body=archive.posts[-1]
    assert {"field":"UNIT~TYPE","operation":"EQ","value":"sčítací operát"} in body['filters']
    assert {"field":"DIGITAL","operation":"EQ","value":"Ano"} in body['filters']
    rng=next(f for f in body['filters'] if f.get('field')=='UNIT~DATE')
    assert rng['gte'].startswith('1890-01-01') and rng['lte'].startswith('1890-12-31')
    assert listed.data['kind']=='census' and [r['source_id'] for r in listed.data['sources']]==['vca:unit:'+V.UNIT_CENSUS]
    row=listed.data['sources'][0]
    assert row['storage']=='182' and row['notes']=='Demo Hradec čp. 1-50' and row['kind']=='census'
    assert row['house_numbers']['start']==1 and row['house_numbers']['end']==50 and row['house_numbers']['parsed']
    assert row['relevance']['house_number_covered'] is True and row['relevance']['status']=='candidate'
    assert 'čísel' in listed.data['note']
    opened=await call(service,'open_source',research_id=sid,source_id=row['source_id'])
    assert opened.data['kind']=='census' and opened.data['archive']=='vca' and opened.data['total']==4
    assert opened.data['unit']['unit_type']=='sčítací operát' and 'čísel' in opened.data['note']
    page=opened.data['pages'][0]
    assert page['page_id']=='vca:unit:'+V.UNIT_CENSUS+':1' and page['source_type']=='dzi'
    view=await call(service,'view_scan',research_id=sid,page_id=page['page_id'])
    assert view.data['image']['archive']=='vca'

@pytest.mark.asyncio
async def test_vca_census_house_number_relevance_and_unknown(auto_service):
    service,_=auto_service
    for house,want in (('99',False),(None,None)):
        person={'house_number':house} if house else {}
        out=await call(service,'start_research',query={'raw_query':'SYNTHETIC hn','place_hint':'Demo Hradec','person':person})
        sid=out.data['research_id']
        await call(service,'find_place',research_id=sid,term='Demo Hradec',kind='census',archives=['vca'])
        listed=await call(service,'list_sources',research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='census')
        assert listed.data['sources'][0]['relevance']['house_number_covered'] is want

@pytest.mark.parametrize('text,covered,not_covered,unknown',[
    ('Demo Hradec čp. 1-38, 40',[1,38,40],[39],[]),
    ('Demo Hradec čp. 1–50',[1,2,50],[51],[]),                       # en dash
    ('Demo Hradec čp. 1—50',[25],[51],[]),                           # em dash
    ('Demo Hradec čp. 51-100 a 200-250',[51,100,200,250],[101,199],[]),
    ('Demo Hradec čp. 1-50, Kukleny čp. 1-20',[1,50],[],[60]),       # unread text -> only positives are safe
    ('Demo Hradec čp. 1-50 (část)',[5],[],[60]),
    ('Demo Hradec',[],[],[1]),
    (None,[],[],[1])])
def test_house_numbers_from_description(text,covered,not_covered,unknown):
    from rubrika.service import vca_house_numbers
    from rubrika.census import house_covered
    hn=vca_house_numbers(text)
    assert all(house_covered(hn,str(n)) is True for n in covered)
    assert all(house_covered(hn,str(n)) is False for n in not_covered)
    assert all(house_covered(hn,str(n)) is None for n in unknown)
    if unknown or text is None: assert not hn.parsed
    else: assert hn.parsed

@pytest.mark.asyncio
async def test_vca_census_rows_sorted_covered_first(auto_service,monkeypatch):
    from rubrika.aron import sort_key
    from rubrika.service import ResearchService
    service,_=auto_service
    extra=V.u(0xA7)
    monkeypatch.setitem(V.UNITS,extra,("SYNTHETIC archiv: Okresní úřad Demo II, poř. č.: 183, 1890","Demo Hradec čp. 51-100","sčítací operát",1,[V.PLACE_CITY]))
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC sort','place_hint':'Demo Hradec','person':{'house_number':'70'}})
    sid=out.data['research_id']
    await call(service,'find_place',research_id=sid,term='Demo Hradec',kind='census',archives=['vca'])
    listed=await call(service,'list_sources',research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='census')
    assert [r['relevance']['house_number_covered'] for r in listed.data['sources']]==[True,False]
    assert listed.data['sources'][0]['source_id']=='vca:unit:'+extra

@pytest.mark.asyncio
async def test_vca_census_rejects_fund_marks_and_district_archive(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC bad'})
    sid=out.data['research_id']
    await call(service,'find_place',research_id=sid,term='Demo Hradec',kind='census',archives=['vca'])
    for extra in (dict(fund_marks=['C 17']),dict(district_archive='Znojmo')):
        res=await service.call('list_sources','alice',dict(research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='census',**extra))
        assert res.is_error and res.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_vca_census_never_lists_registers_or_land_books(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC only census'})
    sid=out.data['research_id']
    await call(service,'find_place',research_id=sid,term='Demo Hradec',kind='census',archives=['vca'])
    listed=await call(service,'list_sources',research_id=sid,place_ids=['vca:place:'+V.PLACE_CITY],kind='census')
    assert listed.data['total']==1

@pytest.mark.asyncio
async def test_vca_detail_decides_kind_without_listing(auto_service):
    service,_=auto_service
    for unit,kind in ((V.UNIT_CENSUS,'census'),(V.UNIT_BIRTHS,'parish_registers'),(V.UNIT_LANDBOOK,'land_records')):
        out=await call(service,'start_research',query={'raw_query':'SYNTHETIC link','book_url':'https://aron.vychodoceskearchivy.cz/apu/'+unit})
        opened=await call(service,'open_source',research_id=out.data['research_id'],source_id='vca:unit:'+unit)
        assert opened.data['kind']==kind

@pytest.mark.asyncio
async def test_vca_listing_kind_wins_over_detail_except_registers(auto_service):
    service,_=auto_service
    sid,_,_=await vca_listing(service,kind='census',years=(1890,1890))
    service.store.link(sid,'alice','book_option','vca:unit:'+V.UNIT_CHRONICLE,{'kind':'census'})
    # a register stays a register whatever the listing said
    service.store.link(sid,'alice','book_option','vca:unit:'+V.UNIT_BIRTHS,{'kind':'census'})
    ent=lambda u:service.store.entity('unit','vca:unit:'+u)
    from rubrika.models import ArchivalUnit
    for u in (V.UNIT_CHRONICLE,V.UNIT_BIRTHS):
        service.store.put_entity('unit','vca:unit:'+u,ArchivalUnit(source_id='vca:unit:'+u,title='SYNTHETIC',
            detail_url='https://aron.vychodoceskearchivy.cz/apu/'+u).model_dump())
    chron=await call(service,'open_source',research_id=sid,source_id='vca:unit:'+V.UNIT_CHRONICLE)
    assert chron.data['kind']=='census'
    births=await call(service,'open_source',research_id=sid,source_id='vca:unit:'+V.UNIT_BIRTHS)
    assert births.data['kind']=='parish_registers'

@pytest.mark.asyncio
async def test_vca_list_archives_offers_census(auto_service):
    service,_=auto_service
    out=await call(service,'list_archives')
    vca=next(a for a in out.data['archives'] if a['code']=='vca')
    assert 'census' in vca['kinds'] and 'census' in vca['available_kinds']

@pytest.mark.asyncio
async def test_vca_open_requires_listing_or_book_url(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC VCA link',
        'book_url':'https://aron.vychodoceskearchivy.cz/apu/'+V.UNIT_DEATHS})
    sid=out.data['research_id']
    ok=await call(service,'open_source',research_id=sid,source_id='vca:unit:'+V.UNIT_DEATHS)
    assert ok.data['total']==2 and ok.data['kind']=='parish_registers'
    bad=await service.call('open_source','alice',dict(research_id=sid,source_id='vca:unit:'+V.UNIT_BIRTHS))
    assert bad.is_error and bad.data['error']['code']=='NOT_FOUND'

@pytest.mark.asyncio
async def test_vca_disabled_is_reported_not_searched(auto_service):
    service,archive=auto_service
    service.cfg=service.cfg.model_copy(update={'vca_enabled':False})
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC VCA off'})
    before=len(archive.requests)
    places=await call(service,'find_place',research_id=out.data['research_id'],term='Demo Hradec',kind='parish_registers',archives=['vca'])
    assert places.data['searched']==[] and places.data['not_searched'][0]['archive']=='vca' and len(archive.requests)==before

@pytest.mark.asyncio
async def test_vca_evidence_in_report_and_gedcom(auto_service):
    service,_=auto_service
    sid,_,listed=await vca_listing(service)
    opened=await call(service,'open_source',research_id=sid,source_id=listed['sources'][0]['source_id'])
    view=await call(service,'view_scan',research_id=sid,page_id=opened.data['pages'][0]['page_id'])
    obs=await call(service,'record_finding',research_id=sid,idempotency_key='k1',observation={
        'kind':'record','image_id':view.data['image']['image_id'],'text':'SYNTHETIC zápis','is_candidate':True})
    status=await call(service,'get_research_status',research_id=sid)
    book=status.data['coverage']['opened_books'][0]
    assert book['source']=='vca' and book['book_id']=='vca:unit:'+V.UNIT_BIRTHS
    tree=await call(service,'export_family_tree',tree={'title':'SYNTHETIC','root_person':'p1','persons':[{'id':'p1',
        'given_name':'Jan','surname':'Demo','events':[{'type':'birth','confidence':'documented','date':{'year':1786},
        'sources':[{'research_id':sid,'observation_id':obs.data['observation_id']}]}]}]})
    ged=tree.data['gedcom']
    assert '1 NAME Státní oblastní archiv v Hradci Králové' in ged and 'aron:' not in ged

@pytest.mark.asyncio
async def test_vca_list_sources_rejects_unit_and_fund_ids_as_places(auto_service):
    service,_=auto_service
    sid,_,_=await vca_listing(service)
    for bad in ('vca:unit:'+V.UNIT_BIRTHS,'vca:fund:'+V.FUND_REGISTERS):
        out=await service.call('list_sources','alice',dict(research_id=sid,place_ids=[bad],kind='parish_registers'))
        assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_open_source_rejects_vca_place_id(auto_service):
    service,_=auto_service
    sid,_,_=await vca_listing(service)
    out=await service.call('open_source','alice',dict(research_id=sid,source_id='vca:place:'+V.PLACE_CITY))
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_link_only_archive_place_is_invalid_argument(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC link only'})
    res=await service.call('list_sources','alice',dict(research_id=out.data['research_id'],
        place_ids=['zao:00000000-0000-4000-8000-0000000000a1'],kind='parish_registers'))
    assert res.is_error and res.data['error']['code']=='INVALID_ARGUMENT'

@pytest.mark.asyncio
async def test_find_place_isolates_archive_failure(auto_service,monkeypatch):
    from rubrika.errors import ResearchError
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC isolate'})
    orig=service._search_places
    async def flaky(owner,research_id,archive,kind,term):
        if archive=='vca': raise ResearchError('SOURCE_UNAVAILABLE','SYNTHETIC výpadek.')
        return await orig(owner=owner,research_id=research_id,archive=archive,kind=kind,term=term)
    monkeypatch.setattr(service,'_search_places',flaky)
    res=await call(service,'find_place',research_id=out.data['research_id'],term='Demo Lhota',kind='parish_registers',archives=['vca','mza'])
    assert res.data['searched']==['mza'] and res.data['places']
    failed=[n for n in res.data['not_searched'] if n['archive']=='vca']
    assert failed and 'SOURCE_UNAVAILABLE' in failed[0]['reason']

@pytest.mark.asyncio
async def test_find_place_spent_daily_cap_of_one_archive_keeps_others(auto_service,monkeypatch):
    from rubrika.errors import ResearchError
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC budget'})
    real=service._search_places
    async def capped(**kw):
        if kw['archive']=='vca': raise ResearchError('BUDGET_EXCEEDED','SYNTHETIC',scope='archive:vca')
        return await real(**kw)
    monkeypatch.setattr(service,'_search_places',capped)
    res=await call(service,'find_place',research_id=out.data['research_id'],term='Demo Lhota',kind='parish_registers',archives=['mza','vca'])
    assert res.data['searched']==['mza'] and 'BUDGET_EXCEEDED' in res.data['not_searched'][0]['reason']

@pytest.mark.asyncio
async def test_find_place_not_found_in_one_archive_keeps_others(auto_service,monkeypatch):
    from rubrika.errors import ResearchError
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC nf'})
    orig=service._search_places
    async def flaky(owner,research_id,archive,kind,term):
        if archive=='vca': raise ResearchError('NOT_FOUND','SYNTHETIC 404')
        return await orig(owner=owner,research_id=research_id,archive=archive,kind=kind,term=term)
    monkeypatch.setattr(service,'_search_places',flaky)
    res=await call(service,'find_place',research_id=out.data['research_id'],term='Demo Lhota',kind='parish_registers',archives=['vca','mza'])
    assert res.data['searched']==['mza'] and res.data['places']
    assert any(n['archive']=='vca' and 'NOT_FOUND' in n['reason'] for n in res.data['not_searched'])

@pytest.mark.asyncio
async def test_vca_view_scan_after_disable_is_live_disabled(auto_service):
    service,_=auto_service
    sid,_,listed=await vca_listing(service)
    opened=await call(service,'open_source',research_id=sid,source_id=listed['sources'][0]['source_id'])
    service.cfg=service.cfg.model_copy(update={'vca_enabled':False})
    out=await service.call('view_scan','alice',dict(research_id=sid,page_id=opened.data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code']=='LIVE_DISABLED'

@pytest.mark.asyncio
async def test_vca_places_ranked_municipality_first_with_description(auto_service):
    service,archive=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC rank'})
    res=await call(service,'find_place',research_id=out.data['research_id'],term='demo hradec',kind='parish_registers',archives=['vca'])
    ids=[p['source_id'] for p in res.data['places']]
    assert archive.posts[-1]['size']==30
    assert ids[:2]==['vca:place:'+V.PLACE_CITY,'vca:place:'+V.PLACE_CITY_DUP]
    assert set(ids[2:])=={'vca:place:'+V.PLACE_CORP,'vca:place:'+V.PLACE_CASTLE}
    city=res.data['places'][0]
    assert city['label'].endswith(' – SYNTHETIC obec Demo Hradec v okrese Testov') and city['names']==['Demo Hradec']

@pytest.mark.asyncio
async def test_mza_place_query_size_unchanged():
    from rubrika import aron
    assert aron.place_query('x')['size']==10 and aron.place_query('x',30)['size']==30


@pytest.mark.asyncio
async def test_vca_place_search_warns_when_candidates_were_cut(auto_service,monkeypatch):
    from rubrika import aron
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'SYNTHETIC cut'})
    sid=out.data['research_id']
    full=await call(service,'find_place',research_id=sid,term='demo hradec',kind='parish_registers',archives=['vca'])
    assert not any('vynechán' in w for w in full.data['warnings'])
    monkeypatch.setattr(aron,'VCA_PLACE_RESULTS',2)
    cut=await call(service,'find_place',research_id=sid,term='demo hradec',kind='parish_registers',archives=['vca'])
    assert len(cut.data['places'])==2 and any('2 bylo vynecháno' in w and 'nejméně odpovídající' in w and 'Vynechané záznamy:' in w and 'okres' in w for w in cut.data['warnings'])
