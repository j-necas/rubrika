"""SOA Třeboň parsers against the synthetic DigiArchiv pages: letters, parishes, books, viewer. Unknown structure is PARSE_ERROR."""
import pytest

from rubrika import demo_trebon as D
from rubrika import trebon as T
from rubrika.errors import ResearchError

LETTERS = T.parse_letters(D.letters_page())


def letter(code):
    return next(l for l in LETTERS if l.code == code)


def bad(fn, *args):
    with pytest.raises(ResearchError) as e:
        fn(*args)
    assert e.value.code == 'PARSE_ERROR'


def test_letters_come_from_the_tree_and_carry_their_initials():
    assert [(l.code, l.label, l.initials) for l in LETTERS] == [('1nrc', 'C/Č', ('c',)), ('1nri', 'CH', ('ch',)), ('1nrp', 'P', ('p',)), ('1nrt', 'T', ('t',))]


@pytest.mark.parametrize('term,code', [('České Budějovice', '1nrc'), ('Cizkrajov', '1nrc'), ('Čakov', '1nrc'), ('Chotoviny', '1nri'),
                                       ('chotoviny', '1nri'), ('Tábor', '1nrt'), ('Třeboň', '1nrt'), ('Písek', '1nrp')])
def test_a_name_is_filed_under_the_letter_it_starts_with(term, code):
    assert T.pick_letter(LETTERS, term).code == code


def test_no_letter_for_a_name_the_list_does_not_start_with():
    assert T.pick_letter(LETTERS, 'Žďár') is None and T.pick_letter(LETTERS, 'Újezd') is None


def test_parishes_of_a_letter():
    parishes = T.parse_letter(D.letter_page('c'), letter('1nrc'))
    assert [(p.id, p.tree, p.name) for p in parishes] == [
        (910001, '1nrcl', 'České Budějovice (St. Nicholas)'), (910002, '1nrcM', 'České Budějovice (St. John of Nepomuk)'),
        (910003, '1nrco', 'Český Krumlov')]
    assert parishes[0].base == 'České Budějovice' and parishes[0].label == 'České Budějovice (St. Nicholas), římskokatolická farnost'


def test_matching_is_by_word_beginnings_exact_name_first():
    parishes = T.parse_letter(D.letter_page('c'), letter('1nrc'))
    assert [p.id for p in T.match_parishes(parishes, 'České Budějovice')] == [910001, 910002]
    assert [p.id for p in T.match_parishes(parishes, 'ceske budej')] == [910001, 910002]
    assert [p.id for p in T.match_parishes(parishes, 'Krumlov')] == [910003]
    assert [p.id for p in T.match_parishes(parishes, 'Nicholas')] == [910001]
    assert [p.id for p in T.match_parishes(parishes, 'Kaplice')] == []
    assert T.match_parishes(parishes, 'farnost') == []


def test_parish_page_gives_books_with_years_localities_and_the_parish_district():
    page = T.parse_parish(D.parish_page(910001), 910001)
    assert page.title == 'České Budějovice (sv. Mikuláš)' and page.district == 'České Budějovice'
    assert page.parish_district == ['České Budějovice', 'České Vrbné', 'Čtyři Dvory', 'Vidov', 'Mokré']
    assert page.other_localities == ['Adamov', 'Borovany', 'Hamburk']
    assert [b.id for b in page.books] == [911001, 911002, 911003, 911004, 911005, 911006, 911007, 911008, 911009]
    first = page.books[0]
    assert (first.number, first.label, first.types, first.note) == ('1', 'register B', [('B', '1615-1630')], '')
    assert first.localities == ['České Budějovice', 'České Vrbné', 'Čtyři Dvory'] and first.other_localities == ['Borovany']
    assert page.books[4].types == [('B', '1785-1920'), ('M', '1793-1920'), ('D', '1785-1919')] and page.books[4].note == 's indexem'
    assert page.not_online == [('3', '2033')]
    mentioned = page.books[8]     # kept for no locality, only mentioning one: the archive's `mat_nevedena` alone
    assert (mentioned.number, mentioned.localities, mentioned.other_localities) == ('9', [], ['České Budějovice'])


