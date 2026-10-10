"""Plain HTTP for the one host without TLS, and the token-in-the-address session layer used by the police registration forms."""
import httpx
import pytest

from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.network import SafeSource
from rubrika.storage import Store

H = "http://digi.nacr.cz"
APP = H + "/prihlasky2/"
TOKEN = "a" * 64
FORM = {"prijmeni": "Dvořák", "jmeno": "Jan", "odroku": "1870", "doroku": "1875", "action": "hledani2", "tlacitko": "Najít"}
ROBOTS = httpx.Response(200, text="User-Agent: *\nDisallow: /\n", headers={"content-type": "text/plain"})


def make(tmp_path, handler, **settings):
    cfg = Settings(_env_file=None, data_dir=tmp_path, **{"live_enabled": True, "prihlasky_enabled": True, "request_interval": 0, **settings})
    store = Store(cfg)
    source = SafeSource(cfg, store, transport=httpx.MockTransport(handler))
    source._interval = lambda host, enforce=True: 0
    sid = store.create("alice", SearchQuery(raw_query="SYNTHETIC"))["research_id"]
    return source, store, sid


def page(request):
    if request.url.path == "/robots.txt":
        return ROBOTS
    return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html; charset=UTF-8"})


@pytest.mark.asyncio
async def test_plain_http_get_is_sent_uncached_and_robots_disallow_is_tolerated_and_recorded(tmp_path):
    seen = []
    def handler(request):
        seen.append((request.method, str(request.url), request.headers.get("cookie")))
        return page(request)
    source, store, sid = make(tmp_path, handler)
    url = APP + "?session=" + TOKEN + "&action=list&od=20"
    assert await source.state_get(sid, "alice", url) == b"<html>ok</html>"
    assert await source.state_get(sid, "alice", url) == b"<html>ok</html>"
    assert [s[0:2] for s in seen] == [("GET", H + "/robots.txt"), ("GET", url), ("GET", url)]        # the page itself is never cached
    assert all(s[2] is None for s in seen) and all(s[1].startswith("http://") for s in seen)
    assert [r["id"] for r in store.links(sid, "alice", "robots_tolerated")] == ["digi.nacr.cz"]
    assert source.robots_disallowed(url)
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_https_and_other_hosts_are_refused_before_any_request(tmp_path):
    seen = []
    source, store, sid = make(tmp_path, lambda r: seen.append(r) or page(r))
    for url in ("https://digi.nacr.cz/prihlasky2/", "http://vademecum.nacr.cz/vademecum/permalink?xid=" + "a" * 32,
                "http://www.mza.cz/robots.txt", "http://ebadatelna.soapraha.cz/pages/SearchMatrikaPage"):
        with pytest.raises(ResearchError) as e:
            await source.state_get(sid, "alice", url)
        assert e.value.code == "INVALID_SOURCE"
    assert seen == []
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_post_goes_through_the_form_check_and_is_never_cached(tmp_path):
    seen = []
    def handler(request):
        seen.append((request.method, str(request.url), request.content))
        return page(request)
    source, store, sid = make(tmp_path, handler)
    url = APP + "?session=" + TOKEN
    await source.state_post(sid, "alice", url, FORM)
    await source.state_post(sid, "alice", url, FORM)
    posts = [s for s in seen if s[0] == "POST"]
    assert len(posts) == 2 and posts[0][2] == b"prijmeni=Dvo%C5%99%C3%A1k&jmeno=Jan&odroku=1870&doroku=1875&action=hledani2&tlacitko=Naj%C3%ADt"
    before = len(seen)
    with pytest.raises(ResearchError) as e:
        await source.state_post(sid, "alice", url, {**FORM, "action": "hledani1"})
    assert e.value.code == "INVALID_SOURCE" and len(seen) == before
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_a_redirect_is_an_error_and_the_switch_and_live_flag_are_checked(tmp_path):
    def handler(request):
        if request.url.path == "/robots.txt":
            return ROBOTS
        return httpx.Response(302, headers={"location": "http://digi.nacr.cz/prihlasky2/"})
    source, store, sid = make(tmp_path, handler)
    with pytest.raises(ResearchError) as e:
        await source.state_get(sid, "alice", APP)
    assert e.value.code == "SOURCE_UNAVAILABLE"
    off, ostore, osid = make(tmp_path / "off", page, prihlasky_enabled=False)
    with pytest.raises(ResearchError) as e:
        await off.state_get(osid, "alice", APP)
    assert e.value.code == "LIVE_DISABLED"
    dead, dstore, dsid = make(tmp_path / "dead", page, live_enabled=False)
    with pytest.raises(ResearchError) as e:
        await dead.state_get(dsid, "alice", APP)
    assert e.value.code == "LIVE_DISABLED"
    for s, st in ((source, store), (off, ostore), (dead, dstore)):
        await s.close(); st.close()


@pytest.mark.asyncio
async def test_the_whole_scan_is_fetched_through_the_cache(tmp_path):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return ROBOTS
        return httpx.Response(200, content=b"\xff\xd8\xff" + b"0" * 20, headers={"content-type": "image/jpeg"})
    source, store, sid = make(tmp_path, handler)
    url = APP + "images/00000103-00000220.jpg"
    assert (await source.fetch(sid, "alice", url, kind="scan"))[:3] == b"\xff\xd8\xff"
    await source.fetch(sid, "alice", url, kind="scan")
    assert seen.count(url) == 1
    await source.close(); store.close()


def test_robots_of_a_plain_http_host_are_keyed_by_its_own_scheme():
    from rubrika.network import robots_root
    assert robots_root("digi.nacr.cz") == "http://digi.nacr.cz"
    assert robots_root("www.mza.cz") == "https://www.mza.cz"
    assert robots_root("unknown.example") == "https://unknown.example"
