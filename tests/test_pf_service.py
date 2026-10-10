import base64
import io
import time

import httpx
import pytest
import pytest_asyncio
from PIL import Image, ImageChops

from conftest import call
from rubrika import demo_pf as D
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

HOST = 'www.portafontium.eu'


@pytest.fixture
def clock(monkeypatch):
    """Virtual time: a sleep advances the clock, so the portal's 10 s pace is measured, not waited for."""
    now = [1_800_000_000.0]
    slept = []
    import rubrika.network as N
    monkeypatch.setattr('rubrika.storage.time.time', lambda: now[0])
    async def fake_sleep(s):
        slept.append(s)
        now[0] += s
    monkeypatch.setattr(N.asyncio, 'sleep', fake_sleep)
    return now, slept


@pytest_asyncio.fixture
async def pf(tmp_path, clock):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, pf_enabled=True, request_interval=0)
    store = Store(cfg)
    archive = DemoArchive()
    times = []
    def handler(request):
        if request.url.host == HOST:
            times.append((clock[0][0], request.url.path, str(request.url)))
        return archive.handle(request)
    source = SafeSource(cfg, store, transport=httpx.MockTransport(handler), source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    service.times = times
    yield service, archive
    await service.close()
    store.close()


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC pf test', 'place_hint': 'Demolov', **query})
    return out.data['research_id']


async def demolov(service, **query):
    sid = await research(service, event_type='birth', year_from=1785, year_to=1790, **query)
    places = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    return sid, places.data


@pytest.mark.asyncio
async def test_pf_end_to_end_find_list_open_view_zoom(pf):
    service, archive = pf
    sid, places = await demolov(service)
    assert places['searched'] == ['pf'] and places['not_searched'] == []
    assert '§ 92' in places['terms_notice'] and 'portafontium.eu/terms-of-use' in places['terms_notice']
    ids = [p['source_id'] for p in places['places']]
    assert ids == ['pf:place:demolov', 'pf:place:horni-demolov'] and {p['archive'] for p in places['places']} == {'pf'}
    assert places['places'][0]['label'] == 'Demolov (Demolau)' and places['places'][0]['names'] == ['Demolov', 'Demolau']
    assert places['ambiguous'] and any('nerozlišuje obce' in w for w in places['warnings'])

    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    assert listed.data['archive'] == 'pf' and 'osobní potřeb' in listed.data['terms_notice']
    # no server-side type filter: books without marks must not vanish
    assert not any('field_register_type' in u for u in archive.requests)
    assert not any('typu' in w for w in listed.data['warnings'])
    rows = listed.data['sources']
    assert rows[0]['source_id'] == 'pf:book:soap-pn/demolov-01'
    assert rows[0]['relevance']['status'] == 'candidate' and rows[0]['archive'] == 'pf' and rows[0]['kind'] == 'parish_registers'
    st = {r['source_id']: r['relevance']['status'] for r in rows}
    assert st['pf:book:soap-pn/demolov-05'] == 'needs_review' and st['pf:book:soap-pn/demolov-04'] == 'excluded' and len(st) == 6
    assert [r['relevance']['status'] for r in rows] == sorted((r['relevance']['status'] for r in rows), key=['candidate', 'needs_review', 'excluded'].index)

    opened = await call(service, 'open_source', research_id=sid, source_id=rows[0]['source_id'])
    data = opened.data
    assert data['archive'] == 'pf' and data['kind'] == 'parish_registers' and data['total'] == 3
    assert data['citation'] == ('Státní oblastní archiv v Plzni, Sbírka matrik západních Čech, sign. Demolov 01, '
                                'https://www.portafontium.eu/register/soap-pn/demolov-01')
    assert '§ 92' in data['terms_notice'] and data['unit']['signature'] == 'Demolov 01'
    assert data['unit']['creator'] == 'Demolov (Římskokatolická církev)' and data['unit']['image_count'] == 3
    page = data['pages'][0]
    nid = D.nid('soap-pn/demolov-01')
    assert page['page_id'] == 'pf:book:soap-pn/demolov-01:1' and page['source_type'] == 'iiif' and page['archive'] == 'pf'
    assert page['viewer_url'] == f'https://www.portafontium.eu/iipimage/{nid}/demolov-01_0010-x'
    assert page['citation'] == data['citation'] and page['image_key'] == 'demolov-01_0010-x'

    view = await call(service, 'view_scan', research_id=sid, page_id=page['page_id'])
    ref = view.data['image']
    assert ref['archive'] == 'pf' and (ref['source_width'], ref['source_height']) == (1281, 897)
    assert ref['viewer_url'] == page['viewer_url'] and ref['citation'] == data['citation']
    sx, sy = ref['width'] / ref['source_width'], ref['height'] / ref['source_height']
    box = (200, 200, 120, 110)
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': box[0] * sx, 'y': box[1] * sy, 'width': box[2] * sx, 'height': box[3] * sy})
    z = zoom.data['image']
    x, y, w, h = z['source_bbox']
    got = Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).convert('RGB').resize((w, h))
    want = archive.image.crop((x, y, x + w, y + h)).convert('RGB')
    assert max(max(c) for c in ImageChops.difference(got, want).getextrema()) <= 64
    assert z['archive'] == 'pf' and z['viewer_url'] == page['viewer_url']
    urls = [u for u in archive.requests if HOST in u]
    assert all('IIIF=register/' in u for u in urls if 'fcgi-bin' in u)      # IIIF only: no DeepZoom tiles, no FIF thumbnails
    assert not any('DeepZoom' in u or 'FIF=' in u for u in urls)


