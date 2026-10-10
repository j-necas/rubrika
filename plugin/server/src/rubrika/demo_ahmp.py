"""Entirely synthetic Bach catalogue shaped like the Prague City Archives' Archivní katalog (tree, permalink viewer,
session cookie, one-time paging tokens) plus Zoomify scans. NEVER real archive data; not shipped in the bundle."""
from __future__ import annotations

import hashlib
import html
import math
import re
from urllib.parse import parse_qs, quote

import httpx

from .bach import AHMP_SITE
from .demo_zoomify import TILE, ZoomifyArchive, tier_sizes

SITE = AHMP_SITE
PAGE = 9
ROOT = 900001                       # "katolické"
INNER, OUTER = 900002, 900003
DISTRICT_1, DISTRICT_2, VYSEHRAD = 900010, 900011, 900012
TESTA, MARIE = 900020, 900021       # parishes of district 1 (Testa has more than one page of books)
DEMETA = 900030                     # first of eleven parishes of district 2
DEMOVICE, TESTOVICE, TESTOVICE_SUB = 900100, 900101, 900110
GATE_XID = "00000000000000000000000000000ABC"
PRAGUE_SERIES, FOREIGN_SERIES = 168668, 290432           # the two Soupis lists (public catalogue node numbers)
CENSUS_ROOT, CENSUS_1869, CENSUS_1880, CENSUS_1900 = 910100, 910101, 910102, 910103
NOVE_MESTO, JOSEFOV, EMPTY_DISTRICT = 910110, 910111, 910112

# node id -> (name, [child ids]); a node without children holds books
TREE: dict[int, tuple[str, list[int]]] = {
    ROOT: ("katolické", [INNER, OUTER]),
    INNER: ("vnitřní obvody", [DISTRICT_1, DISTRICT_2, VYSEHRAD]),
    DISTRICT_1: ("Zkušov I - Horní Město", [TESTA, MARIE]),
    DISTRICT_2: ("Zkušov II - Dolní Město", [DEMETA + i for i in range(11)]),
    VYSEHRAD: ("Zkušov III - Vyšehrádek (VYŠ)", []),
    TESTA: ("Kostel sv. Testa (TST)", []),
    MARIE: ("Kostel Panny Marie Demonstrační (PMD)", []),
    OUTER: ("vnější obvody", [DEMOVICE, TESTOVICE] + [900200 + i for i in range(8)]),
    DEMOVICE: ("Demovice (DMV) - kostel sv. Prubora", []),
    TESTOVICE: ("Testovice", [TESTOVICE_SUB]),
    TESTOVICE_SUB: ("Testovice (TES) - kostel sv. Zkušebního", []),
}
for i in range(11):
    TREE[DEMETA + i] = ("Kostel sv. Demeta (DEM)" if i == 0 else f"Kostel sv. Výplně {i} (VY{i:02d})", [])
for i in range(8):
    TREE[900200 + i] = (f"Výplňová {i} (VP{i}) - kostel sv. Výplně", [])

# node id -> [(signature label, scans)]; TESTA has more books than one page holds
BOOKS: dict[int, list[tuple[str, int]]] = {
    TESTA: [("TST N1 • 1780-1800", 3), ("TST N2 • 1800-1820", 2), ("TST N3", 2), ("TST O1 • 1780-1810", 2),
            ("TST O2 • 1811-1850", 2), ("TST Z1 • 1780-1820", 2), ("TST Z2 • 1821-1850", 2), ("TST i1 • 1780-1820", 2),
            ("TST i2 • 1780-1900", 2), ("TST NOZ4 • 1700-1779", 3), ("TST X9 • 1700-1710", 1), ("TST N10 • 1851-1870", 2)],
    MARIE: [("PMD N1 • 1790-1800", 2), ("PMD O1 • 1790-1800", 1), ("PMD Z1 • 1790-1800", 1)],
    VYSEHRAD: [("VYŠ N1 • 1800-1810", 2), ("VYŠ Z1 • 1800-1810", 1)],
    DEMETA: [("DEM N1 • 1840-1850", 2)],
    DEMOVICE: [("DMV N1 • 1840-1845", 2), ("DMV N2 • 1846-1860", 2), ("DMV O1 • 1840-1860", 1), ("DMV Z1 • 1840-1860", 1)],
    TESTOVICE_SUB: [("TES N1 • 1830-1850", 1)],
    TESTOVICE: [("TSV N1 • 1860-1870", 1)],          # a container that also holds a book of its own
    **{DEMETA + i: [(f"VY{i:02d} N1 • 1850-1860", 1)] for i in range(1, 11)},
    **{900200 + i: [(f"VP{i} N1 • 1850-1860", 1)] for i in range(1, 8)},      # 900200 stays an empty parish
}


