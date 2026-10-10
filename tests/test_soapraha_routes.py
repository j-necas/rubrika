"""SOA Praha (eBadatelna): what the allowlist lets through, and what it never does."""
import pytest

from rubrika import archives as A
from rubrika import sources as S
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

H = "https://ebadatelna.soapraha.cz"
SID = ";jsessionid=990A267905F7782830AADB3D081B6733"
SEARCH_AJAX = H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton"
NEXT_AJAX = H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-pagination-results-nextPageUrl"


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


@pytest.mark.parametrize("url,source,group", [
    (H + "/pages/SearchMatrikaPage", "soapraha", "soapraha_catalog"),
    (H + "/pages/SearchMatrikaPage?0", "soapraha", "soapraha_catalog"),
    (H + "/pages/SearchMatrikaPage" + SID + "?0", "soapraha", "soapraha_catalog"),
    (NEXT_AJAX, "soapraha", "soapraha_catalog"),
    (H + "/pages/MatrikaPage/matrikaId/6367", "soapraha", "soapraha_catalog"),
    (H + "/pages/MatrikaPage/matrikaId/6367?1", "soapraha", "soapraha_catalog"),
    (H + "/pages/MatrikaPage/matrikaId/6367" + SID + "?0", "soapraha", "soapraha_catalog"),
    (H + "/pages/DataMatrikaPage/idx/2/matrikaId/6367", "soapraha", "soapraha_catalog"),
    (H + "/d/6367/2", "soapraha", "soapraha_catalog"),
    (H + "/wicket/resource/org.apache.wicket.Application/Kladno_01__002.jpg", "soapraha_images", "soapraha_images"),
    (H + "/wicket/resource/org.apache.wicket.Application/Mlada_Boleslav-jb_02__1204.jpg", "soapraha_images", "soapraha_images"),
    (H + "/wicket/resource/org.apache.wicket.Application/Scitani_lidu_1869:_Okres_Slany_-_obec_Bdin,_cp._1-50__001.jpg", "soapraha_images", "soapraha_images"),
    (H + "/robots.txt", "robots", "soapraha_robots"),
    (H + "/pages/ErrorPage" + SID, "robots", "soapraha_robots"),
    (H + "/pages/ErrorPage" + SID + "?1", "robots", "soapraha_robots"),
])
def test_read_routes_allowed(url, source, group):
    r = route(url)
    assert (r.source, r.group, r.method, r.archive) == (source, group, "GET", "soapraha")


@pytest.mark.parametrize("url", [
    H + "/pages/SearchArchivaliePage", H + "/pages/SearchArchivaliePage?0", H + "/pages/SearchArchivaliePage" + SID + "?0",
    H + "/pages/SearchArchivaliePage?0-1.0-tableContainer-pagination-results-nextPageUrl",
    H + "/pages/ArchivaliePage/archivalieId/2305", H + "/pages/ArchivaliePage/archivalieId/2305?1",
    H + "/pages/ArchivaliePage/archivalieId/2305" + SID + "?0",
    H + "/pages/DataArchivaliePage/archivalieId/2305", H + "/pages/DataArchivaliePage/archivalieId/2305/idx/3",
    H + "/a/2305/1", H + "/a/2616/3",
])
def test_archival_records_read_routes(url):
    r = route(url)
    assert (r.source, r.group, r.method, r.archive) == ("soapraha_archivalie", "soapraha_catalog", "GET", "soapraha")


def test_archival_search_listener_is_a_post_of_its_own():
    for url in (H + "/pages/SearchArchivaliePage?0-1.0-searchForm-searchButton", H + "/pages/SearchArchivaliePage" + SID + "?0-1.0-searchForm-searchButton"):
        r = route(url, "POST")
        assert (r.source, r.group, r.method, r.archive) == ("soapraha_archivalie", "soapraha_catalog", "POST", "soapraha")


def test_the_search_button_listener_is_the_only_post():
    r = route(SEARCH_AJAX, "POST")
    assert (r.source, r.group, r.method, r.archive) == ("soapraha", "soapraha_catalog", "POST", "soapraha")
    assert route(H + "/pages/SearchMatrikaPage" + SID + "?0-1.0-searchForm-searchButton", "POST").method == "POST"


