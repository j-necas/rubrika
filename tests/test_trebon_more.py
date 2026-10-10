"""SOA Třeboň, more sources (synthetic DigiArchiv): non-Catholic registers and civil registers, land/seigniorial/urbarial books and census.
Limited mode holds: no image request of any kind, scans only through viewer_url, view_scan -> MANUAL_SOURCE -> import_scan."""
import re

import pytest
import pytest_asyncio
from conftest import call
from rubrika import demo_trebon as D
from rubrika import trebon as T
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery

from test_trebon_service import build, clock, hits, research  # noqa: F401  (clock is a fixture)

H = 'digi.ceskearchivy.cz'


def bad(fn, *args):
    with pytest.raises(ResearchError) as e:
        fn(*args)
    assert e.value.code == 'PARSE_ERROR'


@pytest_asyncio.fixture
async def tbx(tmp_path, clock):
    service, archive = build(tmp_path, clock, trebon_land_records_enabled=True, trebon_census_enabled=True)
    yield service, archive
    await service.close()
    service.store_.close()


async def find(service, sid, term, kind, **kw):
    out = await call(service, 'find_place', research_id=sid, term=term, kind=kind, archives=['trebon'], **kw)
    return out.data


# ------------------------------------------------------------------ parsers

def test_confession_root_lists_congregations_directly():
    refs = T.parse_confession_root(D.confession_page('1nC'), '1nC', 'Českobratrská církev evangelická')
    assert [(r.id, r.tree, r.name) for r in refs] == [(950001, '1nCa', 'Slavonice')]
    assert refs[0].label == 'Slavonice, Českobratrská církev evangelická' and refs[0].base == 'Slavonice'


def test_confession_root_of_another_page_or_without_congregations_is_parse_error():
    bad(T.parse_confession_root, D.confession_page('1nC'), '1nb', 'civilní matriky')       # the tree answers an unknown code with another page
    bad(T.parse_confession_root, D.wrap("<div id='menu'>" + D._node('1ne', 'toggle("1ne")', 'Protestant Church', 'p_p_s') + '</div>'), '1ne', 'Evangelická církev')


def test_section_node_gives_groups_and_entities():
    groups, entities = T.parse_section_node(D.section_page('1qa'), '1qa')
    assert [(g.code, g.label) for g in groups] == [('1qab', 'B'), ('1qak', 'K')] and entities == []
    groups, entities = T.parse_section_node(D.section_page('1qab'), '1qab')
    assert groups == [] and [(e.code, e.id, e.label) for e in entities] == [('1qabl', 700001, 'Blatná'), ('1qabo', 700002, 'Borovany')]
    groups, _ = T.parse_section_node(D.section_page('1sec'), '1sec')
    assert [g.label for g in groups] == ['Soudní okres České Budějovice', 'Soudní okres Hluboká nad Vltavou']
    bad(T.parse_section_node, D.section_page('1qa'), '1qe')


def test_unit_page_reads_columns_by_header_id_and_counts_unpublished_rows():
    page = T.parse_unit_page(D.unit_page('1qabl'), 700001)
    assert page.fund_name == 'Velkostatek Blatná, (1327) 1503-1948' and page.localities == ['Blatná', 'Buzice', 'Lom']
    assert [r.id for r in page.rows] == [720001, 720002, 720003, 720004] and page.not_online == 1
    row = page.rows[1]
    assert row.cells['col_signatura'] == 'I B 5' and row.cells['col_nazev'].startswith('Kniha popsání polí') and row.cells['col_datace_od'] == 'asi 1700'
    assert row.headers['col_ukladaci_jednotka'] == 'Ukládací jednotka'


@pytest.mark.parametrize('change', [
    lambda h: h.replace("class='nadpis'", "class='titul'"),
    lambda h: h.replace('col_nazev', 'col_jmeno'),
    lambda h: h.replace('class="header"', 'class="hlavicka"'),
    lambda h: h.replace('https://digi.ceskearchivy.cz/700001', 'https://digi.ceskearchivy.cz/700009'),
    lambda h: h.replace('<tr id="720002">', '<tr id="720001">'),
    lambda h: re.sub(r'<td><div id="obce720001">.*?</td><td width="1%"></td>', '', h, flags=re.S),      # the row loses its last columns
])
def test_changed_unit_page_is_parse_error(change):
    bad(T.parse_unit_page, change(D.unit_page('1qabl')), 700001)


