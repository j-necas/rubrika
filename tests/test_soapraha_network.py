"""The session layer used for eBadatelna: redirects inside a session, cookie handling, one request per hop."""
import httpx
import pytest

from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.network import SafeSource, SiteSession
from rubrika.storage import Store

H = 'https://ebadatelna.soapraha.cz'


def make(tmp_path, handler, **settings):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, soapraha_enabled=True, request_interval=0, **settings)
    store = Store(cfg)
    source = SafeSource(cfg, store, transport=httpx.MockTransport(handler))
    source._interval = lambda host, enforce=True: 0      # the pace has its own test (test_soapraha_service)
    return source, store


SIDS = {}


def session(store=None):
    """A session bound to a real research of the store (requests are counted against it)."""
    if store is not None:
        SIDS['sid'] = store.create('alice', SearchQuery(raw_query='SYNTHETIC'))['research_id']
    return SiteSession(SIDS['sid'], 'alice', 'ebadatelna.soapraha.cz', H + '/pages/SearchMatrikaPage', opener_redirects=True)


def cookie(value='A' * 32):
    return {'set-cookie': f'JSESSIONID={value}; Path=/; Secure; HttpOnly'}


@pytest.mark.asyncio
async def test_wicket_opener_redirect_gives_cookie_and_landing_page_without_session_id(tmp_path):
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get('cookie')))
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /\n', headers={'content-type': 'text/plain'})
        if request.url.path == '/pages/SearchMatrikaPage' and not request.url.query:
            return httpx.Response(302, headers={'location': './SearchMatrikaPage;jsessionid=' + 'A' * 32 + '?0', **cookie()})
        return httpx.Response(200, text='<html>page</html>', headers={'content-type': 'text/html'})
    source, store = make(tmp_path, handler)
    s = session(store)
    await source.session_open(s)
    await source.session_open(s)                      # idempotent
    assert s.cookie == 'A' * 32 and s.landing == H + '/pages/SearchMatrikaPage?0' and s.requests == 1
    assert await source.session_get(s, s.landing) == b'<html>page</html>'
    assert seen[-1] == ('GET', '/pages/SearchMatrikaPage', 'JSESSIONID=' + 'A' * 32) and s.requests == 2
    s.reset()
    assert s.cookie is None and s.landing is None
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_plain_opener_still_needs_a_200(tmp_path):
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='', headers={'content-type': 'text/plain'})
        return httpx.Response(302, headers={'location': './x', **cookie()})
    source, store = make(tmp_path, handler)
    strict = SiteSession(session(store).sid, 'alice', 'ebadatelna.soapraha.cz', H + '/pages/SearchMatrikaPage')
    with pytest.raises(ResearchError) as e:
        await source.session_open(strict)
    assert e.value.code == 'SOURCE_UNAVAILABLE'
    no_cookie = make(tmp_path / 'b', lambda r: httpx.Response(302, headers={'location': './x'}) if r.url.path != '/robots.txt'
                     else httpx.Response(200, text='', headers={'content-type': 'text/plain'}))
    with pytest.raises(ResearchError):
        await no_cookie[0].session_open(SiteSession(no_cookie[1].create('alice', SearchQuery(raw_query='x'))['research_id'], 'alice', 'ebadatelna.soapraha.cz', H + '/pages/SearchMatrikaPage', opener_redirects=True))
    await source.close(); store.close(); await no_cookie[0].close(); no_cookie[1].close()


