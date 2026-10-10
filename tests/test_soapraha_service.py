"""SOA Praha end to end against the synthetic Wicket server: find_place → list_sources → open_source → view_scan → zoom_scan."""
import base64
import io

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from conftest import call
from rubrika import demo_soapraha as D
from rubrika import soapraha as S
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource, SiteSession
from rubrika.service import ResearchService
from rubrika.storage import Store

HOST = 'ebadatelna.soapraha.cz'
LOC = {(l.name, l.community): l.id for l in S.parse_localities(D.search_page(0, 'A' * 32))}
DEMOLOV, HORNI = LOC['Demolov', 'Demolov'], LOC['Horní Demolov', 'Demolov']
DEMOLAU, MALA_TESTOV, MALA_KOLONOV = LOC['Demolau', 'Demolov'], LOC['Malá Ves', 'Testov'], LOC['Malá Ves', 'Kolonov']
FULL_FUND = 'Sbírka matrik a průvodní listinný materiál, Středočeský kraj (NAD 28)'


@pytest.fixture
def clock(monkeypatch):
    """Virtual time: a sleep advances the clock, so the archive's pace is measured, not waited for."""
    now = [1_800_000_000.0]
    slept = []
    import rubrika.network as N
    monkeypatch.setattr('rubrika.storage.time.time', lambda: now[0])
    async def fake_sleep(s):
        slept.append(s)
        now[0] += s
    monkeypatch.setattr(N.asyncio, 'sleep', fake_sleep)
    return now, slept


def build(tmp_path, clock, **settings):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, soapraha_enabled=True, request_interval=0, **settings)
    store = Store(cfg)
    archive = DemoArchive()
    times = []
    def handler(request):
        if request.url.host == HOST:
            times.append((clock[0][0], request.url.path))
        return archive.handle(request)
    source = SafeSource(cfg, store, transport=httpx.MockTransport(handler), source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    service.times, service.store_ = times, store
    return service, archive


@pytest_asyncio.fixture
async def sp(tmp_path, clock):
    service, archive = build(tmp_path, clock)
    yield service, archive
    await service.close()
    service.store_.close()


def hits(archive):
    return [(m, t) for m, t, _ in archive.soapraha.log]


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC soapraha test', 'place_hint': 'Demolov', **query})
    return out.data['research_id']


async def demolov(service, **query):
    sid = await research(service, event_type='birth', year_from=1785, year_to=1790, **query)
    places = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['soapraha'])
    return sid, places.data


async def opened_book(service, book='soapraha:book:7001', **query):
    sid, _ = await demolov(service, **query)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    out = await call(service, 'open_source', research_id=sid, source_id=book)
    return sid, out.data