@pytest.mark.parametrize('raw,status,spans', [
    ('1695–1715', 'known', [(1695, 1715)]), ('1908', 'known', [(1908, 1908)]), ('(1580) 1598–1919', 'known', [(1580, 1919)]),
    ('(1700) 1787–1857', 'known', [(1700, 1857)]), ('asi 1700', 'uncertain', []), ('', 'unknown', []), ('kolem 1800', 'uncertain', [])])
def test_row_dating(raw, status, spans):
    p = T.parse_row_dating(raw)
    assert (p.status, [(s.start, s.end) for s in p.spans]) == (status, spans)


# ------------------------------------------------------------------ non-Catholic registers

@pytest.mark.asyncio
async def test_non_catholic_congregations_and_civil_registers_carry_their_confession_in_the_label(tbx):
    service, archive = tbx
    sid = await research(service, event_type='death', year_from=1950, year_to=1955, place_hint='Volary')
    data = await find(service, sid, 'Volary', 'parish_registers')
    assert [(p['source_id'], p['label']) for p in data['places']] == [
        ('trebon:place:970001', 'Volary, Církev československá husitská'), ('trebon:place:960001', 'Volary, civilní matriky')]
    slav = await find(service, sid, 'Slavonice', 'parish_registers')
    assert [p['label'] for p in slav['places']] == ['Slavonice, Českobratrská církev evangelická']
    assert '/tree.php?menu=0&doctree=1nC&lang=cs' in hits(archive)
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:960001'], kind='parish_registers')
    row = listed.data['sources'][0]
    assert row['source_id'] == 'trebon:book:961001' and row['relevance']['status'] == 'candidate' and row['denomination'] == 'civilní matriky'
    assert hits(archive)[-1] == '/tree.php?menu=0&doctree=1nba&id=960001&lang=cs'
    opened = await call(service, 'open_source', research_id=sid, source_id='trebon:book:961001')
    assert opened.data['kind'] == 'parish_registers' and opened.data['total'] == 1
    assert hits(archive)[-1] == '/view.php?menu=3&id=961001&page=1'


@pytest.mark.asyncio
async def test_a_catholic_parish_is_still_found_first_and_is_not_labelled_with_a_confession(tbx):
    service, _ = tbx
    sid = await research(service)
    data = await find(service, sid, 'Tábor', 'parish_registers')
    assert [p['label'] for p in data['places']] == ['Tábor, římskokatolická farnost']
    assert data['places'][0]['source_id'] == 'trebon:place:920001'


# ------------------------------------------------------------------ land records

@pytest.mark.asyncio
async def test_land_records_are_off_by_default(tmp_path, clock):
    service, archive = build(tmp_path, clock)
    try:
        sid = await research(service)
        out = await call(service, 'find_place', research_id=sid, term='Blatná', kind='land_records', archives=['trebon'])
        assert out.data['searched'] == [] and 'vypnutý' in out.data['not_searched'][0]['reason'] and archive.trebon.log == []
        row = next(a for a in (await call(service, 'list_archives')).data['archives'] if a['code'] == 'trebon')
        assert row['kinds'] == ['parish_registers', 'land_records', 'census'] and row['available_kinds'] == ['parish_registers']
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_each_new_kind_has_its_own_switch(tmp_path, clock):
    service, archive = build(tmp_path, clock, trebon_census_enabled=True)
    try:
        row = next(a for a in (await call(service, 'list_archives')).data['archives'] if a['code'] == 'trebon')
        assert row['available_kinds'] == ['parish_registers', 'census']
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_land_records_walk_the_branches_and_list_units_with_categories(tbx):
    service, archive = tbx
    sid = await research(service, year_from=1690, year_to=1710)
    data = await find(service, sid, 'Blatná', 'land_records')
    assert [(p['source_id'], p['label']) for p in data['places']] == [
        ('trebon:place:l700001', 'Blatná (pozemkové knihy; SOA v Třeboni - oddělení Třeboň)'),
        ('trebon:place:l710001', 'Blatná (vrchnostenské knihy (soupisy poddaných); SOA v Třeboni - oddělení Třeboň)'),
        ('trebon:place:l711001', 'Blatná (urbáře; SOA v Třeboni - oddělení Třeboň)')]
    assert data['catalog_exhaustive'] is False and 'jen k prohlížení' in data['terms_notice']
    assert all(h.startswith(('/tree.php', '/robots.txt')) for h in hits(archive))
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:l700001'], kind='land_records')
    rows = {r['source_id']: r for r in listed.data['sources']}
    assert set(rows) == {'trebon:book:720001', 'trebon:book:720002', 'trebon:book:720003', 'trebon:book:720004'}
    first = rows['trebon:book:720001']
    assert first['kind'] == 'land_records' and first['archive'] == 'trebon' and first['category'] == 'land_book'
    assert first['relevance']['status'] == 'candidate' and first['title'].startswith('Kniha smluv') and first['storage'] == 'kniha 3'
    assert rows['trebon:book:720002']['relevance']['status'] == 'needs_review' and rows['trebon:book:720002']['number'] == 'I B 5'
    assert rows['trebon:book:720003']['relevance']['status'] == 'candidate' and rows['trebon:book:720003']['dating']['spans'] == [{'start': 1700, 'end': 1857}]
    assert rows['trebon:book:720004']['relevance']['status'] == 'excluded'
    assert list(rows)[0] == 'trebon:book:720001'         # candidate first
    assert any('1 knih fondu nemá v DigiArchivu odkaz na snímky' in w for w in listed.data['warnings'])
    assert listed.data['places']['trebon:place:l700001']['localities'] == ['Blatná', 'Buzice', 'Lom']
    assert 'jen k prohlížení' in listed.data['terms_notice']
    seigniorial = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:l710001'], kind='land_records')
    assert seigniorial.data['sources'][0]['category'] == 'other'


