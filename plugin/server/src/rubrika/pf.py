"""Porta fontium (SOA Plzeň, Sbírka matrik západních Čech): conservative HTML parsers and the catalogue client.
The portal has no JSON API and no place autocomplete; places are derived from the rows of the register search.
Source text is never executed; any structure that is not recognised is a PARSE_ERROR, never an empty result."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString

from .catalog import _read_array, iiif_from_deepzoom, normalize
from .errors import ResearchError
from .census import house_covered
from .models import EventCoverage, HouseNumbers, Page, Periods, PfBook, PfRecord, Place, SearchQuery, YearSpan
from .sources import PF_BOOK_TYPE_PARAM, PF_HOST, PF_SLUG, PF_TYPE_PARAM

BASE = "https://" + PF_HOST
SEARCH_PATH = "/searching/register"
MAX_SEARCH_PAGES = 12        # result pages per list_sources call, all places together: one request each, 10 s at the portal's Crawl-delay
MAX_PLACES_PER_LISTING = 2
MAX_PLACES = 15
SOA_NAME = "Státní oblastní archiv v Plzni"
EVENTS = ("birth", "marriage", "death")
_MARKS = {"*": "birth", "oo": "marriage", "+": "death"}
_PART = re.compile(r"(?:(\d{4})(?:\s*-\s*(\d{4}))?\s+)?(\*|oo|\+)( ?i)?")
_SLUG_PATH = re.compile(rf"/register/({PF_SLUG})/({PF_SLUG})/?")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}")
_DEEPZOOM = re.compile(r"/fcgi-bin/iipsrv\.fcgi\?DeepZoom=((?:register|amtsbuch|census)/[A-Za-z0-9_./-]{1,400})\.jp2\.dzi")
_ID = re.compile(rf"pf:book:({PF_SLUG})/({PF_SLUG})")
_AMTSBUCH_ID = re.compile(rf"pf:amtsbuch:({PF_SLUG})/({PF_SLUG})/({PF_SLUG})")
_CENSUS_ID = re.compile(rf"pf:census:({PF_SLUG})/(\d{{4}})/({PF_SLUG})(?:@(\d{{1,5}}))?")
_AMTSBUCH_PATH = re.compile(rf"/amtsbuch/({PF_SLUG})/({PF_SLUG})/({PF_SLUG})/?")
_CENSUS_PATH = re.compile(rf"/census/({PF_SLUG})/(\d{{4}})/({PF_SLUG})(?:/({PF_SLUG}))?/?")
SECTIONS = ("register", "amtsbuch", "census")
MAX_HOUSE_NUMBERS = 2000


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def parse_dating(text: str) -> dict[str, EventCoverage]:
    """'1785-1830 *, *i' → per event the years of records and of the index. Marks: * births, oo marriages,
    + deaths, a trailing i = index. Anything not fully understood makes every period uncertain (never excludes)."""
    raw = " ".join((text or "").replace("\xa0", " ").replace("–", "-").replace("—", "-").split())
    uncertain = {e: EventCoverage(records=Periods(status="uncertain", raw=raw), index=Periods(status="uncertain", raw=raw))
                 for e in EVENTS}
    if not raw:
        return uncertain
    spans: dict[tuple[str, str], list[YearSpan]] = {}
    current = None
    for part in raw.split(","):
        m = _PART.fullmatch(part.strip())
        if not m:
            return uncertain
        if m[1]:
            a, b = int(m[1]), int(m[2] or m[1])
            if a > b:
                return uncertain
            current = (a, b)
        if current is None:
            return uncertain
        spans.setdefault((_MARKS[m[3]], "index" if m[4] else "records"), []).append(YearSpan(start=current[0], end=current[1]))
    def periods(event, kind):
        found = spans.get((event, kind))
        return Periods(status="known", spans=found, raw=raw) if found else Periods(status="absent", raw=raw)
    return {e: EventCoverage(records=periods(e, "records"), index=periods(e, "index")) for e in EVENTS}


@dataclass
class SearchPage:
    books: list[PfBook] = field(default_factory=list)
    records: list[PfRecord] = field(default_factory=list)
    last_page: int = 0
    warnings: list[str] = field(default_factory=list)


def _last_page(soup) -> int:
    pages = [0]
    for a in soup.select("ul.pager a[href]"):
        values = parse_qs(urlsplit(a["href"]).query).get("page")
        if values and values[0].isdigit():
            pages.append(int(values[0]))
    for li in soup.select("ul.pager li.pager-current"):
        if _text(li).isdigit():
            pages.append(int(_text(li)) - 1)
    return max(pages)


def parse_search(html: str, source_url: str) -> SearchPage:
    soup = BeautifulSoup(html, "lxml")
    if soup.find("form", id=re.compile(r"^views-exposed-form-solr-searching")) is None:
        raise ResearchError("PARSE_ERROR", "Stránka nemá vyhledávací formulář Porta fontium; nejde o prázdný výsledek.")
    content = soup.select_one(".view-content")
    if content is None:
        return SearchPage()     # the portal answers a search without hits with the form and no result table
    table = content.find("table")
    headers = [normalize(_text(th)) for th in table.select("thead th")] if table else []
    if len(headers) != 6 or headers[:4] != ["archiv", "nadpis", "misto", "datace"]:
        raise ResearchError("PARSE_ERROR", "Tabulka výsledků Porta fontium má neočekávané sloupce.")
    rows = table.select("tbody tr")
    if not rows:
        raise ResearchError("PARSE_ERROR", "Tabulka výsledků Porta fontium nemá řádky.")
    page = SearchPage(last_page=_last_page(soup))
    seen = set()
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 6:
            raise ResearchError("PARSE_ERROR", "Řádek výsledků Porta fontium má neočekávaný počet sloupců.")
        link = cells[1].find("a", href=True)
        title = _text(cells[1])
        if link is None:
            page.warnings.append("Řádek bez odkazu na detail knihy: " + title[:200])
            continue
        target = urlsplit(urljoin(source_url, link["href"]))
        m = _SLUG_PATH.fullmatch(target.path)
        if target.scheme != "https" or target.hostname != PF_HOST or target.query or not m:
            raise ResearchError("PARSE_ERROR", "Odkaz na detail knihy vede mimo katalog Porta fontium.")
        book_id = f"pf:book:{m[1]}/{m[2]}"
        if book_id in seen:
            continue
        seen.add(book_id)
        places = [_text(li) for li in cells[2].select("li") if _text(li)] or ([_text(cells[2])] if _text(cells[2]) else [])
        viewer = next((re.fullmatch(r"/iipimage/(\d{1,12})(?:/.*)?", a["href"]) for a in cells[5].find_all("a", href=True)
                       if re.fullmatch(r"/iipimage/(\d{1,12})(?:/.*)?", a["href"])), None)
        dating = _text(cells[3])
        page.books.append(PfBook(
            source_id=book_id, number=title, creator="", coverage=parse_dating(dating),
            detail_url=f"{BASE}/register/{m[1]}/{m[2]}", archive_label=_text(cells[0]) or None,
            places=places, dating_raw=dating, nid=viewer[1] if viewer else None))
    return page


def split_place(text: str) -> tuple[str, list[str]]:
    """'Abertamy (Abertham)' → ('Abertamy', ['Abertham']); the brackets carry the German (or older) names."""
    m = re.fullmatch(r"(.+?)\s*\(([^()]*)\)", text.strip())
    if not m:
        return text.strip(), []
    return m[1].strip(), [v.strip() for v in m[2].split(",") if v.strip()]


def place_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(name)).strip("-")[:80]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def _belongs(entry_key: str, wanted: set[str]) -> bool:
    """The entry is the place itself or one of its districts ("Obvod <place> …", e.g. Obvod Plzeň I-vnitřní město)."""
    return entry_key in wanted or any(entry_key.startswith("obvod-" + w + "-") for w in wanted)


def place_label(name: str, names: list[str]) -> str:
    return name + (f" ({', '.join(names[1:])})" if len(names) > 1 else "")


def candidate_places(books: list[PfBook], term: str, strict: bool = True) -> list[dict]:
    """Places named in the result rows, one per name (German variants merged). strict keeps only places whose name or
    variant contains every word of the term (the portal matches whole words)."""
    wanted = _tokens(term)
    groups: dict[str, dict] = {}
    for book in books:
        for key in {place_key(split_place(t)[0]) for t in book.places}:
            groups.setdefault(key, {"books": set()})["books"].add(book.source_id)
        for text in book.places:
            name, variants = split_place(text)
            key = place_key(name)
            if not key:
                continue
            g = groups[key]
            g.setdefault("names", [name])
            g["id"] = "pf:place:" + key
            g["name"] = name
            g["names"].extend(v for v in variants if v not in g["names"])
    found = [g for g in groups.values() if g.get("id") and (not strict or (wanted and any(wanted <= _tokens(n) for n in g["names"])))]
    exact = normalize(term)
    found.sort(key=lambda g: (0 if any(normalize(n) == exact for n in g["names"]) else 1, -len(g["books"]), g["name"]))
    return [{"id": g["id"], "name": g["name"], "names": g["names"], "books": len(g["books"])} for g in found]


def _detail_places(item) -> list[str]:
    out = []
    for strong in item.find_all("strong"):
        name = _text(strong)
        following = strong.next_sibling
        m = re.match(r"\s*\(([^()]*)\)", str(following)) if isinstance(following, NavigableString) else None
        out.append(f"{name} ({' '.join(m[1].split())})" if m and name else name)
    return out or ([_text(item)] if _text(item) else [])


def parse_detail(html: str, slug: str, section: str = "register") -> dict:
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", attrs={"about": True})
    if article is None or article["about"].rstrip("/") != f"/{section}/" + slug:
        raise ResearchError("PARSE_ERROR", "Detail knihy Porta fontium nemá očekávanou strukturu nebo je to jiná kniha.")
    def items(name):
        return [_text(i) for i in article.select(f".field-name-{name} .field-item") if _text(i)]
    def first(name):
        found = items(name)
        return found[0] if found else None
    signature, archive = first("field-sign"), first("field-archives")
    inventory = first("field-inventory")
    # some official books (e.g. council minutes) have only an inventory number; registers always need a signature
    if not archive or not (signature or (section != "register" and inventory)):
        raise ResearchError("PARSE_ERROR", "Detail knihy nemá signaturu (ani inventární číslo) nebo archiv; citaci nelze sestavit.")
    def own_key(key):
        # register keys start with the book name; official-book keys carry the signature between underscores
        if section == "register":
            return key.startswith(slug.split("/")[1] + "_")
        fond, sig = slug.split("/")[-2:]
        return re.search(rf"(?:^|[-_]){re.escape(fond)}_{re.escape(sig)}_", key) is not None
    def viewer_nids(scope):
        found = set()
        for a in scope.find_all("a", href=True):
            m = re.fullmatch(r"/iipimage/(\d{1,12})(?:/([\w.-]+))?", a["href"])
            if m:
                if m[2] and not own_key(m[2]):
                    raise ResearchError("PARSE_ERROR", "Odkaz na snímek patří jiné knize.")
                found.add(m[1])
        return found
    nids = viewer_nids(article)
    if not nids:     # some details list the images only in the sidebar thumbnail block
        block = soup.select_one("div#block-iipimage-base-iipimage-base-thumbnails")
        nids = viewer_nids(block) if block is not None else set()
    if len(nids) != 1:
        raise ResearchError("PARSE_ERROR", "Detail knihy neodkazuje na jeden prohlížeč snímků.")
    place_item = article.select_one(".field-name-field-doc-place .field-item")
    pages = first("field-pages")
    return {"signature": signature, "inventory": inventory, "archive_label": archive, "fund_number": first("field-fonds"),
            "fund_name": first("field-fondsabbr"), "originator": first("field-originator"),
            "places": _detail_places(place_item) if place_item else [], "types": items("field-register-type"),
            "book_types": items("field-book-type"), "fund_title": first("field-fondsname"),
            "pages": int(pages) if pages and pages.isdigit() else None, "dating": first("field-doc-dates"),
            "nid": nids.pop()}


def citation(detail: dict, slug: str) -> str:
    """Source citation with the portal URL, which the terms of use require whenever the copies are used."""
    label = detail["archive_label"]
    name = SOA_NAME if "státní archiv" in label.casefold() else f"{label} (Porta fontium)"
    return ", ".join(x for x in (name, detail.get("fund_name"), f"sign. {detail['signature']}", f"{BASE}/register/{slug}") if x)


def parse_viewer(html: str, nid: str, section: str = "register", key_ok=None) -> list[tuple[str, str]]:
    """(image key, IIIF info.json URL) per image, from the inline `imagesSet` of the viewer page."""
    found = list(re.finditer(r"\bimagesSet\s*:\s*\[", html))
    if len(found) != 1:
        raise ResearchError("PARSE_ERROR", "Prohlížeč Porta fontium neobsahuje očekávaný seznam snímků imagesSet.")
    sources, _ = _read_array(html, found[0].end() - 1)
    if not sources:
        raise ResearchError("PARSE_ERROR", "Seznam snímků je prázdný.")
    result, seen = [], set()
    for src in sources:
        m = _DEEPZOOM.fullmatch(src)
        if not m or ".." in m[1].split("/") or not m[1].startswith(section + "/"):
            raise ResearchError("PARSE_ERROR", "Neznámý tvar adresy snímku v prohlížeči.")
        key = m[1].rsplit("/", 1)[1]
        if not _KEY.fullmatch(key) or key in seen or (key_ok is not None and not key_ok(key)):
            raise ResearchError("PARSE_ERROR", "Neplatný nebo opakovaný název snímku.")
        seen.add(key)
        info = iiif_from_deepzoom(BASE + src)
        if info is None:
            raise ResearchError("PARSE_ERROR", "Adresu snímku nelze převést na IIIF.")
        result.append((key, info))
    urls = re.search(r"\burlsSet\s*:\s*\[", html)
    if urls:
        listed, _ = _read_array(html, urls.end() - 1)
        if listed != [f"iipimage/{nid}/{k}" for k, _ in result]:
            raise ResearchError("PARSE_ERROR", "Seznam odkazů prohlížeče nesouhlasí se seznamem snímků nebo s knihou.")
    return result


def build_pages(book: PfBook, images: list[tuple[str, str]]) -> list[Page]:
    manifest = hashlib.sha256(json.dumps(images, ensure_ascii=False).encode()).hexdigest()
    return [Page(page_id=f"{book.source_id}:{n}", book_id=book.source_id, scan_number=n, image_key=key, source_url=info,
                 viewer_url=f"{BASE}/iipimage/{book.nid}/{key}", source_type="iiif", manifest_sha256=manifest,
                 source="pf", citation=book.citation, archive="pf") for n, (key, info) in enumerate(images, 1)]


_TITLE_NR = re.compile(r",?\s*Nr\.?\s*(\d{1,5})\s*$")
_CENSUS_PREFIX = re.compile(r"^Census\s+\d{4}\s+")
_HOUSE_LABEL = re.compile(r"\(?\s*Nr\.?\s*(\d{1,5})\s*(?:-\s*Nr\.?\s*(\d{1,5}))?\s*\)?")
_DASH = re.compile(r"\s+-\s+")
LAND_BOOK_TYPES = ("pozemková kniha",)      # the portal's book type (filter value grundbuch)


def parse_years(text: str) -> Periods:
    """'1567-1644, 1752' -> spans. Empty is unknown, anything not fully understood is uncertain (never excludes)."""
    raw = " ".join((text or "").replace("\xa0", " ").replace("–", "-").replace("—", "-").split())
    if not raw:
        return Periods(status="unknown", raw="")
    spans = []
    for part in raw.split(","):
        m = re.fullmatch(r"\s*(\d{4})(?:\s*-\s*(\d{4}))?\s*", part)
        if not m or int(m[1]) > int(m[2] or m[1]):
            return Periods(status="uncertain", raw=raw)
        spans.append(YearSpan(start=int(m[1]), end=int(m[2] or m[1])))
    return Periods(status="known", spans=spans, raw=raw)


def compact_numbers(numbers: list[int], limit: int = 300) -> str:
    """[1, 2, 3, 5] -> '1-3, 5' (bounded)."""
    out, i, nums = [], 0, sorted(set(numbers))
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    text = ", ".join(out)
    return text if len(text) <= limit else text[:limit].rsplit(",", 1)[0] + ", ..."


def census_place(items: list[str], title: str) -> tuple[str, str | None]:
    """(place label, part). The first item is the municipality; later items (suburb, hamlet) and a ' - part' suffix are parts.
    Without a place cell the name comes from the title ('Census 1921 Tachov (Tachau), Nr. 652')."""
    head = items[0] if items else _CENSUS_PREFIX.sub("", re.sub(r"\s+-\s+souhrn\s*/.*$", "", _TITLE_NR.sub("", title)).strip())
    name, variants = split_place(head)
    pieces = _DASH.split(name, maxsplit=1)
    parts = [pieces[1].strip()] if len(pieces) > 1 else []
    variants = list(dict.fromkeys(_DASH.split(v, maxsplit=1)[0].strip() for v in variants if v.strip()))
    parts += items[1:]
    label = pieces[0].strip() + (f" ({', '.join(variants)})" if variants else "")
    return label, "; ".join(parts) or None


def label_numbers(label: str) -> list[int] | None:
    """'Nr. 12' -> [12]; '(Nr. 1 - Nr. 500)' -> 1..500; anything else -> None (not understood)."""
    m = _HOUSE_LABEL.fullmatch(" ".join(label.split()))
    if not m:
        return None
    a, b = int(m[1]), int(m[2] or m[1])
    return list(range(a, b + 1)) if a <= b and b - a < MAX_HOUSE_NUMBERS else None


def house_numbers_of(labels: list[str]) -> HouseNumbers:
    """House numbers of a census unit from the portal's list of houses. parsed only when every label is understood and the
    list fits; otherwise the range is kept but no house is ever reported as not covered from single numbers."""
    numbers, understood = set(), True
    for label in labels:
        found = label_numbers(label)
        if found is None:
            understood = False
        else:
            numbers.update(found)
    if not numbers:
        return HouseNumbers(raw=", ".join(labels)[:300])
    ordered = sorted(numbers)
    fits = len(ordered) <= MAX_HOUSE_NUMBERS
    return HouseNumbers(start=ordered[0], end=ordered[-1], raw=compact_numbers(ordered),
                        numbers=ordered if fits else [], parsed=understood and fits)


def _record_link(cells, source_url: str, section: str):
    link = cells[1].find("a", href=True)
    if link is None:
        return None, None
    target = urlsplit(urljoin(source_url, link["href"]))
    pattern = _AMTSBUCH_PATH if section == "amtsbuch" else _CENSUS_PATH
    m = pattern.fullmatch(target.path)
    if target.scheme != "https" or target.hostname != PF_HOST or target.query or not m:
        raise ResearchError("PARSE_ERROR", "Odkaz na detail v Porta fontium vede mimo katalog.")
    return target.path.rstrip("/"), m


def parse_records(html: str, source_url: str, section: str) -> SearchPage:
    """Result rows of the official-books (amtsbuch) or census tab. Census rows are grouped by municipality and year: the
    portal lists a house-numbered set (e.g. 1921) as one row per house."""
    soup = BeautifulSoup(html, "lxml")
    if soup.find("form", id=re.compile(r"^views-exposed-form-solr-searching")) is None:
        raise ResearchError("PARSE_ERROR", "Stránka nemá vyhledávací formulář Porta fontium; nejde o prázdný výsledek.")
    content = soup.select_one(".view-content")
    if content is None:
        return SearchPage()
    table = content.find("table")
    headers = [normalize(_text(th)) for th in table.select("thead th")] if table else []
    if len(headers) != 6 or headers[:4] != ["archiv", "nadpis", "misto", "datace"]:
        raise ResearchError("PARSE_ERROR", "Tabulka výsledků Porta fontium má neočekávané sloupce.")
    rows = table.select("tbody tr")
    if not rows:
        raise ResearchError("PARSE_ERROR", "Tabulka výsledků Porta fontium nemá řádky.")
    page = SearchPage(last_page=_last_page(soup))
    kind = "land_records" if section == "amtsbuch" else "census"
    seen: dict[str, PfRecord] = {}
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) != 6:
            raise ResearchError("PARSE_ERROR", "Řádek výsledků Porta fontium má neočekávaný počet sloupců.")
        title = _text(cells[1])
        path, m = _record_link(cells, source_url, section)
        if path is None:
            page.warnings.append("Řádek bez odkazu na detail: " + title[:200])
            continue
        items = [_text(li) for li in cells[2].select("li") if _text(li)] or ([_text(cells[2])] if _text(cells[2]) else [])
        viewer = next((re.fullmatch(r"/iipimage/(\d{1,12})(?:/.*)?", a["href"]) for a in cells[5].find_all("a", href=True)
                       if re.fullmatch(r"/iipimage/(\d{1,12})(?:/.*)?", a["href"])), None)
        dating = _text(cells[3])
        if section == "amtsbuch":
            rid = f"pf:amtsbuch:{m[1]}/{m[2]}/{m[3]}"
            if rid in seen:
                continue
            seen[rid] = PfRecord(source_id=rid, kind=kind, number=title, archive_label=_text(cells[0]) or None, places=items,
                                 dating_raw=dating, dating=parse_years(dating), nid=viewer[1] if viewer else None,
                                 detail_path=path, detail_url=BASE + path)
            continue
        rid = f"pf:census:{m[1]}/{m[2]}/{m[3]}"
        nr = _TITLE_NR.search(title)
        if rid in seen:
            if nr and len(seen[rid].house_nodes) < MAX_HOUSE_NUMBERS:
                seen[rid].house_nodes.setdefault(int(nr[1]), path)
            if nr and int(nr[1]) not in seen[rid].house_numbers.numbers:
                hn = seen[rid].house_numbers
                nums = sorted({*hn.numbers, int(nr[1])})[:MAX_HOUSE_NUMBERS]
                seen[rid].house_numbers = HouseNumbers(numbers=nums, raw=compact_numbers(nums), parsed=False)
            continue
        label, part = census_place(items, title)
        base_title = _TITLE_NR.sub("", title).strip()
        seen[rid] = PfRecord(
            source_id=rid, kind=kind, number=base_title, archive_label=_text(cells[0]) or None, places=[label], part=part,
            dating_raw=dating, dating=Periods(status="known", spans=[YearSpan(start=int(m[2]), end=int(m[2]))], raw=m[2]),
            year=int(m[2]), nid=viewer[1] if viewer else None, detail_path=path, detail_url=BASE + path,
            house_nodes={int(nr[1]): path} if nr else {},
            house_numbers=HouseNumbers(numbers=[int(nr[1])], raw=f"Nr. {nr[1]}", parsed=False) if nr else HouseNumbers())
    page.records = list(seen.values())
    return page


def parse_census_detail(html: str, slug: str) -> dict:
    """Census node: archive, fund, inventory, year, and the list of houses ("Nr.") with the viewer node of each. The house the
    page shows is not a link (a <big>); its viewer is the one in the thumbnail block."""
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", attrs={"about": True})
    if article is None or article["about"].rstrip("/") != "/census/" + slug:
        raise ResearchError("PARSE_ERROR", "Detail sčítání Porta fontium nemá očekávanou strukturu nebo je to jiný záznam.")
    def items(name):
        return [_text(i) for i in article.select(f".field-name-{name} .field-item") if _text(i)]
    def first(name):
        found = items(name)
        return found[0] if found else None
    archive = first("field-archives")
    if not archive:
        raise ResearchError("PARSE_ERROR", "Detail sčítání nemá archiv; citaci nelze sestavit.")
    houses: list[tuple[str, str | None]] = []
    nav = article.select_one(".field-name-field-navi")
    for node in (nav.find_all(["a", "big"]) if nav is not None else []):
        label = _text(node)
        if not label:
            continue
        if node.name == "big":
            houses.append((label, None))
            continue
        m = re.fullmatch(r"/iipimage/(\d{1,12})(?:\?language=\w{2})?", node.get("href", ""))
        if not m:
            raise ResearchError("PARSE_ERROR", "Odkaz na dům v detailu sčítání má neznámý tvar.")
        houses.append((label, m[1]))
    own = set()
    block = soup.select_one("div#block-iipimage-base-iipimage-base-thumbnails")
    for a in (block.find_all("a", href=True) if block is not None else []):
        m = re.fullmatch(r"/iipimage/(\d{1,12})(?:/[\w.-]+)?", a["href"])
        if m:
            own.add(m[1])
    if len(own) > 1:
        raise ResearchError("PARSE_ERROR", "Detail sčítání neodkazuje na jeden prohlížeč snímků.")
    return {"archive_label": archive, "fund_number": first("field-fonds"), "fund_name": first("field-fondsabbr"),
            "inventory": first("field-inventory"), "dating": first("field-doc-dates"), "houses": houses,
            "own_nid": own.pop() if own else None}


def pick_house(houses: list[tuple[str, str | None]], number: int | None) -> tuple[str, str | None] | None:
    """The house entry whose label covers the number, a single-house label before a range."""
    if number is None:
        return None
    best = None
    for label, nid in houses:
        found = label_numbers(label)
        if found and number in found and (best is None or len(found) < best[0]):
            best = (len(found), (label, nid))
    return best[1] if best else None


def record_citation(detail: dict, section: str, slug: str, title: str | None = None) -> str:
    label = detail["archive_label"]
    name = SOA_NAME if "státní archiv" in label.casefold() else f"{label} (Porta fontium)"
    ref = f"sign. {detail['signature']}" if detail.get("signature") else (f"inv. č. {detail['inventory']}" if detail.get("inventory") else None)
    return ", ".join(x for x in (name, detail.get("fund_title") or detail.get("fund_name"), title, ref, f"{BASE}/{section}/{slug}") if x)


def record_relevance(rec: PfRecord, query: SearchQuery) -> dict:
    """Land books: overlap of the dating with the period; census: the census year in the period (the house number only orders)."""
    if rec.kind == "census":
        base = {"house_number_covered": house_covered(rec.house_numbers, query.person.house_number)}
        if rec.year is None or (query.year_from is None and query.year_to is None):
            return {**base, "status": "needs_review", "reason": "Rok sčítání nebo období zadání není známo."}
        if (query.year_from or 0) <= rec.year <= (query.year_to or 9999):
            return {**base, "status": "candidate", "reason": "Rok sčítání leží v zadaném období."}
        return {**base, "status": "excluded", "reason": "Rok sčítání leží mimo zadané období."}
    base = {"category": rec.category}
    if query.year_from is None and query.year_to is None:
        return {**base, "status": "needs_review", "reason": "Zadání neurčuje období."}
    lo, hi = query.year_from or 0, query.year_to or 9999
    if rec.dating.status == "known":
        if any((sp.start or 0) <= hi and (sp.end or 9999) >= lo for sp in rec.dating.spans):
            return {**base, "status": "candidate", "reason": "Datace knihy se překrývá se zadaným obdobím."}
        return {**base, "status": "excluded", "reason": "Datace knihy se zadaným obdobím nepřekrývá."}
    return {**base, "status": "needs_review", "reason": "Datace chybí nebo je nejistá; knihu nelze bezpečně vyloučit."}


def record_from_url(url: str) -> str | None:
    """Source ID for a detail link to an official book or census unit; None for any other Porta fontium link."""
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != PF_HOST or p.username or p.password or p.port not in (None, 443) or p.query:
        return None
    if m := _AMTSBUCH_PATH.fullmatch(p.path):
        return f"pf:amtsbuch:{m[1]}/{m[2]}/{m[3]}"
    if m := _CENSUS_PATH.fullmatch(p.path):
        return f"pf:census:{m[1]}/{m[2]}/{m[3]}"
    return None


def record_ref(source_id: str) -> tuple[str, str, int | None]:
    """(section, slug, house) of a pf:amtsbuch: or pf:census: ID; house is the optional @<Nr.> suffix of a census unit."""
    if m := _AMTSBUCH_ID.fullmatch(source_id):
        return "amtsbuch", f"{m[1]}/{m[2]}/{m[3]}", None
    if m := _CENSUS_ID.fullmatch(source_id):
        return "census", f"{m[1]}/{m[2]}/{m[3]}", int(m[4]) if m[4] else None
    raise ResearchError("INVALID_ARGUMENT", "Úřední knihu nebo sčítání Porta fontium otevřete podle ID pf:amtsbuch:<…> nebo pf:census:<…> z list_sources.")


def is_record_id(value) -> bool:
    return isinstance(value, str) and value.startswith(("pf:amtsbuch:", "pf:census:"))


def book_id_from_url(url: str) -> str:
    if (record := record_from_url(url)) is not None:
        return record
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != PF_HOST or p.username or p.password or p.port not in (None, 443):
        raise ResearchError("INVALID_SOURCE", "Je vyžadován HTTPS odkaz na detail knihy v Porta fontium.")
    m = _SLUG_PATH.fullmatch(p.path)
    if not m or p.query:
        raise ResearchError("INVALID_SOURCE", "Odkaz není detailem knihy Porta fontium (/register/<archiv>/<kniha>).")
    return f"pf:book:{m[1]}/{m[2]}"


def slug_of(book_id: str) -> str:
    m = _ID.fullmatch(book_id)
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Knihu Porta fontium otevřete podle ID ve tvaru pf:book:<archiv>/<kniha> z list_sources.")
    return f"{m[1]}/{m[2]}"


def search_url(term: str, page: int = 0, types: tuple[str, ...] = (), section: str = "register") -> str:
    """Search URL of one tab. The census tab lists results only in the list view (mode=0); the official-books tab
    filters by book type (field_book_type[]), the register tab by register mark (field_register_type[])."""
    param = PF_BOOK_TYPE_PARAM if section == "amtsbuch" else PF_TYPE_PARAM
    query = [*([("mode", "0")] if section == "census" else []),
             ("search_api_views_fulltext", ""), ("search_api_views_fulltext_1", term), ("search_api_views_fulltext_2", ""),
             ("search_api_aggregation_3", ""), ("field_doc_dates_field_doc_dates_from", ""),
             *[(param, t) for t in types], *([("page", str(page))] if page else [])]
    return BASE + "/searching/" + section + "?" + "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query)


class PfCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    async def _search(self, sid, owner, term, page=0) -> SearchPage:
        url = search_url(term, page)
        body = await self.source.fetch(sid, owner, url, kind="html")
        result = parse_search(body.decode("utf-8-sig"), url)
        if not result.books:
            self.source.invalidate(url)     # "no hits" may be a misspelling; never serve it from the cache as a fact
        return result

    async def _search_section(self, sid, owner, section, term, page=0, types=()) -> SearchPage:
        url = search_url(term, page, types, section)
        body = await self.source.fetch(sid, owner, url, kind="html")
        result = parse_records(body.decode("utf-8-sig"), url, section)
        if not result.records:
            self.source.invalidate(url)     # "no hits" may be a misspelling; never serve it from the cache as a fact
        return result

    async def places(self, sid, owner, term, kind="parish_registers") -> tuple[list[Place], list[str]]:
        section = {"parish_registers": "register", "land_records": "amtsbuch", "census": "census"}[kind]
        page = await (self._search(sid, owner, term) if section == "register" else self._search_section(sid, owner, section, term))
        noun = "knih" if section != "census" else "jednotek"
        hits = page.books if section == "register" else page.records
        warnings = list(page.warnings)
        if page.last_page:
            warnings.append(f"Výsledky Porta fontium mají {page.last_page + 1} stran; místa se odvozují jen z první strany "
                            f"({len(hits)} {noun}). Chybí-li hledané místo, upřesněte název.")
        found = candidate_places(hits, term)
        if not found and hits:
            found = candidate_places(hits, term, strict=False)
            warnings.append(f"Katalog našel {'knihy' if section == 'register' else 'záznamy'}, ale žádné místo v nich se nejmenuje jako hledaný výraz (celá slova, i německy); "
                            "nabízená místa jsou z jejich seznamů.")
        if len(found) > MAX_PLACES:
            warnings.append(f"Zobrazeno {MAX_PLACES} míst z {len(found)}; upřesněte hledaný název.")
            found = found[:MAX_PLACES]
        if not found:
            warnings.append(f"Porta fontium k tomuto výrazu nevrátilo žádné {'knihy' if section == 'register' else 'záznamy'} ani místa. Portál hledá celá slova; zkuste jiný "
                            "tvar názvu, německý název nebo název bez upřesnění. Nejde o důkaz, že místo v portálu není.")
        if found:
            warnings.append("Porta fontium nerozlišuje obce stejného jména; ověřte podle původce a ostatních míst knihy, že jde o správnou obec.")
        url = search_url(term, section=section)
        def label(g):
            key = g["id"].removeprefix("pf:place:")
            districts = [h["name"] for h in found if h["id"].removeprefix("pf:place:").startswith("obvod-" + key + "-")]
            return place_label(g["name"], g["names"]) + (" – včetně obvodů města" if districts else "")
        places = [Place(source_id=g["id"], label=label(g), names=g["names"], source_url=url) for g in found]
        for place in places:
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
        return places, warnings

    async def books(self, sid, owner, place_ids) -> tuple[list[PfBook], bool, list[str]]:
        """Books of the places, from the unfiltered register search (relevance is ranked from the marks; a book without
        marks stays needs_review). One page budget for the whole call keeps it under the tool timeout."""
        books: dict[str, PfBook] = {}
        warnings, complete, budget = [], True, MAX_SEARCH_PAGES
        for pid in place_ids:
            entity = self.store.entity("place", pid)
            if not entity or not entity.get("names"):
                raise ResearchError("NOT_FOUND", "Místo Porta fontium není známé; nejprve použijte find_place.")
            term, key = entity["names"][0], pid.removeprefix("pf:place:")
            if budget < 1:
                complete = False
                warnings.append(f"Místo {term} nebylo prohledáno: vyčerpán limit {MAX_SEARCH_PAGES} stran výsledků na jedno volání.")
                continue
            first = await self._search(sid, owner, term, 0)
            budget -= 1
            pages = [first]
            if not first.books:
                complete = False
                warnings.append(f"Porta fontium pro {term} nevrátilo žádné knihy. Portál hledá celá slova; místo může být psáno jinak "
                                "(německý název, jiný tvar). Nejde o důkaz, že knihy neexistují.")
            for n in range(1, min(first.last_page, budget) + 1):
                pages.append(await self._search(sid, owner, term, n))
            budget -= len(pages) - 1
            if first.last_page + 1 > len(pages):
                complete = False
                warnings.append(f"Výsledky pro {term} mají {first.last_page + 1} stran; načteno {len(pages)} (limit {MAX_SEARCH_PAGES} "
                                "stran na volání). Knihy na dalších stranách chybí.")
            wanted = {key, *(place_key(n) for n in entity["names"])} - {""}
            found = kept = 0
            others: dict[str, int] = {}
            for page in pages:
                warnings.extend(page.warnings)
                for book in page.books:
                    found += 1
                    if any(_belongs(place_key(n), wanted) for t in book.places for n in [split_place(t)[0], *split_place(t)[1]]):
                        kept += 1
                        books.setdefault(book.source_id, book)
                    else:
                        for t in book.places:
                            name = split_place(t)[0]
                            if _tokens(term) <= _tokens(name):
                                others[name] = others.get(name, 0) + 1
            if found > kept:
                hint = (" Mezi nimi jsou knihy míst " + ", ".join(f"{n} ({c})" for n, c in sorted(others.items(), key=lambda x: -x[1])[:3])
                        + "; vypište je zvlášť podle jejich place_id z find_place.") if others else ""
                warnings.append(f"Portál vrátil {found} knih, {kept} z nich má místo {term} v seznamu míst; {found - kept} bez něj "
                                f"(ve výpisu nejsou).{hint}")
        for book in books.values():
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return list(books.values()), complete and not any("bez odkazu" in w for w in warnings), warnings

    async def open_register(self, sid, owner, book: PfBook) -> tuple[PfBook, list[Page], list[str]]:
        slug = slug_of(book.source_id)
        body = await self.source.fetch(sid, owner, f"{BASE}/register/{slug}", kind="html")
        detail = parse_detail(body.decode("utf-8-sig"), slug)
        if book.nid and book.nid != detail["nid"]:
            raise ResearchError("PARSE_ERROR", "Prohlížeč v detailu knihy neodpovídá výpisu.")
        viewer = await self.source.fetch(sid, owner, f"{BASE}/iipimage/{detail['nid']}", kind="html")
        images = parse_viewer(viewer.decode("utf-8-sig"), detail["nid"])
        opened = PfBook.model_validate({
            **book.model_dump(), "creator": detail["originator"] or book.creator, "signature": detail["signature"],
            "archive_label": detail["archive_label"], "fund_number": detail["fund_number"], "fund_name": detail["fund_name"],
            "types": detail["types"], "places": detail["places"] or book.places, "nid": detail["nid"],
            "image_count": len(images), "citation": citation(detail, slug), "detail_url": f"{BASE}/register/{slug}"})
        pages = build_pages(opened, images)
        for page in pages:
            self.source.policy.validate(page.source_url)
            self.store.put_entity("page", page.page_id, page.model_dump())
        return opened, pages, []

    async def records(self, sid, owner, place_ids, kind) -> tuple[list[PfRecord], bool, list[str]]:
        """The whole listing of the places is kept per research, so paging with a cursor makes no request to the portal."""
        key = "pf:records:" + sid + ":" + kind + ":" + hashlib.sha256(json.dumps(sorted(place_ids)).encode()).hexdigest()[:16]
        cached = self.store.entity("listing", key, ttl=self.source.cfg.metadata_ttl)
        if cached is not None:
            records = [PfRecord.model_validate(r) for r in cached["records"]]
            for rec in records:
                self.store.put_entity("book", rec.source_id, rec.model_dump())
                self.store.link(sid, owner, "book_option", rec.source_id, rec.model_dump())
            return records, cached["complete"], cached["warnings"]
        records, complete, warnings = await self._records(sid, owner, place_ids, kind)
        if complete:      # a cut listing is not kept: the user can retry (or narrow the place) instead of getting the cut one for the whole ttl
            self.store.put_entity("listing", key, {"records": [r.model_dump() for r in records], "complete": complete, "warnings": warnings})
        return records, complete, warnings

    async def _records(self, sid, owner, place_ids, kind) -> tuple[list[PfRecord], bool, list[str]]:
        """Official books (land_records) or census units of the places from the tab's search. Land books are told from the
        other official books by the portal's own book type filter (grundbuch), so a place costs two passes; the same
        page budget as for registers keeps the call under the tool timeout."""
        section = "amtsbuch" if kind == "land_records" else "census"
        passes = [("land", ("grundbuch",)), ("all", ())] if section == "amtsbuch" else [("all", ())]
        records: dict[str, PfRecord] = {}
        land_ids: set[str] = set()
        warnings, complete, budget = [], True, MAX_SEARCH_PAGES
        category_certain = True
        for pid in place_ids:
            entity = self.store.entity("place", pid)
            if not entity or not entity.get("names"):
                raise ResearchError("NOT_FOUND", "Místo Porta fontium není známé; nejprve použijte find_place.")
            term, key = entity["names"][0], pid.removeprefix("pf:place:")
            wanted = {key, *(place_key(n) for n in entity["names"])} - {""}
            found = kept = land_kept = 0
            place_certain = True
            others: dict[str, int] = {}
            for name, types in passes:
                if budget < 1:
                    complete = False
                    if name == "land":
                        category_certain = place_certain = False
                    warnings.append(f"Místo {term} nebylo prohledáno{' (' + ('pozemkové knihy' if name == 'land' else 'ostatní úřední knihy') + ')' if section == 'amtsbuch' else ''}"
                                    f": vyčerpán limit {MAX_SEARCH_PAGES} stran výsledků na jedno volání.")
                    continue
                first = await self._search_section(sid, owner, section, term, 0, types)
                budget -= 1
                pages = [first]
                for n in range(1, min(first.last_page, budget) + 1):
                    pages.append(await self._search_section(sid, owner, section, term, n, types))
                budget -= len(pages) - 1
                if first.last_page + 1 > len(pages):
                    complete = False
                    if name == "land":
                        category_certain = place_certain = False
                    warnings.append(f"Výsledky pro {term} mají {first.last_page + 1} stran; načteno {len(pages)} (limit {MAX_SEARCH_PAGES} "
                                    "stran na volání). Záznamy na dalších stranách chybí.")
                if not first.records and name == "all":
                    complete = False
                    warnings.append(f"Porta fontium pro {term} nevrátilo žádné {'úřední knihy' if section == 'amtsbuch' else 'jednotky sčítání'}. "
                                    "Portál hledá celá slova; místo může být psáno jinak (německý název, jiný tvar). "
                                    "Nejde o důkaz, že prameny neexistují.")
                for page in pages:
                    warnings.extend(page.warnings)
                    for rec in page.records:
                        if name == "land":
                            land_ids.add(rec.source_id)
                        if name == "all":
                            found += 1
                        if any(_belongs(place_key(n), wanted) for t in rec.places for n in [split_place(t)[0], *split_place(t)[1]]):
                            if name == "all":
                                kept += 1
                            else:
                                land_kept += 1
                            records.setdefault(rec.source_id, rec)
                        elif name == "all":
                            for t in rec.places:
                                nm = split_place(t)[0]
                                if _tokens(term) <= _tokens(nm):
                                    others[nm] = others.get(nm, 0) + 1
            if section == "amtsbuch" and place_certain and land_kept == 0:
                warnings.append(f"Pozemkové knihy portál pro místo {term} nevrátil (filtr typu knihy „pozemková kniha“ prošel celý a nic pro toto "
                                "místo nenašel); ostatní úřední knihy jsou category=other. Nejde o důkaz, že pozemkové knihy neexistují: "
                                "knihu panství může portál vést pod jiným místem.")
            if found > kept:
                hint = (" Mezi nimi jsou záznamy míst " + ", ".join(f"{n} ({c})" for n, c in sorted(others.items(), key=lambda x: -x[1])[:3])
                        + "; vypište je zvlášť podle jejich place_id z find_place.") if others else ""
                warnings.append(f"Portál vrátil {found} záznamů, {kept} z nich má místo {term} v seznamu míst; {found - kept} bez něj "
                                f"(ve výpisu nejsou).{hint}")
        for rec in records.values():
            if section == "amtsbuch":
                rec.category = "land_book" if rec.source_id in land_ids else "other"
                rec.category_certain = rec.source_id in land_ids or category_certain
            self.store.put_entity("book", rec.source_id, rec.model_dump())
            self.store.link(sid, owner, "book_option", rec.source_id, rec.model_dump())
        if section == "amtsbuch" and not category_certain:
            warnings.append("Druh knih (pozemková kniha, či jiná úřední kniha) se nepodařilo ověřit u všech knih; nezařazené jsou vedeny "
                            "jako „other“ a druh určuje až detail knihy při otevření.")
        return list(records.values()), complete and not any("bez odkazu" in w for w in warnings), warnings

    async def open_record(self, sid, owner, rec: PfRecord, house: int | None = None) -> tuple[PfRecord, list[Page], list[str]]:
        """Detail + viewer of one official book or census unit; a census unit shows the images of one house (Nr.)."""
        section, base_slug, at_house = record_ref(rec.source_id)
        house = house if house is not None else at_house
        # A census unit grouped from house rows opens through the first row's node (the 3-segment path need not be a page).
        path = rec.detail_path or f"/{section}/{base_slug}"
        m = (_AMTSBUCH_PATH if section == "amtsbuch" else _CENSUS_PATH).fullmatch(path)
        if not m or not (path == f"/{section}/{base_slug}" or path.startswith(f"/{section}/{base_slug}/")):
            raise ResearchError("INVALID_SOURCE", "Adresa detailu nepatří k této jednotce Porta fontium.")
        slug = path.removeprefix(f"/{section}/").rstrip("/")
        body = await self.source.fetch(sid, owner, BASE + path, kind="html")
        html = body.decode("utf-8-sig")
        warnings: list[str] = []
        if section == "amtsbuch":
            detail = parse_detail(html, slug, "amtsbuch")
            nid = detail["nid"]
            if rec.nid and rec.nid != nid:
                raise ResearchError("PARSE_ERROR", "Prohlížeč v detailu knihy neodpovídá výpisu.")
            types = detail["book_types"]
            category = ("land_book" if any(t.casefold() in LAND_BOOK_TYPES for t in types) else "other") if types else rec.category
            extra = {"signature": detail["signature"], "fund_number": detail["fund_number"],
                     "fund_name": detail["fund_title"] or detail["fund_name"], "originator": detail["originator"],
                     "book_types": types, "places": detail["places"] or rec.places, "category": category,
                     "category_certain": bool(types) or rec.category_certain,
                     "dating_raw": detail["dating"] or rec.dating_raw, "dating": parse_years(detail["dating"]) if detail["dating"] else rec.dating}
            title = None
            selected = None
        else:
            detail = parse_census_detail(html, slug)
            own = detail["own_nid"] or rec.nid
            houses = detail["houses"]
            chosen = pick_house(houses, house)
            if not houses:
                warnings.append("Detail sčítání nemá rozpis domů; zobrazují se snímky, které portál ukazuje pro tuto jednotku (dům není určen).")
            elif house is not None and chosen is None:
                warnings.append(f"Číslo {house} není v rozpisu domů sčítání; zobrazují se snímky domu, který portál ukazuje jako první.")
            nid = (chosen[1] if chosen and chosen[1] else own)
            selected = chosen[0] if chosen else next((label for label, n in houses if n is None), None)
            if nid is None:
                raise ResearchError("PARSE_ERROR", "Detail sčítání neodkazuje na prohlížeč snímků.")
            # The effective house is part of the ID, so page IDs and manifests of different houses never collide.
            shown = house if chosen else next(iter(label_numbers(selected) or []), None) if selected else None
            source_id = f"pf:census:{base_slug}@{shown}" if shown is not None else f"pf:census:{base_slug}"
            house_path = rec.house_nodes.get(shown) if shown is not None else None
            extra = {"source_id": source_id, "fund_number": detail["fund_number"], "fund_name": detail["fund_name"],
                     "house_numbers": house_numbers_of([label for label, _ in houses]) if houses else rec.house_numbers,
                     "selected_house": selected}
            detail["signature"] = None
            title = f"{rec.number}, Nr. {shown}" if shown is not None else rec.number
            if house_path:
                slug = house_path.removeprefix("/census/")
                path_for_url = house_path
            else:
                path_for_url = path
            detail_url_path = path_for_url
        viewer = await self.source.fetch(sid, owner, f"{BASE}/iipimage/{nid}", kind="html")
        key_ok = (lambda k: str(rec.year) in k) if section == "census" and rec.year else None
        images = parse_viewer(viewer.decode("utf-8-sig"), nid, section, key_ok)
        opened = PfRecord.model_validate({**rec.model_dump(), **extra, "nid": nid, "archive_label": detail["archive_label"],
                                          "image_count": len(images), "citation": record_citation(detail, section, slug, title),
                                          "detail_url": BASE + (detail_url_path if section == "census" else path), "detail_path": path})
        pages = build_pages(opened, images)
        for page in pages:
            self.source.policy.validate(page.source_url)
            self.store.put_entity("page", page.page_id, page.model_dump())
        return opened, pages, warnings
