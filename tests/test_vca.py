import pytest
from rubrika import aron, demo_vca as V
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.network import SafeSource
from rubrika.demo import DemoArchive
from rubrika.sources import VCA_HOST, VCA_IMAGES


def test_listview_places_are_vca_ids():
    data={"items":[{"id":V.PLACE_CITY,"name":"Demo Hradec (Testov, TEST, Česko) SYNTHETIC","description":None,"order":1}],"count":1}
    places=aron.parse_listview_places(data,aron.VCA_SITE)
    assert places[0].source_id=='vca:place:'+V.PLACE_CITY and places[0].names==['Demo Hradec']
    assert places[0].source_url=='https://aron.vychodoceskearchivy.cz/apu/'+V.PLACE_CITY

def test_listview_units_parse_signature_dating_and_notes():
    data={"items":[{"id":V.UNIT_BIRTHS,"name":"SYNTHETIC archiv: Sbírka matrik, sign.: 1-1 (poř.č. 1), 1780-1800",
                    "description":"SYNTHETIC matrika narozených; obce: Demo Hradec","order":1}],"count":1}
    rows,count=aron.parse_listview_units(data)
    assert count==1 and rows[0]['uuid']==V.UNIT_BIRTHS and rows[0]['storage']=='1-1 (poř.č. 1)'
    assert rows[0]['dating'].spans and rows[0]['dating'].spans[0].start==1780
    assert rows[0]['notes'].startswith('SYNTHETIC matrika') and rows[0]['fund_uuid'] is None

@pytest.mark.parametrize('bad',[{"items":[{"id":"x","name":"a"}],"count":1},{"items":"x","count":1},{"items":[]},
                                {"items":[{"id":"00000000-0000-4000-8000-0000000000a1"}],"count":1}])
def test_listview_rejects_bad_shapes(bad):
    with pytest.raises(ResearchError) as e: aron.parse_listview_units(bad)
    assert e.value.code=='PARSE_ERROR'

def test_units_query_adds_unit_type():
    body=aron.units_query([V.PLACE_CITY],None,None,True,0,unit_type='matrika')
    assert body['filters'][1]=={"field":"UNIT~TYPE","operation":"EQ","value":"matrika"}

def test_site_ids():
    u='00000000-0000-4000-8000-0000000000a1'
    assert aron.MZA_SITE.unit_id(u)==aron.MZA_SITE.place_id(u)==aron.MZA_SITE.fund_id(u)=='aron:'+u
    assert aron.VCA_SITE.fund_id(u)=='vca:fund:'+u
    for prefix in ('vca:unit:','vca:place:','vca:fund:'):
        assert aron.VCA_SITE.uuid_of(prefix+u)==u
    with pytest.raises(ResearchError): aron.VCA_SITE.uuid_of('aron:'+u)

def test_vca_detail_parsers_use_vca_ids():
    detail=V.unit_detail(V.UNIT_BIRTHS)
    d=aron.parse_unit_detail(detail,aron.VCA_SITE)
    assert d['ancestors'] and all(a['id'].startswith('vca:') for a in d['ancestors'])
    fund=aron.parse_fund(V.fund_detail(V.FUND_REGISTERS),aron.VCA_SITE)
    assert fund.source_id=='vca:fund:'+V.FUND_REGISTERS

def test_vca_url_to_id():
    u='00000000-0000-4000-8000-0000000000a1'
    assert aron.vca_unit_id_from_url('https://aron.vychodoceskearchivy.cz/apu/'+u.upper()+'/')=='vca:unit:'+u
    with pytest.raises(ResearchError): aron.vca_unit_id_from_url('https://www.mza.cz/aron/apu/'+u)


def test_mza_site_matches_module_helpers():
    u='00000000-0000-4000-8000-0000000000a1'
    assert aron.MZA_SITE.uuid_of('aron:'+u)==aron.uuid_of('aron:'+u)==u
    assert aron.MZA_SITE.detail_url(u)=='https://www.mza.cz/aron/apu/'+u


@pytest.fixture
def vca_catalog(cfg, store):
    archive=DemoArchive()
    source=SafeSource(cfg, store, transport=archive.transport, source_mode='synthetic_demo')
    return aron.AronCatalog(source, store, site=aron.VCA_SITE), archive, store