@pytest.mark.asyncio
async def test_redirects_are_followed_only_when_asked_and_only_within_the_host(tmp_path):
    def handler(request):
        p = request.url.path
        if p == '/robots.txt':
            return httpx.Response(200, text='', headers={'content-type': 'text/plain'})
        if p == '/pages/SearchMatrikaPage':
            return httpx.Response(302, headers={'location': './SearchMatrikaPage?0', **cookie()})
        if p == '/pages/MatrikaPage/matrikaId/1':
            if request.url.query == b'1':
                return httpx.Response(200, text='ok', headers={'content-type': 'text/html'})
            return httpx.Response(302, headers={'location': './1;jsessionid=' + 'B' * 32 + '?1'})
        if p == '/pages/MatrikaPage/matrikaId/2':
            return httpx.Response(302, headers={'location': 'https://www.mza.cz/actapublica/matrika/hledani'})
        if p == '/pages/MatrikaPage/matrikaId/3':
            return httpx.Response(302, headers={'location': './3'})            # a loop
        return httpx.Response(404)
    source, store = make(tmp_path, handler)
    s = session(store)
    with pytest.raises(ResearchError) as e:
        await source.session_get(s, H + '/pages/MatrikaPage/matrikaId/1')
    assert e.value.code == 'SOURCE_UNAVAILABLE' and 'přesměroval' in e.value.message
    assert await source.session_get(s, H + '/pages/MatrikaPage/matrikaId/1', follow=2) == b'ok'
    for n in (2, 3):
        with pytest.raises(ResearchError) as e:
            await source.session_get(s, H + f'/pages/MatrikaPage/matrikaId/{n}', follow=3)
        assert e.value.code in ('SOURCE_UNAVAILABLE', 'INVALID_SOURCE')
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_the_cookie_is_sent_only_to_its_own_host_and_never_to_a_redirect_target_elsewhere(tmp_path):
    cookies = []
    def handler(request):
        cookies.append((request.url.host, request.headers.get('cookie')))
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='', headers={'content-type': 'text/plain'})
        if request.url.path == '/pages/SearchMatrikaPage':
            return httpx.Response(302, headers={'location': './SearchMatrikaPage?0', **cookie()})
        return httpx.Response(302, headers={'location': 'https://www.mza.cz/robots.txt'})
    source, store = make(tmp_path, handler)
    s = session(store)
    with pytest.raises(ResearchError):
        await source.session_get(s, H + '/d/1/1', follow=2)
    assert all(host == 'ebadatelna.soapraha.cz' for host, _ in cookies)
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_session_post_sends_the_form_as_urlencoded_with_the_cookie(tmp_path):
    got = {}
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='', headers={'content-type': 'text/plain'})
        if request.method == 'GET':
            return httpx.Response(302, headers={'location': './SearchMatrikaPage?0', **cookie()})
        got.update(body=request.content.decode(), ctype=request.headers['content-type'], cookie=request.headers['cookie'],
                   ajax=request.headers.get('wicket-ajax'))
        return httpx.Response(200, text='<ajax-response/>', headers={'content-type': 'text/xml;charset=UTF-8'})
    source, store = make(tmp_path, handler)
    form = {'searchType': '1', 'keywordBox:searchTokenIn': 'radio0', 'keywordBox:searchToken': 'Mladá Boleslav', 'dateYearFrom': '1000', 'dateYearTo': '2100'}
    body = await source.session_post(session(store), H + '/pages/SearchMatrikaPage?0-1.0-searchForm-searchButton', form, headers={'Wicket-Ajax': 'true'})
    assert body == b'<ajax-response/>'
    assert got['body'] == 'searchType=1&keywordBox%3AsearchTokenIn=radio0&keywordBox%3AsearchToken=Mlad%C3%A1+Boleslav&dateYearFrom=1000&dateYearTo=2100'
    assert got['ctype'].startswith('application/x-www-form-urlencoded') and got['cookie'] == 'JSESSIONID=' + 'A' * 32 and got['ajax'] == 'true'
    await source.close(); store.close()


@pytest.mark.asyncio
async def test_a_bot_wall_in_a_session_stops_the_whole_host(tmp_path):
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='', headers={'content-type': 'text/plain'})
        if request.url.path == '/pages/SearchMatrikaPage':
            return httpx.Response(302, headers={'location': './SearchMatrikaPage?0', **cookie()})
        return httpx.Response(200, text='<html>Just a moment... verifying your browser</html>', headers={'content-type': 'text/html'})
    source, store = make(tmp_path, handler)
    with pytest.raises(ResearchError) as e:
        await source.session_get(session(store), H + '/d/1/1')
    assert e.value.code == 'SOURCE_ACCESS_DENIED' and e.value.details.get('reason') == 'bot_protection'
    with pytest.raises(ResearchError) as e:
        await source.session_get(session(store), H + '/d/1/2')
    assert e.value.code == 'RATE_LIMITED'
    await source.close(); store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("switches,allowed", [({}, False), ({"soapraha_census_enabled": True}, True), ({"soapraha_land_records_enabled": True}, True)])
async def test_archival_records_need_a_kind_switch_but_registers_do_not(tmp_path, switches, allowed):
    def handler(request):
        if request.url.path == '/robots.txt':
            return httpx.Response(200, text='User-agent: *\nAllow: /\n', headers={'content-type': 'text/plain'})
        return httpx.Response(200, text='<html>page</html>', headers={'content-type': 'text/html'})
    source, store = make(tmp_path, handler, **switches)
    s = session(store)
    s.cookie = 'A' * 32
    assert await source.session_get(s, H + '/pages/SearchMatrikaPage?0') == b'<html>page</html>'      # the register search is not gated by the new switches
    if allowed:
        assert await source.session_get(s, H + '/a/8004/1') == b'<html>page</html>'
    else:
        for url in (H + '/pages/SearchArchivaliePage?0', H + '/a/8004/1', H + '/pages/ArchivaliePage/archivalieId/8004?1'):
            with pytest.raises(ResearchError) as e:
                await source.session_get(s, url)
            assert e.value.code == 'LIVE_DISABLED'
        with pytest.raises(ResearchError) as e:
            await source.session_post(s, H + '/pages/SearchArchivaliePage?0-1.0-searchForm-searchButton',
                                      {"searchLocality": "A - community B - district C", "searchArchivalie": "", "typArchivalie": "19", "mistoUlozeni": "",
                                       "archivniFondId": "", "dateYearFrom": "1000", "dateYearTo": "2100"})
        assert e.value.code == 'LIVE_DISABLED'
    await source.close(); store.close()
