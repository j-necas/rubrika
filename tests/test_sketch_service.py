"""MZA indication sketches end to end against the synthetic application: find_place, list_sources, open_source, view_scan, zoom_scan."""
import base64
import io

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from conftest import call
from rubrika import demo_sketches as D
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

DEMOVICE_ZNOJMO, DEMOVICE_VYSKOV, TESTOV = 'mza:sketch:place:0901', 'mza:sketch:place:0902', 'mza:sketch:place:0904'
SKETCH = 'mza:sketch:MOR090118250'
CITATION = 'Moravský zemský archiv v Brně, fond D 9 Stabilní katastr – indikační skici, Demovice, MOR090118250'


def build(tmp_path, **settings):
    settings = {'sketches_enabled': True, 'aron_enabled': True, 'census_enabled': True, **settings}
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, request_interval=0, **settings)
    store = Store(cfg)
    archive = DemoArchive()
    source = SafeSource(cfg, store, transport=archive.transport, source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    service.store_ = store
    return service, archive


@pytest_asyncio.fixture
async def sk(tmp_path):
    service, archive = build(tmp_path)
    yield service, archive
    await service.close()
    service.store_.close()


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC sketch test', 'place_hint': 'Demovice', **query})
    return out.data['research_id']


async def places(service, sid, term='Demovice', **kw):
    return (await call(service, 'find_place', research_id=sid, term=term, kind='cadastral_maps', **kw)).data


async def opened(service, place=DEMOVICE_ZNOJMO, source=SKETCH, term='Demovice', **query):
    sid = await research(service, **query)
    await places(service, sid, term=term, archives=['mza'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[place], kind='cadastral_maps')
    out = await call(service, 'open_source', research_id=sid, source_id=source)
    return sid, listed.data, out.data


def sketch_hits(archive):
    return archive.sketches.log


def pixels(out):
    return Image.open(io.BytesIO(base64.b64decode(out.image_b64))).convert('RGB')


def close(a, b, tol=14):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@pytest.mark.asyncio
async def test_find_place_lists_katastrs_with_district_and_german_name(sk):
    service, archive = sk
    sid = await research(service)
    out = await places(service, sid, archives=['mza'])
    assert out['searched'] == ['mza'] and out['kind'] == 'cadastral_maps' and out['ambiguous']
    by_id = {p['source_id']: p for p in out['places']}
    assert list(by_id) == [DEMOVICE_ZNOJMO, DEMOVICE_VYSKOV, 'mza:sketch:place:0903'] and {p['archive'] for p in out['places']} == {'mza'}
    znojmo = by_id[DEMOVICE_ZNOJMO]
    assert 'Demovice (Demowitz)' in znojmo['label'] and 'okres Znojmo' in znojmo['label'] and znojmo['names'] == ['Demovice', 'Demowitz']
    assert znojmo['source_url'] == 'https://www.mza.cz/indikacniskici/skica?signatura=0901'
    assert any('stejného jména' in w for w in out['warnings'])
    assert 'Indikační skici MZA' in out['terms_notice'] and 'https://www.mza.cz/indikacniskici/page/view/podminky-uziti-sluzeb' in out['terms_notice']
    assert set(p.split('?')[0] for p in sketch_hits(archive)) == {'/indikacniskici/skica'}


@pytest.mark.asyncio
async def test_find_place_by_german_name_and_by_district(sk):
    service, archive = sk
    sid = await research(service)
    german = await places(service, sid, term='Demowitz', archives=['mza'])
    assert [p['source_id'] for p in german['places']][:2] == [DEMOVICE_ZNOJMO, DEMOVICE_VYSKOV]
    znojmo = await places(service, sid, district='Znojmo')
    assert [p['source_id'] for p in znojmo['places']] == [DEMOVICE_ZNOJMO] and znojmo['searched'] == ['mza'] and not znojmo['not_searched']
    assert 'okres=Znojmo' in ''.join(sketch_hits(archive))


@pytest.mark.asyncio
async def test_find_place_without_result_says_it_is_no_proof(sk):
    service, _ = sk
    sid = await research(service)
    out = await places(service, sid, term='Nikdeje', archives=['mza'])
    assert out['places'] == [] and any('Nejde o důkaz' in w for w in out['warnings'])


@pytest.mark.asyncio
@pytest.mark.parametrize('district', ['Tábor', 'Plzeň-město', 'Karviná', 'Praha'])
async def test_bohemian_district_gets_only_the_cuzk_link_and_no_request(sk, district):
    service, archive = sk
    sid = await research(service)
    before = len(archive.requests)
    out = await places(service, sid, district=district)
    assert len(archive.requests) == before and out['places'] == [] and out['searched'] == []
    (row,) = out['not_searched']
    assert row['archive'] == 'cuzk' and row['homepage'] == 'https://ags.cuzk.gov.cz/archiv/'
    assert 'token' in row['reason'] and 'poplatek' in row['reason'] and 'Rubrika ho nezískává ani neobchází' in row['reason']


@pytest.mark.asyncio
async def test_border_district_searches_mza_and_links_cuzk(sk):
    service, _ = sk
    sid = await research(service)
    out = await places(service, sid, term='Demovice', district='Jihlava')
    assert out['searched'] == ['mza'] and [r['archive'] for r in out['not_searched']] == ['cuzk']
    assert [p['source_id'] for p in out['places']] == ['mza:sketch:place:0903']


@pytest.mark.asyncio
async def test_without_district_mza_is_searched_and_cuzk_is_offered(sk):
    service, _ = sk
    sid = await research(service)
    out = await places(service, sid)
    assert out['searched'] == ['mza'] and [r['archive'] for r in out['not_searched']] == ['cuzk']


@pytest.mark.asyncio
async def test_explicit_cuzk_is_never_searched(sk):
    service, archive = sk
    sid = await research(service)
    before = len(archive.requests)
    out = await places(service, sid, archives=['cuzk'])
    assert len(archive.requests) == before and [r['archive'] for r in out['not_searched']] == ['cuzk']
    listed = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['cuzk:whatever'], kind='cadastral_maps'))
    assert listed.is_error and listed.data['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_switch_off_makes_sketches_not_searched(tmp_path):
    service, archive = build(tmp_path, sketches_enabled=False)
    try:
        sid = await research(service)
        out = await places(service, sid, archives=['mza'])
        assert out['places'] == [] and out['searched'] == []
        (row,) = out['not_searched']
        assert row['archive'] == 'mza' and 'vypnutý' in row['reason'] and 'podminky-uziti-sluzeb' in row['reason']
        assert not sketch_hits(archive)
        rows = {a['code']: a for a in (await call(service, 'list_archives')).data['archives']}
        assert 'cadastral_maps' in rows['mza']['kinds'] and 'cadastral_maps' not in rows['mza']['available_kinds']
        assert rows['cuzk']['link_only'] and rows['cuzk']['available_kinds'] == [] and rows['cuzk']['kinds'] == ['cadastral_maps']
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_list_archives_shows_where_a_district_goes_for_sketches(sk):
    service, _ = sk
    rows = {a['code']: a for a in (await call(service, 'list_archives')).data['archives']}
    assert 'cadastral_maps' in rows['mza']['available_kinds']
    out = (await call(service, 'list_archives', district='Znojmo')).data['district']
    assert out['cadastral_maps'] == ['mza'] and out['archives'] == ['mza']
    assert (await call(service, 'list_archives', district='Cheb')).data['district']['cadastral_maps'] == ['cuzk']


@pytest.mark.asyncio
async def test_list_sources_gives_every_sketch_of_the_katastr_oldest_first(sk):
    service, _ = sk
    sid = await research(service)
    await places(service, sid, term='Testov', archives=['mza'])
    out = (await call(service, 'list_sources', research_id=sid, place_ids=[TESTOV], kind='cadastral_maps')).data
    assert out['archive'] == 'mza' and out['kind'] == 'cadastral_maps' and out['total'] == 3
    assert [(r['source_id'], r['year'], r['sheet']) for r in out['sources']] == [
        ('mza:sketch:MOR090419110', 1911, 0), ('mza:sketch:MOR090419111', 1911, 1), ('mza:sketch:MOR090400000', None, 0)]
    first = out['sources'][0]
    assert first['sheets_in_year'] == 2 and first['katastr'] == 'Testov-Horní' and first['german_name'] == 'Ober Testow' and first['district'] == 'Brno-město'
    assert first['detail_url'] == 'https://www.mza.cz/indikacniskici/skica/detail/MOR090419110' and first['other_names'] == 'Horní Testov'
    assert out['sources'][1]['note'] == 'neúplná' and out['sources'][2]['note'] == 'Bez data'
    assert all(r['relevance']['status'] == 'candidate' and r['archive'] == 'mza' and r['kind'] == 'cadastral_maps' for r in out['sources'])
    assert out['counts'] == {'candidate': 3} and out['listing_complete'] and 'Několik listů' in out['note']
    assert 'podminky-uziti-sluzeb' in out['terms_notice']
    page = (await call(service, 'list_sources', research_id=sid, place_ids=[TESTOV], kind='cadastral_maps', limit=2)).data
    assert len(page['sources']) == 2 and page['next_cursor'] == 2


@pytest.mark.asyncio
async def test_list_sources_uses_the_signature_not_the_name(sk):
    service, archive = sk
    sid = await research(service)
    await places(service, sid, archives=['mza'])
    archive.sketches.log.clear()
    out = (await call(service, 'list_sources', research_id=sid, place_ids=[DEMOVICE_VYSKOV], kind='cadastral_maps')).data
    assert [r['source_id'] for r in out['sources']] == ['mza:sketch:MOR090218270'] and out['sources'][0]['other_municipalities'] == 'Podlesí (Unterwald)'
    assert archive.sketches.log == ['/indikacniskici/skica?signatura=0902']


@pytest.mark.asyncio
async def test_list_sources_argument_errors(sk):
    service, _ = sk
    sid = await research(service)
    await places(service, sid, archives=['mza'])
    async def err(**kw):
        out = await service.call('list_sources', 'alice', dict(research_id=sid, kind='cadastral_maps', **kw))
        assert out.is_error
        return out.data['error']['code']
    assert await err(place_ids=[DEMOVICE_ZNOJMO], year_from=1800) == 'INVALID_ARGUMENT'
    assert await err(place_ids=[DEMOVICE_ZNOJMO], fund_marks=['D 9']) == 'INVALID_ARGUMENT'
    assert await err(place_ids=[DEMOVICE_ZNOJMO], district_archive='Znojmo') == 'INVALID_ARGUMENT'
    assert await err(place_ids=['900001']) == 'INVALID_ARGUMENT'                       # a register place is not a katastr
    assert await err(place_ids=['mza:sketch:place:0999']) == 'NOT_FOUND'              # never found in this research
    other = await service.call('list_sources', 'alice', dict(research_id=sid, kind='parish_registers', place_ids=[DEMOVICE_ZNOJMO]))
    assert other.is_error and other.data['error']['code'] == 'INVALID_ARGUMENT'
    land = await service.call('list_sources', 'alice', dict(research_id=sid, kind='land_records', place_ids=[DEMOVICE_ZNOJMO]))
    assert land.is_error and 'cadastral_maps' in land.data['error']['message']


@pytest.mark.asyncio
async def test_open_source_gives_front_and_back_with_citation_and_links(sk):
    service, _ = sk
    sid, _, out = await opened(service)
    assert out['archive'] == 'mza' and out['kind'] == 'cadastral_maps' and out['citation'] == CITATION and out['total'] == 2
    unit = out['unit']
    assert unit['sides'] == ['front', 'back'] and unit['grid_tiles'] == {'cols': 2, 'rows': 2} and unit['year'] == 1825
    front, back = out['pages']
    assert front['page_id'] == SKETCH + ':1' and back['page_id'] == SKETCH + ':2' and (front['scan_number'], back['scan_number']) == (1, 2)
    assert front['viewer_url'] == 'https://www.mza.cz/indikacniskici/skica/detail/MOR090118250'
    assert back['viewer_url'] == f'https://www.mza.cz/indikacniskici/skica/detail/{D.NUMBERS["MOR090118250"]}?site=BACK'
    assert front['citation'] == CITATION and back['citation'] == CITATION + ', zadní strana skici'
    assert front['source'] == 'sketch' and front['source_type'] == 'sketch' and front['archive'] == 'mza'
    assert 'podminky-uziti-sluzeb' in out['terms_notice'] and 'líc' in out['note'] and 'D 10' in out['note']
    status = await call(service, 'get_research_status', research_id=sid)
    (summary,) = status.data['coverage']['opened_books']
    assert summary['source'] == 'sketch' and summary['archive'] == 'mza' and summary['number'] == 'MOR090118250' and summary['title'] == 'Demovice, 1825'


@pytest.mark.asyncio
async def test_sketch_without_back_side_has_one_page(sk):
    service, _ = sk
    _, _, out = await opened(service, place=TESTOV, source='mza:sketch:MOR090419111', term='Testov')
    assert out['total'] == 1 and out['unit']['sides'] == ['front'] and out['unit']['note'] == 'neúplná'
    assert any('Poznámka archivu' in w for w in out['warnings'])


@pytest.mark.asyncio
async def test_open_source_needs_a_listed_sketch_or_a_link_in_the_query(sk):
    service, _ = sk
    sid = await research(service)
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id=SKETCH))
    assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='mza:sketch:place:0901'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    linked = await research(service, book_url='https://www.mza.cz/indikacniskici/skica/detail/MOR090118250')
    out = await call(service, 'open_source', research_id=linked, source_id=SKETCH)
    assert out.data['unit']['katastr'] == 'Demovice' and out.data['unit']['signature'] == '0901' and out.data['total'] == 2


