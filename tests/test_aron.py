import copy
import json
import httpx
import pytest
from rubrika import demo_aron as D
from rubrika.aron import (AronCatalog, aron_relevance, category, clip, normalize_mark, parse_citation, parse_fund,
    parse_places, parse_unit_date, parse_unit_detail, parse_unit_rows, place_query, unit_id_from_url,
    unit_pages, units_query, uuid_of)
from rubrika.demo import DemoArchive
from rubrika.errors import ResearchError
from rubrika.models import ArchivalUnit, Periods, SearchQuery, YearSpan
from rubrika.network import SafeSource


def listing(*rows):
    return {'items':list(rows),'count':len(rows),'searchAfter':None,'aggregations':None}

def unit(source_id='aron:'+D.UNIT_LAND,**kw):
    return ArchivalUnit(source_id=source_id,title='SYNTHETIC',detail_url='https://www.mza.cz/aron/apu/x',**kw)


def test_places_are_namespaced_with_short_names():
    places=parse_places(listing(D.entity_row(D.PLACE_LHOTA),D.entity_row(D.PLACE_LHOTA)))
    assert len(places)==1
    p=places[0]
    assert p.source_id=='aron:'+D.PLACE_LHOTA and p.names==['Demo Lhota']
    assert p.source_url=='https://www.mza.cz/aron/apu/'+D.PLACE_LHOTA and 'nižší' not in p.label

def test_unit_rows():
    rows,count=parse_unit_rows(listing(D.unit_row(D.UNIT_LAND),D.unit_row(D.UNIT_UNDATED)))
    assert count==2
    land,undated=rows
    assert land['uuid']==D.UNIT_LAND and land['fund_uuid']==D.FUND_LAND and land['storage']=='9001 kniha'
    assert land['dating'].status=='known' and land['dating'].spans==[YearSpan(start=1700,end=1780)]
    assert land['places']==['Demo Lhota'] and land['fund_name']=='SYNTHETIC Pozemkové knihy'
    assert undated['dating'].status=='unknown'

@pytest.mark.parametrize('text,status',[('(1758) 1883','uncertain'),('1773-1802, 1808','known'),('asi 1700','uncertain')])
def test_free_text_row_datings_never_exclude(text,status):
    row=D.unit_row(D.UNIT_LAND)
    row['l'][1]=[{'t':'D','v':[{'v':text,'id':None}]}]
    rows,_=parse_unit_rows(listing(row))
    assert rows[0]['dating'].status==status
    rel=aron_relevance(unit(dating=rows[0]['dating']),SearchQuery(raw_query='x',year_from=1790,year_to=1800))
    assert rel['status']!='excluded' or status=='known'

@pytest.mark.parametrize('data',[
    {},{'items':[],'count':'1'},{'items':{},'count':0},
    listing({'id':'not-a-uuid','l':[]}),
    listing({'id':D.UNIT_LAND,'l':'x'}),
    listing({'id':D.UNIT_LAND,'l':[[{'t':'D','v':[{'v':'1700'}]}]]}),
    listing({'id':D.UNIT_LAND,'l':[[{'t':'N'}]]}),
])
def test_invalid_listings_are_parse_errors_not_empty(data):
    with pytest.raises(ResearchError) as e:parse_unit_rows(data)
    assert e.value.code=='PARSE_ERROR'

def test_long_source_text_is_clipped():
    row=D.unit_row(D.UNIT_LAND);row['l'][0]=[{'t':'N','v':[{'v':'X'*5000}]}]
    rows,_=parse_unit_rows(listing(row))
    assert len(rows[0]['title'])==500
    assert clip('  a \n b  ')=='a b' and clip('')is None and clip(None) is None

def test_fund_marks_and_categories():
    assert parse_fund(D.fund_detail(D.FUND_LAND)).model_dump()=={'source_id':'aron:'+D.FUND_LAND,'mark':'C 17',
        'name':'SYNTHETIC Pozemkové knihy','nad':'142'}
    assert normalize_mark('C17')=='C 17' and normalize_mark(' D 10 ')=='D 10'
    assert normalize_mark('c 17') is None and normalize_mark('C 17; x') is None and normalize_mark(None) is None
    assert category('C 17',None)=='land_book' and category('D 4','úřední kniha')=='cadastre'
    assert category('D 10',None)=='cadastre' and category('F 999','pozemková kniha')=='land_book'
    assert category(None,'Stabilní katastr')=='cadastre' and category('F 999','úřední kniha')=='other'

