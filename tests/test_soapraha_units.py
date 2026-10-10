"""SOA Praha archival records (census, land books): parsers on synthetic Wicket pages. A changed structure is PARSE_ERROR, never an empty result."""
import re

import pytest

from rubrika import demo_soapraha as D
from rubrika import soapraha as S
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.census import census_relevance, house_covered

SID = "A" * 32


def code(fn, *args, **kw):
    with pytest.raises(ResearchError) as e:
        fn(*args, **kw)
    return e.value.code


def results_xml(hits=None, page=0, version=0):
    hits = D.ARCHIVALIA if hits is None else hits
    return D.arch_ajax_response({"id24": '<div id="id24" hidden="" data-wicket-placeholder=""></div>',
                                 "id23": D.arch_results_component(hits, page)}, version).encode()


def test_parse_archive_search_form_reads_listener_words_and_types():
    form = S.parse_archive_search_form(D.arch_search_page(0, SID))
    assert form.listener == "0-1.0-searchForm-searchButton" and form.base_url == "pages/SearchArchivaliePage?0"
    assert (form.community_word, form.district_word) == ("community", "district")
    assert form.types["scitaci operaty"] == "19" and form.types["mesta a obce"] == "3"
    assert S.locality_entry(form, "Demolov", "Demolov", "Demolov") == "Demolov - community Demolov - district Demolov"


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace('name="searchLocality"', 'name="other"'),
    lambda h: h.replace('name="typArchivalie"', 'name="typ"'),
    lambda h: h.replace("searchForm-searchButton", "searchForm-searchButtonX", 1).replace("searchForm-searchButton\"", "searchForm-zzz\""),
    lambda h: h.replace('var obecTerm = "community";', ""),
    lambda h: h.replace('class="searchForm"', 'class="x"'),
    lambda h: h.replace('Wicket.Ajax.baseUrl="pages/SearchArchivaliePage?0";', ""),
])
def test_parse_archive_search_form_refuses_a_changed_page(mutate):
    assert code(S.parse_archive_search_form, mutate(D.arch_search_page(0, SID))) == "PARSE_ERROR"


def test_parse_archive_search_form_page_expired():
    assert code(S.parse_archive_search_form, "<html><h1>Page Expired</h1></html>") == "SOURCE_UNAVAILABLE"


def test_parse_archive_results_rows_and_paging():
    r = S.parse_archive_results(results_xml(page=1))
    assert (r.total, r.page, r.pages) == (len(D.ARCHIVALIA), 1, 6) and len(r.rows) == D.ROWS_PER_PAGE
    assert r.next_listener == "0-1.0-tableContainer-pagination-results-nextPageUrl"
    row = r.rows[0]
    assert (row.archivalie_id, row.title, row.unit_type, row.fund) == (8004, "Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50", "Sčítací operáty", "Okresní úřad Testov 1850-1945")
    assert row.localities == ["Demolov"] and row.scans == 2 and row.year == "1900" and row.span == "---"


def test_parse_archive_results_empty_is_a_message():
    xml = D.arch_ajax_response({"id24": '<div id="id24">\nNo results found\n</div>', "id23": '<div id="id23" hidden="" data-wicket-placeholder=""></div>'}).encode()
    r = S.parse_archive_results(xml)
    assert r.rows == [] and r.message == "No results found"


@pytest.mark.parametrize("mutate", [
    lambda x: x.replace('class="header5"', 'class="header9"'),
    lambda x: x.replace('class="searchResult archivalieResult"', 'class="other"'),
    lambda x: re.sub(r'href="/pages/ArchivaliePage/archivalieId/(\d+)"', 'href="/somewhere/\\1"', x),
    lambda x: x.replace('/pages/DataArchivaliePage/archivalieId/8001/idx/1', '/pages/DataArchivaliePage/archivalieId/8002/idx/1'),
    lambda x: re.sub(r'\d+ archival found', 'archival', x),
    lambda x: re.sub(r'<select class="pageDropdown" name="tableContainer:pagination:', '<select name="x:', x),
    lambda x: x.replace('-tableContainer-pagination-results-nextPageUrl', '-other'),
    lambda x: x.replace('<ajax-response>', '<ajax-response><redirect><![CDATA[x]]></redirect>'),
    lambda x: x.replace('ajax-response', 'html'),
    lambda x: '<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]>' + x,
])
def test_parse_archive_results_refuses_a_changed_answer(mutate):
    xml = results_xml(D.ARCHIVALIA[:4]).decode()
    assert code(S.parse_archive_results, mutate(xml).encode()) in ("PARSE_ERROR", "SOURCE_UNAVAILABLE")