@pytest.mark.asyncio
async def test_catalog_lists_and_opens_vca_registers(vca_catalog):
    catalog,archive,store=vca_catalog
    sid=store.create('alice',SearchQuery(raw_query='SYNTHETIC VCA',event_type='birth',year_from=1785,year_to=1790))['research_id']
    places=await catalog.places(sid,'alice','Demo Hradec')
    assert [p.source_id for p in places][:2]==['vca:place:'+V.PLACE_CITY,'vca:place:'+V.PLACE_CITY_DUP] and len(places)==4
    units,complete,warnings=await catalog.units(sid,'alice',[V.PLACE_CITY],1785,1790,True,None,unit_type='matrika')
    assert complete and [u.source_id for u in units]==['vca:unit:'+V.UNIT_BIRTHS,'vca:unit:'+V.UNIT_DEATHS]
    assert archive.posts[-1]['filters'][1]=={"field":"UNIT~TYPE","operation":"EQ","value":"matrika"}
    assert units[0].notes.startswith('SYNTHETIC')
    unit,ancestors,pages,warnings,citation=await catalog.open_unit(sid,'alice',V.UNIT_BIRTHS,units[0])
    assert unit.source_id=='vca:unit:'+V.UNIT_BIRTHS and unit.unit_type=='matrika'
    assert unit.fund.source_id=='vca:fund:'+V.FUND_REGISTERS
    assert ancestors and all(a['id'].startswith('vca:') for a in ancestors)
    assert citation.startswith('SYNTHETIC') and len(pages)==3
    assert [p.page_id for p in pages]==[f'vca:unit:{V.UNIT_BIRTHS}:{n}' for n in (1,2,3)]
    assert all(p.source=='vca' and p.source_type=='dzi' for p in pages)
    assert all(p.source_url.startswith(f'https://{VCA_IMAGES}/tiles/') and p.source_url.endswith('/image.dzi') for p in pages)
    assert all(p.viewer_url.startswith(f'https://{VCA_HOST}/apu/{V.UNIT_BIRTHS}/dao/') for p in pages)
    assert not any('/mnt/images' in p.source_url for p in pages)
    assert not any('listresults' in u for u in archive.requests)


@pytest.mark.asyncio
async def test_catalog_vca_land_records_land_books_then_other_books(vca_catalog):
    catalog,archive,store=vca_catalog
    sid=store.create('alice',SearchQuery(raw_query='SYNTHETIC VCA'))['research_id']
    units,complete,_=await catalog.units(sid,'alice',[V.PLACE_CITY],None,None,False,None,
                                         unit_type='evidence nemovitostí',other_excluding=aron.VCA_SEPARATE_TYPES)
    assert complete and len(archive.posts)==2
    assert [(u.source_id,u.category) for u in units][0]==('vca:unit:'+V.UNIT_LANDBOOK_GEO,'land_book')
    assert {(u.source_id,u.category) for u in units[1:]}=={('vca:unit:'+V.UNIT_CHRONICLE,'other'),('vca:unit:'+V.UNIT_LAND,'other')}


@pytest.mark.asyncio
async def test_catalog_fund_name_route_keeps_only_exact_fund(vca_catalog):
    catalog,archive,store=vca_catalog
    sid=store.create('alice',SearchQuery(raw_query='SYNTHETIC VCA'))['research_id']
    units,complete,warnings=await catalog.units(sid,'alice',[V.PLACE_CITY],None,None,False,None,
        unit_type='evidence nemovitostí',other_excluding=aron.VCA_SEPARATE_TYPES,fund_names=['Demo Hradec'])
    ids=[u.source_id for u in units]
    assert 'vca:unit:'+V.UNIT_LANDBOOK in ids and 'vca:unit:'+V.UNIT_LANDBOOK_DECOY not in ids
    assert units.by_fund_name=={'vca:unit:'+V.UNIT_LANDBOOK} and complete and len(archive.posts)==3
    assert [u.category for u in units if u.source_id=='vca:unit:'+V.UNIT_LANDBOOK]==['land_book']