def test_a_book_from_another_parish_page_or_without_the_parishs_link_is_refused():
    bad(T.parse_parish, D.parish_page(910001), 910002)
    bad(T.parse_parish, D.parish_page(910001).replace('https://digi.ceskearchivy.cz/910001', 'https://digi.ceskearchivy.cz/'), 910001)


@pytest.mark.parametrize('change', [
    lambda h: h.replace("class='nadpis'", "class='titul'"),
    lambda h: h.replace('m_k_n', 'm_k_x'),
    lambda h: h.replace('class="p_m_p"', 'class="p_m_q"').replace('class="p_m_p0"', 'class="p_m_q0"'),
    lambda h: h.replace('class="mat_vedena"', 'class="mat_x"'),
    lambda h: h.replace('<td class="m_k_t c_n" valign="top"></td></tr>', '</tr>', 1),
    lambda h: h.replace('<tr class="p_m_p" id="911002"', '<tr class="p_m_p" id="911001"'),
])
def test_changed_parish_page_is_parse_error_not_an_empty_list(change):
    bad(T.parse_parish, change(D.parish_page(910001)), 910001)


def test_letter_page_without_parishes_and_index_without_letters_are_parse_errors():
    bad(T.parse_letter, D.letter_page('c').replace('DA?menu=0', 'DB?menu=0'), letter('1nrc'))
    bad(T.parse_letters, D.letters_page().replace('doctree=1nr', 'doctree=1nx'))
    bad(T.parse_letters, '<html><body>Please wait</body></html>')


def cov(label, types):
    c = T.coverage_of(label, types)
    return {e: (v.records.status, [(s.start, s.end) for s in v.records.spans], v.index.status, [(s.start, s.end) for s in v.index.spans]) for e, v in c.items()}


def test_register_coverage_is_by_the_listed_types():
    assert cov('register B', [('B', '1615-1630')]) == {
        'birth': ('known', [(1615, 1630)], 'absent', []), 'marriage': ('uncertain', [], 'absent', []), 'death': ('uncertain', [], 'absent', [])}
    c = cov('register BMD', [('B', '1785-1920'), ('M', '1793-1920'), ('D', '1785-1919')])
    assert c['marriage'] == ('known', [(1793, 1920)], 'absent', []) and c['death'][1] == [(1785, 1919)]


def test_index_coverage_is_index_years_and_no_records():
    c = cov('index B', [('B', '1785-1900')])
    assert c['birth'] == ('absent', [], 'known', [(1785, 1900)]) and c['death'] == ('absent', [], 'uncertain', [])


def test_czech_markers_are_understood_too():
    c = cov('matrika N', [('N', '1700-1750')])
    assert c['birth'] == ('known', [(1700, 1750)], 'absent', []) and c['marriage'][0] == 'uncertain'
    assert cov('rejstřík Z', [('Z', '1700-1750')])['death'] == ('absent', [], 'known', [(1700, 1750)])
    assert cov('matrika NOZ', [('N', '1700-1710'), ('O', '1701-1711'), ('Z', '1702-1712')])['marriage'][1] == [(1701, 1711)]
    assert all(v[0] == 'uncertain' for v in cov('matrika NB', [('N', '1700-1710'), ('B', '1700-1710')]).values())


def test_parish_page_with_czech_row_headings():
    html = D.parish_page(910001).replace('Parish district (1862)', 'Farní obvod (1862)').replace('Other localities', 'Ostatní lokality')
    page = T.parse_parish(html, 910001)
    assert page.parish_district[0] == 'České Budějovice' and page.other_localities == ['Adamov', 'Borovany', 'Hamburk']


@pytest.mark.parametrize('label,types', [('register', []), ('kniha B', [('B', '1700-1710')]), ('register B', [('R', '1700-1710')]),
                                         ('register BB', [('B', '1700-1710'), ('B', '1720-1730')])])
def test_unclear_labels_and_tables_never_exclude(label, types):
    assert all(v[0] == 'uncertain' and v[2] == 'uncertain' for v in cov(label, types).values())


def test_unclear_years_are_uncertain_not_absent():
    assert cov('register B', [('B', '1700-1710 (torzo)')])['birth'][0] == 'uncertain'


