"""Synthetic Národní archiv VadeMeCum (Bach application) for tests and demos: one fund's inventory with its table of
contents, 12-row pages, session cookie and expiry, scan viewer with a thumbnail window, and Zoomify scans behind the
`mrimage` proxy. Mirrors the shapes of the real application. NEVER real archive data; not shipped in the bundle."""
from __future__ import annotations

import hashlib
import html
import math
import re
import uuid
from urllib.parse import parse_qs, quote

import httpx

from .demo_zoomify import NA_ROOT, TILE, ZoomifyArchive, tier_sizes

HOST = "vademecum.nacr.cz"
BASE = "/vademecum"
FUND_XID = "f86bf41ed11ae5f01740720eed9e0dbb"
FUND_ENTITY = "(^n)(((20cb6cc9d41144e5874020c1e918c34f)(de1e5208398e4a7eb52df0f4687e7dd2))(4137))"
PAGE = 12          # rows of the inventory per page
WINDOW = 10        # thumbnails of the viewer
TITLE = "Matriky židovských náboženských obcí v českých krajích"

# (kind, level, text) in inventory order. kind: h header, b book, r cross reference row, u undigitised book
# book text: (inventory number, description, scan count)
ROWS: list[tuple] = [
    ("h", 20, "ÚŘEDNÍ KNIHY (matriky a indexy)"),
    ("h", 40, "ZKUŠOV (o. Testov)"),
    ("b", 0, ("1", "N 1849-1871 O 1841-1856 Z 1850-1870", 4)),
    ("b", 0, ("2", "N 1835-1864 (i)", 3)),
    ("b", 0, ("3", "N 1842-1854, 1864", 2)),
    ("b", 0, ("3a", "O 1850-1860", 2)),
    ("b", 0, ("3b", "Z 1850-1860", 2)),
    ("h", 60, "index"),
    ("b", 0, ("4", "N 1835-1864", 2)),
    ("h", 40, "BABČICE ((o. Tábor)"),
    ("r", 0, "— ANNÍN - viz Kundratice"),
    ("b", 0, ("5", "Z 1840-1862 (i)", 5)),
    ("u", 0, ("6", "O 1845-1861", 0)),
    ("h", 40, "DAVLE viz Roztoky"),
    ("h", 40, "PLZEŇ (o. Plzeň)"),
    ("b", 0, ("1541", "N 1864-1869", 23)),
    ("b", 0, ("1542", "N 1869-1873", 3)),
    ("b", 0, ("1543", "N 1874-1877", 2)),
    ("b", 0, ("1544", "N 1901-1915 (i v letech 1908-1915 pro každý rok)", 2)),
    ("b", 0, ("1549", "O 1868-1878", 2)),
    ("b", 0, ("1550", "O 1868-1891", 2)),
    ("b", 0, ("1554", "Z 1869-1920 (i v letech 1908-1920 pro každý rok)", 2)),
    ("b", 0, ("1557", "N 1799-1840 O 1811-1840 Z 1804-1844", 2)),
    ("h", 60, "indexy"),
    ("b", 0, ("1559", "N 1869-1906", 2)),
    ("b", 0, ("1560", "O 1869-1925", 2)),
    ("b", 0, ("1561", "O 1880-1890", 2)),
    ("h", 40, "PRAHA - matriky narozených"),
    ("b", 0, ("2001", "N 1850-1860", 2)),
    ("b", 0, ("2002", "N 1861-1870", 2)),
    ("h", 40, "PRAHA-KARLÍN"),
    ("b", 0, ("2101", "N 1880-1890", 2)),
    ("b", 0, ("2102", "N 1891-1900", 2)),
    ("h", 40, "PRAHA-SMÍCHOV"),
    ("b", 0, ("2111", "N 1880-1900", 2)),
    ("h", 40, "PRAHA - matriky oddaných"),
    ("b", 0, ("2201", "O 1850-1870", 2)),
    ("h", 40, "PRAHA-KARLÍN"),
    ("b", 0, ("2301", "O 1880-1900", 2)),
    ("h", 40, "ŠTĚNOVICE (o. Plzeň-jih)"),
    ("b", 0, ("2401", "N 1850-1870 O 1850-1870 Z 1850-1870", 2)),
    ("h", 40, "BĚLOHRAD (Lázně Bělohrad, o. Jičín)"),
    ("b", 0, ("2501", "Z 1870-1880", 2)),
    ("h", 40, "LOUNY (o. Louny)"),
    ("b", 0, ("2601", "N 1860-1870", 2)),
    ("h", 60, "Citoliby"),
    ("b", 0, ("2602", "N 1871-1880", 2)),
]
TOTAL = len(ROWS)