def test_units_query_fund_phrases_replace_place_filter():
    body=aron.units_query([],None,None,True,0,unit_type='evidence nemovitostí',fund_phrases=('Archiv města X',))
    assert not any(f.get('field')=='REG~GEO~REF' for f in body['filters'])
    assert {"operation":"OR","filters":[{"operation":"FTX","value":'"Archiv města X"'}]} in body['filters']


@pytest.mark.asyncio
async def test_catalog_incomplete_when_second_query_is_cut(vca_catalog,monkeypatch):
    catalog,archive,store=vca_catalog
    sid=store.create('alice',SearchQuery(raw_query='SYNTHETIC VCA'))['research_id']
    monkeypatch.setattr(aron,'LIST_PAGE_SIZE',1)
    monkeypatch.setattr(aron,'LIST_MAX_PAGES',1)
    units,complete,warnings=await catalog.units(sid,'alice',[V.PLACE_CITY],None,None,False,None,
                                                unit_type='evidence nemovitostí',other_excluding=aron.VCA_SEPARATE_TYPES)
    assert not complete and len(units)==2 and warnings


def test_demo_tiles_follow_deepzoom_geometry(monkeypatch):
    import io, httpx
    from PIL import Image
    archive=DemoArchive()
    base=f'https://{VCA_IMAGES}/tiles/00/00/{V.file_id(V.UNIT_BIRTHS,1)}/'
    get=lambda p: archive.handle(httpx.Request('GET',base+p))
    assert b'Width="1281"' in get('image.dzi').content
    assert Image.open(io.BytesIO(get('image_files/11/0_0.jpg').content)).size==(255,255)      # no left/top overlap
    assert Image.open(io.BytesIO(get('image_files/11/1_1.jpg').content)).size==(256,256)      # overlap on both sides
    assert Image.open(io.BytesIO(get('image_files/11/5_3.jpg').content)).size==(1281-5*254+1,897-3*254+1)
    assert get('image_files/11/6_0.jpg').status_code==404 and get('image_files/12/0_0.jpg').status_code==404
    monkeypatch.setattr(V,'MISSING_TILES',{(11,0,0)})
    assert get('image_files/11/0_0.jpg').status_code==404
    assert archive.handle(httpx.Request('GET',f'https://{VCA_IMAGES}/robots.txt')).status_code==404
    assert b'<!doctype html>' in archive.handle(httpx.Request('GET',f'https://{VCA_HOST}/robots.txt')).content

def test_null_name_tile_records_are_accepted():
    u='00000000-0000-4000-8000-0000000000a1'
    detail=V.unit_detail(V.UNIT_BIRTHS)
    files=detail['digitalObjects'][0]['files']
    tile=[f for f in files if f['type']=='TILE'][0]
    assert tile['name'] is None and tile['file'] is None and tile['contentType']=='application/octetstream'
    d=aron.parse_unit_detail(detail,aron.VCA_SITE)
    unit=aron.ArchivalUnit(source_id='vca:unit:'+V.UNIT_BIRTHS,title='SYNTHETIC',detail_url='https://aron.vychodoceskearchivy.cz/apu/x')
    pages,_=aron.unit_pages(detail,unit,None,aron.VCA_SITE)
    assert pages[0].image_key==tile['id'] and len(pages)==3

def test_present_bad_name_still_rejected():
    detail=V.unit_detail(V.UNIT_BIRTHS)
    [f for f in detail['digitalObjects'][0]['files'] if f['type']=='TILE'][0]['name']='bad name/'
    unit=aron.ArchivalUnit(source_id='vca:unit:'+V.UNIT_BIRTHS,title='SYNTHETIC',detail_url='https://aron.vychodoceskearchivy.cz/apu/x')
    with pytest.raises(ResearchError): aron.unit_pages(detail,unit,None,aron.VCA_SITE)


def test_listview_without_count_is_a_parse_error():
    from rubrika import aron
    from rubrika.errors import ResearchError
    for fn in (lambda d: aron.parse_listview_units(d),lambda d: aron.parse_listview_places(d,aron.VCA_SITE)):
        for data in ({'items':[]},{'items':[],'count':None},{'items':[],'count':'3'},{'count':1}):
            with pytest.raises(ResearchError) as e: fn(data)
            assert e.value.code=='PARSE_ERROR'


