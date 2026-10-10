"""Police registration forms of Prague (digi.nacr.cz, plain HTTP): what the allowlist lets through, and what it never does."""
import pytest

from rubrika import archives as A
from rubrika import sources as S
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

H = "http://digi.nacr.cz"
APP = H + "/prihlasky2/"
TOKEN = "a" * 64  # synthetic; never reuse an archive session token
REF = "czarch:CZ-100000010:874"


def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


@pytest.mark.parametrize("url,source,group", [
    (APP, "prihlasky", "prihlasky_catalog"),
    (H + "/prihlasky2/index.php", "prihlasky", "prihlasky_catalog"),
    (APP + "?session=" + TOKEN, "prihlasky", "prihlasky_catalog"),
    (APP + "?session=" + TOKEN + "&action=list&od=20", "prihlasky", "prihlasky_catalog"),
    (APP + "?session=" + TOKEN + "&action=image&record=3", "prihlasky", "prihlasky_catalog"),
    (APP + "index.php?action=link&ref=" + REF + "&karton=103&folium=220", "prihlasky", "prihlasky_catalog"),
    (APP + "?action=link&ref=" + REF + "&karton=426&folium=707", "prihlasky", "prihlasky_catalog"),
    (APP + "images/00000103-00000220.jpg", "prihlasky_images", "prihlasky_images"),
    (H + "/robots.txt", "robots", "prihlasky_robots"),
])
def test_read_routes_allowed(url, source, group):
    r = route(url)
    assert (r.source, r.group, r.method, r.archive) == (source, group, "GET", "na_prihlasky")


def test_the_search_form_is_the_only_post():
    for url in (APP + "?session=" + TOKEN, H + "/prihlasky2/index.php?session=" + TOKEN):
        r = route(url, "POST")
        assert (r.source, r.group, r.method, r.archive) == ("prihlasky", "prihlasky_catalog", "POST", "na_prihlasky")


@pytest.mark.parametrize("url,method", [
    # simple search, detailed search, new query, ordering, language, info page, PDFs, images of the site
    (APP + "?session=" + TOKEN + "&action=rozs_hledani", "GET"),
    (APP + "?session=" + TOKEN + "&action=novy_dotaz", "GET"),
    (APP + "?session=" + TOKEN + "&action=uvod", "GET"),
    (APP + "?session=" + TOKEN + "&action=list&razeni=rok", "GET"),
    (APP + "?session=" + TOKEN + "&jazyk=en", "GET"),
    (APP + "files/konskripce_navod_V02.pdf", "GET"),
    (APP + "img/logo_NAm.jpg", "GET"),
    (APP + "konskripce2_styly.css", "GET"),
    # malformed or extra parameters
    (APP + "?session=abc", "GET"),
    (APP + "?session=" + TOKEN.upper(), "GET"),
    (APP + "?session=" + TOKEN + "&action=list", "GET"),
    (APP + "?session=" + TOKEN + "&action=list&od=x", "GET"),
    (APP + "?session=" + TOKEN + "&action=list&od=20&x=1", "GET"),
    (APP + "?session=" + TOKEN + "&action=image&record=", "GET"),
    (APP + "?session=" + TOKEN + "&action=image&record=1&record=2", "GET"),
    (APP + "?session=" + TOKEN + "&action=delete&record=1", "GET"),
    (APP + "?action=link&ref=czarch:CZ-999:1&karton=1&folium=1", "GET"),
    (APP + "?action=link&ref=" + REF + "&karton=x&folium=1", "GET"),
    (APP + "?action=link&ref=" + REF + "&karton=1", "GET"),
    (APP + "?action=link&ref=" + REF + "&karton=1&folium=1&session=" + TOKEN, "GET"),
    (APP + "?x=1", "GET"),
    # images: only the 8+8 digit JPEG names, without a query
    (APP + "images/103-220.jpg", "GET"),
    (APP + "images/00000103-00000220.jpg?x=1", "GET"),
    (APP + "images/00000103-00000220.png", "GET"),
    (APP + "images/../index.php", "GET"),
    (APP + "images/00000103/00000220.jpg", "GET"),
    # POST anywhere else, other methods
    (APP + "images/00000103-00000220.jpg", "POST"),
    (APP + "?session=" + TOKEN + "&action=list&od=20", "POST"),
    (APP + "index.php?action=link&ref=" + REF + "&karton=103&folium=220", "POST"),
    (APP, "POST"),
    (APP + "?session=" + TOKEN + "&x=1", "POST"),
    (APP + "?session=" + TOKEN, "DELETE"),
    (H + "/robots.txt?x=1", "GET"),
    (H + "/robots.txt", "POST"),
    # other paths and hosts; TLS is not offered by this host, https is refused too
    (H + "/prihlasky/", "GET"),
    (H + "/", "GET"),
    (H + "/admin", "GET"),
    (H + "/prihlasky2/../admin", "GET"),
    ("https://digi.nacr.cz/prihlasky2/", "GET"),
    ("http://digi.nacr.cz.evil.example/prihlasky2/", "GET"),
    ("http://digi.nacr.cz:8080/prihlasky2/", "GET"),
    ("http://user:pw@digi.nacr.cz/prihlasky2/", "GET"),
])
def test_everything_else_is_closed(url, method):
    with pytest.raises(ResearchError) as e:
        route(url, method)
    assert e.value.code == "INVALID_SOURCE"


def test_plain_http_is_allowed_for_this_one_host_only():
    hosts = [h for a in A.ARCHIVES.values() if a.allow_plain_http for h in a.hosts]
    assert hosts == ["digi.nacr.cz"]
    policy = SourcePolicy(A.integrated_hosts())
    for h in A.integrated_hosts():
        if h == "digi.nacr.cz":
            continue
        with pytest.raises(ResearchError) as e:
            policy.validate(f"http://{h}/robots.txt")
        assert e.value.code == "INVALID_SOURCE"


GOOD = {"prijmeni": "Dvořák", "jmeno": "Jan", "odroku": "1870", "doroku": "1875", "action": "hledani2", "tlacitko": "Najít"}


def test_search_form_accepts_the_exact_form():
    S.check_prihlasky_search_form(GOOD)
    S.check_prihlasky_search_form({**GOOD, "jmeno": "", "odroku": "", "doroku": ""})
    S.check_prihlasky_search_form({**GOOD, "prijmeni": "Novák-Šletina"})


@pytest.mark.parametrize("bad", [
    {"prijmeni": "Dvořák"},
    {**GOOD, "extra": "1"},
    {**GOOD, "action": "hledani1"},
    {**GOOD, "action": "rozs_hledani"},
    {**GOOD, "tlacitko": "Smazat"},
    {**GOOD, "prijmeni": ""},
    {**GOOD, "prijmeni": "   "},
    {**GOOD, "prijmeni": "x" * 61},
    {**GOOD, "jmeno": "x" * 61},
    {**GOOD, "prijmeni": "Dvo\nrák"},
    {**GOOD, "jmeno": "<b>"},
    {**GOOD, "odroku": "1699"},
    {**GOOD, "doroku": "1931"},
    {**GOOD, "odroku": "1880", "doroku": "1870"},
    {**GOOD, "odroku": "abcd"},
    {**GOOD, "odroku": 1870},
    {**GOOD, "odroku": "1870", "doroku": ""},
    {k: GOOD[k] for k in reversed(list(GOOD))},
])
def test_search_form_refuses_anything_else(bad):
    with pytest.raises(ResearchError) as e:
        S.check_prihlasky_search_form(bad)
    assert e.value.code == "INVALID_SOURCE"
