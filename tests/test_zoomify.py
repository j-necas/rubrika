import dataclasses
import io
import logging

import httpx
import pytest
from PIL import Image, ImageChops, ImageStat

from rubrika import archives as A
from rubrika import demo_zoomify as Z
from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.images import OVERVIEW_TIER_TOLERANCE
from rubrika.images import ImageService, parse_zoomify
from rubrika.models import Page
from rubrika.network import SafeSource, SourcePolicy
from rubrika.storage import Store

NA_XML = (b'<IMAGE_PROPERTIES WIDTH="4379" HEIGHT="3284" NUMTILES="326" NUMIMAGES="1" VERSION="1.8" '
          b'TILESIZE="256" DPIX="-1" DPIY="-1"/>')
AHMP_XML = (b"<IMAGE_PROPERTIES WIDTH='1491' HEIGHT='4025' NUMTILES='131' NUMIMAGES='1' VERSION='1.8' "
            b"TILESIZE='256' DPIX='300' DPIY='300' />")
ORIGINAL = dict(A.ARCHIVES)
URL = Z.AHMP_ROOT + '/ImageProperties.xml'


def grid(z): return [(-(-w // 256), -(-h // 256)) for w, h in z.tiers]


def test_tier_math_of_two_real_descriptors():
    na = parse_zoomify(NA_XML, Z.NA_ROOT + '/ImageProperties.xml', 80000000)
    assert (na.width, na.height, na.tile_size, na.num_tiles) == (4379, 3284, 256, 326)
    assert grid(na) == [(1, 1), (2, 1), (3, 2), (5, 4), (9, 7), (18, 13)]
    ahmp = parse_zoomify(AHMP_XML, URL, 80000000)
    assert grid(ahmp) == [(1, 1), (1, 2), (2, 4), (3, 8), (6, 16)]
    assert ahmp.tiers[-1] == (1491, 4025) and ahmp.tiers[0][0] <= 256


def test_tile_url_group_index_beyond_256():
    na = parse_zoomify(NA_XML, Z.NA_ROOT + '/ImageProperties.xml', 80000000)
    assert na.tile_url(0, 0, 0) == Z.NA_ROOT + '/TileGroup0/0-0-0.jpg'
    # 1+2+6+20+63 = 92 tiles below the top tier (18 columns): index 92 + 13*18... last tile is 325.
    assert na.tile_url(5, 17, 12) == Z.NA_ROOT + '/TileGroup1/5-17-12.jpg'
    assert na.tile_url(5, 0, 0) == Z.NA_ROOT + '/TileGroup0/5-0-0.jpg'
    assert '//cz/' in na.tile_url(5, 0, 0)          # the double slash is part of the NA path


@pytest.mark.parametrize('raw', [
    NA_XML.replace(b'NUMTILES="326"', b'NUMTILES="327"'),
    NA_XML.replace(b'WIDTH="4379"', b'WIDTH="0"'),
    NA_XML.replace(b'TILESIZE="256"', b'TILESIZE="4"'),
    NA_XML.replace(b'WIDTH="4379"', b'WIDTH="400000"'),
    b'<!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]><IMAGE_PROPERTIES/>',
    b'<Image TileSize="256"/>',
    b'not xml',
])
def test_invalid_descriptors(raw):
    with pytest.raises(ResearchError) as e:
        parse_zoomify(raw, URL, 80000000)
    assert e.value.code == 'PARSE_ERROR'


@pytest.fixture(scope='module')
def archive_module():
    return Z.ZoomifyArchive()


@pytest.fixture
def zcfg(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, ahmp_enabled=True, na_enabled=True,
                    request_interval=0)


@pytest.fixture
def zstore(zcfg):
    s = Store(zcfg)
    yield s
    s.close()


@pytest.fixture
def zsid(zstore):
    from rubrika.models import SearchQuery
    return zstore.create('alice', SearchQuery(raw_query='Synthetic', event_type='birth', year_from=1840, year_to=1845))['research_id']


@pytest.fixture(autouse=True)
def fast_hosts(monkeypatch):
    for code in ('ahmp', 'na'):
        monkeypatch.setitem(A.ARCHIVES, code, dataclasses.replace(A.ARCHIVES[code], min_interval=0))
    Z.MISSING_TILES.clear()
    yield
    Z.MISSING_TILES.clear()


def make_service(zcfg, zstore, archive):
    source = SafeSource(zcfg, zstore, transport=archive.transport, source_mode='synthetic_demo')
    return ImageService(source, zstore, zcfg), source


def page_for(archive, code='ahmp', pid='p1', book='b1'):
    return Page(page_id=pid, book_id=book, scan_number=1, image_key='x.jpg', source_url=archive.descriptor_url,
                source_type='zoomify', viewer_url='https://katalog.ahmp.cz/pragapublica/x', manifest_sha256='t', archive=code)


@pytest.mark.asyncio
async def test_crop_across_tile_borders_and_edges_matches_source(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        page = page_for(archive_module)
        desc, w, h = await images.descriptor(zsid, 'alice', page)
        assert (w, h) == (4379, 3284)
        for region in [(200, 200, 400, 300),          # crosses the 256 and 512 borders
                       (4379 - 333, 3284 - 190, 333, 190),   # right/bottom edge tiles are smaller
                       (0, 0, 1, 1), (255, 255, 2, 2)]:
            x, y, cw, ch = region
            crop, urls = await images.render_zoomify(zsid, 'alice', desc, region, 1600)
            assert crop.size == (cw, ch)
            diff = ImageChops.difference(crop, archive_module.image.crop((x, y, x + cw, y + ch))).convert('L')
            assert ImageStat.Stat(diff).mean[0] < 6       # JPEG round trip only; a wrong offset would be far off
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_tile_beyond_256_is_requested_from_group_1(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page_for(archive_module))
        crop, urls = await images.render_zoomify(zsid, 'alice', desc, (w - 300, h - 300, 300, 300), 1600)
        assert any('/TileGroup1/5-17-12.jpg' in u for u in urls)
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_lower_tier_chosen_for_overview_and_pixels_close(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page_for(archive_module))
        n = len(archive_module.requests)
        out, urls = await images.render_zoomify(zsid, 'alice', desc, (0, 0, w, h), 1000)
        assert out.size == (1000, 750)
        assert len(urls) == 20 and all('/3-' in u for u in urls)      # tier 3 is 1094x821: 5x4 tiles
        ref = archive_module.image.resize(out.size, Image.Resampling.BOX)
        diff = ImageChops.difference(out, ref).convert('L')
        assert ImageStat.Stat(diff).mean[0] < 6
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_region_too_large_before_any_tile_fetch(zcfg, zstore, zsid, archive_module):
    zcfg.max_tiles_per_call = 4
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page_for(archive_module))
        n = len(archive_module.requests)
        with pytest.raises(ResearchError) as e:
            await images.render_zoomify(zsid, 'alice', desc, (0, 0, 1000, 1000), 1600)
        assert e.value.code == 'REGION_TOO_LARGE' and len(archive_module.requests) == n
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_missing_tile_is_incomplete_image_and_charges_nothing(zcfg, zstore, zsid, archive_module):
    Z.MISSING_TILES.add((5, 1, 1))
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        with pytest.raises(ResearchError) as e:
            await images.get(zsid, 'alice', page_for(archive_module), (200, 200, 400, 300), role='detail')
        assert e.value.code == 'INCOMPLETE_IMAGE'
        assert not zstore.links(zsid, 'alice', 'downloaded_page')
        zstore.check_book_image('b1')
        assert zstore.full_res_used('p1') == 0
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_wrong_numtiles_is_parse_error(zcfg, zstore, zsid):
    bad = Z.ZoomifyArchive(1000, 800, num_tiles=99)
    images, source = make_service(zcfg, zstore, bad)
    try:
        with pytest.raises(ResearchError) as e:
            await images.get(zsid, 'alice', page_for(bad), None)
        assert e.value.code == 'PARSE_ERROR'
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_wrong_tile_size_is_incomplete_image(zcfg, zstore, zsid):
    archive = Z.ZoomifyArchive(1000, 800)
    def handler(req):
        if 'TileGroup' in req.url.path:
            out = io.BytesIO(); Image.new('RGB', (100, 100)).save(out, 'JPEG')
            return httpx.Response(200, content=out.getvalue(), headers={'content-type': 'image/jpeg'})
        return archive.handle(req)
    source = SafeSource(zcfg, zstore, transport=httpx.MockTransport(handler), source_mode='synthetic_demo')
    images = ImageService(source, zstore, zcfg)
    try:
        with pytest.raises(ResearchError) as e:
            await images.get(zsid, 'alice', page_for(archive), (0, 0, 300, 300), role='detail')
        assert e.value.code == 'INCOMPLETE_IMAGE'
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_get_returns_image_with_provenance_and_records_robots(zcfg, zstore, zsid, archive_module, caplog):
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        with caplog.at_level(logging.INFO, logger='rubrika'):
            out = await images.get(zsid, 'alice', page_for(archive_module), (100, 100, 500, 400), role='detail')
        ref = out.data['image']
        assert tuple(ref['source_bbox']) == (100, 100, 500, 400) and (ref['width'], ref['height']) == (500, 400)
        assert ref['robots_disallowed'] is True and ref['archive'] == 'ahmp'
        assert zstore.has_link(zsid, 'alice', 'robots_tolerated', 'images.ahmp.cz')
        assert 'host=images.ahmp.cz path=/mrimage/ahmp/zoomify/' in caplog.text and '?' not in caplog.text.split('path=')[1].split()[0]
        assert source.robots_disallowed(Z.AHMP_ROOT + '/TileGroup0/0-0-0.jpg')
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_na_double_slash_path_is_fetched(zcfg, zstore, zsid):
    archive = Z.ZoomifyArchive(1000, 800, root=Z.NA_ROOT)
    images, source = make_service(zcfg, zstore, archive)
    try:
        out = await images.get(zsid, 'alice', page_for(archive, code='na'), (0, 0, 300, 300), role='detail')
        assert out.data['image']['robots_disallowed'] is True
        assert any('zoomify//cz/archives' in u for u in archive.requests)
        assert zstore.has_link(zsid, 'alice', 'robots_tolerated', 'vademecum.nacr.cz')
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_full_resolution_cap_per_page_and_day(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    page = page_for(archive_module)
    area = 4379 * 3284
    try:
        # 1600x1600 = 2.56 MP = 17.8 % of the scan: two such crops pass, the third is 53 % and is refused.
        for x in (0, 1700):
            await images.get(zsid, 'alice', page, (x, 0, 1600, 1600), role='detail')
        assert zstore.full_res_used('p1') == 2 * 1600 * 1600
        n = len(archive_module.requests)
        with pytest.raises(ResearchError) as e:
            await images.get(zsid, 'alice', page, (0, 1650, 1600, 1600), role='detail')
        assert e.value.code == 'BUDGET_EXCEEDED' and 'captchou' in e.value.message
        assert len(archive_module.requests) == n                       # refused before any request
        assert zstore.full_res_used('p1') == 2 * 1600 * 1600
        # A smaller crop still fits under 50 %, overviews never count.
        await images.get(zsid, 'alice', page, (0, 1650, 1600, 300), role='detail')
        await images.get(zsid, 'alice', page, None, role='overview')
        assert zstore.full_res_used('p1') == 2 * 1600 * 1600 + 1600 * 300
        assert zstore.full_res_used('p1') <= 0.5 * area
        # Another page has its own allowance.
        await images.get(zsid, 'alice', page_for(archive_module, pid='p2'), (0, 0, 1600, 1600), role='detail')
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_downscaled_detail_from_lower_tier_does_not_count(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        await images.get(zsid, 'alice', page_for(archive_module), (0, 0, 4379, 3284), role='detail')   # ratio .37 -> tier 4
        assert zstore.full_res_used('p1') == 0
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_cap_does_not_apply_to_other_archives(zcfg, zstore, zsid, archive_module, monkeypatch):
    monkeypatch.setitem(A.ARCHIVES, 'ahmp', dataclasses.replace(A.ARCHIVES['ahmp'], full_res_captcha=False))
    images, source = make_service(zcfg, zstore, archive_module)
    try:
        for _ in range(4):
            await images.get(zsid, 'alice', page_for(archive_module), (0, 0, 1600, 1600), role='detail')
        assert zstore.full_res_used('p1') == 0
    finally:
        await source.close()


# --- registry and routes -------------------------------------------------

AHMP_TILE = Z.AHMP_ROOT + '/TileGroup0/3-1-2.jpg'
NA_TILE = Z.NA_ROOT + '/TileGroup1/5-17-12.jpg'


def test_registry_entries():
    for code, host, setting in (('ahmp', 'images.ahmp.cz', 'ahmp_global_daily_requests'),
                                ('na', 'vademecum.nacr.cz', 'na_global_daily_requests')):
        a = A.ARCHIVES[code]
        assert host in a.hosts and a.route and a.robots_mode == 'user_triggered' and a.full_res_captcha
        assert a.enabled_setting == f'{code}_enabled' and a.daily_requests_setting == setting
        assert a.kinds == (('parish_registers', 'census') if code == 'ahmp' else ('parish_registers',))
        assert a.link_reason is None and not a.link_only
    assert ORIGINAL['ahmp'].min_interval == ORIGINAL['na'].min_interval == 2.0
    assert not A.ARCHIVES['mza'].full_res_captcha and not A.ARCHIVES['vca'].full_res_captcha
    assert A.ARCHIVES['mza'].robots_mode == A.ARCHIVES['vca'].robots_mode == 'honor'
    s = Settings(_env_file=None)
    assert (s.ahmp_enabled, s.na_enabled, s.ahmp_global_daily_requests, s.na_global_daily_requests) == (False, False, 1000, 1000)
    assert s.full_res_page_share == 0.5
    assert {'images.ahmp.cz', 'vademecum.nacr.cz'} <= set(A.integrated_hosts())


@pytest.mark.parametrize('url,source,group,archive', [
    (Z.AHMP_ROOT + '/ImageProperties.xml', 'ahmp_images', 'ahmp_images', 'ahmp'),
    (AHMP_TILE, 'ahmp_images', 'ahmp_images', 'ahmp'),
    ('https://images.ahmp.cz/robots.txt', 'robots', 'ahmp_robots', 'ahmp'),
    (Z.NA_ROOT + '/ImageProperties.xml', 'na_images', 'na_images', 'na'),
    (NA_TILE, 'na_images', 'na_images', 'na'),
    ('https://vademecum.nacr.cz/robots.txt', 'robots', 'na_robots', 'na'),
])
def test_zoomify_routes_allowed(url, source, group, archive):
    route = SourcePolicy(A.integrated_hosts()).validate(url)
    assert (route.source, route.group, route.archive, route.method) == (source, group, archive, 'GET')


BAD = [
    Z.AHMP_ROOT + '/ImageProperties.xml?captcha=abc',
    Z.AHMP_ROOT + '?captcha=1',
    Z.AHMP_ROOT,
    AHMP_TILE + '?x=1',
    'https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/NAD-156/dao/images/0014/nahled_4cd4d3321eecc71459e876d6582dda29.jpg',
    'https://images.ahmp.cz/mrimage/ahmp/nahled/cz/archives/CZ-321100010/NAD-156/dao/images/0014/4cd4d3321eecc71459e876d6582dda29.jpg',
    'https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-999999999/NAD-156/dao/images/0014/4cd4d3321eecc71459e876d6582dda29.jpg/ImageProperties.xml',
    'https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/../CZ-1/NAD-156/dao/images/0014/4cd4d3321eecc71459e876d6582dda29.jpg/ImageProperties.xml',
    'https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/NAD-156/dao/images/0014/4cd4d3321eecc71459e876d6582dda2%2Fjpg/ImageProperties.xml',
    Z.AHMP_ROOT.replace('4cd4d3321eecc71459e876d6582dda29', '4CD4D3321EECC71459E876D6582DDA29') + '/ImageProperties.xml',
    'https://images.ahmp.cz/BasketBean',
    'https://images.ahmp.cz/login',
    'https://images.ahmp.cz/robots.txt?x=1',
    'https://images.ahmp.cz/',
    AHMP_TILE.replace('.jpg', '.png'),
    AHMP_TILE.replace('TileGroup0', 'TileGroup0/../x'),
    Z.NA_ROOT + '/ImageProperties.xml?captcha=abc',
    Z.NA_ROOT.replace('CZ-100000010', 'CZ-100000011') + '/ImageProperties.xml',
    Z.NA_ROOT.replace('zoomify//cz', 'zoomify/cz') + '/ImageProperties.xml',
    Z.NA_ROOT.replace('ca182acf-5e28-4abf-ab4e-ad7b06ec4e44', 'nahled_x') + '/ImageProperties.xml',
    'https://vademecum.nacr.cz/vademecum/',
    'https://vademecum.nacr.cz/vademecum/BasketBean.action',
    'https://vademecum.nacr.cz/mrimage/vademecum/zoomify//cz/archives/CZ-100000010/inventare/dao/images/0245/%2e%2e/x.jpg/ImageProperties.xml',
]


@pytest.mark.parametrize('url', BAD)
def test_zoomify_routes_forbidden(url):
    with pytest.raises(ResearchError) as e:
        SourcePolicy(A.integrated_hosts()).validate(url)
    assert e.value.code == 'INVALID_SOURCE'


def test_post_is_forbidden():
    with pytest.raises(ResearchError):
        SourcePolicy(A.integrated_hosts()).validate(AHMP_TILE, 'POST')


@pytest.mark.asyncio
async def test_disabled_archive_is_not_fetched(tmp_path):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, request_interval=0)
    store = Store(cfg)
    from rubrika.models import SearchQuery
    sid = store.create('alice', SearchQuery(raw_query='Synthetic', event_type='birth', year_from=1840, year_to=1845))['research_id']
    archive = Z.ZoomifyArchive(600, 400)
    source = SafeSource(cfg, store, transport=archive.transport, source_mode='synthetic_demo')
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid, 'alice', archive.descriptor_url, kind='xml')
        assert e.value.code == 'LIVE_DISABLED' and archive.requests == []
    finally:
        await source.close(); store.close()


@pytest.mark.asyncio
async def test_robots_disallow_on_image_host_is_tolerated_but_bot_gates_are_not(zcfg, zstore, zsid, caplog):
    archive = Z.ZoomifyArchive(600, 400)
    source = SafeSource(zcfg, zstore, transport=archive.transport, source_mode='synthetic_demo')
    try:
        with caplog.at_level(logging.INFO, logger='rubrika'):
            raw = await source.fetch(zsid, 'alice', archive.descriptor_url + '', kind='xml')
        assert b'IMAGE_PROPERTIES' in raw
        assert zstore.has_link(zsid, 'alice', 'robots_tolerated', 'images.ahmp.cz')
        assert source.robots_disallowed(archive.descriptor_url)
    finally:
        await source.close()
    def gate(req):
        if req.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nDisallow: /\n', headers={'content-type': 'text/plain'})
        return httpx.Response(403, text='<html>captcha</html>', headers={'content-type': 'text/html'})
    source = SafeSource(zcfg, zstore, transport=httpx.MockTransport(gate), source_mode='synthetic_demo')
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(zsid, 'alice', AHMP_TILE, kind='image')
        assert 'Robots' not in e.value.message           # not a robots refusal: the 403 itself stops us
        with pytest.raises(ResearchError) as e2:         # and the host is paused, no second attempt
            await source.fetch(zsid, 'alice', AHMP_TILE.replace('3-1-2', '3-1-3'), kind='image')
        assert e2.value.code in ('BOT_GATE', 'RATE_LIMITED')
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_full_resolution_allowance_is_lifetime_not_daily(zcfg, zstore, zsid, archive_module, monkeypatch):
    import time as _t
    images, source = make_service(zcfg, zstore, archive_module)
    page = page_for(archive_module)
    try:
        for x in (0, 1700):
            await images.get(zsid, 'alice', page, (x, 0, 1600, 1600), role='detail')
        now = _t.time()
        monkeypatch.setattr('rubrika.storage.time.time', lambda: now + 3 * 86400)
        zstore.cleanup()                                                   # pruning never touches the counter
        assert zstore.full_res_used('p1') == 2 * 1600 * 1600
        with pytest.raises(ResearchError) as e:
            await images.get(zsid, 'alice', page, (0, 1650, 1600, 1600), role='detail')
        assert e.value.code == 'BUDGET_EXCEEDED'
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_full_resolution_reservation_is_returned_when_the_render_fails(zcfg, zstore, zsid, archive_module):
    images, source = make_service(zcfg, zstore, archive_module)
    page = page_for(archive_module)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page)
        top = len(desc.tiers) - 1
        Z.MISSING_TILES.add((top, 0, 0))
        with pytest.raises(ResearchError):
            await images.get(zsid, 'alice', page, (0, 0, 1600, 1600), role='detail')
        assert zstore.full_res_used('p1') == 0
    finally:
        await source.close()


def test_full_resolution_reservation_is_atomic_and_shared(zstore):
    zstore.reserve_full_res('p9', 60, 100)
    with pytest.raises(ResearchError):
        zstore.reserve_full_res('p9', 41, 100)
    zstore.reserve_full_res('p9', 40, 100)
    assert zstore.full_res_used('p9') == 100
    zstore.release_full_res('p9', 40)
    assert zstore.full_res_used('p9') == 60


# ---- servers that round odd sizes up when halving (real AHMP and Národní archiv) as well as down

def halved(size, halving):
    return (size + 1) // 2 if halving == 'ceil' else size // 2


def test_real_ahmp_descriptor_with_odd_width_parses():
    xml = (b"<IMAGE_PROPERTIES WIDTH='1295' HEIGHT='7044' NUMTILES='231' NUMIMAGES='1' VERSION='1.8' "
           b"TILESIZE='256' DPIX='300' DPIY='300' />")
    z = parse_zoomify(xml, URL, 80000000)
    assert [v.halving for v in z.variants()] == ['ceil', 'floor']          # NUMTILES does not tell them apart
    assert z.variants()[0].tiers[-3:] == [(324, 1761), (648, 3522), (1295, 7044)]
    assert z.variants()[1].tiers[-3:] == [(323, 1761), (647, 3522), (1295, 7044)]


def test_numtiles_must_fit_one_of_the_conventions():
    bad = NA_XML.replace(b'NUMTILES="326"', b'NUMTILES="327"')
    with pytest.raises(ResearchError):
        parse_zoomify(bad, URL, 80000000)


@pytest.mark.asyncio
@pytest.mark.parametrize('halving', ['ceil', 'floor'])
async def test_odd_sized_scan_renders_from_either_kind_of_server(zcfg, zstore, zsid, halving):
    archive = Z.ZoomifyArchive(1295, 7044, halving=halving)     # dimensions of a real AHMP scan
    assert archive.sizes[-2] == (halved(1295, halving), halved(7044, halving)) and archive.num_tiles == 231
    images, source = make_service(zcfg, zstore, archive)
    try:
        page = page_for(archive)
        desc, w, h = await images.descriptor(zsid, 'alice', page)
        assert len(desc.variants()) == 2          # the tile count cannot tell the conventions apart
        for region in [(w - 300, h - 300, 300, 300),                       # right and bottom edge tiles, full resolution
                       (0, 0, w, h)]:                                      # a lower tier: its edge tiles differ by one pixel
            x, y, cw, ch = region
            crop, urls = await images.render_zoomify(zsid, 'alice', desc, region, 1000)
            ref = archive.image.crop((x, y, x + cw, y + ch)).resize(crop.size, Image.Resampling.BOX)
            assert ImageStat.Stat(ImageChops.difference(crop, ref).convert('L')).mean[0] < 8
        out = await images.get(zsid, 'alice', page, (w - 300, h - 300, 300, 300), role='detail')
        assert out.data['image']['source_bbox'] == (w - 300, h - 300, 300, 300)
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_a_tile_of_neither_size_is_still_refused(zcfg, zstore, zsid):
    archive = Z.ZoomifyArchive(1295, 7044, halving='ceil')
    def handler(req):
        response = archive.handle(req)
        if 'TileGroup' in req.url.path and req.url.path.endswith('/5-5-27.jpg'):
            out = io.BytesIO(); Image.new('RGB', (14, 14)).save(out, 'JPEG')
            return httpx.Response(200, content=out.getvalue(), headers={'content-type': 'image/jpeg'})
        return response
    source = SafeSource(zcfg, zstore, transport=httpx.MockTransport(handler), source_mode='synthetic_demo')
    images = ImageService(source, zstore, zcfg)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page_for(archive))
        top = len(desc.tiers) - 1
        assert top == 5
        with pytest.raises(ResearchError) as e:
            await images.render_zoomify(zsid, 'alice', desc, (w - 200, h - 200, 200, 200), 3000, tier=top)
        assert e.value.code == 'INCOMPLETE_IMAGE'
    finally:
        await source.close()


@pytest.mark.asyncio
async def test_a_missing_tile_also_tries_the_other_rounding_convention(zcfg, zstore, zsid):
    """The ceil grid of this scan has a tile the floor server never made (other row count): 404 must not end the render
    before the floor grid is tried, and a tile that is truly missing in both still fails."""
    archive = Z.ZoomifyArchive(1295, 7044, halving='floor')
    images, source = make_service(zcfg, zstore, archive)
    try:
        desc, w, h = await images.descriptor(zsid, 'alice', page_for(archive))
        assert [v.halving for v in desc.variants()] == ['ceil', 'floor']
        # tier 4 is 324x1761 (ceil) or 323x1761 (floor); ask for a region there and drop the ceil-only expectation by 404
        Z.MISSING_TILES.add((4, 1, 0))
        with pytest.raises(ResearchError) as e:
            await images.render_zoomify(zsid, 'alice', desc, (0, 0, w, h), 300, tier=4)
        assert e.value.code == 'INCOMPLETE_IMAGE'
        assert sum(1 for u in archive.requests if '/4-1-0.jpg' in u) == 2          # tried under both conventions, then refused
    finally:
        await source.close()


def test_overview_tier_may_be_just_below_preview_size():
    # A real AHMP spread, 7360x4912: 1000 px wanted; the 920 px tier needs 12 tiles instead of 40 for 1840 px.
    z = parse_zoomify(b"<IMAGE_PROPERTIES WIDTH='7360' HEIGHT='4912' NUMTILES='787' NUMIMAGES='1' VERSION='1.8' TILESIZE='256' />",
                      URL, 80000000)
    region = (0, 0, 7360, 4912)
    assert z.tiers[z.pick_tier(region, 1000)][0] == 1840
    tier = z.pick_tier(region, 1000, OVERVIEW_TIER_TOLERANCE)
    assert z.tiers[tier][0] == 920 and grid(z)[tier] == (4, 3)
    # a tier more than 15 % below the preview size is never taken
    na = parse_zoomify(b"<IMAGE_PROPERTIES WIDTH='5320' HEIGHT='3547' NUMTILES='404' NUMIMAGES='1' VERSION='1.8' TILESIZE='256' />",
                       URL, 80000000)
    assert na.tiers[na.pick_tier((0, 0, 5320, 3547), 1000, OVERVIEW_TIER_TOLERANCE)][0] == 1330     # 665 px would be too small
