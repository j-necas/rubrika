"""Conservative parsers for Acta Publica. Source text is never executed."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .errors import ResearchError
from .models import Book, EventCoverage, Page, Periods, Place, SearchQuery, YearSpan

BASE = "https://www.mza.cz"
SEARCH_PATH = "/actapublica/matrika/hledani"
AUTOCOMPLETE = BASE + "/actapublica/ajax/matrika/autocomplete_obec"


def normalize(s: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", s)
                            if not unicodedata.combining(c)).casefold().split())


def book_id_from_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in ("www.mza.cz", "mza.cz") or p.username or p.password or p.port not in (None,443):
        raise ResearchError("INVALID_SOURCE", "Je vyžadován HTTPS odkaz na detail matriky MZA.")
    m = re.fullmatch(r"/actapublica/matrika/detail/(\d+)/?", p.path)
    if not m:
        raise ResearchError("INVALID_SOURCE", "Odkaz není detailem matriky.")
    return m[1]


def parse_places(data: object, source_url: str) -> list[Place]:
    if not isinstance(data, list):
        raise ResearchError("PARSE_ERROR", "Našeptávač nevrátil seznam lokalit.")
    places = {}
    for item in data:
        if item in ("", None):
            continue
        if not isinstance(item, dict) or "id" not in item:
            raise ResearchError("PARSE_ERROR", "Neznámá struktura záznamu lokality.")
        sid = str(item["id"])
        if not sid.isdigit():
            raise ResearchError("PARSE_ERROR", "Neplatný identifikátor lokality.")
        label = item.get("value") or item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ResearchError("PARSE_ERROR", "Chybí název lokality.")
        label = BeautifulSoup(label, "lxml").get_text(" ", strip=True)[:2000]
        primary = label.split(",")[0].strip()
        names = [re.sub(r"\([^)]*\)", "", primary).strip()]
        names.extend(re.findall(r"\(([^)]+)\)", primary))
        places[sid] = Place(source_id=sid, label=label, names=list(dict.fromkeys(names)), source_url=source_url)
    return list(places.values())


# A year, optionally preceded by month ("5.1866") or day and month ("3.1.1890"); only the year is kept.
_YEAR = r"(?:(?:0?[1-9]|[12]\d|3[01])\.\s*(?:0?[1-9]|1[0-2])\.\s*|(?:0?[1-9]|1[0-2])\.\s*)?(\d{4})"


def parse_periods(text: str | None) -> Periods:
    if text is None:
        return Periods()
    raw = " ".join(text.replace("\xa0", " ").split())
    normalized = raw.replace("–", "-").replace("—", "-")
    if normalized.strip() in ("-", "—", "–"):
        return Periods(status="absent", raw=raw)
    if not normalized:
        return Periods(raw=raw)
    # Only unambiguous numeric lists can exclude a book. Keep unexplained qualifiers.
    spans = []
    parts = re.split(r"\s*[;,]\s*", normalized)
    uncertain = False
    for part in parts:
        m = re.fullmatch(_YEAR + r"\s*-\s*" + _YEAR, part)
        if m:
            a,b = map(int, m.groups())
            if a > b:
                return Periods(status="uncertain", raw=raw)
            spans.append(YearSpan(start=a,end=b))
            continue
        m = re.fullmatch(_YEAR, part)
        if m:
            spans.append(YearSpan(start=int(m[1]),end=int(m[1])))
            continue
        m = re.fullmatch(_YEAR + r"\s*-", part)
        if m:
            spans.append(YearSpan(start=int(m[1])))
            continue
        m = re.fullmatch(r"-\s*" + _YEAR, part)
        if m:
            spans.append(YearSpan(end=int(m[1])))
            continue
        uncertain = True
        # Retain useful numeric hints but never use these to reject a book.
        for a,b in re.findall(_YEAR + r"\s*-\s*" + _YEAR, part):
            spans.append(YearSpan(start=int(a),end=int(b)))
    return Periods(status="uncertain" if uncertain else "known", spans=spans, raw=raw)


def _coverage(cell) -> EventCoverage:
    fragment = BeautifulSoup(str(cell), "lxml")
    root = fragment.find("td") or fragment
    index_nodes = root.select("i, em, .index, .index-roky, [data-index]")
    if index_nodes:
        index_text = " ".join(n.get_text(" ", strip=True) for n in index_nodes)
        for n in index_nodes:
            n.decompose()
        main_text = root.get_text(" ", strip=True)
        return EventCoverage(records=parse_periods(main_text), index=parse_periods(index_text))
    lines = [s.strip() for s in root.get_text("\n", strip=True).splitlines() if s.strip()]
    if len(lines) == 2:
        return EventCoverage(records=parse_periods(lines[0]),index=parse_periods(lines[1]))
    if len(lines) == 1:
        return EventCoverage(records=parse_periods(lines[0]))
    return EventCoverage(records=Periods(status="uncertain",raw=root.get_text(" ",strip=True)))


def parse_books(html: str, source_url: str) -> tuple[list[Book], list[str], list[str]]:
    soup = BeautifulSoup(html, "lxml")
    table = None
    for t in soup.find_all("table"):
        headers = normalize(" ".join(th.get_text(" ", strip=True) for th in t.find_all("th")))
        if "narozeni" in headers and "oddani" in headers and "zemreli" in headers:
            table = t
            break
    if table is None:
        text = normalize(soup.get_text(" ", strip=True))
        if any(x in text for x in ("nebyly nalezeny zadne matriky", "zadne matriky nebyly nalezeny")):
            return [], [], []
        raise ResearchError("PARSE_ERROR", "Nebyla nalezena očekávaná tabulka matrik; nejde o prázdný výsledek.")
    books = []
    warnings = []
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        if len(cells) < 7:
            raise ResearchError("PARSE_ERROR", "Řádek matriky má neočekávaný počet sloupců.")
        link = row.find("a", href=re.compile(r"/actapublica/matrika/detail/\d+"))
        if not link:
            # Never silently lose undigitised/unavailable books.
            warnings.append("Řádek bez odkazu na digitalizaci: " + row.get_text(" ", strip=True)[:800])
            continue
        detail = urljoin(source_url, link["href"])
        bid = book_id_from_url(detail)
        detail = urlunsplit((*urlsplit(detail)[:3], "", ""))
        creator_lines = list(cells[1].stripped_strings)
        def number(cell):
            s = re.sub(r"\s+", "", cell.get_text(" ", strip=True))
            return int(s) if s.isdigit() else None
        books.append(Book(
            source_id=bid,number=cells[0].get_text(" ",strip=True),
            creator=creator_lines[0] if creator_lines else "",
            denomination=" ".join(creator_lines[1:]) or None,
            coverage={e:_coverage(cells[i]) for e,i in (("birth",2),("marriage",3),("death",4))},
            village_count=number(cells[5]), image_count=number(cells[6]), detail_url=detail,
            raw_notes=row.get_text(" ", strip=True)[:4000]))
    pages = []
    for link in soup.select('.pagination a[href], a[rel="next"], .pager a[href]'):
        if "disabled" in (link.parent.get("class", []) if link.parent else []):
            continue
        target = urljoin(source_url, link["href"])
        p, original = urlsplit(target), urlsplit(source_url)
        if p.hostname not in ("www.mza.cz","mza.cz") or p.path != original.path:
            raise ResearchError("PARSE_ERROR", "Stránkování opouští katalog MZA.")
        if parse_qs(p.query).get("obec_id") != parse_qs(original.query).get("obec_id"):
            raise ResearchError("PARSE_ERROR", "Stránkování mění vybranou obec.")
        pages.append(target)
    return books, list(dict.fromkeys(pages)), warnings


def relevance(book: Book, query: SearchQuery) -> dict:
    if query.event_type is None:
        return {"status": "needs_review", "reason": "Typ události není určen."}
    cov = book.coverage[query.event_type]
    lo, hi = query.year_from or 0, query.year_to or 9999
    unknown = False
    for kind, periods in (("records",cov.records),("index",cov.index)):
        if periods.status in ("unknown","uncertain"):
            unknown = True
        if periods.status == "known" and any((s.start or 0) <= hi and (s.end or 9999) >= lo for s in periods.spans):
            return {"status":"candidate", "via":kind, "reason":"Katalog uvádí překryv období pro daný druh události."}
    if unknown:
        return {"status":"needs_review", "reason":"Neúplná nebo nejistá metadata; knihu nelze bezpečně vyloučit."}
    return {"status":"excluded", "reason":"Katalog neuvádí záznamy ani index daného druhu v zadaném období."}


def _read_array(text: str, start: int) -> tuple[list[str], int]:
    """Only arrays of quoted string literals; never eval/exec JavaScript."""
    i = start
    while i < len(text) and text[i].isspace(): i += 1
    if i >= len(text) or text[i] != "[":
        raise ResearchError("PARSE_ERROR", "Chybí pole obrazových zdrojů.")
    i += 1
    result = []
    while i < len(text):
        while i < len(text) and text[i].isspace(): i += 1
        if i < len(text) and text[i] == "]": return result, i+1
        if i >= len(text) or text[i] not in ("'", '"'):
            raise ResearchError("PARSE_ERROR", "Obrazový seznam obsahuje jiný výraz než řetězec.")
        quote, out = text[i], []
        i += 1
        while i < len(text) and text[i] != quote:
            c = text[i]; i += 1
            if c == "\\":
                if i >= len(text): break
                c = text[i]; i += 1
                if c in ('"',"'","\\","/"): out.append(c)
                elif c in "nrtbf": out.append({"n":"\n","r":"\r","t":"\t","b":"\b","f":"\f"}[c])
                elif c in ("u","x"):
                    n = 4 if c == "u" else 2
                    value=text[i:i+n]
                    if not re.fullmatch(r"[0-9a-fA-F]{%d}"%n,value):
                        raise ResearchError("PARSE_ERROR", "Neplatný escape v obrazovém seznamu.")
                    out.append(chr(int(value,16))); i += n
                else: raise ResearchError("PARSE_ERROR", "Nepodporovaný escape v obrazovém seznamu.")
            else: out.append(c)
        if i >= len(text): break
        i += 1
        result.append("".join(out))
        if len(result) > 10000:
            raise ResearchError("PARSE_ERROR", "Manifest je příliš velký.")
        while i < len(text) and text[i].isspace(): i += 1
        if i < len(text) and text[i] == ",": i += 1
        elif i < len(text) and text[i] == "]": return result,i+1
        else: raise ResearchError("PARSE_ERROR", "Neplatné oddělení položek obrazového seznamu.")
    raise ResearchError("PARSE_ERROR", "Neukončený obrazový seznam.")


def iiif_from_deepzoom(url: str) -> str | None:
    """MZA's and Porta fontium's iipsrv serve the same JPEG2000 over IIIF: one request per region instead of dozens of DZI tiles."""
    p = urlsplit(url)
    query = parse_qs(p.query)
    if not p.path.endswith("/iipsrv.fcgi") or len(query) != 1 or next(iter(query)).lower() != "deepzoom":
        return None
    values = next(iter(query.values()))
    if len(values) != 1:
        return None
    image = values[0]
    if not image.endswith(".jp2.dzi"):
        return None
    return urlunsplit((p.scheme, p.netloc, p.path, "IIIF=" + image[:-4] + "/info.json", ""))


def parse_pages(html: str, book: Book) -> list[Page]:
    match = re.search(r"\bCreateSeadragon\s*\(", html)
    if not match:
        raise ResearchError("PARSE_ERROR", "Detail neobsahuje známý manifest CreateSeadragon. Formát je třeba ověřit.")
    sources, end = _read_array(html, match.end())
    nextpos = end
    while nextpos < len(html) and html[nextpos].isspace(): nextpos += 1
    if nextpos >= len(html) or html[nextpos] != ",":
        raise ResearchError("PARSE_ERROR", "Chybí identifikátory snímků.")
    names, _ = _read_array(html, nextpos+1)
    if not sources or len(sources) != len(names):
        raise ResearchError("PARSE_ERROR", "Nesouhlasí počet zdrojů a identifikátorů snímků.")
    manifest_hash = hashlib.sha256(json.dumps([sources,names],ensure_ascii=False).encode()).hexdigest()
    result = []
    seen = set()
    for index,(source,name) in enumerate(zip(sources,names),1):
        if not name or re.search(r"[/\\\x00-\x1f]",name):
            raise ResearchError("PARSE_ERROR", "Neplatný název snímku.")
        source = urljoin(book.detail_url,source)
        if source in seen:
            raise ResearchError("PARSE_ERROR", "Manifest obsahuje duplicitní zdroj.")
        seen.add(source)
        if iiif_from_deepzoom(source):
            source, typ = iiif_from_deepzoom(source), "iiif"
        elif ".dzi" in source:
            typ = "dzi"
        elif source.endswith("/info.json"):
            typ = "iiif"
        else:
            raise ResearchError("UNSUPPORTED_SOURCE", "Neznámý protokol obrazového zdroje.")
        pid = "page_" + hashlib.sha256((book.source_id+"\0"+source).encode()).hexdigest()[:28]
        result.append(Page(page_id=pid,book_id=book.source_id,scan_number=index,image_key=name,
                           source_url=source,source_type=typ,
                           viewer_url=book.detail_url+"?"+urlencode({"image":name}),
                           manifest_sha256=manifest_hash))
    return result


class Catalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    async def places(self, sid, owner, term):
        url = AUTOCOMPLETE + "?" + urlencode({"term":term})
        body = await self.source.fetch(sid,owner,url,kind="json")
        try:
            data = json.loads(body)
        except (ValueError,UnicodeError) as e:
            raise ResearchError("PARSE_ERROR", "Našeptávač nevrátil validní JSON.") from e
        places = parse_places(data,url)
        # A second diacritic-free request is only a fallback, not a full catalog enumeration.
        if not places and normalize(term) != term:
            url = AUTOCOMPLETE+"?"+urlencode({"term":normalize(term)})
            body = await self.source.fetch(sid,owner,url,kind="json")
            try: places = parse_places(json.loads(body),url)
            except (ValueError,UnicodeError) as e: raise ResearchError("PARSE_ERROR","Neplatný JSON.") from e
        for place in places:
            self.store.put_entity("place",place.source_id,place.model_dump())
            self.store.link(sid,owner,"place_option",place.source_id,place.model_dump())
        return places

    async def books(self,sid,owner,place_id):
        url = BASE+SEARCH_PATH+"?"+urlencode({"typ":"obec","obec_id":place_id})
        pending, visited, books, warnings = [url], set(), {}, []
        while pending:
            url = pending.pop(0)
            if url in visited: continue
            if len(visited) >= 30:
                return list(books.values()),False,warnings+["Dosažen limit 30 katalogových stránek."]
            visited.add(url)
            body = await self.source.fetch(sid,owner,url,kind="html")
            parsed,pages,warns = parse_books(body.decode("utf-8-sig"),url)
            warnings.extend(warns)
            for book in parsed:
                if book.source_id in books and books[book.source_id] != book:
                    warnings.append("Odlišná metadata knihy "+book.number+" na dalších stránkách.")
                books[book.source_id]=book
                self.store.put_entity("book",book.source_id,book.model_dump())
                self.store.link(sid,owner,"book_option",book.source_id,book.model_dump())
            pending.extend(u for u in pages if u not in visited)
        return list(books.values()),not warnings,warnings

    async def pages(self,sid,owner,book):
        body=await self.source.fetch(sid,owner,book.detail_url,kind="html")
        pages=parse_pages(body.decode("utf-8-sig"),book)
        for page in pages:
            self.source.policy.validate(page.source_url)
            self.store.put_entity("page",page.page_id,page.model_dump())
        return pages
