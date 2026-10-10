import base64
import io

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from conftest import call
from rubrika import demo_pf as D
from rubrika import demo_pf_more as M
from rubrika import pf as P
from rubrika import archives as A
from rubrika.archives import ARCHIVES
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.errors import ResearchError
from rubrika.models import HouseNumbers, SearchQuery
from rubrika.network import SafeSource, SourcePolicy
from rubrika.service import ResearchService
from rubrika.storage import Store

HOST = 'www.portafontium.eu'
PF = 'https://' + HOST


@pytest.fixture
def clock(monkeypatch):
    now = [1_800_000_000.0]
    import rubrika.network as N
    monkeypatch.setattr('rubrika.storage.time.time', lambda: now[0])
    async def fake_sleep(s):
        now[0] += s
    monkeypatch.setattr(N.asyncio, 'sleep', fake_sleep)
    return now


def make_fixture(**flags):
    @pytest_asyncio.fixture
    async def fixture(tmp_path, clock):
        cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, pf_enabled=True, request_interval=0, **flags)
        store = Store(cfg)
        archive = DemoArchive()
        source = SafeSource(cfg, store, transport=httpx.MockTransport(archive.handle), source_mode='synthetic_demo')
        service = ResearchService(cfg, store, source)
        yield service, archive
        await service.close()
        store.close()
    return fixture


pf = make_fixture(pf_land_records_enabled=True, pf_census_enabled=True)
pf_off = make_fixture()


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC pf records', 'place_hint': 'Demolov', **query})
    return out.data['research_id']


# ---- pure parsers -------------------------------------------------------------------------------------------

def test_parse_years_known_uncertain_unknown():
    known = P.parse_years('1567-1644, 1752')
    assert known.status == 'known' and [(s.start, s.end) for s in known.spans] == [(1567, 1644), (1752, 1752)]
    assert P.parse_years('1571–1652').spans[0].end == 1652
    assert P.parse_years('kolem 1800').status == 'uncertain'
    assert P.parse_years('1700-1600').status == 'uncertain'
    assert P.parse_years('').status == 'unknown'


def test_census_place_takes_the_municipality_and_keeps_parts():
    assert P.census_place(['Chodov - Pec (Meigelshof - Hochofen)'], '') == ('Chodov (Meigelshof)', 'Pec')
    assert P.census_place(['Domažlice', 'Bezděkovské předměstí (Taus, Bezděkauer Vorstadt)'], '') == \
        ('Domažlice', 'Bezděkovské předměstí (Taus, Bezděkauer Vorstadt)')
    assert P.census_place([], 'Census 1921 Tachov (Tachau), Nr. 652') == ('Tachov (Tachau)', None)
    assert P.census_place([], 'Census 1890 Tachov (Tachau) - souhrn / Sammelbogen') == ('Tachov (Tachau)', None)


def test_house_numbers_from_the_portal_list():
    hn = P.house_numbers_of(['Nr. 1', 'Nr. 2', '(Nr. 5 - Nr. 7)'])
    assert hn.numbers == [1, 2, 5, 6, 7] and hn.parsed and hn.raw == '1-2, 5-7' and (hn.start, hn.end) == (1, 7)
    unknown = P.house_numbers_of(['Nr. 1', 'Souhrn'])
    assert unknown.numbers == [1] and unknown.parsed is False       # a label not understood: never reported as not covered
    assert P.label_numbers('Nr. 12') == [12] and P.label_numbers('(Nr. 1 - Nr. 500)') == list(range(1, 501)) and P.label_numbers('x') is None
    houses = [('(Nr. 1 - Nr. 500)', '11'), ('Nr. 12', '12'), ('Nr. 501', '13')]
    assert P.pick_house(houses, 12) == ('Nr. 12', '12') and P.pick_house(houses, 40) == ('(Nr. 1 - Nr. 500)', '11')
    assert P.pick_house(houses, 999) is None and P.pick_house(houses, None) is None


