"""Národní archiv (Archivní VadeMeCum): what the allowlist lets through, and what it never does."""
import pytest

from rubrika import archives as A
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

BASE = "https://vademecum.nacr.cz/vademecum/"
FUND = "f86bf41ed11ae5f01740720eed9e0dbb"
BOOK_XID = "371e8f54-f231-4150-bb25-dd1d9931bc90"
SCAN = "437287d91d165badd46333ca8cdcddbf"
VOLUME = "%28%5En%29%28%28%2820cb6cc9d41144e5874020c1e918c34f%29%28de1e5208398e4a7eb52df0f4687e7dd2%29%29%284137%29%29"
BOOK = "%28%5En%29%28%28%2820cb6cc9d41144e5874020c1e918c34f%29%28de1e5208398e4a7eb52df0f4687e7dd2%29%29%28292135%29%29"
TOKEN = "SyntheticSourcePage_" * 5  # synthetic; never reuse an archive session token
MEDIA_TOKEN = "SyntheticMediaToken_" * 5 + "%3D"  # synthetic; never reuse an archive session token
SID = ";jsessionid=" + "A" * 32  # synthetic; never reuse an archive session token


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


@pytest.mark.parametrize("url", [
    BASE + "permalink?xid=" + FUND,
    BASE + "permalink?xid=" + BOOK_XID,
    BASE + "permalink?xid=" + BOOK_XID + "&scan=" + SCAN,
    BASE + "permalink?xid=" + BOOK_XID + "&scan=" + SCAN + "&parentType=9019",
    BASE + "BookVolume.action?browseAid=&entity=" + VOLUME,
    BASE + "BookVolume.action" + SID + "?browseAid=&entity=" + VOLUME,
    BASE + "PaginatorBookVolume.action?alter=&_sourcePage=" + TOKEN + "&zanchor=%23bookResume2030&rowTxt=2030",
    BASE + "PaginatorBookVolume.action" + SID + "?alter=&_sourcePage=" + TOKEN + "&zanchor=%23bookResume2030&rowTxt=2030",
    BASE + "PaginatorBookVolume.action?_sourcePage=" + TOKEN + "&row=2040",
    BASE + "PaginatorMedia.action?_sourcePage=" + MEDIA_TOKEN + "&row=10",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=0",
    BASE + "Zoomify.action" + SID + "?entityRef=" + BOOK + "&scanIndex=0",
    "https://vademecum.nacr.cz/robots.txt",
])
def test_catalogue_routes_allowed(url):
    r = route(url)
    assert (r.archive, r.method) == ("na", "GET")
    assert r.group == ("na_robots" if url.endswith("robots.txt") else "na_catalog")