def _n_of(prefix: str, nth: int = 0) -> int:
    hits = [i + 1 for i, r in enumerate(ROWS) if r[0] == "h" and r[2].startswith(prefix)]
    return hits[nth]


def n_of(prefix: str, nth: int = 0) -> int:
    """1-based table-of-contents number (the `bookResumeN` id) of a synthetic header."""
    return _n_of(prefix, nth)


def entity_of(row: int) -> int:
    return 292100 + row


def xid_of(row: int) -> str:
    return str(uuid.UUID(hashlib.md5(f"SYNTHETIC-NA|book|{row}".encode()).hexdigest()))


def entity(number: int) -> str:
    return quote(f"(^n)(((20cb6cc9d41144e5874020c1e918c34f)(de1e5208398e4a7eb52df0f4687e7dd2))({number}))", safe="")


def scan_size(scan: int) -> tuple[int, int]:
    return 1295 + 2 * scan, 907 + 3 * scan       # odd sizes: the real server halves them with ceil


def _uuid_of(row: int, scan: int) -> str:
    return str(uuid.UUID(hashlib.md5(f"SYNTHETIC-NA|scan|{row}|{scan}".encode()).hexdigest()))


def root_of(row: int, scan: int) -> str:
    return NA_ROOT.rsplit("/", 1)[0] + "/" + _uuid_of(row, scan) + ".jpg"


def thumb_of(row: int, scan: int) -> str:
    return root_of(row, scan).replace("/zoomify//", "/image//") + "/nahled_maly.jpg"


def scan_hash(row: int, scan: int) -> str:
    return hashlib.md5(f"SYNTHETIC-NA|hash|{row}|{scan}".encode()).hexdigest()


def book_rows() -> dict[int, tuple]:
    """entity id -> (row index, inventory number, description, scans)"""
    return {entity_of(i): (i, r[2][0], r[2][1], r[2][2]) for i, r in enumerate(ROWS) if r[0] == "b"}