@pytest.mark.parametrize('values,text,status,spans',[
    (['{"from":"1700-01-01T00:00:00","to":"1780-12-31T23:59:59","valueFromEstimated":false,"valueToEstimated":false}'],'1700–1780','known',[(1700,1780)]),
    (['{"from":"1760-01-01T00:00:00","to":"1770-12-31T23:59:59","valueFromEstimated":true,"valueToEstimated":false}'],None,'uncertain',[(1760,1770)]),
    (['{"from":"1790-01-01T00:00:00","to":"1780-12-31T23:59:59"}'],None,'uncertain',[]),
    ([],'(1758) 1883','uncertain',[]),
    ([],None,'unknown',[]),
])
def test_unit_date(values,text,status,spans):
    p=parse_unit_date(values,text)
    assert p.status==status and [(s.start,s.end) for s in p.spans]==spans

@pytest.mark.parametrize('value',['not json','{"from":null,"to":"1780"}','[]'])
def test_invalid_unit_date_is_parse_error(value):
    with pytest.raises(ResearchError) as e:parse_unit_date([value],None)
    assert e.value.code=='PARSE_ERROR'

def test_unit_detail_fields_and_ancestors():
    d=parse_unit_detail(D.unit_detail(D.UNIT_LAND))
    assert d['uuid']==D.UNIT_LAND and d['fund_uuid']==D.FUND_LAND and d['unit_type']=='pozemková kniha'
    assert d['formal_title']=='SYNTHETIC Grund Buch' and 'rejstřík' in d['notes'] and d['part']=='1.'
    assert d['digitized'] is True and d['dao_link'] is None and d['dating'].status=='known'
    assert [a['id'] for a in d['ancestors']]==['aron:'+D.FUND_LAND,'aron:'+D.SERIES]

def test_access_restriction_becomes_warning():
    data=D.unit_detail(D.UNIT_LAND)
    data['parts'][0]['items'].append({'type':'UNIT~RESTRICTION~TEXT','value':'Originál není přístupný.'})
    assert parse_unit_detail(data)['warnings']==['Omezení přístupu: Originál není přístupný.']

@pytest.mark.parametrize('mutate',[
    lambda d:d.pop('parts'), lambda d:d.update(parts='x'), lambda d:d.update(id='x'),
    lambda d:d['parent'].update(id='x'),
    lambda d:d['parts'][0].update(items=5), lambda d:d['parts'][0].update(childParts=5),
])
def test_invalid_detail_is_parse_error(mutate):
    data=D.unit_detail(D.UNIT_LAND);mutate(data)
    with pytest.raises(ResearchError) as e:parse_unit_detail(data)
    assert e.value.code=='PARSE_ERROR'

def test_citation():
    assert parse_citation({'citation':'  MZA;  x '})=='MZA; x'
    for bad in ({'citation':None},{},[]):
        with pytest.raises(ResearchError):parse_citation(bad)

def test_pages_are_iiif_with_deep_links_and_gap_warning():
    u=unit()
    pages,warnings=unit_pages(D.unit_detail(D.UNIT_LAND),u,'SYNTHETIC citation')
    assert [p.scan_number for p in pages]==[1,2,3]
    p=pages[0]
    assert p.page_id=='aron:'+D.UNIT_LAND+':1' and p.book_id=='aron:'+D.UNIT_LAND
    assert p.source=='aron' and p.citation=='SYNTHETIC citation' and p.source_type=='iiif'
    assert p.source_url.endswith('?IIIF=/srv/DEMO/216000010-DEMO-9001-00010.jp2/info.json')
    assert p.image_key=='216000010-DEMO-9001-00010.jp2'
    assert p.viewer_url==f'https://www.mza.cz/aron/apu/{D.UNIT_LAND}/dao/{D.UNIT_LAND}/file/{D.file_id(D.UNIT_LAND,10)}'
    assert len({x.manifest_sha256 for x in pages})==1
    assert any('mezery' in w for w in warnings)
    _,warnings=unit_pages(D.unit_detail(D.UNIT_CADASTRE),unit('aron:'+D.UNIT_CADASTRE),None)
    assert warnings==[]

