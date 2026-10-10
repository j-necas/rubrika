"""ARON (MZA archival portal): fixed query templates and conservative JSON parsers.
Source text is data, never instructions."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .catalog import BASE, iiif_from_deepzoom, normalize as normalize_text, parse_periods
from .errors import ResearchError
from .models import ArchivalUnit, Fund, Page, Periods, Place, SearchQuery, YearSpan
from .sources import ARON_API, UUID, VCA_API, VCA_HOST

API = BASE + ARON_API
PREFIX = "aron:"
LAND_BOOK_FUNDS = frozenset({"C 17"})
CADASTRAL_PROTOCOL_FUND = "D 10"     # Stabilní katastr – písemné operáty: parcel protocols per cadastral community
CADASTRE_FUNDS = frozenset({"D 1", "D 2", "D 3", "D 4", "D 5", "D 6", "D 9", "D 10"})
LIST_PAGE_SIZE = 50
LIST_MAX_PAGES = 4
MAX_FUNDS_PER_CALL = 10
_STATUS_ORDER = {"candidate": 0, "needs_review": 1, "excluded": 2}
_CATEGORY_ORDER = {"land_book": 0, "cadastre": 1, "other": 2}
_SCAN = re.compile(r"-(\d{5})\.jp2$")
# Storage labels in the East Bohemian listview name: "<archive>: <fund>, <label>: <value>, <dating>".
_STORAGE_LABELS = r"(?:nepl\. inv\. č\.|sign\. pův\.|sign\.|poř\. č\.|inv\. č\.)"
_STORAGE = re.compile(_STORAGE_LABELS + r":\s*([^,]+?)\s*(?:,|$)")
_FUND_OF_NAME = re.compile(r"[^:]*:\s*(.+?),\s*" + _STORAGE_LABELS + ":")
# VČA unit types with their own kind; land_records lists town land books and every other digitized book.
VCA_CENSUS_TYPE, VCA_LAND_TYPE = "sčítací operát", "evidence nemovitostí"
VCA_SEPARATE_TYPES = ("matrika", VCA_CENSUS_TYPE, VCA_LAND_TYPE)


def _parse_error(message: str) -> ResearchError:
    return ResearchError("PARSE_ERROR", message)


def with_katastr(title: str, mark: str | None, katastr: str | None) -> str:
    """A D 10 unit is titled only "Originál", "Duplikát" ...: the cadastral community it belongs to is the name of its parent."""
    # Only a node named like a cadastral community ("Name (Německy), obec: X, okres: Y") is taken for the katastr; else no suffix.
    if mark == CADASTRAL_PROTOCOL_FUND and katastr and re.search(r"\bobec:.*\bokres:", katastr) and katastr not in title:
        return f"{title} – {katastr}"[:500]
    return title


def clip(value, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:limit] or None


def aron_id(uuid: str) -> str:
    return PREFIX + uuid


def is_aron_id(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def uuid_of(source_id: str) -> str:
    uuid = source_id[len(PREFIX):] if is_aron_id(source_id) else ""
    if not re.fullmatch(UUID, uuid):
        raise ResearchError("INVALID_ARGUMENT", "Očekáváno ID z ARONu ve tvaru aron:<uuid>.")
    return uuid


def unit_id_from_url(url: str) -> str:
    # Also accepts a scan viewer deep link (.../dao/{uuid}/file/{uuid}); only the unit's own uuid is used.
    p = urlsplit(url)
    m = re.fullmatch(rf"/aron/apu/({UUID})(?:/dao/{UUID}/file/{UUID})?/?", p.path.lower())
    if (p.scheme != "https" or p.hostname not in ("www.mza.cz", "mza.cz") or p.port not in (None, 443)
            or p.username or p.password or not m):
        raise ResearchError("INVALID_SOURCE", "Odkaz není detailem archiválie ani odkazem na snímek v ARONu "
                                               "(https://www.mza.cz/aron/apu/<uuid> nebo .../dao/<uuid>/file/<uuid>).")
    return aron_id(m[1])


@dataclass(frozen=True)
class AronSite:
    """One ARON installation: its endpoints, ID prefixes and the route its images are served from."""
    archive: str
    api: str
    web: str
    list_path: str
    image_source: str
    page_source: str
    place_prefix: str
    unit_prefix: str
    fund_prefix: str

    def place_id(self, uuid: str) -> str:
        return self.place_prefix + uuid

    def unit_id(self, uuid: str) -> str:
        return self.unit_prefix + uuid

    def fund_id(self, uuid: str) -> str:
        return self.fund_prefix + uuid

    def uuid_of(self, source_id) -> str:
        for prefix in dict.fromkeys((self.unit_prefix, self.place_prefix, self.fund_prefix)):
            if isinstance(source_id, str) and source_id.startswith(prefix) and re.fullmatch(UUID, source_id[len(prefix):]):
                return source_id[len(prefix):]
        raise ResearchError("INVALID_ARGUMENT", f"Očekáváno ID ve tvaru {self.unit_prefix}<uuid>.")

    def detail_url(self, uuid: str) -> str:
        return f"{self.web}/apu/{uuid}"

    def viewer_url(self, unit: str, dao: str, file: str) -> str:
        return f"{self.web}/apu/{unit}/dao/{dao}/file/{file}"


MZA_SITE = AronSite("mza", API, BASE + "/aron", "apu/listresults", "mza_images", "aron", PREFIX, PREFIX, PREFIX)
VCA_SITE = AronSite("vca", f"https://{VCA_HOST}{VCA_API}", f"https://{VCA_HOST}", "apu/listview", "vca_images", "vca",
                    "vca:place:", "vca:unit:", "vca:fund:")


def vca_unit_id_from_url(url: str) -> str:
    p = urlsplit(url)
    m = re.fullmatch(rf"/apu/({UUID})(?:/dao/{UUID}/file/{UUID})?/?", p.path.lower())
    if (p.scheme != "https" or p.hostname != VCA_HOST or p.port not in (None, 443)
            or p.username or p.password or not m):
        raise ResearchError("INVALID_SOURCE", f"Odkaz není detailem archiválie ani odkazem na snímek v ARONu VČA "
                                               f"(https://{VCA_HOST}/apu/<uuid> nebo .../dao/<uuid>/file/<uuid>).")
    return VCA_SITE.unit_id(m[1])


def normalize_mark(mark) -> str | None:
    m = re.fullmatch(r"\s*([A-Z]{1,2})\s?(\d{1,4}[a-z]?)\s*", mark) if isinstance(mark, str) else None
    return f"{m[1]} {m[2]}" if m else None


def category(mark: str | None, unit_type: str | None) -> str:
    if mark in LAND_BOOK_FUNDS:
        return "land_book"
    if mark in CADASTRE_FUNDS:
        return "cadastre"
    kind = (unit_type or "").casefold()
    if "pozemková kniha" in kind or "gruntovní" in kind:
        return "land_book"
    if "katastr" in kind:
        return "cadastre"
    return "other"


# Query templates: the agent supplies only typed values, never raw ARON filters.
def _type(value: str) -> dict:
    return {"field": "type", "operation": "EQ", "value": value}


def place_query(term: str, size: int = 10) -> dict:
    return {"filters": [_type("ENTITY"), {"operation": "FTX", "value": term}],
            "sort": [{"field": "name", "type": "SCORE", "order": "DESC", "sortMode": "MIN"}],
            "offset": 0, "size": size, "aggregations": []}


def units_query(place_uuids: list[str], year_from: int | None, year_to: int | None,
                digitized_only: bool, offset: int, unit_type: str | None = None,
                exclude_types: tuple[str, ...] = (), fund_phrases: tuple[str, ...] = ()) -> dict:
    filters = [_type("ARCH_DESC")]
    if unit_type:
        filters.append({"field": "UNIT~TYPE", "operation": "EQ", "value": unit_type})
    # One NOT per type: each excludes on its own, whatever ARON does with several children of one NOT.
    filters += [{"operation": "NOT", "filters": [{"field": "UNIT~TYPE", "operation": "EQ", "value": t}]}
                for t in exclude_types]
    if digitized_only:
        filters.append({"field": "DIGITAL", "operation": "EQ", "value": "Ano"})
    if year_from is not None or year_to is not None:
        filters.append({"field": "UNIT~DATE", "operation": "RANGE",
                        "gte": f"{year_from or 1000:04d}-01-01T00:00:00.000Z",
                        "lte": f"{year_to or 2200:04d}-12-31T23:59:59.999Z"})
    if fund_phrases:
        # Town land books carry no REG~GEO~REF; only the fund name ("Archiv města Pardubice") ties them to a place.
        filters.append({"operation": "OR", "filters": [{"operation": "FTX", "value": f'"{p}"'} for p in fund_phrases]})
    else:
        filters.append({"operation": "OR", "filters": [
            {"field": "REG~GEO~REF", "operation": "CONTAINS", "value": u} for u in place_uuids]})
    return {"filters": filters, "sort": [{"field": "name", "type": "FIELD", "order": "ASC", "sortMode": "MIN"}],
            "offset": offset, "size": LIST_PAGE_SIZE, "aggregations": []}


def _uuid(obj, what: str) -> str:
    value = obj.get("id") if isinstance(obj, dict) else None
    if not isinstance(value, str) or not re.fullmatch(UUID, value):
        raise _parse_error(f"Neplatný identifikátor {what} v ARONu.")
    return value


def _items(data) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or type(data.get("count")) is not int:
        raise _parse_error("ARON nevrátil očekávaný seznam výsledků.")
    return data["items"]


def _cells(row) -> dict[str, list[dict]]:
    lines = row.get("l")
    if not isinstance(lines, list):
        raise _parse_error("Řádek výsledku ARON nemá očekávanou strukturu.")
    cells: dict[str, list[dict]] = {}
    for line in lines:
        if not isinstance(line, list):
            raise _parse_error("Řádek výsledku ARON nemá očekávanou strukturu.")
        for cell in line:
            if not isinstance(cell, dict) or not isinstance(cell.get("t"), str) or not isinstance(cell.get("v"), list):
                raise _parse_error("Buňka výsledku ARON nemá očekávanou strukturu.")
            cells.setdefault(cell["t"], []).extend(
                v for v in cell["v"] if isinstance(v, dict) and isinstance(v.get("v"), str))
    return cells


def _first(cells: dict, code: str) -> dict | None:
    return next((v for v in cells.get(code, []) if v["v"].strip()), None)


def parse_places(data, site: AronSite = MZA_SITE) -> list[Place]:
    places = {}
    for row in _items(data):
        uuid = _uuid(row, "geografické entity")
        name = _first(_cells(row), "N")
        if not name:
            raise _parse_error("Chybí název geografické entity ARON.")
        label = clip(name["v"], 2000)
        places[uuid] = Place(source_id=site.place_id(uuid), label=label, names=[label.split(" (")[0].strip()],
                             source_url=site.detail_url(uuid))
    return list(places.values())


VCA_PLACE_CANDIDATES, VCA_PLACE_RESULTS = 30, 15


class UnitList(list):
    """Units of one listing; `by_fund_name` holds the IDs matched only through the town archive's fund name."""
    by_fund_name: frozenset = frozenset()


