"""SOA Praha (eBadatelna): conservative parsers on synthetic Wicket pages. Structure changes are PARSE_ERROR, never an empty result."""
import io
import re

import pytest
from PIL import Image

from rubrika import demo_soapraha as D
from rubrika import soapraha as S
from rubrika.catalog import relevance
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery


def results_xml(hits=None, page=0, version=1):
    hits = D.BOOKS if hits is None else hits
    return D.ajax_response({"id27": '<div id="id27" hidden="" data-wicket-placeholder=""></div>',
                            "id26": D.results_component(hits, page)}, version).encode()


def code(fn, *args):
    with pytest.raises(ResearchError) as e:
        fn(*args)
    return e.value.code


def test_parse_results_rows_columns_and_paging():
    r = S.parse_results(results_xml(page=1))
    assert (r.total, r.page, r.pages) == (len(D.BOOKS), 1, 4) and len(r.rows) == D.ROWS_PER_PAGE
    assert r.next_listener == "1-2.0-tableContainer-pagination-results-nextPageUrl"
    row = r.rows[0]
    assert (row.matrika_id, row.title, row.originator, row.district) == (7004, "Demolov 04", "Roman Catholic Church", "Demolov")
    assert row.localities == ["Demolov", "Testov"] and row.scans == 2 and row.years[0] == "1850 - 1899"


def test_parse_results_last_page_may_be_short_and_needs_no_next_listener():
    r = S.parse_results(results_xml(page=3))
    assert r.page == 3 and len(r.rows) == len(D.BOOKS) - 3 * D.ROWS_PER_PAGE


def test_parse_results_empty_result_is_a_message_not_an_error():
    xml = D.ajax_response({"id27": '<div id="id27">\nNo results found\n</div>',
                           "id26": '<div id="id26" hidden="" data-wicket-placeholder=""></div>'}, None).encode()
    r = S.parse_results(xml)
    assert r.rows == [] and r.total == 0 and r.message == "No results found"


@pytest.mark.parametrize("mutate", [
    lambda x: x.replace('class="header5"', 'class="header9"'),                       # columns moved
    lambda x: x.replace('class="searchResult matrikaResult"', 'class="other"'),        # table renamed
    lambda x: re.sub(r'href="/pages/MatrikaPage/matrikaId/(\d+)"', 'href="/somewhere/\\1"', x),
    lambda x: x.replace('/pages/DataMatrikaPage/idx/1/matrikaId/7004', '/pages/DataMatrikaPage/idx/1/matrikaId/7005'),
    lambda x: re.sub(r'\d+ registers found', 'registers', x),                       # no count
    lambda x: re.sub(r'<select class="pageDropdown" name="tableContainer:pagination:', '<select name="x:', x),
    lambda x: x.replace('<option selected="selected" value="1">', '<option value="1">'),   # no current page
    lambda x: x.replace('-tableContainer-pagination-results-nextPageUrl', '-other'),   # cannot go on
    lambda x: re.sub(r'<span>\d+</span>\n</td>\n\n</tr>', '<span>many</span>\n</td>\n\n</tr>', x, count=1),   # scans not a number
    lambda x: x.replace('<ajax-response>', '<ajax-response><redirect><![CDATA[x]]></redirect>'),
    lambda x: x.replace('ajax-response', 'html'),
    lambda x: '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]>' + x,
    lambda x: x[:200],                                                              # cut off
])
def test_parse_results_structure_changes_are_parse_errors(mutate):
    xml = mutate(results_xml(page=1).decode())
    assert code(S.parse_results, xml.encode()) in ("PARSE_ERROR", "SOURCE_UNAVAILABLE")
    if "redirect" not in xml:
        assert code(S.parse_results, xml.encode()) == "PARSE_ERROR"


def test_parse_results_unknown_message_is_not_an_empty_result():
    xml = D.ajax_response({"id27": '<div id="id27">Something went wrong</div>', "id26": '<div id="id26">also visible</div>'}, None).encode()
    assert code(S.parse_results, xml) == "PARSE_ERROR"
    xml = D.ajax_response({"id26": '<div id="id26" hidden="" data-wicket-placeholder=""></div>'}, None).encode()
    assert code(S.parse_results, xml) == "PARSE_ERROR"