def test_only_tiles_count_and_bad_file_lists_fail():
    data=D.unit_detail(D.UNIT_CADASTRE)
    for f in data['digitalObjects'][0]['files']:f['type']='PUBLISHED'
    assert unit_pages(data,unit('aron:'+D.UNIT_CADASTRE),None)==([],[])
    for mutate in (lambda d:d.pop('digitalObjects'),
                   lambda d:d['digitalObjects'][0]['files'][1].update(referencedFile=d['digitalObjects'][0]['files'][0]['referencedFile']),
                   lambda d:d['digitalObjects'][0]['files'][0].update(name='../x.jp2'),
                   lambda d:d['digitalObjects'][0]['files'][0].update(order='1')):
        data=D.unit_detail(D.UNIT_CADASTRE);mutate(data)
        with pytest.raises(ResearchError) as e:unit_pages(data,unit('aron:'+D.UNIT_CADASTRE),None)
        assert e.value.code=='PARSE_ERROR'

def test_large_unit_parses():
    # ~1500 scans.
    data=D.unit_detail(D.UNIT_CADASTRE)
    template=data['digitalObjects'][0]['files'][0]
    data['digitalObjects'][0]['files']=[{**template,'id':D.u(0x500000+i),'order':i,
        'name':f'216000010-DEMO-9002-{i*10:05d}.jp2.dzi',
        'referencedFile':f'https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/DEMO/216000010-DEMO-9002-{i*10:05d}.jp2.dzi'}
        for i in range(1,1501)]
    pages,warnings=unit_pages(data,unit('aron:'+D.UNIT_CADASTRE),None)
    assert len(pages)==1500 and pages[-1].scan_number==1500 and warnings==[]

def test_ids_and_links():
    assert uuid_of('aron:'+D.UNIT_LAND)==D.UNIT_LAND
    for bad in ('900001','aron:x','aron:'+D.UNIT_LAND.upper()):
        with pytest.raises(ResearchError) as e:uuid_of(bad)
        assert e.value.code=='INVALID_ARGUMENT'
    # Browser links with upper case or trailing slash.
    assert unit_id_from_url('https://www.mza.cz/aron/apu/'+D.UNIT_LAND.upper()+'/')=='aron:'+D.UNIT_LAND
    # Scan viewer deep links carry the archival unit's uuid first.
    viewer=f'https://www.mza.cz/aron/apu/{D.UNIT_LAND.upper()}/dao/{D.SERIES}/file/{D.file_id(D.UNIT_LAND,10)}'
    assert unit_id_from_url(viewer)=='aron:'+D.UNIT_LAND
    for bad in ('http://www.mza.cz/aron/apu/'+D.UNIT_LAND,'https://evil.example/aron/apu/'+D.UNIT_LAND,
                'https://www.mza.cz/aron/apu/x'):
        with pytest.raises(ResearchError) as e:unit_id_from_url(bad)
        assert e.value.code=='INVALID_SOURCE'

def test_query_templates():
    q=place_query('Lhota Vyškov')
    assert q['filters']==[{'field':'type','operation':'EQ','value':'ENTITY'},{'operation':'FTX','value':'Lhota Vyškov'}]
    q=units_query([D.PLACE_LHOTA],1700,None,True,50)
    assert q['offset']==50 and q['size']==50
    assert {'field':'UNIT~DATE','operation':'RANGE','gte':'1700-01-01T00:00:00.000Z','lte':'2200-12-31T23:59:59.999Z'} in q['filters']
    assert q['filters'][-1]=={'operation':'OR','filters':[{'field':'REG~GEO~REF','operation':'CONTAINS','value':D.PLACE_LHOTA}]}
    q=units_query([D.PLACE_LHOTA],None,None,False,0)
    assert [f.get('field') for f in q['filters']]==['type',None]

@pytest.mark.parametrize('dating,status',[
    (Periods(status='known',spans=[YearSpan(start=1700,end=1780)]),'candidate'),
    (Periods(status='known',spans=[YearSpan(start=1850,end=1860)]),'excluded'),
    (Periods(status='uncertain',spans=[YearSpan(start=1850,end=1860)]),'needs_review'),
    (Periods(),'needs_review'),
])
def test_relevance_never_excludes_unknown(dating,status):
    rel=aron_relevance(unit(dating=dating,category='land_book'),SearchQuery(raw_query='x',year_from=1740,year_to=1760))
    assert rel['status']==status and rel['category']=='land_book'
    assert aron_relevance(unit(dating=dating),SearchQuery(raw_query='x'))['status']=='needs_review'