def _place(label,uuid_n):
    from rubrika import aron
    from rubrika.models import Place
    return Place(source_id=f'vca:place:00000000-0000-4000-8000-{uuid_n:012d}',label=label,names=[label.split(' (')[0]],source_url='https://aron.vychodoceskearchivy.cz/x')


def test_rank_places_puts_town_parts_between_exact_matches_and_the_rest():
    from rubrika import aron
    places=[_place('Pardubicky (SYNTHETIC)',1),_place('Pardubice-Staré Město (SYNTHETIC)',2),_place('Pardubice (SYNTHETIC : zámek)',3),
            _place('Pardubice (SYNTHETIC)',4),_place('Nové Pardubice (SYNTHETIC)',5),_place('Pardubice II (SYNTHETIC)',6)]
    ranked=[p.label.split(' (')[0]+('*' if ':' in p.label else '') for p in aron.rank_places(places,'pardubice')]
    assert ranked==['Pardubice','Pardubice*','Pardubice-Staré Město','Pardubice II','Pardubicky','Nové Pardubice']


@pytest.mark.parametrize('name,storage,fund',[
    ('SYNTHETIC archiv: Okresní úřad Demo I, poř. č.: 182, 1890','182','Okresní úřad Demo I'),
    ('SYNTHETIC archiv: Archiv města Demo, inv. č.: 88, 1605-1699','88','Archiv města Demo'),
    ('SYNTHETIC archiv: Archiv města Demo, nepl. inv. č.: 7, 1700-1710','7','Archiv města Demo'),
    ('SYNTHETIC archiv: Velkostatek Demo, sign. pův.: A 3, 1700-1710','A 3','Velkostatek Demo'),
    ('SYNTHETIC archiv: Sbírka matrik, sign.: 1-1 (poř.č. 1), 1780-1800','1-1 (poř.č. 1)','Sbírka matrik')])
def test_listview_storage_and_fund_labels(name,storage,fund):
    rows,_=aron.parse_listview_units({"items":[{"id":V.UNIT_CENSUS,"name":name,"description":None}],"count":1})
    assert rows[0]['storage']==storage and rows[0]['fund_name']==fund and rows[0]['dating'].spans

def test_listview_census_single_year_dating():
    rows,_=aron.parse_listview_units({"items":[{"id":V.UNIT_CENSUS,"name":V.UNITS[V.UNIT_CENSUS][0],
                                                "description":"Demo Hradec čp. 1-50"}],"count":1})
    assert rows[0]['dating'].spans[0].start==rows[0]['dating'].spans[0].end==1890

def test_units_query_excludes_types_as_separate_not_filters():
    body=aron.units_query([V.PLACE_CITY],None,None,True,0,exclude_types=('matrika','sčítací operát'))
    nots=[f for f in body['filters'] if f.get('operation')=='NOT']
    assert [n['filters'] for n in nots]==[[{"field":"UNIT~TYPE","operation":"EQ","value":t}] for t in ('matrika','sčítací operát')]

def test_unit_detail_flags_census():
    d=aron.parse_unit_detail(V.unit_detail(V.UNIT_CENSUS),aron.VCA_SITE)
    assert d['is_census'] and not d['is_register'] and d['storage']=='182'
    assert not aron.parse_unit_detail(V.unit_detail(V.UNIT_BIRTHS),aron.VCA_SITE)['is_census']

def test_vca_registry_kinds():
    from rubrika.archives import ARCHIVES
    assert ARCHIVES['vca'].kinds==('parish_registers','land_records','census')


def test_listview_month_year_dating_gives_known_span():
    rows,_=aron.parse_listview_units({"items":[{"id":V.UNIT_CENSUS,"name":"SYNTHETIC archiv: Sbírka matrik, sign.: 167-7239, 5.1866-5.1903",
                                                "description":None}],"count":1})
    d=rows[0]['dating']
    assert d.status=='known' and [(s.start,s.end) for s in d.spans]==[(1866,1903)]