# ---- the other branches of the Sbírka matrik: non-Catholic churches, civil registers, death protocols (own roots) ----
NONCATH, EVANGELIC, EVANGELIC_TESTA, PRAVOSLAVNA, PRAVOSLAVNA_SUB, OLD_CATHOLIC = 900300, 900301, 900302, 900310, 900311, 900320
CIVIL, CIVIL_CENTRAL, CIVIL_OFFICES, CIVIL_OFFICE_ZK, CIVIL_EMPTY = 900400, 900401, 900410, 900411, 900420
DEATH_PROTOCOLS = 900500
EXTRA_ROOTS = {NONCATH: "Nekatolické matriky", CIVIL: "Civilní matriky Magistrátu", DEATH_PROTOCOLS: "Úmrtní protokoly"}
HIDDEN_CHILDREN = {PRAVOSLAVNA}      # a container with books of its own lists its child nodes in the hidden list (as the real catalogue does)
TREE.update({
    NONCATH: ("nekatolické", [EVANGELIC, PRAVOSLAVNA, OLD_CATHOLIC]),
    EVANGELIC: ("Českobratrská církev evangelická (ČCE)", [EVANGELIC_TESTA]),
    EVANGELIC_TESTA: ("ČCE (Ukázkov) u sv. Evangelisty (EVT)", []),
    PRAVOSLAVNA: ("Pravoslavná církev v Praze", [PRAVOSLAVNA_SUB]),
    PRAVOSLAVNA_SUB: ("Česká pravoslavná církev (ČPRAV) u sv. Pravoslavného, Ukázkov - Horní Město", []),
    OLD_CATHOLIC: ("Starokatolická církev (STK)", []),
    CIVIL: ("civilní", [CIVIL_CENTRAL, CIVIL_OFFICES, CIVIL_EMPTY]),
    CIVIL_CENTRAL: ("Magistrát hlavního města Ukázkova - centrální úřad (MAG)", []),
    CIVIL_OFFICES: ("Magistrátní úřadovny", [CIVIL_OFFICE_ZK]),
    CIVIL_OFFICE_ZK: ("Ukázkov (MGZK)", []),
    CIVIL_EMPTY: ("Standesamt Ukázkov (STA)", []),
    DEATH_PROTOCOLS: ("Úmrtní protokoly", []),
})
BOOKS.update({
    EVANGELIC_TESTA: [("EVT N1O1Z1 • 1783-1836", 3), ("EVT N2 • 1802-1826", 2), ("EVT O2 • 1827-1842", 2), ("EVT Z3 • 1843-1870", 2)],
    PRAVOSLAVNA: [("PRAV N1O1Z1 • 1875-1908", 2), ("PRAV N2 • 1909-1915", 1)],
    PRAVOSLAVNA_SUB: [("ČPRAV N1 • 1930-1940", 1)],
    OLD_CATHOLIC: [("STK N1 • 1900-1917", 1), ("STKZ O1 • 1940-1947", 1)],
    CIVIL_CENTRAL: [("MAG N1 • 1872-1908", 3), ("MAG O1 • 1872-1908", 2), ("MAG Z1 • 1872-1908", 2)],
    CIVIL_OFFICE_ZK: [("MGZK O1 • 1927-1927", 1), ("MGZK N1 • 1927-1928", 1)],
    DEATH_PROTOCOLS: [("UP-IZ 1a • 1924-1939", 2)],
})