@pytest.mark.asyncio
async def test_end_to_end_find_list_open_view_zoom(sp):
    service, archive = sp
    sid, places = await demolov(service)
    assert places['searched'] == ['soapraha'] and places['not_searched'] == []
    assert [p['source_id'] for p in places['places']] == [DEMOLOV, HORNI]
    assert {p['archive'] for p in places['places']} == {'soapraha'}
    assert places['places'][0]['label'] == 'Demolov, okres Demolov' and places['places'][0]['names'] == ['Demolov']
    assert places['places'][1]['label'] == 'Horní Demolov, obec Demolov, okres Demolov' and places['ambiguous']
    assert not archive.soapraha.posts, 'the places come from the page\'s locality list: no search request'

    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    assert listed.data['archive'] == 'soapraha' and listed.data['listing_complete'] is True
    rows = listed.data['sources']
    assert rows[0]['source_id'] == 'soapraha:book:7001' and rows[0]['relevance']['status'] == 'candidate'
    assert rows[0]['archive'] == 'soapraha' and rows[0]['kind'] == 'parish_registers' and rows[0]['district'] == 'Demolov'
    assert rows[0]['detail_url'] == 'https://ebadatelna.soapraha.cz/pages/MatrikaPage/matrikaId/7001' and rows[0]['image_count'] == 3
    assert rows[0]['coverage']['birth']['records']['spans'] == [{'start': 1785, 'end': 1830}]
    status = {r['source_id']: r['relevance']['status'] for r in rows}
    assert status == {'soapraha:book:7001': 'candidate', 'soapraha:book:7002': 'excluded', 'soapraha:book:7003': 'excluded',
                      'soapraha:book:7004': 'excluded', 'soapraha:book:7005': 'needs_review', 'soapraha:book:7006': 'needs_review',
                      'soapraha:book:7007': 'excluded', 'soapraha:book:7010': 'excluded', 'soapraha:book:7011': 'excluded'}
    assert [r['relevance']['status'] for r in rows] == sorted((r['relevance']['status'] for r in rows), key=['candidate', 'needs_review', 'excluded'].index)
    # the search matched a word that merely starts with the term (Starý Demolovec): left out and said so
    assert any('1 ne (ve výpisu nejsou)' in w and 'Starý Demolovec' in w for w in listed.data['warnings'])

    opened = await call(service, 'open_source', research_id=sid, source_id='soapraha:book:7001')
    data = opened.data
    assert data['archive'] == 'soapraha' and data['kind'] == 'parish_registers' and data['total'] == 3
    assert data['citation'] == 'Státní oblastní archiv v Praze, ' + FULL_FUND + ', Demolov 01'
    assert data['unit']['localities'] == ['Demolov / Demolau', 'Horní Demolov / Ober-Demolau'] and data['unit']['image_count'] == 3
    page = data['pages'][0]
    assert page['page_id'] == 'soapraha:book:7001:1' and page['source_type'] == 'jpeg' and page['archive'] == 'soapraha'
    assert page['viewer_url'] == 'https://ebadatelna.soapraha.cz/d/7001/1' and page['citation'] == data['citation']
    assert page['image_key'] == 'scan-1'

    view = await call(service, 'view_scan', research_id=sid, page_id=page['page_id'])
    ref = view.data['image']
    assert ref['archive'] == 'soapraha' and (ref['source_width'], ref['source_height']) == D.SCAN_SIZE
    assert ref['viewer_url'] == page['viewer_url'] and ref['citation'] == data['citation'] and ref['image_key'] == 'Demolov_01__001.jpg'
    assert ref['source_bbox'] == (0, 0, *D.SCAN_SIZE) and ref['sha256'] and ref['role'] == 'overview'

    before = len(archive.requests)
    sx, sy = ref['width'] / ref['source_width'], ref['height'] / ref['source_height']
    x0, y0, x1, y1 = D.MARKER
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': (x0 - 20) * sx, 'y': (y0 - 20) * sy, 'width': (x1 - x0 + 40) * sx, 'height': (y1 - y0 + 40) * sy})
    z = zoom.data['image']
    assert len(archive.requests) == before, 'the second look must come from the cache: no request'
    x, y, w, h = z['source_bbox']
    assert z['role'] == 'detail' and z['viewer_url'] == page['viewer_url'] and z['image_key'] == 'Demolov_01__001.jpg'
    got = Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).convert('RGB')
    assert got.size == (w, h)
    cx, cy = (x0 + x1) // 2 - x, (y0 + y1) // 2 - y
    r, g, b = got.getpixel((cx, cy))
    assert b > 150 and r < 100                     # the blue marker of the synthetic scan sits where the source bbox says it does
    assert got.getpixel((2, 2))[0] > 200           # and the margin around it is the scan's paper
    saved = await call(service, 'show_saved_evidence', research_id=sid, image_id=z['image_id'])
    assert saved.data['image']['sha256'] == z['sha256']


@pytest.mark.asyncio
async def test_a_detail_beyond_the_scan_is_refused_and_scaling_is_local(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=data['pages'][1]['page_id'])
    ref = view.data['image']
    assert max(ref['width'], ref['height']) <= service.cfg.preview_side
    out = await service.call('zoom_scan', 'alice', dict(research_id=sid, image_id=ref['image_id'],
                                                        bbox_px={'x': 0, 'y': 0, 'width': ref['width'] + 5, 'height': ref['height']}))
    assert out.is_error and out.data['error']['code'] == 'INVALID_REGION'
    full = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': 0, 'y': 0, 'width': ref['width'], 'height': ref['height']})
    assert full.data['image']['source_bbox'] == (0, 0, *D.SCAN_SIZE)