def test_parse_results_redirect_means_expired_session():
    with pytest.raises(ResearchError) as e:
        S.parse_results(b'<ajax-response><redirect><![CDATA[./SearchMatrikaPage?3]]></redirect></ajax-response>')
    assert e.value.details["reason"] == "session_expired"


def test_locality_count_mismatch_is_a_warning():
    r = S.parse_results(results_xml().decode().replace('<td class="hidden">\n<span>2</span>', '<td class="hidden">\n<span>9</span>', 1).encode())
    assert any("nesouhlasí" in w for w in r.warnings)


def test_parse_search_form_reads_the_listener_and_base_from_the_page():
    form = S.parse_search_form(D.search_page(7, "A" * 32))
    assert form.listener == "7-1.0-searchForm-searchButton" and form.base_url == "pages/SearchMatrikaPage?7"


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace('name="keywordBox:searchToken"', 'name="term"'),
    lambda h: h.replace('value="radio0"', 'value="rb0"'),
    lambda h: h.replace('searchForm-searchButton"', 'searchForm-searchButtonFake2"', 1).replace('searchForm-searchButtonFake"', 'x"'),
    lambda h: h.replace('class="searchForm"', 'class="other"'),
    lambda h: h.replace('Wicket.Ajax.baseUrl="pages/SearchMatrikaPage?0"', 'Wicket.Ajax.baseUrl="elsewhere"'),
    lambda h: h + '<script>Wicket.Ajax.ajax({"u":"./SearchMatrikaPage?9-1.0-searchForm-searchButton"});</script>',   # two different buttons
])
def test_parse_search_form_structure_changes(mutate):
    assert code(S.parse_search_form, mutate(D.search_page(0, "A" * 32))) == "PARSE_ERROR"


def test_parse_search_form_expired_page():
    with pytest.raises(ResearchError) as e:
        S.parse_search_form("<html><body><h1>Page Expired</h1></body></html>")
    assert e.value.details["reason"] == "session_expired"


def test_year_cells_and_coverage():
    assert S.parse_year_cell("1663\xa0-\xa01676").status == "known"
    assert S.parse_year_cell("1871 - 1900, 1902").spans[1].start == 1902
    assert S.parse_year_cell("---").status == "absent" and S.parse_year_cell("").status == "unknown"
    assert S.parse_year_cell("1900 - ?").status == "uncertain"
    cov = S.coverage_of(["1785 - 1830", "---", "---", "---", "---", "---"])
    assert cov["birth"].records.status == "known" and cov["marriage"].records.status == "absent" and cov["death"].index.status == "absent"


def test_a_row_without_any_year_is_uncertain_everywhere():
    cov = S.coverage_of(["---"] * 6)
    assert all(c.records.status == c.index.status == "uncertain" for c in cov.values())
    with pytest.raises(ResearchError):
        S.coverage_of(["1800 - 1810"] * 5)


def test_relevance_never_excludes_on_uncertain_data():
    query = SearchQuery(raw_query="x", event_type="birth", year_from=1785, year_to=1790)
    rel = lambda years: relevance(S.SoaPrahaCatalog(None, None)._book(S.Row(1, "T", "RC", "d", ["A"], years, 2)), query)["status"]
    assert rel(["1785 - 1830", "---", "---", "---", "---", "---"]) == "candidate"
    assert rel(["---", "1785 - 1830", "---", "---", "---", "---"]) == "excluded"
    assert rel(["1850 - 1899", "---", "---", "---", "---", "---"]) == "excluded"
    assert rel(["---"] * 6) == "needs_review"
    assert rel(["1900 - ?", "---", "---", "---", "---", "---"]) == "needs_review"
    assert rel(["---", "---", "---", "1780 - 1800", "---", "---"]) == "candidate"          # the index counts


def localities():
    return S.parse_localities(D.search_page(0, "A" * 32))


def test_parse_localities_entries_ids_and_labels():
    locs = localities()
    assert len(locs) == sum(len(n) for _, _, n in D.LOCALITIES)
    assert len({loc.id for loc in locs}) == len(locs)                      # the id identifies one entry (name, community, district)
    demolov = next(loc for loc in locs if loc.name == "Demolov")
    assert demolov.id.startswith("soapraha:place:demolov-") and S.PLACE_ID.fullmatch(demolov.id)
    assert demolov.label == "Demolov, okres Demolov" and demolov.mates == ["Demolau", "Horní Demolov", "Ober-Demolau"]
    german = next(loc for loc in locs if loc.name == "Demolau")
    assert german.label == "Demolau, obec Demolov, okres Demolov"

    again = localities()
    assert [loc.id for loc in again] == [loc.id for loc in locs]