def test_ids_and_urls():
    assert P.book_id_from_url(PF + '/amtsbuch/soap-pn/vs-dolni-lukavice/k076') == 'pf:amtsbuch:soap-pn/vs-dolni-lukavice/k076'
    assert P.book_id_from_url(PF + '/census/soap-tc/1921/tachov/cp652') == 'pf:census:soap-tc/1921/tachov'
    assert P.book_id_from_url(PF + '/register/soap-pn/abertamy-04') == 'pf:book:soap-pn/abertamy-04'
    with pytest.raises(ResearchError):
        P.book_id_from_url(PF + '/census/soap-tc/1921/tachov/cp652?x=1')
    assert P.record_ref('pf:census:soap-do/1869/chodov@12') == ('census', 'soap-do/1869/chodov', 12)
    assert P.record_ref('pf:amtsbuch:soap-pn/a/b') == ('amtsbuch', 'soap-pn/a/b', None)
    with pytest.raises(ResearchError):
        P.record_ref('pf:census:soap-do/x/chodov')


def test_search_urls_per_tab():
    assert 'field_book_type%5B%5D=grundbuch' in P.search_url('Lukavice', 0, ('grundbuch',), 'amtsbuch')
    census = P.search_url('Chodov', 2, (), 'census')
    assert census.startswith(PF + '/searching/census?mode=0&') and census.endswith('&page=2')
    assert P.search_url('Plzeň').startswith(PF + '/searching/register?')


def test_record_relevance_never_excludes_uncertain_data():
    q = SearchQuery(raw_query='x', year_from=1800, year_to=1810)
    rows = {b[0]: P.parse_records(M.search_page('amtsbuch', {'search_api_views_fulltext_1': ['Demolov']}, 50), PF + '/searching/amtsbuch', 'amtsbuch') for b in M.AMTSBUCH[:1]}
    recs = {r.source_id: r for r in next(iter(rows.values())).records}
    rel = {k.split('/')[-1]: P.record_relevance(r, q)['status'] for k, r in recs.items()}
    assert rel == {'k001': 'candidate', 'k002': 'excluded', 'k003': 'needs_review', 'k011': 'excluded'}
    assert P.record_relevance(recs['pf:amtsbuch:soap-pn/vs-demolov/k001'], SearchQuery(raw_query='x'))['status'] == 'needs_review'


def test_parse_records_groups_1921_house_rows_into_one_unit():
    html = M.search_page('census', {'search_api_views_fulltext_1': ['Demolov'], 'mode': ['0']}, 50)
    page = P.parse_records(html, PF + '/searching/census', 'census')
    ids = [r.source_id for r in page.records]
    assert 'pf:census:soap-tc/1921/demolov' in ids and sum(i.startswith('pf:census:soap-tc/1921') for i in ids) == 1
    group = next(r for r in page.records if r.source_id == 'pf:census:soap-tc/1921/demolov')
    assert group.year == 1921 and group.places == ['Demolov (Demolau)'] and group.detail_path == '/census/soap-tc/1921/demolov/cp5'
    assert group.house_numbers.numbers == [5, 6, 7] and group.house_numbers.parsed is False and group.number == 'Census 1921 Demolov (Demolau)'
    pec = next(r for r in page.records if r.source_id.endswith('demolov-pec'))
    assert pec.places == ['Demolov (Demolau)'] and pec.part == 'Pec'
    suburb = next(r for r in page.records if r.source_id.endswith('demolov-predmesti'))
    assert suburb.places == ['Demolov'] and suburb.part.startswith('Dolejší')


def test_parse_records_refuses_unknown_structure():
    with pytest.raises(ResearchError) as e:
        P.parse_records('<html><body>nic</body></html>', '', 'census')
    assert e.value.code == 'PARSE_ERROR'
    html = M.search_page('amtsbuch', {'search_api_views_fulltext_1': ['Lhota']}, 50).replace('/amtsbuch/soap-pn/vs-lhota/k020', '/evil/x')
    with pytest.raises(ResearchError):
        P.parse_records(html, PF + '/searching/amtsbuch', 'amtsbuch')


# ---- routes and registry ------------------------------------------------------------------------------------

BASE = ('search_api_views_fulltext=&search_api_views_fulltext_1=Lukavice&search_api_views_fulltext_2=&search_api_aggregation_3='
        '&field_doc_dates_field_doc_dates_from=')


