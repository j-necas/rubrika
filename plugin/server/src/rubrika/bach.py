"""Shared adapter for catalogues built on the Bach systems "Archivní katalog" (AHMP now, the National Archives next).

The application is stateful (Stripes framework, JSESSIONID, one-time paging tokens) and answers HTML only. Everything
here is conservative: a parser that does not recognise the structure raises PARSE_ERROR and never returns an empty
result; source text is data, JavaScript is never evaluated."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .errors import ResearchError
from .images import parse_zoomify
from .network import SiteSession


@dataclass(frozen=True)
class BachSite:
    code: str            # Rubrika archive code
    host: str
    base: str            # application path, e.g. "/pragapublica"
    images_host: str
    opener_xid: str      # a permalink whose only job is to make the server issue a session cookie
    entity_prefix: str = "(^n)(((localArchiv,^n,hot_)(unidata))("
    entity_suffix: str = "))"

    @property
    def entity_re(self) -> re.Pattern:
        # The catalogue writes the root as (0)...(1) and every other node as (^n)...(<id>).
        return re.compile(r"\((?:\^n|0)\)" + re.escape(self.entity_prefix[len("(^n)"):]) + r"(\d{1,9})" + re.escape(self.entity_suffix))

    def node_url(self, node_id: int) -> str:
        return f"https://{self.host}{self.base}/NavigBean.action?entity=" + quote(f"{self.entity_prefix}{int(node_id)}{self.entity_suffix}", safe="")

    def permalink_url(self, xid: str, scan: int | None = None) -> str:
        if not re.fullmatch(r"[0-9A-Fa-f]{32}", xid):
            raise ResearchError("INVALID_SOURCE", "Neplatný trvalý odkaz.")
        return f"https://{self.host}{self.base}/permalink?xid={xid}" + (f"&scan={int(scan)}" if scan else "")

    def opener_url(self) -> str:
        return self.permalink_url(self.opener_xid)

    def session(self, sid: str, owner: str) -> SiteSession:
        return SiteSession(sid, owner, self.host, self.opener_url())


AHMP_SITE = BachSite("ahmp", "katalog.ahmp.cz", "/pragapublica", "images.ahmp.cz",
                     opener_xid="48b199810beb15e188a7bdec56417e5f")


# ---------------------------------------------------------------- parsers

@dataclass
class NodeRef:
    id: int
    name: str
    count: int | None
    expandable: bool     # the tree shows a "+" (child nodes) instead of a "-" (records only)


@dataclass
class BookRow:
    xid: str
    entity_id: int
    label: str
    signature: str
    years: str | None
    scans: int
    digitized: bool = True
    entity_type: int = 10099     # 10099 registers, 10120 Soupis sheets, 10125 census houses


ROW_TYPES = {"10099": 10099, "10120": 10120, "10125": 10125}
_TOKEN = re.compile(r"[A-Za-z0-9_=-]{16,600}")


@dataclass(frozen=True)
class JumpForm:
    """The pager's "go to row" box: a plain GET form. Both tokens come from the response and belong to the session."""
    source_page: str
    fp: str


@dataclass
class NodePage:
    id: int
    name: str
    ancestors: list[tuple[int, str]]
    children: list[NodeRef]
    books: list[BookRow]
    warnings: list[str]
    total: int
    first: int | None
    last: int | None
    next_url: str | None
    jump: JumpForm | None = None


def _bad(message: str, **details) -> ResearchError:
    return ResearchError("PARSE_ERROR", message, **details)


def is_expired(html: str) -> bool:
    # AHMP answers in Czech, the National Archives in English; both are a 200 page without the requested content.
    return "Platnost stránky vypršela" in html or "<h1>Page has Expired</h1>" in html