# ---- census: root › year › district (a district lists one row per house number, 10125) -------------------------
TREE[CENSUS_ROOT] = ("Spisy (synthetic)", [CENSUS_1869, CENSUS_1880, CENSUS_1900])
TREE[CENSUS_1869] = ("1869", [])
TREE[CENSUS_1880] = ("1880", [NOVE_MESTO, JOSEFOV, EMPTY_DISTRICT])
TREE[CENSUS_1900] = ("1900", [])
TREE[NOVE_MESTO] = ("II-Zkušov Nové Město", [])
TREE[JOSEFOV] = ("V-Zkušov Josefov", [])
TREE[EMPTY_DISTRICT] = ("VI-Zkušov Prázdnice", [])


def _houses(low: int, high: int, skip=()):
    return [n for n in range(low, high + 1) if n not in skip]


HOUSES = {NOVE_MESTO: _houses(1, 44, skip=(9, 13, 14, 30)), JOSEFOV: _houses(1, 7)}
CENSUS_ROWS = {node: [(f"138 • 1880 • {TREE[node][0].split('-', 1)[1]}, čp. {n}", 2 if n % 5 == 0 else 1) for n in houses]
               for node, houses in HOUSES.items()}


# ---- Soupis: flat lists sorted the way the archive sorts them (lemma order below is the archive's, written by hand) ----
# (lemma, displayed surname variants); the order of the lemmas is Czech collation with W = V, Š after S, Ch after H
SOUPIS_LEMMAS = ["Adler", "Bartoš", "Bauer", "Cibulka", "Čapek", "Dvorak", "Dvořák", "Hora", "Chalupa", "Kučera", "Novák", "Nový",
                 "Pospíšil", "Rada", "Řezáč", "Sedlák", "Simek", "Svoboda", "Šimek", "Šrámek", "Tesař", "Wagner", "Vávra", "Weiss",
                 "Wolf", "Vondrák", "Žák"]
SOUPIS_VARIANTS = {"Novák": ["Novák", "Novák", "Nowak", "Nowák", "Novak", "Nováková", "Novák-ová"],
                   "Šimek": ["Šimek", "Schimek"], "Wagner": ["Wagner", "Vagner"], "Chalupa": ["Chalupa", "Chalupová"],
                   "Vávra": ["Vávra", "Wawra"], "Dvořák": ["Dvořák", "Dwořák"], "Čapek": ["Čapek", "Czapek"]}
SOUPIS_GIVEN = ["Anna", "Emil", "František", "Jan", "Josef", "Joseph", "Karel", "Marie", "Václav", "Wenzl"]
_GIVEN_ORDER = {g: i for i, g in enumerate(SOUPIS_GIVEN)}      # the archive's order of first names (Josef before Joseph)


def _soupis(series: int) -> list[tuple[str, str, str, int, str]]:
    """(surname, given name, birth year label, scans, lemma) in archive order; the foreign series has fewer, other people."""
    rows = []
    for li, lemma in enumerate(SOUPIS_LEMMAS):
        many = lemma in ("Novák", "Dvořák", "Kučera") if series == PRAGUE_SERIES else lemma == "Adler"
        people = []
        for gi, given in enumerate(SOUPIS_GIVEN):
            n = (7 if lemma == "Novák" else 4 if lemma == "Dvořák" else 2) if many else (1 if (li + gi) % 4 == 0 else 0)
            if lemma in ("Dvorak", "Simek", "Dvořák", "Šimek") and given in ("Josef", "Joseph", "Jan"):
                n = 2                                        # the same name with and without diacritics: two places in the list
            if (lemma, given) == ("Kučera", "Josef") and series == PRAGUE_SERIES:
                n = 240                                      # a very common name: three sheets a year over eighty years
            for k in range(n):
                variants = SOUPIS_VARIANTS.get(lemma, [lemma])
                year = 1885 - 3 * k - gi if n < 100 else 1900 - k // 3
                label = "neuveden" if (lemma, given, k) == ("Dvořák", "Anna", 0) and series == PRAGUE_SERIES else str(year)
                people.append((variants[(li + gi + k) % len(variants)], given, label, 2 if k == 3 else 1, lemma, gi, year))
        people.sort(key=lambda p: (p[5], -p[6]))
        rows += [p[:5] for p in people]
    return rows