@pytest.mark.asyncio
async def test_view_scan_assembles_the_overview_from_the_tiles_that_exist(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    archive.sketches.log.clear()
    view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
    ref = view.data['image']
    assert (ref['source_width'], ref['source_height']) == (5208, 4176) and ref['role'] == 'overview' and ref['archive'] == 'mza'
    assert ref['citation'] == CITATION and ref['viewer_url'].endswith('/MOR090118250') and ref['source'] == 'sketch'
    tiles = [p for p in archive.sketches.log if '/data/obrazky/' in p]
    assert len(tiles) == 27 and all('/Img/little/A0' in p for p in tiles)                  # three level-1 tiles, 3 x 3 each; A02B is blank
    assert not any('A02B' in p for p in tiles)
    image = pixels(view)
    assert (image.width, image.height) == (ref['width'], ref['height']) and max(image.size) == 1000
    scale = ref['width'] / 5208
    def at(tx, ty):                       # centre of level-2 tile (tx, ty)
        return image.getpixel((int((tx + .5) * 217 / .25 * scale), int((ty + .5) * 174 / .25 * scale)))
    assert close(at(0, 0), D.tile_color(2, 0, 0)) and close(at(4, 1), D.tile_color(2, 4, 1)) and close(at(1, 4), D.tile_color(2, 1, 4))
    assert at(4, 4) == (255, 255, 255)                                                     # the missing tile stays white


@pytest.mark.asyncio
async def test_zoom_scan_uses_the_finest_level_for_a_small_area(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
    ref = view.data['image']
    archive.sketches.log.clear()
    factor = ref['source_width'] / ref['width']
    # source region 434 x 348 px at the top left = the four finest tiles (0,0) (1,0) (0,1) (1,1) of level 4
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': 0, 'y': 0, 'width': 434 / factor, 'height': 348 / factor})
    crop = zoom.data['image']
    assert crop['role'] == 'detail' and crop['source_bbox'][2] <= 435 and crop['source_bbox'][3] <= 349
    tiles = sorted(p for p in archive.sketches.log if '/data/obrazky/' in p)
    assert tiles == [f'/indikacniskici/data/obrazky/MOR090118250/Img/Normal/A01A-A1-A1-{n}.jpg' for n in ('A1', 'A2', 'B1', 'B2')]
    assert zoom.image_b64


@pytest.mark.asyncio
async def test_zoom_pixels_land_where_the_tiles_belong(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
    ref = view.data['image']
    # exactly level-4 tile (5, 2): at level 4 one tile is 217 x 174 source pixels
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=ref['image_id'],
                      bbox_px={'x': 5 * 217 * ref['width'] / 5208, 'y': 2 * 174 * ref['height'] / 4176,
                               'width': 217 * ref['width'] / 5208, 'height': 174 * ref['height'] / 4176})
    image = pixels(zoom)
    assert close(image.getpixel((image.width // 2, image.height // 2)), D.tile_color(4, 5, 2))
    assert any('/Img/Normal/A01A-B1-A2-B1.jpg' in p for p in archive.sketches.log)


@pytest.mark.asyncio
async def test_back_side_reads_its_own_page_and_tiles(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    archive.sketches.log.clear()
    view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][1]['page_id'])
    log = archive.sketches.log
    assert f'/indikacniskici/skica/detail/{D.NUMBERS["MOR090118250"]}?site=BACK' in log
    assert any('/Img/little/R01A-' in p for p in log) and not any('/Img/little/A0' in p for p in log)
    assert view.data['image']['citation'].endswith('zadní strana skici')


@pytest.mark.asyncio
async def test_whole_sheet_that_does_not_fit_the_tile_limit_comes_from_the_thumbnail(tmp_path):
    service, archive = build(tmp_path, max_tiles_per_call=1)
    try:
        sid, _, out = await opened(service)
        archive.sketches.log.clear()
        view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
        assert [p for p in archive.sketches.log if '/data/obrazky/' in p] == ['/indikacniskici/data/obrazky/MOR090118250/Img/map.jpg']
        img = view.data['image']
        assert img['source_width'] == 5208 and (img['width'], img['height']) == (108, 86)          # native thumbnail size, not upscaled
        assert img['low_resolution'] is True and img['effective_scale'] == pytest.approx(108 / 5208, rel=0.01)
        assert view.data['low_resolution'] is True and 'nízkém rozlišení' in view.data['warnings'][0]
        archive.sketches.log.clear()
        ref = view.data['image']
        zoom = await service.call('zoom_scan', 'alice', dict(research_id=sid, image_id=ref['image_id'],
                                                             bbox_px={'x': 0, 'y': 0, 'width': ref['width'] - 1, 'height': ref['height']}))
        assert zoom.is_error and zoom.data['error']['code'] == 'REGION_TOO_LARGE' and 'menší oblast' in zoom.data['error']['message']
        assert not any('/data/obrazky/' in p for p in archive.sketches.log)
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_a_missing_tile_is_an_incomplete_image(sk, monkeypatch):
    service, archive = sk
    sid, _, out = await opened(service)
    real = archive.sketches.handle
    def handle(request):
        if '/Img/little/A01B-B2' in request.url.path:
            return httpx.Response(404, text='gone', headers=D.HTML)
        return real(request)
    monkeypatch.setattr(archive.sketches, 'handle', handle)
    view = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=out['pages'][0]['page_id']))
    assert view.is_error and view.data['error']['code'] == 'INCOMPLETE_IMAGE'


@pytest.mark.asyncio
async def test_wrongly_sized_tile_is_refused(sk, monkeypatch):
    service, archive = sk
    sid, _, out = await opened(service)
    real = archive.sketches.handle
    def handle(request):
        if request.url.path.endswith('/A01A-A1.jpg'):
            buf = io.BytesIO(); Image.new('RGB', (100, 100)).save(buf, format='JPEG')
            return httpx.Response(200, content=buf.getvalue(), headers={'content-type': 'image/jpeg'})
        return real(request)
    monkeypatch.setattr(archive.sketches, 'handle', handle)
    view = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=out['pages'][0]['page_id']))
    assert view.is_error and view.data['error']['code'] == 'INCOMPLETE_IMAGE'