def _number(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def _entity_id(site: BachSite, href: str) -> int:
    p = urlsplit(href)
    if p.path != site.base + "/NavigBean.action":
        raise _bad("Odkaz na uzel katalogu má neznámý tvar.")
    values = parse_qs(p.query, keep_blank_values=True)
    if list(values) != ["entity"] or len(values["entity"]) != 1:
        raise _bad("Odkaz na uzel katalogu má neznámé parametry.")
    m = site.entity_re.fullmatch(values["entity"][0])
    if not m:
        raise _bad("Odkaz na uzel katalogu má neznámý tvar entity.")
    return int(m[1])


def _count(title: str | None) -> int | None:
    m = re.match(r"\s*(\d[\d\s ]*)\s+záznam", title or "")
    return _number(m[1]) if m else None


def _next_link(site: BachSite, block, page_url: str) -> str | None:
    """The forward arrow of the pager; only a link straight back into the same application is followed."""
    for icon in block.select("i.icon-forward3"):
        anchor = icon.find_parent("a")
        if anchor is None:
            continue
        target = urljoin(page_url, anchor.get("href", ""))
        p = urlsplit(target)
        values = parse_qs(p.query, keep_blank_values=True)
        if (p.scheme != "https" or p.hostname != site.host or p.path != site.base + "/PaginatorNavig.action"
                or sorted(values) != ["_sourcePage", "row"] or any(len(v) != 1 for v in values.values())):
            raise _bad("Stránkování katalogu opouští očekávaný tvar.")
        return target
    return None


def _jump_form(pager) -> JumpForm | None:
    form = pager.select_one("div.pageNum form") if pager else None
    if form is None:
        return None
    tokens = {}
    for name in ("_sourcePage", "__fp"):
        inputs = form.select(f'input[name="{name}"]')
        value = inputs[0].get("value", "") if len(inputs) == 1 else ""
        if not _TOKEN.fullmatch(value):
            return None
        tokens[name] = value
    return JumpForm(tokens["_sourcePage"], tokens["__fp"])


def jump_url(site: BachSite, row: int, form: JumpForm) -> str:
    """The URL that shows the list around row `row` (the window starts about five rows earlier)."""
    if not isinstance(row, int) or not 1 <= row <= 999999:
        raise ResearchError("INVALID_ARGUMENT", "Číslo řádku katalogu je mimo rozsah.")
    return (f"https://{site.host}{site.base}/PaginatorNavig.action?rowTxt={row}"
            f"&_sourcePage={quote(form.source_page, safe='')}&__fp={quote(form.fp, safe='')}")


def parse_node(html: str, site: BachSite, page_url: str, expected_id: int, extra_rows: dict[int, int] | None = None) -> NodePage:
    """One page of a tree node: child nodes (sidebar tree) or book rows (content), plus the pager. Rows other than register
    books (10099) are accepted only below the nodes named in `extra_rows` (ancestor node id → entity type), e.g. the AHMP
    Soupis (10120) or census (10125); anywhere else they are a PARSE_ERROR."""
    if is_expired(html):
        raise ResearchError("SOURCE_UNAVAILABLE", "Relace katalogu vypršela.", reason="session_expired")
    soup = BeautifulSoup(html, "lxml")
    lists = soup.select("div.categoryBlock > ul.category")
    heading = soup.select_one("div.contentBlock h1")
    if not lists or heading is None or not heading.get_text(strip=True):
        raise _bad("Stránka katalogu nemá očekávanou strukturu stromu; nejde o prázdný výsledek.")
    ancestors = []
    for li in lists[0].select("li"):
        a = li.find("a", href=True)
        if a is None:
            raise _bad("Cesta ve stromu katalogu má neočekávanou položku.")
        ancestors.append((_entity_id(site, a["href"]), a.get_text(" ", strip=True)))
    if not ancestors or ancestors[-1][0] != expected_id:
        raise _bad("Katalog vrátil jiný uzel, než byl požadován.", expected=expected_id)
    children = []
    if len(lists) > 1:
        # A hidden list still carries the child nodes of a container that also holds books of its own (the AHMP
        # Pravoslavná církev); its other items are empty placeholders, one per book row.
        hidden = "displayNone" in lists[1].get("class", [])
        for li in lists[1].select("li"):
            a = li.find("a", href=True)
            if hidden and a is None and "navigatorLineTerminal" in li.get("class", []) and not li.get_text(strip=True):
                continue
            icon = li.find("i")
            if a is None or icon is None:
                raise _bad("Podřízený uzel katalogu nemá očekávaný tvar.")
            classes = icon.get("class", [])
            if "icon-plus" not in classes and "icon-minus" not in classes:
                raise _bad("Podřízený uzel katalogu nemá známou ikonu.")
            children.append(NodeRef(_entity_id(site, a["href"]), a.get_text(" ", strip=True),
                                    _count(a.get("title")), "icon-plus" in classes))
    allowed = {10099} | {t for node, t in (extra_rows or {}).items() if any(a[0] == node for a in ancestors)}
    books, warnings, rows = [], [], soup.select("div.contentBlock div.navigatorLine.listArticle")
    for row in rows:
        link = row.find("a", class_="attachments", href=True)
        text_link = next((a for a in row.find_all("a", href=True) if "attachments" not in a.get("class", [])), None)
        label = text_link.get_text(" ", strip=True) if text_link else ""
        if link is None:
            # Never lose an undigitised book silently.
            warnings.append("Záznam bez digitalizace: " + (label or row.get_text(" ", strip=True))[:300])
            continue
        p = urlsplit(urljoin(page_url, link["href"]))
        values = parse_qs(p.query, keep_blank_values=True)
        m_title = re.search(r"(\d+)\s+obráz", link.get("title", ""))
        xid = (values.get("xid") or [""])[0]
        m_ref = site.entity_re.fullmatch((values.get("entityRef") or [""])[0])
        if (p.path != site.base + "/Zoomify.action" or not re.fullmatch(r"[0-9A-Fa-f]{32}", xid) or not m_ref
                or values.get("entityType", [""])[0] not in ROW_TYPES or len(values["entityType"]) != 1
                or ROW_TYPES[values["entityType"][0]] not in allowed
                or not m_title or not label):
            raise _bad("Řádek knihy nemá očekávaný tvar (odkaz na prohlížeč, počet obrazů nebo název).")
        entity_type = ROW_TYPES[values["entityType"][0]]
        # Register rows are "signature • years"; Soupis and census rows have no signature, the whole label is theirs.
        signature, _, years = label.partition("•") if entity_type == 10099 else (label, "", "")
        books.append(BookRow(xid=xid.upper(), entity_id=int(m_ref[1]), label=label,
                             signature=" ".join(signature.split()), years=" ".join(years.split()) or None,
                             scans=int(m_title[1]), entity_type=entity_type))
    pager = soup.select_one("div.paginationBlock")
    text = pager.get_text(" ", strip=True) if pager else ""
    first = last = None
    m_range = re.search(r"(\d+)\s*-\s*(\d+)\s*/\s*\d+\s*-\s*(\d+)", text)
    m_all = re.search(r"Nalezen\w*\s+(\d[\d\s ]*?)\s+záznam", text)
    if m_range:
        first, last, total = int(m_range[1]), int(m_range[2]), int(m_range[3])
        expected_items = min(last, total) - first + 1     # the last page still says "10 - 18 / 1 - 16"
    elif m_all:
        total = _number(m_all[1])
        expected_items = total
    else:
        raise _bad("Stránka katalogu neuvádí počet záznamů; nejde o prázdný výsledek.")
    if len(children) + len(rows) != expected_items:
        raise _bad("Počet záznamů na stránce nesouhlasí s počtem uvedeným katalogem.",
                   parsed=len(children) + len(rows), stated=expected_items)
    return NodePage(id=expected_id, name=heading.get_text(" ", strip=True), ancestors=ancestors, children=children,
                    books=books, warnings=warnings, total=total, first=first, last=last,
                    next_url=_next_link(site, pager, page_url) if pager else None, jump=_jump_form(pager))


@dataclass
class ViewerPage:
    xid: str
    signature: str
    scan: int
    total: int
    image_root: str      # …/<NNNN>/<32hex>.jpg – ImageProperties.xml and TileGroup* live below it
    width: int
    height: int


_ROOT = re.compile(r'^\s*var\s+zoomifyImgPath\s*=\s*"(https://[^"\s]+)"\s*;', re.M)
_PROPS = re.compile(r"^\s*var\s+imgProperties\s*=\s*\"[^\n]*?zImageProperties=(<IMAGE_PROPERTIES[^>]*/>)", re.M)


def parse_viewer(html: str, site: BachSite, xid: str, scan: int, max_pixels: int) -> ViewerPage:
    """A permalink with &scan=N: the scan's image root, its size, the book's signature and total scan count."""
    if is_expired(html):
        raise ResearchError("SOURCE_UNAVAILABLE", "Relace katalogu vypršela.", reason="session_expired")
    soup = BeautifulSoup(html, "lxml")
    roots, props = _ROOT.findall(html), _PROPS.findall(html)
    headline = soup.select_one("a.headline[href]")
    counter = soup.select_one("#getScan .number")
    position = soup.select_one('#getScan input[name="rowTxt"]')
    if len(roots) != 1 or len(props) != 1 or headline is None or counter is None or position is None:
        raise _bad("Stránka prohlížeče nemá očekávanou strukturu (kořen obrazu, rozměry, signatura nebo počet snímků).")
    linked = parse_qs(urlsplit(headline["href"]).query).get("xid", [""])[0]
    if linked.upper() != xid.upper():
        raise _bad("Prohlížeč patří jiné knize, než byla požadována.")
    if not position.get("value", "").isdigit() or int(position["value"]) != scan:
        raise _bad("Prohlížeč neotevřel požadovaný snímek.", requested=scan, shown=position.get("value"))
    root = roots[0]
    p = urlsplit(root)
    if p.hostname != site.images_host or p.query or p.fragment or not root.endswith(".jpg"):
        raise _bad("Kořen obrazu leží mimo očekávaný obrazový server.")
    descriptor = parse_zoomify(props[0].encode(), root + "/ImageProperties.xml", max_pixels)
    return ViewerPage(xid=xid.upper(), signature=" ".join(headline.get("title", headline.get_text()).split()),
                      scan=scan, total=_number(counter.get_text()), image_root=root,
                      width=descriptor.width, height=descriptor.height)


def manifest_hash(site: BachSite, xid: str, total: int) -> str:
    """Roots are resolved lazily, one per scan; the book's identity and scan count are what a manifest can promise."""
    return hashlib.sha256(f"{site.code}\0{xid.upper()}\0{total}".encode()).hexdigest()


# ---------------------------------------------------------------- walking a node

@dataclass
class Node:
    id: int
    name: str
    ancestors: list[tuple[int, str]]
    children: list[NodeRef] = field(default_factory=list)
    books: list[BookRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    complete: bool = True
    requests: int = 0


async def walk_node(source, site: BachSite, session: SiteSession, node_id: int, max_requests: int) -> Node:
    """A node with every page of its list. Stops (incomplete) when the session has made `max_requests` requests in
    total, opener and retries included. A session that expired mid-way is replaced once and the node restarted."""
    limit = session.requests + max_requests
    for attempt in (0, 1):
        try:
            return await _walk(source, site, session, node_id, limit)
        except ResearchError as e:
            if e.details.get("reason") != "session_expired" or attempt or limit - session.requests < 2:
                raise
            session.reset()
    raise AssertionError("unreachable")


async def _walk(source, site, session, node_id, limit) -> Node:
    url, node, seen = site.node_url(node_id), None, set()
    while url:
        seen.add(url)
        body = await source.session_get(session, url)
        page = parse_node(body.decode("utf-8-sig", errors="replace"), site, url, node_id)
        if node is None:
            node = Node(page.id, page.name, page.ancestors)
        node.requests += 1
        node.children += page.children
        node.books += page.books
        node.warnings += page.warnings
        if page.next_url is None:
            break
        if page.next_url in seen:
            raise _bad("Stránkování katalogu se zacyklilo.")
        if session.requests >= limit:
            node.complete = False
            node.warnings.append("Dosažen limit požadavků na seznam; výpis je neúplný.")
            break
        url = page.next_url
    if node.complete and page.total != len(node.children) + len(node.books) + len(node.warnings):
        raise _bad("Součet záznamů ze všech stránek nesouhlasí s počtem uvedeným katalogem.",
                   parsed=len(node.children) + len(node.books) + len(node.warnings), stated=page.total)
    return node