@pytest.mark.asyncio
async def test_land_unit_opens_in_limited_mode_and_never_touches_images(tbx):
    service, archive = tbx
    sid = await research(service)
    await find(service, sid, 'Blatná', 'land_records')
    await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:l700001'], kind='land_records')
    opened = await call(service, 'open_source', research_id=sid, source_id='trebon:book:720001')
    d = opened.data
    assert d['kind'] == 'land_records' and d['total'] == 2 and d['unit']['section'] == 'land_records'
    assert d['citation'] == ('Státní oblastní archiv v Třeboni, Pozemkové knihy, Velkostatek Blatná, (1327) 1503-1948, '
                             'Kniha smluv na koupě a prodeje panství Blatná, č. 3 (DigiArchiv, id 720001)')
    page = d['pages'][0]
    assert page['source_type'] == 'manual' and page['viewer_url'] == f'https://{H}/view.php?menu=3&id=720001&page=1'
    assert 'jen k prohlížení' in d['terms_notice']
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=page['page_id']))
    assert out.is_error and out.data['error']['code'] == 'MANUAL_SOURCE' and out.data['error']['details']['viewer_url'] == page['viewer_url']
    assert not any(p.startswith(('/cgi-bin', '/pages', '/oai', '/help')) for p, _ in archive.trebon.log)


@pytest.mark.asyncio
async def test_a_place_of_one_kind_is_refused_for_another(tbx):
    service, _ = tbx
    sid = await research(service)
    await find(service, sid, 'Blatná', 'land_records')
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['trebon:place:l700001'], kind='census'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT' and 'land_records' in out.data['error']['message']
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=['trebon:place:l700001'], kind='parish_registers'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_land_search_needs_the_letter_and_never_claims_completeness(tbx):
    service, _ = tbx
    sid = await research(service)
    none = await find(service, sid, 'Žamberk', 'land_records')
    assert none['places'] == [] and none['catalog_exhaustive'] is False and any('není písmeno' in w for w in none['warnings'])
    assert any('mezi lokalitami fondu' in w for w in none['warnings'])


@pytest.mark.asyncio
async def test_walk_budget_is_kept_and_the_unwalked_branches_are_named(tbx, monkeypatch):
    service, archive = tbx
    monkeypatch.setattr(T, 'MAX_WALK_REQUESTS', 3)
    sid = await research(service)
    data = await find(service, sid, 'Hluboká nad Vltavou', 'land_records')
    walked = [h for h in hits(archive) if h.startswith('/tree.php')]
    assert len(walked) == 3
    warn = next(w for w in data['warnings'] if 'neprohledáno' in w)
    assert 'zadejte okres' in warn and 'vrchnostenské knihy (soupisy poddaných) (větev nezačata)' in warn and 'urbáře (větev nezačata)' in warn


