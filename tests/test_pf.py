import pytest

from rubrika import demo_pf as D
from rubrika import pf as P
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.catalog import relevance

URL = 'https://www.portafontium.eu/searching/register?search_api_views_fulltext_1=Demolov'


def ev(book, e):
    c = book.coverage[e]
    return c.records.status, [(s.start, s.end) for s in c.records.spans], c.index.status, [(s.start, s.end) for s in c.index.spans]


@pytest.mark.parametrize('text,expected', [
    ('1785-1830 *, *i', {'birth': ('known', [(1785, 1830)], 'known', [(1785, 1830)]),
                         'marriage': ('absent', [], 'absent', []), 'death': ('absent', [], 'absent', [])}),
    ('1545-1674 *, oo, +', {'birth': ('known', [(1545, 1674)], 'absent', []),
                            'marriage': ('known', [(1545, 1674)], 'absent', []),
                            'death': ('known', [(1545, 1674)], 'absent', [])}),
    ('1648-1733 *i', {'birth': ('absent', [], 'known', [(1648, 1733)]), 'marriage': ('absent', [], 'absent', []),
                      'death': ('absent', [], 'absent', [])}),
    ('1719-1947 *i, ooi, +i', {'birth': ('absent', [], 'known', [(1719, 1947)]), 'marriage': ('absent', [], 'known', [(1719, 1947)]),
                               'death': ('absent', [], 'known', [(1719, 1947)])}),
    ('1906-1939 + i', {'birth': ('absent', [], 'absent', []), 'marriage': ('absent', [], 'absent', []),
                       'death': ('absent', [], 'known', [(1906, 1939)])}),   # "+ i": the index mark with a space, an index only
    ('1732-1783 *, oo, ooi, +, +i', {'birth': ('known', [(1732, 1783)], 'absent', []),
                                     'marriage': ('known', [(1732, 1783)], 'known', [(1732, 1783)]),
                                     'death': ('known', [(1732, 1783)], 'known', [(1732, 1783)])}),
    ('1784-1806 oo', {'birth': ('absent', [], 'absent', []), 'marriage': ('known', [(1784, 1806)], 'absent', []),
                      'death': ('absent', [], 'absent', [])}),
    ('1801 *', {'birth': ('known', [(1801, 1801)], 'absent', []), 'marriage': ('absent', [], 'absent', []),
                'death': ('absent', [], 'absent', [])}),
    ('1700-1710 *, 1720-1730 oo', {'birth': ('known', [(1700, 1710)], 'absent', []),
                                   'marriage': ('known', [(1720, 1730)], 'absent', []), 'death': ('absent', [], 'absent', [])}),
])
def test_parse_dating_marks(text, expected):
    cov = P.parse_dating(text)
    for e, want in expected.items():
        got = (cov[e].records.status, [(s.start, s.end) for s in cov[e].records.spans],
               cov[e].index.status, [(s.start, s.end) for s in cov[e].index.spans])
        assert got == want, (text, e)


@pytest.mark.parametrize('text', ['', '1900-1910 ?', '1900-1910', '*, oo', 'asi 1800 *', '1910-1900 *', '1800-1810 x'])
def test_parse_dating_unclear_is_uncertain_never_excluding(text):
    cov = P.parse_dating(text)
    assert all(c.records.status == 'uncertain' and c.index.status == 'uncertain' for c in cov.values())
    assert all(c.records.raw == text.strip() for c in cov.values())
    q = SearchQuery(raw_query='x', event_type='birth', year_from=1200, year_to=1201)
    from rubrika.models import Book
    b = Book(source_id='pf:book:a/b', number='n', creator='', coverage=cov, detail_url='https://www.portafontium.eu/register/a/b')
    assert relevance(b, q)['status'] == 'needs_review'