def test_viewer_gives_the_number_of_scans_and_needs_nothing_about_images():
    assert T.parse_viewer(D.viewer_page(911001, 2), 911001, 2).total == 3
    bare = ("<html><form><input type=hidden name='id' value='911001'><select name='page' id='page'><option value='1'>Snímek 1</option>"
            "<option value='2' selected>Snímek 2</option></select></form><script>top.window.history.pushState(null,null,\"/911001/2\");</script></html>")
    assert T.parse_viewer(bare, 911001, 2).total == 2


@pytest.mark.parametrize('change', [
    lambda h: h.replace('pushState(null,null,"/911001/2")', 'pushState(null,null,"/911001/3")'),
    lambda h: h.replace("id='page'", "id='strana'"),
    lambda h: h.replace(" selected", ""),
    lambda h: h.replace("<option value='3'>Image 3</option>", "<option value='4'>Image 4</option>"),
    lambda h: h.replace("value='911001'", "value='911002'"),
])
def test_changed_viewer_is_parse_error(change):
    bad(T.parse_viewer, change(D.viewer_page(911001, 2)), 911001, 2)


def test_viewer_of_another_book_or_scan_is_refused():
    bad(T.parse_viewer, D.viewer_page(911001, 2), 911002, 2)
    bad(T.parse_viewer, D.viewer_page(911001, 2), 911001, 1)


@pytest.mark.parametrize('url,book', [
    ('https://digi.ceskearchivy.cz/2711/2', 'trebon:book:2711'), ('https://digi.ceskearchivy.cz/2711', 'trebon:book:2711'),
    ('https://digi.ceskearchivy.cz/2711/2/100/200/150/0', 'trebon:book:2711'),
    ('https://digi.ceskearchivy.cz/view.php?menu=3&id=2711&page=4', 'trebon:book:2711'),
    ('https://digi.ceskearchivy.cz/DA?menu=3&id=2711', 'trebon:book:2711')])
def test_book_from_a_pasted_link(url, book):
    assert T.book_id_from_url(url) == book


@pytest.mark.parametrize('url', ['http://digi.ceskearchivy.cz/2711/2', 'https://digi.ceskearchivy.cz.evil.example/2711/2',
                                 'https://digi.ceskearchivy.cz/pages/search.php', 'https://digi.ceskearchivy.cz/view.php?menu=2&id=1&page=1',
                                 'https://digi.ceskearchivy.cz/tree.php?menu=0&doctree=1nrcl&id=2710', 'https://user@digi.ceskearchivy.cz/2711/2'])
def test_other_links_are_refused(url):
    with pytest.raises(ResearchError) as e:
        T.book_id_from_url(url)
    assert e.value.code == 'INVALID_SOURCE'


def test_ids():
    assert T.parish_id_of('trebon:place:910001') == 910001 and T.book_id_of('trebon:book:911001') == 911001
    for wrong in ('trebon:place:x', 'trebon:book:1', 'pf:place:1', ''):
        with pytest.raises(ResearchError):
            T.parish_id_of(wrong)


def test_a_note_saying_the_book_has_an_index_never_leaves_index_absent():
    c = T.coverage_of('register B', [('B', '1631-1660')], 's indexem')
    assert (c['birth'].index.status, [(x.start, x.end) for x in c['birth'].index.spans]) == ('known', [(1631, 1660)])
    assert c['marriage'].index.status == 'uncertain' and c['marriage'].records.status == 'uncertain'
    assert T.coverage_of('register B', [('B', '1631-1660')], 'Index v knize')['birth'].index.status == 'known'
    assert T.coverage_of('register B', [('B', '1631-1660')], 'duplikát')['birth'].index.status == 'absent'
    assert T.coverage_of('register B', [('B', '1631-1660')])['birth'].index.status == 'absent'
    row = T.parse_parish(D.parish_page(910001), 910001).books[1]          # the synthetic row 911002 carries „s indexem“
    assert row.note == 's indexem'
    assert T.TrebonCatalog._book(T.TrebonCatalog(None, None), row, T.parse_parish(D.parish_page(910001), 910001)).coverage['birth'].index.status == 'known'
