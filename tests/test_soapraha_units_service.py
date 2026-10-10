"""SOA Praha archival records (census, land records) end to end against the synthetic Wicket server."""
import pytest
import pytest_asyncio

from conftest import call
from rubrika import demo_soapraha as D
from rubrika import soapraha as S
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store
import httpx

HOST = 'ebadatelna.soapraha.cz'
LOC = {(l.name, l.community): l.id for l in S.parse_localities(D.arch_search_page(0, 'A' * 32))}
DEMOLOV, HORNI = LOC['Demolov', 'Demolov'], LOC['Horní Demolov', 'Demolov']
TESTOV, LHOTKA_TESTOV = LOC['Testov', 'Testov'], LOC['Testov', 'Lhotka']


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Virtual time: a sleep advances the clock, so the archive's pace is measured, not waited for."""
    now = [1_800_000_000.0]
    import rubrika.network as N
    monkeypatch.setattr('rubrika.storage.time.time', lambda: now[0])
    async def fake_sleep(s):
        now[0] += s
    monkeypatch.setattr(N.asyncio, 'sleep', fake_sleep)
    return now


def build(tmp_path, **settings):
    opts = dict(soapraha_census_enabled=True, soapraha_land_records_enabled=True)
    opts.update(settings)
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, soapraha_enabled=True, request_interval=0, **opts)
    store = Store(cfg)
    archive = DemoArchive()
    source = SafeSource(cfg, store, transport=httpx.MockTransport(archive.handle), source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    service.store_ = store
    return service, archive


@pytest_asyncio.fixture
async def sp(tmp_path):
    service, archive = build(tmp_path)
    yield service, archive
    await service.close()
    service.store_.close()


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC soapraha units', 'place_hint': 'Demolov', **query})
    return out.data['research_id']


async def places(service, sid, term, kind):
    return (await call(service, 'find_place', research_id=sid, term=term, kind=kind, archives=['soapraha'])).data


async def listed(service, sid, place_ids, kind, **kw):
    return (await call(service, 'list_sources', research_id=sid, place_ids=place_ids, kind=kind, **kw)).data


def posts(archive):
    return [p for p in archive.soapraha.posts if 'searchLocality' in p]


@pytest.mark.asyncio
async def test_census_find_list_open_view(sp):
    service, archive = sp
    sid = await research(service, year_from=1895, year_to=1905, person={'house_number': '12'})
    found = await places(service, sid, 'Demolov', 'census')
    assert found['searched'] == ['soapraha'] and found['places'][0]['source_id'] == DEMOLOV
    assert not posts(archive), 'places come from the locality list: no archival search yet'

    data = await listed(service, sid, [DEMOLOV], 'census', digitized_only=False)
    assert data['archive'] == 'soapraha' and data['kind'] == 'census' and data['listing_complete'] is True
    assert posts(archive) == [{'searchLocality': 'Demolov - community Demolov - district Demolov', 'searchArchivalie': '', 'typArchivalie': '19',
                               'mistoUlozeni': '', 'archivniFondId': '', 'dateYearFrom': '1000', 'dateYearTo': '2100'}]
    rows = data['sources']
    by_id = {r['source_id']: r for r in rows}
    assert set(by_id) == {f'soapraha:unit:{i}' for i in (8001, 8002, 8003, 8004, 8007, 8008)}, 'only census sets that list the locality (not Horní Demolov)'
    first = rows[0]
    assert first['source_id'] == 'soapraha:unit:8004' and first['relevance']['status'] == 'candidate' and first['relevance']['house_number_covered'] is True
    assert (first['year'], first['district'], first['municipality']) == (1900, 'Testov', 'Demolov')
    assert first['house_numbers']['start'] == 1 and first['house_numbers']['end'] == 50 and first['house_numbers']['parsed'] is True
    assert first['archive'] == 'soapraha' and first['kind'] == 'census' and first['image_count'] == 2
    assert first['detail_url'] == 'https://ebadatelna.soapraha.cz/pages/ArchivaliePage/archivalieId/8004'
    assert by_id['soapraha:unit:8001']['relevance']['status'] == 'excluded' and by_id['soapraha:unit:8001']['house_numbers']['parsed'] is False
    assert by_id['soapraha:unit:8001']['relevance']['house_number_covered'] is None
    assert by_id['soapraha:unit:8003']['relevance']['status'] == 'excluded'

    opened = (await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8004')).data
    assert opened['archive'] == 'soapraha' and opened['kind'] == 'census' and opened['total'] == 2
    assert opened['citation'] == ('Státní okresní archiv Testov (eBadatelna SOA Praha), fond Okresní úřad Testov 1850-1945, '
                                  'Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50')
    page = opened['pages'][0]
    assert page['page_id'] == 'soapraha:unit:8004:1' and page['viewer_url'] == 'https://ebadatelna.soapraha.cz/a/8004/1' and page['source_type'] == 'jpeg'
    assert 'první dům' in opened['note']
    view = await call(service, 'view_scan', research_id=sid, page_id=page['page_id'])
    ref = view.data['image']
    assert ref['archive'] == 'soapraha' and ref['viewer_url'] == page['viewer_url'] and ref['citation'] == opened['citation']
    assert ref['image_key'] == 'Scitani_lidu_1900:_Okres_Testov_-_obec_Demolov,_cp._1-50__001.jpg'
    assert (ref['source_width'], ref['source_height']) == D.SCAN_SIZE
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'], bbox_px={'x': 10, 'y': 10, 'width': 100, 'height': 80})
    assert zoom.data['image']['role'] == 'detail'


@pytest.mark.asyncio
async def test_house_number_outside_every_range_is_reported_not_covered(sp):
    service, _ = sp
    sid = await research(service, year_from=1875, year_to=1885, person={'house_number': '70'})
    await places(service, sid, 'Demolov', 'census')
    data = await listed(service, sid, [DEMOLOV], 'census')
    by = {r['source_id']: r['relevance'] for r in data['sources']}
    assert by['soapraha:unit:8003']['status'] == 'candidate' and by['soapraha:unit:8003']['house_number_covered'] is True
    assert by['soapraha:unit:8001']['status'] == 'excluded'
    assert by['soapraha:unit:8002']['house_number_covered'] is False and by['soapraha:unit:8002']['status'] == 'needs_review'
    assert by['soapraha:unit:8002']['reason'] == 'dům 70 není v rozsahu čp. 1–50 této jednotky'
    assert by['soapraha:unit:8001']['status'] == 'excluded'
    assert [r['source_id'] for r in data['sources']][:2] == ['soapraha:unit:8003', 'soapraha:unit:8002'], 'a covering range first, a range that does not cover last'


@pytest.mark.asyncio
async def test_unranged_census_set_stays_unknown_for_a_house_number(sp):
    service, _ = sp
    sid = await research(service, year_from=1869, year_to=1869, person={'house_number': '5'})
    await places(service, sid, 'Demolov', 'census')
    data = await listed(service, sid, [DEMOLOV], 'census', year_from=1869, year_to=1869)
    assert [r['source_id'] for r in data['sources']] == ['soapraha:unit:8001']
    rel = data['sources'][0]['relevance']
    assert rel['status'] == 'candidate' and rel['house_number_covered'] is None


@pytest.mark.asyncio
async def test_sets_without_scans_are_hidden_unless_asked_for(sp):
    service, _ = sp
    sid = await research(service, year_from=1900, year_to=1900)
    await places(service, sid, 'Horní Demolov', 'census')
    data = await listed(service, sid, [HORNI], 'census')
    assert data['sources'] == [] and any('nemá v eBadatelně žádné snímky' in w for w in data['warnings'])
    data = await listed(service, sid, [HORNI], 'census', digitized_only=False)
    assert [r['source_id'] for r in data['sources']] == ['soapraha:unit:8005']
    assert data['sources'][0]['part'] == 'Horní Demolov' and data['sources'][0]['municipality'] == 'Demolov'


@pytest.mark.asyncio
async def test_land_records_are_categorised_and_only_land_related_types_are_listed(sp):
    service, archive = sp
    sid = await research(service, year_from=1700, year_to=1780)
    await places(service, sid, 'Demolov', 'land_records')
    data = await listed(service, sid, [DEMOLOV], 'land_records', limit=20)
    assert data['kind'] == 'land_records' and data['listing_complete'] is True
    assert [p['typArchivalie'] for p in posts(archive)] == ['3', '2', '11'], 'one search per land-related archival type'
    ids = [r['source_id'] for r in data['sources']]
    assert set(ids) == {f'soapraha:unit:{i}' for i in (8101, 8102, 8103, 8201, 8202, 8203)}, 'church chronicle and photograph are not land records'
    cat = {r['source_id']: r['category'] for r in data['sources']}
    assert cat == {'soapraha:unit:8101': 'land_book', 'soapraha:unit:8102': 'land_book', 'soapraha:unit:8202': 'land_book',
                   'soapraha:unit:8103': 'other', 'soapraha:unit:8201': 'other', 'soapraha:unit:8203': 'other'}
    rel = {r['source_id']: r['relevance']['status'] for r in data['sources']}
    assert rel['soapraha:unit:8101'] == 'candidate' and rel['soapraha:unit:8102'] == 'excluded' and rel['soapraha:unit:8203'] == 'needs_review'
    assert ids[0] == 'soapraha:unit:8101', 'candidates first, land books before other units'
    first = data['sources'][0]
    assert first['signature'] == 'OS Testov 100' and first['original_title'] == 'Grundbuch der Stadt Demolau' and first['dating']['spans'] == [{'start': 1700, 'end': 1780}]
    assert first['archive'] == 'soapraha' and first['kind'] == 'land_records'
    opened = (await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8101')).data
    assert opened['kind'] == 'land_records' and opened['unit']['storage_location'] == 'Státní okresní archiv Testov' and opened['total'] == 3
    assert opened['citation'] == ('Státní okresní archiv Testov (eBadatelna SOA Praha), fond Archiv města Demolov, sign. OS Testov 100, '
                                  'Demolov: Pozemková kniha města 1700-1780')
    assert 'čísel popisných' not in opened['note']
    view = await call(service, 'view_scan', research_id=sid, page_id=opened['pages'][2]['page_id'])
    assert view.data['image']['viewer_url'] == 'https://ebadatelna.soapraha.cz/a/8101/3'


@pytest.mark.asyncio
async def test_year_filter_keeps_unknown_dating(sp):
    service, _ = sp
    sid = await research(service)
    await places(service, sid, 'Demolov', 'land_records')
    data = await listed(service, sid, [DEMOLOV], 'land_records', year_from=1700, year_to=1720, limit=20)
    ids = {r['source_id'] for r in data['sources']}
    assert 'soapraha:unit:8101' in ids and 'soapraha:unit:8102' not in ids and 'soapraha:unit:8203' in ids     # 8203 has no dating
    assert any('Filtr let' in w for w in data['warnings'])


@pytest.mark.asyncio
async def test_paging_of_archival_results_and_the_cache(sp):
    service, archive = sp
    sid = await research(service)
    await places(service, sid, 'Demolov', 'census')
    await listed(service, sid, [DEMOLOV], 'census', digitized_only=False)
    n_get = sum(1 for m, t, _ in archive.soapraha.log if 'nextPageUrl' in t)
    assert n_get == 1, '6 sets at 3 per page: one next-page request'
    before = len(archive.soapraha.log)
    again = await listed(service, sid, [DEMOLOV], 'census', digitized_only=False)
    assert len(archive.soapraha.log) == before and again['total'] == 6, 'a complete answer is remembered'


@pytest.mark.asyncio
async def test_cut_result_is_not_complete_and_not_remembered(sp, monkeypatch):
    service, archive = sp
    monkeypatch.setattr(S, 'MAX_RESULT_PAGES', 1)
    sid = await research(service)
    await places(service, sid, 'Demolov', 'census')
    data = await listed(service, sid, [DEMOLOV], 'census', digitized_only=False)
    assert data['listing_complete'] is False and data['total'] == 3 and any('limit 1 stran' in w for w in data['warnings'])
    monkeypatch.setattr(S, 'MAX_RESULT_PAGES', 8)
    data = await listed(service, sid, [DEMOLOV], 'census', digitized_only=False)
    assert data['listing_complete'] is True and data['total'] == 6


@pytest.mark.asyncio
async def test_same_named_locality_of_another_district_is_left_out_or_marked(sp):
    service, _ = sp
    sid = await research(service, year_from=1900, year_to=1900)
    found = await places(service, sid, 'Testov', 'census')
    assert {p['source_id'] for p in found['places']} >= {TESTOV, LHOTKA_TESTOV}
    own = await listed(service, sid, [TESTOV], 'census')
    assert [r['source_id'] for r in own['sources']] == ['soapraha:unit:8009'], 'the set of obec Lhotka (okres Kolonov) belongs to the other Testov'
    assert any('Sčítání lidu 1900: Okres Kolonov - obec Lhotka, čp. 1-25' in w for w in own['warnings']), 'dropped titles are named'
    hist = own['sources'][0]['relevance']
    assert hist['status'] == 'needs_review' and 'historický politický okres' in hist['reason'], 'a district mismatch alone never drops a unit'
    assert any('historický politický okres' in w for w in own['warnings'])
    other = await listed(service, sid, [LHOTKA_TESTOV], 'census')
    assert [r['source_id'] for r in other['sources']] == ['soapraha:unit:8006', 'soapraha:unit:8009']
    assert [r['relevance']['status'] for r in other['sources']] == ['candidate', 'needs_review']


@pytest.mark.asyncio
async def test_land_record_of_a_homonym_that_cannot_be_told_needs_review(sp):
    service, _ = sp
    sid = await research(service, year_from=1600, year_to=1700)
    await places(service, sid, 'Testov', 'land_records')
    data = await listed(service, sid, [LHOTKA_TESTOV], 'land_records')
    urbar = next(r for r in data['sources'] if r['source_id'] == 'soapraha:unit:8201')
    assert urbar['relevance']['status'] == 'needs_review' and 'existuje i jinde' in urbar['relevance']['reason']
    assert urbar['review_reason']


@pytest.mark.asyncio
async def test_kind_switches_are_separate(tmp_path):
    service, archive = build(tmp_path, soapraha_census_enabled=False)
    try:
        sid = await research(service)
        census = await call(service, 'find_place', research_id=sid, term='Demolov', kind='census', archives=['soapraha'])
        assert census.data['places'] == [] and census.data['searched'] == [] and census.data['not_searched'][0]['archive'] == 'soapraha'
        land = await places(service, sid, 'Demolov', 'land_records')
        assert land['searched'] == ['soapraha']
        out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='census'))
        assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'
        assert not posts(archive)
        data = await listed(service, sid, [DEMOLOV], 'land_records')
        assert data['total'] >= 1
        await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8101')
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_a_switch_turned_off_later_stops_the_scan(tmp_path):
    service, archive = build(tmp_path)
    try:
        sid = await research(service)
        await places(service, sid, 'Demolov', 'census')
        await listed(service, sid, [DEMOLOV], 'census')
        opened = (await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8004')).data
        service.cfg.soapraha_census_enabled = False
        out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=opened['pages'][0]['page_id']))
        assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'
        assert not any(t.endswith('.jpg') for _, t, _ in archive.soapraha.log)
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_both_switches_off_closes_the_archival_routes_and_lists_no_kind(tmp_path):
    service, archive = build(tmp_path, soapraha_census_enabled=False, soapraha_land_records_enabled=False)
    try:
        sid = await research(service)
        for kind in ('census', 'land_records'):
            r = await call(service, 'find_place', research_id=sid, term='Demolov', kind=kind, archives=['soapraha'])
            assert r.data['searched'] == []
        arch = await call(service, 'list_archives')
        row = next(a for a in arch.data['archives'] if a['code'] == 'soapraha')
        assert row['available_kinds'] == ['parish_registers'] and row['kinds'] == ['parish_registers', 'land_records', 'census']
        assert not any('Archivalie' in t for _, t, _ in archive.soapraha.log)
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_unit_must_come_from_a_listing_or_the_research_link(sp):
    service, _ = sp
    sid = await research(service)
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:unit:8004'))
    assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:unit:x'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    linked = await research(service, book_url='https://ebadatelna.soapraha.cz/a/8101/2')
    data = (await call(service, 'open_source', research_id=linked, source_id='soapraha:unit:8101')).data
    assert data['kind'] == 'land_records' and data['unit']['title'] == 'Demolov: Pozemková kniha města 1700-1780'


