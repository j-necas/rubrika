"""MZA indication sketches: parsers on synthetic pages, structure changes as PARSE_ERROR, tile naming and geometry."""
import pytest

from rubrika import demo_sketches as D
from rubrika import sketches as S
from rubrika.errors import ResearchError

def rows(**query):
    return D.DemoSketches()._matching({k: [v] for k, v in query.items()})


def detail(mor='MOR090118250', side='A'):
    return D.detail_page(mor, side)


def test_search_table_is_parsed_with_notes_and_undated_sketches():
    parsed, last, total = S.parse_search(D.search_page(rows(nazev='Testov'), 1))
    assert (last, total) == (3, 3) and [r.mor for r in parsed] == ['MOR090419110', 'MOR090419111', 'MOR090400000']
    first = parsed[0]
    assert (first.signature, first.katastr, first.german_name, first.district, first.year) == ('0904', 'Testov-Horní', 'Ober Testow', 'Brno-město', 1911)
    assert first.other_names == 'Horní Testov' and first.note is None
    assert parsed[1].note == 'neúplná'
    assert parsed[2].year is None and parsed[2].note == 'Bez data'          # year 0: the archive lists it without a date
    other, _, _ = S.parse_search(D.search_page(rows(signatura='0902'), 1))
    assert other[0].other_municipalities == 'Podlesí (Unterwald)'


def test_search_paging_counts_come_from_the_footer():
    many = D.ROWS * 5
    parsed, last, total = S.parse_search(D.search_page(many, 2, size=20))
    assert (len(parsed), last, total) == (len(many) - 20 if len(many) < 40 else 20, min(40, len(many)), len(many))


def test_empty_result_is_an_empty_list_not_an_error():
    assert S.parse_search(D.search_page([], 1)) == ([], 0, 0)


@pytest.mark.parametrize('mutate', [
    lambda h: h.replace('id="table"', 'id="other"'),
    lambda h: h.replace('<th>Okres</th>', '<th>District</th>'),
    lambda h: h.replace('<th>Identifikátor</th>', ''),
    lambda h: h.replace('Zobrazené záznamy', 'Shown'),
    lambda h: h.replace('>MOR090118250<', '>X090118250<'),
    lambda h: h.replace('>0901<', '>abc<'),
    lambda h: h.replace('>1825<', '>18x5<'),
    lambda h: h.replace('celkem: 2', 'celkem: 0'),
])
def test_changed_search_page_is_a_parse_error(mutate):
    html = D.search_page(rows(signatura='0901'), 1)
    assert 'MOR090118250' in html
    with pytest.raises(ResearchError) as e:
        S.parse_search(mutate(html))
    assert e.value.code == 'PARSE_ERROR'


def test_a_row_with_too_few_columns_is_a_parse_error():
    html = D.search_page(rows(signatura='0901'), 1).replace('<td class="px-1"></td><td class="px-1"></td>', '', 1)
    parsed, _, _ = S.parse_search(html)          # the trailing columns are not needed
    assert len(parsed) == 2
    html = html.replace('<td class="clickable" onclick="window.location=\'https://www.mza.cz/indikacniskici/skica/detail/MOR090118250\';">1825</td>', '')
    with pytest.raises(ResearchError):
        S.parse_search(html)


def test_detail_page_gives_fields_grid_and_back_side():
    d = S.parse_detail(detail(), 'MOR090118250')
    assert (d.signature, d.katastr, d.german_name, d.district, d.year) == ('0901', 'Demovice', 'Demowitz', 'Znojmo', 1825)
    assert d.internal_id == D.NUMBERS['MOR090118250'] and d.has_back
    g = d.grid
    assert (g.sketch_id, g.side, g.cols, g.rows, g.tile_w, g.tile_h) == ('MOR090118250', 'A', 2, 2, 217, 174)
    assert g.tiles == {'A01A', 'A01B', 'A02A', 'R01A', 'R01B', 'R02A'}
    assert (g.width, g.height) == (2 * 12 * 217, 2 * 12 * 174)
    assert S.parse_grid(detail(side='R')).side == 'R'


def test_sketch_without_back_tiles_has_no_back_side():
    assert not S.parse_detail(D.detail_page('MOR090419111', 'A'), 'MOR090419111').has_back