@pytest.mark.asyncio
async def test_catalog_lists_units_and_resolves_each_fund_once(cfg,store,sid):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    try:
        cat=AronCatalog(source,store)
        units,complete,warnings=await cat.units(sid,'alice',[D.PLACE_LHOTA,D.PLACE_LHOTA_DUP],None,None,True,None)
        assert complete and warnings==[] and len(units)==5
        by_id={u.source_id:u for u in units}
        assert by_id['aron:'+D.UNIT_LAND].fund.mark=='C 17' and by_id['aron:'+D.UNIT_LAND].category=='land_book'
        assert by_id['aron:'+D.UNIT_CADASTRE].category=='cadastre'
        assert by_id['aron:'+D.UNIT_LAND].digitized is True
        await cat.units(sid,'alice',[D.PLACE_LHOTA],None,None,True,None)
        fund_calls=[r for r in archive.requests if any(r.endswith('/apu/'+f) for f in D.FUNDS)]
        assert len(fund_calls)==3
        assert store.has_link(sid,'alice','book_option','aron:'+D.UNIT_LAND)
        only=await cat.units(sid,'alice',[D.PLACE_LHOTA],None,None,True,['D4'])
        assert [u.source_id for u in only[0]]==['aron:'+D.UNIT_CADASTRE]
    finally:await source.close()

@pytest.mark.asyncio
async def test_listing_stops_after_four_pages_and_reports_incomplete(cfg,store,sid):
    bodies=[]
    def handler(req):
        body=json.loads(req.content);bodies.append(body)
        rows=[{'id':f'00000000-0000-4000-9000-{body["offset"]+i:012x}','t':'A_IB',
               'l':[[{'t':'N','v':[{'v':f'SYNTHETIC {body["offset"]+i}'}]}]]} for i in range(body['size'])]
        return httpx.Response(200,json={'items':rows,'count':250})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        units,complete,warnings=await AronCatalog(source,store).units(sid,'alice',[D.PLACE_LHOTA],None,None,True,None)
        assert len(units)==200 and not complete and '250' in warnings[0]
        assert [b['offset'] for b in bodies]==[0,50,100,150]
    finally:await source.close()

@pytest.mark.asyncio
async def test_short_page_with_higher_count_is_reported_as_mismatch_not_page_limit(cfg,store,sid):
    bodies=[]
    def handler(req):
        body=json.loads(req.content);bodies.append(body)
        size=body['size'] if body['offset']==0 else 5
        rows=[{'id':f'00000000-0000-4000-9000-{body["offset"]+i:012x}','t':'A_IB',
               'l':[[{'t':'N','v':[{'v':f'SYNTHETIC {body["offset"]+i}'}]}]]} for i in range(size)]
        return httpx.Response(200,json={'items':rows,'count':60})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        units,complete,warnings=await AronCatalog(source,store).units(sid,'alice',[D.PLACE_LHOTA],None,None,True,None)
        assert len(units)==55 and not complete
        assert any('jedinečných' in w for w in warnings) and not any('Zužte' in w for w in warnings)
        assert [b['offset'] for b in bodies]==[0,50]
    finally:await source.close()

@pytest.mark.asyncio
async def test_open_unit_returns_pages_citation_and_ancestors(cfg,store,sid):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    try:
        unit,ancestors,pages,warnings,citation=await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_LAND,None)
        assert unit.category=='land_book' and unit.fund.nad=='142' and unit.formal_title=='SYNTHETIC Grund Buch'
        assert citation.startswith('SYNTHETIC archiv') and pages[0].citation==citation
        assert [a['id'] for a in ancestors]==['aron:'+D.FUND_LAND,'aron:'+D.SERIES]
        assert store.entity('page',pages[0].page_id)['source']=='aron'
        assert any('mezery' in w for w in warnings)
    finally:await source.close()

@pytest.mark.asyncio
async def test_missing_citation_is_only_a_warning(cfg,store,sid):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    try:
        _,_,pages,warnings,citation=await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_UNDATED,None)
        assert citation is None and pages and any('Citaci' in w for w in warnings)
    finally:await source.close()

@pytest.mark.asyncio
async def test_unit_without_supported_scans_is_unsupported_but_citable(cfg,store,sid):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    try:
        with pytest.raises(ResearchError) as e:
            await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_LINK_ONLY,None)
        assert e.value.code=='UNSUPPORTED_SOURCE'
        assert e.value.details['citation'] and e.value.details['dao_link']=='https://example.invalid/synthetic-viewer'
        assert e.value.details['unit']['dating']['status']=='uncertain'
    finally:await source.close()