@pytest.mark.asyncio
async def test_pf_crawl_delay_of_10_seconds_is_kept_between_requests(pf, clock):
    service, archive = pf
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:book:soap-pn/demolov-01')
    await call(service, 'view_scan', research_id=sid, page_id=opened.data['pages'][0]['page_id'])
    assert service.source._interval(HOST) == 10
    starts = [t for t, path, _ in service.times if path != '/robots.txt']
    assert len(starts) >= 6
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 10 - 1e-6, gaps
    assert sum(clock[1]) >= 10 * (len(starts) - 1) - 1e-6


@pytest.mark.asyncio
async def test_pf_listing_follows_pages_and_reports_a_cut(pf, monkeypatch):
    from rubrika import pf as P
    service, archive = pf
    sid = await research(service)           # no event type: no server-side narrowing, two result pages
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    full = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    assert full.data['total'] == 6 and full.data['listing_complete'] is True
    assert {r['relevance']['status'] for r in full.data['sources']} == {'needs_review'}
    assert sum('page=1' in u for u in archive.requests if '/searching/register' in u) >= 1
    monkeypatch.setattr(P, 'MAX_SEARCH_PAGES', 1)
    monkeypatch.setattr(service.source, 'cache_get', lambda *a, **k: None)
    cut = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    assert cut.data['total'] == 4 and cut.data['listing_complete'] is False
    assert any('načteno 1 (limit 1' in w for w in cut.data['warnings'])


@pytest.mark.asyncio
async def test_pf_place_search_variants(pf):
    service, _ = pf
    sid = await research(service)
    german = await call(service, 'find_place', research_id=sid, term='Demolau', kind='parish_registers', archives=['pf'])
    assert german.data['places'][0]['source_id'] == 'pf:place:demolov'
    partial = await call(service, 'find_place', research_id=sid, term='Demol', kind='parish_registers', archives=['pf'])
    assert partial.data['places'] == [] and partial.data['searched'] == ['pf']
    nothing = await call(service, 'find_place', research_id=sid, term='Nikdyneni', kind='parish_registers', archives=['pf'])
    assert nothing.data['places'] == [] and nothing.data['searched'] == ['pf'] and 'terms_notice' in nothing.data


