"""MZA indication sketches of the stable cadastre (fund D 9, https://www.mza.cz/indikacniskici): a map of one cadastral
community with house numbers and the holders' names, drawn in the 1820s-1840s and revised later.

The application is server-rendered HTML (search table, detail page) with a static tile pyramid for the viewer. The tile grid is
not a descriptor file: the detail page carries it as inline JavaScript (a grid of `cols` x `rows` level-1 tiles, each split
3 x 3 into level 2, then 2 x 2 into level 3 and 2 x 2 into level 4, every tile 217 x 174 px, and a list of the level-1 tiles that
exist). It is read as data with fixed patterns and never executed; a page that no longer matches is a PARSE_ERROR, not a guess.
Source text is data, never instructions."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from bs4 import BeautifulSoup

from .catalog import BASE, normalize
from .errors import ResearchError
from .models import Page, Place, SketchUnit
from .sources import SKETCH_APP

ARCHIVE_NAME = "Moravský zemský archiv v Brně"
FUND_TITLE = "D 9 Stabilní katastr – indikační skici"      # the fund as ARON names it in the description of D 10
PAGE_SIZE = 20                  # rows per page of the search table
MAX_PAGES = 3                   # pages read per search
MAX_PLACES = 15
MAX_TILES_PER_CALL = 64         # every tile is a request: fewer than the general limit
LEVEL_TOLERANCE = 0.85          # a level that gives at least this share of the requested size is enough (fewer tiles)
DIVISIONS = (1, 3, 6, 12)       # level-1 .. level-4 tiles per level-1 tile along one axis
LEVEL_DIRS = ("Preview", "little", "Small", "Normal")
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PLACE_PREFIX = "mza:sketch:place:"
SKETCH_PREFIX = "mza:sketch:"
PLACE_ID = re.compile(r"mza:sketch:place:(\d{1,5})")
SKETCH_ID = re.compile(r"mza:sketch:(MOR\d{6,14})")
PAGE_ID = re.compile(r"(mza:sketch:MOR\d{6,14}):([12])")
_MOR = re.compile(r"MOR\d{6,14}")
_HEADERS = ["Identifikátor", "Signatura", "Název katastru", "Katastr (německy)", "Okres", "Rok"]
_TOOLTIPS = {"Poznámka": "note", "Další názvy": "other_names", "Další obce": "other_municipalities"}
SIDES = {"A": "front", "R": "back"}
_COUNT = re.compile(r"Zobrazené záznamy:\s*(\d+)\s*-\s*(\d+),\s*celkem:\s*(\d+)")
_NOTHING = "Nebyly nalezeny žádné vyhovující záznamy."
# Constants of the viewer script that the tile arithmetic below depends on; if any changes, the page is refused.
_SCALES = "var levelScales = [(43 / 174) / 12, 1 / 12, 1 / 4, 1 / 2, 1];"


def _bad(message: str, **details) -> ResearchError:
    return ResearchError("PARSE_ERROR", message, **details)


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def is_sketch_id(value) -> bool:
    return isinstance(value, str) and value.startswith(SKETCH_PREFIX)


def is_sketch_place(value) -> bool:
    return isinstance(value, str) and value.startswith(PLACE_PREFIX)


def signature_of(place_id: str) -> str:
    m = PLACE_ID.fullmatch(place_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Místa ze skic MZA musí mít ID ve tvaru mza:sketch:place:<signatura> (z find_place).")
    return m[1]


def mor_of(source_id: str) -> str:
    m = SKETCH_ID.fullmatch(source_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Skicu MZA otevřete podle ID ve tvaru mza:sketch:MOR<číslo> z list_sources.")
    return m[1]


def sketch_id_from_url(url: str) -> str:
    """Sketch named by a link the user pasted (the detail page by its MOR id); the link itself is never fetched."""
    p = urlsplit(url)
    m = re.fullmatch(rf"{SKETCH_APP}/skica/detail/(MOR\d{{6,14}})/?", p.path)
    if p.scheme != "https" or p.hostname not in ("www.mza.cz", "mza.cz") or p.username or p.password or p.port not in (None, 443) or not m:
        raise ResearchError("INVALID_SOURCE", "Odkaz není detailem skici MZA (https://www.mza.cz/indikacniskici/skica/detail/MOR<číslo>).")
    return SKETCH_PREFIX + m[1]


# ---------------------------------------------------------------- addresses

def search_url(nazev: str | None = None, okres: str | None = None, signatura: str | None = None, page: int = 1) -> str:
    params = [(k, v) for k, v in (("nazev", nazev), ("okres", okres), ("signatura", signatura)) if v]
    if page > 1:
        params.append(("page", str(page)))
    return f"{BASE}{SKETCH_APP}/skica?{urlencode(params)}"


def detail_url(mor: str) -> str:
    return f"{BASE}{SKETCH_APP}/skica/detail/{mor}"


def side_url(number: int | str, side: str) -> str:
    """The sheet's other side, by the numeric id the site redirects a MOR id to."""
    return f"{BASE}{SKETCH_APP}/skica/detail/{int(number)}?site={side}"


