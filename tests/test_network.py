import asyncio
import gzip
import json
import re
import socket
import time
from urllib.parse import parse_qs
import httpx
import pytest
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy,SafeSource,public_ip,PublicOnlyBackend

URL='https://www.mza.cz/actapublica/matrika/hledani?typ=obec&obec_id=1'

@pytest.mark.parametrize('url',[
    'http://www.mza.cz/actapublica/matrika/hledani',
    'https://www.mza.cz.evil.example/actapublica/matrika/hledani',
    'https://127.0.0.1/actapublica/matrika/hledani',
    'https://www.mza.cz:8080/actapublica/matrika/hledani',
    'https://www.mza.cz/admin',
    'https://x:y@www.mza.cz/robots.txt',
    'https://www.mza.cz/iipsrv/../admin',
    'https://www.mza.cz/iipsrv/%252e%252e/admin',
    'https://www.mza.cz/iipsrv/iipsrv.fcgi?CVT=jpeg&FIF=/private.jp2',
    'https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/../private.jp2.dzi',
])
def test_reject_unsafe_urls(url):
    with pytest.raises(ResearchError):SourcePolicy(['www.mza.cz']).validate(url)

@pytest.mark.parametrize('ip',['127.0.0.1','::1','10.0.0.1','169.254.169.254','192.168.1.1','224.0.0.1','::ffff:127.0.0.1','not-ip'])
def test_nonpublic_ips(ip):assert not public_ip(ip)

@pytest.mark.asyncio
async def test_dns_rebinding_rejected_before_connect(monkeypatch):
    loop=asyncio.get_running_loop()
    async def lookup(*a,**kw):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
    monkeypatch.setattr(loop,'getaddrinfo',lookup)
    with pytest.raises(ResearchError):await PublicOnlyBackend().connect_tcp('www.mza.cz',443)

@pytest.mark.asyncio
async def test_connection_uses_validated_numeric_ip(monkeypatch):
    loop=asyncio.get_running_loop();received=[]
    async def lookup(*a,**kw):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
    monkeypatch.setattr(loop,'getaddrinfo',lookup)
    backend=PublicOnlyBackend()
    async def connect(host,*a,**kw):received.append(host);return 'fake-stream'
    monkeypatch.setattr(backend.backend,'connect_tcp',connect)
    assert await backend.connect_tcp('www.mza.cz',443)=='fake-stream'
    assert received==['8.8.8.8']

@pytest.mark.asyncio
async def test_cache_and_accounting(cfg,store,sid):
    calls=[]
    def handler(req):calls.append(str(req.url));return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        a=await source.fetch(sid,'alice',URL)
        b=await source.fetch(sid,'alice',URL)
        assert a==b and len(calls)==1
        assert store.usage(sid,'alice')['archive_requests']==1
        body,_=source._cache_paths(URL);body.write_bytes(b'corruption')
        await source.fetch(sid,'alice',URL)
        assert len(calls)==2
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('status,code',[(403,'SOURCE_ACCESS_DENIED'),(429,'RATE_LIMITED')])
async def test_refusal_circuit_persists(cfg,store,sid,status,code):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(status,headers={'retry-after':'60'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code==code
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='RATE_LIMITED' and len(calls)==1
        assert store.usage(sid,'alice')['archive_requests']==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_robots_disallows(cfg,store,sid):
    calls=[]
    def handler(req):
        calls.append(str(req.url));return httpx.Response(200,text='User-agent: *\nDisallow: /actapublica/',headers={'content-type':'text/plain'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED'
        assert calls==['https://www.mza.cz/robots.txt']
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('response,code',[
    (httpx.Response(302,headers={'location':'https://evil.example/data'}),'INVALID_SOURCE'),
    (httpx.Response(200,text='login',headers={'content-type':'text/plain'}),'SOURCE_UNAVAILABLE'),
    (httpx.Response(200,content=b'123',headers={'content-type':'text/html','content-length':'9'}),'SOURCE_UNAVAILABLE'),
    (httpx.Response(200,content=b'x',headers={'content-type':'text/html','content-length':'999999999'}),'SOURCE_UNAVAILABLE'),
])
async def test_bad_response_is_not_success(cfg,store,sid,response,code):
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:response),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code==code
        assert source.cache_get(URL,999) is None
    finally:await source.close()

@pytest.mark.asyncio
async def test_live_disabled_never_calls_transport(cfg,store,sid):
    cfg.live_enabled=False
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='LIVE_DISABLED'
    finally:await source.close()

ARON_LIST='https://www.mza.cz/aron/api/aron/apu/listresults?listType=ENTITY'
ARON_UNIT='https://www.mza.cz/aron/api/aron/apu/00000000-0000-4000-8000-0000000000a1'
IMG='https://www.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/x.jp2/info.json'
IMG2='https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/y.jp2/info.json'

def json_response(data=b'{"items":[],"count":0}'):
    return httpx.Response(200,content=data,headers={'content-type':'application/json'})

@pytest.mark.asyncio
async def test_post_sends_canonical_json_and_caches_by_body(cfg,store,sid):
    seen=[]
    def handler(req):
        seen.append((req.method,req.headers.get('content-type'),req.content));return json_response()
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'b':1,'a':'Brno'})
        await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':'Brno','b':1})
        await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':'Znojmo','b':1})
        assert len(seen)==2
        assert seen[0][0]=='POST' and seen[0][1]=='application/json'
        assert json.loads(seen[0][2])=={'a':'Brno','b':1}
    finally:await source.close()

@pytest.mark.asyncio
async def test_post_body_limit_and_get_only_routes(cfg,store,sid):
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'v':'x'*9000})
        assert e.value.code=='INVALID_ARGUMENT'
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',ARON_UNIT,kind='json',json_body={})
        assert e.value.code=='INVALID_SOURCE'
    finally:await source.close()

