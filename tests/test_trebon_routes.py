"""SOA Třeboň (DigiArchiv): what the allowlist lets through, and what it never does."""
import pytest

from rubrika import archives as A
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

H = "https://digi.ceskearchivy.cz"


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


@pytest.mark.parametrize("url,source,group", [
    (H + "/robots.txt", "robots", "trebon_robots"),
    (H + "/tree.php?menu=0&doctree=1nr&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nrc&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nrcl&id=2710&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nrcM&id=17107&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nC&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nb&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1nCb&id=17070&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1q&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1qa&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1qaI&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1qabl&id=700043&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1s&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1sec9c&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1t&lang=cs", "trebon", "trebon_catalog"),
    (H + "/tree.php?menu=0&doctree=1U&lang=cs", "trebon", "trebon_catalog"),
    (H + "/view.php?menu=3&id=2711&page=2", "trebon", "trebon_catalog"),
    (H + "/view.php?page=2&menu=3&id=2711", "trebon", "trebon_catalog"),
])
def test_read_routes_allowed(url, source, group):
    r = route(url)
    assert (r.source, r.group, r.method, r.archive) == (source, group, "GET", "trebon")


@pytest.mark.parametrize("url,method", [
    # the archive's search, help and OAI (robots.txt disallows /pages/* and /oai/*): never reachable
    (H + "/pages/search.php", "GET"),
    (H + "/pages/search.php?q=Tabor", "GET"),
    (H + "/pages/help.php?subject=2&menu=8", "GET"),
    (H + "/help/2", "GET"),
    (H + "/oai/", "GET"),
    (H + "/oai/?verb=ListRecords&metadataPrefix=oai_dc", "GET"),
    (H + "/oai/?verb=Identify", "GET"),
    # login, registration, export, bookmarks, notes, other entry points
    (H + "/login.php", "GET"),
    (H + "/pages/login.php", "GET"),
    (H + "/registrace.php", "GET"),
    (H + "/exportw.php?rot=&l=&r=&t=&b=&p=&q=&f=&jq=", "GET"),
    (H + "/notepad.php?u=2", "GET"),
    (H + "/DA?menu=3&id=2711", "GET"),
    (H + "/k/406747", "GET"),
    (H + "/description.php?id=406747&menu=4&l=1", "GET"),
    (H + "/2711/1", "GET"),
    (H + "/sitemap-index.xml", "GET"),
    (H + "/index.php", "GET"),
    # tree: other menus and doctrees, extra/missing/duplicate/odd parameters
    (H + "/tree.php?menu=0&doctree=10&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1n&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nB&lang=cs", "GET"),         # virtual tour
    (H + "/tree.php?menu=0&doctree=1ns&lang=cs", "GET"),         # citizens of the Third Reich: not read
    (H + "/tree.php?menu=0&doctree=1nC&id=5&lang=cs", "GET"),    # a root has no id
    (H + "/tree.php?menu=0&doctree=1nCb&lang=cs", "GET"),        # a congregation needs its id
    (H + "/tree.php?menu=0&doctree=1qabl&id=12345678&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1qabl&id=abc&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1q&id=5&lang=cs", "GET"),      # a branch node is not a leaf: no id
    (H + "/tree.php?menu=0&doctree=1qa&id=5&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1s&id=5&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1qabcdefghij&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1q&lang=de", "GET"),
    (H + "/tree.php?menu=0&doctree=1q&lang=cs&x=1", "GET"),
    (H + "/tree.php?menu=0&doctree=1p&lang=cs", "GET"),          # professional journals
    (H + "/tree.php?menu=0&doctree=1qa%0a&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrA&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrc", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrc&lang=de", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrc&lang=cs&x=1", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrc&id=5&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrcl&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrcl&id=abc&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrcl&id=12345678&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrcl&id=1&id=2&lang=cs", "GET"),
    (H + "/tree.php?menu=1&doctree=1nrc&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrc%0a&lang=cs", "GET"),
    (H + "/tree.php?menu=0&doctree=1nrcl_x&id=1&lang=cs", "GET"),
    (H + "/tree.php", "GET"),
    # viewer: other menus, missing or odd page numbers, extra parameters (position, zoom, export)
    (H + "/view.php?menu=3&id=2711", "GET"),
    (H + "/view.php?menu=3&id=2711&page=0", "GET"),
    (H + "/view.php?menu=3&id=2711&page=abc", "GET"),
    (H + "/view.php?menu=3&id=2711&page=123456", "GET"),
    (H + "/view.php?menu=2&id=2711&page=1", "GET"),
    (H + "/view.php?menu=3&id=2711&page=1&x=10&y=10&z=150", "GET"),
    (H + "/view.php?menu=3&id=2711&page=1&doctree=1nrcl", "GET"),
    (H + "/view.php?menu=3&id=2711&page=1&page=2", "GET"),
    # image CGI: closed altogether (limited mode: the archive allows its scans to be viewed in DigiArchiv only)
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.desc", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.3-5-6", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.12.4-13-27", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.thumb", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.desc&x=1", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data2/163220855.0.desc", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/../163220855.0.desc", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.3-5", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.3-5-6.jpg", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/1632.0.desc", "GET"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.1.desc", "GET"),
    (H + "/cgi-bin/isrv6.cgi", "GET"),
    (H + "/cgi-bin/other.cgi?data1/163220855.0.desc", "GET"),
    # other methods and hosts
    (H + "/view.php?menu=3&id=2711&page=1", "POST"),
    (H + "/tree.php?menu=0&doctree=1nrc&lang=cs", "POST"),
    (H + "/cgi-bin/isrv6.cgi?data1/163220855.0.desc", "DELETE"),
    (H + "/robots.txt", "POST"),
    (H + "/robots.txt?x=1", "GET"),
    ("http://digi.ceskearchivy.cz/view.php?menu=3&id=2711&page=1", "GET"),
    ("https://digi.ceskearchivy.cz.evil.example/view.php?menu=3&id=2711&page=1", "GET"),
    ("https://www.ceskearchivy.cz/robots.txt", "GET"),
    ("https://digi.ceskearchivy.cz/cgi-bin/../admin", "GET"),
])
def test_everything_else_is_closed(url, method):
    with pytest.raises(ResearchError) as e:
        route(url, method)
    assert e.value.code == "INVALID_SOURCE"