@pytest.mark.asyncio
async def test_pages_outside_the_image_allowlist_are_dropped(cfg,store,sid):
    detail=D.unit_detail(D.UNIT_CADASTRE)
    detail['digitalObjects'][0]['files'][1]['referencedFile']='https://evil.example/iipsrv/iipsrv.fcgi?Deepzoom=/x.jp2.dzi'
    posts=[]
    def handler(req):
        if req.url.path.endswith('/apu/'+D.UNIT_CADASTRE):return httpx.Response(200,json=detail)
        return D.handle_aron(req,posts)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        _,_,pages,warnings,_=await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_CADASTRE,None)
        assert [p.scan_number for p in pages]==[1] and any('mimo povolený' in w for w in warnings)
    finally:await source.close()

@pytest.mark.asyncio
async def test_detail_for_another_unit_is_rejected(cfg,store,sid):
    def handler(req):return httpx.Response(200,json=D.unit_detail(D.UNIT_CADASTRE))
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_LAND,None)
        assert e.value.code=='PARSE_ERROR'
    finally:await source.close()

@pytest.mark.asyncio
async def test_page_referencing_an_aron_api_route_is_dropped_not_served_as_image(cfg,store,sid):
    # `referencedFile` must resolve to mza_images, not merely pass validate().
    detail=D.unit_detail(D.UNIT_CADASTRE)
    detail['digitalObjects'][0]['files'][1]['referencedFile']=f'https://www.mza.cz/aron/api/aron/apu/{D.UNIT_CADASTRE}'
    posts=[]
    def handler(req):
        if req.url.path.endswith('/apu/'+D.UNIT_CADASTRE):return httpx.Response(200,json=detail)
        return D.handle_aron(req,posts)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        _,_,pages,warnings,_=await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_CADASTRE,None)
        assert [p.scan_number for p in pages]==[1] and any('mimo povolený' in w for w in warnings)
    finally:await source.close()

@pytest.mark.asyncio
async def test_deeply_nested_json_is_parse_error_not_recursion_error(cfg,store,sid):
    def handler(req):return httpx.Response(200,content=b'['*200000,headers={'content-type':'application/json'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await AronCatalog(source,store)._json(sid,'alice','https://www.mza.cz/aron/api/aron/apu/'+D.UNIT_LAND)
        assert e.value.code=='PARSE_ERROR'
    finally:await source.close()

@pytest.mark.asyncio
async def test_missing_archival_unit_is_not_found_not_source_unavailable(cfg,store,sid):
    def handler(req):return httpx.Response(404,json={'message':'not found'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await AronCatalog(source,store)._json(sid,'alice','https://www.mza.cz/aron/api/aron/apu/'+D.UNIT_LAND)
        assert e.value.code=='NOT_FOUND' and e.value.details['status']==404
    finally:await source.close()

@pytest.mark.asyncio
async def test_citation_failure_downgrades_to_warning_but_rate_limit_propagates(cfg,store,sid):
    archive=D.handle_aron
    def failing(req,status):
        posts=[]
        if req.url.path==f'{D.API}/script/citation/{D.UNIT_LAND}':
            return httpx.Response(status,headers={'retry-after':'60'} if status==429 else {})
        return archive(req,posts)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:failing(r,500)),respect_robots=False)
    try:
        _,_,pages,warnings,citation=await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_LAND,None)
        assert citation is None and pages and any('Citaci' in w for w in warnings)
    finally:await source.close()
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:failing(r,429)),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await AronCatalog(source,store).open_unit(sid,'alice',D.UNIT_LAND,None)
        assert e.value.code=='RATE_LIMITED'
    finally:await source.close()


def test_with_katastr_only_takes_a_node_named_like_a_katastr():
    from rubrika.aron import with_katastr
    named = 'Lesoňovice (Lesseniowitz), obec: Bystřice, okres: Žďár'
    assert with_katastr('Duplikát', 'D 10', named) == 'Duplikát – ' + named
    assert with_katastr('Duplikát', 'D 10', 'Morava, katastrální území: řazeno abecedně') == 'Duplikát'
    assert with_katastr('Duplikát', 'D 10', None) == 'Duplikát' and with_katastr('Duplikát', 'C 17', named) == 'Duplikát'