def test_parse_archive_results_scans_must_be_a_number():
    xml = results_xml(D.ARCHIVALIA[:2]).decode()
    xml = re.sub(r'<span>3</span>\n</td>\n\n</tr>', '<span>many</span>\n</td>\n\n</tr>', xml, count=1)
    assert code(S.parse_archive_results, xml.encode()) == "PARSE_ERROR"


def test_census_title_year_district_municipality_part_and_house_numbers():
    t = S.parse_census_title("Sčítání lidu 1869: Okres Slaný - obec Hnidousy - osada Motyčín, čp. 51-80")
    assert (t["year"], t["district"], t["municipality"], t["part"]) == (1869, "Slaný", "Hnidousy", "Motyčín")
    assert (t["house_numbers"].start, t["house_numbers"].end, t["house_numbers"].parsed) == (51, 80, True)
    assert S.parse_census_title("Sčítání lidu 1900: Okres Mělník - obec Mělník-předměstí, čp. 116-130")["municipality"] == "Mělník-předměstí"
    whole = S.parse_census_title("Sčítání lidu 1869: Okres Slaný - obec Bdín")
    assert whole["house_numbers"].parsed is False and whole["house_numbers"].numbers == [] and whole["part"] is None
    multi = S.parse_census_title("Sčítání lidu 1910: Okres Testov - obec Demolov, čp. 1-50 a 60-70")["house_numbers"]
    assert multi.parsed and 55 not in multi.numbers and 65 in multi.numbers
    odd = S.parse_census_title("Něco jiného")
    assert odd["year"] is None and odd["house_numbers"].parsed is False


def test_house_number_coverage_from_title_never_excludes_an_unranged_set():
    ranged = S.parse_census_title("Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50")["house_numbers"]
    whole = S.parse_census_title("Sčítání lidu 1869: Okres Testov - obec Demolov")["house_numbers"]
    assert house_covered(ranged, "12") is True and house_covered(ranged, "51") is False and house_covered(whole, "51") is None


@pytest.mark.parametrize("title,category", [
    ("Bakov: Pozemková kniha města 1619-1818", "land_book"), ("X: Kniha gruntovní dominikální", "land_book"),
    ("X: Purkrechtní kniha", "land_book"), ("X: Pozemková kniha emfyteutická", "land_book"),
    ("X: Urbář 1669", "other"), ("X: Kronika města", "other"), ("X: Soupis poddaných 1806", "other"),
])
def test_land_category(title, category):
    assert S.land_category(title) == category


def row(**kw):
    base = dict(archivalie_id=1, title="t", unit_type="x", fund="f", inventory="", signature="", localities=["a"], span="---", year="---", exact="---", original="", scans=1)
    return S.ArchiveRow(**{**base, **kw})


def test_unit_dating_range_then_year_then_exact_date():
    assert [(x.start, x.end) for x in S.unit_dating(row(span="1619 - 1818")).spans] == [(1619, 1818)]
    assert [(x.start, x.end) for x in S.unit_dating(row(year="1669")).spans] == [(1669, 1669)]
    assert [(x.start, x.end) for x in S.unit_dating(row(exact="1629-05-01")).spans] == [(1629, 1629)]
    assert S.unit_dating(row()).status == "unknown"
    assert S.unit_dating(row(span="asi 1700")).status == "uncertain"
    assert S.unit_dating(row(exact="někdy v létě")).status == "uncertain"


def test_unit_detail_parse_and_mismatch():
    d = S.parse_unit_detail(D.arch_detail_page(D.ARCH_BY_ID[8101]), 8101)
    assert d["title"] == "Demolov: Pozemková kniha města 1700-1780" and d["scans"] == 3 and d["localities"] == ["Demolov"]
    assert d["info"]["Storage location"] == "Státní okresní archiv Testov" and d["info"]["Signature"] == "OS Testov 100"
    assert d["description"].startswith("SYNTHETIC description")
    assert code(S.parse_unit_detail, D.arch_detail_page(D.ARCH_BY_ID[8101]), 8102) == "PARSE_ERROR"
    assert code(S.parse_unit_detail, D.arch_detail_page(D.ARCH_BY_ID[8101]).replace("<div>Scans</div>", "<div>Pages</div>"), 8101) == "PARSE_ERROR"
    assert code(S.parse_unit_detail, "<html>Page Expired</html>", 8101) == "SOURCE_UNAVAILABLE"