@pytest.mark.asyncio
async def test_jpeg_is_cached_second_zoom_and_second_view_make_no_request(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    pid = data['pages'][0]['page_id']
    first = await call(service, 'view_scan', research_id=sid, page_id=pid)
    marks = len(archive.soapraha.log)
    assert sum(t.endswith('.jpg') for _, t in hits(archive)) == 1
    again = await call(service, 'view_scan', research_id=sid, page_id=pid)
    ref = first.data['image']
    await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'], bbox_px={'x': 10, 'y': 10, 'width': 100, 'height': 80})
    assert len(archive.soapraha.log) == marks and again.data['image']['sha256'] == ref['sha256']
    # a scan of the same book is a new download; the cache holds each scan under its own viewer address
    await call(service, 'view_scan', research_id=sid, page_id=data['pages'][1]['page_id'])
    assert sum(t.endswith('.jpg') for _, t in hits(archive)) == 2


@pytest.mark.asyncio
async def test_a_lost_cache_entry_is_fetched_again_in_a_new_session(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    page = data['pages'][0]
    view = await call(service, 'view_scan', research_id=sid, page_id=page['page_id'])
    sessions = len(archive.soapraha.sessions)
    service.source.invalidate(page['viewer_url'])
    ref = view.data['image']
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'], bbox_px={'x': 10, 'y': 10, 'width': 100, 'height': 80})
    assert zoom.data['image']['source_bbox'] == (16, 16, 160, 128)
    assert len(archive.soapraha.sessions) == sessions + 1
    assert hits(archive)[-2:] == [('GET', '/d/7001/1'), ('GET', '/wicket/resource/org.apache.wicket.Application/Demolov_01__001.jpg')]


@pytest.mark.asyncio
async def test_each_tool_call_uses_its_own_session_and_the_cookie_is_never_stored(sp, tmp_path):
    service, archive = sp
    sid, _ = await demolov(service)
    first = set(archive.soapraha.sessions)
    assert len(first) >= 2                      # robots.txt hands out a cookie of its own; the locality list opens another
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    second = set(archive.soapraha.sessions) - first
    assert second, 'a search opens a new session'
    await call(service, 'open_source', research_id=sid, source_id='soapraha:book:7001')
    assert len(set(archive.soapraha.sessions) - first - second) == 1
    for cookie in archive.soapraha.sessions:
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.stat().st_size < 50_000_000:
                assert cookie.encode() not in path.read_bytes(), path
    assert all(has for m, t, has in archive.soapraha.log if 'searchButton' in t or 'nextPageUrl' in t)


@pytest.mark.asyncio
async def test_search_reads_component_ids_from_the_page_and_follows_paging(sp):
    service, archive = sp
    archive.soapraha.start_version = 7
    sid, places = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    ajax = archive.soapraha.ajax
    posts = [a for a in ajax if 'searchButton' in a[0]]
    assert posts == [('/pages/SearchMatrikaPage?7-1.0-searchForm-searchButton', 'true')]
    pages = [a for a in ajax if 'nextPageUrl' in a[0]]
    assert len(pages) == 3 and all(flag == 'true' for _, flag in pages)      # 10 hits at 3 rows per page: three more pages
    assert len({a for a, _ in pages}) == 3                                    # the version moves on with each answer
    assert archive.soapraha.posts == [{'searchType': '1', 'keywordBox:searchTokenIn': 'radio0', 'keywordBox:searchToken': 'Demolov',
                                       'dateYearFrom': '1000', 'dateYearTo': '2100'}]


@pytest.mark.asyncio
async def test_years_of_the_research_do_not_narrow_the_archive_search(sp):
    service, archive = sp
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    assert {(p['dateYearFrom'], p['dateYearTo']) for p in archive.soapraha.posts} == {('1000', '2100')}


@pytest.mark.asyncio
async def test_locality_list_is_fetched_once_and_cached_without_session_data(sp, tmp_path):
    service, archive = sp
    sid, _ = await demolov(service)
    pages = [t for m, t, _ in archive.soapraha.log if t.startswith('/pages/SearchMatrikaPage')]
    again = await call(service, 'find_place', research_id=sid, term='malá ves', kind='parish_registers', archives=['soapraha'])
    assert [t for m, t, _ in archive.soapraha.log if t.startswith('/pages/SearchMatrikaPage')] == pages
    assert [p['source_id'] for p in again.data['places']] == [MALA_KOLONOV, MALA_TESTOV]
    cached = service.store.entity('soapraha_localities', 'all')
    assert len(cached['localities']) == sum(len(n) for _, _, n in D.LOCALITIES)
    assert not any(k in str(cached) for k in ('jsessionid', 'JSESSIONID'))
    # a search by a locality is remembered per term as well
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    posts = len(archive.soapraha.posts)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    assert len(archive.soapraha.posts) == posts


@pytest.mark.asyncio
async def test_same_named_localities_are_told_apart_by_community_and_district(sp):
    service, archive = sp
    sid = await research(service)
    found = await call(service, 'find_place', research_id=sid, term='Malá Ves', kind='parish_registers', archives=['soapraha'])
    labels = {p['source_id']: p['label'] for p in found.data['places']}
    assert labels == {MALA_TESTOV: 'Malá Ves, obec Testov, okres Testov', MALA_KOLONOV: 'Malá Ves, obec Kolonov, okres Kolonov'}
    assert any('Lokalit tohoto jména je v archivu víc' in w for w in found.data['warnings'])
    testov = await call(service, 'list_sources', research_id=sid, place_ids=[MALA_TESTOV], kind='parish_registers')
    # 7008 is a register of the district Testov, 7010 (district Demolov) names the community Testov among its localities
    assert {r['source_id'] for r in testov.data['sources']} == {'soapraha:book:7008', 'soapraha:book:7010'}
    kolonov = await call(service, 'list_sources', research_id=sid, place_ids=[MALA_KOLONOV], kind='parish_registers')
    # 7008 is of the district of the other Malá Ves and dropped; 7010 is of a third district: kept, but only for a human look
    assert [r['source_id'] for r in kolonov.data['sources']] == ['soapraha:book:7010']
    assert kolonov.data['sources'][0]['relevance']['status'] == 'needs_review' and 'okres matriky' in kolonov.data['sources'][0]['review_reason']
    assert any('1 ne (ve výpisu nejsou)' in w for w in kolonov.data['warnings'])


@pytest.mark.asyncio
async def test_a_german_name_is_searched_by_a_neighbouring_czech_name_and_says_so(sp):
    service, archive = sp
    sid = await research(service)
    found = await call(service, 'find_place', research_id=sid, term='Demolau', kind='parish_registers', archives=['soapraha'])
    assert found.data['places'][0]['source_id'] == DEMOLAU and found.data['places'][0]['label'] == 'Demolau, obec Demolov, okres Demolov'
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLAU], kind='parish_registers')
    assert listed.data['total'] == 9 and any('Demolau' in w and 'sousedního názvu Demolov' in w for w in listed.data['warnings'])
    assert [p['keywordBox:searchToken'] for p in archive.soapraha.posts] == ['Demolau', 'Demolov']