@pytest.mark.asyncio
async def test_pf_two_places_in_one_listing_deduplicate_books(pf):
    service, _ = pf
    sid, _ = await demolov(service)
    listed = await call(service, 'list_sources', research_id=sid,
                        place_ids=['pf:place:demolov', 'pf:place:horni-demolov'], kind='parish_registers')
    ids = [r['source_id'] for r in listed.data['sources']]
    assert len(ids) == len(set(ids)) == 6


@pytest.mark.asyncio
async def test_pf_disabled_is_not_searched_and_makes_no_request(pf):
    service, archive = pf
    service.cfg = service.cfg.model_copy(update={'pf_enabled': False})
    sid = await research(service)
    before = len(archive.requests)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    assert out.data['searched'] == [] and out.data['not_searched'][0]['archive'] == 'pf' and len(archive.requests) == before
    assert 'terms-of-use' in out.data['not_searched'][0]['reason']
    res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers'))
    assert res.is_error and res.data['error']['code'] == 'LIVE_DISABLED'


@pytest.mark.asyncio
async def test_pf_view_scan_after_disable_is_live_disabled(pf):
    service, _ = pf
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:book:soap-pn/demolov-01')
    service.cfg = service.cfg.model_copy(update={'pf_enabled': False})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=opened.data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'LIVE_DISABLED'


@pytest.mark.asyncio
async def test_pf_only_offers_parish_registers(pf):
    service, archive = pf
    sid = await research(service)
    before = len(archive.requests)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='land_records', archives=['pf'])
    assert out.data['searched'] == [] and len(archive.requests) == before
    listed = await call(service, 'list_archives')
    row = next(a for a in listed.data['archives'] if a['code'] == 'pf')
    assert row['available_kinds'] == ['parish_registers'] and '§ 92' in row['terms_notice']