@pytest.mark.asyncio
async def test_changed_search_page_is_reported_not_treated_as_no_result(sk, monkeypatch):
    service, archive = sk
    sid = await research(service)
    real = archive.sketches.handle
    def broken(request):
        response = real(request)
        if request.url.path.endswith('/skica'):
            return httpx.Response(200, text=response.text.replace('Okres', 'District'), headers=D.HTML)
        return response
    monkeypatch.setattr(archive.sketches, 'handle', broken)
    out = await places(service, sid, archives=['mza'])
    assert out['places'] == [] and out['searched'] == [] and 'PARSE_ERROR' in out['not_searched'][0]['reason']


@pytest.mark.asyncio
async def test_changed_viewer_script_is_a_parse_error_when_opening(sk, monkeypatch):
    service, archive = sk
    sid = await research(service)
    await places(service, sid, archives=['mza'])
    await call(service, 'list_sources', research_id=sid, place_ids=[DEMOVICE_ZNOJMO], kind='cadastral_maps')
    real = archive.sketches.handle
    def broken(request):
        response = real(request)
        if '/skica/detail/' in request.url.path and response.status_code == 200:
            return httpx.Response(200, text=response.text.replace('maxLevel: 4,', 'maxLevel: 6,'), headers=D.HTML)
        return response
    monkeypatch.setattr(archive.sketches, 'handle', broken)
    res = await service.call('open_source', 'alice', dict(research_id=sid, source_id=SKETCH))
    assert res.is_error and res.data['error']['code'] == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_disabled_switch_stops_opening_and_viewing(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    service.cfg.sketches_enabled = False
    before = len(archive.requests)
    for tool, args in (('view_scan', dict(page_id=out['pages'][0]['page_id'])),
                       ('list_sources', dict(place_ids=[DEMOVICE_ZNOJMO], kind='cadastral_maps')),
                       ('open_source', dict(source_id=SKETCH))):
        res = await service.call(tool, 'alice', dict(research_id=sid, **args))
        assert res.is_error and res.data['error']['code'] == 'LIVE_DISABLED', tool
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_only_get_requests_to_allowed_paths_with_the_declared_user_agent(sk):
    service, archive = sk
    sid, _, out = await opened(service)
    await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
    assert archive.sketches.last_ua.startswith('rubrika/') and '(+https://github.com/j-necas/rubrika)' in archive.sketches.last_ua
    assert all(p.startswith('/indikacniskici/') or p == '/robots.txt' for p in archive.sketches.log)
    assert all(str(u).startswith('https://www.mza.cz/') for u in archive.requests)


@pytest.mark.asyncio
async def test_saved_finding_cites_the_sketch(sk):
    service, _ = sk
    sid, _, out = await opened(service)
    view = await call(service, 'view_scan', research_id=sid, page_id=out['pages'][0]['page_id'])
    ref = view.data['image']
    await call(service, 'record_finding', research_id=sid, idempotency_key='k1',
               observation={'kind': 'record', 'image_id': ref['image_id'], 'text': 'SYNTHETIC: house number 12', 'fields': {'house_number': '12'}})
    findings = (await call(service, 'list_findings', research_id=sid)).data['observations']
    assert findings[0]['book_id'] == SKETCH and findings[0]['evidence']['citation'] == CITATION


@pytest.mark.asyncio
async def test_daily_request_cap_of_the_sketch_application(tmp_path):
    service, archive = build(tmp_path, sketches_global_daily_requests=3)
    try:
        sid = await research(service)
        await places(service, sid, archives=['mza'])                        # robots.txt + one search = 2 requests
        out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[DEMOVICE_ZNOJMO], kind='cadastral_maps'))
        assert not out.is_error                                             # third request
        out = await service.call('open_source', 'alice', dict(research_id=sid, source_id=SKETCH))
        assert out.is_error and out.data['error']['code'] == 'BUDGET_EXCEEDED'
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_cuzk_asked_for_another_kind_says_it_does_not_hold_it_and_gets_no_import_hint(sk):
    service, _ = sk
    sid = await research(service)
    out = (await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['cuzk'])).data
    assert out['not_searched'][0]['reason'] == 'Archiv tento druh pramene nemá.'
    rows = {a['code']: a for a in (await call(service, 'list_archives')).data['archives']}
    assert 'manual_import' not in rows['cuzk'] and 'manual_import' in rows['zao']