@pytest.mark.asyncio
async def test_unknown_and_partial_names(sp):
    service, archive = sp
    sid = await research(service)
    nothing = await call(service, 'find_place', research_id=sid, term='Nikdyneni', kind='parish_registers', archives=['soapraha'])
    assert nothing.data['places'] == [] and nothing.data['searched'] == ['soapraha']
    assert any('neobsahuje' in w and 'Nejde o důkaz' in w for w in nothing.data['warnings'])
    partial = await call(service, 'find_place', research_id=sid, term='Demolo', kind='parish_registers', archives=['soapraha'])
    assert partial.data['places'] and any('začíná' in w for w in partial.data['warnings'])
    assert archive.soapraha.posts == []


@pytest.mark.asyncio
async def test_expired_page_is_retried_once_in_a_new_session(sp):
    service, archive = sp
    archive.soapraha.expire_next = 1
    sid, places = await demolov(service)
    assert places['places'][0]['source_id'] == DEMOLOV
    assert sum(1 for m, t in hits(archive) if t == '/pages/SearchMatrikaPage') == 2


@pytest.mark.asyncio
async def test_a_permanently_expired_page_is_an_error_not_an_empty_result(sp):
    service, archive = sp
    archive.soapraha.mode = 'expired'
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['soapraha'])
    assert out.data['places'] == [] and out.data['searched'] == [] and out.data['not_searched'][0]['archive'] == 'soapraha'
    assert 'SOURCE_UNAVAILABLE' in out.data['not_searched'][0]['reason']


@pytest.mark.asyncio
async def test_changed_page_structure_is_a_parse_error_not_an_empty_result(sp, monkeypatch):
    service, archive = sp
    sid, _ = await demolov(service)
    monkeypatch.setattr(D, 'HEADERS', D.HEADERS[:-1])
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='parish_registers'))
    assert out.is_error and out.data['error']['code'] == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_changed_locality_list_is_a_parse_error(sp, monkeypatch):
    service, archive = sp
    monkeypatch.setattr(D, 'LOCALITIES', [])
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['soapraha'])
    assert out.data['places'] == [] and 'PARSE_ERROR' in out.data['not_searched'][0]['reason']
    assert service.store.entity('soapraha_localities', 'all') is None