def test_detail_notes_and_other_names_are_read():
    d = S.parse_detail(D.detail_page('MOR090419110', 'A'), 'MOR090419110')
    assert d.other_names == 'Horní Testov'
    assert S.parse_detail(D.detail_page('MOR090419111', 'A'), 'MOR090419111').note == 'neúplná'
    assert S.parse_detail(D.detail_page('MOR090400000', 'A'), 'MOR090400000').year is None


@pytest.mark.parametrize('mutate', [
    lambda h: h.replace('var levelScales = [(43 / 174) / 12, 1 / 12, 1 / 4, 1 / 2, 1];', 'var levelScales = [1, 1 / 2];'),
    lambda h: h.replace('maxLevel: 4,', 'maxLevel: 5,'),
    lambda h: h.replace('var siteLetter = "A";', 'var siteLetter = "X";'),
    lambda h: h.replace('var tiles = [', 'var tilez = ['),
    lambda h: h.replace('var cols = 2;', 'var cols = 0;'),
    lambda h: h.replace('var cols = 2;', 'var cols = 40;'),
    lambda h: h.replace('width: cols * 12 * 217', 'width: cols * 12 * 300'),
    lambda h: h.replace('"A02A"', '"A09A"'),
    lambda h: h.replace('"A01B"', '"A01F"'),
    lambda h: h.replace('"A01A", ', '', 1).replace('"A01B", "A02A", "R01A", "R01B", "R02A"', '').replace('var tiles = [', 'var tiles = []; var x = ['),
    lambda h: h.replace('data/obrazky/MOR090118250/Img/"', 'data/obrazky/MOR090118260/Img/"'),
    lambda h: h.replace('<div class="card-header bg-dark text-white">', '<div>'),
    lambda h: h.replace('Signatura', 'Sign'),
    lambda h: h.replace('>MOR090118250<', '>MOR090118260<'),
])
def test_changed_detail_page_is_a_parse_error(mutate):
    with pytest.raises(ResearchError) as e:
        S.parse_detail(mutate(detail()), 'MOR090118250')
    assert e.value.code == 'PARSE_ERROR'


# real tile names of the live application (the naming pattern, not content): a 4 x 3 grid, last column of the last row of level 4
def test_tile_names_follow_the_viewer_script():
    g = S.SketchGrid('MOR113918250', 'A', 4, 3, 217, 174, frozenset({'A01A', 'A03D'}))
    assert g.tile_name(1, 0, 0) == 'A01A' and g.tile_name(2, 0, 0) == 'A01A-A1'
    assert g.tile_name(3, 0, 0) == 'A01A-A1-A1' and g.tile_name(4, 0, 0) == 'A01A-A1-A1-A1'
    assert g.tile_name(4, 47, 35) == 'A03D-C3-B2-B2' and g.tile_name(3, 23, 17) == 'A03D-C3-B2'
    assert g.tile_name(2, 11, 8) == 'A03D-C3' and g.tile_name(1, 3, 2) == 'A03D'
    assert g.tile_name(4, 12, 0) is None and g.tile_name(1, 1, 0) is None          # level-1 tile not in the list: blank area
    assert g.tile_url(4, 47, 35) == 'https://www.mza.cz/indikacniskici/data/obrazky/MOR113918250/Img/Normal/A03D-C3-B2-B2.jpg'
    assert g.tile_url(3, 0, 0).endswith('/Img/Small/A01A-A1-A1.jpg') and g.tile_url(2, 0, 0).endswith('/Img/little/A01A-A1.jpg')
    assert g.tile_url(1, 0, 0).endswith('/Img/Preview/A01A.jpg')
    back = S.SketchGrid('MOR113918250', 'R', 4, 3, 217, 174, frozenset({'R01A'}))
    assert back.tile_name(4, 1, 1) == 'R01A-A1-A1-B2'


def test_tile_names_round_trip_on_every_level():
    g = S.SketchGrid('MOR090118250', 'A', 2, 2, 217, 174, frozenset({'A01A', 'A01B', 'A02A', 'A02B'}))
    for level in (1, 2, 3, 4):
        nx, ny = g.level_tiles(level)
        names = set()
        for y in range(ny):
            for x in range(nx):
                name = g.tile_name(level, x, y)
                assert D.parse_tile(name) == (level, x, y)
                names.add(name)
        assert len(names) == nx * ny            # every tile has its own name


