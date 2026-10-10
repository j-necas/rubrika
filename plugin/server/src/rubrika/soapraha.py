"""State Regional Archives in Prague (eBadatelna, Apache Wicket): the register search, book details and whole-scan JPEGs.

The application is stateful (JSESSIONID, page versions in every link) and has no API. Results appear only in the XML
answer of an AJAX request; a scan's JPEG can be downloaded only in the session that rendered its viewer page. Rubrika
therefore opens one session per tool call, reads component addresses from the pages it is given (the page-version
counter is never hard-coded) and treats every unexpected structure as PARSE_ERROR, never as an empty result.
Source text is data; JavaScript is never evaluated."""
from __future__ import annotations

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

from .catalog import normalize, parse_periods
from .errors import ResearchError
from .census import parse_description_house_numbers
from .models import EventCoverage, HouseNumbers, Page, Periods, Place, SoaBook, SoaUnit
from .network import SiteSession
from .sources import SOAPRAHA_ARCHIVE_FIELDS, SOAPRAHA_HOST, SOAPRAHA_SEARCH_FIELDS

BASE = "https://" + SOAPRAHA_HOST
SEARCH_URL = BASE + "/pages/SearchMatrikaPage"
ARCHIVE_SEARCH_URL = BASE + "/pages/SearchArchivaliePage"
ARCHIVE_NAME = "Státní oblastní archiv v Praze"
FUND_TITLE = "Sbírka matrik a průvodní listinný materiál, Středočeský kraj (NAD 28)"
MAX_RESULT_PAGES = 8                 # result pages one search may read (50 rows each on the live application)
MAX_PLACES = 15
MAX_EXACT_PLACES = 60         # places with exactly the searched name are never cut at MAX_PLACES
MAX_PLACES_PER_LISTING = 3
EVENTS = ("birth", "marriage", "death")
BOOK_ID = re.compile(r"soapraha:book:(\d{1,9})")
UNIT_ID = re.compile(r"soapraha:unit:(\d{1,9})")
PAGE_ID = re.compile(r"(soapraha:(?:book|unit):\d{1,9}):([1-9]\d{0,4})")
CENSUS_TYPE = "Sčítací operáty"                            # the archive's archival type of the census records
LAND_TYPES = ("Města a obce", "Velkostatky", "Rodinné archivy")   # archival types searched for land records; church, maps, photographs and the rest are not
PLACE_ID = re.compile(r"soapraha:place:([a-z0-9][a-z0-9-]{0,79})")
_LISTENER = r"(\d{1,4}-\d{1,3}\.\d{1,3}-%s)"
_AJAX = re.compile(r'Wicket\.Ajax\.ajax\(\{"u":"\./SearchMatrikaPage(?:;jsessionid=[A-Za-z0-9._-]+)?\?([^"]{1,200})"')
_BASE_URL = re.compile(r'Wicket\.Ajax\.baseUrl="(pages/SearchMatrikaPage\?\d{1,4})"')
_ARCH_AJAX = re.compile(r'Wicket\.Ajax\.ajax\(\{"u":"\./SearchArchivaliePage(?:;jsessionid=[A-Za-z0-9._-]+)?\?([^"]{1,200})"')
_ARCH_BASE_URL = re.compile(r'Wicket\.Ajax\.baseUrl="(pages/SearchArchivaliePage\?\d{1,4})"')
# a scan of an archival record is named after the record's title: "Scitani_lidu_1869:_Okres_X_-_obec_Y,_cp._1-50__001.jpg"
_JPEG_LINK = re.compile(r"(?:\.\./)+wicket/resource/org\.apache\.wicket\.Application/([A-Za-z0-9][A-Za-z0-9_.,:()-]{0,300}__(\d{3,4})\.jpg)")
_EXPIRED = re.compile(r"page\s+expired|platnost\s+str[aá]nky\s+vypr", re.I)
_DASHES = re.compile(r"[-–—]+")


def _bad(message: str, **details) -> ResearchError:
    return ResearchError("PARSE_ERROR", message, **details)