@pytest.mark.asyncio
async def test_listing_reports_a_cut_when_pages_exceed_the_limit(sp, monkeypatch):
    service, archive = sp
    monkeypatch.setattr(S, 'MAX_RESULT_PAGES', 2)
    sid, _ = await demolov(service)
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    assert any('načteno 2 (limit 2' in w for w in listed.data['warnings'])
    assert listed.data['listing_complete'] is False and listed.data['total'] == 6       # the first two pages of three rows


@pytest.mark.asyncio
async def test_two_places_in_one_listing_deduplicate_books_and_the_limit_is_kept(sp):
    service, _ = sp
    sid, _ = await demolov(service)
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV, HORNI], kind='parish_registers')
    ids = [r['source_id'] for r in listed.data['sources']]
    assert len(ids) == len(set(ids)) == 9
    many = await service.call('list_sources', 'alice', dict(research_id=sid, kind='parish_registers', place_ids=[f'soapraha:place:p{i}' for i in range(4)]))
    assert many.is_error and many.data['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_listing_refuses_filters_and_foreign_or_unknown_places(sp):
    service, _ = sp
    sid, _ = await demolov(service)
    for extra in ({'year_from': 1800}, {'fund_marks': ['C 17']}, {'district_archive': 'x'}):
        out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='parish_registers', **extra))
        assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['soapraha:place:testov-x'], kind='parish_registers'))
    assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='land_records'))
    assert out.is_error
    out = await service.call('find_place', 'alice', dict(research_id=sid, term='Demolov', kind='census', archives=['soapraha']))
    assert out.data['searched'] == [] and out.data['not_searched'][0]['archive'] == 'soapraha'


@pytest.mark.asyncio
async def test_open_source_needs_a_listed_book_or_the_link_of_the_research(sp):
    service, archive = sp
    sid = await research(service)
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:book:7001'))
    assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='soapraha:book:x'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    linked = await research(service, book_url='https://ebadatelna.soapraha.cz/pages/DataMatrikaPage/idx/2/matrikaId/7002')
    opened = await call(service, 'open_source', research_id=linked, source_id='soapraha:book:7002')
    assert opened.data['unit']['number'] == 'Demolov 02' and opened.data['total'] == 2
    other = await service.call('open_source', 'alice', dict(research_id=linked, source_id='soapraha:book:7003'))
    assert other.is_error and other.data['error']['code'] == 'NOT_FOUND'


@pytest.mark.asyncio
async def test_detail_scan_count_wins_over_the_listing_and_a_difference_is_said(sp, monkeypatch):
    service, archive = sp
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    row = D.BY_ID[7001]
    monkeypatch.setitem(D.BY_ID, 7001, (*row[:6], 4))
    opened = await call(service, 'open_source', research_id=sid, source_id='soapraha:book:7001')
    assert opened.data['total'] == 4 and any('liší' in w for w in opened.data['warnings'])


@pytest.mark.asyncio
async def test_pages_beyond_the_window_are_still_viewable(sp):
    service, _ = sp
    sid, data = await opened_book(service)
    out = await call(service, 'open_source', research_id=sid, source_id='soapraha:book:7001', cursor=0, limit=1)
    assert len(out.data['pages']) == 1 and out.data['next_cursor'] == 1
    view = await call(service, 'view_scan', research_id=sid, page_id='soapraha:book:7001:3')
    assert view.data['image']['scan_number'] == 3 and view.data['image']['image_key'] == 'Demolov_01__003.jpg'
    bad = await service.call('view_scan', 'alice', dict(research_id=sid, page_id='soapraha:book:7001:4'))
    assert bad.is_error and bad.data['error']['code'] == 'NOT_FOUND'
    bad = await service.call('view_scan', 'alice', dict(research_id=sid, page_id='soapraha:book:7002:1'))
    assert bad.is_error and bad.data['error']['code'] == 'NOT_FOUND'


@pytest.mark.asyncio
async def test_changed_scan_count_at_view_time_is_source_changed(sp, monkeypatch):
    service, _ = sp
    sid, data = await opened_book(service)
    row = D.BY_ID[7001]
    monkeypatch.setitem(D.BY_ID, 7001, (*row[:6], 5))
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'SOURCE_CHANGED'