class PlaceList(list):
    """Places of one search; `omitted` counts candidates cut from the result."""
    omitted = 0
    omitted_labels: tuple = ()


def rank_places(places: list[Place], term: str) -> list[Place]:
    """Exact bare-name matches first, then exact ones with a ': qualifier' (castle, cadastre, corporation), then names
    that start with the term and continue after '-' or a space (town parts such as 'Pardubice-Staré Město')."""
    want = normalize_text(term.strip())

    def rank(p: Place) -> int:
        name = normalize_text(p.names[0])
        if name != want:
            return 2 if want and name.startswith(want) and name[len(want)] in "- " else 3
        return 1 if ":" in p.label.partition("(")[2].partition(")")[0] else 0
    return sorted(places, key=rank)          # stable: source order otherwise


def parse_listview_places(data, site: AronSite) -> list[Place]:
    """Places from the plain-JSON listview of the East Bohemian ARON."""
    places = {}
    for row in _items(data):
        uuid = _uuid(row, "geografické entity")
        label = clip(row.get("name"), 2000)
        if not label:
            raise _parse_error("Chybí název geografické entity ARON.")
        desc = clip(row.get("description"), 200)
        places[uuid] = Place(source_id=site.place_id(uuid), label=f"{label} – {desc}" if desc else label,
                             names=[label.split(" (")[0].strip()],
                             source_url=site.detail_url(uuid))
    return list(places.values())