@pytest.mark.asyncio
async def test_district_picks_the_fund_first(tbx):
    service, archive = tbx
    sid = await research(service)
    data = await find(service, sid, 'Hluboká', 'land_records', district='České Budějovice')
    assert [p['source_id'] for p in data['places']] == ['trebon:place:l700004']
    walked = [h for h in hits(archive) if h.startswith('/tree.php')]
    fund = '/tree.php?menu=0&doctree=1qe&lang=cs'
    other = '/tree.php?menu=0&doctree=1qa&lang=cs'
    assert fund in walked and (other not in walked or walked.index(fund) < walked.index(other)), 'the fund named like the district first'


# ------------------------------------------------------------------ census

@pytest.mark.asyncio
async def test_census_units_by_municipality_and_year_with_house_numbers(tbx):
    service, archive = tbx
    sid = await research(service, event_type='birth', year_from=1860, year_to=1870, person={'house_number': '300'})
    data = await find(service, sid, 'České Budějovice', 'census', district='České Budějovice')
    place = next(p for p in data['places'] if p['source_id'] == 'trebon:place:c800001')
    assert place['label'] == 'České Budějovice (sčítání lidu; SOkA České Budějovice › 1869 › Soudní okres České Budějovice)'
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:c800001'], kind='census')
    rows = listed.data['sources']
    by = {r['source_id']: r for r in rows}
    assert by['trebon:book:810002']['relevance']['house_number_covered'] is True and by['trebon:book:810001']['relevance']['house_number_covered'] is False
    hn = by['trebon:book:810001']['house_numbers']
    assert (hn['start'], hn['end'], hn['parsed'], hn['raw']) == (1, 250, True, 'České Budějovice, čp. 1-250')
    assert rows[0]['source_id'] == 'trebon:book:810002'         # the set covering the house number first
    assert by['trebon:book:810001']['census_year'] == 1869 and by['trebon:book:810001']['category'] == 'other'
    assert by['trebon:book:810001']['relevance']['status'] == 'candidate'


@pytest.mark.asyncio
async def test_census_year_outside_the_query_is_excluded_and_unknown_house_numbers_are_never_false(tbx):
    service, _ = tbx
    sid = await research(service, year_from=1900, year_to=1910, person={'house_number': '5'})
    await find(service, sid, 'Zliv', 'census')
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:c800002'], kind='census')
    row = listed.data['sources'][0]
    assert row['relevance']['status'] == 'excluded'
    assert row['relevance']['house_number_covered'] is True      # čp. 1-90 a 100-120 is parsed and contains 5
    sid2 = await research(service, person={'house_number': '95'})
    await find(service, sid2, 'Zliv', 'census')
    row2 = (await call(service, 'list_sources', research_id=sid2, place_ids=['trebon:place:c800002'], kind='census')).data['sources'][0]
    assert row2['relevance']['house_number_covered'] is False
    sid3 = await research(service, person={'house_number': '7'})
    await find(service, sid3, 'Volary', 'census')
    row3 = (await call(service, 'list_sources', research_id=sid3, place_ids=['trebon:place:c800003'], kind='census')).data['sources'][0]
    assert row3['house_numbers']['parsed'] is False and row3['relevance']['house_number_covered'] is None


@pytest.mark.asyncio
async def test_census_open_gives_viewer_links_only(tbx):
    service, archive = tbx
    sid = await research(service)
    await find(service, sid, 'Zliv', 'census')
    await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:c800002'], kind='census')
    d = (await call(service, 'open_source', research_id=sid, source_id='trebon:book:810011')).data
    assert d['kind'] == 'census' and d['total'] == 1 and d['pages'][0]['viewer_url'].endswith('/view.php?menu=3&id=810011&page=1')
    assert not any(p.startswith('/cgi-bin') for p, _ in archive.trebon.log)


@pytest.mark.asyncio
async def test_summaries_of_opened_units_in_the_research_status(tbx):
    service, _ = tbx
    sid = await research(service)
    await find(service, sid, 'Blatná', 'land_records')
    await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:l700001'], kind='land_records')
    await call(service, 'open_source', research_id=sid, source_id='trebon:book:720001')
    status = (await call(service, 'get_research_status', research_id=sid)).data
    text = repr(status)
    assert 'trebon:book:720001' in text and 'Velkostatek Blatná' in text