@pytest.mark.asyncio
async def test_bad_downloads_are_refused_and_never_cached(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    pid = data['pages'][0]['page_id']
    archive.soapraha.jpeg_override = lambda b, n: httpx.Response(200, content=b'<html>not an image</html>', headers={'content-type': 'image/jpeg'})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=pid))
    assert out.is_error and out.data['error']['code'] == 'INCOMPLETE_IMAGE'
    archive.soapraha.jpeg_override = lambda b, n: httpx.Response(200, text='<html><body>Login</body></html>', headers={'content-type': 'text/html'})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=pid))
    assert out.is_error and out.data['error']['code'] == 'SOURCE_UNAVAILABLE'
    good = D.scan_image(7001, 1)
    buf = io.BytesIO(); good.save(buf, format='JPEG')
    archive.soapraha.jpeg_override = lambda b, n: httpx.Response(200, content=buf.getvalue()[:len(buf.getvalue()) // 2], headers={'content-type': 'image/jpeg'})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=pid))
    assert out.is_error and out.data['error']['code'] in ('INCOMPLETE_IMAGE', 'SOURCE_UNAVAILABLE')
    archive.soapraha.jpeg_override = None
    ok = await call(service, 'view_scan', research_id=sid, page_id=pid)
    assert ok.data['image']['page_id'] == pid


@pytest.mark.asyncio
async def test_a_scan_over_the_pixel_limit_is_refused(tmp_path, clock):
    service, archive = build(tmp_path, clock, max_source_pixels=1_000_000)
    try:
        sid, data = await opened_book(service)
        out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
        assert out.is_error and out.data['error']['code'] == 'INCOMPLETE_IMAGE'
    finally:
        await service.close(); service.store_.close()


@pytest.mark.asyncio
async def test_daily_request_cap_of_the_archive_applies(tmp_path, clock):
    service, archive = build(tmp_path, clock, soapraha_global_daily_requests=4)
    try:
        sid = await research(service)
        out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['soapraha'])
        assert out.data['searched'] == [] and 'BUDGET_EXCEEDED' in out.data['not_searched'][0]['reason']
        assert len(archive.soapraha.log) <= 4
    finally:
        await service.close(); service.store_.close()


@pytest.mark.asyncio
async def test_pace_between_requests_is_at_least_a_second(sp, clock):
    service, archive = sp
    sid, data = await opened_book(service)
    await call(service, 'view_scan', research_id=sid, page_id=data['pages'][0]['page_id'])
    starts = [t for t, _ in service.times]
    assert len(starts) >= 10
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 1.0 - 1e-6


@pytest.mark.asyncio
async def test_robots_html_error_page_means_no_rules_and_is_fetched_once(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    await call(service, 'view_scan', research_id=sid, page_id=data['pages'][0]['page_id'])
    robots = [t for m, t in hits(archive) if t == '/robots.txt']
    assert len(robots) == 1
    assert [t for m, t in hits(archive)[:3]] == ['/robots.txt', '/pages/ErrorPage;jsessionid=' + hits(archive)[1][1].split('=')[1], hits(archive)[2][1]]
    assert hits(archive)[2][1].endswith('?1') and archive.soapraha.log[0][2] is False


@pytest.mark.asyncio
async def test_only_the_allowlisted_requests_leave_the_machine(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    await call(service, 'view_scan', research_id=sid, page_id=data['pages'][0]['page_id'])
    for method, target in hits(archive):
        assert method in ('GET', 'POST')
        assert method == 'GET' or 'searchForm-searchButton' in target
        assert not any(x in target for x in ('Login', 'Regist', '.zip', 'UserNote', 'Archivalie', '/a/'))


@pytest.mark.asyncio
async def test_session_layer_refuses_foreign_forms_hosts_and_routes(sp):
    service, _ = sp
    source = service.source
    session = SiteSession('s', 'alice', HOST, 'https://ebadatelna.soapraha.cz/pages/SearchMatrikaPage', opener_redirects=True)
    from rubrika.errors import ResearchError
    good = {'searchType': '1', 'keywordBox:searchTokenIn': 'radio0', 'keywordBox:searchToken': 'x', 'dateYearFrom': '1000', 'dateYearTo': '2100'}
    for url, form in (('https://ebadatelna.soapraha.cz/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton', {**good, 'extra': '1'}),
                      ('https://ebadatelna.soapraha.cz/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton', {**good, 'dateYearTo': '3000'}),
                      ('https://ebadatelna.soapraha.cz/pages/LoginPage', good),
                      ('https://ebadatelna.soapraha.cz/pages/MatrikaPage/matrikaId/7001', good),
                      ('https://www.mza.cz/actapublica/matrika/hledani', good)):
        with pytest.raises(ResearchError) as e:
            await source.session_post(session, url, form)
        assert e.value.code == 'INVALID_SOURCE'
    with pytest.raises(ResearchError) as e:
        await source.session_get(SiteSession('s', 'alice', 'www.mza.cz', 'https://www.mza.cz/', ), 'https://ebadatelna.soapraha.cz/d/7001/1')
    assert e.value.code == 'INVALID_SOURCE'
    assert session.requests == 0


@pytest.mark.asyncio
async def test_disabled_archive_is_not_searched_and_makes_no_request(sp):
    service, archive = sp
    service.cfg = service.cfg.model_copy(update={'soapraha_enabled': False})
    service.source.cfg = service.cfg
    sid = await research(service)
    before = len(archive.requests)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['soapraha'])
    assert out.data['searched'] == [] and out.data['not_searched'][0]['archive'] == 'soapraha' and len(archive.requests) == before
    res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOLOV], kind='parish_registers'))
    assert res.is_error and res.data['error']['code'] == 'LIVE_DISABLED'