def parse_listview_units(data) -> tuple[list[dict], int]:
    """Unit rows from the listview: signature and dating are only in the name, the description becomes notes."""
    rows = []
    for row in _items(data):
        uuid = _uuid(row, "archiválie")
        name = clip(row.get("name"))
        if not name:
            raise _parse_error("Chybí název archiválie ARON.")
        storage = _STORAGE.search(name)
        fund = _FUND_OF_NAME.match(name)
        rows.append({"uuid": uuid, "title": name,
                     "dating": parse_periods(name.rsplit(",", 1)[1].strip()) if "," in name else Periods(),
                     "storage": storage[1] if storage else None, "fund_uuid": None,
                     "fund_name": clip(fund[1]) if fund else None, "places": [],
                     "notes": clip(row.get("description"), 2000) or ""})
    return rows, data["count"]


def parse_unit_rows(data) -> tuple[list[dict], int]:
    rows = []
    for row in _items(data):
        uuid = _uuid(row, "archiválie")
        cells = _cells(row)
        name = _first(cells, "N")
        if not name:
            raise _parse_error("Chybí název archiválie ARON.")
        fund, dating, storage = _first(cells, "J_F"), _first(cells, "D"), _first(cells, "J_UC")
        fund_uuid = fund.get("id") if fund and isinstance(fund.get("id"), str) and re.fullmatch(UUID, fund["id"]) else None
        places = [clip(v["v"]) for v in cells.get("J_G", []) + cells.get("G", []) if clip(v["v"])]
        rows.append({"uuid": uuid, "title": clip(name["v"]),
                     "dating": parse_periods(clip(dating["v"]) if dating else None),
                     "storage": clip(storage["v"]) if storage else None, "fund_uuid": fund_uuid,
                     "fund_name": clip(fund["v"]) if fund else None, "places": list(dict.fromkeys(places))[:30],
                     "parent": clip(parent["v"], 300) if (parent := _first(cells, "J_C")) else None})
    return rows, data["count"]