class NaArchive:
    """Serves vademecum.nacr.cz (catalogue with a real session, images through Zoomify) and its robots.txt."""

    def __init__(self, robots="User-agent: *\nDisallow: /\n", *, single_use_tokens=False, jsessionid_links=False):
        self.robots = robots
        self.single_use_tokens, self.jsessionid_links = single_use_tokens, jsessionid_links
        self.requests: list[tuple[str, str, str | None]] = []       # (method, url, cookie header)
        self.sessions: set[str] = set()
        self.tokens: dict[str, str] = {}                            # token -> session
        self.used: set[str] = set()
        self.volume_open: set[str] = set()                          # sessions that have loaded the inventory
        self.viewer: dict[str, int] = {}                            # session -> entity of the book in its viewer
        self.books = book_rows()
        self.zoomify: dict[str, ZoomifyArchive] = {}
        self.forbidden_hits: list[str] = []
        self.gate = False
        self.status: dict[str, int] = {}                            # action path -> forced status
        self.transport = httpx.MockTransport(self.handle)

    # -- bookkeeping ----------------------------------------------------------------------------------------
    def expire_sessions(self):
        self.sessions.clear()

    def image_archive(self, root: str) -> ZoomifyArchive | None:
        for entity_id, (row, inv, descr, scans) in self.books.items():
            for scan in range(1, scans + 1):
                if root_of(row, scan) == root:
                    if root not in self.zoomify:
                        w, h = scan_size(scan)
                        self.zoomify[root] = ZoomifyArchive(w, h, root=root, robots=self.robots, halving="ceil")
                    return self.zoomify[root]
        return None

    def image_requests(self) -> list[str]:
        return [u for m, u, c in self.requests if u.startswith(f"https://{HOST}/mrimage/")]

    def catalog_requests(self) -> list[str]:
        return [u for m, u, c in self.requests if u.startswith(f"https://{HOST}{BASE}/")]

    def _token(self, session: str) -> str:
        token = hashlib.sha256(f"{session}|{len(self.tokens)}".encode()).hexdigest()[:48] + "_" + str(len(self.tokens))
        self.tokens[token] = session
        return token

    def _action(self, name: str, session: str, fresh: bool) -> str:
        return f"{BASE}/{name}" + (f";jsessionid={session}" if fresh or self.jsessionid_links else "")

    # -- HTML -----------------------------------------------------------------------------------------------
    @staticmethod
    def _shell(body: str, script: str = "") -> str:
        return ('<!DOCTYPE html><html lang="cs"><head><meta charset="utf-8"><meta name="ROBOTS" content="NONE">'
                "<title>Národní archiv</title></head><body>" + body + script + "</body></html>")

    def _article(self, session: str, i: int) -> str:
        kind, level, data = ROWS[i]
        show = (f"{BASE}/BookVolume.action?show=&amp;_sourcePage={self._token(session)}&amp;pagerCompStateId=PAGER_RESULT_BOOK"
                f"&amp;xid={{xid}}&amp;entityType={{etype}}&amp;paginatorCompStateId=PAGINATOR_RESULT_BOOK&amp;rowPg={i}")
        if kind == "h":
            link = show.format(xid=xid_of(i), etype=10018)
            return (f'<aricle class="contentRecordList kapitola" ><div class="content"><a href="{link}" class="textBlockLink linOn linkRight">'
                    f'<div class="textBlock"><p class="title tooltipFor">\n  {html.escape(data)}   \n</p><p class="years"><span class="tooltipFor"></span></p>'
                    f'<p class="descr"><span class="tooltipFor">{html.escape(data)}</span></p></div></a>'
                    f'<div class="links"><span class="empty"></span><a href="{BASE}/BasketBean.action?addToBasket=&amp;entityRef={entity(entity_of(i))}" class="basket">'
                    '<i class="icon icon-cart2"></i></a></div></div></aricle>')
        link = show.format(xid=xid_of(i), etype=9019)
        if kind == "r":
            title, descr, attach = data, "", ""
        else:
            inv, descr, scans = data
            title = f"inv. č. {inv}"
            attach = ""
            if kind == "b":
                attach = (f'<div class="atachmentsAll"><i class="icon icon-attachment"></i><span class="atachmentsHover">'
                          f'<a href="{self._action("Zoomify.action", session, False)}?entityRef={entity(entity_of(i))}&amp;scanIndex=0" '
                          f'class="attachments linkRight" target="repo-zoomify"><i class="icon icon-image2 linkTooltip" '
                          f'tooltip="Zobrazit v plné kvalitě {scans} skenů"></i></a></span></div>')
        return (f'<aricle class="contentRecordList  " ><div class="content"><a href="{link}" class="textBlockLink linOn linkRight">'
                f'<div class="textBlock"><p class="title tooltipFor">\n  {html.escape(title)}   \n</p><p class="years"><span class="tooltipFor"></span></p>'
                f'<p class="descr"><span class="tooltipFor">{html.escape(descr)}</span></p></div></a>'
                f'<div class="links">{attach}<a href="{BASE}/BasketBean.action?addToBasket=&amp;entityRef={entity(entity_of(i))}" class="basket">'
                '<i class="icon icon-cart2"></i></a></div></div></aricle>')

    def _volume(self, session: str, start: int, fresh: bool = False) -> str:
        def pager_link(icon, row):
            return (f'<div class="pageIco"><a href="{self._action("PaginatorBookVolume.action", session, fresh)}?_sourcePage={self._token(session)}&amp;row={row}">'
                    f'<i class="{icon} icon link"></i></a></div>')
        nolink = lambda icon: f'<div class="pageIco"><span class="nolinkSpan"><i class="{icon} icon nolink"></i></span></div>'
        pager = ('<div id="paginatiorBlock">'
                 + (pager_link("icon-first", 0) + pager_link("icon-backward2", start - PAGE) if start else nolink("icon-first") + nolink("icon-backward2"))
                 + f'<div class="pageNum"><form method="GET" action="{BASE}/PaginatorBookVolume.action"><input size="6" name="rowTxt" type="text" value="{start + 1}" />'
                 f'<span>\n\tCelkem\n\t :\n\t{TOTAL}\n</span></form></div>'
                 + (pager_link("icon-forward3", start + PAGE) + pager_link("icon-last", TOTAL - 1) if start + PAGE < TOTAL else nolink("icon-forward3") + nolink("icon-last"))
                 + "</div>")
        articles = "".join(self._article(session, i) for i in range(start, min(TOTAL, start + PAGE)))
        toc = ""
        for i, (kind, level, data) in enumerate(ROWS):
            if kind == "h":
                n = i + 1
                toc += (f'<div class="bookResumeList" id="bookResume{n}" data-varstatus="{n}"><a style="padding-left:{level}px" '
                        f'href="{self._action("PaginatorBookVolume.action", session, fresh)}?alter=&amp;_sourcePage={self._token(session)}'
                        f'&amp;zanchor=%23bookResume{n}&amp;rowTxt={n}"><span>{html.escape(data)}</span></a></div>')
        return self._shell(
            f'<section id="contentBook"><div class="paginatorBlock"><div id="paginatiorBlock">{pager}</div></div>{articles}</section>'
            f'<section id="sidebar"><div id="mainSearch"><h2>Obsah pomůcky:</h2><div id="bookResume">{toc}</div></div></section>')

    def _viewer(self, session: str, entity_id: int, first: int, fresh: bool = False) -> str:
        row, inv, descr, scans = self.books[entity_id]
        last = min(scans, first + WINDOW - 1)

        def link(icon, target):
            return (f'<div class="pageIco"><a href="{self._action("PaginatorMedia.action", session, fresh)}?_sourcePage={self._token(session)}'
                    f'&amp;row={target}"><i class="{icon} icon link"></i></a></div>')
        pager = ("".join(link(i, r) for i, r in (("icon-first", 0), ("icon-previous", first - 2)) if first > 1)
                 + f'<div class="pageNum">\n\t\t{first} - {first + WINDOW - 1} | {scans}\n</div>'
                 + ("".join(link(i, r) for i, r in (("icon-next", first), ("icon-forward3", first - 1 + WINDOW), ("icon-last", scans - 1)) if last < scans)))
        thumbs = "".join(
            f'<li class="{"active" if s == first else ""}" id="scan{scan_hash(row, s)}"><a href="https://{HOST}{BASE}/permalink?xid={xid_of(row)}'
            f'&scan={scan_hash(row, s)}&parentType=9019" title="Sken {s} - "><img src=\'{thumb_of(row, s)}\' alt=""/>'
            f'<span class="numImg">{s}</span></a></li>' for s in range(first, last + 1))
        w, h = scan_size(first)
        props = f"<IMAGE_PROPERTIES WIDTH='{w}' HEIGHT='{h}' NUMTILES='{sum(math.ceil(a / TILE) * math.ceil(b / TILE) for a, b in tier_sizes(w, h, halving='ceil'))}' NUMIMAGES='1' VERSION='1.8' TILESIZE='256' DPIX='-1' DPIY='-1' />"
        body = (f'<div id="headerTopPanel"><a class="text headline" href="https://{HOST}{BASE}/permalink?xid={xid_of(row)}" '
                f'title="{TITLE} • inv. č. {inv}">{TITLE} • inv. č. {inv}</a></div>'
                f'<div id="headerBottomPanel"><div class="paginatorPlusSave"><div id="paginatiorBlock">{pager}'
                f'<div class="pageAfter"><form method="GET" action="{BASE}/PaginatorMedia.action" id="getScan"><div id="getScanSelect"></div>'
                f'<span class="slash">|</span><span class="number">{scans}</span></form></div></div></div></div>'
                f'<nav><ul class="pictures">{thumbs}</ul></nav>')
        script = ("<script type=\"text/javascript\">\n\tjQuery(document).ready(function() {\n\t\tfunction getScanSelect(){\n"
                  f'\t\t  var max= "{scans}";\n\t\t  var activePicture = "{first}";\n\t\t}}\n'
                  f'\t    \tvar zoomifyImgPath = "{root_of(row, first)}";\n'
                  f'\t    \t//var imgProperties = "zSkinPath=x&zImageProperties=<IMAGE_PROPERTIES WIDTH=\'9\' HEIGHT=\'9\' NUMTILES=\'1\' />";\n'
                  f'\t    \tvar imgProperties = "zSkinPath=js/Assets/Skins/Light&" + addParams + "&zRotationVisible=1&zInitialRotation=0&zImageProperties={props}";\n'
                  '\t    \tif(localStorage.getItem(\'zoomIndex\')) {\n\t    \t\timgProperties = "zSkinPath=x&zImageProperties=" + activeItemDPI;\n\t    \t}\n'
                  '\t    \tZ.showImage("showZoomify", zoomifyImgPath, imgProperties);\n\t});\n</script>')
        self.viewer[session] = entity_id
        return self._shell(body, script)

    # -- transport ------------------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie")
        self.requests.append((request.method, str(request.url), cookie))
        host, path = request.url.host, request.url.path
        query = parse_qs(request.url.query.decode(), keep_blank_values=True)
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        if host == HOST and path.startswith("/mrimage/"):
            m = re.match(r"(/mrimage/.+?\.jpg)/", path)
            archive = self.image_archive("https://" + HOST + m[1]) if m else None
            return archive.handle(request) if archive else httpx.Response(404)
        if host != HOST or request.method != "GET" or not path.startswith(BASE + "/"):
            return httpx.Response(404)
        html_headers = {"content-type": "text/html;charset=UTF-8"}
        if self.gate:
            return httpx.Response(200, text="<html><title>Just a moment...</title><body>Verifying your browser <form action='/human/challenge'></form></body></html>",
                                  headers={"content-type": "text/html"})
        action = path[len(BASE) + 1:].split(";")[0]
        if action in self.status:
            return httpx.Response(self.status[action], text="<html>x</html>", headers={"content-type": "text/html"})
        if (action in ("BasketBean.action", "SearchBean.action", "Rejstriky.action", "AutocompleteBean.action") or "login" in action
                or "captcha" in query or "showOne" in query or "baseUrl" in query or "show" in query):
            self.forbidden_hits.append(str(request.url))
            return httpx.Response(403)
        session = None
        if cookie:
            m = re.fullmatch(r"JSESSIONID=([0-9a-f]+)", cookie)
            session = m[1] if m and m[1] in self.sessions else None
        if action == "permalink":
            headers = dict(html_headers)
            if not session:
                session = hashlib.md5(f"na{len(self.sessions)}{len(self.requests)}".encode()).hexdigest().upper().lower()
                self.sessions.add(session)
                headers["set-cookie"] = f"JSESSIONID={session}; Path={BASE}; HttpOnly"
            return httpx.Response(200, text=self._shell(
                f'<h1>POMŮCKA</h1><a href="{BASE}/BookVolume.action;jsessionid={session}?browseAid=&amp;entity={entity(4137)}">Listování</a>'), headers=headers)
        expired = httpx.Response(200, text=self._shell('<div class="errorPage"><h1>Page has Expired</h1></div>'), headers=html_headers)
        if not session:
            return expired
        if action == "BookVolume.action":
            if query.get("entity") != [FUND_ENTITY] or query.get("browseAid") != [""]:
                return httpx.Response(500, text="<html>error</html>", headers={"content-type": "text/html"})
            self.volume_open.add(session)
            return httpx.Response(200, text=self._volume(session, 0), headers=html_headers)
        if action == "PaginatorBookVolume.action":
            token = (query.get("_sourcePage") or [""])[0]
            if self.tokens.get(token) != session or (self.single_use_tokens and token in self.used):
                return expired
            self.used.add(token)
            if session not in self.volume_open:
                return httpx.Response(500, text="<html>stack trace</html>", headers={"content-type": "text/html"})
            if "rowTxt" in query:
                start = (int(query["rowTxt"][0]) - 1) // PAGE * PAGE
            else:
                start = int(query["row"][0])
            if not 0 <= start < TOTAL:
                return httpx.Response(500, text="<html>stack trace</html>", headers={"content-type": "text/html"})
            return httpx.Response(200, text=self._volume(session, start), headers=html_headers)
        if action == "Zoomify.action":
            m = re.search(r"\((\d+)\)\)$", (query.get("entityRef") or [""])[0])
            entity_id = int(m[1]) if m else 0
            if entity_id not in self.books or self.books[entity_id][3] == 0:
                return httpx.Response(500, text="<html>error</html>", headers={"content-type": "text/html"})
            # The real viewer ignores scanIndex and always opens the first scan.
            return httpx.Response(200, text=self._viewer(session, entity_id, 1), headers=html_headers)
        if action == "PaginatorMedia.action":
            token = (query.get("_sourcePage") or [""])[0]
            if self.tokens.get(token) != session or (self.single_use_tokens and token in self.used):
                return expired
            self.used.add(token)
            entity_id = self.viewer.get(session)
            row = int(query["row"][0])
            if entity_id is None or not 0 <= row < self.books[entity_id][3]:
                return httpx.Response(500, text="<html>stack trace</html>", headers={"content-type": "text/html"})
            return httpx.Response(200, text=self._viewer(session, entity_id, row + 1), headers=html_headers)
        return httpx.Response(404)