@pytest.mark.parametrize('url,source', [
    (f'{PF}/searching/amtsbuch?{BASE}', 'pf_land'),
    (f'{PF}/searching/amtsbuch?{BASE}&field_book_type%5B%5D=grundbuch&page=1', 'pf_land'),
    (f'{PF}/searching/census?mode=0&{BASE}', 'pf_census'),
    (f'{PF}/amtsbuch/soap-pn/vs-dolni-lukavice/k076', 'pf_land'),
    (f'{PF}/census/soap-do/1869/chodov', 'pf_census'),
    (f'{PF}/census/soap-tc/1921/tachov/cp652', 'pf_census'),
    (f'{PF}/fcgi-bin/iipsrv.fcgi?IIIF=amtsbuch/soap-pn/a/b/c/x_0010.jp2/info.json', 'pf_land_images'),
    (f'{PF}/fcgi-bin/iipsrv.fcgi?DeepZoom=census/soap-do/census-1869-okres-domazlice/chodov/cp001/x_0010.jp2.dzi', 'pf_census_images'),
    (f'{PF}/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/a/b/c.jp2/info.json', 'pf_images'),
])
def test_new_routes_allowed(url, source):
    route = SourcePolicy(A.integrated_hosts()).validate(url, 'GET')
    assert (route.source, route.archive) == (source, 'pf') and route.group in ('pf_catalog', 'pf_images')


@pytest.mark.parametrize('url', [
    f'{PF}/searching/census?{BASE}',                                     # without mode=0 the tab lists nothing: not needed
    f'{PF}/searching/census?mode=1&{BASE}',
    f'{PF}/searching/census?mode=0&{BASE}&field_book_type%5B%5D=grundbuch',
    f'{PF}/searching/amtsbuch?{BASE}&field_book_type%5B%5D=evil',
    f'{PF}/searching/amtsbuch?{BASE}&field_book_type%5B%5D=grundbuch&field_book_type%5B%5D=grundbuch',
    f'{PF}/searching/amtsbuch?{BASE}&field_register_type%5B%5D=register-birth',
    f'{PF}/searching/amtsbuch?{BASE}&order=field_archives',
    f'{PF}/searching/census?mode=0&{BASE}&field_doc_place_field_doc_place_location_latlon_op=5',
    f'{PF}/amtsbuch/soap-pn/vs-a',
    f'{PF}/amtsbuch/soap-pn/vs-a/k1/extra',
    f'{PF}/amtsbuch/soap-pn/vs-a/k1?x=1',
    f'{PF}/census/soap-do/abcd/chodov',
    f'{PF}/census/soap-do/1869',
    f'{PF}/census/soap-do/1869/a/b/c',
    f'{PF}/fcgi-bin/iipsrv.fcgi?IIIF=census/../x/info.json',
    f'{PF}/fcgi-bin/iipsrv.fcgi?IIIF=other/soap-do/x.jp2/info.json',
    f'{PF}/fcgi-bin/iipsrv.fcgi?IIIF=amtsbuch/a%2Fb/info.json',
])
def test_new_routes_forbidden(url):
    with pytest.raises(ResearchError) as e:
        SourcePolicy(A.integrated_hosts()).validate(url, 'GET')
    assert e.value.code == 'INVALID_SOURCE'


def test_registry_and_switches():
    a = ARCHIVES['pf']
    assert a.kinds == ('parish_registers', 'land_records', 'census')
    assert dict(a.kind_settings) == {'land_records': 'pf_land_records_enabled', 'census': 'pf_census_enabled'}
    s = Settings(_env_file=None)
    assert s.pf_land_records_enabled is False and s.pf_census_enabled is False


