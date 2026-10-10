"""Read-only HTTP, DNS-pinned public addresses, cache and persistent budgets."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.cookiejar
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlencode, urljoin, urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend  # pinned httpcore==1.0.9

from .config import Settings
from .errors import ResearchError
from .archives import ARCHIVES, archive_for_host
from .gates import GATE_COOLDOWN, is_bot_gate
from .robots import Robots
from .sources import FORM_CHECKS, Route

MAX_REQUEST_BODY = 8192
CONNECT_RETRY_PAUSE = 1.5  # seconds before the single repeat of a GET that failed while connecting
GATE_PROBE = 65536  # bytes of a non-200 HTML answer inspected for an anti-bot wall
ARON_DISABLED = ("ARON je vypnutý. Provozovatel musí posoudit podmínky užití ARON "
                 "(https://www.mza.cz/aplikace/podminky-pouziti-aplikace-aron/) a nastavit RUBRIKA_ARON_ENABLED=true.")
CENSUS_DISABLED = ("Sčítací operáty jsou vypnuté. Provozovatel musí posoudit podmínky užití aplikace "
                   "(https://www.mza.cz/scitacioperaty/) a nastavit RUBRIKA_CENSUS_ENABLED=true.")
SKETCHES_DISABLED = ("Indikační skici jsou vypnuté. Provozovatel musí posoudit podmínky užití "
                     "(https://www.mza.cz/indikacniskici/page/view/podminky-uziti-sluzeb) a nastavit RUBRIKA_SKETCHES_ENABLED=true.")
log = logging.getLogger("rubrika")
REDIRECTS = (301, 302, 303, 307, 308)
MAX_FORM_BODY = 2048
# "scan" is a whole-page JPEG (SOA Praha): larger than a tile, and only a real image type is accepted.
KIND_LIMITS = {"html":8000000,"json":2000000,"xml":1000000,"image":8000000,"robots":512000,"scan":20000000}
HTML_MIMES = ("text/html", "application/xhtml+xml")
KIND_MIME = {"html":("text/html","application/xhtml+xml"),
    # IIIF info.json: ld+json per spec, iipsrv (MZA) sends json+ld.
    "json":("application/json","text/json","text/javascript","application/ld+json","application/json+ld"),
    "xml":("application/xml","text/xml","application/octet-stream"),
    "image":("image/jpeg","image/png","image/webp","application/octet-stream"),
    "robots":("text/plain","text/html"),"scan":("image/jpeg",)}


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    """Rubrika keeps no browser-like session: a cookie is sent only when a caller sets the header explicitly."""
    def set_ok(self, cookie, request): return False
    def return_ok(self, cookie, request): return False


def retry_after_seconds(headers) -> float:
    """Retry-After as seconds (number or HTTP date); default 60, clamped to 1 s .. 24 h."""
    retry = headers.get("retry-after", "60")
    try: seconds = float(retry)
    except ValueError:
        try: seconds = parsedate_to_datetime(retry).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError): seconds = 60
    if seconds != seconds: seconds = 60
    return max(1, min(86400, seconds))


class SourcePolicy:
    def __init__(self, hosts: list[str]):
        self.hosts = frozenset(hosts)

    def validate(self, url: str, method: str = "GET") -> Route:
        if len(url) > 6000 or any(ord(c) < 32 for c in url):
            raise ResearchError("INVALID_SOURCE", "Neplatná zdrojová adresa.")
        try:
            p = urlsplit(url)
            archive = archive_for_host(p.hostname)
            # HTTPS everywhere, except the one archive host that offers no TLS: there http:// (and only http://) is the scheme.
            scheme, port = ("http", 80) if archive and archive.allow_plain_http else ("https", 443)
            if p.scheme != scheme or p.hostname not in self.hosts or p.port not in (None, port) or p.username or p.password or p.fragment:
                raise ValueError()
        except ValueError as e:
            raise ResearchError("INVALID_SOURCE", "Zdroj musí být na povoleném HTTPS serveru integrovaného archivu.") from e
        path = unquote(p.path)
        if "\\" in path or ".." in path.split("/") or "%" in path:
            raise ResearchError("INVALID_SOURCE", "Neplatná cesta zdroje.")
        if archive is None or archive.route is None:
            raise ResearchError("INVALID_SOURCE", "Zdroj musí být na povoleném HTTPS serveru integrovaného archivu.")
        route = archive.route(p.hostname, path, p.query, method)
        if route is None:
            raise ResearchError("INVALID_SOURCE", "Tato cesta není součástí čtecího rozhraní.")
        return route


def robots_root(host: str) -> str:
    """Where a host's robots.txt lives, and the key of its cached rules: the host's own scheme (http only for a host without TLS)."""
    archive = archive_for_host(host)
    return f"{'http' if archive and archive.allow_plain_http else 'https'}://{host}"


def public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        # Also rejects RFC1918, loopback, link-local, multicast and mapped private IPv4.
        return ip.is_global and not ip.is_multicast
    except ValueError:
        return False


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Validate DNS once and CONNECT to that IP; TLS still uses the original host/SNI."""
    def __init__(self):
        self.backend = AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(timeout or 20):
                records = await loop.getaddrinfo(host,port,type=socket.SOCK_STREAM)
        except (OSError,TimeoutError) as e:
            raise httpcore.ConnectError("DNS resolution failed") from e
        addresses = list(dict.fromkeys(r[4][0] for r in records))
        if not addresses or any(not public_ip(ip) for ip in addresses):
            raise ResearchError("INVALID_SOURCE", "DNS odpověď neobsahuje výhradně veřejné adresy.")
        # Numeric address pins resolution; httpcore subsequently wraps TLS using original SNI.
        return await self.backend.connect_tcp(addresses[0],port,timeout,local_address,socket_options)

    async def connect_unix_socket(self, *args, **kwargs):
        raise ResearchError("INVALID_SOURCE", "Unixové sockety nejsou povoleny.")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class CoreStream(httpx.AsyncByteStream):
    def __init__(self, stream): self.stream=stream
    async def __aiter__(self):
        async for part in self.stream: yield part
    async def aclose(self): await self.stream.aclose()