def test_level_choice_prefers_the_coarser_level_within_tolerance():
    assert S.level_for(0.098) == 1 and S.level_for(0.0833) == 1 and S.level_for(0.25) == 2 and S.level_for(0.29) == 2
    assert S.level_for(0.5) == 3 and S.level_for(0.58) == 3 and S.level_for(0.6) == 4 and S.level_for(0.9) == 4 and S.level_for(1.0) == 4
    assert S.level_for(1000 / (4 * 12 * 217)) == 1          # a 4 x 3 sketch shown in 1000 px: 12 tiles instead of 108


def test_region_tiles_cover_exactly_the_requested_pixels():
    g = S.SketchGrid('MOR090118250', 'A', 2, 2, 217, 174, frozenset({'A01A'}))
    (l, t, r, b), xs, ys = S.region_tiles(g, 4, (0, 0, 217, 174))
    assert (l, t, r, b) == (0, 0, 217, 174) and list(xs) == [0] and list(ys) == [0]
    (l, t, r, b), xs, ys = S.region_tiles(g, 4, (200, 170, 40, 10))
    assert list(xs) == [0, 1] and list(ys) == [0, 1] and (r - l, b - t) == (40, 10)
    (l, t, r, b), xs, ys = S.region_tiles(g, 1, (0, 0, g.width, g.height))
    assert (r - l, b - t) == (2 * 217, 2 * 174) and list(xs) == [0, 1] and list(ys) == [0, 1]


def test_ids_and_links():
    assert S.sketch_id_from_url('https://www.mza.cz/indikacniskici/skica/detail/MOR113918250') == 'mza:sketch:MOR113918250'
    for bad in ('http://www.mza.cz/indikacniskici/skica/detail/MOR113918250', 'https://example.org/indikacniskici/skica/detail/MOR113918250',
                'https://www.mza.cz/indikacniskici/skica/detail/1632', 'https://www.mza.cz/indikacniskici/skica?nazev=Brno',
                'https://user@www.mza.cz/indikacniskici/skica/detail/MOR113918250'):
        with pytest.raises(ResearchError):
            S.sketch_id_from_url(bad)
    assert S.signature_of('mza:sketch:place:0901') == '0901'
    for bad in ('mza:sketch:place:', 'mza:sketch:place:1234567', 'mza:sketch:MOR113918250', 'aron:place'):
        with pytest.raises(ResearchError):
            S.signature_of(bad)
    assert S.mor_of('mza:sketch:MOR113918250') == 'MOR113918250'
    with pytest.raises(ResearchError):
        S.mor_of('mza:sketch:place:0901')
    assert S.search_url('Brno', 'Brno-město') == 'https://www.mza.cz/indikacniskici/skica?nazev=Brno&okres=Brno-m%C4%9Bsto'
    assert S.search_url(signatura='1139', page=2) == 'https://www.mza.cz/indikacniskici/skica?signatura=1139&page=2'
    assert S.side_url('1632', 'BACK') == 'https://www.mza.cz/indikacniskici/skica/detail/1632?site=BACK'


def test_relative_back_link_is_recognised_and_missing_link_is_flagged():
    html = detail().replace('https://www.mza.cz/indikacniskici/skica/detail/', '/indikacniskici/skica/detail/')
    d = S.parse_detail(html, 'MOR090118250')
    assert d.has_back and d.internal_id == D.NUMBERS['MOR090118250'] and not d.back_unresolved
    d = S.parse_detail(detail().replace('?site=', '?x='), 'MOR090118250')
    assert not d.has_back and d.back_unresolved


def test_thumbnail_size_follows_the_grid():
    assert S.SketchGrid('MOR000000000', 'A', 4, 3, 217, 174, frozenset({'A01A'})).map_size == (216, 129)


def test_has_back_needs_a_parsed_back_link():
    only_front = detail().replace('?site=BACK', '?site=FRONT')
    d = S.parse_detail(only_front, 'MOR090118250')
    assert not d.has_back and d.back_unresolved