# ---- land books ---------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_land_records_find_list_open_view(pf):
    service, archive = pf
    sid = await research(service, year_from=1800, year_to=1810)
    places = await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    assert places.data['searched'] == ['pf'] and '§ 92' in places.data['terms_notice']
    assert [p['source_id'] for p in places.data['places']] == ['pf:place:demolov', 'pf:place:horni-demolov']
    assert any('/searching/amtsbuch' in u for u in archive.requests) and not any('/searching/register' in u for u in archive.requests)

    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    d = listed.data
    assert d['archive'] == 'pf' and d['kind'] == 'land_records' and d['listing_complete'] is True and 'osobní potřeb' in d['terms_notice']
    rows = {r['source_id'].split('/')[-1]: r for r in d['sources']}
    assert set(rows) == {'k001', 'k002', 'k003', 'k011'}
    assert rows['k001']['category'] == 'land_book' and rows['k011']['category'] == 'land_book'
    assert rows['k002']['category'] == 'other' and rows['k003']['category'] == 'other'
    assert [rows[k]['relevance']['status'] for k in ('k001', 'k003', 'k002', 'k011')] == ['candidate', 'needs_review', 'excluded', 'excluded']
    assert [r['source_id'].split('/')[-1] for r in d['sources']][:2] == ['k001', 'k003']
    assert rows['k001']['dating']['spans'] == [{'start': 1764, 'end': 1853}] and rows['k003']['dating']['status'] == 'uncertain'
    # two passes: the portal's grundbuch filter (land books) and the unfiltered search (the other official books)
    searches = [u for u in archive.requests if '/searching/amtsbuch' in u and 'Demolov' in u]
    assert any('field_book_type' in u for u in searches) and any('field_book_type' not in u for u in searches)

    opened = await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-demolov/k001')
    o = opened.data
    assert o['archive'] == 'pf' and o['kind'] == 'land_records' and o['total'] == 3
    assert o['citation'] == ('Státní oblastní archiv v Plzni, Velkostatek Demolov, sign. K 1, '
                             'https://www.portafontium.eu/amtsbuch/soap-pn/vs-demolov/k001')
    assert o['unit']['book_types'] == ['pozemková kniha'] and o['unit']['category'] == 'land_book' and '§ 92' in o['terms_notice']
    page = o['pages'][0]
    assert page['page_id'] == 'pf:amtsbuch:soap-pn/vs-demolov/k001:1' and page['source_type'] == 'iiif' and page['source'] == 'pf'
    assert page['viewer_url'] == f'{PF}/iipimage/{M.amts_nid("soap-pn/vs-demolov/k001")}/{M.amts_keys("soap-pn/vs-demolov/k001")[0]}'
    assert page['source_url'].startswith(PF + '/fcgi-bin/iipsrv.fcgi?IIIF=amtsbuch/soap-pn/vs-demolov/k001/')

    view = await call(service, 'view_scan', research_id=sid, page_id=page['page_id'])
    assert view.data['image']['archive'] == 'pf' and view.data['image']['citation'] == o['citation']
    urls = [u for u in archive.requests if HOST in u and 'fcgi-bin' in u]
    assert urls and all('IIIF=amtsbuch/' in u for u in urls) and not any('DeepZoom' in u for u in urls)


@pytest.mark.asyncio
async def test_land_records_detail_book_type_settles_the_category(pf):
    service, _ = pf
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-demolov/k002')
    assert opened.data['unit']['book_types'] == ['urbář'] and opened.data['unit']['category'] == 'other'
    assert opened.data['unit']['category_certain'] is True


@pytest.mark.asyncio
async def test_land_records_town_district_rule(pf):
    service, _ = pf
    sid = await research(service, place_hint='Mestov')
    places = await call(service, 'find_place', research_id=sid, term='Mestov', kind='land_records', archives=['pf'])
    labels = {p['source_id']: p['label'] for p in places.data['places']}
    assert labels['pf:place:mestov'].endswith('– včetně obvodů města')
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:mestov'], kind='land_records')
    assert {r['source_id'].split('/')[-1] for r in listed.data['sources']} == {'k100', 'k101'}


@pytest.mark.asyncio
async def test_land_records_page_cut_marks_the_category_unverified(pf, monkeypatch):
    service, _ = pf
    monkeypatch.setattr(P, 'MAX_SEARCH_PAGES', 1)
    monkeypatch.setattr(D, 'ROWS_PER_PAGE', 1)
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    assert listed.data['listing_complete'] is False and any('limit 1' in w for w in listed.data['warnings'])
    assert any('vyčerpán limit' in w or 'limit 1' in w for w in listed.data['warnings'])
    assert all(r['category_certain'] is False for r in listed.data['sources'] if r['category'] == 'other')


@pytest.mark.asyncio
async def test_land_records_year_filter_keeps_undated(pf):
    service, _ = pf
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records', year_from=1800, year_to=1810)
    assert {r['source_id'].split('/')[-1] for r in listed.data['sources']} == {'k001', 'k003'}


@pytest.mark.asyncio
async def test_land_records_user_link_opens_without_listing(pf):
    service, _ = pf
    sid = await research(service, book_url=PF + '/amtsbuch/soap-pn/vs-testov/k010')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-testov/k010')
    assert opened.data['total'] == 2 and opened.data['unit']['signature'] == 'K 10' and opened.data['unit']['category'] == 'land_book'