def test_parse_search_rows_german_names_and_marks():
    page = P.parse_search(D.search_page({'search_api_views_fulltext_1': ['Demolov']}), URL)
    assert [b.number for b in page.books] == ['Demolov 01', 'Demolov 02', 'Demolov 03', 'Demolov 04']
    assert page.last_page == 1 and page.warnings == []
    first = page.books[0]
    assert first.source_id == 'pf:book:soap-pn/demolov-01'
    assert first.detail_url == 'https://www.portafontium.eu/register/soap-pn/demolov-01'
    assert first.nid == str(D.nid('soap-pn/demolov-01')) and first.dating_raw == '1785-1830 *, *i'
    assert first.archive_label == 'Plzeň (státní archiv)'
    assert first.places == ['Demolov (Demolau)', 'Horní Demolov (Ober-Demolau)']
    assert ev(first, 'birth') == ('known', [(1785, 1830)], 'known', [(1785, 1830)])
    assert ev(page.books[1], 'marriage')[0] == 'known' and ev(page.books[1], 'birth')[0] == 'absent'
    assert page.books[3].places == ['Demolov (Demolau)', 'Testov (Teststadt)']


def test_parse_search_last_page_and_second_page():
    page2 = P.parse_search(D.search_page({'search_api_views_fulltext_1': ['Demolov'], 'page': ['1']}), URL)
    assert [b.number for b in page2.books] == ['Demolov 05', 'Index Demolov 001']
    assert page2.last_page == 1
    other = page2.books[1]
    assert other.source_id == 'pf:book:amp/index-demolov-001' and other.archive_label == 'Demolov (archiv města)'


def test_parse_search_empty_result_is_empty_not_error():
    page = P.parse_search(D.search_page({'search_api_views_fulltext_1': ['Nikdyneni']}), URL)
    assert page.books == [] and page.last_page == 0


def test_parse_search_structure_changes_are_parse_errors():
    good = D.search_page({'search_api_views_fulltext_1': ['Demolov']})
    for broken in (
        good.replace('id="views-exposed-form-solr-searching-page-solr-register"', 'id="other"'),   # no search form at all
        good.replace('<th scope="col">Místo</th>', '<th scope="col">Lokalita</th>'),
        good.replace('<th scope="col">Nadpis</th>', ''),
        good.replace('<td class="views-field views-field-nothing"><p>SYNTHETIC regest...</p> </td>', ''),      # a column is gone
        good.replace('<td class="views-field views-field-nothing"><p>SYNTHETIC regest...</p> </td>', '<td>a</td><td>b</td>', 1),   # one more
        '<html><body><h1>503 Service Unavailable</h1></body></html>',
    ):
        with pytest.raises(ResearchError) as e:
            P.parse_search(broken, URL)
        assert e.value.code == 'PARSE_ERROR'


def test_parse_search_row_with_foreign_link_is_parse_error():
    good = D.search_page({'search_api_views_fulltext_1': ['Demolov']})
    bad = good.replace('href="/register/soap-pn/demolov-01"', 'href="https://evil.example/register/soap-pn/demolov-01"')
    with pytest.raises(ResearchError) as e:
        P.parse_search(bad, URL)
    assert e.value.code == 'PARSE_ERROR'


def test_parse_search_row_without_detail_link_is_a_warning_not_a_loss():
    good = D.search_page({'search_api_views_fulltext_1': ['Demolov']})
    bad = good.replace('<a href="/register/soap-pn/demolov-02">Demolov 02</a>', 'Demolov 02')
    page = P.parse_search(bad, URL)
    assert len(page.books) == 3 and any('Demolov 02' in w for w in page.warnings)


def test_parse_places_groups_by_name_and_keeps_german_variants():
    rows = P.parse_search(D.search_page({'search_api_views_fulltext_1': ['Demolov']}), URL).books
    places = P.candidate_places(rows, 'Demolov')
    ids = [p['id'] for p in places]
    assert ids[0] == 'pf:place:demolov' and 'pf:place:horni-demolov' in ids
    top = places[0]
    assert top['name'] == 'Demolov' and top['names'] == ['Demolov', 'Demolau'] and top['books'] == 4
    # Testov is listed on a book, but does not match the term
    assert 'pf:place:testov' not in ids