@pytest.mark.asyncio
async def test_post_redirect_is_not_followed(cfg,store,sid):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(307,headers={'location':ARON_LIST})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':1})
        assert e.value.code=='SOURCE_UNAVAILABLE' and len(calls)==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_aron_disabled_never_calls_transport(cfg,store,sid):
    cfg.aron_enabled=False
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',ARON_UNIT,kind='json')
        assert e.value.code=='LIVE_DISABLED' and 'RUBRIKA_ARON_ENABLED' in e.value.message
    finally:await source.close()

@pytest.mark.asyncio
async def test_aron_refusal_does_not_block_acta_publica(cfg,store,sid):
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path.startswith('/aron/'):return httpx.Response(429,headers={'retry-after':'60'})
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',ARON_UNIT,kind='json')
        assert e.value.code=='RATE_LIMITED'
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',ARON_UNIT,kind='json')
        assert e.value.code=='RATE_LIMITED'
        assert [c for c in calls if c.startswith('/aron/')]==['/aron/api/aron/apu/00000000-0000-4000-8000-0000000000a1']
    finally:await source.close()

@pytest.mark.asyncio
async def test_image_server_refusal_pauses_images_for_both_hosts(cfg,store,sid):
    calls=[]
    def handler(req):calls.append(str(req.url));return httpx.Response(429,headers={'retry-after':'60'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError):await source.fetch(sid,'alice',IMG,kind='json')
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',IMG2,kind='json')
        assert e.value.code=='RATE_LIMITED' and len(calls)==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_legacy_global_cooldown_is_still_respected(cfg,store,sid):
    store.meta_set('source_block_until',time.time()+60)
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='RATE_LIMITED'
    finally:await source.close()

@pytest.mark.asyncio
async def test_gzip_is_decoded_even_when_not_requested(cfg,store,sid):
    payload=b'{"items":[],"count":0}'
    def handler(req):
        return httpx.Response(200,content=gzip.compress(payload),
                              headers={'content-type':'application/json','content-encoding':'gzip'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:assert await source.fetch(sid,'alice',ARON_UNIT,kind='json')==payload
    finally:await source.close()

@pytest.mark.asyncio
async def test_gzip_bomb_stops_at_the_decoded_limit(cfg,store,sid):
    bomb=gzip.compress(b'0'*3_000_000)
    def handler(req):
        return httpx.Response(200,content=bomb,headers={'content-type':'application/json','content-encoding':'gzip'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',ARON_UNIT,kind='json')
        assert e.value.code=='SOURCE_UNAVAILABLE' and e.value.details['reason']=='too_large'
    finally:await source.close()

@pytest.mark.asyncio
async def test_html_instead_of_json_is_not_success(cfg,store,sid):
    # Maintenance page or SPA fallback on an API path.
    def handler(req):return httpx.Response(200,text='<html>ARON</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':1})
        assert e.value.code=='SOURCE_UNAVAILABLE'
    finally:await source.close()

@pytest.mark.asyncio
async def test_redirect_to_aron_respects_the_switch_per_hop(cfg,store,sid):
    # A GET redirect from Acta Publica to ARON must re-check RUBRIKA_ARON_ENABLED on the new hop.
    cfg.aron_enabled=False
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path==httpx.URL(ARON_UNIT).path:
            raise AssertionError('ARON must not be contacted while disabled')
        return httpx.Response(302,headers={'location':ARON_UNIT})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',URL)
        assert e.value.code=='LIVE_DISABLED'
        assert calls==[httpx.URL(URL).path]
    finally:await source.close()

@pytest.mark.asyncio
async def test_aron_global_daily_request_cap_leaves_acta_publica(cfg,store,sid):
    cfg.aron_global_daily_requests=1
    def handler(req):
        if req.url.path.startswith('/aron/'):return json_response()
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':1})
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':2})
        assert e.value.code=='BUDGET_EXCEEDED' and e.value.details['scope']=='global:aron'
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
    finally:await source.close()

CENSUS_DETAIL='https://www.mza.cz/scitacioperaty/digisada/detail/4422'

@pytest.mark.asyncio
async def test_census_disabled_never_calls_transport(cfg,store,sid):
    cfg.census_enabled=False
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',CENSUS_DETAIL)
        assert e.value.code=='LIVE_DISABLED' and 'RUBRIKA_CENSUS_ENABLED' in e.value.message
    finally:await source.close()

@pytest.mark.asyncio
async def test_census_refusal_pauses_only_census(cfg,store,sid):
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path.startswith('/scitacioperaty/'):return httpx.Response(429,headers={'retry-after':'60'})
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',CENSUS_DETAIL)
        assert e.value.code=='RATE_LIMITED'
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
        with pytest.raises(ResearchError):await source.fetch(sid,'alice',CENSUS_DETAIL)
        assert calls.count('/scitacioperaty/digisada/detail/4422')==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_census_global_daily_cap(cfg,store,sid):
    cfg.census_global_daily_requests=1
    def handler(req):return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.fetch(sid,'alice',CENSUS_DETAIL)
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice','https://www.mza.cz/scitacioperaty/digisada/detail/4423')
        assert e.value.code=='BUDGET_EXCEEDED' and e.value.details['scope']=='global:census'
    finally:await source.close()

@pytest.mark.asyncio
async def test_redirect_into_census_respects_the_switch(cfg,store,sid):
    cfg.census_enabled=False
    def handler(req):
        if req.url.path.startswith('/scitacioperaty/'):raise AssertionError('census must not be contacted')
        return httpx.Response(302,headers={'location':CENSUS_DETAIL})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='LIVE_DISABLED'
    finally:await source.close()

FORM_URL='https://www.mza.cz/scitacioperaty/digisada/do_advanced_search'
RESULTS='/scitacioperaty/digisada/advanced_search'
FORM={'archiv':'','obec':'Demo Lhota','cobec':'','ulice':'','rscitan':'','sumar':'','submit':'Vyhledat'}

def form_app(calls,pages=3,post_status=302,location='https://www.mza.cz'+RESULTS,cookie=True):
    sessions={}
    def handler(req):
        calls.append((req.method,req.url.path,req.headers.get('cookie')))
        if req.method=='POST':
            sid=f'sess{len(sessions)}';sessions[sid]=parse_qs(req.content.decode())
            headers={'location':location}
            if cookie:headers['set-cookie']=f'ci_session={sid}; path=/; HttpOnly'
            return httpx.Response(post_status,headers=headers)
        m=re.search(r'ci_session=(\w+)',req.headers.get('cookie') or '')
        form=sessions.get(m[1]) if m else None
        offset=int(req.url.path.rsplit('/',1)[1]) if req.url.path!=RESULTS else 0
        more=f'<a href="{RESULTS}/{offset+10}">další</a>' if offset+10<pages*10 else ''
        body=f"<html>obec={form['obec'][0] if form else 'NONE'} offset={offset}{more}</html>"
        return httpx.Response(200,text=body,headers={'content-type':'text/html; charset=utf-8','set-cookie':'tracker=1; path=/'})
    return handler

def next_link(body,url):
    m=re.search(rb'href="([^"]+)"',body)
    return m[1].decode() if m else None

@pytest.mark.asyncio
async def test_form_search_uses_one_redirect_and_its_own_cookie(cfg,store,sid):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls)),respect_robots=False)
    try:
        pages,truncated=await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert [p.split(b' offset=')[1][:2] for p in pages]==[b'0<',b'10',b'20']
        assert all(b'obec=Demo Lhota' in p for p in pages) and truncated is False
        assert calls[0][0]=='POST' and calls[0][2] is None
        assert [c[2] for c in calls[1:]]==['ci_session=sess0']*3
        assert len(source.client.cookies)==0
        again,_=await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert again==pages and len(calls)==4
    finally:await source.close()

@pytest.mark.asyncio
async def test_concurrent_form_searches_never_share_a_session(cfg,store,sid):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls,pages=2)),respect_robots=False)
    try:
        a,b=await asyncio.gather(
            source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link),
            source.form_search(sid,'alice',FORM_URL,{**FORM,'obec':'Demovice'},RESULTS,5,next_link))
        assert all(b'obec=Demo Lhota' in p for p in a[0]) and all(b'obec=Demovice' in p for p in b[0])
        assert len(source.client.cookies)==0
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_search_stops_at_max_pages(cfg,store,sid):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls,pages=4)),respect_robots=False)
    try:
        pages,truncated=await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,2,next_link)
        assert len(pages)==2 and truncated is True and len(calls)==3
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs',[
    {'post_status':200},
    {'location':'https://www.mza.cz/scitacioperaty/digisada/search'},
    {'location':'https://evil.example'+RESULTS},
    {'cookie':False},
])
async def test_unexpected_form_response_is_unavailable(cfg,store,sid,kwargs):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls,**kwargs)),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert e.value.code=='SOURCE_UNAVAILABLE' and len(calls)==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_expired_session_mid_paging_is_not_success(cfg,store,sid):
    calls=[]
    inner=form_app(calls)
    def handler(req):
        if req.url.path==RESULTS+'/10':return httpx.Response(302,headers={'location':'https://www.mza.cz'+RESULTS})
        return inner(req)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert e.value.code=='SOURCE_UNAVAILABLE'
    finally:await source.close()