def citation_of(unit: SketchUnit) -> str:
    return f"{ARCHIVE_NAME}, fond {FUND_TITLE}, {unit.katastr}, {mor_of(unit.source_id)}"


def manifest_hash(mor: str, sides: list[str], cols: int, rows: int, tiles) -> str:
    return hashlib.sha256(json.dumps([mor, sides, cols, rows, sorted(tiles)]).encode()).hexdigest()


# ---------------------------------------------------------------- search table

@dataclass(frozen=True)
class SketchRow:
    mor: str
    signature: str
    katastr: str
    german_name: str | None
    district: str
    year: int | None
    note: str | None = None
    other_names: str | None = None
    other_municipalities: str | None = None


def _tooltips(node) -> dict[str, str]:
    """Tooltips of a row or of the detail header: the title holds a small HTML fragment `<strong>label</strong><br>text`."""
    found: dict[str, str] = {}
    for tip in node.find_all(attrs={"data-toggle": "tooltip"}):
        fragment = BeautifulSoup(tip.get("title", ""), "lxml")
        label = fragment.find("strong")
        if label is None:
            continue
        name = _text(label)
        label.decompose()
        value = _text(fragment)
        key = _TOOLTIPS.get(name)
        if key:
            found[key] = (found[key] + "; " if key in found else "") + value
        elif value:
            found["note"] = (found["note"] + "; " if "note" in found else "") + f"{name}: {value}"
    return found


def _year(text: str) -> int | None:
    if not re.fullmatch(r"\d{1,4}", text):
        raise _bad("Rok skici v seznamu MZA nemá očekávaný tvar.")
    return int(text) or None        # 0: the archive lists the sketch without a date


