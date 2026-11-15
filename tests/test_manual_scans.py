"""Archives that do not let tools fetch scans (Archive.manual_scans): MZA since November 2026 (Acta Publica behind an ALTCHA
proof-of-work, ARON/census/sketch tiles refused) and SOA Praha (robots.txt disallows the digitised copies). Their catalogues
are still searched; open_source lists the scans; view_scan and zoom_scan answer MANUAL_SOURCE before any request.
The other tests switch these archives' scans on (conftest.synthetic_scans) to exercise the image path on synthetic data."""
import pytest
import pytest_asyncio

from conftest import call
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store
from test_aron_service import aron_flow
from test_soapraha_service import build, clock, hits, opened_book   # noqa: F401  (clock is a fixture)
from test_vca_service import vca_listing
from rubrika import demo_aron as D

OFF = dict(mza_scans_enabled=False, soapraha_scans_enabled=False)


@pytest_asyncio.fixture
async def manual(tmp_path):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, aron_enabled=True, census_enabled=True, vca_enabled=True,
                   request_interval=0, **OFF)
    store = Store(cfg)
    archive = DemoArchive()
    service = ResearchService(cfg, store, SafeSource(cfg, store, transport=archive.transport, source_mode='synthetic_demo'))
    yield service, archive
    await service.close()
    store.close()


async def listed_register(service):
    sid = (await call(service, 'start_research', query={'raw_query': 'Synthetic test', 'place_hint': 'Demo Lhota', 'event_type': 'birth',
                                                         'year_from': 1840, 'year_to': 1845})).data['research_id']
    places = await call(service, 'find_place', research_id=sid, term='Demo Lhota', kind='parish_registers', archives=['mza'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[places.data['places'][0]['source_id']], kind='parish_registers')
    return sid, listed.data['sources'][0]


def test_defaults_keep_mza_and_soapraha_scans_manual():
    cfg = Settings(_env_file=None, mza_scans_enabled=False, soapraha_scans_enabled=False)
    plain = Settings.model_fields
    assert plain['mza_scans_enabled'].default is False and plain['soapraha_scans_enabled'].default is False
    assert not cfg.mza_scans_enabled and not cfg.soapraha_scans_enabled


@pytest.mark.asyncio
async def test_mza_register_opens_from_the_catalogue_without_a_request_and_points_to_the_browser(manual):
    service, archive = manual
    sid, book = await listed_register(service)
    before = len(archive.requests)
    opened = await call(service, 'open_source', research_id=sid, source_id=book['source_id'], limit=2)
    assert len(archive.requests) == before, 'the detail page with the image viewer is never fetched'
    data = opened.data
    assert data['total'] == book['image_count'] and [p['scan_number'] for p in data['pages']] == [1, 2]
    assert all(p['source_type'] == 'manual' and p['viewer_url'] == book['detail_url'] for p in data['pages'])
    assert 'Omezený režim' in data['note'] and 'import_scan' in data['note'] and 'ověřením proti robotům' in data['manual_scans']
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][1]['page_id']))
    err = out.data['error']
    assert out.is_error and err['code'] == 'MANUAL_SOURCE' and 'import_scan' in err['message']
    assert err['details']['viewer_url'] == book['detail_url'] and err['details']['scan_number'] == 2
    by_number = await service.call('view_scan', 'alice', dict(research_id=sid, source_id=book['source_id'], scan_number=1))
    assert by_number.data['error']['code'] == 'MANUAL_SOURCE' and by_number.data['error']['details']['scan_number'] == 1
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_mza_land_record_scans_are_never_fetched(manual):
    service, archive = manual
    sid, _, _ = await aron_flow(service)
    unit = await call(service, 'open_source', research_id=sid, source_id='aron:' + D.UNIT_LAND)
    assert 'manual_scans' in unit.data
    before = len(archive.requests)
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=unit.data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'MANUAL_SOURCE'
    assert out.data['error']['details']['viewer_url'] == unit.data['pages'][0]['viewer_url']
    assert len(archive.requests) == before, 'no descriptor or tile request'


@pytest.mark.asyncio
async def test_vca_shares_the_aron_client_but_keeps_its_scans(manual):
    service, _ = manual
    sid, _, listed = await vca_listing(service)
    opened = await call(service, 'open_source', research_id=sid, source_id=listed['sources'][0]['source_id'])
    assert 'manual_scans' not in opened.data
    view = await call(service, 'view_scan', research_id=sid, page_id=opened.data['pages'][0]['page_id'])
    assert view.data['image']['archive'] == 'vca'


@pytest.mark.asyncio
async def test_an_overview_made_while_scans_were_allowed_is_not_zoomed_again(manual):
    service, archive = manual
    service.cfg.mza_scans_enabled = True
    sid, book = await listed_register(service)
    opened = await call(service, 'open_source', research_id=sid, source_id=book['source_id'])
    view = await call(service, 'view_scan', research_id=sid, page_id=opened.data['pages'][0]['page_id'])
    service.cfg.mza_scans_enabled = False
    before = len(archive.requests)
    ref = view.data['image']
    out = await service.call('zoom_scan', 'alice', dict(research_id=sid, image_id=ref['image_id'],
                                                        bbox_px={'x': 0, 'y': 0, 'width': ref['width'] / 4, 'height': ref['height'] / 4}))
    assert out.is_error and out.data['error']['code'] == 'MANUAL_SOURCE'
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_list_archives_names_the_archives_without_scans(manual):
    service, _ = manual
    rows = {a['code']: a for a in (await call(service, 'list_archives')).data['archives']}
    assert 'ALTCHA' not in rows['mza']['manual_scans'] and 'import_scan' in rows['mza']['manual_scans']
    assert 'robots.txt' in rows['soapraha']['manual_scans']
    assert not any('manual_scans' in rows[c] for c in ('vca', 'pf', 'ahmp', 'na'))


@pytest.mark.asyncio
async def test_soapraha_scans_are_never_fetched(tmp_path, clock):
    service, archive = build(tmp_path, clock, **OFF)
    try:
        sid, data = await opened_book(service)
        assert 'robots.txt' in data['manual_scans']
        before = list(hits(archive))
        out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
        assert out.is_error and out.data['error']['code'] == 'MANUAL_SOURCE'
        assert out.data['error']['details']['viewer_url'] == data['pages'][0]['viewer_url']
        assert list(hits(archive)) == before
    finally:
        await service.close()
        service.store_.close()