SOUPIS = {series: _soupis(series) for series in (PRAGUE_SERIES, FOREIGN_SERIES)}
SOUPIS_NAMES = {PRAGUE_SERIES: "Pražští příslušníci", FOREIGN_SERIES: "Cizí příslušníci"}
SOUPIS_ROOT = 168667


def soupis_label(series: int, i: int) -> str:
    surname, given, year, scans, _ = SOUPIS[series][i]
    return f"{i % 500 + 1} • {year} • {surname}, {given}"


def flat_rows(node: int) -> list[tuple[str, int]]:
    if node in SOUPIS:
        return [(soupis_label(node, i), SOUPIS[node][i][3]) for i in range(len(SOUPIS[node]))]
    return CENSUS_ROWS.get(node, [])


def count(node: int) -> int:
    if node in SOUPIS or node in CENSUS_ROWS:
        return len(flat_rows(node))
    return len(BOOKS.get(node, [])) + sum(count(c) for c in TREE[node][1])


def xid_of(node: int, signature: str) -> str:
    return hashlib.md5(f"SYNTHETIC|{node}|{signature}".encode()).hexdigest().upper()


def root_of(xid: str, scan: int, folder: str = "NAD-156") -> str:
    return (f"https://images.ahmp.cz/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/{folder}/dao/images/0001/"
            + hashlib.md5(f"SYNTHETIC|{xid}|{scan}".encode()).hexdigest() + ".jpg")


def scan_size(scan: int) -> tuple[int, int]:
    return 1000 + 41 * scan, 760 + 23 * scan


def entity(node: int) -> str:
    return quote(f"(^n)(((localArchiv,^n,hot_)(unidata))({node}))", safe="")


def flat_xid(node: int, i: int, label: str) -> str:
    return xid_of(node, f"{i}|{label}")


def folder_of(node: int) -> str:
    return "spisy" if node in CENSUS_ROWS else "NAD-4" if node in SOUPIS else "NAD-156"


def _book_index() -> dict[str, tuple[int, str, int]]:
    index = {xid_of(n, label): (n, label, scans) for n, rows in BOOKS.items() for label, scans in rows}
    for node in (*SOUPIS, *CENSUS_ROWS):
        for i, (label, scans) in enumerate(flat_rows(node)):
            index[flat_xid(node, i, label)] = (node, label, scans)
    return index