# ---- census -------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_census_find_list_open_by_house(pf):
    service, archive = pf
    sid = await research(service, year_from=1869, year_to=1869, person={'house_number': '2'})
    places = await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    assert places.data['places'][0]['source_id'] == 'pf:place:demolov'
    assert any('/searching/census' in u and 'mode=0' in u for u in archive.requests)

    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1869, year_to=1869)
    d = listed.data
    assert d['kind'] == 'census' and d['listing_complete'] is True and '§ 92' in d['terms_notice']
    ids = [r['source_id'] for r in d['sources']]
    assert ids[:2] == ['pf:census:soap-do/1869/demolov', 'pf:census:soap-do/1869/demolov-pec']
    assert len(ids) == 2 and 'pf:census:soap-do/1880/demolov' not in ids            # year filter; Zadní Demolov is another place
    row = d['sources'][0]
    assert row['year'] == 1869 and row['relevance']['status'] == 'candidate' and row['relevance']['house_number_covered'] is None
    assert row['places'] == ['Demolov (Demolau)']

    opened = await call(service, 'open_source', research_id=sid, source_id=ids[0])
    o = opened.data
    assert o['kind'] == 'census' and o['unit']['selected_house'] == 'Nr. 2' and o['unit']['house_numbers']['numbers'] == [1, 2, 3]
    assert o['total'] == 2 and o['citation'].startswith('Státní oblastní archiv v Plzni, OÚ Demolov, I, Census 1869 Demolov (Demolau), Nr. 2, inv. č. i725')
    assert o['unit']['source_id'] == ids[0] + '@2' and o['pages'][0]['page_id'] == f'{ids[0]}@2:1' and 'census/soap-do/census-1869-okres-demolov/demolov/cp002/' in o['pages'][0]['source_url']
    assert 'pf:census:soap-do/1869/demolov@<číslo domu>' in o['note'] and '1-3' in o['note']
    view = await call(service, 'view_scan', research_id=sid, page_id=o['pages'][0]['page_id'])
    assert view.data['image']['archive'] == 'pf'
    assert all('IIIF=census/' in u for u in archive.requests if HOST in u and 'fcgi-bin' in u)


@pytest.mark.asyncio
async def test_census_house_suffix_and_missing_house(pf):
    service, _ = pf
    sid = await research(service, year_from=1880, year_to=1880)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1880, year_to=1880)
    first = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-do/1880/demolov')
    assert first.data['unit']['selected_house'] == 'Nr. 1'
    assert first.data['unit']['house_numbers']['numbers'] == [1, 2, 4] and first.data['unit']['house_numbers']['parsed'] is True
    at4 = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-do/1880/demolov@4')
    assert at4.data['unit']['selected_house'] == 'Nr. 4' and at4.data['pages'][0]['page_id'].startswith('pf:census:soap-do/1880/demolov@4:')
    assert 'cp004' in at4.data['pages'][0]['source_url']
    at3 = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-do/1880/demolov@3')
    assert at3.data['unit']['selected_house'] == 'Nr. 1' and any('Číslo 3 není v rozpisu' in w for w in at3.data['warnings'])


@pytest.mark.asyncio
async def test_census_1921_group_opens_through_the_first_node(pf):
    service, archive = pf
    sid = await research(service, year_from=1921, year_to=1921, person={'house_number': '6'})
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1921, year_to=1921)
    assert [r['source_id'] for r in listed.data['sources']] == ['pf:census:soap-tc/1921/demolov']
    assert listed.data['sources'][0]['relevance']['house_number_covered'] is None or listed.data['sources'][0]['relevance']['house_number_covered'] is True
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-tc/1921/demolov')
    assert opened.data['unit']['selected_house'] == 'Nr. 6' and 'cp006' in opened.data['pages'][0]['source_url']
    assert any(u.endswith('/census/soap-tc/1921/demolov/cp5') for u in archive.requests)


@pytest.mark.asyncio
async def test_census_needs_one_year_and_other_kinds_are_unaffected(pf):
    service, _ = pf
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1869, year_to=1880))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov'], kind='census', fund_marks=['a']))
    assert out.is_error


@pytest.mark.asyncio
async def test_registers_still_work_with_the_kind_switches_on(pf):
    service, _ = pf
    sid = await research(service, event_type='birth', year_from=1785, year_to=1790)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    assert listed.data['sources'][0]['source_id'] == 'pf:book:soap-pn/demolov-01'
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:book:soap-pn/demolov-01')
    assert opened.data['kind'] == 'parish_registers'