@pytest.mark.asyncio
async def test_view_scan_after_disable_is_live_disabled(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    service.cfg = service.cfg.model_copy(update={'soapraha_enabled': False})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'


@pytest.mark.asyncio
async def test_findings_report_and_gedcom_name_the_archive(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=data['pages'][0]['page_id'])
    image_id = view.data['image']['image_id']
    rec = await call(service, 'record_finding', research_id=sid, idempotency_key='k1', observation={
        'kind': 'record', 'image_id': image_id, 'text': 'SYNTHETIC', 'fields': {'given_name': 'Test'}, 'is_candidate': True})
    status = await call(service, 'get_research_status', research_id=sid)
    opened = status.data['coverage']['opened_books']
    assert opened == [{'book_id': 'soapraha:book:7001', 'source': 'soapraha', 'archive': 'soapraha', 'number': 'Demolov 01',
                       'title': 'Demolov 01', 'detail_url': 'https://ebadatelna.soapraha.cz/pages/MatrikaPage/matrikaId/7001'}]
    not_opened = {b['book_id']: b for b in status.data['coverage']['catalog_books_not_opened']}
    assert 'soapraha:book:7002' in not_opened and not_opened['soapraha:book:7002']['archive'] == 'soapraha'
    tree = {'title': 'T', 'root_person': 'p1', 'persons': [{'id': 'p1', 'given_name': 'Test', 'surname': 'Person', 'sex': 'M', 'events': [
        {'type': 'birth', 'confidence': 'documented', 'date': {'year': 1786}, 'sources': [{'research_id': sid, 'observation_id': rec.data['observation_id']}]}]}]}
    exported = await call(service, 'export_family_tree', tree=tree)
    gedcom = exported.data['gedcom']
    assert 'SOA Praha, Demolov 01' in gedcom and 'Státní oblastní archiv v Praze' in gedcom


@pytest.mark.asyncio
async def test_territory_routes_central_bohemian_districts_to_soapraha():
    from rubrika.territory import archives_for_district
    assert archives_for_district('Kladno') == ('soapraha',)
    assert archives_for_district('Mladá Boleslav') == ('soapraha',) and archives_for_district('Kolín') == ('soapraha',)
    assert archives_for_district('Rakovník') == ('soapraha', 'pf')
    assert archives_for_district('Praha-východ') == ('soapraha', 'ahmp') and archives_for_district('Praha') == ('ahmp', 'soapraha')


@pytest.mark.asyncio
async def test_a_district_of_the_territory_searches_soapraha(sp):
    service, archive = sp
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', district='Kladno')
    assert out.data['searched'] == ['soapraha'] and out.data['places'][0]['archive'] == 'soapraha'
    listing = await call(service, 'list_archives')
    row = next(a for a in listing.data['archives'] if a['code'] == 'soapraha')
    assert row['available_kinds'] == ['parish_registers'] and not row['link_only']


@pytest.mark.asyncio
async def test_zoom_returns_the_pixels_of_the_scan_at_the_stated_coordinates(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=data['pages'][0]['page_id'])
    ref = view.data['image']
    sx = ref['source_width'] / ref['width']
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': 300 / sx, 'y': 200 / sx, 'width': 400 / sx, 'height': 300 / sx})
    x, y, w, h = zoom.data['image']['source_bbox']
    assert (w, h) == (zoom.data['image']['width'], zoom.data['image']['height'])          # below the crop side: not resampled
    got = Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).convert('RGB')
    want = D.scan_image(7001, 1).crop((x, y, x + w, y + h))
    from PIL import ImageChops
    assert max(max(c) for c in ImageChops.difference(got, want).getextrema()) <= 64        # JPEG noise only
    assert zoom.data['image']['sha256'] != ref['sha256']