@pytest.mark.asyncio
async def test_paging_link_outside_results_is_rejected(cfg,store,sid):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls)),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,
                                     lambda body,url:'/scitacioperaty/digisada/detail/1')
        assert e.value.code=='PARSE_ERROR'
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_body_limit_and_switch(cfg,store,sid):
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,{**FORM,'obec':'x'*3000},RESULTS,5,next_link)
        assert e.value.code=='INVALID_ARGUMENT'
        cfg.census_enabled=False
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert e.value.code=='LIVE_DISABLED'
    finally:await source.close()

@pytest.mark.asyncio
async def test_client_cookie_jar_stays_empty_for_every_source(cfg,store,sid):
    def handler(req):
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html','set-cookie':'x=1; path=/'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.fetch(sid,'alice',URL)
        assert len(source.client.cookies)==0
    finally:await source.close()


@pytest.mark.asyncio
async def test_result_pages_are_checked_against_robots_per_page(cfg,store,sid):
    calls=[]
    inner=form_app(calls)
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nDisallow: '+RESULTS+'/',
                                  headers={'content-type':'text/plain'})
        return inner(req)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=True)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert e.value.code=='SOURCE_ACCESS_DENIED'
        assert not any(c[1]==RESULTS+'/10' for c in calls)
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_search_accepts_comma_and_hyphen_in_session_cookie(cfg,store,sid):
    # `ci_session` values seen from the live app can contain "," and "-".
    calls=[]
    def handler(req):
        calls.append((req.method,req.url.path,req.headers.get('cookie')))
        if req.method=='POST':
            return httpx.Response(302,headers={'location':'https://www.mza.cz'+RESULTS,
                                               'set-cookie':'ci_session=abc-123,xyz; path=/; HttpOnly'})
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,lambda body,url:None)
        assert calls[1][2]=='ci_session=abc-123,xyz'
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_search_uses_last_valid_session_cookie(cfg,store,sid):
    calls=[]
    def handler(req):
        calls.append((req.method,req.url.path,req.headers.get('cookie')))
        if req.method=='POST':
            return httpx.Response(302,headers=[('location','https://www.mza.cz'+RESULTS),
                                               ('set-cookie','ci_session=deleted; Max-Age=0; path=/'),
                                               ('set-cookie','ci_session=sess0; path=/')])
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,next_link)
        assert calls[1][2]=='ci_session=sess0'
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_search_detects_paging_cycle(cfg,store,sid):
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(form_app(calls)),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,5,lambda body,url:RESULTS)
        assert e.value.code=='PARSE_ERROR'
    finally:await source.close()