def test_parse_localities_merges_repeated_and_accent_only_entries():
    html = D.search_page(0, "A" * 32).replace('var availableTags1 = [', 'var availableTags1 = ["Dubí - community Dubí - district Kladno", "Dubi - community Dubí - district Kladno", "Dubí - community Dubí - district Kladno", ')
    locs = S.parse_localities(html)
    assert len([l for l in locs if l.community == "Dubí"]) == 1 and len({l.id for l in locs}) == len(locs)


def test_parse_localities_uses_the_words_the_page_defines():
    html = D.search_page(0, "A" * 32).replace('okresTerm = "district"', 'okresTerm = "okres"').replace('obecTerm = "community"', 'obecTerm = "obec"')
    html = html.replace(" - community ", " - obec ").replace(" - district ", " - okres ")
    assert [loc.name for loc in S.parse_localities(html)] == [loc.name for loc in localities()]


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("var availableTags1", "var otherTags"),
    lambda h: h.replace('var okresTerm = "district";', ""),
    lambda h: h.replace("Demolov - community Demolov - district Demolov", "Demolov"),
    lambda h: h.replace("var availableTags1 = [", "var availableTags1 = [1, "),
    lambda h: h.replace("var availableTags1 = [", "var availableTags1 = [broken, "),
    lambda h: re.sub(r"var availableTags1 = \[.*?\];", "var availableTags1 = [];", h, flags=re.S),
    lambda h: h + "var availableTags1 = [];",
])
def test_parse_localities_structure_changes(mutate):
    assert code(S.parse_localities, mutate(D.search_page(0, "A" * 32))) == "PARSE_ERROR"


def test_parse_localities_expired_page():
    with pytest.raises(ResearchError) as e:
        S.parse_localities("<html><body><h1>Page Expired</h1></body></html>")
    assert e.value.details["reason"] == "session_expired"


def test_match_localities_by_czech_or_german_name_whole_words_first():
    locs = localities()
    found, loose = S.match_localities(locs, "demolov")
    assert [l.name for l in found] == ["Demolov", "Horní Demolov"] and not loose
    assert [l.name for l in S.match_localities(locs, "Demolau")[0]] == ["Demolau", "Alt-Demolau", "Ober-Demolau"]
    assert [l.name for l in S.match_localities(locs, "Ober Demolau")[0]] == ["Ober-Demolau"]
    found, loose = S.match_localities(locs, "Demolo")
    assert loose and {l.name for l in found} >= {"Demolov", "Starý Demolovec"}
    assert S.match_localities(locs, "Nikde") == ([], False)
    same = S.match_localities(locs, "Malá Ves")[0]
    assert [l.district for l in same] == ["Kolonov", "Testov"] and len({l.id for l in same}) == 2


def test_split_locality_name_and_variants():
    assert S.split_locality("Újezd / Aujezd unter Kladno / Oujezd pod Kladnem, Újezd pod Kladnem") == \
        ("Újezd", ["Aujezd unter Kladno", "Oujezd pod Kladnem", "Újezd pod Kladnem"])
    assert S.split_locality("Kladno") == ("Kladno", [])


def test_parse_detail():
    d = S.parse_detail(D.detail_page(D.BY_ID[7001]), 7001)
    assert d["title"] == "Demolov 01" and d["scans"] == 3
    assert d["localities"] == ["Demolov / Demolau", "Horní Demolov / Ober-Demolau"]
    assert d["info"]["District"] == "Demolov" and d["info"]["Completeness"] == "complete" and "SYNTHETIC layout" in d["description"]


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("<h1>Demolov 01</h1>", ""),
    lambda h: h.replace("/pages/DataMatrikaPage/matrikaId/7001", "/pages/DataMatrikaPage/matrikaId/7002"),
    lambda h: h.replace('class="obecCastLabel"', 'class="x"'),
])
def test_parse_detail_structure_changes_and_other_book(mutate):
    assert code(S.parse_detail, mutate(D.detail_page(D.BY_ID[7001])), 7001) == "PARSE_ERROR"
    assert code(S.parse_detail, D.detail_page(D.BY_ID[7001]), 7002) == "PARSE_ERROR"