@pytest.mark.asyncio
async def test_a_cut_search_is_not_remembered(sp, monkeypatch):
    service, archive = sp
    monkeypatch.setattr(S, 'MAX_RESULT_PAGES', 1)
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    posts = len(archive.soapraha.posts)
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLOV], kind='parish_registers')
    assert listed.data['listing_complete'] is False and len(archive.soapraha.posts) == posts + 1


@pytest.mark.asyncio
async def test_a_sibling_name_makes_every_book_needs_review_with_a_reason(sp):
    service, archive = sp
    sid, _ = await demolov(service)
    await call(service, 'find_place', research_id=sid, term='Demolau', kind='parish_registers', archives=['soapraha'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[DEMOLAU], kind='parish_registers')
    rel = {r['source_id']: r['relevance'] for r in listed.data['sources']}
    assert rel['soapraha:book:7001']['status'] == 'needs_review' and 'jiné části' in rel['soapraha:book:7001']['reason']
    assert not any(r['status'] == 'candidate' for r in rel.values())
    assert any('jiné části obce' in w for w in listed.data['warnings'])
    assert len(listed.data['warnings']) == len(set(listed.data['warnings']))


@pytest.mark.asyncio
async def test_an_unmatched_district_never_silently_empties_the_listing(sp, monkeypatch):
    service, archive = sp
    sid = await research(service)
    found = await call(service, 'find_place', research_id=sid, term='Testov', kind='parish_registers', archives=['soapraha'])
    lhotka = next(p['source_id'] for p in found.data['places'] if 'Lhotka' in p['label'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[lhotka], kind='parish_registers')
    # every register naming Testov is of the district Testov or Demolov: the homonym of Lhotka (Kolonov) cannot be told from them
    assert listed.data['total'] > 0 and all(r['relevance']['status'] != 'candidate' for r in listed.data['sources'])
    monkeypatch.setattr(S, '_district_key', lambda name: 'x' if name else '')
    found = await call(service, 'find_place', research_id=sid, term='Malá Ves', kind='parish_registers', archives=['soapraha'])
    out = await call(service, 'list_sources', research_id=sid, place_ids=[MALA_TESTOV], kind='parish_registers')
    assert out.data['total'] > 0 or (out.data['listing_complete'] is False and any('jednoznačně' in w for w in out.data['warnings']))


@pytest.mark.asyncio
async def test_places_of_the_wanted_district_come_first_and_exact_names_are_not_cut(sp, monkeypatch):
    import rubrika.service as SV
    monkeypatch.setattr(SV, 'district_name', lambda n: 'Testov' if 'Testov' in n else None)
    service, archive = sp
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Malá Ves', kind='parish_registers', archives=['soapraha'], district='Testov')
    assert [p['source_id'] for p in out.data['places']] == [MALA_TESTOV, MALA_KOLONOV]
    monkeypatch.setattr(S, 'MAX_PLACES', 1)
    monkeypatch.setattr(S, 'MAX_EXACT_PLACES', 1)
    cut = await call(service, 'find_place', research_id=sid, term='Malá Ves', kind='parish_registers', archives=['soapraha'], district='Kolonov')
    assert [p['source_id'] for p in cut.data['places']] == [MALA_KOLONOV]
    assert any('vynecháno 1 míst v okresech Testov' in w and 'district=' in w for w in cut.data['warnings'])
    hint = await research(service, place_hint='okres Testov')
    hinted = await call(service, 'find_place', research_id=hint, term='Malá Ves', kind='parish_registers', archives=['soapraha'])
    assert hinted.data['places'][0]['source_id'] == MALA_TESTOV


@pytest.mark.asyncio
async def test_the_book_image_cap_is_checked_before_a_download(sp):
    service, archive = sp
    sid, data = await opened_book(service)
    for _ in range(service.cfg.book_daily_images):
        service.store.charge_book_image('alice', 'soapraha:book:7001')
    before = len(archive.soapraha.log)
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'BUDGET_EXCEEDED' and len(archive.soapraha.log) == before


def test_a_name_without_letters_still_gets_a_valid_place_id():
    loc = S.Locality('***', 'Obec', 'Okres')
    assert S.PLACE_ID.fullmatch(loc.id) and S.PLACE_ID.fullmatch(S.Locality('Ábc', 'Obec', 'Okres').id)
