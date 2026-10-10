import httpx
import json
import pytest
from rubrika import demo_census as D
from rubrika.census import (ARCHIVES, archive_code, archive_warnings, build_citation, census_relevance,
    CensusCatalog, house_covered, next_page_url, parse_house_numbers, parse_places, parse_results, parse_set_detail,
    place_of, public_set, search_form, set_id_from_url, set_id_of, set_pages, sort_key)
from rubrika.demo import DemoArchive
from rubrika.errors import ResearchError
from rubrika.models import CensusSet, SearchQuery
from rubrika.network import SafeSource


def results(ids,offset=0):
    return D.results_page(ids,offset,len(ids))

def test_places_from_autocomplete():
    places=parse_places([{'value':'Brno - Královo Pole','label':'Brno - Královo Pole (město/obec - česky)'}],'obec')
    assert places[0].source_id=='census:obec:Brno - Královo Pole'
    assert place_of(places[0].source_id)==('obec','Brno - Královo Pole')
    assert places[0].names==['Brno - Královo Pole']
    for bad in ({},[{'label':'x'}],[{'value':' '}],[5]):
        with pytest.raises(ResearchError) as e:parse_places(bad,'obec')
        assert e.value.code=='PARSE_ERROR'

def test_ids_and_links():
    assert set_id_of('census:4422')=='4422'
    for bad in ('4422','census:x','aron:4422','census:obec:X'):
        with pytest.raises(ResearchError) as e:set_id_of(bad)
        assert e.value.code=='INVALID_ARGUMENT'
    for bad in ('census:4422','census:ulice:X','census:obec:'):
        with pytest.raises(ResearchError) as e:place_of(bad)
        assert e.value.code=='INVALID_ARGUMENT'
    assert set_id_from_url('https://www.mza.cz/scitacioperaty/digisada/detail/4422/')=='census:4422'
    for bad in ('http://www.mza.cz/scitacioperaty/digisada/detail/1','https://evil.example/scitacioperaty/digisada/detail/1',
                'https://www.mza.cz/scitacioperaty/digisada/search'):
        with pytest.raises(ResearchError) as e:set_id_from_url(bad)
        assert e.value.code=='INVALID_SOURCE'

@pytest.mark.parametrize('name,code',[('Blansko','226201010'),('SOkA Blansko','226201010'),('blansko','226201010'),
    ('MZA v Brně','216000010'),('zdar nad sazavou','226105010')])
def test_archive_names(name,code):
    assert archive_code(name)==code

def test_unknown_archive_lists_the_options():
    with pytest.raises(ResearchError) as e:archive_code('Praha')
    assert e.value.code=='INVALID_ARGUMENT' and 'Blansko' in e.value.details['allowed'] and len(ARCHIVES)==16

def test_search_form_is_fixed():
    assert search_form('cobec','Lhota','226201010',1890)=={'archiv':'226201010','obec':'','cobec':'Lhota',
        'ulice':'','rscitan':'1890','sumar':'','submit':'Vyhledat'}
    assert list(search_form('obec','X',None,None))==['archiv','obec','cobec','ulice','rscitan','sumar','submit']

@pytest.mark.parametrize('range_text,detail,numbers,parsed',[
    ('1-40','1-38, 40',[*range(1,39),40],True),
    ('1-5','1-5',[1,2,3,4,5],True),
    ('1-5','1-3, 5a',[1,2,3],False),
    (None,None,[],False),
])
def test_house_numbers(range_text,detail,numbers,parsed):
    hn=parse_house_numbers(range_text,detail)
    assert hn.numbers==numbers and hn.parsed is parsed

@pytest.mark.parametrize('house,expected',[('39',False),('40',True),('12a',None),('č. 12',None),('',None),(None,None)])
def test_house_coverage(house,expected):
    assert house_covered(parse_house_numbers('1-40','1-38, 40'),house) is expected

def test_house_coverage_with_unparsed_detail_uses_range():
    hn=parse_house_numbers('1-5','1-3, 5a')
    assert house_covered(hn,'2') is True and house_covered(hn,'9') is False and house_covered(hn,'4') is None

