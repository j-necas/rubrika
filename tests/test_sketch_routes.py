"""MZA indication sketch routes: GET only, exact paths and parameters, two throttling groups; no other mza.cz path opens up."""
import pytest

from rubrika import archives as A
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

APP = "https://www.mza.cz/indikacniskici"
IMG = APP + "/data/obrazky/MOR113918250/Img/"


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


@pytest.mark.parametrize("url", [
    APP + "/skica?nazev=Brno",
    APP + "/skica?nazev=Brno&okres=Brno-m%C4%9Bsto",
    APP + "/skica?nazev=Bab&okres=&signatura=&page=2",
    APP + "/skica?signatura=1139",
    APP + "/skica?nazev=Kreutzgasse",
    APP + "/skica?nazev=%C5%BD%C4%8F%C3%A1r+nad",
    APP + "/skica/detail/MOR113918250",
    APP + "/skica/detail/1632",
    APP + "/skica/detail/1632?site=BACK",
    APP + "/skica/detail/1632?site=FRONT",
])
def test_catalogue_routes_are_allowed(url):
    r = route(url)
    assert (r.archive, r.source, r.group, r.method) == ("mza", "sketches", "mza_sketch_catalog", "GET")


@pytest.mark.parametrize("path", [
    "map.jpg", "Preview/A01A.jpg", "little/A01A-A1.jpg", "Small/A01A-A1-A1.jpg", "Normal/A01A-A1-A1-A1.jpg",
    "Normal/A03D-C3-B2-B2.jpg", "Preview/R01A.jpg", "Normal/R12L-B3-A2-B1.jpg",
])
def test_tile_routes_are_allowed(path):
    r = route(IMG + path)
    assert (r.archive, r.source, r.group) == ("mza", "sketches", "mza_sketch_images")


@pytest.mark.parametrize("url", [
    APP + "/skica",                                            # the whole list
    APP + "/skica?okres=Brno",                                 # no name or signature
    APP + "/skica?nazev=",
    APP + "/skica?nazev=Brno&x=1",
    APP + "/skica?nazev=Brno&nazev=Praha",
    APP + "/skica?nazev=Brno&page=0",
    APP + "/skica?nazev=Brno&page=abc",
    APP + "/skica?signatura=12ab",
    APP + "/skica?signatura=123456",
    APP + "/skica?nazev=" + "a" * 101,
    APP + "/skica?nazev=%3Cscript%3E",
    APP + "/skica?letter=A",
    APP + "/skica/detail/MOR113918250?site=BACK",             # the MOR id takes no parameter
    APP + "/skica/detail/1632?site=BACK&mode=ORDERING",       # ordering mode
    APP + "/skica/detail/1632?mode=ORDERING",
    APP + "/skica/detail/1632?site=TOP",
    APP + "/skica/detail/abc",
    APP + "/skica/detail/MOR1",
    APP + "/skica/detail/123456789",
    APP + "/skica/detail/1632/extra",
    APP + "/objednavka",                                      # basket
    APP + "/ajax/skica/autocomplete_nazev?term=Br",
    APP + "/page/view/podminky-uziti-sluzeb",
    APP + "/assets/js/application.js",
    APP + "/test/tile?level=1&x=0&y=0",
    IMG + "Preview/A01A.jpg?x=1",
    IMG + "Preview/a01a.jpg",
    IMG + "Preview/A01A.png",
    IMG + "Preview/A1A.jpg",
    IMG + "Normal/A01A-D1-A1-A1.jpg",                          # level-2 letters go A-C
    IMG + "Normal/A01A-A4-A1-A1.jpg",
    IMG + "Normal/A01A-A1-C1-A1.jpg",
    IMG + "Normal/A01A-A1-A1.jpg",                             # wrong depth for the folder
    IMG + "Small/A01A-A1-A1-A1.jpg",
    IMG + "other/A01A.jpg",
    APP + "/data/obrazky/MOR113918250/Img/../Img/map.jpg",
    APP + "/data/obrazky/MOR113918250/map.jpg",
    APP + "/data/obrazky/1632/Img/map.jpg",
    APP + "/data/objednavky/MOR113918250.zip",
])
def test_everything_else_under_the_application_is_refused(url):
    with pytest.raises(ResearchError) as e:
        route(url)
    assert e.value.code == "INVALID_SOURCE"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD"])
def test_sketch_paths_are_read_only(method):
    for url in (APP + "/skica?nazev=Brno", APP + "/skica/detail/MOR113918250", IMG + "map.jpg"):
        with pytest.raises(ResearchError):
            route(url, method)


def test_other_mza_paths_stay_as_they_were():
    assert route("https://www.mza.cz/robots.txt").group == "robots"
    assert route("https://www.mza.cz/actapublica/matrika/hledani?x=1").source == "acta_publica"
    with pytest.raises(ResearchError):
        route("https://www.mza.cz/indikacniskici")            # only the listed paths
    with pytest.raises(ResearchError):
        route("https://iipserver.mza.cz/indikacniskici/skica?nazev=Brno")
    with pytest.raises(ResearchError):
        route("https://www.mza.cz/wp-admin/")


@pytest.mark.parametrize("url", [
    "https://ags.cuzk.gov.cz/archiv/",
    "https://ags.cuzk.gov.cz/arcgis4/rest/services/Archiv/archiv_spojene_cisarske_otisky/MapServer?f=json",
    "https://ags.cuzk.gov.cz/arcgis4/rest/services/GenerateToken",
    "https://ags.cuzk.cz/archiv/?xy=-599625,-1160181",
    "https://geoportal.cuzk.gov.cz/Default.aspx",
])
def test_cuzk_is_a_link_never_a_source(url):
    from urllib.parse import urlsplit
    assert urlsplit(url).hostname not in A.integrated_hosts()
    with pytest.raises(ResearchError) as e:
        route(url)
    assert e.value.code == "INVALID_SOURCE"
    assert A.archive_for_host(urlsplit(url).hostname) is None