@pytest.mark.parametrize("url", [
    # the captcha behind "uložit" and the other Zoomify.action variants
    BASE + "Zoomify.action?captcha=&baseUrl=https://vademecum.nacr.cz/mrimage/vademecum/",
    BASE + "Zoomify.action?captcha=",
    BASE + "Zoomify.action?showOne=&_sourcePage=" + TOKEN + "&rowPg=0",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=0&captcha=",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=0&baseUrl=x",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=0&scanIndex=1",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=-1",
    BASE + "Zoomify.action?entityRef=" + BOOK,
    BASE + "Zoomify.action?entityRef=%28%5En%29%28%28%28aaaa%29%28bbbb%29%29%281%29%29&scanIndex=0",   # another fund
    # basket, search, registers of persons/places, login, downloads
    BASE + "BasketBean.action?addToBasket=&entityRef=" + BOOK,
    BASE + "BasketBean.action",
    BASE + "SearchBean.action?searchType=basic",
    BASE + "Rejstriky.action?_sourcePage=" + TOKEN + "&indexTypeKey=nastavRejstrikMistoId4Search",
    BASE + "AutocompleteBean.action?_eventName=getRejstrikMistoForAutocompleteByName&term=Plzen",
    BASE + "login.jsp",
    BASE + "index.jsp",
    BASE + "napoveda.jsp",
    BASE + "js/coreRejstriky.js",
    # the show link of a book row is not needed and stays closed
    BASE + "BookVolume.action?show=&_sourcePage=" + TOKEN + "&pagerCompStateId=PAGER_RESULT_BOOK&xid=" + BOOK_XID + "&entityType=9019&rowPg=1",
    # exact parameters only
    BASE + "BookVolume.action?browseAid=&entity=" + VOLUME + "&x=1",
    BASE + "BookVolume.action?browseAid=1&entity=" + VOLUME,
    BASE + "BookVolume.action?browseAid=&entity=" + BOOK,
    BASE + "BookVolume.action?entity=" + VOLUME,
    BASE + "BookVolume.action",
    BASE + "PaginatorBookVolume.action?alter=&_sourcePage=" + TOKEN + "&zanchor=%23bookResume2030",
    BASE + "PaginatorBookVolume.action?alter=x&_sourcePage=" + TOKEN + "&zanchor=%23bookResume2030&rowTxt=2030",
    BASE + "PaginatorBookVolume.action?alter=&_sourcePage=" + TOKEN + "&zanchor=%23other&rowTxt=2030",
    BASE + "PaginatorBookVolume.action?_sourcePage=" + TOKEN + "&row=-1",
    BASE + "PaginatorBookVolume.action?_sourcePage=" + TOKEN + "&row=1&row=2",
    BASE + "PaginatorBookVolume.action?_sourcePage=a%20b&row=1",
    BASE + "PaginatorBookVolume.action?row=1",
    BASE + "PaginatorMedia.action?_sourcePage=" + MEDIA_TOKEN + "&row=1&captcha=",
    BASE + "PaginatorMedia.action?_sourcePage=" + MEDIA_TOKEN,
    BASE + "PaginatorMedia.action?row=1",
    BASE + "permalink",
    BASE + "permalink?xid=xyz",
    BASE + "permalink?xid=" + BOOK_XID + "&scan=12",
    BASE + "permalink?xid=" + BOOK_XID + "&parentType=9019",
    BASE + "permalink?xid=" + BOOK_XID + "&scan=" + SCAN + "&parentType=x",
    BASE + "permalink?xid=" + BOOK_XID + "&scan=" + SCAN + "&scan=" + SCAN,
    BASE + "permalink?xid=" + BOOK_XID + "&captcha=1",
    # the session id belongs in the path only in the shape the application writes
    BASE + "BookVolume.action;jsessionid=abc?browseAid=&entity=" + VOLUME,
    BASE + "BookVolume.action;jsessionid=C51CC69080876C764AB0CE5866C1C37A;x=1?browseAid=&entity=" + VOLUME,
    BASE + "permalink;jsessionid=C51CC69080876C764AB0CE5866C1C37A?xid=" + FUND,
    BASE + "BookVolume.action?jsessionid=C51CC69080876C764AB0CE5866C1C37A&browseAid=&entity=" + VOLUME,
    "https://vademecum.nacr.cz/vademecum/../admin",
    "https://vademecum.nacr.cz/robots.txt?x=1",
    "http://vademecum.nacr.cz/vademecum/permalink?xid=" + FUND,
    "https://vademecum.nacr.cz.evil.example/vademecum/permalink?xid=" + FUND,
])
def test_catalogue_routes_forbidden(url):
    with pytest.raises(ResearchError) as e:
        route(url)
    assert e.value.code == "INVALID_SOURCE"


@pytest.mark.parametrize("url", [
    BASE + "permalink?xid=" + FUND,
    BASE + "BookVolume.action?browseAid=&entity=" + VOLUME,
    BASE + "PaginatorBookVolume.action?_sourcePage=" + TOKEN + "&row=2040",
    BASE + "PaginatorMedia.action?_sourcePage=" + MEDIA_TOKEN + "&row=10",
    BASE + "Zoomify.action?entityRef=" + BOOK + "&scanIndex=0",
])
def test_catalogue_is_read_only(url):
    for method in ("POST", "PUT", "DELETE"):
        with pytest.raises(ResearchError):
            route(url, method)


def test_images_and_robots_routes_are_unchanged():
    image = "https://vademecum.nacr.cz/mrimage/vademecum/zoomify//cz/archives/CZ-100000010/inventare/dao/images/0245/ca182acf-5e28-4abf-ab4e-ad7b06ec4e44.jpg/ImageProperties.xml"
    assert route(image).group == "na_images"
    thumb = image.replace("zoomify//", "image//").replace("ImageProperties.xml", "nahled_maly.jpg")
    with pytest.raises(ResearchError):
        route(thumb)