def test_census_table_with_empty_header_ids_is_read_by_header_text():
    page = T.parse_unit_page(D.unit_page('1sec9cx'), 800001)          # live shape: every th id is "col_", unclosed rows, DA?menu=3&id= links
    assert page.year == 1869 and [r.id for r in page.rows] == [810001, 810002] and page.not_online == 0
    row = page.rows[0]
    assert row.cells['col_nazev'] == 'České Budějovice, čp. 1-250' and row.cells['col_signatura'] == 'B I čp. 7'
    assert row.cells['col_ukladaci_jednotka'] == 'kniha 1' and row.headers['col_nazev'] == 'Obsah' and row.headers['col_obec'] == 'Sčítací obec'


def test_census_page_without_title_column_or_with_unknown_rows_is_parse_error():
    bad(T.parse_unit_page, D.unit_page('1sec9cx').replace('Obsah', 'Poznámka'), 800001)
    bad(T.parse_unit_page, D.unit_page('1sec9cx').replace('<tr id=\'810002\' title=\'Otevřít\'>', '<tr id=\'810001\' title=\'Otevřít\'>'), 800001)


def test_section_node_of_an_unknown_shape_is_parse_error():
    bad(T.parse_section_node, D.section_page('1qa').replace('doctree=1qab', 'doctree=1qabcd'), '1qa')             # a child two characters longer
    bad(T.parse_section_node, D.wrap("<div id='menu'>" + D._node('1qab', 'toggle("1qab")', 'B', 'p_p_s') + '</div>'), '1qab')   # no child at all


def test_local_and_host_wide_errors_are_told_apart():
    assert T.node_error_is_local(ResearchError('PARSE_ERROR', 'x'))
    assert T.node_error_is_local(ResearchError('SOURCE_UNAVAILABLE', 'x', status=404))
    assert not T.node_error_is_local(ResearchError('SOURCE_UNAVAILABLE', 'x', status=503, retry_after=60))
    assert not T.node_error_is_local(ResearchError('SOURCE_UNAVAILABLE', 'x', content_type='text/x'))
    assert not T.node_error_is_local(ResearchError('RATE_LIMITED', 'x')) and not T.node_error_is_local(ResearchError('SOURCE_ACCESS_DENIED', 'x'))


@pytest.mark.asyncio
async def test_a_paused_host_stops_the_walk(tbx, monkeypatch):
    service, _ = tbx
    real = service.trebon._page
    calls = []
    async def page(sid, owner, url):
        calls.append(url)
        if len(calls) == 3:
            raise ResearchError('RATE_LIMITED', 'pause', retry_after=60)
        return await real(sid, owner, url)
    monkeypatch.setattr(service.trebon, '_page', page)
    sid = await research(service)
    out = await find(service, sid, 'Blatná', 'land_records')
    assert out['searched'] == [] and 'RATE_LIMITED' in out['not_searched'][0]['reason'] and len(calls) == 3


