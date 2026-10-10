"""Sketch rendering on ordinary grid sizes: overview and crops never exceed the tile limit and never fail for a whole sheet."""
import pytest
from PIL import Image

from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.images import ImageService
from rubrika.sketches import MAX_TILES_PER_CALL, SketchGrid

GRIDS = [(2, 2), (3, 3), (3, 4), (2, 4), (4, 3), (4, 14), (6, 5)]


def grid(cols, rows):
    names = {f'A{r:02d}{"ABCDEFGHIJKLMNOPQRSTUVWXYZ"[c]}' for r in range(1, rows + 1) for c in range(cols)}
    return SketchGrid('MOR000000000', 'A', cols, rows, 217, 174, frozenset(names))


@pytest.mark.asyncio
@pytest.mark.parametrize('cols,rows', GRIDS)
@pytest.mark.parametrize('side', [1000, 1600])
async def test_whole_sheet_renders_within_the_tile_limit(cols, rows, side):
    g = grid(cols, rows)
    svc = ImageService(None, None, Settings(_env_file=None))
    requested = []

    async def tile(sid, owner, url, **where):
        requested.append(url)
        size = g.map_size if url.endswith('map.jpg') else (217, 174)
        return Image.new('RGB', size, (120, 120, 120))
    svc._tile = tile
    image, urls, meta = await svc.render_sketch('s', 'o', g, (0, 0, g.width, g.height), side)
    assert 1 <= len(requested) <= MAX_TILES_PER_CALL and requested == urls
    assert max(image.size) <= side and image.size[0] / image.size[1] == pytest.approx(g.width / g.height, rel=0.05)
    ratio = side / g.width if g.width > g.height else side / g.height
    assert meta['effective_scale'] == pytest.approx(image.width / g.width, rel=0.02)
    if requested == [g.map_url]:                           # the thumbnail is never upscaled and is flagged
        assert image.size == g.map_size and meta['low_resolution']
    else:
        assert meta['low_resolution'] == (meta['effective_scale'] < ratio * 0.85 - 1e-9)


@pytest.mark.asyncio
@pytest.mark.parametrize('cols,rows', GRIDS)
async def test_quarter_of_the_sheet_renders_at_crop_size(cols, rows):
    g = grid(cols, rows)
    svc = ImageService(None, None, Settings(_env_file=None))
    requested = []

    async def tile(sid, owner, url, **where):
        requested.append(url)
        return Image.new('RGB', (217, 174), (120, 120, 120))
    svc._tile = tile
    image, _, meta = await svc.render_sketch('s', 'o', g, (0, 0, g.width // 2, g.height // 2), 1600)
    assert 1 <= len(requested) <= MAX_TILES_PER_CALL and max(image.size) <= 1600
    assert meta['low_resolution'] == (max(image.size) < 1600 * 0.85 - 1e-9) and not any(u.endswith('map.jpg') for u in requested)


@pytest.mark.asyncio
async def test_a_region_too_big_even_for_the_coarsest_level_is_refused_with_advice():
    g = grid(26, 40)
    svc = ImageService(None, None, Settings(_env_file=None))
    with pytest.raises(ResearchError) as e:
        await svc.render_sketch('s', 'o', g, (0, 0, g.width - 1, g.height), 1600)
    assert e.value.code == 'REGION_TOO_LARGE' and 'menší oblast' in e.value.message


@pytest.mark.asyncio
async def test_canvas_pixel_limit_steps_down_to_a_coarser_level():
    g = grid(4, 3)
    svc = ImageService(None, None, Settings(_env_file=None, max_source_pixels=1_000_000))
    requested = []

    async def tile(sid, owner, url, **where):
        requested.append(url)
        return Image.new('RGB', (217, 174), (120, 120, 120))
    svc._tile = tile
    await svc.render_sketch('s', 'o', g, (0, 0, 1500, 1200), 1600)          # 1.8 Mpx at the finest level, 0.45 Mpx one level up
    assert requested and not any('/Normal/' in u for u in requested)


@pytest.mark.asyncio
async def test_thumbnail_is_used_at_its_actual_size_and_flagged():
    g = grid(5, 14)                                 # 70 level-1 tiles: too many, so the thumbnail (270 x 602 announced) serves; the site sends another size
    svc = ImageService(None, None, Settings(_env_file=None))
    async def tile(sid, owner, url, **where):
        return Image.new('RGB', (150, 300), (1, 2, 3)) if url.endswith('map.jpg') else Image.new('RGB', (217, 174))
    svc._tile = tile
    image, urls, meta = await svc.render_sketch('s', 'o', g, (0, 0, g.width, g.height), 1000)
    assert urls == [g.map_url] and image.size == (150, 300) and meta['low_resolution'] is True
    assert meta['effective_scale'] == pytest.approx(150 / g.width)


@pytest.mark.asyncio
async def test_undecodable_thumbnail_falls_back_to_tiles():
    from rubrika.errors import ResearchError as E
    g = grid(6, 5)
    svc = ImageService(None, None, Settings(_env_file=None))
    requested = []
    async def tile(sid, owner, url, **where):
        requested.append(url)
        if url.endswith('map.jpg'): raise E('INCOMPLETE_IMAGE', 'bad')
        return Image.new('RGB', (217, 174))
    svc._tile = tile
    image, urls, meta = await svc.render_sketch('s', 'o', g, (0, 0, g.width, g.height), 256)
    assert len(urls) > 1 and not any(u.endswith('map.jpg') for u in urls)


@pytest.mark.asyncio
async def test_back_side_never_uses_the_front_thumbnail():
    g = SketchGrid('MOR000000000', 'R', 6, 5, 217, 174, frozenset(t.replace('A', 'R', 1) for t in grid(6, 5).tiles))
    svc = ImageService(None, None, Settings(_env_file=None))
    requested = []
    async def tile(sid, owner, url, **where):
        requested.append(url)
        return Image.new('RGB', (217, 174))
    svc._tile = tile
    await svc.render_sketch('s', 'o', g, (0, 0, g.width, g.height), 256)
    assert requested and not any(u.endswith('map.jpg') for u in requested)