CERTS = Path(__file__).parent / "certs"
# www.portafontium.eu sends its leaf certificate without the intermediate "GEANT TLS RSA 1" (issued by HARICA TLS RSA
# Root CA 2021, which Python trusts). Browsers fetch it via AIA; we ship this public CA certificate instead.
GEANT_TLS_RSA_1_SHA256 = "5b678dc44095a52895b63b31f27227f4b36c3e347491bf2bfa691837a5fb8c79"


def tls_context() -> ssl.SSLContext:
    """System trust plus the pinned-by-fingerprint GEANT intermediate; verification and hostname checks stay on."""
    ctx = ssl.create_default_context()
    pem = (CERTS / "geant-tls-rsa-1.pem").read_text(encoding="ascii")
    if hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest() != GEANT_TLS_RSA_1_SHA256:
        raise RuntimeError("certs/geant-tls-rsa-1.pem does not match the expected fingerprint")
    ctx.load_verify_locations(cadata=pem)
    return ctx


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.pool=httpcore.AsyncConnectionPool(ssl_context=tls_context(),
                    network_backend=PublicOnlyBackend(),max_connections=8,retries=0,
                    http1=True,http2=True)  # iipsrv answers HTTP/1.1 with 426
    async def handle_async_request(self,request):
        response=await self.pool.handle_async_request(httpcore.Request(
            method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme,host=request.url.raw_host,
                             port=request.url.port,target=request.url.raw_path),
            headers=request.headers.raw,content=request.stream,extensions=request.extensions))
        return httpx.Response(response.status,headers=response.headers,
                              stream=CoreStream(response.stream),extensions=response.extensions)
    async def aclose(self): await self.pool.aclose()


def session_cookie(headers, name: str, value_re: str) -> str | None:
    """Value of the last valid `name=` Set-Cookie: a deleted cookie (Max-Age=0, past Expires, "deleted") does not count."""
    cookie = None
    for value in headers.get_list("set-cookie"):
        parts = [p.strip() for p in value.split(";")]
        m = re.match(re.escape(name) + "=(" + value_re + ")$", parts[0])
        if not m or m[1].lower() == "deleted":
            continue
        expired = False
        for attr in parts[1:]:
            k, _, val = attr.partition("=")
            k, val = k.strip().lower(), val.strip()
            if k == "max-age" and re.fullmatch(r"-?\d+", val) and int(val) <= 0: expired = True; break
            if k == "expires":
                try:
                    if parsedate_to_datetime(val).timestamp() < time.time(): expired = True; break
                except (ValueError, TypeError, OverflowError): pass
        if not expired:
            cookie = m[1]      # the last valid Set-Cookie wins, matching browser/session semantics
    return cookie