def _detail_items(apu) -> dict[str, list[str]]:
    if not isinstance(apu, dict) or not isinstance(apu.get("parts"), list):
        raise _parse_error("Detail ARON neobsahuje části popisu.")
    found: dict[str, list[str]] = {}

    def walk(parts, depth=0):
        if depth > 10 or not isinstance(parts, list):
            raise _parse_error("Detail ARON má neočekávanou strukturu částí.")
        for part in parts:
            if not isinstance(part, dict):
                raise _parse_error("Detail ARON má neočekávanou strukturu částí.")
            items = part.get("items")
            if items and not isinstance(items, list):
                raise _parse_error("Detail ARON má neočekávanou strukturu částí.")
            for item in items or []:
                if isinstance(item, dict) and isinstance(item.get("type"), str) and isinstance(item.get("value"), str):
                    found.setdefault(item["type"], []).append(item["value"])
            child_parts = part.get("childParts")
            if child_parts and not isinstance(child_parts, list):
                raise _parse_error("Detail ARON má neočekávanou strukturu částí.")
            walk(child_parts or [], depth + 1)

    walk(apu["parts"])
    return found


def parse_fund(data, site: AronSite = MZA_SITE) -> Fund:
    uuid = _uuid(data, "fondu")
    items = _detail_items(data)
    first = lambda code: clip(items[code][0]) if items.get(code) else None
    return Fund(source_id=site.fund_id(uuid), mark=normalize_mark(first("FUND~MARK")),
                name=first("TITLE") or clip(data.get("name")), nad=first("CISLO~NAD"))


