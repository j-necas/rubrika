"""MZA census records (Sčítací operáty): fixed search form and conservative HTML/JSON parsers.
Source text is data, never instructions."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit, urlencode

from bs4 import BeautifulSoup

from .aron import clip
from .catalog import BASE, _read_array, iiif_from_deepzoom, normalize
from .errors import ResearchError
from .models import CensusSet, HouseNumbers, Page, Place, SearchQuery

APP = BASE + "/scitacioperaty"
AUTOCOMPLETE = APP + "/digisada/autocomplete/"
POST_URL = APP + "/digisada/do_advanced_search"
RESULTS_PATH = "/scitacioperaty/digisada/advanced_search"
PREFIX = "census:"
MAX_PAGES = 5
RESTRICTED_YEARS = frozenset({1921})   # "Snímky z roku 1921 jsou přístupné pouze v síti badatelen MZA."
ARCHIVES = {"MZA v Brně": "216000010", "Blansko": "226201010", "Brno-venkov": "226203010", "Břeclav": "226204010",
            "Havlíčkův Brod": "226101010", "Hodonín": "226205010", "Jihlava": "226102010", "Kroměříž": "226301010",
            "Pelhřimov": "226103010", "Třebíč": "226104010", "Uherské Hradiště": "226302010", "Vsetín": "226303010",
            "Vyškov": "226206010", "Zlín": "226304010", "Znojmo": "226207010", "Žďár nad Sázavou": "226105010"}
_STATUS_ORDER = {"candidate": 0, "needs_review": 1, "excluded": 2}
_COVER_ORDER = {True: 0, None: 1, False: 2}


def covered_first(status_order_key: tuple, covered: bool | None) -> tuple:
    """Insert MZA census's house-number ordering (covered, unknown, not covered) after the status of an ARON sort key."""
    return (status_order_key[0], _COVER_ORDER[covered], *status_order_key[1:])


def _parse_error(message: str) -> ResearchError:
    return ResearchError("PARSE_ERROR", message)