@pytest.mark.asyncio
async def test_form_search_rejects_max_pages_below_one(cfg,store,sid):
    def no(req):raise AssertionError('network must not be contacted')
    source=SafeSource(cfg,store,transport=httpx.MockTransport(no),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:
            await source.form_search(sid,'alice',FORM_URL,FORM,RESULTS,0,next_link)
        assert e.value.code=='INVALID_ARGUMENT'
    finally:await source.close()

@pytest.mark.asyncio
async def test_robots_served_as_html_means_no_rules(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='<!doctype html><html><body>app</body></html>',headers={'content-type':'text/html'})
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
    finally:await source.close()

@pytest.mark.asyncio
async def test_robots_wildcards_are_enforced(cfg,store,sid):
    def handler(req):
        return httpx.Response(200,text='User-agent: *\nDisallow: /actapublica/*/hledani\n',headers={'content-type':'text/plain'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED'
    finally:await source.close()

@pytest.mark.asyncio
async def test_robots_html_with_bom_means_no_rules(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,content='\ufeff<!doctype html><html></html>'.encode('utf-8'),headers={'content-type':'text/html'})
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
    finally:await source.close()

@pytest.mark.asyncio
async def test_robots_with_bom_is_enforced(cfg,store,sid):
    def handler(req):
        return httpx.Response(200,content='\ufeffUser-agent: *\nDisallow: /\n'.encode('utf-8'),headers={'content-type':'text/plain'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED'
    finally:await source.close()


@pytest.mark.asyncio
async def test_disabled_archive_is_live_disabled_with_terms(cfg,store,sid):
    cfg=cfg.model_copy(update={'vca_enabled':False})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:httpx.Response(500)))
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice','https://aron.vychodoceskearchivy.cz/api/aron/apu/00000000-0000-4000-8000-0000000000a1',kind='json')
        assert e.value.code=='LIVE_DISABLED' and 'soahradec.gov.cz' in e.value.message
    finally:await source.close()


@pytest.fixture
def sleeps(monkeypatch):
    import rubrika.network as N
    waits=[]
    real=asyncio.sleep
    async def fake(s):
        waits.append(s)
        await real(0)
    monkeypatch.setattr(N.asyncio,'sleep',fake)
    return waits

@pytest.mark.asyncio
async def test_crawl_delay_applies_to_the_first_request_after_robots(cfg,store,sid,sleeps,monkeypatch):
    clock=[1000.0]
    monkeypatch.setattr('rubrika.storage.time.time',lambda:clock[0])
    import rubrika.network as N
    async def advance(s):
        sleeps.append(s); clock[0]+=s
    monkeypatch.setattr(N.asyncio,'sleep',advance)
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nCrawl-delay: 7\n',headers={'content-type':'text/plain'})
        return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        await source.fetch(sid,'alice',URL)
        assert source._interval('www.mza.cz')==7 and sum(sleeps)>=7   # the page waited 7 s after robots.txt
    finally:await source.close()

@pytest.mark.asyncio
async def test_excessive_crawl_delay_is_not_shortened(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nCrawl-delay: 120\n',headers={'content-type':'text/plain'})
        return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html'})
    calls=[]
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:(calls.append(r.url.path),handler(r))[1]))
    try:
        with pytest.raises(ResearchError) as e: await source.fetch(sid,'alice',URL)
        assert e.value.code=='RATE_LIMITED' and e.value.details['retry_after']>=120
        assert calls==['/robots.txt']
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[429,503])
@pytest.mark.parametrize('retry,expected',[('30',30),('Wed, 21 Oct 2099 07:28:00 GMT',86400),('soon',60),(None,60)])
async def test_retry_after_pauses_group(cfg,store,sid,status,retry,expected):
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(status,headers={'retry-after':retry} if retry else {})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e: await source.fetch(sid,'alice',URL)
        assert e.value.details['retry_after']==pytest.approx(expected,abs=2) or e.value.details['retry_after']==expected
        n=len(calls)
        with pytest.raises(ResearchError) as e2: await source.fetch(sid,'alice',URL)
        assert e2.value.code=='RATE_LIMITED' and len(calls)==n         # the transport was not hit again
    finally:await source.close()

@pytest.mark.asyncio
async def test_bot_gate_is_access_denied_with_cooldown(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(200,text='<html>Verifying your browser before proceeding __uzdbm</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED' and e.value.details['reason']=='bot_protection'
        with pytest.raises(ResearchError) as e2:await source.fetch(sid,'alice',URL)
        assert e2.value.code=='RATE_LIMITED'
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('path,kind',[('/actapublica/ajax/matrika/autocomplete_obec?term=Brno','json'),('/robots.txt','robots')])
async def test_gate_on_json_route_and_on_robots_is_detected(cfg,store,sid,path,kind):
    gate='<html><form method="post" action="/x/human/verify"><input type="hidden" name="nonce"></form></html>'
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:httpx.Response(200,text=gate,headers={'content-type':'text/html'})))
    try:
        with pytest.raises(ResearchError) as e: await source.fetch(sid,'alice','https://www.mza.cz'+path,kind=kind)
        assert e.value.details.get('reason')=='bot_protection'
    finally:await source.close()

@pytest.mark.asyncio
async def test_redirect_to_challenge_is_a_gate_net(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(302,headers={'location':'https://www.mza.cz/app/human/challenge'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.details['reason']=='bot_protection'
    finally:await source.close()

@pytest.mark.asyncio
async def test_no_implicit_cookies_between_hosts(cfg,store,sid):
    seen=[]
    def handler(req):
        seen.append((req.url.host,req.headers.get('cookie')))
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html',
                              'set-cookie':'track=1; Domain=.mza.cz; Path=/'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        await source.fetch(sid,'alice',URL)
        await source.fetch(sid,'alice','https://mza.cz/actapublica/matrika/hledani?typ=obec&obec_id=2')
        assert all(c is None for _,c in seen)
    finally:await source.close()

@pytest.mark.asyncio
async def test_slow_host_does_not_block_another_host(cfg,store,sid):
    gate=asyncio.Event()
    async def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        if req.url.host=='www.mza.cz':
            await gate.wait()
            return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html'})
        return httpx.Response(200,json={'ok':True})       # the VCA descriptor route is a JSON API
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        slow=asyncio.create_task(source.fetch(sid,'alice',URL))
        await asyncio.sleep(0.05)
        fast=await asyncio.wait_for(source.fetch(sid,'alice','https://aron.vychodoceskearchivy.cz/api/aron/apu/00000000-0000-4000-8000-0000000000a1',kind='json'),2)
        gate.set(); await slow
    finally:await source.close()

@pytest.mark.asyncio
async def test_user_triggered_mode_tolerates_disallow_and_records_it(cfg,store,sid,monkeypatch,caplog):
    import dataclasses
    from rubrika import archives as A
    monkeypatch.setitem(A.ARCHIVES,'mza',dataclasses.replace(A.ARCHIVES['mza'],robots_mode='user_triggered'))
    def handler(req):
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nDisallow: /actapublica/\n',headers={'content-type':'text/plain'})
        return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with caplog.at_level('INFO',logger='rubrika'):
            assert await source.fetch(sid,'alice',URL)==b'<html>x</html>'
        assert store.has_link(sid,'alice','robots_tolerated','www.mza.cz')
        assert 'obec_id' not in caplog.text and '/actapublica/matrika/hledani' in caplog.text   # path logged, query not
        assert source.robots_disallowed(URL) and not source.robots_disallowed('https://www.mza.cz/iipsrv/x')
    finally:await source.close()

@pytest.mark.asyncio
async def test_slow_body_is_aborted_before_the_lease_expires_and_lease_is_released(cfg,store,sid,monkeypatch):
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                yield b'x'
                await asyncio.sleep(0.1)
    def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(200,headers={'content-type':'text/html'},stream=Slow())
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(source,'_deadline',lambda:0.3)
    try:
        started=time.monotonic()
        with pytest.raises(ResearchError) as e: await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_UNAVAILABLE' and time.monotonic()-started<3
        assert store.meta_get('host_lease:www.mza.cz') is None
        assert source._deadline()<source._lease()
    finally:await source.close()

@pytest.mark.asyncio
async def test_stale_excessive_crawl_delay_does_not_block_robots_refresh(cfg,store,sid):
    from rubrika.robots import Robots
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nCrawl-delay: 5\n',headers={'content-type':'text/plain'})
        return httpx.Response(200,text='<html>x</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        source.robots['https://www.mza.cz']=(time.time()-90000,Robots.parse('User-agent: *\nCrawl-delay: 120\n'))
        with pytest.raises(ResearchError) as e: source._interval('www.mza.cz')
        assert e.value.code=='RATE_LIMITED'
        await source._check_robots(sid,'alice',URL)      # refresh must not be blocked by the stale rule
        assert calls==['/robots.txt'] and source._interval('www.mza.cz')==5
    finally:await source.close()


CHALLENGE='<html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/x"></script></html>'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,headers',[(403,{}),(503,{'retry-after':'5'}),(202,{}),(429,{'retry-after':'5'})])
async def test_challenge_page_on_non_200_is_a_gate(cfg,store,sid,status,headers):
    def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(status,text=CHALLENGE,headers={'content-type':'text/html',**headers})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED' and e.value.details['reason']=='bot_protection'
        assert store.meta_get('source_block_until:host:www.mza.cz')>time.time()+3000
    finally:await source.close()

@pytest.mark.asyncio
async def test_plain_403_is_not_a_bot_gate_and_pauses_only_its_group(cfg,store,sid):
    def handler(req):
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        return httpx.Response(403,text='<html>Forbidden</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_ACCESS_DENIED' and 'reason' not in e.value.details
        assert store.meta_get('source_block_until:host:www.mza.cz') is None
    finally:await source.close()

@pytest.mark.asyncio
async def test_gate_pauses_the_whole_host_but_not_another_host(cfg,store,sid):
    calls=[]
    def handler(req):
        calls.append(str(req.url))
        if req.url.path=='/robots.txt': return httpx.Response(404,text='')
        if req.url.path.startswith('/actapublica/'):
            return httpx.Response(200,text=CHALLENGE,headers={'content-type':'text/html'})
        return httpx.Response(200,json={'ok':True})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.details['reason']=='bot_protection'
        n=len(calls)
        for url in (IMG,ARON_UNIT):
            with pytest.raises(ResearchError) as e2:await source.fetch(sid,'alice',url,kind='json')
            assert e2.value.code=='RATE_LIMITED'
        assert len(calls)==n
        assert await source.fetch(sid,'alice','https://aron.vychodoceskearchivy.cz/api/aron/apu/00000000-0000-4000-8000-0000000000a1',kind='json')   # other host is unaffected
    finally:await source.close()

@pytest.mark.asyncio
async def test_cache_write_failure_does_not_fail_the_fetch(cfg,store,sid,monkeypatch):
    from pathlib import Path
    def boom(self,target): raise OSError('disk full')
    monkeypatch.setattr(Path,'replace',boom)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})),respect_robots=False)
    try:
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
        assert not list(source.cache_dir.glob('*.tmp'))
    finally:await source.close()

def test_cache_put_uses_a_unique_temp_name(cfg,store,monkeypatch):
    from pathlib import Path
    import rubrika.network as n
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:httpx.Response(404)))
    names=[]
    real=Path.write_bytes
    def spy(self,data): names.append(self.name); return real(self,data)
    monkeypatch.setattr(Path,'write_bytes',spy)
    source.cache_put(URL,b'a'); source.cache_put(URL,b'b')
    assert len(set(names))==2 and all(x.endswith('.tmp') for x in names)