def test_results_rows_and_total():
    rows,total=parse_results(results([D.SET_LHOTA_1890,D.SET_PASEKA_1890,D.SET_LHOTA_1921]))
    assert total==3 and [r.source_id for r in rows]==['census:9001','census:9002','census:9003']
    r=rows[1]
    assert (r.place,r.part,r.year,r.code,r.archive)==('Demo Lhota','Demo Paseka',1890,'BK9002','Blansko')
    assert r.house_numbers.numbers==list(range(1,13)) and r.detail_url.endswith('/digisada/detail/9002')
    assert rows[2].images_available is False and rows[0].images_available is True

@pytest.mark.parametrize('page',[D.results_page([],0,0),
    '<html><div class="card"><h2>Vyhledané záznamy</h2><p>Nebyly nalezeny žádné záznamy.</p></div></html>',
    # Shape of the live app's zero-results page (a village without a 1900 census): empty list, no "celkem".
    '<html><div class="card"><h2 class="card-header">\n Vyhledané záznamy </h2><ul class="list-group">\n </ul>'
    '<div class="card-footer"><span>Nebyly nalezeny žádné vyhovující záznamy.</span></div></div></html>'])
def test_recognised_zero_results(page):
    assert parse_results(page)==([],0)

@pytest.mark.parametrize('page',['<html><form></form>SYNTHETIC form without results</html>',
    '<html><li class="list-group-item"><span class="small text-muted ">Město/obec</span><p>X</p></li></html>',
    '<html><body><form></form><p>celkem: 0</p></body></html>',
    '<html><body><table><tr><td>Foo</td></tr></table><footer>záznam nebyl nalezen</footer></body></html>'])
def test_unrecognised_results_page_is_parse_error(page):
    # Session gone → form without results; "0 results" text outside the
    # results card must not be trusted — it can be leftover/unrelated markup).
    with pytest.raises(ResearchError) as e:parse_results(page)
    assert e.value.code=='PARSE_ERROR'

def test_next_page_url():
    ids=sorted(i for i in D.SETS if D.SETS[i][0]==D.BIG_PLACE)
    first=results(ids).encode()
    url='https://www.mza.cz/scitacioperaty/digisada/advanced_search'
    assert next_page_url(first,url)==url+'/10'
    assert next_page_url(results(ids,10).encode(),url+'/10')==url+'/20'
    assert next_page_url(results(ids,20).encode(),url+'/20') is None

def test_detail_metadata_pages_and_citation():
    unit,entries=parse_set_detail(D.detail_page(D.SET_PASEKA_1890),'9002')
    assert unit.source_id=='census:9002' and unit.code=='BK9002' and unit.part=='Demo Paseka'
    assert unit.metadata['Číslo NAD']=='999' and 'Signatura' not in unit.metadata
    assert len(entries)==2 and entries[0][0].startswith('https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/DEMO/')
    citation=build_citation(unit)
    assert citation=='SOkA Blansko; SYNTHETIC Okresní úřad Testov (NAD 999); inv. č. 77; kód sady BK9002; sčítání 1890; Demo Lhota, Demo Paseka'
    pages=set_pages(unit,entries,citation)
    assert [p.page_id for p in pages]==['census:9002:1','census:9002:2']
    assert pages[0].source=='census' and pages[0].source_type=='iiif' and pages[0].viewer_url==unit.detail_url
    assert pages[0].citation==citation and len({p.manifest_sha256 for p in pages})==1

def test_detail_without_scans_and_restricted_year():
    # An empty CreateSeadragon([],[],0) still parses, just with no entries.
    unit,entries=parse_set_detail(D.detail_page(D.SET_NO_SCANS),'9005')
    assert entries==[] and unit.images_available is True
    unit,_=parse_set_detail(D.detail_page(D.SET_LHOTA_1921),'9003')
    assert unit.images_available is False

@pytest.mark.parametrize('page',['<html><main>nothing</main></html>',
    D.detail_page(D.SET_PASEKA_1890).replace('Rok sčítání','Něco jiného')])
def test_invalid_detail_is_parse_error(page):
    with pytest.raises(ResearchError) as e:parse_set_detail(page,'9002')
    assert e.value.code=='PARSE_ERROR'