@pytest.mark.asyncio
async def test_census_open_uses_the_same_viewer_id_space_and_dates_from_the_row_or_year(tbx):
    service, _ = tbx
    sid = await research(service, year_from=1860, year_to=1875)
    await find(service, sid, 'Volary', 'census')
    row = (await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:c800003'], kind='census')).data['sources'][0]
    assert row['census_year'] == 1870 and row['dating']['status'] == 'uncertain'          # page says 1870, tree node 1869
    assert [(x['start'], x['end']) for x in row['dating']['spans']] == [(1869, 1869), (1870, 1870)] and row['relevance']['status'] == 'needs_review'
    assert row['title'] == 'Volary' and row['coverage'] == {} and row['citation'].startswith('Státní oblastní archiv v Třeboni, Sčítání lidu 1870, ')
    d = (await call(service, 'open_source', research_id=sid, source_id='trebon:book:810021')).data
    assert d['pages'][0]['viewer_url'] == f'https://{H}/view.php?menu=3&id=810021&page=1'


@pytest.mark.asyncio
async def test_house_numbers_come_from_the_title_only(tbx):
    service, _ = tbx
    sid = await research(service, person={'house_number': '7'})
    await find(service, sid, 'České Budějovice', 'census', district='České Budějovice')
    rows = (await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:c800001'], kind='census')).data['sources']
    first = next(r for r in rows if r['source_id'] == 'trebon:book:810001')
    assert (first['house_numbers']['start'], first['house_numbers']['end']) == (1, 250) and first['house_numbers']['raw'] == 'České Budějovice, čp. 1-250'


@pytest.mark.asyncio
async def test_land_units_carry_no_event_coverage(tbx):
    service, _ = tbx
    sid = await research(service)
    await find(service, sid, 'Blatná', 'land_records')
    rows = (await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:l700001'], kind='land_records')).data['sources']
    assert all(r['coverage'] == {} for r in rows)


@pytest.mark.asyncio
async def test_the_seat_of_a_congregation_is_not_warned_about(tbx):
    service, _ = tbx
    sid = await research(service, place_hint='Slavonice')
    await find(service, sid, 'Slavonice', 'parish_registers')
    listed = await call(service, 'list_sources', research_id=sid, place_ids=['trebon:place:950001'], kind='parish_registers')
    assert not any('Místo ze zadání' in w for w in listed.data['warnings'])
    sid2 = await research(service, place_hint='Jemnice')
    await find(service, sid2, 'Slavonice', 'parish_registers')
    listed2 = await call(service, 'list_sources', research_id=sid2, place_ids=['trebon:place:950001'], kind='parish_registers')
    assert any('Místo ze zadání' in w for w in listed2.data['warnings'])


@pytest.mark.asyncio
async def test_a_node_id_used_by_another_tree_is_never_overwritten(tbx):
    service, _ = tbx
    service.store_.put_entity('trebon_place', 'trebon:place:l700001', {'tree': '1qzzz', 'id': 700001, 'name': 'Jiné', 'section': 'land_records'})
    sid = await research(service)
    out = await find(service, sid, 'Blatná', 'land_records')
    assert 'trebon:place:l700001' not in [p['source_id'] for p in out['places']]
    assert any('už použito pro jiný uzel' in w for w in out['warnings'])
    assert service.store_.entity('trebon_place', 'trebon:place:l700001')['tree'] == '1qzzz'


@pytest.mark.asyncio
async def test_place_ids_of_the_same_archive_id_differ_by_branch():
    assert T.place_id_of('parish_registers', 5) == 'trebon:place:5' and T.place_id_of('land_records', 5) == 'trebon:place:l5'
    assert T.place_id_of('census', 5) == 'trebon:place:c5' and T.kind_of_place('trebon:place:c5') == 'census'


@pytest.mark.asyncio
async def test_a_repeated_search_continues_where_the_last_stopped_and_cached_pages_are_free(tbx, monkeypatch):
    service, archive = tbx
    monkeypatch.setattr(T, 'MAX_WALK_REQUESTS', 3)
    sid = await research(service)
    first = await find(service, sid, 'Hluboká nad Vltavou', 'land_records')
    urls1 = [h for h in hits(archive) if h.startswith('/tree.php')]
    assert len(urls1) == 3 and any('neprohledáno' in w for w in first['warnings'])
    second = await find(service, sid, 'Hluboká nad Vltavou', 'land_records')
    urls2 = [h for h in hits(archive) if h.startswith('/tree.php')]
    assert len(urls2) == 6 and len(set(urls2)) == 6, 'three new pages, none fetched twice'
    third = await find(service, sid, 'Hluboká nad Vltavou', 'land_records')
    urls3 = [h for h in hits(archive) if h.startswith('/tree.php')]
    assert len(set(urls3)) == len(urls3) and 'trebon:place:l700004' in [p['source_id'] for p in third['places']]


@pytest.mark.asyncio
async def test_after_a_hit_only_branches_named_like_the_term_are_fetched(tbx):
    service, archive = tbx
    sid = await research(service)
    data = await find(service, sid, 'Blatná', 'land_records')
    assert data['places']
    fetched = [h for h in hits(archive) if h.startswith('/tree.php')]
    # the hit is found in the letter branch; the other (unnamed) branches are not fetched once it is known
    again = await find(service, sid, 'Blatná', 'land_records')
    assert [p['source_id'] for p in again['places']] == [p['source_id'] for p in data['places']]
    assert [h for h in hits(archive) if h.startswith('/tree.php')] == fetched      # repeated call: all from the cache
    if any('nenačítaly' in w for w in data['warnings']):
        assert any('zadejte okres' in w for w in data['warnings'])
