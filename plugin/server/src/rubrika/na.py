"""National Archives (Národní archiv): Jewish registers of the fund HBMa (NAD 167) through the Archivní VadeMeCum.

The application (Bach systems, like AHMP) is stateful and answers HTML only. The whole inventory is one long list of
3 900 rows in pages of twelve with a table of contents in the sidebar; a table-of-contents header is a place, the
rows below it are its books. The table of contents is read once and remembered in the user's own cache; a place's
books are read by jumping to its header inside one session. A book's scans are resolved lazily, ten at a time, from
the viewer's thumbnail window. Source text is data; everything unexpected raises PARSE_ERROR."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .ahmp import sort_key, tokens as name_tokens, word_matches
from .bach import BachSite, _ROOT, _PROPS, _bad, _number, is_expired, manifest_hash
from .catalog import normalize, parse_periods
from .errors import ResearchError
from .images import parse_zoomify
from .models import Book, EventCoverage, Page, Periods, Place, YearSpan

PREFIX = "na:"
HOST = "vademecum.nacr.cz"
BASE = "/vademecum"
FUND_XID = "f86bf41ed11ae5f01740720eed9e0dbb"      # permalink of the finding aid; it also makes the server issue a session
FUND_ENTITY = "(^n)(((20cb6cc9d41144e5874020c1e918c34f)(de1e5208398e4a7eb52df0f4687e7dd2))(4137))"
SITE = BachSite("na", HOST, BASE, HOST, opener_xid=FUND_XID)
CITATION = "Národní archiv, Matriky židovských náboženských obcí v českých krajích (HBMa, NAD 167), inv. č. "
PAGE = 12                     # rows of the inventory per page
WINDOW = 10                   # thumbnails (scans) per viewer page
MAX_LIST_PAGES = 20           # inventory pages one list_sources may read after the jump (12 rows each)
MAX_PLACES = 25
KEY = "hbma"
PLACE_ID = re.compile(r"na:place:(\d{1,6})")
BOOK_ID = re.compile(r"na:book:(\d{1,9})")
PAGE_ID = re.compile(r"(na:book:\d{1,9}):([1-9]\d{0,4})")
_STOP = frozenset({"okres", "o", "u", "v", "ve", "na", "z", "do", "matriky", "matrika", "zidovske", "obec"})
_EVENTS = {"N": "birth", "O": "marriage", "Z": "death"}
_XID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN = re.compile(r"[A-Za-z0-9_=-]{16,600}")
_BOOK_ENTITY = re.compile(r"\(\^n\)\(\(\(20cb6cc9d41144e5874020c1e918c34f\)\(de1e5208398e4a7eb52df0f4687e7dd2\)\)\((\d{1,9})\)\)")
_THUMB = re.compile(rf"https://{re.escape(HOST)}/mrimage/vademecum/image//(cz/archives/CZ-100000010/inventare/dao/images/\d{{4}}/"
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg)/nahled_maly\.jpg")
_INDEX = re.compile(r"^index(?:y)?\b")


def is_na_id(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def place_number(place_id: str) -> int:
    m = PLACE_ID.fullmatch(place_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Místa Národního archivu musí mít ID ve tvaru na:place:<číslo> (z find_place).")
    return int(m[1])


def entity_id_of(book_id: str) -> int:
    m = BOOK_ID.fullmatch(book_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Knihu Národního archivu otevřete podle ID ve tvaru na:book:<číslo> z list_sources.")
    return int(m[1])


# ---------------------------------------------------------------- URLs

def _url(action: str, values: list[tuple[str, str]]) -> str:
    return f"https://{HOST}{BASE}/{action}?" + urlencode(values, quote_via=quote)


def book_entity(entity_id: int) -> str:
    return f"(^n)(((20cb6cc9d41144e5874020c1e918c34f)(de1e5208398e4a7eb52df0f4687e7dd2))({int(entity_id)}))"


def volume_url() -> str:
    return _url("BookVolume.action", [("browseAid", ""), ("entity", FUND_ENTITY)])


def viewer_url(entity_id: int) -> str:
    return _url("Zoomify.action", [("entityRef", book_entity(entity_id)), ("scanIndex", "0")])


def permalink(xid: str, scan: str | None = None) -> str:
    if not _XID.fullmatch(xid) and not re.fullmatch(r"[0-9a-f]{32}", xid):
        raise ResearchError("INVALID_SOURCE", "Neplatný trvalý odkaz.")
    return (f"https://{HOST}{BASE}/permalink?xid={xid}" + (f"&scan={scan}&parentType=9019" if scan else ""))


def _link(href: str, page_url: str, action: str, names: set[str]) -> dict[str, str]:
    """A link back into the same application, its `;jsessionid=` path parameter dropped (the cookie is what counts);
    only the given parameters, each once."""
    p = urlsplit(urljoin(page_url, href))
    if (p.scheme != "https" or p.hostname != HOST
            or not re.fullmatch(re.escape(f"{BASE}/{action}") + r"(?:;jsessionid=[A-Za-z0-9._-]{8,200})?", p.path)):
        raise _bad("Odkaz katalogu opouští očekávaný tvar.")
    values = parse_qs(p.query, keep_blank_values=True)
    if set(values) != names or any(len(v) != 1 for v in values.values()):
        raise _bad("Odkaz katalogu má neznámé parametry.")
    return {k: v[0] for k, v in values.items()}


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


# ---------------------------------------------------------------- the inventory

@dataclass
class Row:
    index: int
    kind: str            # header | book | undigitised | reference
    text: str
    xid: str = ""
    entity_id: int = 0
    scans: int = 0
    descr: str = ""
    years: str = ""


@dataclass
class TocEntry:
    n: int
    level: int
    text: str
    url: str


@dataclass
class VolumePage:
    start: int
    total: int
    rows: list[Row]
    toc: list[TocEntry] = field(default_factory=list)
    next_url: str | None = None


def _row(article, page_url: str) -> Row:
    anchor = article.find("a", class_="textBlockLink", href=True)
    if anchor is None:
        raise _bad("Řádek inventáře nemá odkaz na záznam.")
    q = _link(anchor["href"], page_url, "BookVolume.action",
              {"show", "_sourcePage", "pagerCompStateId", "xid", "entityType", "paginatorCompStateId", "rowPg"})
    if (not q["rowPg"].isdigit() or not _XID.fullmatch(q["xid"]) or q["entityType"] not in ("10018", "9019")
            or q["pagerCompStateId"] != "PAGER_RESULT_BOOK" or q["paginatorCompStateId"] != "PAGINATOR_RESULT_BOOK"):
        raise _bad("Řádek inventáře má neplatný odkaz na záznam.")
    header = "kapitola" in article.get("class", [])
    if header != (q["entityType"] == "10018"):
        raise _bad("Řádek inventáře je označen jako záhlaví i jako kniha.")
    title, descr, years = _text(article.select_one("p.title")), _text(article.select_one("p.descr")), _text(article.select_one("p.years"))
    row = Row(int(q["rowPg"]), "header" if header else "reference", title, xid=q["xid"], descr=descr, years=years)
    if not title:
        raise _bad("Řádek inventáře nemá název.")
    if header:
        return row
    m_inv = re.fullmatch(r"inv\. č\. ([0-9A-Za-z./-]{1,20})", title)
    attach = article.select_one("div.links a.attachments[href]")
    if attach is None:
        row.kind = "undigitised" if m_inv else "reference"      # "— ANNÍN - viz Kundratice" is a cross reference
        return row
    z = _link(attach["href"], page_url, "Zoomify.action", {"entityRef", "scanIndex"})
    m_ref = _BOOK_ENTITY.fullmatch(z["entityRef"])
    icon = attach.find("i")
    m_scans = re.search(r"(\d+)\s+sken", (icon.get("tooltip") or icon.get("title") or "") if icon else "")
    if not m_inv or not m_ref or z["scanIndex"] != "0" or not m_scans:
        raise _bad("Řádek knihy nemá očekávaný tvar (inv. č., odkaz na prohlížeč nebo počet skenů).")
    row.kind, row.entity_id, row.scans, row.text = "book", int(m_ref[1]), int(m_scans[1]), m_inv[1]
    return row


def parse_volume(html: str, page_url: str, want_toc: bool = False) -> VolumePage:
    """One page of the inventory: twelve rows, the pager and (when wanted) the table of contents with its jump links."""
    if is_expired(html):
        raise ResearchError("SOURCE_UNAVAILABLE", "Relace katalogu vypršela.", reason="session_expired")
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("section#contentBook")
    pager = soup.select_one("section#contentBook div#paginatiorBlock")
    # `aricle` is the application's own misspelling of the tag; the parser keeps unknown tags as they are.
    articles = soup.select("section#contentBook aricle.contentRecordList")
    m_total = re.search(r"Celkem\s*:\s*(\d+)", pager.get_text(" ", strip=True) if pager else "")
    if content is None or pager is None or not articles or not m_total:
        raise _bad("Stránka inventáře nemá očekávanou strukturu; nejde o prázdný výsledek.")
    total = int(m_total[1])
    rows = [_row(a, page_url) for a in articles]
    start = rows[0].index
    if start % PAGE or [r.index for r in rows] != list(range(start, start + len(rows))) or len(rows) != min(PAGE, total - start):
        raise _bad("Řádky inventáře na stránce nejdou po sobě nebo jich není tolik, kolik katalog uvádí.", start=start, shown=len(rows), total=total)
    next_url = None
    if start + PAGE < total:
        icon = pager.select_one("i.icon-forward3")
        anchor = icon.find_parent("a") if icon else None
        if anchor is None or not anchor.get("href"):
            raise _bad("Stránkování inventáře nemá odkaz na další stránku.")
        q = _link(anchor["href"], page_url, "PaginatorBookVolume.action", {"_sourcePage", "row"})
        if not _TOKEN.fullmatch(q["_sourcePage"]) or q["row"] != str(start + PAGE):
            raise _bad("Odkaz na další stránku inventáře neodpovídá pořadí řádků.")
        next_url = _url("PaginatorBookVolume.action", [("_sourcePage", q["_sourcePage"]), ("row", q["row"])])
    toc: list[TocEntry] = []
    if want_toc:
        for item in soup.select("div#bookResume div.bookResumeList"):
            m_id, anchor = re.fullmatch(r"bookResume(\d{1,6})", item.get("id", "")), item.find("a", href=True)
            m_level = re.search(r"padding-left:\s*(\d+)px", anchor.get("style", "")) if anchor else None
            text = _text(anchor)
            if not m_id or not m_level or int(m_level[1]) not in (20, 40, 60) or not text:
                raise _bad("Položka obsahu inventáře nemá očekávaný tvar.")
            n = int(m_id[1])
            q = _link(anchor["href"], page_url, "PaginatorBookVolume.action", {"alter", "_sourcePage", "zanchor", "rowTxt"})
            if q["alter"] != "" or q["zanchor"] != f"#bookResume{n}" or q["rowTxt"] != str(n) or not _TOKEN.fullmatch(q["_sourcePage"]):
                raise _bad("Odkaz položky obsahu inventáře neodpovídá jejímu číslu.")
            toc.append(TocEntry(n, int(m_level[1]), text, _url("PaginatorBookVolume.action", [
                ("alter", ""), ("_sourcePage", q["_sourcePage"]), ("zanchor", q["zanchor"]), ("rowTxt", q["rowTxt"])])))
        numbers = [t.n for t in toc]
        if not toc or numbers != sorted(set(numbers)) or numbers[0] < 1 or numbers[-1] > total:
            raise _bad("Obsah inventáře je prázdný nebo nemá rostoucí čísla řádků.")
    return VolumePage(start, total, rows, toc, next_url)


# ---------------------------------------------------------------- places

@dataclass(frozen=True)
class NaPlace:
    n: int                        # number of the table-of-contents item (the row is n - 1)
    label: str
    header: str
    names: tuple[str, ...]        # place name first, then names in brackets
    okres: str | None
    context: str | None           # Prague: the register type above a city quarter
    end: int                      # number of the next place, or total + 1

    @property
    def rows(self) -> int:
        return self.end - self.n - 1


def _names(header: str) -> tuple[str, list[str], str | None]:
    base = re.split(r"\s*\(|\s+viz\s+", header, maxsplit=1)[0].strip() or header
    okres, alt = None, []
    for inner in re.findall(r"\(([^()]*)\)?", header):
        if re.fullmatch(r"\s*(?:dř\.\s*)?o[.,]?\s*", inner):      # "(o." cut off in the inventory
            continue
        m = re.search(r"(?:^|,)\s*(?:dř\.\s*)?o[.,]\s*(\S[^,]*)$", inner)
        rest = inner[:m.start()] if m else inner
        if m:
            okres = m[1].strip()
        if rest.strip(" ,"):
            alt.append(rest.strip(" ,"))
    return base, alt, okres


_PRAHA_TYPE = re.compile(r"^praha\s*-?\s*(?:matriky|indexy)\b")
_PRAHA_QUARTER = re.compile(r"^praha(?:-|\s(?!-))\S[^,]*$")      # PRAHA-KARLÍN, PRAHA-STARÉ MĚSTO, PRAHA ŽIŽKOV
_PRAHA_OTHER = re.compile(r"ohledaci|hlaseni")


def build_places(items: list[tuple[int, int, str]], total: int) -> list[NaPlace]:
    """Table-of-contents items (number, indent, text) → places. Index sub-headings belong to the place above; any other
    heading of the second level starts a place of its own. A Prague heading of a register type gives its city quarters
    below it their context, because the same quarter names repeat for every type."""
    starts = [(n, level, text) for n, level, text in items if level >= 40 and not _INDEX.match(normalize(text))]
    places, context, parent = [], None, None
    for i, (n, level, text) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else total + 1
        norm = normalize(text)
        label = text
        if _PRAHA_TYPE.match(norm):
            context = text
        elif _PRAHA_QUARTER.fullmatch(norm) and not _PRAHA_OTHER.search(norm) and context:
            label = f"{context} › {text}"
        else:
            context = None
            if level == 60 and parent:
                label = f"{parent} › {text}"
        if level == 40:
            parent = text
        base, alt, okres = _names(text)
        places.append(NaPlace(n, label, text, tuple(dict.fromkeys([base, *alt])), okres, context if label != text else None, end))
    return places


def match_score(term_tokens: list[str], place: NaPlace) -> int | None:
    """Every word of the term must occur in the place's names or above them (district, Prague register type);
    a word of the own names counts double."""
    own = [t for name in place.names for t in name_tokens(name)]
    above = [t for text in (place.okres, place.context) if text for t in name_tokens(text)]
    score = 0
    for t in term_tokens:
        if any(word_matches(t, w) for w in own):
            score += 2
        elif any(word_matches(t, w) for w in above):
            score += 1
        else:
            return None
    return score


# ---------------------------------------------------------------- books

@dataclass
class BookRow:
    inv: str
    entity_id: int
    xid: str
    descr: str
    years: str
    scans: int
    index_section: bool = False


_YEAR_ITEM = r"\d{4}(?:\s*-\s*(?:\d{4}|\d{2}))?"
_YEAR_LIST = re.compile(rf"\s*({_YEAR_ITEM}(?:\s*[,;]\s*{_YEAR_ITEM})*)(.*)$", re.S)
_INDEX_NOTE = re.compile(r"\(\s*i(?:\s+v letech\s+([^)]*?))?\s*\)")


def _years(spec: str) -> Periods | None:
    """`1849-1871`, `1849-71` (end year in the century of the start), `1842-1854, 1864`; None when anything is ambiguous."""
    spans = []
    for item in re.split(r"\s*[,;]\s*", spec.strip()):
        m = re.fullmatch(r"(\d{4})(?:\s*-\s*(\d{4}|\d{2}))?", item)
        if not m:
            return None
        start = int(m[1])
        end = start if m[2] is None else int(m[2])
        if m[2] is not None and len(m[2]) == 2:
            end = start - start % 100 + end
        if end < start:
            return None
        spans.append(YearSpan(start=start, end=end))
    return Periods(status="known", spans=spans, raw=spec.strip())


def coverage_of(descr: str, index_section: bool = False) -> dict[str, EventCoverage]:
    """`N 1849-1871 O 1841-1856 Z 1850-1870`; `(i)` after a span means an index to the book, a book below an
    "index" heading is an index of the events it names. Everything is read conservatively: an event the description
    does not name, a year list the parser cannot account for completely (leftover text, a reversed span), or a missing
    `(i)` (which proves nothing about an index) leaves that part `uncertain`, never known or absent."""
    parts = re.split(r"(?:^|\s)([NOZ]{1,3})(?=\s+\d)", descr)      # "NOZ 1802-1869" names three events at once
    found: dict[str, list[str]] = {}
    for letters, text in zip(parts[1::2], parts[2::2]):
        for letter in letters:
            found.setdefault(letter, []).append(text)             # a letter may occur again: its spans add up
    unnamed = Periods(status="uncertain", raw="podle popisu" if found else descr)
    out = {}
    for letter, event in _EVENTS.items():
        if letter not in found:
            out[event] = EventCoverage(records=unnamed, index=unnamed)
            continue
        spans, indexes, ok, raw = [], [], True, " ".join(t.strip() for t in found[letter])
        for text in found[letter]:
            m = _YEAR_LIST.match(text)
            periods = _years(m[1]) if m else None
            rest = m[2].strip(" ,;") if m else ""
            note = _INDEX_NOTE.fullmatch(rest) if rest else None
            if periods is None or (rest and note is None):
                ok = False
                continue
            spans += periods.spans
            if note:
                found_years = re.findall(_YEAR_ITEM, note[1] or "")       # "(i v letech 1908-1915 pro každý rok)"
                inner = _years(", ".join(found_years)) if found_years else periods
                indexes.append(inner)
            else:
                indexes.append(None)
        uncertain = Periods(status="uncertain", raw=raw)
        if not ok:
            out[event] = EventCoverage(records=uncertain, index=uncertain)
            continue
        records = Periods(status="known", spans=spans, raw=raw)
        if index_section:
            out[event] = EventCoverage(records=Periods(status="absent", raw=""), index=records)
        elif indexes and all(i is not None for i in indexes):
            out[event] = EventCoverage(records=records, index=Periods(status="known", spans=[x for i in indexes for x in i.spans], raw=raw))
        else:
            out[event] = EventCoverage(records=records, index=uncertain)
    return out


def citation_of(book: Book) -> str:
    return CITATION + book.number


def xid_of_book(book: Book) -> str:
    m = re.fullmatch(rf"https://{re.escape(HOST)}{re.escape(BASE)}/permalink\?xid=({_XID.pattern})", book.detail_url)
    if not m:
        raise ResearchError("PARSE_ERROR", "Kniha nemá trvalý odkaz v očekávaném tvaru.")
    return m[1]


# ---------------------------------------------------------------- the viewer

@dataclass
class ScanThumb:
    scan: int
    hash: str
    root: str


@dataclass
class ViewerWindow:
    first: int
    last: int
    total: int
    thumbs: list[ScanThumb]
    tokens: list[str]            # tokens of the scan pager links, any of which can be paired with another row


def parse_viewer(html: str, page_url: str, xid: str, total: int, max_pixels: int, expect_first: int | None = None) -> ViewerWindow:
    """The viewer of one book: ten thumbnails from `first`, the first one shown large. The thumbnails carry the image
    roots of the neighbouring scans, so no further request is needed for them."""
    if is_expired(html):
        raise ResearchError("SOURCE_UNAVAILABLE", "Relace katalogu vypršela.", reason="session_expired")
    soup = BeautifulSoup(html, "lxml")
    headline = soup.select_one("a.headline[href]")
    pager = soup.select_one("div#paginatiorBlock")
    counter = soup.select_one("#getScan .number")
    roots, props = _ROOT.findall(html), _PROPS.findall(html)
    active, maximum = re.findall(r'var\s+activePicture\s*=\s*"(\d+)"', html), re.findall(r'var\s+max\s*=\s*"(\d+)"', html)
    m_range = re.search(r"(\d+)\s*-\s*(\d+)\s*\|\s*(\d+)", pager.select_one("div.pageNum").get_text(" ", strip=True)) if pager and pager.select_one("div.pageNum") else None
    if (headline is None or counter is None or not m_range or len(roots) != 1 or len(props) != 1 or len(active) != 1 or len(maximum) != 1):
        raise _bad("Stránka prohlížeče nemá očekávanou strukturu (záhlaví, počet a pozice snímků, kořen obrazu nebo rozměry).")
    if parse_qs(urlsplit(headline["href"]).query).get("xid", [""])[0].lower() != xid.lower():
        raise _bad("Prohlížeč patří jiné knize, než byla požadována.")
    first, shown_last, shown_total = (int(x) for x in m_range.groups())
    last = min(shown_last, total)          # the last window still says "31 - 40 | 35"
    if not (shown_total == _number(counter.get_text()) == int(maximum[0]) == total) or int(active[0]) != first \
            or not 1 <= first <= last or shown_last != first + WINDOW - 1:
        raise _bad("Počet nebo pozice snímků v prohlížeči nesouhlasí s knihou.", total=total, shown=shown_total)
    if expect_first is not None and first != expect_first:
        raise _bad("Prohlížeč neotevřel požadovanou skupinu snímků.", requested=expect_first, shown=first)
    thumbs = []
    for li in soup.select("ul.pictures > li"):
        anchor, img, number = li.find("a", href=True), li.find("img"), li.select_one("span.numImg")
        m_id = re.fullmatch(r"scan([0-9a-f]{32})", li.get("id", ""))
        q = parse_qs(urlsplit(anchor["href"]).query) if anchor else {}
        m_title = re.fullmatch(r"Sken (\d+) - ", anchor.get("title", "")) if anchor else None
        m_thumb = _THUMB.fullmatch(img.get("src", "")) if img else None
        if (not (m_id and m_title and m_thumb and number) or q.get("xid", [""])[0].lower() != xid.lower()
                or q.get("scan") != [m_id[1]] or q.get("parentType") != ["9019"]
                or not number.get_text(strip=True).isdigit() or int(number.get_text(strip=True)) != int(m_title[1])):
            raise _bad("Miniatura snímku nemá očekávaný tvar.")
        thumbs.append(ScanThumb(int(m_title[1]), m_id[1], f"https://{HOST}/mrimage/vademecum/zoomify//{m_thumb[1]}"))
    if [t.scan for t in thumbs] != list(range(first, last + 1)):
        raise _bad("Miniatury nejdou po sobě nebo jich není tolik, kolik prohlížeč uvádí.")
    if roots[0] != thumbs[0].root:
        raise _bad("Zobrazený snímek neodpovídá své miniatuře.")
    parse_zoomify(props[0].encode(), roots[0] + "/ImageProperties.xml", max_pixels)
    tokens = []
    for anchor in pager.select("a[href]"):
        q = _link(anchor["href"], page_url, "PaginatorMedia.action", {"_sourcePage", "row"})
        if not _TOKEN.fullmatch(q["_sourcePage"]) or not q["row"].isdigit():
            raise _bad("Odkaz na další snímky má neplatný tvar.")
        tokens.append(q["_sourcePage"])
    return ViewerWindow(first, last, total, thumbs, tokens)


# ---------------------------------------------------------------- catalogue

class NaCatalog:
    def __init__(self, source, store, site: BachSite = SITE):
        self.source, self.store, self.site = source, store, site

    # -- inventory ------------------------------------------------------------------------------------------
    async def _volume_page(self, session, url: str, want_toc: bool = False) -> VolumePage:
        body = await self.source.session_get(session, url)
        return parse_volume(body.decode("utf-8-sig", errors="replace"), url, want_toc)

    def _store_toc(self, page: VolumePage):
        self.store.put_entity("na_toc", KEY, {"total": page.total, "items": [[t.n, t.level, t.text] for t in page.toc]})

    def _cached_toc(self, ttl: int | None) -> tuple[list[tuple[int, int, str]], int] | None:
        cached = self.store.entity("na_toc", KEY, ttl)
        return ([(n, level, text) for n, level, text in cached["items"]], cached["total"]) if cached else None

    async def places_index(self, sid, owner) -> list[NaPlace]:
        cached = self._cached_toc(self.source.cfg.metadata_ttl)
        if cached is None:
            session = self.site.session(sid, owner)
            page = await self._volume_page(session, volume_url(), want_toc=True)
            self._store_toc(page)
            cached = [(t.n, t.level, t.text) for t in page.toc], page.total
        return build_places(*cached)

    async def places(self, sid, owner, term: str) -> tuple[list[Place], list[str]]:
        term_tokens = [t for t in name_tokens(term) if t not in _STOP]
        if not term_tokens:
            raise ResearchError("INVALID_ARGUMENT", "Zadejte název místa, například obce nebo židovské obce.")
        scored = [(s, p) for p in await self.places_index(sid, owner) if (s := match_score(term_tokens, p)) is not None]
        scored.sort(key=lambda sp: (-sp[0], sp[1].n))
        warnings = []
        if len(scored) > MAX_PLACES:
            warnings.append(f"Nalezeno {len(scored)} míst; zobrazeno prvních {MAX_PLACES}. Upřesněte název (například doplňte okres).")
        if any(p.context for _, p in scored[:MAX_PLACES]):
            warnings.append("Praha je v inventáři rozdělena podle druhu matriky a čtvrti: stejná čtvrť se opakuje pro narozené, oddané, "
                            "zemřelé a indexy; v popisku je druh matriky před čtvrtí.")
        places = []
        for _, p in scored[:MAX_PLACES]:
            place = Place(source_id=f"na:place:{p.n}", label=p.label, names=list(p.names), source_url=permalink(FUND_XID))
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
            places.append(place)
        return places, warnings

    async def books(self, sid, owner, place: Place) -> tuple[list[Book], bool, list[str]]:
        n = place_number(place.source_id)
        # The label is part of the key: if the inventory is renumbered, a stale list never answers for another place.
        key = f"{n}:{hashlib.sha1(place.label.encode()).hexdigest()[:10]}"
        cached = self.store.entity("na_books", key, self.source.cfg.metadata_ttl)
        if cached is None:
            for attempt in (0, 1):
                session = self.site.session(sid, owner)
                try:
                    cached = await self._read_block(session, n, place)
                    break
                except ResearchError as e:
                    if e.details.get("reason") != "session_expired" or attempt:
                        raise
            if cached["complete"]:
                self.store.put_entity("na_books", key, cached)
        books = {}
        for row in cached["rows"]:
            book = self._book(BookRow(**row), place)
            books[book.source_id] = book
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return list(books.values()), cached["complete"], cached["warnings"]

    async def _read_block(self, session, n: int, place: Place) -> dict:
        """Load the inventory, jump to the place's header and read its rows; everything inside one session."""
        page = await self._volume_page(session, volume_url(), want_toc=True)
        fresh = {p.n: p for p in build_places([(t.n, t.level, t.text) for t in page.toc], page.total)}
        self._store_toc(page)
        current = fresh.get(n)
        if current is None or current.label != place.label:
            raise ResearchError("SOURCE_CHANGED", "Obsah inventáře se v katalogu změnil; vyhledejte místo znovu pomocí find_place.")
        first_row, last_row, start = n - 1, current.end - 2, (n - 1) // PAGE * PAGE
        if start != page.start:
            link = next(t.url for t in page.toc if t.n == n)
            page = await self._volume_page(session, link)
            if page.start != start:
                raise _bad("Katalog otevřel jinou stránku inventáře, než odpovídá záhlaví místa.", expected=start, shown=page.start)
        rows, complete, warnings, pages = [], True, [], 1
        while True:
            rows += [r for r in page.rows if first_row <= r.index <= last_row]
            if page.start + PAGE > last_row:
                break
            if page.next_url is None:
                raise _bad("Stránkování inventáře skončilo dřív než blok místa.")
            if pages >= MAX_LIST_PAGES:
                complete = False
                warnings.append(f"Výpis je useknutý: přečteno {MAX_LIST_PAGES} stránek inventáře (asi {MAX_LIST_PAGES * PAGE} řádků) a "
                                "blok místa je delší. Zbývající knihy najdete v prohlížeči archivu: " + permalink(FUND_XID) + " (Listování v pomůcce).")
                break
            page = await self._volume_page(session, page.next_url)
            pages += 1
        if not rows or rows[0].index != first_row or rows[0].kind != "header" or " ".join(rows[0].text.split()) != current.header:
            raise _bad("První řádek bloku není záhlaví místa.")
        out, section_index = [], False
        for r in rows[1:]:
            if r.kind == "header":
                section_index = bool(_INDEX.match(normalize(r.text)))
            elif r.kind == "book":
                out.append(BookRow(r.text, r.entity_id, r.xid, r.descr, r.years, r.scans, section_index).__dict__)
            elif r.kind == "undigitised":
                warnings.append(f"Záznam bez digitalizace: {r.text} {r.descr}"[:300])
            else:
                warnings.append(f"Odkaz v inventáři: {r.text}"[:300])
        if current.rows == 0:
            warnings.append(f"Záhlaví bez knih: {current.header}")
        return {"rows": out, "complete": complete, "warnings": warnings}

    def _book(self, row: BookRow, place: Place) -> Book:
        return Book(source_id=f"na:book:{row.entity_id}", number=row.inv, creator=place.names[0], denomination="židovská matrika",
                    coverage=coverage_of(row.descr, row.index_section), image_count=row.scans,
                    detail_url=permalink(row.xid), raw_notes=f"{place.label}; inv. č. {row.inv}; {row.descr}; {row.years}"[:4000])

    # -- pages ----------------------------------------------------------------------------------------------
    def shell_page(self, book: Book, n: int) -> Page:
        """A scan's page before its image root is known: the book's record permalink stands in for the source, and
        there is no per-scan viewer link until the scan is resolved (it needs the scan's hash)."""
        xid = xid_of_book(book)
        link = permalink(xid)
        return Page(page_id=f"{book.source_id}:{n}", book_id=book.source_id, scan_number=n, image_key=f"scan-{n}",
                    source_url=link, viewer_url=None, source_type="zoomify",
                    manifest_sha256=manifest_hash(self.site, xid, book.image_count), source="na",
                    citation=citation_of(book), archive="na")

    async def _viewer_window(self, session, url: str, xid: str, total: int, expect_first: int | None = None) -> ViewerWindow:
        body = await self.source.session_get(session, url)
        try:
            return parse_viewer(body.decode("utf-8-sig", errors="replace"), url, xid, total, self.source.cfg.max_source_pixels, expect_first)
        except ResearchError as e:
            if e.code == "PARSE_ERROR":
                self.source.invalidate(url)
            raise

    async def resolve(self, sid, owner, book: Book, n: int) -> list[Page]:
        """The pages of the ten-scan window that holds scan `n`: the viewer of the book (session, one request) and, for
        a later window, one more request with the pager's token and the window's row."""
        xid, total = xid_of_book(book), book.image_count
        if not total or not 1 <= n <= total:
            raise ResearchError("NOT_FOUND", "Snímek leží mimo rozsah knihy.")
        row = (n - 1) // WINDOW * WINDOW
        for attempt in (0, 1):
            session = self.site.session(sid, owner)
            try:
                window = await self._viewer_window(session, viewer_url(entity_id_of(book.source_id)), xid, total)
                if not window.first <= n <= window.last:
                    if not window.tokens:
                        raise _bad("Prohlížeč nenabízí další snímky knihy.")
                    window = await self._viewer_window(session, _url("PaginatorMedia.action", [
                        ("_sourcePage", window.tokens[0]), ("row", str(row))]), xid, total, expect_first=row + 1)
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
        pages = []
        for t in window.thumbs:
            source_url = t.root + "/ImageProperties.xml"
            self.source.policy.validate(source_url)
            pages.append(self.shell_page(book, t.scan).model_copy(update={
                "source_url": source_url, "image_key": t.root.rsplit("/", 1)[1], "viewer_url": permalink(xid, t.hash)}))
        return pages