class AhmpArchive:
    """Serves katalog.ahmp.cz (with a real session), images.ahmp.cz (Zoomify) and both robots.txt files."""

    def __init__(self, robots="User-agent: *\nDisallow: /\n"):
        self.robots = robots
        self.requests: list[tuple[str, str, str | None]] = []       # (method, url, cookie header)
        self.sessions: set[str] = set()
        self.tokens: dict[str, tuple[str, int]] = {}                # paging token -> (session, row)
        self.last_node: dict[str, int] = {}                         # session -> node whose list it is paging
        self.books = _book_index()
        self.zoomify: dict[str, ZoomifyArchive] = {}
        self.flat_tokens: dict[str, str] = {}                       # pager form token -> session
        self.forbidden_hits: list[str] = []                         # requests that reached a forbidden action
        self.gate = False                                           # answer everything with a challenge page
        self.status: dict[str, int] = {}                            # path -> forced status
        self.transport = httpx.MockTransport(self.handle)

    # -- images ---------------------------------------------------------------------------------------------
    def image_archive(self, root: str) -> ZoomifyArchive | None:
        for xid, (node, label, scans) in self.books.items():
            for scan in range(1, scans + 1):
                if root_of(xid, scan, folder_of(node)) == root:
                    if root not in self.zoomify:
                        w, h = scan_size(scan)
                        self.zoomify[root] = ZoomifyArchive(w, h, root=root, robots=self.robots)
                    return self.zoomify[root]
        return None

    def image_requests(self) -> list[str]:
        return [u for m, u, c in self.requests if "images.ahmp.cz" in u]

    def catalog_requests(self) -> list[str]:
        return [u for m, u, c in self.requests if "katalog.ahmp.cz" in u]

    # -- HTML -----------------------------------------------------------------------------------------------
    def _token(self, session: str, row: int) -> str:
        token = hashlib.sha256(f"{session}|{row}|{len(self.tokens)}".encode()).hexdigest()[:40] + "_" + str(len(self.tokens))
        self.tokens[token] = (session, row)
        return token

    def _shell(self, body: str) -> str:
        return ('<!DOCTYPE html><html lang="cs"><head><meta charset="utf-8"><title>Archivní katalog</title></head>'
                "<body><header><h1>Archivní katalog</h1></header>" + body + "</body></html>")

    def _ancestors(self, node: int) -> list[int]:
        path = [node]
        while True:
            parent = next((p for p, (_, kids) in TREE.items() if path[0] in kids), None)
            if parent is None:
                return path
            path.insert(0, parent)

    def _node_page(self, session: str, node: int, first: int) -> str:
        self.last_node[session] = node
        name, kids = TREE[node]
        own = [label for label, _ in BOOKS.get(node, [])]
        items = [*kids, *own]
        window = list(range(first, min(first + PAGE, len(items))))
        tree = ""
        for i, n in enumerate(self._ancestors(node)):
            link = f'<a href="{SITE.base}/NavigBean.action?entity={entity(n)}" title="{count(n)} záznamů">{html.escape(TREE[n][0])}</a>'
            tree += (f'<li style="margin-left:{20 * i}px" class="navigatorLine treeComponent"><i class="icon-plus icon"></i>'
                     + (f"<strong>{link}</strong>" if n == node else link) + "</li>")
        children = ""
        shown = [i for i in window if i < len(kids)]
        if node in HIDDEN_CHILDREN and shown:
            for i in shown:
                c = kids[i]
                children += (f'<li class="navigatorLine treeComponent" style="margin-left:100px"><i class="icon-minus icon"></i>'
                             f'<a href="{SITE.base}/NavigBean.action?entity={entity(c)}" title="{count(c)} záznamů">{html.escape(TREE[c][0])}</a></li>')
            children += ('<li class="navigatorLine treeComponent navigatorLineTerminal" style="margin-left:100px">\n</li>'
                         * len([i for i in window if i >= len(kids)]))
            children = f'<ul class="category displayNone">{children}</ul>'
        elif kids and shown:
            for i in shown:
                c = kids[i]
                icon = "icon-plus" if TREE[c][1] else "icon-minus"
                children += (f'<li class="navigatorLine treeComponent"><i class="{icon} icon"></i>'
                             f'<a href="{SITE.base}/NavigBean.action?entity={entity(c)}" title="{count(c)} záznamů">'
                             f"{html.escape(TREE[c][0])}</a></li>")
            children = f'<ul class="category ">{children}</ul>'
        else:
            children = '<ul class="category  displayNone"></ul>'
        rows = ""
        if True:
            for i in [i for i in window if i >= len(kids)]:
                label, scans = BOOKS[node][i - len(kids)]
                xid = xid_of(node, label)
                rows += ('<div class="navigatorLine listArticle navigatorLineTerminal">'
                         f'<a href="{SITE.base}/Zoomify.action?xid={xid}&amp;entityType=10099&amp;entityRef={entity(node + 1000 + i)}&amp;scanIndex=0"'
                         f' title="Zobrazit v plné kvalitě {scans} obrázků" class="attachments" target="repo-zoomify"><i class="icon icon-image"></i></a>'
                         f'<a href="{SITE.base}/NavigBean.action?eventNavigPager=&amp;_sourcePage=x&amp;rowPg={i}">{html.escape(label)}</a>'
                         '<span class="info infoType">Archiválie</span></div>')
        total = len(items)
        if total > PAGE:
            def link(row, icon):
                return (f'<div class="pageIco"><a href="{SITE.base}/PaginatorNavig.action?_sourcePage={self._token(session, row)}&amp;row={row}">'
                        f'<i class="{icon} icon link"></i></a></div>')
            pager = ('<div id="paginatiorBlock"><div class="pageNum"><form method="GET" action="' + SITE.base + '/PaginatorNavig.action">'
                     f'<span class="setRow">{first + 1}\n - \n{first + PAGE}</span><span class="slash">/</span><span>1\n-\n{total}</span></form></div>'
                     + (link(first + PAGE, "icon-forward3") if first + PAGE < total else "") + "</div>")
        elif total:
            pager = f"<span>Nalezeny <b>{total}</b> záznamy</span>"
        else:
            pager = "<span>Nalezeno <b>0</b> záznamů</span>"
        return self._shell(
            f'<div class="searchNavig row"><div class="categoryBlock"><ul class="category">{tree}</ul>{children}</div></div>'
            f'<div class="contentBlock"><div class="contentArticle row"><h1>{html.escape(name)}</h1></div>'
            f'<div class="paginationBlock">{pager}</div><div class="contentArticle">{rows}</div></div>')

    # -- flat lists (Soupis, census districts): a pager form with two tokens and a "go to row" box ------------
    def _flat_token(self, session: str) -> str:
        token = hashlib.sha256(f"F|{session}|{len(self.flat_tokens)}".encode()).hexdigest() + "_" + str(len(self.flat_tokens))
        self.flat_tokens[token] = session
        return token

    def _flat_page(self, session: str, node: int, first: int) -> str:
        """`first` is the 0-based index of the first row shown; nine rows per page, the last page is clipped."""
        self.last_node[session] = node
        rows = flat_rows(node)
        window = list(range(first, min(first + PAGE, len(rows))))
        chain = self._ancestors(node) if node in TREE else [SOUPIS_ROOT, node]
        names = {SOUPIS_ROOT: "Soupis pražského obyvatelstva 1830-1910 (1920)", **{n: TREE[n][0] for n in TREE}, **SOUPIS_NAMES}
        tree = ""
        for i, n in enumerate(chain):
            link = f'<a href="{SITE.base}/NavigBean.action?entity={entity(n)}" title="{count(n) if n != SOUPIS_ROOT else 1} záznamů">{html.escape(names[n])}</a>'
            tree += (f'<li style="margin-left:{20 * i}px" class="navigatorLine treeComponent"><i class="icon-plus icon"></i>'
                     + (f"<strong>{link}</strong>" if n == node else link) + "</li>")
        body = ""
        for i in window:
            label, scans = rows[i]
            xid = flat_xid(node, i, label)
            kind = 10125 if node in CENSUS_ROWS else 10120
            noun = "obrázek" if scans == 1 else "obrázky"
            body += ('<div class="navigatorLine listArticle navigatorLineTerminal  ">'
                     f'<a href="{SITE.base}/Zoomify.action?xid={xid}&amp;entityType={kind}&amp;entityRef={entity(node + 100000 + i)}&amp;scanIndex=0"'
                     f' title="Zobrazit v plné kvalitě {scans} {noun}" class="attachments" target="repo-zoomify"><i class="icon icon-image"></i></a>'
                     f'<a href="{SITE.base}/NavigBean.action?eventNavigPager=&amp;_sourcePage=x&amp;rowPg={i}">{html.escape(label)}</a>'
                     '<span class="info infoType">Archiválie</span></div>')
        total = len(rows)
        form = (f'<div class="pageNum"><form method="GET" action="{SITE.base}/PaginatorNavig.action">'
                f'<input size="6" name="rowTxt" type="text" class="inputForm" value="{first + 5}" />'
                f'<span class="setRow">{first + 1}\n - \n{first + PAGE}</span><span class="slash">/</span><span>\n1\n-\n{total}\n</span>'
                f'<div style="display: none;"><input type="hidden" name="_sourcePage" value="{self._flat_token(session)}" />'
                f'<input type="hidden" name="__fp" value="{self._flat_token(session)}" /></div></form></div>')
        search = ('<form method="POST" action="/pragapublica/SearchBean.action"><div style="display: none;">'
                  f'<input type="hidden" name="_sourcePage" value="{self._flat_token(session)}" /></div></form>')
        pager = f'<div id="paginatiorBlock">{form}</div>' if total > PAGE else f"<span>Nalezeny <b>{total}</b> záznamy</span>"
        return self._shell(
            search + f'<div class="searchNavig row"><div class="categoryBlock"><ul class="category">{tree}</ul>'
            '<ul class="category  displayNone"></ul></div></div>'
            f'<div class="contentBlock"><div class="contentArticle row"><h1>{html.escape(names[node])}</h1></div>'
            f'<div class="paginationBlock">{pager}</div><div class="contentArticle">{body}</div></div>')

    def _detail(self, xid: str) -> str:
        node, label, scans = self.books[xid]
        order, _, rest = label.partition(" • ")
        return self._shell(
            f'<div class="contentBlock"><h1>{html.escape(label)}</h1>'
            f'<div class="fields"><span>Typ záznamu:</span> Archiválie <span>Pořadové číslo:</span> {order} '
            f'<span>Ukládací jednotka:</span> karton/{200 + int(xid[:2], 16) % 50} <span>Zobrazeno:</span> 3x</div></div>')

    def _viewer(self, xid: str, scan: int) -> str:
        node, label, scans = self.books[xid]
        root = root_of(xid, scan, folder_of(node))
        w, h = scan_size(scan)
        tiles = sum(math.ceil(a / TILE) * math.ceil(b / TILE) for a, b in tier_sizes(w, h))
        props = f"<IMAGE_PROPERTIES WIDTH='{w}' HEIGHT='{h}' NUMTILES='{tiles}' NUMIMAGES='1' VERSION='1.8' TILESIZE='256' DPIX='300' DPIY='300' />"
        return self._shell(
            '<div id="page"><section id="content"><header id="header"><div id="headerTopPanel">'
            f'<a class="text headline" href="https://katalog.ahmp.cz{SITE.base}/permalink?xid={xid}" title="{html.escape(label)}">{html.escape(label)}</a></div>'
            f'<div id="headerBottomPanel"><div id="paginatiorBlock"><div class="pageNum">{scan} - {scan} | {scans}</div>'
            f'<form method="GET" action="{SITE.base}/PaginatorMedia.action" id="getScan"><input size="6" name="rowTxt" type="text" value="{scan}" />'
            f'<span class="slash">|</span><span class="number">{scans}</span></form></div></div></header></section></div>'
            "<script>\n"
            f'    var zoomifyImgPath = "{root}";\n'
            "    // alert(zoomifyImgPath);\n"
            f'    //var imgProperties = "zSkinPath=x&zImageProperties={props}";\n'
            f'    var imgProperties = "zSkinPath=js/Assets/Skins/Light&" + addParams + "&zImageProperties={props}&zRotationVisible=1";\n'
            '    Z.showImage("showZoomify", zoomifyImgPath, imgProperties);\n</script>')

    # -- transport ------------------------------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie")
        self.requests.append((request.method, str(request.url), cookie))
        host, path = request.url.host, request.url.path
        query = parse_qs(request.url.query.decode(), keep_blank_values=True)
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        if host == "images.ahmp.cz":
            m = re.match(r"(/mrimage/.+?\.jpg)/", path)
            archive = self.image_archive("https://images.ahmp.cz" + m[1]) if m else None
            return archive.handle(request) if archive else httpx.Response(404)
        if host != "katalog.ahmp.cz" or request.method != "GET":
            return httpx.Response(404)
        if self.gate:
            return httpx.Response(200, text="<html><title>Just a moment...</title><body>Verifying your browser <form action='/human/challenge'></form></body></html>",
                                  headers={"content-type": "text/html"})
        if path in self.status:
            return httpx.Response(self.status[path], text="<html>x</html>", headers={"content-type": "text/html"})
        if path.endswith(("Zoomify.action", "BasketBean.action", "SearchBean.action")) or "login" in path:
            self.forbidden_hits.append(str(request.url))
            return httpx.Response(403)
        session = None
        if cookie:
            m = re.fullmatch(r"JSESSIONID=([0-9a-f]+)", cookie)
            session = m[1] if m and m[1] in self.sessions else None
        expired = httpx.Response(200, text=self._shell('<div class="errorPage"><h1>Platnost stránky vypršela</h1></div>'),
                                 headers={"content-type": "text/html;charset=UTF-8"})
        if path == SITE.base + "/permalink":
            xid = query["xid"][0].upper()
            headers = {"content-type": "text/html;charset=UTF-8"}
            if not session:
                session = hashlib.md5(f"s{len(self.sessions)}".encode()).hexdigest()
                self.sessions.add(session)
                headers["set-cookie"] = f"JSESSIONID={session}; Path={SITE.base}; Secure; HttpOnly"
            if "scan" in query:
                if xid not in self.books or not 1 <= int(query["scan"][0]) <= self.books[xid][2]:
                    return httpx.Response(500, text="<html>error</html>", headers={"content-type": "text/html"})
                return httpx.Response(200, text=self._viewer(xid, int(query["scan"][0])), headers=headers)
            if xid in self.books and self.books[xid][0] in SOUPIS:
                return httpx.Response(200, text=self._detail(xid), headers=headers)
            return httpx.Response(200, text=self._shell("<h1>Sbírka matrik</h1>"), headers=headers)
        if not session:
            return expired
        if path == SITE.base + "/NavigBean.action":
            m = re.search(r"\((\d+)\)\)$", query["entity"][0])
            node = int(m[1]) if m else 0
            if node in SOUPIS or node in CENSUS_ROWS:
                return httpx.Response(200, text=self._flat_page(session, node, 0), headers={"content-type": "text/html;charset=UTF-8"})
            if node not in TREE:
                return httpx.Response(200, text=self._shell("<h1>?</h1>"), headers={"content-type": "text/html"})
            return httpx.Response(200, text=self._node_page(session, node, 0), headers={"content-type": "text/html;charset=UTF-8"})
        if path == SITE.base + "/PaginatorNavig.action" and "rowTxt" in query:
            if self.flat_tokens.get(query["_sourcePage"][0]) != session or self.flat_tokens.get(query["__fp"][0]) != session:
                return expired
            last = self.last_node.get(session)
            if last not in SOUPIS and last not in CENSUS_ROWS:
                return expired
            first = max(0, int(query["rowTxt"][0]) - 6)
            return httpx.Response(200, text=self._flat_page(session, last, first), headers={"content-type": "text/html;charset=UTF-8"})
        if path == SITE.base + "/PaginatorNavig.action":
            owner, row = self.tokens.get(query["_sourcePage"][0], (None, None))
            if owner != session or row != int(query["row"][0]):
                return expired
            last = self.last_node.get(session)
            return httpx.Response(200, text=self._node_page(session, last, row), headers={"content-type": "text/html;charset=UTF-8"})
        return httpx.Response(404)