def test_missing_manifest_script_is_parse_error():
    # No CreateSeadragon( anywhere is a different, unparseable page, not "no scans".
    page=D.detail_page(D.SET_LHOTA_1890).replace('CreateSeadragon(','NotAManifestCall(')
    with pytest.raises(ResearchError) as e:parse_set_detail(page,'9001')
    assert e.value.code=='PARSE_ERROR'

@pytest.mark.parametrize('display,year,available',[
    ('1921 (sumář)',1921,False),
    ('1921/22',1921,False),
    ('',None,False),
    ('1890',1890,True),
])
def test_images_available_fails_closed_on_restricted_or_unknown_year(display,year,available):
    # `images_available` must fail closed for an unknown year and for a restricted
    # year buried anywhere in the raw header text, not just an exact "1921" match.
    page=D.detail_page(D.SET_LHOTA_1921).replace(
        '<span class="font-weight-bolder">1921</span>', f'<span class="font-weight-bolder">{display}</span>')
    unit,_=parse_set_detail(page,'9003')
    assert unit.year==year and unit.images_available is available

def test_manifest_with_mismatched_arrays_is_parse_error():
    page=D.detail_page(D.SET_PASEKA_1890)
    broken=page.replace(', "226201010-BK9002-00020.jp2"]',']')
    assert broken!=page
    with pytest.raises(ResearchError) as e:parse_set_detail(broken,'9002')
    assert e.value.code=='PARSE_ERROR'

def test_archive_warnings_flags_missing_archive():
    got=[CensusSet(source_id='census:1',archive=None,detail_url='https://x'),
         CensusSet(source_id='census:2',archive='Blansko',detail_url='https://y')]
    warnings=archive_warnings('Demo Lhota',got)
    assert any('chybí údaj o archivu' in w for w in warnings)

def test_archive_warnings_flags_multiple_archives():
    got=[CensusSet(source_id='census:1',archive='Blansko',detail_url='https://x'),
         CensusSet(source_id='census:2',archive='Havlíčkův Brod',detail_url='https://y')]
    warnings=archive_warnings('Demo Lhota',got)
    assert any('více archivů' in w for w in warnings) and not any('chybí údaj' in w for w in warnings)

def test_archive_warnings_empty_when_consistent():
    got=[CensusSet(source_id='census:1',archive='Blansko',detail_url='https://x')]
    assert archive_warnings('Demo Lhota',got)==[]

def test_public_set_omits_house_numbers_and_keeps_the_rest():
    unit,_=parse_set_detail(D.detail_page(D.SET_PASEKA_1890),'9002')
    dumped=public_set(unit)
    assert 'numbers' not in dumped['house_numbers']
    assert dumped['house_numbers']['raw']==unit.house_numbers.raw
    assert dumped['source_id']==unit.source_id

def test_long_source_text_is_clipped():
    page=results([D.SET_LHOTA_1890]).replace('SYNTHETIC Okresní úřad Testov','X'*5000)
    rows,_=parse_results(page)
    assert len(rows[0].fund)==500

@pytest.mark.parametrize('years,status',[((1880,1900),'candidate'),((1900,1910),'excluded'),((None,None),'needs_review')])
def test_relevance(years,status):
    rows,_=parse_results(results([D.SET_LHOTA_1890]))
    q=SearchQuery(raw_query='x',year_from=years[0],year_to=years[1],person={'house_number':'40'})
    rel=census_relevance(rows[0],q)
    assert rel['status']==status and rel['house_number_covered'] is True

def test_sort_prefers_covered_houses_then_year():
    rows,_=parse_results(results([D.SET_LHOTA_1890,D.SET_PASEKA_1890,D.SET_HB_1890]))
    q=SearchQuery(raw_query='x',year_from=1880,year_to=1900,person={'house_number':'15'})
    ranked=sorted(({**r.model_dump(),'relevance':census_relevance(r,q)} for r in rows),key=sort_key)
    assert [r['source_id'] for r in ranked]==['census:9001','census:9004','census:9002']

@pytest.fixture
async def census(cfg,store):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    yield CensusCatalog(source,store),archive
    await source.close()