def test_session_cookie_ignores_deleted_and_expired_cookies():
    from rubrika.network import session_cookie
    def h(*values): return httpx.Headers([("set-cookie", v) for v in values])
    ok = r"[A-Za-z0-9._-]{8,200}"
    assert session_cookie(h("JSESSIONID=ABCDEF123456; Path=/x; Secure; HttpOnly"), "JSESSIONID", ok) == "ABCDEF123456"
    assert session_cookie(h("JSESSIONID=ABCDEF123456; Max-Age=0"), "JSESSIONID", ok) is None
    assert session_cookie(h("JSESSIONID=ABCDEF123456; Expires=Thu, 01 Jan 1970 00:00:00 GMT"), "JSESSIONID", ok) is None
    assert session_cookie(h("JSESSIONID=deleted; Path=/"), "JSESSIONID", r"[A-Za-z0-9._-]{7,200}") is None
    assert session_cookie(h("JSESSIONID=ABCDEF123456", "JSESSIONID=ZZZZZZZZ9999; Max-Age=0"), "JSESSIONID", ok) == "ABCDEF123456"
    assert session_cookie(h("JSESSIONID=short"), "JSESSIONID", ok) is None
    assert session_cookie(h("OTHER=ABCDEF123456"), "JSESSIONID", ok) is None