# ---- switches -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kind_switches_default_off_make_no_request(pf_off):
    service, archive = pf_off
    sid = await research(service)
    for kind in ('land_records', 'census'):
        out = await call(service, 'find_place', research_id=sid, term='Demolov', kind=kind, archives=['pf'])
        assert out.data['places'] == [] and out.data['searched'] == [] and out.data['not_searched'][0]['archive'] == 'pf'
        bad = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov'], kind=kind))
        assert bad.is_error and bad.data['error']['code'] == 'LIVE_DISABLED'
    assert not any(HOST in u for u in archive.requests)
    rows = {a['code']: a for a in (await call(service, 'list_archives')).data['archives']}
    assert rows['pf']['kinds'] == ['parish_registers', 'land_records', 'census'] and rows['pf']['available_kinds'] == ['parish_registers']


@pytest.mark.asyncio
async def test_one_switch_does_not_open_the_other_kind(tmp_path, clock):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, pf_enabled=True, request_interval=0, pf_census_enabled=True)
    store = Store(cfg)
    archive = DemoArchive()
    service = ResearchService(cfg, store, SafeSource(cfg, store, transport=httpx.MockTransport(archive.handle), source_mode='synthetic_demo'))
    try:
        sid = await research(service, book_url=PF + '/amtsbuch/soap-pn/vs-testov/k010')
        out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-testov/k010'))
        assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'
        assert not any('/amtsbuch/' in u for u in archive.requests)
        ok = await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
        assert ok.data['searched'] == ['pf']
    finally:
        await service.close()
        store.close()


@pytest.mark.asyncio
async def test_page_of_a_disabled_kind_is_not_fetched(pf):
    service, archive = pf
    sid = await research(service, book_url=PF + '/amtsbuch/soap-pn/vs-testov/k010')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-testov/k010')
    service.cfg.pf_land_records_enabled = False
    before = len(archive.requests)
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=opened.data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED' and len(archive.requests) == before


@pytest.mark.asyncio
async def test_records_appear_in_status_and_tree(pf):
    service, _ = pf
    sid = await research(service, year_from=1800, year_to=1810)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:soap-pn/vs-demolov/k001')
    status = (await call(service, 'get_research_status', research_id=sid)).data
    coverage = status['coverage']
    opened = coverage['opened_books'][0]
    assert opened['source'] == 'pf' and opened['book_id'] == 'pf:amtsbuch:soap-pn/vs-demolov/k001' and opened['title']
    options = coverage['catalog_books_not_opened']
    assert {o['book_id'].split('/')[-1] for o in options} == {'k002', 'k003', 'k011'} and all(o['source'] == 'pf' for o in options)
    src = ResearchService._tree_source({'id': 'pf:census:soap-do/1869/demolov', 'source': 'pf', 'number': 'Census 1869 Demolov',
                                        'detail_url': PF + '/census/soap-do/1869/demolov'})
    assert src['archive'] == ARCHIVES['pf'].name and src['title'] == 'Porta fontium, Census 1869 Demolov'


@pytest.mark.asyncio
async def test_census_effective_house_is_in_the_id_and_citation(pf):
    service, _ = pf
    sid = await research(service, year_from=1921, year_to=1921, person={'house_number': '6'})
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1921, year_to=1921)
    six = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-tc/1921/demolov')
    u = six.data['unit']
    assert u['source_id'] == 'pf:census:soap-tc/1921/demolov@6' and six.data['pages'][0]['page_id'].startswith('pf:census:soap-tc/1921/demolov@6:')
    assert six.data['citation'].endswith('Census 1921 Demolov (Demolau), Nr. 6, inv. č. i725, https://www.portafontium.eu/census/soap-tc/1921/demolov/cp6')
    assert u['detail_url'].endswith('/census/soap-tc/1921/demolov/cp6')
    await call(service, 'revise_research', research_id=sid, query={'raw_query': 'x', 'year_from': 1921, 'year_to': 1921, 'person': {'house_number': '7'}}, expected_revision=1)
    seven = await call(service, 'open_source', research_id=sid, source_id='pf:census:soap-tc/1921/demolov')
    assert seven.data['unit']['source_id'].endswith('@7') and seven.data['pages'][0]['page_id'] != six.data['pages'][0]['page_id']
    assert seven.data['pages'][0]['source_url'] != six.data['pages'][0]['source_url']
    assert u['dating']['status'] == 'known' and u['dating']['spans'] == [{'start': 1921, 'end': 1921}]