def test_candidate_places_match_german_term_and_whole_words_only():
    rows = P.parse_search(D.search_page({'search_api_views_fulltext_1': ['Demolov']}), URL).books
    assert P.candidate_places(rows, 'Demolau')[0]['id'] == 'pf:place:demolov'
    assert P.candidate_places(rows, 'Demol') == []
    assert [p['id'] for p in P.candidate_places(rows, 'Ober Demolau')] == ['pf:place:horni-demolov']


def test_parse_detail():
    d = P.parse_detail(D.detail_page('soap-pn/demolov-01'), 'soap-pn/demolov-01')
    assert d['signature'] == 'Demolov 01' and d['archive_label'] == 'Plzeň (státní archiv)'
    assert d['fund_number'] == '10014' and d['fund_name'] == 'Sbírka matrik západních Čech'
    assert d['originator'] == 'Demolov (Římskokatolická církev)'
    assert d['places'] == ['Demolov (Demolau)', 'Horní Demolov (Ober-Demolau)']
    assert d['types'] == ['Matrika narozených', 'Index narozených'] and d['pages'] == 6
    assert d['nid'] == str(D.nid('soap-pn/demolov-01'))


def test_parse_detail_rejects_other_book_and_structure_change():
    good = D.detail_page('soap-pn/demolov-01')
    for text, slug in ((good, 'soap-pn/demolov-02'),
                       (good.replace('field-name-field-sign', 'field-name-other'), 'soap-pn/demolov-01'),
                       (good.replace('/iipimage/' + str(D.nid('soap-pn/demolov-01')) + '/', '/iipimage/12345/', 1), 'soap-pn/demolov-01'),
                       (good.replace('<article', '<div').replace('</article>', '</div>'), 'soap-pn/demolov-01'),
                       ('<html><body>Chyba</body></html>', 'soap-pn/demolov-01')):
        with pytest.raises(ResearchError) as e:
            P.parse_detail(text, slug)
        assert e.value.code == 'PARSE_ERROR'


def test_parse_viewer_keys_and_urls():
    keys = P.parse_viewer(D.viewer_page('soap-pn/demolov-01'), str(D.nid('soap-pn/demolov-01')))
    assert [k for k, _ in keys] == D.keys('soap-pn/demolov-01')
    assert keys[0][1] == ('https://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/demolov/demolov-01/'
                          'demolov-01_0010-x.jp2/info.json')


@pytest.mark.parametrize('mutate', [
    lambda h: h.replace('imagesSet :', 'imagesList :'),
    lambda h: h.replace('DeepZoom=register/', 'DeepZoom=other/', 1),
    lambda h: h.replace('.jp2.dzi"', '.png"', 1),
    lambda h: h.replace('/fcgi-bin/iipsrv.fcgi?DeepZoom=', 'https://evil.example/fcgi-bin/iipsrv.fcgi?DeepZoom=', 1),
    lambda h: h.replace('imagesSet : [', 'imagesSet : [1, ', 1),
    lambda h: h.replace('urlsSet : [', 'urlsSet : ["iipimage/1/x", ', 1),
    lambda h: h.replace('imagesSet : [', 'imagesSet : [], x: [', 1),
    lambda h: h.replace('demolov-01_0030-n', 'demolov-01_0010-x'),      # duplicate image
    lambda h: h.replace('demolov-01_0030-n', 'a b'),
])
def test_parse_viewer_structure_changes(mutate):
    html = mutate(D.viewer_page('soap-pn/demolov-01'))
    with pytest.raises(ResearchError) as e:
        P.parse_viewer(html, str(D.nid('soap-pn/demolov-01')))
    assert e.value.code in ('PARSE_ERROR', 'UNSUPPORTED_SOURCE')


def test_viewer_nid_mismatch_is_parse_error():
    with pytest.raises(ResearchError) as e:
        P.parse_viewer(D.viewer_page('soap-pn/demolov-01'), '111')
    assert e.value.code == 'PARSE_ERROR'