def _expired() -> ResearchError:
    return ResearchError("SOURCE_UNAVAILABLE", "Relace eBadatelny vypršela.", reason="session_expired")


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def book_id_of(book_id: str) -> int:
    m = BOOK_ID.fullmatch(book_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Matriku SOA Praha otevřete podle ID ve tvaru soapraha:book:<číslo> z list_sources.")
    return int(m[1])


def unit_id_of(unit_id: str) -> int:
    m = UNIT_ID.fullmatch(unit_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Archiválii SOA Praha otevřete podle ID ve tvaru soapraha:unit:<číslo> z list_sources.")
    return int(m[1])


def is_unit_id(value) -> bool:
    return isinstance(value, str) and UNIT_ID.fullmatch(value) is not None


def ident_of(source_id: str) -> tuple[str, int]:
    """('book', id) for a register, ('unit', id) for an archival record (census, land book)."""
    return ("unit", unit_id_of(source_id)) if is_unit_id(source_id) else ("book", book_id_of(source_id))


def kind_of_type(unit_type: str | None) -> str | None:
    """census for the archive's census type, land_records for the land-related types, None for anything else or unreadable."""
    key = normalize(unit_type or "")
    if key == normalize(CENSUS_TYPE):
        return "census"
    return "land_records" if key in {normalize(t) for t in LAND_TYPES} else None


def is_soapraha_id(value) -> bool:
    return isinstance(value, str) and value.startswith("soapraha:")


def detail_url(matrika_id: int) -> str:
    return f"{BASE}/pages/MatrikaPage/matrikaId/{int(matrika_id)}"


def viewer_url(matrika_id: int, scan: int) -> str:
    return f"{BASE}/d/{int(matrika_id)}/{int(scan)}"


def unit_detail_url(archivalie_id: int) -> str:
    return f"{BASE}/pages/ArchivaliePage/archivalieId/{int(archivalie_id)}"


def unit_viewer_url(archivalie_id: int, scan: int) -> str:
    return f"{BASE}/a/{int(archivalie_id)}/{int(scan)}"


def viewer_of(source_id: str, scan: int) -> str:
    kind, ident = ident_of(source_id)
    return unit_viewer_url(ident, scan) if kind == "unit" else viewer_url(ident, scan)


def book_id_from_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != SOAPRAHA_HOST or p.username or p.password or p.port not in (None, 443):
        raise ResearchError("INVALID_SOURCE", "Je vyžadován HTTPS odkaz na matriku v eBadatelně SOA Praha.")
    path = re.sub(r";jsessionid=[A-Za-z0-9._-]+", "", p.path)
    unit = (re.fullmatch(r"/pages/(?:Data)?ArchivaliePage/archivalieId/(\d{1,9})(?:/idx/\d{1,5})?", path)
            or re.fullmatch(r"/a/(\d{1,9})/\d{1,5}", path))
    if unit and (not p.query or p.query.isdigit()):
        return f"soapraha:unit:{unit[1]}"
    m = (re.fullmatch(r"/pages/(?:MatrikaPage|DataMatrikaPage)/matrikaId/(\d{1,9})", path)
         or re.fullmatch(r"/pages/DataMatrikaPage/idx/\d{1,5}/matrikaId/(\d{1,9})", path)
         or re.fullmatch(r"/d/(\d{1,9})/\d{1,5}", path))
    if not m or (p.query and not p.query.isdigit()):
        raise ResearchError("INVALID_SOURCE", "Odkaz není matrikou ani archiválií eBadatelny (/pages/MatrikaPage/matrikaId/<číslo>, /pages/ArchivaliePage/archivalieId/<číslo>).")
    return f"soapraha:book:{m[1]}"


# ---------------------------------------------------------------- years and coverage

def parse_year_cell(text: str) -> Periods:
    """'1663 - 1676' → known; '---' → absent; anything else the application writes there → uncertain (never excludes)."""
    raw = " ".join((text or "").replace("\xa0", " ").split())
    if not raw:
        return Periods(status="unknown", raw=raw)
    if _DASHES.fullmatch(raw):
        return Periods(status="absent", raw=raw)
    return parse_periods(raw)


def coverage_of(cells: list[str]) -> dict[str, EventCoverage]:
    """Births, marriages, deaths and their indexes from the six year columns. A row without a single year says nothing
    about its content and stays uncertain; otherwise an event with no years is absent, as the archive catalogued it."""
    periods = [parse_year_cell(c) for c in cells]
    if len(periods) != 6:
        raise _bad("Řádek matriky nemá šest sloupců s roky.")
    if all(p.status in ("absent", "unknown") for p in periods):
        periods = [Periods(status="uncertain", raw=p.raw) for p in periods]
    return {e: EventCoverage(records=periods[i], index=periods[i + 3]) for i, e in enumerate(EVENTS)}


# ---------------------------------------------------------------- search page and results

@dataclass
class SearchForm:
    listener: str        # query of the search-button listener, e.g. "0-1.0-searchForm-searchButton"
    base_url: str        # Wicket-Ajax-BaseURL, e.g. "pages/SearchMatrikaPage?0"


def parse_search_form(html: str) -> SearchForm:
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", class_="searchForm")
    if form is None:
        raise _bad("Stránka nemá vyhledávací formulář eBadatelny; nejde o prázdný výsledek.")
    names = {i.get("name"): i for i in form.find_all(["input", "select"]) if i.get("name")}
    radios = {i.get("value") for i in form.find_all("input", attrs={"name": "keywordBox:searchTokenIn"})}
    if not {"searchType", "keywordBox:searchToken", "dateYearFrom", "dateYearTo"} <= set(names) or "radio0" not in radios:
        raise _bad("Formulář hledání eBadatelny má jiná pole, než Rubrika zná.")
    listeners = {m[1] for m in _AJAX.finditer(html) if re.fullmatch(_LISTENER % "searchForm-searchButton", m[1])}
    bases = set(_BASE_URL.findall(html))
    if len(listeners) != 1 or len(bases) != 1:
        raise _bad("Stránka neuvádí (jednoznačně) tlačítko hledání nebo základ adres; složení komponent se změnilo.")
    return SearchForm(listener=listeners.pop(), base_url=bases.pop())


@dataclass
class Row:
    matrika_id: int
    title: str
    originator: str
    district: str
    localities: list[str]
    years: list[str]
    scans: int


@dataclass
class ResultPage:
    rows: list[Row] = field(default_factory=list)
    total: int = 0
    page: int = 0
    pages: int = 1
    next_listener: str | None = None
    base_url: str | None = None
    message: str | None = None
    warnings: list[str] = field(default_factory=list)


def _components(xml: bytes) -> tuple[list[str], list[str]]:
    upper = xml.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise _bad("XML s externími entitami není povoleno.")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise _bad("Odpověď eBadatelny není platné XML.") from e
    if root.tag != "ajax-response":
        raise _bad("Odpověď eBadatelny není odpověď Wicket AJAX.")
    if root.find("redirect") is not None:
        raise _expired()
    return [c.text or "" for c in root.findall("component")], [e.text or "" for e in root.findall("evaluate") + root.findall("header-contribution")]


def parse_results(xml: bytes) -> ResultPage:
    components, scripts = _components(xml)
    if not components:
        raise _bad("Odpověď eBadatelny neobsahuje žádnou komponentu.")
    soup = BeautifulSoup("<body>" + "".join(components) + "</body>", "lxml")
    table = soup.find("table", class_="searchResult")
    if table is None:
        # "No results" is the answer's only visible text next to a hidden placeholder for the result table
        visible = [_text(BeautifulSoup(c, "lxml")) for c in components if "data-wicket-placeholder" not in c]
        if (len(components) == 2 and any("data-wicket-placeholder" in c for c in components) and len([v for v in visible if v]) == 1
                and len(visible[0]) <= 200 and "<table" not in "".join(components)):
            return ResultPage(message=next(v for v in visible if v))
        raise _bad("Odpověď eBadatelny nemá tabulku výsledků ani zprávu o prázdném výsledku.")
    heads = [th.get("class", [""])[0] for th in table.select("thead th")]
    if heads != [f"header{i}" for i in range(1, 13)]:
        raise _bad("Tabulka výsledků eBadatelny má neočekávané sloupce.")
    found = soup.select_one(".resultsFound")
    total = re.search(r"\d+", found.get_text(" ", strip=True).replace("\xa0", "")) if found else None
    dropdown = soup.find("select", attrs={"name": re.compile(r"pagination:results:pageDropdown$")})
    if not total or dropdown is None:
        raise _bad("Odpověď eBadatelny neuvádí počet nalezených matrik nebo stránek.")
    options = dropdown.find_all("option")
    selected = [o for o in options if o.has_attr("selected")]
    if not options or len(selected) != 1 or not selected[0].get("value", "").isdigit():
        raise _bad("Stránkování výsledků eBadatelny má neočekávaný tvar.")
    result = ResultPage(total=int(total[0]), page=int(selected[0]["value"]), pages=len(options))
    seen = set()
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        link = tr.find("a", class_="matrikaLink", href=True)
        m = re.fullmatch(r"/pages/MatrikaPage/matrikaId/(\d{1,9})", link["href"]) if link else None
        if len(cells) != 12 or not m or tr.get("href") != f"/pages/DataMatrikaPage/idx/1/matrikaId/{m[1]}":
            raise _bad("Řádek výsledků eBadatelny má neočekávaný tvar (sloupce nebo odkazy na matriku).")
        values = [_text(c) for c in cells]
        if not values[11].isdigit() or not values[0]:
            raise _bad("Řádek výsledků eBadatelny nemá název nebo počet snímků.")
        if m[1] in seen:
            continue
        seen.add(m[1])
        localities = [x.strip() for x in values[4].split(",") if x.strip()]
        if values[3].isdigit() and int(values[3]) != len(localities):
            result.warnings.append(f"Počet lokalit v řádku {values[0]} nesouhlasí se seznamem; názvy s čárkou mohou být rozděleny.")
        result.rows.append(Row(int(m[1]), values[0], values[1], values[2], localities, values[5:11], int(values[11])))
    if not result.rows:
        raise _bad("Tabulka výsledků eBadatelny nemá řádky.")
    text = "\n".join(scripts)
    listeners = {m[1] for m in _AJAX.finditer(text) if re.fullmatch(_LISTENER % "tableContainer-pagination-results-nextPageUrl", m[1])}
    bases = set(_BASE_URL.findall(text))
    if len(listeners) > 1 or len(bases) > 1:
        raise _bad("Odpověď eBadatelny uvádí více různých adres pro další stranu.")
    result.next_listener = listeners.pop() if listeners else None
    result.base_url = bases.pop() if bases else None
    if result.page + 1 < result.pages and result.next_listener is None:
        raise _bad("Odpověď eBadatelny neuvádí adresu další strany výsledků.")
    return result


# ---------------------------------------------------------------- archival records: search page, results, titles

@dataclass
class ArchiveForm:
    listener: str            # query of the search-button listener, e.g. "0-1.0-searchForm-searchButton"
    base_url: str            # Wicket-Ajax-BaseURL, e.g. "pages/SearchArchivaliePage?0"
    community_word: str      # the words the page uses in its locality list ("community", "district"): they depend on the page language
    district_word: str
    types: dict[str, str]    # normalized label of an archival type → the option value the form posts


def parse_archive_search_form(html: str) -> ArchiveForm:
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form", class_="searchForm")
    if form is None:
        raise _bad("Stránka nemá vyhledávací formulář archiválií eBadatelny; nejde o prázdný výsledek.")
    names = {i.get("name") for i in form.find_all(["input", "select"]) if i.get("name")}
    if not {"searchLocality", "searchArchivalie", "typArchivalie", "mistoUlozeni", "archivniFondId", "dateYearFrom", "dateYearTo"} <= names:
        raise _bad("Formulář hledání archiválií eBadatelny má jiná pole, než Rubrika zná.")
    listeners = {m[1] for m in _ARCH_AJAX.finditer(html) if re.fullmatch(_LISTENER % "searchForm-searchButton", m[1])}
    bases = set(_ARCH_BASE_URL.findall(html))
    if len(listeners) != 1 or len(bases) != 1:
        raise _bad("Stránka archiválií neuvádí (jednoznačně) tlačítko hledání nebo základ adres; složení komponent se změnilo.")
    words = {k: re.findall(rf'\bvar {k} = "([A-Za-z]{{2,30}})";', html) for k in ("okresTerm", "obecTerm")}
    select = form.find("select", attrs={"name": "typArchivalie"})
    if any(len(v) != 1 for v in words.values()) or select is None:
        raise _bad("Stránka hledání archiválií eBadatelny neobsahuje výrazy seznamu lokalit nebo seznam typů archiválií.")
    types = {normalize(_text(o)): o.get("value", "") for o in select.find_all("option") if _text(o) and (o.get("value") or "").isdigit()}
    return ArchiveForm(listeners.pop(), bases.pop(), words["obecTerm"][0], words["okresTerm"][0], types)


def locality_entry(form: ArchiveForm, name: str, community: str, district: str) -> str:
    """The entry of the archive's locality list the archival-records search accepts (only exactly such an entry finds anything)."""
    return f"{name} - {form.community_word} {community} - {form.district_word} {district}"


@dataclass
class ArchiveRow:
    archivalie_id: int
    title: str
    unit_type: str
    fund: str
    inventory: str
    signature: str
    localities: list[str]
    span: str            # "Date (year from to)"
    year: str            # "Date (year)"
    exact: str           # "Date (exact date)"
    original: str
    scans: int


@dataclass
class ArchiveResultPage:
    rows: list[ArchiveRow] = field(default_factory=list)
    total: int = 0
    page: int = 0
    pages: int = 1
    next_listener: str | None = None
    base_url: str | None = None
    message: str | None = None


def parse_archive_results(xml: bytes) -> ArchiveResultPage:
    components, scripts = _components(xml)
    if not components:
        raise _bad("Odpověď eBadatelny neobsahuje žádnou komponentu.")
    soup = BeautifulSoup("<body>" + "".join(components) + "</body>", "lxml")
    table = soup.find("table", class_="searchResult")
    if table is None:
        visible = [_text(BeautifulSoup(c, "lxml")) for c in components if "data-wicket-placeholder" not in c]
        if (len(components) == 2 and any("data-wicket-placeholder" in c for c in components) and len([v for v in visible if v]) == 1
                and len(visible[0]) <= 200 and "<table" not in "".join(components)):
            return ArchiveResultPage(message=next(v for v in visible if v))
        raise _bad("Odpověď eBadatelny nemá tabulku archiválií ani zprávu o prázdném výsledku.")
    heads = [th.get("class", [""])[0] for th in table.select("thead th")]
    if heads != [f"header{i}" for i in range(1, 12)]:
        raise _bad("Tabulka archiválií eBadatelny má neočekávané sloupce.")
    found = soup.select_one(".resultsFound")
    total = re.search(r"\d+", found.get_text(" ", strip=True).replace("\xa0", "")) if found else None
    dropdown = soup.find("select", attrs={"name": re.compile(r"pagination:results:pageDropdown$")})
    if not total or dropdown is None:
        raise _bad("Odpověď eBadatelny neuvádí počet nalezených archiválií nebo stránek.")
    options = dropdown.find_all("option")
    selected = [o for o in options if o.has_attr("selected")]
    if not options or len(selected) != 1 or not selected[0].get("value", "").isdigit():
        raise _bad("Stránkování výsledků archiválií eBadatelny má neočekávaný tvar.")
    result = ArchiveResultPage(total=int(total[0]), page=int(selected[0]["value"]), pages=len(options))
    seen = set()
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        link = tr.find("a", class_="matrikaLink", href=True)
        m = re.fullmatch(r"/pages/ArchivaliePage/archivalieId/(\d{1,9})", link["href"]) if link else None
        if len(cells) != 11 or not m or tr.get("href") != f"/pages/DataArchivaliePage/archivalieId/{m[1]}/idx/1":
            raise _bad("Řádek výsledků archiválií eBadatelny má neočekávaný tvar (sloupce nebo odkazy na archiválii).")
        values = [_text(c) for c in cells]
        if not values[10].isdigit() or not values[0]:
            raise _bad("Řádek výsledků archiválií eBadatelny nemá název nebo počet snímků.")
        if m[1] in seen:
            continue
        seen.add(m[1])
        result.rows.append(ArchiveRow(int(m[1]), values[0], values[1], values[2], values[3], values[4],
                                      [x.strip() for x in values[5].split(",") if x.strip()], values[6], values[7], values[8], values[9], int(values[10])))
    if not result.rows:
        raise _bad("Tabulka archiválií eBadatelny nemá řádky.")
    text = "\n".join(scripts)
    listeners = {m[1] for m in _ARCH_AJAX.finditer(text) if re.fullmatch(_LISTENER % "tableContainer-pagination-results-nextPageUrl", m[1])}
    bases = set(_ARCH_BASE_URL.findall(text))
    if len(listeners) > 1 or len(bases) > 1:
        raise _bad("Odpověď eBadatelny uvádí více různých adres pro další stranu.")
    result.next_listener = listeners.pop() if listeners else None
    result.base_url = bases.pop() if bases else None
    if result.page + 1 < result.pages and result.next_listener is None:
        raise _bad("Odpověď eBadatelny neuvádí adresu další strany výsledků.")
    return result


_HOUSES = r"(?:,\s*[čc]\.?\s*p\.?\s*.*)"        # ", čp. 1-50", ", č.p. 1-50", ", čp 1-50"
_CENSUS_TITLE = re.compile(r"Sčítání\s+lidu\s+(\d{4})\s*:\s*Okres\s+(.+?)\s+-\s+obec\s+(.+?)(?:\s+-\s+osada\s+(.+?))?(" + _HOUSES + r")?", re.S)
_LAND_BOOK = re.compile(r"pozemkov|gruntovn|purkrecht|purkrechtn|gruntbuch|grundbuch|zemsk[aáé]\s+desk|hlavn[ií]\s+kniha", re.I)


def parse_census_title(title: str) -> dict:
    """'Sčítání lidu 1900: Okres X - obec Y - osada Z, čp. 1-50' → year, district, municipality, part and the house numbers.
    Another shape gives nothing (the year is then read from the year column) and the house numbers stay unknown."""
    text = " ".join((title or "").replace("\xa0", " ").split())
    m = _CENSUS_TITLE.fullmatch(text)
    if not m:
        return {"year": None, "district": None, "municipality": None, "part": None, "house_numbers": HouseNumbers()}
    houses = HouseNumbers()
    if m[5]:
        houses = parse_description_house_numbers("čp. " + re.sub(r"^,\s*[čc]\.?\s*p\.?\s*", "", m[5]))
    return {"year": int(m[1]), "district": m[2], "municipality": m[3], "part": m[4], "house_numbers": houses}


def land_category(title: str) -> str:
    """land_book for a land, ground or emphyteutic book; everything else the archive lists for the place (urbars, registers, chronicles) is other."""
    return "land_book" if _LAND_BOOK.search(title or "") else "other"


def unit_dating(row: ArchiveRow) -> Periods:
    """Dating from the range column, else the year column, else the year of an exact date; anything not understood stays uncertain."""
    for cell in (row.span, row.year):
        if cell and not _DASHES.fullmatch(cell.strip()):
            return parse_periods(cell)
    exact = re.match(r"(\d{4})\b", row.exact or "")
    if exact and not _DASHES.fullmatch((row.exact or "").strip()):
        return parse_periods(exact[1])
    if all(not c or _DASHES.fullmatch(c.strip()) for c in (row.span, row.year, row.exact)):
        return Periods(status="unknown", raw="")
    return Periods(status="uncertain", raw=row.exact)


def parse_unit_detail(html: str, archivalie_id: int) -> dict:
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    soup = BeautifulSoup(html, "lxml")
    headings = soup.select("div.dataPageTitle h1")
    ids = set(re.findall(r'href="/pages/DataArchivaliePage/archivalieId/(\d+)"', html))
    if len(headings) != 1 or not _text(headings[0]) or ids != {str(archivalie_id)}:
        raise _bad("Detail archiválie eBadatelny nemá očekávanou strukturu nebo je to jiná archiválie.")
    info: dict[str, str] = {}
    for row in soup.select(".tableMatrikaBasicInfoRow"):
        columns = row.find_all("div", class_="tableMatrikaBasicInfoColumn", recursive=False)
        if len(columns) >= 2:
            info.setdefault(_text(columns[0]), " ".join(_text(c) for c in columns[1:]))
    localities = [_text(n) for n in soup.select(".obecCastLabel") if _text(n)]
    sections = soup.select(".tableMatrikaDescription")
    description = ""
    if sections:
        rows = sections[0].select(".tableMatrikaDescriptionRow")
        description = "\n".join(s.get_text("\n", strip=True) for s in rows[1:]) if len(rows) > 1 else ""
    scans = info.get("Scans", "")
    if not scans.isdigit():
        raise _bad("Detail archiválie eBadatelny neuvádí počet snímků.")
    return {"title": _text(headings[0]), "info": info, "localities": localities, "scans": int(scans),
            "description": None if not description or _DASHES.fullmatch(description.strip()) else description[:2000]}


# ---------------------------------------------------------------- places

def split_locality(text: str) -> tuple[str, list[str]]:
    """'Újezd / Aujezd unter Kladno / Oujezd pod Kladnem, Újezd pod Kladnem' → (name, variants)."""
    parts = [p.strip() for p in text.split("/")]
    variants = [v.strip() for p in parts[1:] for v in p.split(",") if v.strip()]
    return parts[0], list(dict.fromkeys(variants))


def _district_key(name: str | None) -> str:
    """District names compared without case, accents, hyphens and a leading 'okres'."""
    return re.sub(r"^okres\s+", "", normalize(name or "").replace("-", " ")).strip()


def place_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(name)).strip("-")[:80]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def _starts_like(term: str, name: str) -> bool:
    """Every word of the term begins some word of the name: what the archive's search matches."""
    words = _tokens(name)
    return bool(_tokens(term)) and all(any(w.startswith(t) for w in words) for t in _tokens(term))


@dataclass
class Locality:
    """One entry of the archive's locality list: a name (Czech or a variant; the list does not say which), the community
    (obec) and district (okres) it belongs to, and the other names listed next to it for that community and district."""
    name: str
    community: str
    district: str
    mates: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        digest = hashlib.sha1("|".join([normalize(self.name), normalize(self.community), normalize(self.district)]).encode()).hexdigest()[:8]
        base = place_key(self.name)[:60]
        return f"soapraha:place:{base}-{digest}" if base else f"soapraha:place:{digest}"

    @property
    def label(self) -> str:
        where = ([f"obec {self.community}"] if normalize(self.community) != normalize(self.name) else []) + [f"okres {self.district}"]
        return self.name + ", " + ", ".join(where)


_TAGS = re.compile(r"var availableTags1 = (\[.*?\]);", re.S)


def parse_localities(html: str) -> list[Locality]:
    """The locality list the search page embeds for its autocomplete: entries "<name> - <obec word> <community> - <okres word>
    <district>", the two words being defined by the page itself (so the language of the page does not matter). Consecutive entries
    of one community and district are one locality: its Czech name and variants. Any other shape is PARSE_ERROR."""
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    words = {k: re.findall(rf'\bvar {k} = "([A-Za-z]{{2,30}})";', html) for k in ("okresTerm", "obecTerm")}
    tags = _TAGS.findall(html)
    if any(len(v) != 1 for v in words.values()) or len(tags) != 1:
        raise _bad("Stránka hledání eBadatelny neobsahuje očekávaný seznam lokalit (našeptávač).")
    try:
        entries = json.loads(tags[0])
    except ValueError as e:
        raise _bad("Seznam lokalit eBadatelny není platné pole textů.") from e
    if not isinstance(entries, list) or not entries or not all(isinstance(x, str) and len(x) < 300 for x in entries):
        raise _bad("Seznam lokalit eBadatelny je prázdný nebo má jiné položky než texty.")
    pattern = re.compile(rf"(.+?) - {re.escape(words['obecTerm'][0])} (.+?) - {re.escape(words['okresTerm'][0])} (.+)")
    parsed: list[tuple[str, str, str]] = []
    for entry in entries:
        m = pattern.fullmatch(entry)
        if not m:
            raise _bad("Položka seznamu lokalit eBadatelny má neznámý tvar.")
        parsed.append(tuple(x.strip() for x in m.groups()))
    result: list[Locality] = []
    seen: dict[tuple[str, str, str], Locality] = {}
    start = 0
    for end in range(1, len(parsed) + 1):
        if end == len(parsed) or parsed[end][1:] != parsed[start][1:]:
            run = list(dict.fromkeys(name for name, _, _ in parsed[start:end]))
            for name in run:
                mates = [o for o in run if o != name]
                ident = (normalize(name), normalize(parsed[start][1]), normalize(parsed[start][2]))
                known = seen.get(ident)
                if known is None:       # the list repeats some entries; one entry stays, with the neighbours of every appearance
                    seen[ident] = known = Locality(name, parsed[start][1], parsed[start][2], [])
                    result.append(known)
                known.mates += [m for m in mates if m not in known.mates]
            start = end
    return result


def match_localities(localities: list[Locality], term: str) -> tuple[list[Locality], bool]:
    """Entries whose name (Czech or variant) contains every word of the term; when none does, those with a word starting
    like the term's words (second value True). The exact name comes first."""
    wanted = _tokens(term)
    strict = [loc for loc in localities if wanted and wanted <= _tokens(loc.name)]
    loose = [] if strict else [loc for loc in localities if _starts_like(term, loc.name)]
    exact = normalize(term)
    found = strict or loose
    found.sort(key=lambda loc: (0 if normalize(loc.name) == exact else 1, len(loc.name), loc.name, loc.district))
    return found, bool(loose)


# ---------------------------------------------------------------- detail and viewer

def parse_detail(html: str, matrika_id: int) -> dict:
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    soup = BeautifulSoup(html, "lxml")
    headings = soup.select("div.dataPageTitle h1")
    ids = set(re.findall(r'href="/pages/DataMatrikaPage/matrikaId/(\d+)"', html))
    if len(headings) != 1 or not _text(headings[0]) or ids != {str(matrika_id)}:
        raise _bad("Detail matriky eBadatelny nemá očekávanou strukturu nebo je to jiná matrika.")
    info: dict[str, str] = {}
    for row in soup.select(".tableMatrikaBasicInfoRow"):
        columns = row.find_all("div", class_="tableMatrikaBasicInfoColumn", recursive=False)
        if len(columns) >= 2:
            info.setdefault(_text(columns[0]), " ".join(_text(c) for c in columns[1:]))
    localities = [_text(n) for n in soup.select(".obecCastLabel") if _text(n)]
    if not localities:
        raise _bad("Detail matriky eBadatelny neuvádí lokality.")
    sections = soup.select(".tableMatrikaDescription")
    description = ""
    if sections:
        rows = sections[0].select(".tableMatrikaDescriptionRow")
        description = "\n".join(s.get_text("\n", strip=True) for s in rows[1:]) if len(rows) > 1 else ""
    scans = info.get("Number of scans", "")
    return {"title": _text(headings[0]), "info": info, "localities": localities,
            "description": None if not description or _DASHES.fullmatch(description.strip()) else description[:2000],
            "scans": int(scans) if scans.isdigit() else None}


def parse_viewer(html: str, matrika_id: int, scan: int, total: int | None, unit: bool = False) -> str:
    """File name of the scan's JPEG from the viewer page of `scan` (1-based); the page must belong to the book (or, with `unit`,
    the archival record) and scan."""
    if _EXPIRED.search(html[:20000]):
        raise _expired()
    soup = BeautifulSoup(html, "lxml")
    counts, indexes, urls = re.findall(r"\bvar pageCount = (\d+);", html), re.findall(r"\bvar idx = (\d+);", html), re.findall(r"\bvar basicUrl = '([^']*)';", html)
    links = [_JPEG_LINK.fullmatch(a["href"]) for a in soup.select("a.manipulationButton[href]") if "wicket/resource" in a["href"]]
    if len(counts) != 1 or len(indexes) != 1 or urls != [f"/pages/DataArchivaliePage/archivalieId/{matrika_id}" if unit else f"/pages/DataMatrikaPage/matrikaId/{matrika_id}"] or len(links) != 1 or links[0] is None:
        raise _bad("Prohlížeč eBadatelny nemá očekávanou strukturu (počet snímků, číslo snímku, matrika nebo odkaz ke stažení).")
    if int(indexes[0]) != scan - 1 or int(links[0][2]) != scan:
        raise _bad("Prohlížeč eBadatelny neotevřel požadovaný snímek.", requested=scan, shown=int(indexes[0]) + 1)
    if total is not None and int(counts[0]) != total:
        raise ResearchError("SOURCE_CHANGED", "Počet snímků knihy se v eBadatelně změnil; znovu vypište a otevřete knihu.",
                            listed=total, shown=int(counts[0]))
    return links[0][1]


def check_jpeg(raw: bytes, max_pixels: int) -> None:
    """Magic bytes, JPEG format, pixel budget and a complete decode: nothing else is kept in the cache."""
    if raw[:3] != b"\xff\xd8\xff":
        raise ResearchError("INCOMPLETE_IMAGE", "Stažený snímek není JPEG.")
    try:
        image = Image.open(io.BytesIO(raw))
        if image.format != "JPEG" or image.width * image.height > max_pixels:
            raise ValueError()
        image.load()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as e:
        raise ResearchError("INCOMPLETE_IMAGE", "Snímek z eBadatelny nelze bezpečně dekódovat jako úplný JPEG.") from e


def citation_of(title: str) -> str:
    return f"{ARCHIVE_NAME}, {FUND_TITLE}, {title}"


def unit_citation(title: str, fund: str = "", signature: str = "", inventory: str = "", storage: str | None = None) -> str:
    """Archive (the holding SOkA once the detail names it; the record is read in SOA Praha's eBadatelna), fund, signature, title."""
    where = f"{storage} (eBadatelna SOA Praha)" if storage else ARCHIVE_NAME
    parts = [where, f"fond {fund}" if fund and not _DASHES.fullmatch(fund.strip()) else "",
             f"sign. {signature}" if signature and not _DASHES.fullmatch(signature.strip()) else
             f"inv. č. {inventory}" if inventory and not _DASHES.fullmatch(inventory.strip()) else "", title]
    return ", ".join(x for x in parts if x)


def manifest_hash(matrika_id: int, total: int, unit: bool = False) -> str:
    prefix = "soapraha\0unit\0" if unit else "soapraha\0"
    return hashlib.sha256(f"{prefix}{int(matrika_id)}\0{int(total)}".encode()).hexdigest()


def sort_key(number: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", number)]


# ---------------------------------------------------------------- catalogue client

@dataclass
class SearchOutcome:
    rows: list
    total: int
    complete: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    rows: list[Row]
    total: int
    complete: bool
    warnings: list[str] = field(default_factory=list)


class SoaPrahaCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    def session(self, sid: str, owner: str) -> SiteSession:
        return SiteSession(sid, owner, SOAPRAHA_HOST, SEARCH_URL, opener_redirects=True)

    # -- search
    @staticmethod
    def _ajax_headers(base_url: str) -> dict:
        return {"Wicket-Ajax": "true", "Wicket-Ajax-BaseURL": base_url, "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/xml, text/xml, */*; q=0.01"}

    async def _run_search(self, session: SiteSession, term: str) -> SearchResult:
        await self.source.session_open(session)
        page = await self.source.session_get(session, session.landing or SEARCH_URL)
        form = parse_search_form(page.decode("utf-8-sig", errors="replace"))
        base = form.base_url
        body = await self.source.session_post(
            session, f"{SEARCH_URL}?{form.listener}",
            dict(zip(SOAPRAHA_SEARCH_FIELDS, ("1", "radio0", term, "1000", "2100"))), headers=self._ajax_headers(base))
        result = parse_results(body)
        if result.message is not None:
            return SearchResult([], 0, True, [f"eBadatelna odpověděla: {result.message}"])
        rows, warnings, pages = list(result.rows), list(result.warnings), 1
        while result.page + 1 < result.pages:
            if pages >= MAX_RESULT_PAGES:
                warnings.append(f"Výsledky mají {result.pages} stran; načteno {pages} (limit {MAX_RESULT_PAGES} stran na hledání). "
                                "Matriky na dalších stranách chybí; hledání zpřesněte.")
                return SearchResult(rows, result.total, False, warnings)
            base = result.base_url or base
            body = await self.source.session_get(session, f"{SEARCH_URL}?{result.next_listener}", kind="xml", headers=self._ajax_headers(base))
            nxt = parse_results(body)
            if nxt.page != result.page + 1 or nxt.total != result.total:
                raise _bad("Stránkování výsledků eBadatelny se nechová podle očekávání.")
            rows += nxt.rows
            warnings += nxt.warnings
            result, pages = nxt, pages + 1
        if len({r.matrika_id for r in rows}) != result.total:
            raise _bad("Počet načtených matrik nesouhlasí s počtem, který eBadatelna uvádí.", parsed=len(rows), stated=result.total)
        return SearchResult(rows, result.total, True, warnings)

    async def search(self, sid, owner, term: str) -> SearchResult:
        """Register search by locality over the full year range; the answer is remembered for the metadata TTL."""
        key = hashlib.sha256(normalize(term).encode()).hexdigest()[:32]
        cached = self.store.entity("soapraha_search", key, self.source.cfg.metadata_ttl)
        if cached is not None:
            return SearchResult([Row(**r) for r in cached["rows"]], cached["total"], cached["complete"], cached["warnings"])
        session = self.session(sid, owner)
        for attempt in (0, 1):
            try:
                result = await self._run_search(session, term)
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        if result.rows and result.complete:      # a cut answer is not remembered: a later, narrower or larger call may read more
            self.store.put_entity("soapraha_search", key, {"rows": [asdict(r) for r in result.rows], "total": result.total,
                                                           "complete": result.complete, "warnings": result.warnings})
        return result

    async def localities(self, sid, owner) -> list[Locality]:
        """The archive's locality list, from the user's cache or from the search page (one session, no search request)."""
        cached = self.store.entity("soapraha_localities", "all", self.source.cfg.metadata_ttl)
        if cached is not None:
            return [Locality(*entry) for entry in cached["localities"]]
        session = self.session(sid, owner)
        for attempt in (0, 1):
            try:
                await self.source.session_open(session)
                page = await self.source.session_get(session, session.landing or SEARCH_URL)
                found = parse_localities(page.decode("utf-8-sig", errors="replace"))
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        self.store.put_entity("soapraha_localities", "all", {"localities": [[loc.name, loc.community, loc.district, loc.mates] for loc in found]})
        return found

    async def places(self, sid, owner, term: str, district: str | None = None, kind: str = "parish_registers") -> tuple[list[Place], list[str]]:
        every = await self.localities(sid, owner)
        found, loose = match_localities(every, term)
        warnings = []
        if loose:
            warnings.append("Žádná lokalita se nejmenuje jako hledaný výraz (ani německy); nabízené jsou ty, jejichž název tak začíná.")
        wanted = _district_key(district)
        exact = normalize(term)
        found.sort(key=lambda loc: (0 if wanted and _district_key(loc.district) == wanted else 1, 0 if normalize(loc.name) == exact else 1,
                                    len(loc.name), loc.name, loc.district))
        shown = [loc for loc in found if normalize(loc.name) == exact][:MAX_EXACT_PLACES]
        shown += [loc for loc in found if loc not in shown][:max(0, MAX_PLACES - len(shown))]
        shown.sort(key=lambda loc: found.index(loc))
        if len(shown) < len(found):
            omitted = [loc for loc in found if loc not in shown]
            districts = sorted({loc.district for loc in omitted})
            warnings.append(f"Zobrazeno {len(shown)} míst z {len(found)}; vynecháno {len(omitted)} míst v okresech "
                            f"{', '.join(districts[:12])}{'…' if len(districts) > 12 else ''}. Zadejte district=<okres> nebo upřesněte název.")
        if not found:
            warnings.append("Seznam lokalit SOA Praha hledaný výraz neobsahuje (hledá se i v německých variantách názvů). Zkuste jiný tvar "
                            "názvu. Nejde o důkaz, že místo v archivu není.")
        by_name: dict[str, list[Locality]] = {}
        for loc in every:
            by_name.setdefault(place_key(loc.name), []).append(loc)
        places = []
        for loc in shown:
            place = Place(source_id=loc.id, label=loc.label, names=[loc.name], source_url=SEARCH_URL if kind == "parish_registers" else ARCHIVE_SEARCH_URL)
            others = [o for o in by_name.get(place_key(loc.name), []) if o is not loc]
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.put_entity("soapraha_place", place.source_id, {
                "community": loc.community, "district": loc.district, "mates": loc.mates, "homonyms": len(others),
                "homonym_districts": sorted({_district_key(o.district) for o in others}),
                "homonym_communities": sorted({place_key(o.community) for o in others})})
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
            places.append(place)
        if any(self.store.entity("soapraha_place", p.source_id)["homonyms"] for p in places):
            warnings.append("Lokalit tohoto jména je v archivu víc (v jiných obcích nebo okresech); rozlišuje je obec a okres v labelu.")
        return places, warnings

    # -- books
    def _book(self, row: Row, review: str | None = None) -> SoaBook:
        return SoaBook(source_id=f"soapraha:book:{row.matrika_id}", number=row.title, creator=row.originator, district=row.district,
                       originator_type=row.originator, localities=row.localities, coverage=coverage_of(row.years),
                       image_count=row.scans, detail_url=detail_url(row.matrika_id), citation=citation_of(row.title),
                       raw_notes="; ".join(row.years)[:400], review_reason=review)

    async def books(self, sid, owner, place_ids: list[str]) -> tuple[list[SoaBook], bool, list[str]]:
        """Registers of the chosen localities: the archive's search by the locality's exact Czech name (it matches word starts),
        then only the rows that list that locality. A row of a same-named locality elsewhere is left out."""
        books: dict[str, SoaBook] = {}
        warnings, complete = [], True
        for pid in place_ids:
            entity, extra = self.store.entity("place", pid), self.store.entity("soapraha_place", pid)
            if not entity or not entity.get("names") or extra is None:
                raise ResearchError("NOT_FOUND", "Místo SOA Praha není známé; nejprve použijte find_place.")
            names = entity["names"]
            community_key = place_key(extra["community"])
            result = None
            mates = sorted(extra["mates"], key=lambda m: place_key(m) != community_key)     # the community's own name first
            used = names[0]
            for name in [names[0], *mates][:3]:    # the name itself; a variant of it (a German name) is searched by its neighbours
                used, keys = name, {place_key(name)}
                result = await self.search(sid, owner, name)
                warnings += result.warnings
                complete = complete and result.complete
                if any(place_key(n) in keys for row in result.rows for n in row.localities):
                    break
            if used != names[0] and any(place_key(n) in keys for row in result.rows for n in row.localities):
                warnings.append(f"Název {names[0]} archiv v matrikách nepoužívá; hledáno podle sousedního názvu {used} téže obce.")
            if not result or not result.rows:
                complete = False
                warnings.append(f"eBadatelna pro {names[0]} nevrátila žádné matriky. Nejde o důkaz, že knihy neexistují.")
                continue
            sibling = used != names[0] and any(place_key(n) in keys for row in result.rows for n in row.localities)
            own = _district_key(extra["district"])
            kept = listed = 0
            others: dict[str, int] = {}
            for row in result.rows:
                named = any(place_key(n) in keys for n in row.localities)
                listed += named
                reason = "Název lokality je v archivu jen variantou; kniha může patřit k jiné části téže obce, ověřte lokality v detailu knihy." if sibling else None
                keep = named
                if named and extra["homonyms"] and not any(place_key(n) == community_key for n in row.localities if community_key not in keys):
                    row_district = _district_key(row.district)
                    if row_district == own:
                        pass
                    elif row_district and row_district in extra["homonym_districts"]:
                        keep = False                      # the register is of the district of a same-named locality
                    else:                                 # the district of a register is not always the locality's okres: ask a human
                        reason = reason or (f"Lokalita {names[0]} existuje i jinde; okres matriky ({row.district}) se liší od okresu místa "
                                            f"({extra['district']}), ověřte lokality v detailu knihy.")
                if keep:
                    kept += 1
                    books.setdefault(f"soapraha:book:{row.matrika_id}", self._book(row, reason))
                else:
                    for n in row.localities:
                        if named and place_key(n) in keys or _starts_like(names[0], n):
                            others[n] = others.get(n, 0) + 1
            if sibling:
                warnings.append(f"Název {names[0]} archiv v matrikách nepoužívá; hledáno podle sousedního názvu {used} téže obce. Kniha může patřit "
                                "k jiné části obce, u každé je needs_review; ověřte lokality knihy v detailu (open_source).")
            if listed and not kept:
                complete = False
                warnings.append(f"eBadatelna vrátila {listed} matrik s lokalitou {names[0]}, ale žádnou se nepodařilo jednoznačně přiřadit k místu "
                                f"({extra['district']}); výpis není úplný. Zkuste jiné místo stejného jména nebo prohlédněte matriky v eBadatelně.")
            if kept < len(result.rows):
                hint = (" Mezi nimi jsou matriky míst " + ", ".join(f"{n} ({c})" for n, c in sorted(others.items(), key=lambda x: -x[1])[:3])
                        + "; vypište je zvlášť podle jejich place_id z find_place.") if others else ""
                warnings.append(f"eBadatelna vrátila {len(result.rows)} matrik, {kept} z nich patří k lokalitě {names[0]} ({extra['district']}); "
                                f"{len(result.rows) - kept} ne (ve výpisu nejsou).{hint}")
        for book in books.values():
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return list(books.values()), complete, list(dict.fromkeys(warnings))

    async def open_register(self, sid, owner, book: SoaBook) -> tuple[SoaBook, list[str]]:
        matrika_id = book_id_of(book.source_id)
        session = self.session(sid, owner)
        for attempt in (0, 1):
            try:
                body = await self.source.session_get(session, detail_url(matrika_id), follow=2)
                detail = parse_detail(body.decode("utf-8-sig", errors="replace"), matrika_id)
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        warnings = []
        scans = detail["scans"] or book.image_count
        if not scans:
            raise ResearchError("UNSUPPORTED_SOURCE", "eBadatelna neuvádí počet snímků matriky.")
        if book.image_count and detail["scans"] and book.image_count != detail["scans"]:
            warnings.append(f"Počet snímků ve výpisu ({book.image_count}) a v detailu ({detail['scans']}) se liší; použit je počet z detailu.")
        info = detail["info"]
        opened = SoaBook.model_validate({
            **book.model_dump(), "number": detail["title"], "localities": detail["localities"], "image_count": scans,
            "citation": citation_of(detail["title"]), "detail_url": detail_url(matrika_id), "description": detail["description"],
            "district": info.get("District") or book.district, "physical_state": info.get("Physical state") or None,
            "completeness": info.get("Completeness") or None, "register_type": info.get("Register type") or None})
        return opened, warnings

    # -- pages
    def shell_page(self, book: SoaBook | SoaUnit, n: int) -> Page:
        """A scan's page before its JPEG is fetched: the viewer address stands in for the source (and is the cache key)."""
        kind, ident = ident_of(book.source_id)
        total = book.image_count
        if not total or not 1 <= n <= total:
            raise ResearchError("NOT_FOUND", "Snímek leží mimo rozsah knihy.")
        link = viewer_of(book.source_id, n)
        return Page(page_id=f"{book.source_id}:{n}", book_id=book.source_id, scan_number=n, image_key=f"scan-{n}", source_url=link,
                    viewer_url=link, source_type="jpeg", manifest_sha256=manifest_hash(ident, total, unit=kind == "unit"), source="soapraha",
                    citation=book.citation or citation_of(book.title if kind == "unit" else book.number), archive="soapraha")

    async def scan(self, sid, owner, page: Page) -> tuple[bytes, str]:
        """The whole JPEG of a scan: from the user's cache, else through one session (viewer page, then the download that
        only that session may make). Returns the bytes and the file name the archive gave the scan."""
        kind, matrika_id = ident_of(page.book_id)
        total = None
        book = self.store.entity("unit" if kind == "unit" else "book", page.book_id)
        if book:
            total = book.get("image_count")
        cached = self.source.cache_get(page.source_url, self.source.cfg.metadata_ttl)
        if cached is not None:
            return cached, page.image_key
        session = self.session(sid, owner)
        for attempt in (0, 1):
            try:
                body = await self.source.session_get(session, viewer_of(page.book_id, page.scan_number))
                name = parse_viewer(body.decode("utf-8-sig", errors="replace"), matrika_id, page.scan_number, total, unit=kind == "unit")
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        limit = self.source.cfg.max_source_pixels
        raw = await self.source.session_get(session, f"{BASE}/wicket/resource/org.apache.wicket.Application/{name}", kind="scan",
                                            check=lambda data: check_jpeg(data, limit))
        self.source.cache_put(page.source_url, raw)
        return raw, name

    # -- archival records (census, land books)
    def archive_session(self, sid: str, owner: str) -> SiteSession:
        return SiteSession(sid, owner, SOAPRAHA_HOST, ARCHIVE_SEARCH_URL, opener_redirects=True)

    async def _run_archive_searches(self, session: SiteSession, jobs: list[tuple]) -> dict[tuple, SearchOutcome]:
        """Each job is (locality name, community, district, archival type label or None); the locality is posted as the
        archive's own list entry, the type as the option value the form offers for its label. One session, one form."""
        await self.source.session_open(session)
        page = await self.source.session_get(session, session.landing or ARCHIVE_SEARCH_URL)
        form = parse_archive_search_form(page.decode("utf-8-sig", errors="replace"))
        out: dict[tuple, SearchOutcome] = {}
        for job in jobs:
            name, community, district, type_label = job
            value = ""
            if type_label is not None:
                value = form.types.get(normalize(type_label))
                if value is None:
                    out[job] = SearchOutcome([], 0, False, [f"eBadatelna nenabízí typ archiválií „{type_label}“; archiválie tohoto typu nejsou ve výpisu."])
                    continue
            base = form.base_url
            body = await self.source.session_post(
                session, f"{ARCHIVE_SEARCH_URL}?{form.listener}",
                dict(zip(SOAPRAHA_ARCHIVE_FIELDS, (locality_entry(form, name, community, district), "", value, "", "", "1000", "2100"))),
                headers=self._ajax_headers(base))
            result = parse_archive_results(body)
            if result.message is not None:
                out[job] = SearchOutcome([], 0, True, [])
                continue
            rows, warnings, pages = list(result.rows), [], 1
            while result.page + 1 < result.pages:
                if pages >= MAX_RESULT_PAGES:
                    warnings.append(f"Výsledky archiválií mají {result.pages} stran; načteno {pages} (limit {MAX_RESULT_PAGES} stran na hledání). "
                                    "Archiválie na dalších stranách chybí.")
                    break
                base = result.base_url or base
                body = await self.source.session_get(session, f"{ARCHIVE_SEARCH_URL}?{result.next_listener}", kind="xml", headers=self._ajax_headers(base))
                nxt = parse_archive_results(body)
                if nxt.page != result.page + 1 or nxt.total != result.total:
                    raise _bad("Stránkování výsledků archiválií eBadatelny se nechová podle očekávání.")
                rows += nxt.rows
                result, pages = nxt, pages + 1
            complete = not warnings
            if complete and len({r.archivalie_id for r in rows}) != result.total:
                raise _bad("Počet načtených archiválií nesouhlasí s počtem, který eBadatelna uvádí.", parsed=len(rows), stated=result.total)
            out[job] = SearchOutcome(rows, result.total, complete, warnings)
        return out

    async def archive_search(self, sid, owner, jobs: list[tuple]) -> dict[tuple, SearchOutcome]:
        """Archival-records searches by locality (and type), each remembered for the metadata TTL when complete and non-empty."""
        keys = {job: hashlib.sha256("|".join([normalize(job[0]), normalize(job[1]), normalize(job[2]), normalize(job[3] or "")]).encode()).hexdigest()[:32]
                for job in jobs}
        found: dict[tuple, SearchOutcome] = {}
        for job, key in keys.items():
            cached = self.store.entity("soapraha_archive_search", key, self.source.cfg.metadata_ttl)
            if cached is not None:
                found[job] = SearchOutcome([ArchiveRow(**r) for r in cached["rows"]], cached["total"], cached["complete"], cached["warnings"])
        missing = [job for job in jobs if job not in found]
        if missing:
            session = self.archive_session(sid, owner)
            for attempt in (0, 1):
                try:
                    fresh = await self._run_archive_searches(session, missing)
                    break
                except ResearchError as e:
                    if e.details.get("reason") != "session_expired" or attempt:
                        raise
                    session.reset()
            for job, outcome in fresh.items():
                found[job] = outcome
                if outcome.rows and outcome.complete:
                    self.store.put_entity("soapraha_archive_search", keys[job], {
                        "rows": [asdict(r) for r in outcome.rows], "total": outcome.total, "complete": outcome.complete, "warnings": outcome.warnings})
        return found

    def _unit(self, row: ArchiveRow, kind: str, review: str | None = None) -> SoaUnit:
        dash = lambda v: None if not v or _DASHES.fullmatch(v.strip()) else v
        census = kind == "census"
        parsed = parse_census_title(row.title) if census else {}
        year = parsed.get("year") or (int(row.year) if census and row.year.isdigit() else None)
        return SoaUnit(source_id=f"soapraha:unit:{row.archivalie_id}", kind=kind, title=row.title, original_title=dash(row.original),
                       unit_type=row.unit_type, category="other" if census else land_category(row.title), fund=dash(row.fund),
                       signature=dash(row.signature), inventory_number=dash(row.inventory), dating=unit_dating(row), year=year,
                       district=parsed.get("district"), municipality=parsed.get("municipality"), part=parsed.get("part"),
                       house_numbers=parsed.get("house_numbers") or HouseNumbers(), localities=row.localities, image_count=row.scans,
                       detail_url=unit_detail_url(row.archivalie_id), citation=unit_citation(row.title, row.fund, row.signature, row.inventory),
                       review_reason=review)

    async def units(self, sid, owner, place_ids: list[str], kind: str) -> tuple[list[SoaUnit], bool, list[str]]:
        """Census sets or land records of the chosen localities: the archival-records search by the locality's own list entry (and by
        archival type). A record whose title shows it belongs to a same-named locality of another district is left out; one that
        cannot be told is kept for review."""
        types = [CENSUS_TYPE] if kind == "census" else list(LAND_TYPES)
        wanted = {normalize(t) for t in types}
        places = []
        for pid in place_ids:
            entity, extra = self.store.entity("place", pid), self.store.entity("soapraha_place", pid)
            if not entity or not entity.get("names") or extra is None:
                raise ResearchError("NOT_FOUND", "Místo SOA Praha není známé; nejprve použijte find_place.")
            places.append((pid, entity["names"][0], extra))
        outcomes = await self.archive_search(sid, owner, [(name, extra["community"], extra["district"], t) for _, name, extra in places for t in types])
        units: dict[str, SoaUnit] = {}
        warnings: list[str] = []
        complete = True
        for pid, name, extra in places:
            own, community = _district_key(extra["district"]), _tokens(extra["community"])
            seen = kept = 0
            elsewhere: list[str] = []
            historical: set[str] = set()
            for t in types:
                outcome = outcomes[(name, extra["community"], extra["district"], t)]
                complete = complete and outcome.complete
                warnings += outcome.warnings
                for row in outcome.rows:
                    if normalize(row.unit_type) not in wanted:
                        continue
                    seen += 1
                    review = None
                    named = (parse_census_title(row.title)["district"] or "") if kind == "census" else ""
                    municipality = (parse_census_title(row.title)["municipality"] or "") if kind == "census" else ""
                    district = _district_key(named)
                    if district and district != own:
                        historical.add(named)
                    if extra["homonyms"]:
                        if district:
                            if district == own:
                                pass
                            elif (district in extra["homonym_districts"] and place_key(municipality) in extra["homonym_communities"]
                                  and place_key(municipality) != place_key(extra["community"])):
                                elsewhere.append(row.title)            # district and municipality both point to the other locality
                                continue
                            else:
                                review = (f"Lokalita {name} existuje i jinde a okres sčítání ({named}) je historický politický okres, který se může lišit od "
                                          f"dnešního okresu místa ({extra['district']}); ověřte obec {extra['community']} v názvu a lokality v detailu.")
                        elif not (community and community <= _tokens(row.title + " " + row.fund)):
                            review = (f"Lokalita {name} existuje i jinde; z názvu archiválie nelze poznat, zda patří k obci {extra['community']} "
                                      f"({extra['district']}), ověřte lokality v detailu.")
                    kept += 1
                    unit = self._unit(row, kind, review)
                    known = self.store.entity("unit", unit.source_id)
                    if known and known.get("storage_location") and known.get("citation"):
                        unit = unit.model_copy(update={"storage_location": known["storage_location"], "citation": known["citation"]})
                    units.setdefault(unit.source_id, unit)
            if elsewhere:
                warnings.append(f"eBadatelna pro {name} vrátila i {len(elsewhere)} archiválií lokality téhož jména jinde (okres i obec v názvu ukazují jinam); "
                                f"ve výpisu nejsou: {'; '.join(elsewhere[:6])}{'…' if len(elsewhere) > 6 else ''}.")
            if historical:
                warnings.append(f"Okres v názvu sčítání ({', '.join(sorted(historical))}) je historický politický okres; dnešní okres místa {name} je {extra['district']}.")
            if not seen:
                warnings.append(f"eBadatelna pro {name} nevrátila žádné {'sčítací operáty' if kind == 'census' else 'pozemkové knihy ani jiné související archiválie'}. "
                                "Nejde o důkaz, že neexistují (mohou být uvedeny pod jinou lokalitou, např. pod obcí nebo osadou).")
        for unit in units.values():
            self.store.put_entity("unit", unit.source_id, unit.model_dump())
            self.store.link(sid, owner, "book_option", unit.source_id, {**unit.model_dump(), "kind": kind})
        return list(units.values()), complete, list(dict.fromkeys(warnings))

    async def open_unit(self, sid, owner, unit: SoaUnit) -> tuple[SoaUnit, list[str]]:
        archivalie_id = unit_id_of(unit.source_id)
        session = self.archive_session(sid, owner)
        for attempt in (0, 1):
            try:
                body = await self.source.session_get(session, unit_detail_url(archivalie_id), follow=2)
                detail = parse_unit_detail(body.decode("utf-8-sig", errors="replace"), archivalie_id)
                break
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        warnings = []
        if not detail["scans"]:
            raise ResearchError("UNSUPPORTED_SOURCE", "Archiválie nemá v eBadatelně žádné snímky.")
        if unit.image_count and unit.image_count != detail["scans"]:
            warnings.append(f"Počet snímků ve výpisu ({unit.image_count}) a v detailu ({detail['scans']}) se liší; použit je počet z detailu.")
        info = {k: (None if _DASHES.fullmatch(v.strip()) else v) for k, v in detail["info"].items()}
        fund = info.get("Archival fund") or unit.fund
        signature, inventory = info.get("Signature") or unit.signature, info.get("Inventory number") or unit.inventory_number
        opened = unit.model_copy(update={
            "title": detail["title"], "localities": detail["localities"] or unit.localities, "image_count": detail["scans"], "fund": fund,
            "signature": signature, "inventory_number": inventory, "original_title": info.get("Original title") or unit.original_title,
            "storage_location": info.get("Storage location"), "unit_type": info.get("Archival type") or unit.unit_type, "description": detail["description"], "detail_url": unit_detail_url(archivalie_id),
            "citation": unit_citation(detail["title"], fund or "", signature or "", inventory or "", info.get("Storage location"))})
        return SoaUnit.model_validate(opened.model_dump()), warnings