def parse_search(html: str) -> tuple[list[SketchRow], int, int]:
    """Rows of one page of the search table, the number of the last row shown on it and the total count."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="table")
    if table is None or table.find("tbody") is None:
        raise _bad("Výsledky hledání skic MZA nemají očekávanou strukturu (chybí tabulka).")
    headers = [_text(th) for th in table.find_all("th")]
    if headers[:len(_HEADERS)] != _HEADERS:
        raise _bad("Tabulka skic MZA má jiné sloupce, než Rubrika zná.", headers=headers[:8])
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < len(_HEADERS):
            raise _bad("Řádek skic MZA má méně sloupců, než Rubrika zná.")
        values = [_text(c) for c in cells[:len(_HEADERS)]]
        mor, signature = values[0], values[1]
        if not _MOR.fullmatch(mor) or not re.fullmatch(r"\d{1,5}", signature) or not values[2] or not values[4]:
            raise _bad("Řádek skic MZA nemá očekávaný identifikátor, signaturu, název nebo okres.")
        tips = _tooltips(tr)
        rows.append(SketchRow(mor=mor, signature=signature, katastr=values[2][:200], german_name=values[3][:200] or None,
                              district=values[4][:100], year=_year(values[5]), note=tips.get("note"),
                              other_names=tips.get("other_names"), other_municipalities=tips.get("other_municipalities")))
    footer = " ".join(soup.get_text(" ").split())
    counted = _COUNT.search(footer)
    if counted:
        last, total = int(counted[2]), int(counted[3])
    elif _NOTHING in footer:
        last = total = 0
    else:
        raise _bad("Výsledky hledání skic MZA neuvádějí počet záznamů.")
    if bool(rows) != bool(total):
        raise _bad("Počet záznamů skic MZA nesouhlasí s tabulkou.")
    return rows, last, total


# ---------------------------------------------------------------- detail page and tile grid

@dataclass(frozen=True)
class SketchGrid:
    sketch_id: str          # MOR id, as the tile addresses spell it
    side: str               # "A" front, "R" back
    cols: int               # level-1 tiles across
    rows: int
    tile_w: int
    tile_h: int
    tiles: frozenset        # the level-1 tiles (both sides) that exist

    @property
    def width(self) -> int:
        return self.cols * 12 * self.tile_w

    @property
    def height(self) -> int:
        return self.rows * 12 * self.tile_h

    @staticmethod
    def scale(level: int) -> float:
        return DIVISIONS[level - 1] / 12

    def level_tiles(self, level: int) -> tuple[int, int]:
        n = DIVISIONS[level - 1]
        return self.cols * n, self.rows * n

    def _first(self, x: int, y: int) -> str:
        return f"{self.side}{y + 1:02d}{LETTERS[x]}"

    def tile_name(self, level: int, x: int, y: int) -> str | None:
        """File name of a tile without extension, None where the sketch has no tile (blank area). Mirrors the viewer's script."""
        n = DIVISIONS[level - 1]
        first = self._first(x // n, y // n)
        if first not in self.tiles:
            return None
        if level == 1:
            return first
        second = f"{LETTERS[(x // (n // 3)) % 3]}{(y // (n // 3)) % 3 + 1}"
        if level == 2:
            return f"{first}-{second}"
        third = f"{LETTERS[(x // (n // 6)) % 2]}{(y // (n // 6)) % 2 + 1}"
        if level == 3:
            return f"{first}-{second}-{third}"
        return f"{first}-{second}-{third}-{LETTERS[x % 2]}{y % 2 + 1}"

    @property
    def map_size(self) -> tuple[int, int]:
        """Size of the single-file thumbnail `map.jpg` (the viewer's level 0): 54 x 43 px per level-1 tile."""
        return self.cols * 54, self.rows * 43

    @property
    def map_url(self) -> str:
        return f"{BASE}{SKETCH_APP}/data/obrazky/{self.sketch_id}/Img/map.jpg"

    def tile_url(self, level: int, x: int, y: int) -> str | None:
        name = self.tile_name(level, x, y)
        if name is None:
            return None
        return f"{BASE}{SKETCH_APP}/data/obrazky/{self.sketch_id}/Img/{LEVEL_DIRS[level - 1]}/{name}.jpg"


@dataclass(frozen=True)
class SketchDetail:
    mor: str
    signature: str
    katastr: str
    german_name: str | None
    district: str
    year: int | None
    note: str | None
    other_names: str | None
    other_municipalities: str | None
    grid: SketchGrid
    internal_id: int | None         # the number the site redirects the MOR id to, from its front/back link
    has_back: bool
    back_unresolved: bool = False   # the tile list indicates a back side but no link to it was recognised


def parse_grid(html: str) -> SketchGrid:
    """Tile grid and existing tiles from the viewer script of a detail page."""
    if _SCALES not in html or "maxLevel: 4," not in html:
        raise _bad("Prohlížeč skic MZA má jinou strukturu dlaždic, než Rubrika zná.")
    side = re.search(r'var siteLetter = "([AR])";', html)
    listed = re.search(r"var tiles = \[([^\]]{0,60000})\];", html)
    rows = re.search(r"var rows = (\d{1,2});", html)
    cols = re.search(r"var cols = (\d{1,2});", html)
    width = re.search(r"width: cols \* 12 \* (\d{2,4}),", html)
    height = re.search(r"height: rows \* 12 \* (\d{2,4}),", html)
    tile_w = re.search(r"getTileWidth: function\(level\) \{\s*return level > 0 \? (\d{2,4}) :", html)
    tile_h = re.search(r"getTileHeight: function\(level\) \{\s*return level > 0 \? (\d{2,4}) :", html)
    base = re.search(r'var url = "https://www\.mza\.cz/indikacniskici/data/obrazky/(MOR\d{6,14})/Img/";', html)
    if not all((side, listed, rows, cols, width, height, tile_w, tile_h, base)) or width[1] != tile_w[1] or height[1] != tile_h[1]:
        raise _bad("Detail skici MZA nemá očekávané údaje o dlaždicích.")
    tiles = frozenset(re.findall(r'"([AR]\d{2}[A-Z])"', listed[1]))
    if not tiles or len(tiles) != listed[1].count('"') // 2:
        raise _bad("Seznam dlaždic skici MZA nemá očekávaný tvar.")
    grid = SketchGrid(sketch_id=base[1], side=side[1], cols=int(cols[1]), rows=int(rows[1]),
                      tile_w=int(tile_w[1]), tile_h=int(tile_h[1]), tiles=tiles)
    if not (1 <= grid.cols <= 26 and 1 <= grid.rows <= 99 and 32 <= grid.tile_w <= 1024 and 32 <= grid.tile_h <= 1024):
        raise _bad("Rozměry mřížky dlaždic skici MZA jsou mimo očekávaný rozsah.")
    if any(LETTERS.index(t[3]) >= grid.cols or not 1 <= int(t[1:3]) <= grid.rows for t in tiles):
        raise _bad("Seznam dlaždic skici MZA přesahuje mřížku.")
    return grid


def parse_detail(html: str, expected_mor: str) -> SketchDetail:
    soup = BeautifulSoup(html, "lxml")
    header = soup.select_one("div.card-header")
    if header is None:
        raise _bad("Detail skici MZA nemá očekávanou hlavičku.")
    labelled: dict[str, str] = {}
    for item in header.select("li.nav-item"):
        label, value = item.select_one("span.font-italic"), item.select_one("span.font-weight-bolder")
        if label is not None and value is not None:
            labelled[_text(label)] = _text(value)
    if not {"Identifikátor", "Signatura", "Název katastru", "Okres", "Rok"} <= set(labelled):
        raise _bad("Detail skici MZA nemá očekávané údaje (identifikátor, signatura, název, okres, rok).")
    if labelled["Identifikátor"] != expected_mor:
        raise _bad("MZA vrátilo jinou skicu, než byla vyžádána.")
    if not re.fullmatch(r"\d{1,5}", labelled["Signatura"]):
        raise _bad("Signatura skici MZA nemá očekávaný tvar.")
    tips = _tooltips(header)
    internal, back = None, None
    for link in soup.find_all("a", href=True):
        m = re.fullmatch(rf"(?:{re.escape(BASE)})?{SKETCH_APP}/skica/detail/(\d{{1,8}})\?site=(?:FRONT|BACK)", link["href"].strip())
        if m:
            internal = internal if internal is not None else int(m[1])
            if link["href"].strip().endswith("site=BACK"):
                back = int(m[1])
                break
    grid = parse_grid(html)
    if grid.sketch_id != expected_mor:
        raise _bad("Dlaždice skici MZA patří k jiné skice, než byla vyžádána.")
    return SketchDetail(mor=expected_mor, signature=labelled["Signatura"], katastr=labelled["Název katastru"][:200],
                        german_name=labelled.get("Katastr (německy)", "")[:200] or None, district=labelled["Okres"][:100],
                        year=_year(labelled["Rok"]), note=tips.get("note"), other_names=tips.get("other_names"),
                        other_municipalities=tips.get("other_municipalities"), grid=grid, internal_id=back if back is not None else internal,
                        has_back=back is not None and any(t.startswith("R") for t in grid.tiles),
                        back_unresolved=back is None and any(t.startswith("R") for t in grid.tiles))


async def fetch_grid(source, sid, owner, page: Page) -> SketchGrid:
    """Tile grid of one sheet side (the detail page is cached with the other catalogue pages)."""
    m = PAGE_ID.fullmatch(page.page_id)
    if not m:
        raise ResearchError("INVALID_SOURCE", "Snímek není stranou skici MZA.")
    body = await source.fetch(sid, owner, page.source_url, kind="html")
    try:
        grid = parse_grid(body.decode("utf-8-sig", errors="replace"))
        if grid.sketch_id != mor_of(m[1]) or grid.side != ("A" if m[2] == "1" else "R"):
            raise _bad("Stránka skici MZA neodpovídá otevřené skice nebo její straně.")
        return grid
    except ResearchError:
        source.invalidate(page.source_url)
        raise


# ---------------------------------------------------------------- ranking and catalogue client

def sort_key(row: dict):
    return (row["year"] is None, row["year"] or 0, row["source_id"])


def relevance() -> dict:
    return {"status": "candidate",
            "reason": "Indikační skica zachycuje domy a parcely katastru; nemá druh události, proto se nevylučuje podle let. Rok je rok vyhotovení nebo revize skice."}


def rank_places(places: list[Place], term: str) -> list[Place]:
    """Exact Czech or German name first, then names that start with the term (the archive's search is by the beginning)."""
    want = normalize(term.strip())

    def rank(p: Place) -> int:
        names = [normalize(n) for n in p.names]
        return 0 if want in names else 1 if any(n.startswith(want) for n in names) else 2
    return sorted(places, key=rank)


class SketchCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    async def _search(self, sid, owner, *, nazev=None, okres=None, signatura=None) -> tuple[list[SketchRow], int, list[str]]:
        rows: list[SketchRow] = []
        total, warnings = 0, []
        for page in range(1, MAX_PAGES + 1):
            url = search_url(nazev, okres, signatura, page)
            body = await self.source.fetch(sid, owner, url, kind="html")
            try:
                batch, last, total = parse_search(body.decode("utf-8-sig", errors="replace"))
            except ResearchError:
                self.source.invalidate(url)
                raise
            rows += batch
            if not batch or last >= total:
                break
        if len(rows) < total:
            warnings.append(f"MZA uvádí {total} skic, načteno prvních {len(rows)}; upřesněte název nebo okres.")
        return rows, total, warnings

    async def places(self, sid, owner, term: str, okres: str | None = None) -> tuple[list[Place], list[str]]:
        rows, total, warnings = await self._search(sid, owner, nazev=term.strip(), okres=okres)
        by_signature: dict[str, list[SketchRow]] = {}
        for row in rows:
            by_signature.setdefault(row.signature, []).append(row)
        found = []
        for signature, group in by_signature.items():
            first = group[0]
            german = [n.strip() for n in (first.german_name or "").split(",") if n.strip()]
            label = f"{first.katastr} ({', '.join(german)})" if german else first.katastr
            found.append(Place(source_id=PLACE_PREFIX + signature,
                               label=f"{label}, okres {first.district} (signatura {signature}, {len(group)} skic)",
                               names=list(dict.fromkeys([first.katastr, *german])), source_url=search_url(signatura=signature)))
        places = rank_places(found, term)
        omitted = max(0, len(places) - MAX_PLACES)
        places = places[:MAX_PLACES]
        if omitted:
            warnings.append(f"Nalezeno více katastrů; zobrazeno {MAX_PLACES}, dalších {omitted} bylo vynecháno. Upřesněte název nebo okres.")
        for place in places:
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, {**place.model_dump(), "source": "sketch"})
        return places, warnings

    async def sources(self, sid, owner, place_ids: list[str]) -> tuple[list[SketchUnit], bool, list[str]]:
        units: dict[str, SketchUnit] = {}
        warnings, complete = [], True
        for place_id in dict.fromkeys(place_ids):
            signature = signature_of(place_id)
            rows, total, warns = await self._search(sid, owner, signatura=signature)
            warnings += warns
            complete = complete and len(rows) >= total
            rows = [r for r in rows if r.signature == signature]
            per_year: dict[int | None, int] = {}
            for row in rows:
                per_year[row.year] = per_year.get(row.year, 0) + 1
            for row in rows:
                unit = SketchUnit(source_id=SKETCH_PREFIX + row.mor, signature=row.signature, katastr=row.katastr, german_name=row.german_name,
                                  district=row.district, year=row.year, sheet=int(row.mor[-1]), sheets_in_year=per_year[row.year],
                                  note=row.note, other_names=row.other_names, other_municipalities=row.other_municipalities,
                                  detail_url=detail_url(row.mor))
                units[unit.source_id] = unit.model_copy(update={"citation": citation_of(unit)})
        for unit in units.values():
            self.store.put_entity("sketch", unit.source_id, unit.model_dump())
            self.store.link(sid, owner, "book_option", unit.source_id, {**unit.model_dump(), "source": "sketch"})
        return list(units.values()), complete, warnings

    async def open_sketch(self, sid, owner, unit: SketchUnit) -> tuple[SketchUnit, list[Page], list[str]]:
        """The front side, and the back if the sheet has one, as pages. One request (the detail page, cached); the back side's
        own page is read only when it is viewed."""
        mor = mor_of(unit.source_id)
        url = detail_url(mor)
        body = await self.source.fetch(sid, owner, url, kind="html")
        try:
            detail = parse_detail(body.decode("utf-8-sig", errors="replace"), mor)
        except ResearchError:
            self.source.invalidate(url)
            raise
        sides = ["front"] + (["back"] if detail.has_back else [])
        grid = detail.grid
        manifest = manifest_hash(mor, sides, grid.cols, grid.rows, grid.tiles)
        opened = unit.model_copy(update={"signature": detail.signature, "katastr": detail.katastr, "german_name": detail.german_name, "district": detail.district,
                                         "year": detail.year, "note": detail.note, "other_names": detail.other_names,
                                         "other_municipalities": detail.other_municipalities, "sides": sides,
                                         "grid_tiles": {"cols": grid.cols, "rows": grid.rows}})
        citation = citation_of(opened)
        opened = opened.model_copy(update={"citation": citation})
        pages = []
        for n, side in enumerate(sides, 1):
            link = url if n == 1 else side_url(detail.internal_id, "BACK")
            pages.append(Page(page_id=f"{unit.source_id}:{n}", book_id=unit.source_id, scan_number=n, image_key=f"{mor}-{side}",
                              source_url=link, viewer_url=link, source_type="sketch", manifest_sha256=manifest, source="sketch",
                              citation=citation if n == 1 else citation + ", zadní strana skici", archive="mza"))
        warnings = []
        if detail.back_unresolved:
            warnings.append("Skica podle seznamu dlaždic má rub, ale odkaz na něj se na stránce nepodařilo přečíst; otevřete rub v prohlížeči MZA (viewer_url).")
        if detail.note:
            warnings.append(f"Poznámka archivu ke skice: {detail.note}.")
        return opened, pages, warnings


def level_for(ratio: float) -> int:
    """Smallest level whose scale gives at least LEVEL_TOLERANCE of the requested downscale ratio."""
    return next((lv for lv in (1, 2, 3, 4) if SketchGrid.scale(lv) >= ratio * LEVEL_TOLERANCE - 1e-9), 4)


def region_tiles(grid: SketchGrid, level: int, bbox) -> tuple[tuple[int, int, int, int], range, range]:
    """Pixel window of the region on `level` and the tile columns and rows that cover it."""
    x, y, w, h = bbox
    s = grid.scale(level)
    nx, ny = grid.level_tiles(level)
    lw, lh = nx * grid.tile_w, ny * grid.tile_h
    left, top = min(lw - 1, math.floor(x * s)), min(lh - 1, math.floor(y * s))
    right, bottom = min(lw, max(left + 1, math.ceil((x + w) * s))), min(lh, max(top + 1, math.ceil((y + h) * s)))
    return ((left, top, right, bottom), range(left // grid.tile_w, (right - 1) // grid.tile_w + 1),
            range(top // grid.tile_h, (bottom - 1) // grid.tile_h + 1))