@pytest.mark.parametrize("url,method", [
    # login, registration, notes, ZIP of a whole book, archival records, other listeners and pages
    (H + "/pages/LoginPage", "GET"),
    (H + "/pages/RegistrationPage", "GET"),
    (H + "/pages/UserNoteMatrikaPage/matrikaId/6367", "GET"),
    (H + "/pages/PuvodcePage/matrikaId/6367", "GET"),
    (H + "/pages/PreviewDataMatrikaPage/matrikaId/6367", "GET"),
    (H + "/pages/SearchArchivaliePage?0-1.0-mainNavigation-loginLogoutPanel-loginPanel-login", "GET"),
    (H + "/pages/SearchArchivaliePage?0-1.0-searchForm-mistoUlozeni", "POST"),
    (H + "/pages/SearchArchivaliePage?0-1.0-searchForm-searchButton", "GET"),
    (H + "/pages/SearchArchivaliePage?0-1.0-tableContainer-sortByName", "GET"),
    (H + "/pages/SearchArchivaliePage?0-1.0-tableContainer-pagination-results-nextPageUrl", "POST"),
    (H + "/pages/SearchArchivaliePage", "POST"),
    (H + "/pages/UserNoteArchivaliePage/archivalieId/2305", "GET"),
    (H + "/pages/PreviewDataArchivaliePage/archivalieId/2305", "GET"),
    (H + "/pages/ArchivaliePage/archivalieId/2305", "POST"),
    (H + "/a/12/0", "GET"),
    (H + "/a/12/1?1--scanImage", "GET"),
    (H + "/a/12/1", "POST"),
    (H + "/a/x/1", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Scitani_lidu_1869:_Okres_X__001.zip", "GET"),
    (H + "/pages/DocumentsPage", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Kladno_01.zip", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Kladno_01__001.zip", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Digitalizovane%20matriky%20SOA%20Praha.pdf", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Kladno_01__001.jpg?download=1", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/Kladno_01__1.jpg", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/../Kladno_01__001.jpg", "GET"),
    (H + "/wicket/resource/org.apache.wicket.Application/a/b__001.jpg", "GET"),
    (H + "/wicket/resource/org.apache.wicket.resource.JQueryResourceReference/jquery/jquery-3.6.4.js", "GET"),
    (H + "/servlets/UpdateCustomPropertiesServlet?browserWidth=1", "GET"),
    # the other listeners of the search page (login, search type, sorting, page size, jump to a page) stay closed
    (H + "/pages/SearchMatrikaPage?0-1.0-mainNavigation-loginLogoutPanel-loginPanel-login", "GET"),
    (H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchType", "GET"),
    (H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchType", "POST"),
    (H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-sortByName", "GET"),
    (H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-resultsOnPagePanel-resultsOnPage", "POST"),
    (H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-pagination-results-pageDropdown", "POST"),
    (H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-pagination-results-lastPageUrl", "GET"),
    (H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchButtonFake", "POST"),
    (H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton", "GET"),
    (H + "/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton&x=1", "POST"),
    (H + "/pages/SearchMatrikaPage?1-2.0-tableContainer-pagination-results-nextPageUrl", "POST"),
    (H + "/pages/MatrikaPage/matrikaId/6367?1-2.0-tableContainer-pagination-results-nextPageUrl", "GET"),
    (H + "/pages/SearchMatrikaPage", "POST"),
    (H + "/pages/SearchMatrikaPage?0", "POST"),
    # POST anywhere else, other methods, other shapes
    (H + "/pages/MatrikaPage/matrikaId/6367", "POST"),
    (H + "/d/6367/2", "POST"),
    (H + "/d/6367/2", "DELETE"),
    (H + "/d/6367/0", "GET"),
    (H + "/d/6367/2?1--scanImage", "GET"),
    (H + "/d/abc/2", "GET"),
    (H + "/pages/DataMatrikaPage/idx/0/matrikaId/6367", "GET"),
    (H + "/pages/MatrikaPage/matrikaId/6367;x=1", "GET"),
    (H + "/pages/MatrikaPage/matrikaId/6367;jsessionid=abc", "GET"),
    (H + "/pages/MatrikaPage/matrikaId/6367?jsessionid=990A267905F7782830AADB3D081B6733", "GET"),
    (H + "/robots.txt?x=1", "GET"),
    (H + "/pages/ErrorPage?x=1", "GET"),
    ("http://ebadatelna.soapraha.cz/pages/SearchMatrikaPage", "GET"),
    ("https://ebadatelna.soapraha.cz.evil.example/pages/SearchMatrikaPage", "GET"),
    ("https://ebadatelna.soapraha.cz/pages/../admin", "GET"),
])
def test_everything_else_is_closed(url, method):
    with pytest.raises(ResearchError) as e:
        route(url, method)
    assert e.value.code == "INVALID_SOURCE"


GOOD = {"searchType": "1", "keywordBox:searchTokenIn": "radio0", "keywordBox:searchToken": "Kladno",
        "dateYearFrom": "1000", "dateYearTo": "2100"}


def test_search_form_accepts_the_exact_form():
    S.check_soapraha_search_form(GOOD)
    S.check_soapraha_search_form({**GOOD, "keywordBox:searchToken": "Mladá Boleslav", "dateYearFrom": "1785", "dateYearTo": "1830"})


@pytest.mark.parametrize("bad", [
    {"keywordBox:searchToken": "Kladno"},
    {**GOOD, "extra": "1"},
    {**GOOD, "searchType": "2"},
    {**GOOD, "keywordBox:searchTokenIn": "radio1"},
    {**GOOD, "keywordBox:searchToken": ""},
    {**GOOD, "keywordBox:searchToken": "   "},
    {**GOOD, "keywordBox:searchToken": "x" * 101},
    {**GOOD, "keywordBox:searchToken": "Klad\nno"},
    {**GOOD, "keywordBox:searchToken": "<script>"},
    {**GOOD, "dateYearFrom": "999"},
    {**GOOD, "dateYearTo": "2101"},
    {**GOOD, "dateYearFrom": "1900", "dateYearTo": "1800"},
    {**GOOD, "dateYearFrom": "abcd"},
    {**GOOD, "dateYearFrom": 1000},
    {k: GOOD[k] for k in reversed(list(GOOD))},
])
def test_search_form_refuses_anything_else(bad):
    with pytest.raises(ResearchError) as e:
        S.check_soapraha_search_form(bad)
    assert e.value.code == "INVALID_SOURCE"


ARCH = {"searchLocality": "Bakov - community Bakov - district Testov", "searchArchivalie": "", "typArchivalie": "19",
        "mistoUlozeni": "", "archivniFondId": "", "dateYearFrom": "1000", "dateYearTo": "2100"}


def test_archival_search_form_accepts_the_exact_form():
    S.check_soapraha_search_form(ARCH)
    S.check_soapraha_search_form({**ARCH, "typArchivalie": ""})
    S.check_soapraha_search_form({**ARCH, "searchLocality": "Mělník - community Mělník - district Mělník"})


@pytest.mark.parametrize("bad", [
    {**ARCH, "extra": "1"},
    {k: ARCH[k] for k in reversed(list(ARCH))},
    {k: v for k, v in ARCH.items() if k != "typArchivalie"},
    {**ARCH, "searchLocality": ""},
    {**ARCH, "searchLocality": "   "},
    {**ARCH, "searchLocality": "x" * 201},
    {**ARCH, "searchLocality": "Bak\nov"},
    {**ARCH, "searchLocality": "<script>"},
    {**ARCH, "searchArchivalie": "Urbář"},
    {**ARCH, "mistoUlozeni": "1"},
    {**ARCH, "archivniFondId": "748"},
    {**ARCH, "typArchivalie": "abc"},
    {**ARCH, "typArchivalie": "1234"},
    {**ARCH, "dateYearFrom": "999"},
    {**ARCH, "dateYearTo": "2101"},
    {**ARCH, "dateYearFrom": "1900", "dateYearTo": "1800"},
    {**ARCH, "dateYearFrom": 1000},
])
def test_archival_search_form_refuses_anything_else(bad):
    with pytest.raises(ResearchError) as e:
        S.check_soapraha_search_form(bad)
    assert e.value.code == "INVALID_SOURCE"