@pytest.mark.asyncio
async def test_get_is_repeated_once_after_connect_failure(cfg,store,sid,monkeypatch):
    from rubrika import network
    monkeypatch.setattr(network,'CONNECT_RETRY_PAUSE',0)
    calls=[]
    def handler(req):
        calls.append(req)
        if len(calls)==1:raise httpx.ConnectError('tls handshake',request=req)
        return httpx.Response(200,text='<html>ok</html>',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        assert await source.fetch(sid,'alice',URL)==b'<html>ok</html>'
        assert len(calls)==2 and store.usage(sid,'alice')['archive_requests']==2
    finally:await source.close()

@pytest.mark.asyncio
async def test_two_connect_failures_stay_source_unavailable(cfg,store,sid,monkeypatch):
    from rubrika import network
    monkeypatch.setattr(network,'CONNECT_RETRY_PAUSE',0)
    calls=[]
    def handler(req):calls.append(req);raise httpx.ConnectTimeout('slow',request=req)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_UNAVAILABLE' and len(calls)==2
    finally:await source.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('exc',[httpx.ReadError('reset'),httpx.RemoteProtocolError('cut')])
async def test_failure_after_connecting_is_not_repeated(cfg,store,sid,monkeypatch,exc):
    from rubrika import network
    monkeypatch.setattr(network,'CONNECT_RETRY_PAUSE',0)
    calls=[]
    def handler(req):calls.append(req);raise exc
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False)
    try:
        with pytest.raises(ResearchError) as e:await source.fetch(sid,'alice',URL)
        assert e.value.code=='SOURCE_UNAVAILABLE' and len(calls)==1
    finally:await source.close()

@pytest.mark.asyncio
async def test_post_and_error_statuses_are_not_repeated(cfg,store,sid,monkeypatch):
    from rubrika import network
    monkeypatch.setattr(network,'CONNECT_RETRY_PAUSE',0)
    calls=[]
    def post(req):calls.append(req);raise httpx.ConnectError('x',request=req)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(post),respect_robots=False)
    try:
        with pytest.raises(ResearchError):await source.fetch(sid,'alice',ARON_LIST,kind='json',json_body={'a':1})
        assert len(calls)==1
    finally:await source.close()
    calls.clear()
    def err(req):calls.append(req);return httpx.Response(500,text='x',headers={'content-type':'text/html'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(err),respect_robots=False)
    try:
        with pytest.raises(ResearchError):await source.fetch(sid,'alice',URL)
        assert len(calls)==1
    finally:await source.close()