def test_book_id_from_url():
    ok = P.book_id_from_url('https://www.portafontium.eu/register/soap-pn/abertamy-04')
    assert ok == 'pf:book:soap-pn/abertamy-04'
    assert P.book_id_from_url('https://www.portafontium.eu/register/soap-pn/abertamy-04/') == ok
    for bad in ('http://www.portafontium.eu/register/soap-pn/abertamy-04', 'https://evil.example/register/soap-pn/abertamy-04',
                'https://www.portafontium.eu/iipimage/30060004', 'https://www.portafontium.eu/register/soap-pn',
                'https://user@www.portafontium.eu/register/soap-pn/abertamy-04'):
        with pytest.raises(ResearchError) as e:
            P.book_id_from_url(bad)
        assert e.value.code == 'INVALID_SOURCE'


def test_search_url_is_exact_and_sorted_params():
    url = P.search_url('Plzeň', page=2, types=('register-birth', 'index-birth'))
    assert url == ('https://www.portafontium.eu/searching/register?search_api_views_fulltext=&search_api_views_fulltext_1=Plze%C5%88'
                   '&search_api_views_fulltext_2=&search_api_aggregation_3=&field_doc_dates_field_doc_dates_from='
                   '&field_register_type%5B%5D=register-birth&field_register_type%5B%5D=index-birth&page=2')
    assert 'page=' not in P.search_url('Plzeň')


def test_citation_has_portal_url_and_source_names():
    d = P.parse_detail(D.detail_page('soap-pn/demolov-01'), 'soap-pn/demolov-01')
    assert P.citation(d, 'soap-pn/demolov-01') == ('Státní oblastní archiv v Plzni, Sbírka matrik západních Čech, '
                                                   'sign. Demolov 01, https://www.portafontium.eu/register/soap-pn/demolov-01')
    other = P.parse_detail(D.detail_page('amp/index-demolov-001'), 'amp/index-demolov-001')
    c = P.citation(other, 'amp/index-demolov-001')
    assert c.startswith('Demolov (archiv města) (Porta fontium)') and c.endswith('https://www.portafontium.eu/register/amp/index-demolov-001')


def test_docs_carry_the_terms_notice_and_pace():
    from pathlib import Path
    root = Path(__file__).parent.parent
    ref = (root / 'plugin/skills/ancestor-research/references/archives/pf.md').read_text(encoding='utf-8')
    assert '§ 92' in ref and 'Crawl-delay 10' in ref and 'terms_notice' in ref and 'https://www.portafontium.eu/terms-of-use' in ref
    skill = (root / 'plugin/skills/ancestor-research/SKILL.md').read_text(encoding='utf-8')
    assert '`pf`' in skill and 'terms_notice' in skill and 'jednou za hledání' in skill
    archives = (root / 'docs/ARCHIVY.md').read_text(encoding='utf-8')
    assert 'Podmínky Porta fontium' in archives and '§ 92' in archives
    readme = (root / 'README.md').read_text(encoding='utf-8')
    assert 'docs/ARCHIVY.md' in readme and '§ 92' in readme


def test_parse_detail_takes_viewer_links_from_the_sidebar_block_when_the_article_has_none():
    slug = 'soap-pn/demolov-01'
    d = P.parse_detail(D.detail_page(slug, sidebar=True), slug)
    assert d['nid'] == str(D.nid(slug)) and d['signature'] == 'Demolov 01'


def test_parse_detail_sidebar_links_must_belong_to_the_book_and_be_one_viewer():
    slug = 'soap-pn/demolov-01'
    good = D.detail_page(slug, sidebar=True)
    foreign = good.replace('demolov-01_0010-x', 'jina-kniha_0010-x')
    other_nid = good.replace('/iipimage/%d/demolov-01_0020-n' % D.nid(slug), '/iipimage/777/demolov-01_0020-n')
    no_links = D.detail_page(slug, sidebar=True).replace('id="block-iipimage-base-iipimage-base-thumbnails"', 'id="other"')
    for bad in (foreign, other_nid, no_links):
        with pytest.raises(ResearchError) as e:
            P.parse_detail(bad, slug)
        assert e.value.code == 'PARSE_ERROR'
    # a foreign key in the article itself is refused as well
    with pytest.raises(ResearchError):
        P.parse_detail(D.detail_page(slug).replace('demolov-01_0020-n', 'x_0020-n', 1), slug)