class SiteSession:
    """Cookie of a stateful catalogue, kept in memory for the duration of ONE tool call and never stored or shared."""
    def __init__(self,sid:str,owner:str,host:str,opener_url:str,cookie_name:str="JSESSIONID",opener_redirects:bool=False):
        self.sid,self.owner,self.host,self.opener_url,self.cookie_name=sid,owner,host,opener_url,cookie_name
        self.opener_redirects=opener_redirects   # the opener answers 302 + cookie + the page it created (Wicket)
        self.cookie:str|None=None
        self.landing:str|None=None               # that page, without a session id in the address
        self.requests=0          # every HTTP request made for this session, opener and failed ones included

    def reset(self): self.cookie=self.landing=None


class SafeSource:
    def __init__(self, cfg: Settings, store, *, transport=None, respect_robots=True,
                 source_mode="live"):
        self.cfg,self.store=cfg,store
        self.policy=SourcePolicy(cfg.allowed_hosts)
        self.mode=source_mode
        self.client=httpx.AsyncClient(transport=transport or PublicTransport(),trust_env=False,
            timeout=cfg.request_timeout,follow_redirects=False,
            headers={"User-Agent":cfg.user_agent,"Accept-Encoding":"identity"})
        self.client.cookies.jar.set_policy(_NoCookies())
        self.robots_locks: dict[str, asyncio.Lock]={}
        self.robots={}
        self.respect_robots=respect_robots
        self.cache_dir=cfg.data_dir/"cache"
        self.cache_dir.mkdir(parents=True,exist_ok=True)

    async def close(self): await self.client.aclose()

    def _check_switch(self,route):
        if route.source=="aron" and not self.cfg.aron_enabled:
            raise ResearchError("LIVE_DISABLED",ARON_DISABLED)
        if route.source=="census" and not self.cfg.census_enabled:
            raise ResearchError("LIVE_DISABLED",CENSUS_DISABLED)
        if route.source=="soapraha_archivalie" and not (self.cfg.soapraha_census_enabled or self.cfg.soapraha_land_records_enabled):
            raise ResearchError("LIVE_DISABLED","Archiválie SOA Praha (sčítání, pozemkové knihy) jsou vypnuté; provozovatel je zapíná zvlášť.")
        if route.source=="sketches" and not self.cfg.sketches_enabled:
            raise ResearchError("LIVE_DISABLED",SKETCHES_DISABLED)
        if route.source in ("pf_land","pf_land_images") and not self.cfg.pf_land_records_enabled:
            raise ResearchError("LIVE_DISABLED","Úřední knihy Porta fontium jsou vypnuté (RUBRIKA_PF_LAND_RECORDS_ENABLED).")
        if route.source in ("pf_census","pf_census_images") and not self.cfg.pf_census_enabled:
            raise ResearchError("LIVE_DISABLED","Sčítání lidu Porta fontium je vypnuté (RUBRIKA_PF_CENSUS_ENABLED).")
        archive=ARCHIVES.get(route.archive)
        if archive and archive.enabled_setting and not getattr(self.cfg,archive.enabled_setting):
            raise ResearchError("LIVE_DISABLED",f"{archive.name} ({archive.app}) je vypnutý. Provozovatel musí posoudit "
                                f"podmínky užití ({archive.terms_url}) a zapnout ho.")

    def _cache_paths(self,url,request_body=None):
        # GET keys stay identical to v0.1 so existing caches remain valid.
        material=url if request_body is None else "POST\0"+url+"\0"+hashlib.sha256(request_body).hexdigest()
        key=hashlib.sha256(material.encode()).hexdigest()
        return self.cache_dir/(key+".bin"), self.cache_dir/(key+".json")

    def cache_get(self,url,ttl,request_body=None):
        data,meta=self._cache_paths(url,request_body)
        try:
            m=json.loads(meta.read_text(encoding="utf-8"))
            if m["created"] < time.time()-ttl: return None
            body=data.read_bytes()
            if hashlib.sha256(body).hexdigest()!=m["sha256"]:
                return None
            return body
        except (OSError,ValueError,KeyError):
            return None

    def cache_put(self,url,body,request_body=None):
        data,meta=self._cache_paths(url,request_body)
        # A unique temp name keeps concurrent writers apart; a failing cache write must never fail a good fetch.
        tmp=data.with_name(f"{data.stem}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(body); tmp.replace(data)
            meta.write_text(json.dumps({"created":time.time(),"sha256":hashlib.sha256(body).hexdigest()}),encoding="utf-8")
        except OSError as e:
            log.warning("cache write failed: %s",type(e).__name__)
            tmp.unlink(missing_ok=True)

    def invalidate(self,url,request_body=None):
        for p in self._cache_paths(url,request_body): p.unlink(missing_ok=True)

    def cleanup_cache(self):
        files=[]
        for p in self.cache_dir.glob("*.bin"):
            # Another server on the same data dir may remove a file between listing and stat.
            try: files.append((p,p.stat()))
            except FileNotFoundError: continue
        total=sum(s.st_size for _,s in files)
        cutoff=time.time()-self.cfg.metadata_ttl
        deleted=0
        for p,s in sorted(files,key=lambda f:f[1].st_mtime):
            if s.st_mtime < cutoff or total > self.cfg.max_cache_bytes:
                total-=s.st_size
                p.unlink(missing_ok=True); p.with_suffix(".json").unlink(missing_ok=True)
                deleted+=1
        return deleted

    def _robots_lock(self,root):
        return self.robots_locks.setdefault(root,asyncio.Lock())

    async def _check_robots(self,sid,owner,url):
        p=urlsplit(url)
        root=robots_root(p.hostname)
        async with self._robots_lock(root):
            entry=self.robots.get(root)
            if entry is None or entry[0] < time.time()-86400:
                body=await self._fetch(sid,owner,root+"/robots.txt","robots",ttl=86400)
                text=body.decode("utf-8-sig",errors="replace")
                # An SPA page served as robots.txt is not a rule set (RFC 9309: treat as unavailable).
                if text.lstrip()[:1]=="<":
                    log.info("robots.txt on %s is an HTML page; treated as no rules",p.hostname)
                    rules=Robots.empty()
                else:
                    rules=Robots.parse(text)
                self.robots[root]=(time.time(),rules)
            rules=self.robots[root][1]
        target=(p.path or "/")+("?"+p.query if p.query else "")
        if not rules.allowed(self.cfg.user_agent,target):
            archive=archive_for_host(p.hostname)
            if archive and archive.robots_mode=="user_triggered":
                # The user asked for this page; robots.txt addresses crawlers. Record it, never hide it.
                log.info("robots.txt disallow tolerated (user-triggered): host=%s path=%s",p.hostname,p.path)
                self.store.link(sid,owner,"robots_tolerated",p.hostname)
                return
            raise ResearchError("SOURCE_ACCESS_DENIED","Robots.txt nepovoluje tuto cestu pro automatizovaný přístup.")

    def robots_disallowed(self,url):
        """Whether cached robots.txt rules disallow `url`; never touches the network."""
        p=urlsplit(url)
        entry=self.robots.get(robots_root(p.hostname))
        return bool(entry) and not entry[1].allowed(self.cfg.user_agent,(p.path or "/")+("?"+p.query if p.query else ""))

    def _lease(self): return self.cfg.request_timeout+10

    def _deadline(self):
        """Whole send + body read must end before the host lease expires (httpx timeouts are per read)."""
        return self._lease()-5

    def _interval(self,host,enforce=True):
        archive=archive_for_host(host)
        entry=self.robots.get(robots_root(host))
        delay=entry[1].crawl_delay(self.cfg.user_agent) if entry else None
        interval=max(self.cfg.request_interval,archive.min_interval if archive else 0,delay or 0)
        if not enforce:
            # The robots.txt request itself must stay possible, or a stale slow rule could never be refreshed.
            return min(interval,self.cfg.max_crawl_delay)
        if interval>self.cfg.max_crawl_delay:
            raise ResearchError("RATE_LIMITED",f"Archiv žádá prodlevu {interval:g} s mezi požadavky; tak pomalé čtení "
                                "Rubrika neprovádí. Otevřete pramen v prohlížeči.",retry_after=round(interval))
        return interval

    async def fetch(self,sid,owner,url,kind="html",ttl=None,json_body=None):
        self.store.search(sid,owner)
        request_body=None
        if json_body is not None:
            request_body=json.dumps(json_body,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
            if len(request_body)>MAX_REQUEST_BODY:
                raise ResearchError("INVALID_ARGUMENT","Dotaz na zdroj je příliš velký.")
        route=self.policy.validate(url,"GET" if request_body is None else "POST")
        if not self.cfg.live_enabled:
            raise ResearchError("LIVE_DISABLED", "Živý přístup je vypnutý. Provozovatel musí ověřit podmínky a nastavit RUBRIKA_LIVE_ENABLED=true.")
        self._check_switch(route)
        if self.respect_robots:
            await self._check_robots(sid,owner,url)
        return await self._fetch(sid,owner,url,kind,ttl or self.cfg.metadata_ttl,request_body)

    async def _exchange(self,sid,owner,method,url,kind,*,body=None,content_type=None,headers=None):
        """One HTTP exchange under every safeguard; never follows redirects.
        Returns (status, headers, body); a 3xx and a robots 404 come back with an empty body.
        A GET that failed while connecting (no response began) is repeated once after a short pause; the second attempt
        goes through the same host interval, budgets and daily caps as any other request."""
        try:
            return await self._exchange_once(sid,owner,method,url,kind,body=body,content_type=content_type,headers=headers)
        except ResearchError as e:
            if method!="GET" or not getattr(e,"connect_failure",False):
                raise
        await asyncio.sleep(CONNECT_RETRY_PAUSE)
        return await self._exchange_once(sid,owner,method,url,kind,body=body,content_type=content_type,headers=headers)

    async def _exchange_once(self,sid,owner,method,url,kind,*,body=None,content_type=None,headers=None):
        route=self.policy.validate(url,method)
        self._check_switch(route)
        host=urlsplit(url).hostname
        token=uuid.uuid4().hex
        while True:
            # Re-check often: a cooldown set by another process must stop a waiting request.
            wait=self.store.claim_host(host,token,interval=self._interval(host,enforce=kind!="robots"),lease=self._lease(),
                                       groups=[route.group,"host:"+host])
            if wait==0: break
            if wait>self.cfg.tool_timeout:
                raise ResearchError("RATE_LIMITED","Přístup ke zdroji je dočasně pozastaven.",retry_after=round(wait))
            await asyncio.sleep(min(wait,1.0))
        allowance,actual=KIND_LIMITS[kind],0
        responded=False
        try:
            self.store.count_request(sid,owner,source=route.source,archive=route.archive)
            # MZA answers its ajax endpoints with 404 unless the request looks like XHR.
            h={"X-Requested-With":"XMLHttpRequest","Accept":"application/json"} if kind=="json" else {}
            archive=archive_for_host(host)
            if archive and archive.request_headers: h.update(archive.request_headers)
            if content_type: h["Content-Type"]=content_type
            h.update(headers or {})
            async with asyncio.timeout(self._deadline()):
                async with self.client.stream(method,url,headers=h or None,content=body) as response:
                    status=response.status_code
                    responded=True
                    if status in REDIRECTS:
                        if is_bot_gate(b"",response.headers.get("location")): self._gate(route,host)
                        return status,response.headers,b""
                    if status!=200:
                        # A challenge page often comes with 403/503/202; look before treating it as a plain refusal.
                        mime=response.headers.get("content-type","").split(";")[0].lower()
                        if status in (401,403,429,503) or mime in HTML_MIMES:
                            head=b""
                            async for part in response.aiter_bytes(chunk_size=8192):
                                head+=part; actual+=len(part)
                                if len(head)>=GATE_PROBE: break
                            if is_bot_gate(head[:GATE_PROBE],content_type=mime): self._gate(route,host)
                    if status in (401,403):
                        self.store.meta_set("source_block_until:"+route.group,time.time()+300)
                        raise ResearchError("SOURCE_ACCESS_DENIED","Zdroj odmítl přístup. Nepokoušejte se ochranu obcházet.",status=status)
                    if status in (429,503):
                        seconds=retry_after_seconds(response.headers)
                        self.store.meta_set("source_block_until:"+route.group,time.time()+seconds)
                        if status==429:
                            raise ResearchError("RATE_LIMITED","Zdroj požaduje pozastavení požadavků.",retry_after=seconds)
                        raise ResearchError("SOURCE_UNAVAILABLE","Zdroj je dočasně nedostupný a žádá pozastavení požadavků.",
                                            status=status,retry_after=seconds)
                    if status==404 and kind=="robots":
                        return 404,response.headers,b""
                    if status!=200:
                        raise ResearchError("SOURCE_UNAVAILABLE","Zdroj nevrátil úplná data.",status=status)
                    mime=response.headers.get("content-type","").split(";")[0].lower()
                    if mime not in KIND_MIME[kind] and mime not in HTML_MIMES:
                        raise ResearchError("SOURCE_UNAVAILABLE","Neočekávaný typ obsahu; může jít o přístupovou stránku.",content_type=mime)
                    length=response.headers.get("content-length")
                    if length and length.isdigit() and int(length)>allowance:
                        raise ResearchError("SOURCE_UNAVAILABLE","Odpověď archivu je větší, než Rubrika u tohoto druhu dat přijímá.",reason="too_large",allowed_bytes=allowance)
                    chunks=[]
                    async for part in response.aiter_bytes(chunk_size=8192):
                        actual+=len(part)
                        if actual>allowance:
                            raise ResearchError("SOURCE_UNAVAILABLE","Přenos byl zastaven na limitu velikosti odpovědi.",reason="too_large",allowed_bytes=allowance)
                        chunks.append(part)
                    data=b"".join(chunks)
                    # An HTML answer on any route may be a challenge page, whatever the caller expected.
                    if mime in HTML_MIMES and is_bot_gate(data,content_type=mime): self._gate(route,host)
                    if mime not in KIND_MIME[kind]:
                        raise ResearchError("SOURCE_UNAVAILABLE","Neočekávaný typ obsahu; může jít o přístupovou stránku.",content_type=mime)
                    if length and length.isdigit() and response.headers.get("content-encoding","identity")=="identity" and len(data)!=int(length):
                        raise ResearchError("SOURCE_UNAVAILABLE","Neúplná síťová odpověď.")
                    return 200,response.headers,data
        except TimeoutError as e:
            raise ResearchError("SOURCE_UNAVAILABLE","Zdroj neodpověděl v časovém limitu; požadavek byl přerušen.") from e
        except (httpx.HTTPError,httpcore.NetworkError,httpcore.TimeoutException,httpcore.ProtocolError,OSError) as e:
            err=ResearchError("SOURCE_UNAVAILABLE","Síťové spojení nebo ověření TLS selhalo.",error_type=type(e).__name__)
            # Only a failure while connecting (TCP/TLS handshake, before any response) may be repeated.
            err.connect_failure=not responded and isinstance(e,(httpx.ConnectError,httpx.ConnectTimeout,
                                                                 httpcore.ConnectError,httpcore.ConnectTimeout))
            raise err from e
        finally:
            self.store.release_host(host,token)

    def _gate(self,route,host):
        """Anti-bot wall: pause the whole host for an hour and stop. Rubrika never solves or bypasses it."""
        until=time.time()+GATE_COOLDOWN
        self.store.meta_set("source_block_until:"+route.group,until)
        self.store.meta_set("source_block_until:host:"+host,until)
        raise ResearchError("SOURCE_ACCESS_DENIED","Archiv blokuje automatizovaný přístup ochranou proti botům; Rubrika ji neobchází.",
                            reason="bot_protection")

    async def _fetch(self,sid,owner,url,kind,ttl,request_body=None):
        cached=self.cache_get(url,ttl,request_body)
        if cached is not None: return cached
        method="GET" if request_body is None else "POST"
        current=url
        for _ in range(4):
            if current!=url:
                self._check_switch(self.policy.validate(current,method))
                if self.respect_robots and kind!="robots":
                    # Redirects must obey the new host's policy too.
                    await self._check_robots(sid,owner,current)
            status,headers,body=await self._exchange(sid,owner,method,current,kind,body=request_body,
                content_type="application/json" if request_body is not None else None)
            if status in REDIRECTS:
                if request_body is not None:
                    raise ResearchError("SOURCE_UNAVAILABLE","Zdroj přesměroval dotaz; POST se nepřesměrovává.",status=status)
                location=headers.get("location")
                if not location: raise ResearchError("SOURCE_UNAVAILABLE","Přesměrování bez cíle.")
                current=urljoin(current,location)
                self.policy.validate(current)
                continue
            self.cache_put(url,body,request_body)
            return body
        raise ResearchError("SOURCE_UNAVAILABLE","Příliš mnoho přesměrování.")

    async def state_get(self,sid,owner,url:str,kind:str="html") -> bytes:
        """GET of a page whose state lives in its own address (a session token in the query string, as digi.nacr.cz does): never
        cached, no cookie, a redirect is an error. The caller keeps the token for one tool call only."""
        return await self._state_send(sid,owner,"GET",url,kind)

    async def state_post(self,sid,owner,url:str,form:dict,kind:str="html") -> bytes:
        """POST a form the archive's route table knows (see sources.FORM_CHECKS) to such a page. A redirect is an error."""
        body=urlencode(list(form.items())).encode()
        if len(body)>MAX_FORM_BODY:
            raise ResearchError("INVALID_ARGUMENT","Dotaz na zdroj je příliš velký.")
        route=self.policy.validate(url,"POST")
        check_form=FORM_CHECKS.get(route.archive)
        if check_form is None:
            raise ResearchError("INVALID_SOURCE","Tomuto archivu Rubrika formuláře neposílá.")
        check_form(form)
        return await self._state_send(sid,owner,"POST",url,kind,body=body,content_type="application/x-www-form-urlencoded; charset=UTF-8")

    async def _state_send(self,sid,owner,method,url,kind,*,body=None,content_type=None) -> bytes:
        self.store.search(sid,owner)
        route=self.policy.validate(url,method)
        if not self.cfg.live_enabled:
            raise ResearchError("LIVE_DISABLED", "Živý přístup je vypnutý. Provozovatel musí ověřit podmínky a nastavit RUBRIKA_LIVE_ENABLED=true.")
        self._check_switch(route)
        if self.respect_robots:
            await self._check_robots(sid,owner,url)
        status,_,data=await self._exchange(sid,owner,method,url,kind,body=body,content_type=content_type)
        if status in REDIRECTS:
            raise ResearchError("SOURCE_UNAVAILABLE","Zdroj přesměroval dotaz; přesměrování se nesleduje.",status=status)
        return data

    async def session_get(self,session:"SiteSession",url:str,kind:str="html",check:Callable[[bytes],None]|None=None,
                          follow:int=0,headers:dict|None=None) -> bytes:
        """GET inside a site session that lives only inside one tool call (see SiteSession). Never cached; the session
        cookie is sent only to the host that issued it. `follow` same-host redirects are followed (each one checked
        like a request of its own); by default a redirect is an error."""
        return await self._session_send(session,"GET",url,kind,check=check,follow=follow,headers=headers)

    async def session_post(self,session:"SiteSession",url:str,form:dict,kind:str="xml",check:Callable[[bytes],None]|None=None,
                           headers:dict|None=None) -> bytes:
        """POST a form the archive's route table knows (see sources.FORM_CHECKS) inside a site session. A redirect is an error."""
        body=urlencode(list(form.items())).encode()
        if len(body)>MAX_FORM_BODY:
            raise ResearchError("INVALID_ARGUMENT","Dotaz na zdroj je příliš velký.")
        route=self.policy.validate(url,"POST")
        check_form=FORM_CHECKS.get(route.archive)
        if check_form is None:
            raise ResearchError("INVALID_SOURCE","Tomuto archivu Rubrika formuláře neposílá.")
        check_form(form)
        return await self._session_send(session,"POST",url,kind,body=body,check=check,headers=headers,
                                        content_type="application/x-www-form-urlencoded; charset=UTF-8")

    async def session_open(self,session:"SiteSession"):
        """Open the session now (no-op when it is open): the opener page, its cookie and, for Wicket, the page it created."""
        if not self.cfg.live_enabled:
            raise ResearchError("LIVE_DISABLED", "Živý přístup je vypnutý. Provozovatel musí ověřit podmínky a nastavit RUBRIKA_LIVE_ENABLED=true.")
        self._check_switch(self.policy.validate(session.opener_url))
        if session.cookie is None:
            await self._open_session(session)

    async def _session_send(self,session,method,url,kind,*,body=None,content_type=None,check=None,follow=0,headers=None) -> bytes:
        route=self.policy.validate(url,method)
        if not self.cfg.live_enabled:
            raise ResearchError("LIVE_DISABLED", "Živý přístup je vypnutý. Provozovatel musí ověřit podmínky a nastavit RUBRIKA_LIVE_ENABLED=true.")
        self._check_switch(route)
        if urlsplit(url).hostname!=session.host:
            raise ResearchError("INVALID_SOURCE","Relace patří jinému serveru.")
        if session.cookie is None:
            await self._open_session(session)
        current=url
        for hop in range(follow+1):
            if hop:
                self.policy.validate(current,method)
            if self.respect_robots:
                await self._check_robots(session.sid,session.owner,current)
            session.requests+=1
            status,response_headers,data=await self._exchange(session.sid,session.owner,method,current,kind,body=body,
                content_type=content_type,headers={**(headers or {}),"Cookie":f"{session.cookie_name}={session.cookie}"})
            if status not in REDIRECTS:
                if check: check(data)
                return data
            if method!="GET" or hop==follow:
                raise ResearchError("SOURCE_UNAVAILABLE","Zdroj přesměroval dotaz v rámci relace; přesměrování se nesleduje.",status=status)
            current=self._same_host_target(session,current,response_headers.get("location"))
        raise AssertionError("unreachable")

    def _same_host_target(self,session,current,location) -> str:
        """Where a redirect inside a session leads: same host, no session id in the address (the cookie carries it)."""
        if not location: raise ResearchError("SOURCE_UNAVAILABLE","Přesměrování bez cíle.")
        target=re.sub(r";jsessionid=[A-Za-z0-9._-]+","",urljoin(current,location))
        if urlsplit(target).hostname!=session.host:
            raise ResearchError("SOURCE_UNAVAILABLE","Přesměrování v rámci relace vede na jiný server.")
        self.policy.validate(target)
        return target

    async def _open_session(self,session:"SiteSession"):
        """One plain GET of the opener page yields the session cookie; no login, no form and no challenge involved.
        Wicket answers it with 302 + the cookie + the page it has just created (`session.landing`)."""
        self.policy.validate(session.opener_url)
        if self.respect_robots:
            await self._check_robots(session.sid,session.owner,session.opener_url)
        session.requests+=1
        status,headers,_=await self._exchange(session.sid,session.owner,"GET",session.opener_url,"html")
        cookie=session_cookie(headers,session.cookie_name,r"[A-Za-z0-9._-]{8,200}")
        if not cookie or not (status==200 or (session.opener_redirects and status in (302,303))):
            raise ResearchError("SOURCE_UNAVAILABLE","Zdroj nevydal relaci potřebnou k procházení katalogu.",status=status)
        session.cookie=cookie
        session.landing=None
        if status!=200:
            session.landing=self._same_host_target(session,session.opener_url,headers.get("location"))

    async def form_search(self,sid,owner,post_url,form,results_path,max_pages,next_url:Callable[[bytes,str],str|None]):
        """POST a fixed search form, follow exactly one redirect to results_path and page through
        the results with a session cookie that lives only inside this call."""
        if max_pages<1:
            raise ResearchError("INVALID_ARGUMENT","Počet stránek musí být alespoň jedna.")
        self.store.search(sid,owner)
        body=urlencode(list(form.items())).encode()
        if len(body)>MAX_FORM_BODY:
            raise ResearchError("INVALID_ARGUMENT","Dotaz na zdroj je příliš velký.")
        route=self.policy.validate(post_url,"POST")
        if not self.cfg.live_enabled:
            raise ResearchError("LIVE_DISABLED", "Živý přístup je vypnutý. Provozovatel musí ověřit podmínky a nastavit RUBRIKA_LIVE_ENABLED=true.")
        self._check_switch(route)
        results_url=urljoin(post_url,results_path)
        if self.respect_robots:
            await self._check_robots(sid,owner,post_url)
            await self._check_robots(sid,owner,results_url)
        cache_key=b"FORM\0"+body
        cached=self.cache_get(post_url,self.cfg.metadata_ttl,cache_key)
        if cached is not None:
            saved=json.loads(cached)
            return [base64.b64decode(p) for p in saved["pages"]],saved["truncated"]
        status,headers,_=await self._exchange(sid,owner,"POST",post_url,"html",body=body,
                                              content_type="application/x-www-form-urlencoded")
        target=urlsplit(urljoin(post_url,headers.get("location") or "")) if status in (302,303) else None
        expected=urlsplit(results_url)
        if not target or (target.hostname,target.path)!=(expected.hostname,expected.path) or target.query:
            raise ResearchError("SOURCE_UNAVAILABLE","Hledání nevrátilo očekávané přesměrování na výsledky.",status=status)
        cookie=session_cookie(headers,"ci_session",r"[A-Za-z0-9,-]{1,256}")
        if not cookie:
            raise ResearchError("SOURCE_UNAVAILABLE","Hledání nevrátilo relaci výsledků.")
        pages,current,seen,truncated=[],results_url,set(),False
        while True:
            seen.add(current)
            if self.respect_robots:
                # Every result page is re-checked: pagination can walk into a path robots.txt disallows.
                await self._check_robots(sid,owner,current)
            status,_,page=await self._exchange(sid,owner,"GET",current,"html",headers={"Cookie":"ci_session="+cookie})
            if status!=200:
                raise ResearchError("SOURCE_UNAVAILABLE","Relace výsledků vypršela nebo byla přesměrována.",status=status)
            pages.append(page)
            nxt=next_url(page,current)
            if not nxt: break
            nxt=urljoin(current,nxt)
            p=urlsplit(nxt)
            if (p.hostname,p.path)!=(expected.hostname,expected.path) and not (p.hostname==expected.hostname and p.path.startswith(expected.path+"/")):
                raise ResearchError("PARSE_ERROR","Stránkování opouští výsledky hledání.")
            self.policy.validate(nxt)
            if nxt in seen:
                raise ResearchError("PARSE_ERROR","Stránkování výsledků se zacyklilo.")
            if len(pages)>=max_pages:
                truncated=True
                break
            current=nxt
        self.cache_put(post_url,json.dumps({"pages":[base64.b64encode(p).decode() for p in pages],
                                            "truncated":truncated}).encode(),cache_key)
        return pages,truncated