@pytest.mark.asyncio
async def test_catalog_places_from_both_fields(census,store,sid):
    cat,archive=census
    places=await cat.places(sid,'alice','Demo')
    assert {p.source_id for p in places}=={'census:obec:Demo Lhota','census:obec:Demo Velkoves','census:cobec:Demo Paseka'}
    assert store.has_link(sid,'alice','place_option','census:cobec:Demo Paseka')

@pytest.mark.asyncio
async def test_catalog_sets_with_homonym_warning(census,store,sid):
    cat,archive=census
    sets,complete,warnings=await cat.sets(sid,'alice',[('obec','Demo Lhota')],None,None)
    assert complete and {s.source_id for s in sets}=={'census:9001','census:9002','census:9003','census:9004','census:9005'}
    assert any('archive' in w for w in warnings)
    assert store.has_link(sid,'alice','book_option','census:9004')
    sets,_,warnings=await cat.sets(sid,'alice',[('obec','Demo Lhota')],'226201010',1890)
    assert [s.source_id for s in sets]==['census:9001','census:9002'] and warnings==[]
    assert all(p['archiv']==['226201010'] for p in archive.census_sessions.values() if p.get('rscitan')==['1890'])

@pytest.mark.asyncio
async def test_catalog_paging_and_truncation(census,store,sid,monkeypatch):
    cat,archive=census
    sets,complete,_=await cat.sets(sid,'alice',[('obec',D.BIG_PLACE)],None,None)
    assert len(sets)==25 and complete
    import rubrika.census as C
    monkeypatch.setattr(C,'MAX_PAGES',2)
    sets,complete,warnings=await CensusCatalog(cat.source,store).sets(sid,'alice',[('obec',D.BIG_PLACE)],None,1880)
    assert len(sets)==20 and not complete and any('Zužte' in w for w in warnings)

@pytest.mark.asyncio
async def test_catalog_open_set(census,store,sid):
    cat,archive=census
    unit,pages,warnings,citation=await cat.open_set(sid,'alice','9001')
    assert unit.code=='BK9001' and len(pages)==3 and pages[0].citation==citation and warnings==[]
    assert store.entity('page','census:9001:1')['source']=='census'

@pytest.mark.asyncio
async def test_restricted_year_is_metadata_only_without_image_traffic(census,store,sid):
    cat,archive=census
    with pytest.raises(ResearchError) as e:await cat.open_set(sid,'alice','9003')
    assert e.value.code=='UNSUPPORTED_SOURCE' and e.value.details['reason']=='reading_room_only'
    assert e.value.details['citation'] and e.value.details['set']['year']==1921
    assert not any('iipsrv' in r for r in archive.requests)

@pytest.mark.asyncio
async def test_restricted_year_text_variant_fails_closed_without_image_traffic(census,store,sid):
    # "1921 (sumář)" must be caught even though it isn't a plain "1921" year.
    cat,archive=census
    with pytest.raises(ResearchError) as e:await cat.open_set(sid,'alice',str(D.SET_LHOTA_1921_TEXT))
    assert e.value.code=='UNSUPPORTED_SOURCE' and e.value.details['reason']=='reading_room_only'
    assert not any('iipsrv' in r for r in archive.requests)

@pytest.mark.asyncio
async def test_unknown_year_fails_closed_with_year_unknown_reason(census,store,sid):
    # An unrecognisable "Rok sčítání" must fail closed too, with its own reason.
    cat,archive=census
    with pytest.raises(ResearchError) as e:await cat.open_set(sid,'alice',str(D.SET_YEAR_UNKNOWN))
    assert e.value.code=='UNSUPPORTED_SOURCE' and e.value.details['reason']=='year_unknown'
    assert not any('iipsrv' in r for r in archive.requests)

@pytest.mark.asyncio
async def test_set_without_scans_and_missing_set(census,store,sid):
    cat,archive=census
    with pytest.raises(ResearchError) as e:await cat.open_set(sid,'alice','9005')
    assert e.value.code=='UNSUPPORTED_SOURCE' and e.value.details['reason']=='no_images'
    with pytest.raises(ResearchError) as e:await cat.open_set(sid,'alice','123456')
    assert e.value.code=='NOT_FOUND'