def parse_unit_date(values: list[str], text: str | None) -> Periods:
    raw = clip(text) or ""
    if not values:
        return parse_periods(raw) if raw else Periods()
    spans, estimated = [], False
    for value in values:
        try:
            d = json.loads(value)
            start, end = int(d["from"][:4]), int(d["to"][:4])
        except (ValueError, TypeError, KeyError) as e:
            raise _parse_error("Neplatná datace UNIT~DATE v ARONu.") from e
        if start > end:
            return Periods(status="uncertain", raw=raw)
        estimated = estimated or bool(d.get("valueFromEstimated")) or bool(d.get("valueToEstimated"))
        spans.append(YearSpan(start=start, end=end))
    return Periods(status="uncertain" if estimated else "known", spans=spans,
                   raw=raw or ", ".join(f"{s.start}–{s.end}" for s in spans))


def parse_unit_detail(data, site: AronSite = MZA_SITE) -> dict:
    """Fields of one unit. The detail carries only the fund's uuid; its mark is resolved separately."""
    uuid = _uuid(data, "archiválie")
    items = _detail_items(data)
    first = lambda code: clip(items[code][0]) if items.get(code) else None
    ancestors, node = [], data.get("parent")
    while node is not None:
        if len(ancestors) >= 30:
            raise _parse_error("Řetězec nadřazených úrovní ARON je příliš dlouhý.")
        ancestors.append({"id": site.unit_id(_uuid(node, "nadřazené úrovně")), "name": clip(node.get("name")) or ""})
        node = node.get("parent")
    notes = "; ".join(filter(None, (clip(v, 2000) for v in items.get("NOTE", []))))[:2000]
    return {"uuid": uuid, "title": clip(data.get("name")) or "(bez názvu)", "formal_title": first("FORMAL~TITLE"),
            "unit_type": first("UNIT~TYPE"),
            "is_register": any(clip(v) and clip(v).casefold() == "matrika" for v in items.get("UNIT~TYPE", [])),
            "is_census": any(clip(v) and clip(v).casefold() == VCA_CENSUS_TYPE for v in items.get("UNIT~TYPE", [])),
            "storage": first("STORAGE"), "part": first("PART"), "notes": notes,
            "dating": parse_unit_date(items.get("UNIT~DATE", []), first("UNIT~DATE~TEXT")),
            "digitized": (items["DIGITAL"][0] == "Ano") if items.get("DIGITAL") else None,
            "dao_link": first("DAO~LINK"),
            "fund_uuid": next((v for v in items.get("FUND~REF", []) if re.fullmatch(UUID, v)), None),
            "ancestors": list(reversed(ancestors)),
            "warnings": [f"Omezení přístupu: {clip(v)}" for v in items.get("UNIT~RESTRICTION~TEXT", []) if clip(v)]}


def parse_citation(data) -> str:
    value = clip(data.get("citation"), 1000) if isinstance(data, dict) else None
    if not value:
        raise _parse_error("ARON nevrátil citaci.")
    return value


def unit_pages(data, unit: ArchivalUnit, citation: str | None, site: AronSite = MZA_SITE) -> tuple[list[Page], list[str]]:
    daos = data.get("digitalObjects") if isinstance(data, dict) else None
    if not isinstance(daos, list):
        raise _parse_error("Detail ARON neobsahuje seznam digitalizátů.")
    entries = []
    for dao in sorted(daos, key=lambda d: d.get("order") if isinstance(d, dict) and type(d.get("order")) is int else 0):
        dao_uuid = _uuid(dao, "digitalizátu")
        if not isinstance(dao.get("files"), list):
            raise _parse_error("Digitalizát ARON nemá seznam souborů.")
        tiles = []
        for f in dao["files"]:
            if not isinstance(f, dict):
                raise _parse_error("Neplatný záznam souboru v ARONu.")
            if f.get("type") != "TILE":
                continue
            if (type(f.get("order")) is not int or not isinstance(f.get("referencedFile"), str)
                    or (f.get("name") is not None
                        and (not isinstance(f["name"], str) or not re.fullmatch(r"[\w.\-]{1,200}", f["name"])))):
                raise _parse_error("Neplatný záznam snímku v ARONu.")
            tiles.append((f["order"], _uuid(f, "snímku"), f["name"], f["referencedFile"]))
        entries.extend((dao_uuid, *t) for t in sorted(tiles))
    if len({e[4] for e in entries}) != len(entries):
        raise _parse_error("Seznam snímků ARON obsahuje duplicitní zdroj.")
    manifest = hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()
    uuid = site.uuid_of(unit.source_id)
    pages = []
    for n, (dao_uuid, _order, file_uuid, name, ref) in enumerate(entries, 1):
        iiif = iiif_from_deepzoom(ref)
        source_url, kind = (iiif, "iiif") if iiif else (ref, "dzi")
        pages.append(Page(page_id=f"{unit.source_id}:{n}", book_id=unit.source_id, scan_number=n,
                          image_key=(name or file_uuid).removesuffix(".dzi"), source_url=source_url, source_type=kind,
                          viewer_url=site.viewer_url(uuid, dao_uuid, file_uuid),
                          manifest_sha256=manifest, source=site.page_source, archive=site.archive, citation=citation))
    warnings = []
    numbers = [int(m[1]) for m in (_SCAN.search(p.image_key) for p in pages) if m]
    if len(numbers) == len(pages) > 1 and len({b - a for a, b in zip(numbers, numbers[1:])}) > 1:
        warnings.append("Číslování souborů snímků má mezery; digitalizát může vynechávat prázdné strany nebo být neúplný.")
    return pages, warnings