def test_parse_detail_without_scan_count_is_not_an_error():
    html = D.detail_page(D.BY_ID[7001]).replace("Number of scans", "Počet skenů")
    assert S.parse_detail(html, 7001)["scans"] is None


def test_parse_viewer():
    assert S.parse_viewer(D.viewer_page(D.BY_ID[7001], 2), 7001, 2, 3) == "Demolov_01__002.jpg"
    assert S.parse_viewer(D.viewer_page(D.BY_ID[7001], 2), 7001, 2, None) == "Demolov_01__002.jpg"


@pytest.mark.parametrize("mutate,expected", [
    (lambda h: h.replace("var idx = 1;", "var idx = 2;"), "PARSE_ERROR"),                                # another scan
    (lambda h: h.replace("Demolov_01__002.jpg", "Demolov_01__003.jpg"), "PARSE_ERROR"),
    (lambda h: h.replace("../../wicket/resource/org.apache.wicket.Application/", "/x/"), "PARSE_ERROR"),
    (lambda h: h.replace("/pages/DataMatrikaPage/matrikaId/7001", "/pages/DataMatrikaPage/matrikaId/7002"), "PARSE_ERROR"),
    (lambda h: h.replace("var pageCount = 3;", "var pageCount = 4;"), "SOURCE_CHANGED"),
    (lambda h: h.replace("var pageCount = 3;", ""), "PARSE_ERROR"),
    # a second download link inside the page (after </html> some lxml builds drop it, e.g. on Windows)
    (lambda h: h.replace("</body>", h[h.index("<a class=\"manipulationButton\""):h.index("</a>", h.index("<a class=\"manipulationButton\"")) + 4] + "</body>"), "PARSE_ERROR"),
])
def test_parse_viewer_mismatches(mutate, expected):
    with pytest.raises(ResearchError) as e:
        S.parse_viewer(mutate(D.viewer_page(D.BY_ID[7001], 2)), 7001, 2, 3)
    assert e.value.code == expected


def jpeg(size=(40, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, (200, 10, 10)).save(out, format="JPEG")
    return out.getvalue()


def test_check_jpeg():
    S.check_jpeg(jpeg(), 10_000)
    raw = jpeg()
    for bad in (b"<html>", raw[:len(raw) // 2], b"\xff\xd8\xff" + b"garbage" * 20):
        assert code(S.check_jpeg, bad, 10_000) == "INCOMPLETE_IMAGE"
    assert code(S.check_jpeg, jpeg((200, 200)), 1_000) == "INCOMPLETE_IMAGE"
    png = io.BytesIO(); Image.new("RGB", (4, 4)).save(png, format="PNG")
    assert code(S.check_jpeg, png.getvalue(), 10_000) == "INCOMPLETE_IMAGE"


def test_ids_urls_and_citation():
    assert S.book_id_of("soapraha:book:6367") == 6367
    for bad in ("soapraha:book:", "soapraha:book:1:2", "pf:book:1", "soapraha:book:x"):
        assert code(S.book_id_of, bad) == "INVALID_ARGUMENT"
    B = "https://ebadatelna.soapraha.cz"
    for url in (B + "/pages/MatrikaPage/matrikaId/6367", B + "/pages/MatrikaPage/matrikaId/6367;jsessionid=ABCDEFGH12345678?0",
                B + "/pages/DataMatrikaPage/idx/2/matrikaId/6367", B + "/pages/DataMatrikaPage/matrikaId/6367", B + "/d/6367/12"):
        assert S.book_id_from_url(url) == "soapraha:book:6367"
    for url in (B + "/pages/SearchMatrikaPage", B + "/a/x/1", "http://ebadatelna.soapraha.cz/d/1/1", "https://www.mza.cz/d/1/1",
                B + "/d/6367/1?x=1", B + "/pages/PuvodcePage/matrikaId/6367"):
        assert code(S.book_id_from_url, url) == "INVALID_SOURCE"
    assert S.citation_of("Kladno 01") == "Státní oblastní archiv v Praze, Sbírka matrik a průvodní listinný materiál, Středočeský kraj (NAD 28), Kladno 01"
    assert S.viewer_url(6367, 3) == B + "/d/6367/3" and S.detail_url(6367) == B + "/pages/MatrikaPage/matrikaId/6367"
    assert S.sort_key("Demolov 10") > S.sort_key("Demolov 9")