@pytest.mark.asyncio
async def test_listing_is_cached_per_research_so_paging_costs_no_request(pf, monkeypatch):
    service, archive = pf
    monkeypatch.setattr(D, 'ROWS_PER_PAGE', 1)
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    first = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records', limit=2)
    n = len(archive.requests)
    assert first.data['next_cursor'] == 2 and n > 4
    second = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records', limit=2, cursor=2)
    assert len(archive.requests) == n and second.data['sources'] and second.data['total'] == first.data['total']
    opened = await call(service, 'open_source', research_id=sid, source_id=second.data['sources'][0]['source_id'])
    assert opened.data['total'] >= 2


@pytest.mark.asyncio
async def test_no_land_book_from_the_filter_is_said_explicitly(pf):
    service, _ = pf
    sid = await research(service, place_hint='Nikolov')
    await call(service, 'find_place', research_id=sid, term='Nikolov', kind='land_records', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:nikolov'], kind='land_records')
    assert [r['category'] for r in listed.data['sources']] == ['other'] and listed.data['sources'][0]['category_certain'] is True
    assert any('Pozemkové knihy portál pro místo Nikolov nevrátil' in w for w in listed.data['warnings'])
    assert 'category_certain=true znamená' in listed.data['note']
    full = await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    ok = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    assert not any('nevrátil' in w for w in ok.data['warnings'])


@pytest.mark.asyncio
async def test_census_note_warns_about_parts_and_unknown_coverage(pf):
    service, _ = pf
    sid = await research(service, year_from=1869, year_to=1869)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['pf'])
    d = (await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='census', year_from=1869, year_to=1869)).data
    assert 'po částech obce' in d['note'] and 'do té doby je house_number_covered neznámé' in d['note']
    assert all(r['dating']['status'] == 'known' for r in d['sources'])


def test_strict_image_keys():
    html = M.viewer_page(M.amts_nid('soap-pn/vs-demolov/k001'))
    assert P.parse_detail(M.amtsbuch_detail('soap-pn/vs-demolov/k001'), 'soap-pn/vs-demolov/k001', 'amtsbuch')['nid']
    other = M.amtsbuch_detail('soap-pn/vs-demolov/k001').replace('_k001_', '_k999_')
    with pytest.raises(ResearchError):
        P.parse_detail(other, 'soap-pn/vs-demolov/k001', 'amtsbuch')
    with pytest.raises(ResearchError):
        P.parse_viewer(html, str(M.amts_nid('soap-pn/vs-demolov/k001')), 'amtsbuch', lambda k: False)


@pytest.mark.asyncio
async def test_book_without_signature_cites_the_inventory_number(pf):
    service, _ = pf
    sid = await research(service, place_hint='Radov')
    await call(service, 'find_place', research_id=sid, term='Radov', kind='land_records', archives=['pf'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:radov'], kind='land_records')
    assert [r['source_id'] for r in listed.data['sources']] == ['pf:amtsbuch:amp/am-radov/i00001']
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:amtsbuch:amp/am-radov/i00001')
    assert opened.data['unit']['signature'] is None and opened.data['total'] == 2
    assert opened.data['citation'].endswith('inv. č. 1, https://www.portafontium.eu/amtsbuch/amp/am-radov/i00001')
    neither = M.amtsbuch_detail('amp/am-radov/i00001').replace('field-name-field-inventory', 'field-name-x')
    with pytest.raises(ResearchError) as e:
        P.parse_detail(neither, 'amp/am-radov/i00001', 'amtsbuch')
    assert e.value.code == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_a_cut_listing_is_not_cached(pf, monkeypatch):
    service, archive = pf
    monkeypatch.setattr(P, 'MAX_SEARCH_PAGES', 1)
    monkeypatch.setattr(D, 'ROWS_PER_PAGE', 1)
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    first = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    assert first.data['listing_complete'] is False
    monkeypatch.setattr(service.source, 'cache_get', lambda *a, **k: None)
    n = len(archive.requests)
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='land_records')
    assert len(archive.requests) > n