def aron_relevance(unit: ArchivalUnit, query: SearchQuery) -> dict:
    base = {"category": unit.category}
    if query.year_from is None and query.year_to is None:
        return {**base, "status": "needs_review", "reason": "Zadání neurčuje období."}
    lo, hi = query.year_from or 0, query.year_to or 9999
    overlap = any((s.start or 0) <= hi and (s.end or 9999) >= lo for s in unit.dating.spans)
    if unit.dating.status == "known":
        if overlap:
            return {**base, "status": "candidate", "reason": "Datace archiválie se překrývá se zadaným obdobím."}
        return {**base, "status": "excluded", "reason": "Datace archiválie se zadaným obdobím nepřekrývá."}
    return {**base, "status": "needs_review", "reason": "Datace chybí nebo je nejistá; archiválii nelze bezpečně vyloučit."}


def sort_key(row: dict) -> tuple:
    """Rows are ArchivalUnit dumps with a 'relevance' key; ARON itself cannot sort by date."""
    start = min((s.get("start") or 9999 for s in row["dating"]["spans"]), default=9999)
    return (_STATUS_ORDER[row["relevance"]["status"]], _CATEGORY_ORDER[row["category"]], start,
            row.get("storage") or "", row["source_id"])


class AronCatalog:
    """Network orchestration: fixed queries in, typed models out, cached and linked in Store."""

    def __init__(self, source, store, site: AronSite = MZA_SITE):
        self.source, self.store, self.site = source, store, site

    @property
    def _listview(self) -> bool:
        return self.site.list_path == "apu/listview"

    async def _json(self, sid, owner, url, body=None):
        try:
            raw = await self.source.fetch(sid, owner, url, kind="json", json_body=body)
        except ResearchError as e:
            if e.code == "SOURCE_UNAVAILABLE" and e.details.get("status") == 404:
                raise ResearchError("NOT_FOUND", "Archiválie nebo záznam v ARONu neexistuje.", status=404) from e
            raise
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as e:
            raise _parse_error("ARON nevrátil validní JSON.") from e

    async def places(self, sid, owner, term: str) -> list[Place]:
        data = await self._json(sid, owner, f"{self.site.api}/{self.site.list_path}?listType=ENTITY",
                                place_query(term, VCA_PLACE_CANDIDATES if self._listview else 10))
        places = parse_listview_places(data, self.site) if self._listview else parse_places(data, self.site)
        if self._listview:
            ranked = rank_places(places, term)
            places = PlaceList(ranked[:VCA_PLACE_RESULTS])
            places.omitted = len(ranked) - len(places)
            places.omitted_labels = tuple(p.label for p in ranked[len(places):])
        for place in places:
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id,
                            {**place.model_dump(), "source": self.site.page_source})
        return places

    async def fund(self, sid, owner, fund_uuid: str) -> Fund:
        key = self.site.fund_id(fund_uuid)
        cached = self.store.entity("fund", key, ttl=self.source.cfg.metadata_ttl)
        if cached:
            return Fund.model_validate(cached)
        fund = parse_fund(await self._json(sid, owner, f"{self.site.api}/apu/{fund_uuid}"), self.site)
        self.store.put_entity("fund", key, fund.model_dump())
        return fund

    async def _rows(self, sid, owner, place_uuids, year_from, year_to, digitized_only, unit_type, exclude_types,
                    fund_phrases=()):
        """All listview rows of one query (capped at LIST_MAX_PAGES); returns (rows, complete, warnings)."""
        url = f"{self.site.api}/{self.site.list_path}?listType=ARCH_DESC"
        parse_rows = parse_listview_units if self._listview else parse_unit_rows
        rows, warnings, complete, count, truncated = {}, [], True, 0, False
        for page in range(LIST_MAX_PAGES):
            query = units_query(place_uuids, year_from, year_to, digitized_only, page * LIST_PAGE_SIZE, unit_type,
                                exclude_types, fund_phrases)
            batch, count = parse_rows(await self._json(sid, owner, url, query))
            rows.update((r["uuid"], r) for r in batch)
            if len(batch) < LIST_PAGE_SIZE or len(rows) >= count:
                break
        else:
            truncated = True
        if len(rows) < count:
            complete = False
            if truncated:
                warnings.append(f"ARON uvádí {count} archiválií, načteno prvních {len(rows)}. Zužte výpis roky nebo fondem.")
            else:
                warnings.append(f"ARON uvádí {count} archiválií, ale vrátil jen {len(rows)} jedinečných; výpis nemusí být úplný.")
        return rows, complete, warnings

    async def units(self, sid, owner, place_uuids, year_from, year_to, digitized_only, fund_marks, unit_type=None, kind=None,
                    other_excluding: tuple[str, ...] | None = None, fund_names: list[str] | None = None):
        """With `other_excluding`, a second query adds every book except those types (VČA land records:
        `unit_type` = the land books, then all other books); land books come first and get category land_book."""
        rows, complete, warnings = await self._rows(sid, owner, place_uuids, year_from, year_to, digitized_only,
                                                    unit_type, ())
        by_fund_name = set()
        if other_excluding and fund_names:
            # Second route to land books: the town archive fund ("Archiv města/obce <name>") found by phrase search;
            # only rows whose fund is exactly that name are kept (the phrase may also hit descriptions).
            wanted = {normalize_text(f"Archiv {kind} {name}") for name in fund_names for kind in ("města", "obce")}
            phrases = tuple(f"Archiv {kind} {name.replace(chr(34), '')}" for name in fund_names for kind in ("města", "obce"))
            more, ok, warns = await self._rows(sid, owner, place_uuids, year_from, year_to, digitized_only, unit_type,
                                               (), phrases)
            complete, warnings = complete and ok, warnings + warns
            for k, v in more.items():
                if k not in rows and normalize_text(v["fund_name"] or "") in wanted:
                    rows[k] = v
                    by_fund_name.add(self.site.unit_id(k))
        land_books = set(rows) if other_excluding else set()
        if other_excluding:
            more, ok, warns = await self._rows(sid, owner, place_uuids, year_from, year_to, digitized_only, None,
                                               tuple(other_excluding))
            rows.update((k, v) for k, v in more.items() if k not in rows)
            complete, warnings = complete and ok, warnings + warns
        funds = {}
        for fund_uuid in dict.fromkeys(r["fund_uuid"] for r in rows.values() if r["fund_uuid"]):
            if len(funds) >= MAX_FUNDS_PER_CALL:
                warnings.append("Značky některých fondů nebyly zjištěny (nejvýš 10 fondů na výpis); jejich kategorie je odhadnuta.")
                break
            funds[fund_uuid] = await self.fund(sid, owner, fund_uuid)
        units = []
        for r in rows.values():
            fund = funds.get(r["fund_uuid"]) or Fund(source_id=self.site.fund_id(r["fund_uuid"]) if r["fund_uuid"] else None)
            fund = fund.model_copy(update={"name": fund.name or r["fund_name"]})
            units.append(ArchivalUnit(source_id=self.site.unit_id(r["uuid"]), title=with_katastr(r["title"], fund.mark, r.get("parent")), fund=fund,
                                      category="land_book" if r["uuid"] in land_books else category(fund.mark, None),
                                      dating=r["dating"], storage=r["storage"],
                                      notes=r.get("notes", ""), places=r["places"],
                                      digitized=True if digitized_only else None,
                                      detail_url=self.site.detail_url(r["uuid"])))
        if fund_marks:
            marks = {normalize_mark(m) for m in fund_marks}
            if any(u.fund.mark is None for u in units):
                complete = False
            units = [u for u in units if u.fund.mark in marks]
        for unit in units:
            self.store.put_entity("unit", unit.source_id, unit.model_dump())
            self.store.link(sid, owner, "book_option", unit.source_id,
                            {**unit.model_dump(), "source": self.site.page_source,
                             **({"kind": kind} if kind else {})})
        result = UnitList(units)
        result.by_fund_name = frozenset(u.source_id for u in units if u.source_id in by_fund_name)
        return result, complete, warnings

    async def open_unit(self, sid, owner, unit_uuid: str, option: ArchivalUnit | None):
        data = await self._json(sid, owner, f"{self.site.api}/apu/{unit_uuid}")
        d = parse_unit_detail(data, self.site)
        if d["uuid"] != unit_uuid:
            raise _parse_error("ARON vrátil jinou archiválii, než byla vyžádána.")
        if d["fund_uuid"]:
            fund = await self.fund(sid, owner, d["fund_uuid"])
        else:
            fund = option.fund if option else Fund()
        # The VČA detail lists several UNIT~TYPE values ('úřední kniha', 'matrika'); any 'matrika' makes it a register,
        # else any 'sčítací operát' a census.
        unit_type = d["unit_type"]
        if self.site.archive == "vca":
            unit_type = "matrika" if d["is_register"] else VCA_CENSUS_TYPE if d["is_census"] else unit_type
        unit = ArchivalUnit(source_id=self.site.unit_id(unit_uuid),
                            title=with_katastr(d["title"], fund.mark, next((a["name"] for a in reversed(d["ancestors"]) if re.search(r"\bobec:.*\bokres:", a["name"])), None)),
                            formal_title=d["formal_title"], fund=fund,
                            unit_type=unit_type, category=category(fund.mark, d["unit_type"]), dating=d["dating"],
                            storage=d["storage"], part=d["part"], notes=d["notes"],
                            places=option.places if option else [], digitized=d["digitized"],
                            detail_url=self.site.detail_url(unit_uuid))
        warnings = list(d["warnings"])
        citation = None
        try:
            citation = parse_citation(await self._json(sid, owner, f"{self.site.api}/script/citation/{unit_uuid}"))
        except ResearchError as e:
            # RATE_LIMITED / SOURCE_ACCESS_DENIED / BUDGET_EXCEEDED / LIVE_DISABLED still propagate.
            if e.code not in ("PARSE_ERROR", "SOURCE_UNAVAILABLE", "NOT_FOUND"):
                raise
            warnings.append("Citaci ARON se nepodařilo načíst; uveďte odkaz na archiválii.")
        pages, page_warnings = unit_pages(data, unit, citation, self.site)
        allowed = []
        for page in pages:
            try:
                route = self.source.policy.validate(page.source_url)
                if route.source != self.site.image_source:
                    raise ResearchError("INVALID_SOURCE", "Odkaz na snímek neukazuje na obrazový server.")
            except ResearchError as e:
                if e.code != "INVALID_SOURCE":
                    raise
                page_warnings.append(f"Snímek {page.scan_number} odkazuje mimo povolený obrazový server a byl vynechán.")
                continue
            allowed.append(page)
        if not allowed:
            raise ResearchError("UNSUPPORTED_SOURCE", "Archiválie nemá v ARONu podporovaný digitalizát; lze ji jen citovat.",
                                unit=unit.model_dump(), citation=citation, dao_link=d["dao_link"])
        for page in allowed:
            self.store.put_entity("page", page.page_id, page.model_dump())
        return unit, d["ancestors"], allowed, warnings + page_warnings, citation