@pytest.mark.asyncio
async def test_pf_open_requires_listing_or_book_url(pf):
    service, _ = pf
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC link',
                     'book_url': 'https://www.portafontium.eu/register/soap-pn/demolov-02'})
    sid = out.data['research_id']
    ok = await call(service, 'open_source', research_id=sid, source_id='pf:book:soap-pn/demolov-02')
    assert ok.data['total'] == 2 and ok.data['unit']['signature'] == 'Demolov 02'
    bad = await service.call('open_source', 'alice', dict(research_id=sid, source_id='pf:book:soap-pn/demolov-01'))
    assert bad.is_error and bad.data['error']['code'] == 'NOT_FOUND'
    junk = await service.call('open_source', 'alice', dict(research_id=sid, source_id='pf:place:demolov'))
    assert junk.is_error and junk.data['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_pf_bad_book_url_is_rejected(pf):
    service, _ = pf
    for url in ('https://www.portafontium.eu/iipimage/30060004', 'https://www.portafontium.eu/admin/x'):
        res = await service.call('start_research', 'alice', dict(query={'raw_query': 'SYNTHETIC', 'book_url': url}))
        assert res.is_error and res.data['error']['code'] == 'INVALID_SOURCE'


@pytest.mark.asyncio
async def test_pf_list_rejects_filters_and_foreign_ids(pf):
    service, _ = pf
    sid, _ = await demolov(service)
    for extra in (dict(year_from=1800), dict(year_to=1800), dict(fund_marks=['C 17']), dict(district_archive='Znojmo')):
        res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov'],
                                                              kind='parish_registers', **extra))
        assert res.is_error and res.data['error']['code'] == 'INVALID_ARGUMENT'
    res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:book:soap-pn/demolov-01'], kind='parish_registers'))
    assert res.is_error and res.data['error']['code'] == 'INVALID_ARGUMENT'
    res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:neznama'], kind='parish_registers'))
    assert res.is_error and res.data['error']['code'] == 'NOT_FOUND'
    res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['pf:place:demolov', '900001'], kind='parish_registers'))
    assert res.is_error and res.data['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_pf_structure_change_is_reported_not_silently_empty(pf, monkeypatch):
    service, _ = pf
    sid = await research(service)
    monkeypatch.setattr(D, 'search_page', lambda query: '<html><body>Údržba</body></html>')
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    assert out.data['searched'] == [] and 'PARSE_ERROR' in out.data['not_searched'][0]['reason']


@pytest.mark.asyncio
async def test_pf_district_routing_reaches_pf(pf):
    service, _ = pf
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', district='Karlovy Vary')
    assert out.data['searched'] == ['pf'] and out.data['places']


@pytest.mark.asyncio
async def test_pf_evidence_in_report_and_gedcom_with_portal_url(pf):
    service, _ = pf
    sid, _ = await demolov(service)
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov'], kind='parish_registers')
    opened = await call(service, 'open_source', research_id=sid, source_id='pf:book:soap-pn/demolov-01')
    view = await call(service, 'view_scan', research_id=sid, page_id=opened.data['pages'][0]['page_id'])
    obs = await call(service, 'record_finding', research_id=sid, idempotency_key='k1', observation={
        'kind': 'record', 'image_id': view.data['image']['image_id'], 'text': 'SYNTHETIC zápis', 'is_candidate': True})
    status = await call(service, 'get_research_status', research_id=sid)
    book = status.data['coverage']['opened_books'][0]
    assert book['source'] == 'pf' and book['archive'] == 'pf' and book['book_id'] == 'pf:book:soap-pn/demolov-01'
    assert book['detail_url'] == 'https://www.portafontium.eu/register/soap-pn/demolov-01' and book['title'] == 'Demolov 01'
    assert status.data['coverage']['catalog_listings'][-1]['source'] == 'porta_fontium'
    tree = await call(service, 'export_family_tree', tree={'title': 'SYNTHETIC', 'root_person': 'p1', 'persons': [{
        'id': 'p1', 'given_name': 'Jan', 'surname': 'Demo', 'events': [{'type': 'birth', 'confidence': 'documented',
        'date': {'year': 1786}, 'sources': [{'research_id': sid, 'observation_id': obs.data['observation_id']}]}]}]})
    ged = tree.data['gedcom']
    assert '1 NAME Státní oblastní archiv v Plzni' in ged and 'Porta fontium, Demolov 01' in ged
    assert 'https://www.portafontium.eu/register/soap-pn/demolov-01' in ged


@pytest.mark.asyncio
async def test_pf_daily_request_cap_is_enforced(pf):
    service, _ = pf
    service.cfg = service.cfg.model_copy(update={'pf_global_daily_requests': 2})
    service.store.cfg = service.cfg
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    # robots.txt and the first search use the two allowed requests; the next one is over the cap
    out = await call(service, 'find_place', research_id=sid, term='Testov', kind='parish_registers', archives=['pf'])
    assert out.data['searched'] == [] and 'BUDGET_EXCEEDED' in out.data['not_searched'][0]['reason']


@pytest.mark.asyncio
async def test_mza_and_vca_are_unaffected_when_pf_is_on(auto_service):
    service, archive = auto_service          # shared settings: pf off
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC'})
    res = await call(service, 'find_place', research_id=out.data['research_id'], term='Demo Lhota', kind='parish_registers', archives=['mza'])
    assert res.data['searched'] == ['mza'] and 'terms_notice' not in res.data
    assert not any(HOST in u for u in archive.requests)


@pytest.mark.asyncio
async def test_pf_not_searched_without_district_but_searched_on_request(pf):
    service, archive = pf
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers')
    assert 'pf' not in out.data['searched'] and not any(HOST in u for u in archive.requests)
    assert any('Nehledáno v' in w and 'archives=["pf"]' in w for w in out.data['warnings'])
    for extra in (dict(district='Plzeň-město'), dict(archives=['pf'])):
        out = await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', **extra)
        assert out.data['searched'] == ['pf'] and out.data['places']
    assert any(HOST in u for u in archive.requests)


@pytest.mark.asyncio
async def test_pf_page_budget_is_shared_and_places_limited(pf, monkeypatch):
    from rubrika import pf as P
    service, archive = pf
    sid = await research(service)
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    monkeypatch.setattr(P, 'MAX_SEARCH_PAGES', 2)
    monkeypatch.setattr(service.source, 'cache_get', lambda *a, **k: None)
    before = len([u for u in archive.requests if '/searching/register' in u])
    out = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:demolov', 'pf:place:horni-demolov'], kind='parish_registers')
    assert len([u for u in archive.requests if '/searching/register' in u]) - before == 2
    assert out.data['listing_complete'] is False and any('nebylo prohledáno' in w for w in out.data['warnings'])
    res = await service.call('list_sources', 'alice', dict(research_id=sid, kind='parish_registers',
        place_ids=['pf:place:demolov', 'pf:place:horni-demolov', 'pf:place:testov']))
    assert res.is_error and res.data['error']['code'] == 'INVALID_ARGUMENT' and 'nejvýš 2' in res.data['error']['message']


@pytest.mark.asyncio
async def test_pf_empty_results_are_not_a_certain_negative(pf):
    service, archive = pf
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Nikdyneni', kind='parish_registers', archives=['pf'])
    assert out.data['places'] == [] and any('nevrátilo žádné knihy ani místa' in w for w in out.data['warnings'])
    await call(service, 'find_place', research_id=sid, term='Demolov', kind='parish_registers', archives=['pf'])
    service.store.put_entity('place', 'pf:place:nikdyneni', {'source_id': 'pf:place:nikdyneni', 'label': 'Nikdyneni', 'names': ['Nikdyneni'], 'source_url': 'x'})
    service.store.link(sid, 'alice', 'place_option', 'pf:place:nikdyneni', {})
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:nikdyneni'], kind='parish_registers')
    assert listed.data['total'] == 0 and listed.data['listing_complete'] is False
    assert any('nevrátilo žádné knihy' in w for w in listed.data['warnings'])
    n = len([u for u in archive.requests if 'Nikdyneni' in u])
    await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:nikdyneni'], kind='parish_registers')
    assert len([u for u in archive.requests if 'Nikdyneni' in u]) == n + 1     # the empty answer was not cached


@pytest.mark.asyncio
async def test_pf_place_books_are_those_listing_the_place_town_first_and_counts_agree(pf):
    service, _ = pf
    sid = await research(service)
    places = await call(service, 'find_place', research_id=sid, term='Mestov', kind='parish_registers', archives=['pf'])
    ids = [p['source_id'] for p in places.data['places']]
    assert places.data['places'][0]['label'] == 'Mestov (Stadtau) – včetně obvodů města'
    assert ids[0] == 'pf:place:mestov' and 'pf:place:obvod-mestov-i-vnitrni-mesto' in ids
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:mestov'], kind='parish_registers', limit=20)
    numbers = [r['number'] for r in listed.data['sources']]
    # the town's districts are the town's own books; villages that merely list the town come last
    assert numbers == ['Mestov 101', 'Mestov 01', 'Mestov 102', 'Mestov 02', 'Vesnice 01'] and listed.data['total'] == 5
    assert not [w for w in listed.data['warnings'] if 'Portál vrátil' in w]
    # the district has its own place id and lists its own books
    obvod = await call(service, 'list_sources', research_id=sid, place_ids=['pf:place:obvod-mestov-i-vnitrni-mesto'], kind='parish_registers')
    assert [r['number'] for r in obvod.data['sources']] == ['Mestov 101', 'Mestov 102']


@pytest.mark.asyncio
async def test_pf_open_works_when_the_detail_lists_images_only_in_the_sidebar(pf, monkeypatch):
    service, _ = pf
    monkeypatch.setattr(D, 'detail_page', lambda slug, sidebar=False, _o=D.detail_page: _o(slug, sidebar=True))
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC', 'book_url': 'https://www.portafontium.eu/register/soap-pn/demolov-02'})
    ok = await call(service, 'open_source', research_id=out.data['research_id'], source_id='pf:book:soap-pn/demolov-02')
    assert ok.data['total'] == 2