@pytest.mark.asyncio
async def test_a_register_id_still_opens_only_as_a_register(sp):
    service, _ = sp
    sid = await research(service)
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:book:7001'))
    assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'


@pytest.mark.asyncio
async def test_expired_page_is_retried_in_a_new_session(sp):
    service, archive = sp
    archive.soapraha.expire_next = 1
    sid = await research(service)
    await places(service, sid, 'Demolov', 'census')
    data = await listed(service, sid, [DEMOLOV], 'census')
    assert data['listing_complete'] is True and data['total'] >= 1


@pytest.mark.asyncio
async def test_a_changed_search_page_is_a_parse_error_not_an_empty_listing(sp, monkeypatch):
    service, archive = sp
    original = D.arch_search_page
    monkeypatch.setattr(D, 'arch_search_page', lambda page_id, sid: original(page_id, sid).replace('name="typArchivalie"', 'name="typ"'))
    sid = await research(service)
    await places(service, sid, 'Demolov', 'census')
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='census'))
    assert out.is_error and out.data['error']['code'] == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_findings_report_and_gedcom_name_the_unit(sp):
    service, _ = sp
    sid = await research(service, year_from=1900, year_to=1900)
    await places(service, sid, 'Demolov', 'census')
    await listed(service, sid, [DEMOLOV], 'census')
    opened = (await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8004')).data
    view = await call(service, 'view_scan', research_id=sid, page_id=opened['pages'][0]['page_id'])
    rec = await call(service, 'record_finding', research_id=sid, idempotency_key='k1', observation={
        'kind': 'record', 'image_id': view.data['image']['image_id'], 'text': 'SYNTHETIC', 'fields': {'given_name': 'Test'}, 'is_candidate': True})
    status = (await call(service, 'get_research_status', research_id=sid)).data
    assert status['coverage']['opened_books'] == [{
        'book_id': 'soapraha:unit:8004', 'source': 'soapraha', 'archive': 'soapraha', 'number': None,
        'title': 'Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50',
        'detail_url': 'https://ebadatelna.soapraha.cz/pages/ArchivaliePage/archivalieId/8004', 'census': True, 'year': 1900}]
    not_opened = {b['book_id'] for b in status['coverage']['catalog_books_not_opened']}
    assert 'soapraha:unit:8002' in not_opened
    tree = {'title': 'T', 'root_person': 'p1', 'persons': [{'id': 'p1', 'given_name': 'Test', 'surname': 'Person', 'sex': 'M', 'events': [
        {'type': 'birth', 'confidence': 'documented', 'date': {'year': 1890}, 'sources': [{'research_id': sid, 'observation_id': rec.data['observation_id']}]}]}]}
    gedcom = (await call(service, 'export_family_tree', tree=tree)).data['gedcom']
    assert 'Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50' in gedcom and 'Státní oblastní archiv v Praze' in gedcom


@pytest.mark.asyncio
async def test_census_place_source_url_is_the_archival_search_page(sp):
    service, _ = sp
    sid = await research(service)
    census = await places(service, sid, 'Demolov', 'census')
    regs = await places(service, sid, 'Demolov', 'parish_registers')
    assert census['places'][0]['source_url'].endswith('/pages/SearchArchivaliePage')
    assert regs['places'][0]['source_url'].endswith('/pages/SearchMatrikaPage')


@pytest.mark.asyncio
async def test_listing_and_opened_unit_share_the_fuller_citation(sp):
    service, _ = sp
    sid = await research(service, year_from=1700, year_to=1780)
    await places(service, sid, 'Demolov', 'land_records')
    before = (await listed(service, sid, [DEMOLOV], 'land_records'))['sources'][0]['citation']
    opened = (await call(service, 'open_source', research_id=sid, source_id='soapraha:unit:8101')).data
    after = (await listed(service, sid, [DEMOLOV], 'land_records'))['sources'][0]
    assert after['source_id'] == 'soapraha:unit:8101' and after['citation'] == opened['citation'] != before
    view = await call(service, 'view_scan', research_id=sid, page_id=opened['pages'][0]['page_id'])
    assert view.data['image']['citation'] == opened['citation']


@pytest.mark.asyncio
@pytest.mark.parametrize('census_on,land_on,unit,ok', [
    (False, True, 8004, False), (True, False, 8101, False), (True, True, 8004, True), (False, True, 8101, True)])
async def test_a_unit_named_by_link_follows_its_own_kind_switch(tmp_path, census_on, land_on, unit, ok):
    service, archive = build(tmp_path, soapraha_census_enabled=census_on, soapraha_land_records_enabled=land_on)
    try:
        sid = await research(service, book_url=f'https://ebadatelna.soapraha.cz/a/{unit}/1')
        out = await service.call('open_source', 'alice', dict(research_id=sid, source_id=f'soapraha:unit:{unit}'))
        if ok:
            assert not out.is_error and out.data['kind'] == ('census' if unit == 8004 else 'land_records')
        else:
            assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'
            status = (await call(service, 'get_research_status', research_id=sid)).data
            assert status['coverage']['opened_books'] == []
            assert not any(t.endswith('.jpg') for _, t, _ in archive.soapraha.log)
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_a_unit_named_by_link_of_an_unknown_type_is_refused(sp):
    service, _ = sp
    sid = await research(service, book_url='https://ebadatelna.soapraha.cz/a/8302/1')
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:unit:8302'))
    assert out.is_error and out.data['error']['code'] == 'UNSUPPORTED_SOURCE'