def is_census_id(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def set_id_of(source_id) -> str:
    m = re.fullmatch(r"census:(\d{1,9})", source_id) if isinstance(source_id, str) else None
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Očekáváno ID sady sčítání ve tvaru census:<číslo>.")
    return m[1]


def place_of(source_id) -> tuple[str, str]:
    m = re.fullmatch(r"census:(obec|cobec):(.{1,200})", source_id) if isinstance(source_id, str) else None
    if not m or not m[2].strip():
        raise ResearchError("INVALID_ARGUMENT", "Očekáváno místo sčítání ve tvaru census:obec:<název> nebo census:cobec:<název>.")
    return m[1], m[2]


def set_id_from_url(url: str) -> str:
    p = urlsplit(url)
    m = re.fullmatch(r"/scitacioperaty/digisada/detail/(\d{1,9})/?", p.path)
    if (p.scheme != "https" or p.hostname not in ("www.mza.cz", "mza.cz") or p.port not in (None, 443)
            or p.username or p.password or not m):
        raise ResearchError("INVALID_SOURCE", "Odkaz není detailem sady sčítacích operátů "
                                              "(https://www.mza.cz/scitacioperaty/digisada/detail/<číslo>).")
    return PREFIX + m[1]


def archive_code(name: str) -> str:
    key = normalize(re.sub(r"^\s*SOkA\s+", "", name or "", flags=re.I))
    for label, code in ARCHIVES.items():
        if normalize(label) == key:
            return code
    raise ResearchError("INVALID_ARGUMENT", "Neznámý archiv sčítacích operátů.", allowed=list(ARCHIVES))


def search_form(kind: str, name: str, archive: str | None, year: int | None) -> dict[str, str]:
    """The only form the server ever posts; field order is fixed."""
    return {"archiv": archive or "", "obec": name if kind == "obec" else "", "cobec": name if kind == "cobec" else "",
            "ulice": "", "rscitan": str(year) if year else "", "sumar": "", "submit": "Vyhledat"}


def parse_places(data, kind: str) -> list[Place]:
    if not isinstance(data, list):
        raise _parse_error("Našeptávač sčítacích operátů nevrátil seznam.")
    places = {}
    for item in data:
        name = clip(item.get("value"), 200) if isinstance(item, dict) else None
        if not name:
            raise _parse_error("Neznámá struktura našeptávače sčítacích operátů.")
        label = clip(item.get("label"), 2000) or name
        sid = f"{PREFIX}{kind}:{name}"
        places[sid] = Place(source_id=sid, label=label, names=[name], source_url=APP + "/digisada/advanced_search")
    return list(places.values())


def parse_house_numbers(range_text: str | None, detail_text: str | None) -> HouseNumbers:
    raw = clip(detail_text, 2000) or clip(range_text, 2000) or ""
    m = re.fullmatch(r"\s*(\d{1,5})\s*-\s*(\d{1,5})\s*", range_text or "")
    start, end = (int(m[1]), int(m[2])) if m else (None, None)
    numbers, parsed = set(), bool(raw)
    for part in filter(None, (p.strip() for p in re.split(r"[,;]", raw))):
        r = re.fullmatch(r"(\d{1,5})\s*-\s*(\d{1,5})", part)
        if r and int(r[1]) <= int(r[2]) and len(numbers) + int(r[2]) - int(r[1]) < 2000:
            numbers.update(range(int(r[1]), int(r[2]) + 1))
        elif re.fullmatch(r"\d{1,5}", part) and len(numbers) < 2000:
            numbers.add(int(part))
        else:
            parsed = False
    return HouseNumbers(start=start, end=end, raw=raw, numbers=sorted(numbers), parsed=parsed)


def parse_description_house_numbers(description: str | None) -> HouseNumbers:
    """House numbers from a VČA census description such as 'Hradec Králové čp. 1-50', '... čp. 1-38, 40' or
    '... čp. 51-100 a 200-250'. Every 'čp.' group is read; dashes may be hyphen, en or em dash. Anything not
    understood (another 'čp.' label like 'Kukleny čp. 1-20', words, too many numbers) makes parsed False, so
    house_covered answers only True (never a false 'not covered')."""
    raw = clip(description, 2000) or ""
    groups = re.split(r"čp\.", raw)[1:]
    numbers, parsed = set(), bool(groups)
    for group in groups:
        for part in filter(None, (p.strip() for p in re.split(r"[,;]|\s+a\s+", group))):
            r = re.match(r"(\d{1,5})(?:\s*[-\u2013\u2014]\s*(\d{1,5}))?", part)
            lo, hi = (int(r[1]), int(r[2] or r[1])) if r else (1, 0)
            if not r or lo > hi or len(numbers) + hi - lo >= 2000:
                parsed = False
                continue
            numbers.update(range(lo, hi + 1))
            if part[r.end():].strip():
                parsed = False        # numbers were read, but trailing text (e.g. '(část)') was not understood
    # start/end only for a fully understood description: they let house_covered answer False outside the range.
    return HouseNumbers(start=min(numbers) if numbers and parsed else None, end=max(numbers) if numbers and parsed else None,
                        raw=raw, numbers=sorted(numbers), parsed=parsed)


def house_covered(hn: HouseNumbers, house: str | None) -> bool | None:
    m = re.fullmatch(r"\s*(\d{1,5})\s*", house or "")
    if not m:
        return None
    n = int(m[1])
    if n in hn.numbers:
        return True
    if hn.parsed:
        return False
    if hn.start is not None and hn.end is not None and not hn.start <= n <= hn.end:
        return False
    return None


_YEAR_TOKEN = re.compile(r"\b(1[89]\d\d)\b")


def _year(value) -> int | None:
    """First plausible 4-digit year anywhere in the raw value (e.g. "1921 (sumář)" -> 1921)."""
    m = _YEAR_TOKEN.search(value or "")
    return int(m[1]) if m else None


def _images_available(value) -> bool:
    """Fail closed: an unrecognisable year, or any restricted year token anywhere in the raw
    value (e.g. "1921 (sumář)", "1921/22"), means no images are released."""
    if _year(value) is None:
        return False
    return not any(int(tok) in RESTRICTED_YEARS for tok in _YEAR_TOKEN.findall(value or ""))


def _census_set(set_num: str, f: dict, metadata: dict | None = None) -> CensusSet:
    raw_year = f.get("Rok sčítání")
    return CensusSet(source_id=PREFIX + set_num, code=f.get("Kód sady"), year=_year(raw_year), place=f.get("Město/obec"),
                     part=f.get("Část obce/osada"), street=f.get("Ulice"),
                     house_numbers=parse_house_numbers(f.get("Číslo popisné od-do"), f.get("Rozpis čísel popisných")),
                     archive=f.get("Archiv"), fund=f.get("Název fondu"), images_available=_images_available(raw_year),
                     metadata=metadata or {}, detail_url=f"{APP}/digisada/detail/{set_num}")


def public_set(unit: CensusSet) -> dict:
    """Tool-output dump: omit house_numbers.numbers (can hold up to 2000 entries) to keep
    responses small. Stored entities/links keep the full model dump instead of this."""
    d = unit.model_dump()
    d["house_numbers"] = {k: v for k, v in d["house_numbers"].items() if k != "numbers"}
    return d


def restricted_set_error(unit: CensusSet, citation: str) -> ResearchError:
    if unit.year is None:
        return ResearchError("UNSUPPORTED_SOURCE", "Rok sčítání nelze ověřit; snímky se ze zákonné opatrnosti "
                             "nezpřístupňují.", reason="year_unknown", set=public_set(unit), citation=citation)
    return ResearchError("UNSUPPORTED_SOURCE", "Snímky sčítání z roku 1921 jsou podle MZA přístupné pouze v síti "
                         "badatelen MZA; server je nezpřístupňuje.", reason="reading_room_only",
                         set=public_set(unit), citation=citation)


def _results_card(soup):
    """The results card, identified by its header text, not by markup/CSS class alone (both can
    change independently of the content). Absent -> session lost or markup changed, not zero results."""
    for card in soup.select(".card"):
        h2 = card.find("h2")
        if h2 is not None and " ".join(h2.get_text().split()) == "Vyhledané záznamy":
            return card
    return None


def parse_results(text: str) -> tuple[list[CensusSet], int]:
    soup = BeautifulSoup(text, "lxml")
    card = _results_card(soup)
    if card is None:
        raise _parse_error("Stránka výsledků sčítacích operátů nemá očekávanou strukturu.")
    plain = " ".join(card.get_text(" ").split())
    total = re.search(r"celkem:\s*(\d+)", plain)
    items = card.select("li.list-group-item")
    if not items:
        # Live app: "Nebyly nalezeny žádné vyhovující záznamy." in the card footer.
        if (total and int(total[1]) == 0) or re.search(r"Nebyly nalezeny žádné (?:vyhovující )?záznamy", plain):
            return [], 0
        raise _parse_error("Stránka výsledků sčítacích operátů nemá očekávanou strukturu.")
    if not total:
        raise _parse_error("Stránka výsledků neuvádí celkový počet.")
    rows = []
    for li in items:
        fields = {}
        for label in li.select("span.text-muted"):
            p = label.find_next_sibling("p")
            key = " ".join(label.get_text().split())
            fields[key] = clip(p.get_text(" ", strip=True), 2000 if key == "Rozpis čísel popisných" else 500) if p else None
        link = li.select_one('a[href*="/digisada/detail/"]')
        m = re.search(r"/digisada/detail/(\d{1,9})", link.get("href", "")) if link else None
        if not m or "Rok sčítání" not in fields:
            raise _parse_error("Záznam výsledku sčítacích operátů nemá očekávanou strukturu.")
        rows.append(_census_set(m[1], fields))
    return rows, int(total[1])


def _offset(url: str) -> int:
    m = re.search(r"/advanced_search/(\d{1,5})$", urlsplit(url).path)
    return int(m[1]) if m else 0


def next_page_url(body: bytes, current_url: str) -> str | None:
    text = body.decode("utf-8", "replace")
    current = _offset(current_url)
    offsets = sorted({int(m[1]) for m in re.finditer(r'href="[^"]*?/scitacioperaty/digisada/advanced_search/(\d{1,5})"', text)})
    nxt = next((o for o in offsets if o > current), None)
    return f"{APP}/digisada/advanced_search/{nxt}" if nxt is not None else None


def parse_set_detail(text: str, set_num: str) -> tuple[CensusSet, list[tuple[str, str]]]:
    soup = BeautifulSoup(text, "lxml")
    header = soup.select_one("#digisada-header")
    if not header:
        raise _parse_error("Detail sady nemá očekávanou hlavičku.")
    fields = {}
    for li in header.select("li.nav-item"):
        label = li.select_one("span.small")
        if not label:
            continue
        key = " ".join(label.get_text().split())
        pop = li.select_one("[data-content]")
        value_el = li.select_one("span.font-weight-bolder, div.font-weight-bolder")
        value = pop.get("data-content") if pop is not None else (value_el.get_text(" ", strip=True) if value_el else None)
        fields[key] = clip(value, 2000 if key == "Rozpis čísel popisných" else 500)
    if "Rok sčítání" not in fields:
        raise _parse_error("Detail sady neuvádí rok sčítání.")
    metadata = {}
    extra = soup.select_one("#pill_data_extra")
    for row in extra.select("div.row") if extra else []:
        cols = row.find_all("div", recursive=False)
        if len(cols) == 2:
            k, v = clip(cols[0].get_text(" ", strip=True), 80), clip(cols[1].get_text(" ", strip=True))
            if k and v and len(metadata) < 30:
                metadata[k] = v
    unit = _census_set(set_num, fields, metadata)
    match = re.search(r"\bCreateSeadragon\s*\(", text)
    if not match:
        raise _parse_error("Detail sady neobsahuje známý manifest snímků.")
    sources, end = _read_array(text, match.end())
    while end < len(text) and text[end].isspace():
        end += 1
    if end >= len(text) or text[end] != ",":
        raise _parse_error("Chybí identifikátory snímků.")
    names, _ = _read_array(text, end + 1)
    if len(sources) != len(names):
        raise _parse_error("Nesouhlasí počet zdrojů a identifikátorů snímků.")
    return unit, list(zip(sources, names))


def build_citation(unit: CensusSet) -> str:
    md = unit.metadata
    m = re.search(r"\(([^)]+)\)", md.get("Archiv") or "")
    archive = m[1] if m else unit.archive
    fund = unit.fund or md.get("Název fondu")
    if fund and md.get("Číslo NAD"):
        fund = f"{fund} (NAD {md['Číslo NAD']})"
    ident = " / ".join(x for x in (md.get("Inventární číslo") and f"inv. č. {md['Inventární číslo']}",
                                    md.get("Signatura") and f"sign. {md['Signatura']}") if x)
    place = ", ".join(x for x in (unit.place, unit.part) if x)
    parts = [archive, fund, ident, unit.code and f"kód sady {unit.code}", unit.year and f"sčítání {unit.year}", place]
    return clip("; ".join(p for p in parts if p), 1000) or ""


def set_pages(unit: CensusSet, entries: list[tuple[str, str]], citation: str | None) -> list[Page]:
    manifest = hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()
    pages, seen = [], set()
    for n, (source, name) in enumerate(entries, 1):
        if not name or len(name) > 200 or re.search(r"[/\\\x00-\x1f]", name):
            raise _parse_error("Neplatný název snímku.")
        source = urljoin(unit.detail_url, source)
        if source in seen:
            raise _parse_error("Manifest obsahuje duplicitní zdroj.")
        seen.add(source)
        iiif = iiif_from_deepzoom(source)
        url, kind = (iiif, "iiif") if iiif else (source, "dzi")
        pages.append(Page(page_id=f"{unit.source_id}:{n}", book_id=unit.source_id, scan_number=n, image_key=name,
                          source_url=url, source_type=kind, viewer_url=unit.detail_url, manifest_sha256=manifest,
                          source="census", citation=citation))
    return pages


def census_relevance(unit: CensusSet, query: SearchQuery) -> dict:
    base = {"house_number_covered": house_covered(unit.house_numbers, query.person.house_number)}
    if unit.year is None or (query.year_from is None and query.year_to is None):
        return {**base, "status": "needs_review", "reason": "Rok sčítání nebo období zadání není známo."}
    if (query.year_from or 0) <= unit.year <= (query.year_to or 9999):
        return {**base, "status": "candidate", "reason": "Rok sčítání leží v zadaném období."}
    return {**base, "status": "excluded", "reason": "Rok sčítání leží mimo zadané období."}


def archive_warnings(name: str, got: list[CensusSet]) -> list[str]:
    warnings = []
    archives = sorted({g.archive for g in got if g.archive})
    if len(archives) > 1:
        warnings.append(f'Pod názvem "{name}" jsou sady z více archivů ({", ".join(archives)}); stejnojmenné obce rozlište parametrem district_archive.')
    if any(not g.archive for g in got):
        warnings.append("U některých sad chybí údaj o archivu; stejnojmenné obce nelze automaticky rozlišit.")
    return warnings


def sort_key(row: dict) -> tuple:
    """Rows are CensusSet dumps with a 'relevance' key."""
    rel = row["relevance"]
    return (_STATUS_ORDER[rel["status"]], _COVER_ORDER[rel["house_number_covered"]], row.get("year") or 9999,
            row.get("code") or "", row["source_id"])


class CensusCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    async def places(self, sid, owner, term: str) -> list[Place]:
        found = []
        for kind in ("obec", "cobec"):
            raw = await self.source.fetch(sid, owner, AUTOCOMPLETE + "?" + urlencode({"field": kind, "term": term}), kind="json")
            try:
                data = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError) as e:
                raise _parse_error("Našeptávač sčítacích operátů nevrátil validní JSON.") from e
            found.extend(parse_places(data, kind))
        for place in found:
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, {**place.model_dump(), "source": "census"})
        return found

    async def sets(self, sid, owner, places, archive, year):
        rows, warnings, complete = {}, [], True
        for kind, name in places:
            pages, truncated = await self.source.form_search(sid, owner, POST_URL, search_form(kind, name, archive, year),
                                                             RESULTS_PATH, MAX_PAGES, next_page_url)
            got, total = [], 0
            for body in pages:
                batch, total = parse_results(body.decode("utf-8", "replace"))
                got.extend(batch)
            if truncated or len({g.source_id for g in got}) < total:
                complete = False
                warnings.append(f'Sčítací operáty uvádějí pro "{name}" {total} sad, načteno {len(got)}. Zužte hledání archivem nebo rokem.')
            warnings.extend(archive_warnings(name, got))
            rows.update((g.source_id, g) for g in got)
        sets = list(rows.values())
        for s in sets:
            self.store.put_entity("census_set", s.source_id, s.model_dump())
            self.store.link(sid, owner, "book_option", s.source_id, {**s.model_dump(), "source": "census"})
        return sets, complete, warnings

    async def open_set(self, sid, owner, set_num: str):
        try:
            body = await self.source.fetch(sid, owner, f"{APP}/digisada/detail/{set_num}", kind="html")
        except ResearchError as e:
            if e.code == "SOURCE_UNAVAILABLE" and e.details.get("status") == 404:
                raise ResearchError("NOT_FOUND", "Sada sčítacích operátů neexistuje.", status=404) from e
            raise
        unit, entries = parse_set_detail(body.decode("utf-8", "replace"), set_num)
        citation = build_citation(unit)
        if not unit.images_available:
            raise restricted_set_error(unit, citation)
        pages, warnings = set_pages(unit, entries, citation), []
        allowed = []
        for page in pages:
            try:
                ok = self.source.policy.validate(page.source_url).source == "mza_images"
            except ResearchError as e:
                if e.code != "INVALID_SOURCE":
                    raise
                ok = False
            if not ok:
                warnings.append(f"Snímek {page.scan_number} odkazuje mimo povolený obrazový server a byl vynechán.")
                continue
            allowed.append(page)
        if not allowed:
            raise ResearchError("UNSUPPORTED_SOURCE", "Sada nemá digitalizované snímky dostupné přes server.",
                                reason="no_images", set=unit.model_dump(), citation=citation)
        for page in allowed:
            self.store.put_entity("page", page.page_id, page.model_dump())
        return unit, allowed, warnings, citation