def test_unit_viewer_parse_uses_the_archivalie_urls_and_title_named_files():
    html = D.arch_viewer_page(D.ARCH_BY_ID[8002], 2)
    name = S.parse_viewer(html, 8002, 2, 2, unit=True)
    assert name == "Scitani_lidu_1880:_Okres_Testov_-_obec_Demolov,_cp._1-50__002.jpg"
    assert code(S.parse_viewer, html, 8002, 2, 2) == "PARSE_ERROR"                       # a register viewer would read a different base address
    assert code(S.parse_viewer, html, 8003, 2, 2, unit=True) == "PARSE_ERROR"
    assert code(S.parse_viewer, html, 8002, 1, 2, unit=True) == "PARSE_ERROR"
    assert code(S.parse_viewer, html, 8002, 2, 5, unit=True) == "SOURCE_CHANGED"


def test_unit_ids_urls_and_citation():
    assert S.unit_id_of("soapraha:unit:2305") == 2305 and S.ident_of("soapraha:unit:5") == ("unit", 5) and S.ident_of("soapraha:book:5") == ("book", 5)
    for bad in ("soapraha:unit:", "soapraha:unit:1:2", "soapraha:book:1", "soapraha:unit:x"):
        assert code(S.unit_id_of, bad) == "INVALID_ARGUMENT"
    B = "https://ebadatelna.soapraha.cz"
    for url in (B + "/pages/ArchivaliePage/archivalieId/2305", B + "/pages/ArchivaliePage/archivalieId/2305;jsessionid=ABCDEFGH12345678?0",
                B + "/pages/DataArchivaliePage/archivalieId/2305/idx/4", B + "/pages/DataArchivaliePage/archivalieId/2305", B + "/a/2305/12"):
        assert S.book_id_from_url(url) == "soapraha:unit:2305"
    for url in (B + "/a/2305/1?x=1", B + "/a/x/1", B + "/pages/UserNoteArchivaliePage/archivalieId/2305", B + "/pages/SearchArchivaliePage"):
        assert code(S.book_id_from_url, url) == "INVALID_SOURCE"
    assert S.unit_viewer_url(2305, 3) == B + "/a/2305/3" and S.unit_detail_url(2305) == B + "/pages/ArchivaliePage/archivalieId/2305"
    assert S.unit_citation("Bakov: Pozemková kniha města 1619-1818", "Archiv města Bakov", "OS Testov 3388") == \
        "Státní oblastní archiv v Praze, fond Archiv města Bakov, sign. OS Testov 3388, Bakov: Pozemková kniha města 1619-1818"
    assert S.unit_citation("T", "F", "---", "23", "Státní okresní archiv Testov") == "Státní okresní archiv Testov (eBadatelna SOA Praha), fond F, inv. č. 23, T"
    assert S.manifest_hash(5, 3, unit=True) != S.manifest_hash(5, 3)


def test_census_relevance_on_parsed_unit():
    catalog = S.SoaPrahaCatalog(None, None)
    unit = catalog._unit(S.ArchiveRow(8004, "Sčítání lidu 1900: Okres Testov - obec Demolov, čp. 1-50", "Sčítací operáty", "F", "", "", ["Demolov"], "---", "1900", "---", "", 2), "census")
    assert (unit.year, unit.category, unit.image_count, unit.source_id) == (1900, "other", 2, "soapraha:unit:8004")
    q = SearchQuery.model_validate({"raw_query": "x", "year_from": 1895, "year_to": 1905, "person": {"house_number": "12"}})
    rel = census_relevance(unit, q)
    assert rel["status"] == "candidate" and rel["house_number_covered"] is True


@pytest.mark.parametrize("tail", [", č.p. 1-50", ", čp 1-50", ", čp. 1-50", ",čp.1-50"])
def test_census_title_accepts_house_label_variants(tail):
    t = S.parse_census_title("Sčítání lidu 1900: Okres X - obec Y" + tail)
    assert t["municipality"] == "Y" and (t["house_numbers"].start, t["house_numbers"].end, t["house_numbers"].parsed) == (1, 50, True)


def test_house_numbers_are_read_only_when_the_title_has_the_expected_head():
    t = S.parse_census_title("Něco jiného, čp. 1-50")
    assert t["year"] is None and t["house_numbers"].parsed is False and t["house_numbers"].numbers == []


def test_kind_of_type():
    assert S.kind_of_type("Sčítací operáty") == "census" and S.kind_of_type("Velkostatky") == "land_records"
    assert S.kind_of_type("Fotografie") is None and S.kind_of_type(None) is None and S.kind_of_type("") is None
