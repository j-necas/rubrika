"""SOA Třeboň (DigiArchiv, digi.ceskearchivy.cz): the tree of Roman Catholic parishes, of non-Catholic congregations and civil
registers, the register books of a parish, the units of the land-register, seigniorial, urbarial and census branches and the
viewer pages of their scans.

The application has a search, but robots.txt disallows it (/pages/*), so Rubrika does not use it: parishes are found in the
alphabetical parish list (one letter page, chosen by the first letter of the name), books in the parish's overview page, and
the scans are not fetched at all (limited mode: the archive allows them to be viewed in DigiArchiv only). Pages are HTML; source text is data and is never executed.
Any structure that is not recognised is a PARSE_ERROR, never an empty result."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .catalog import normalize, parse_periods
from .census import house_covered, parse_description_house_numbers
from .errors import ResearchError
from .models import EventCoverage, HouseNumbers, Page, Periods, Place, TrebonBook, YearSpan
from .sources import TREBON_HOST

BASE = "https://" + TREBON_HOST
ARCHIVE_NAME = "Státní oblastní archiv v Třeboni"
FUND_TITLE = "Sbírka matrik Jihočeského kraje"      # as the archive names it: „Fond Sbírka matrik Jihočeského kraje je uložen v centrále Státního oblastního archivu v Třeboni“ (tree.php?menu=0&doctree=1n, Czech)
MAX_PLACES = 15
EVENTS = ("birth", "marriage", "death")
# the overview's type letters: B/M/D in English, N/O/Z (narození, oddaní, zemřelí) in Czech
_LETTER_OF_EVENT = {"B": "birth", "M": "marriage", "D": "death", "N": "birth", "O": "marriage", "Z": "death"}
PLACE_ID = re.compile(r"trebon:place:([lc]?)(\d{1,7})")      # l = land records branch, c = census; none = registers
BOOK_ID = re.compile(r"trebon:book:(\d{1,7})")
PAGE_ID = re.compile(r"(trebon:book:\d{1,7}):([1-9]\d{0,4})")
_LETTER_CODE = re.compile(r"1nr[a-z]")
_PARISH_TREE = re.compile(r"1nr[a-z][A-Za-z0-9]{1,3}")
_OPE = re.compile(r'ope\("(?:top\.)?",\s*"(?:DA|tree\.php)\?menu=0&doctree=([0-9A-Za-z]{2,8})(?:&id=(\d{1,7}))?"\)')
_ENTITY_TREE = re.compile(r"1n[Ccegpbhi][A-Za-z0-9]{1,5}")       # a congregation or civil-register office under a confession root: 1nCb
_SECTION_TREE = re.compile(r"1[qstU][A-Za-z0-9]{0,8}")
_SECTION_ENTITY = re.compile(r"1[qstU][A-Za-z0-9]{1,8}")
_LETTER_LABEL = re.compile(r"[A-ZČŘŠŽ]{1,2}(?:/[A-ZČŘŠŽ]{1,2})*")
_YEAR_LABEL = re.compile(r"1[89]\d\d")
_TOGGLE = re.compile(r'toggle\("([0-9A-Za-z]{2,10})"\)')
# Non-Catholic confessions and civil registers: their root lists the congregations directly. (root doctree, name in labels)
CONFESSIONS = (
    ("1nC", "Českobratrská církev evangelická"), ("1nc", "Církev československá husitská"), ("1ne", "Evangelická církev"),
    ("1ng", "Německá evangelická církev"), ("1np", "Pravoslavná církev"), ("1nb", "civilní matriky"),
    ("1nh", "německé civilní matriky"), ("1ni", "německé vojenské matriky"))
# Branches with units other than registers: (root doctree, category of its books, Czech name in labels)
SECTIONS = {
    "land_records": (("1q", "land_book", "pozemkové knihy"), ("1t", "other", "vrchnostenské knihy (soupisy poddaných)"), ("1U", "other", "urbáře")),
    "census": (("1s", "other", "sčítání lidu"),),
}
MAX_WALK_REQUESTS = 24        # new (not cached) tree pages one find_place walk of land books or census may fetch
MAX_WALK_STEPS = 400          # pages one walk may read at all, cached ones included
_STOP = frozenset({"farnost", "fara", "u", "v", "ve", "na", "z", "do", "sv", "svaty", "svateho"})


def _bad(message: str, **details) -> ResearchError:
    return ResearchError("PARSE_ERROR", message, **details)


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def is_trebon_id(value) -> bool:
    return isinstance(value, str) and value.startswith("trebon:")


def parish_id_of(place_id: str) -> int:
    m = PLACE_ID.fullmatch(place_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Místa SOA Třeboň musí mít ID ve tvaru trebon:place:<číslo> (z find_place).")
    return int(m[2])


def place_id_of(kind: str, entity_id: int) -> str:
    """A place's ID carries its branch, so the same archive id in two trees can never overwrite one another."""
    return f"trebon:place:{ {'land_records': 'l', 'census': 'c'}.get(kind, '') }{int(entity_id)}"


def kind_of_place(place_id: str) -> str:
    return {"": "parish_registers", "l": "land_records", "c": "census"}[PLACE_ID.fullmatch(place_id)[1]]


def node_error_is_local(e: ResearchError) -> bool:
    """A failure of one tree page (changed structure, page gone) that a walk may report and go on from. A pause, a gate or a denial
    (429, 503, 401/403, unexpected content) concerns the whole host and stops the walk."""
    return e.code == "PARSE_ERROR" or (e.code == "SOURCE_UNAVAILABLE" and e.details.get("status") in (404, 410) and "retry_after" not in e.details)


def book_id_of(book_id: str) -> int:
    m = BOOK_ID.fullmatch(book_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Knihu SOA Třeboň otevřete podle ID ve tvaru trebon:book:<číslo> z list_sources.")
    return int(m[1])


# ---------------------------------------------------------------- addresses

def letters_url() -> str:
    return f"{BASE}/tree.php?menu=0&doctree=1nr&lang=cs"


def letter_url(code: str) -> str:
    return f"{BASE}/tree.php?menu=0&doctree={code}&lang=cs"


def parish_url(tree: str, parish_id: int) -> str:
    return f"{BASE}/tree.php?menu=0&doctree={tree}&id={int(parish_id)}&lang=cs"


def viewer_url(book_id: int, scan: int) -> str:
    return f"{BASE}/view.php?menu=3&id={int(book_id)}&page={int(scan)}"


def detail_url(book_id: int) -> str:
    """The book's page in the application (the address its overview links to)."""
    return f"{BASE}/DA?menu=3&id={int(book_id)}"


def book_id_from_url(url: str) -> str:
    """Book named by a link the user pasted: the viewer, the application's book address or the permalink /<book>/<scan>."""
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname != TREBON_HOST or p.username or p.password or p.port not in (None, 443):
        raise ResearchError("INVALID_SOURCE", "Je vyžadován HTTPS odkaz na knihu v DigiArchivu SOA Třeboň.")
    query = parse_qs(p.query)
    m = re.fullmatch(r"/(\d{1,7})(?:/\d{1,5}(?:/-?\d+){0,4})?/?", p.path)
    if m:
        return f"trebon:book:{m[1]}"
    ids = query.get("id", [])
    if p.path in ("/view.php", "/DA") and query.get("menu") == ["3"] and len(ids) == 1 and re.fullmatch(r"\d{1,7}", ids[0]):
        return f"trebon:book:{ids[0]}"
    raise ResearchError("INVALID_SOURCE", "Odkaz není knihou DigiArchivu (/<kniha>/<snímek>, view.php nebo DA s menu=3 a id).")


def citation_of(book: TrebonBook) -> str:
    if book.section != "parish_registers":
        branch = (book.tree_path[0][:1].upper() + book.tree_path[0][1:]) if book.tree_path else None
        if branch and book.section == "census" and book.census_year:
            branch += f" {book.census_year}"
        parts = [ARCHIVE_NAME, branch, book.fund_name or book.parish, book.title, f"č. {book.number}" if book.number else None]
        return ", ".join(x for x in parts if x) + f" (DigiArchiv, id {book_id_of(book.source_id)})"
    label = f" [{book.register_label}]" if book.register_label else ""
    return f"{ARCHIVE_NAME}, {FUND_TITLE}, {book.parish}, kniha č. {book.number}{label} (DigiArchiv, id {book_id_of(book.source_id)})"


def scan_citation(book_citation: str | None, n: int) -> str:
    return f"{book_citation}, snímek {n}"


def manifest_hash(book_id: int, total: int) -> str:
    return hashlib.sha256(f"trebon\0{int(book_id)}\0{int(total)}".encode()).hexdigest()


def sort_key(number: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", number)]


# ---------------------------------------------------------------- the parish tree

@dataclass(frozen=True)
class Letter:
    code: str                    # doctree of the letter: 1nrc
    label: str                   # as the tree writes it: "C/Č", "CH"
    initials: tuple[str, ...]    # the same, lower-case and without diacritics: ("c",), ("ch",)


@dataclass(frozen=True)
class ParishRef:
    id: int
    tree: str                    # doctree of the parish, needed to open its page: 1nrcl
    name: str                    # as the letter page writes it, e.g. "České Budějovice (St. Nicholas)"
    confession: str | None = None    # None = Roman Catholic; otherwise the church or register type of a congregation

    @property
    def base(self) -> str:
        return re.sub(r"\s*\([^)]*\)", "", self.name).strip() or self.name

    @property
    def label(self) -> str:
        return f"{self.name}, {self.confession or 'římskokatolická farnost'}"


def _tree_nodes(html: str) -> list[tuple[str, int | None, str]]:
    """(doctree, id, label) of every clickable node of the tree that opens a tree page."""
    soup = BeautifulSoup(html, "lxml")
    nodes = []
    for div in soup.find_all("div", class_="tree_pom"):
        m = _OPE.fullmatch((div.get("onclick") or "").strip())
        if m:
            nodes.append((m[1], int(m[2]) if m[2] else None, _text(div)))
    return nodes


def parse_letters(html: str) -> list[Letter]:
    """The letters of the Roman Catholic parish list from the tree page `1nr`."""
    letters, seen = [], set()
    for doctree, pid, label in _tree_nodes(html):
        if pid is None and _LETTER_CODE.fullmatch(doctree) and doctree not in seen and re.fullmatch(r"[A-ZČŘŠŽ]{1,2}(?:/[A-ZČŘŠŽ]{1,2})*", label):
            seen.add(doctree)
            letters.append(Letter(doctree, label, tuple(dict.fromkeys(normalize(x) for x in label.split("/")))))
    if len(letters) < 3:
        raise _bad("Strom DigiArchivu nemá očekávaný seznam písmen římskokatolických farností.")
    return letters


def pick_letter(letters: list[Letter], term: str) -> Letter | None:
    """The letter a name is filed under: the longest initial that starts it (CH before C)."""
    key = normalize(term)
    best = None
    for letter in letters:
        for initial in letter.initials:
            if key.startswith(initial) and (best is None or len(initial) > best[0]):
                best = (len(initial), letter)
    return best[1] if best else None


def parse_letter(html: str, letter: Letter) -> list[ParishRef]:
    """Parishes under one letter. A letter the tree lists always has parishes: none found means the page has changed."""
    parishes, seen = [], set()
    for doctree, pid, label in _tree_nodes(html):
        if pid is not None and doctree.startswith(letter.code) and _PARISH_TREE.fullmatch(doctree) and pid not in seen and label:
            seen.add(pid)
            parishes.append(ParishRef(pid, doctree, label))
    if not parishes:
        raise _bad("Stránka písmene DigiArchivu neobsahuje žádné farnosti; struktura stromu se změnila.", letter=letter.label)
    return parishes


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalize(text)) if t not in _STOP]


def match_parishes(parishes: list[ParishRef], term: str) -> list[ParishRef]:
    """Every word of the term begins some word of the parish name; the exact name first, then the shorter names."""
    wanted = _tokens(term)
    if not wanted:
        return []
    found = [p for p in parishes if all(any(w.startswith(t) for w in _tokens(p.name)) for t in wanted)]
    exact = normalize(term)
    found.sort(key=lambda p: (0 if normalize(p.base) == exact else 1, len(p.name), p.name))
    return found


def _expanded(html: str) -> set[str]:
    """Doctree codes the page shows expanded (the node the page is about): `toggle("1nCb")`."""
    return set(_TOGGLE.findall(html))


def parse_confession_root(html: str, root: str, confession: str) -> list[ParishRef]:
    """The congregations (or civil-register offices) listed directly under a confession root such as 1nC. A page that is not the root's
    (the tree answers an unknown code with its start page) or has no congregation is a changed structure, never an empty result."""
    if root not in _expanded(html):
        raise _bad("Stránka stromu DigiArchivu není stránkou hledané větve matrik.", root=root)
    found, seen = [], set()
    for doctree, pid, label in _tree_nodes(html):
        if pid is not None and doctree.startswith(root) and doctree != root and _ENTITY_TREE.fullmatch(doctree) and pid not in seen and label:
            seen.add(pid)
            found.append(ParishRef(pid, doctree, label, confession))
    if not found:
        raise _bad("Větev matrik DigiArchivu neobsahuje žádné farnosti ani úřady; struktura stromu se změnila.", root=root)
    return found


# ---------------------------------------------------------------- the land, seigniorial, urbarial and census branches

@dataclass(frozen=True)
class TreeNode:
    code: str
    id: int | None
    label: str


def parse_section_node(html: str, code: str) -> tuple[list[TreeNode], list[TreeNode]]:
    """(groups, entities) below a node, in the shapes seen live: every child's code is the node's code plus ONE character (1q -> 1qa
    -> 1qab -> entity 1qabl; 1s -> 1se -> 1sec -> 1sec9 -> 1sec9c -> entity 1sec9cc). Groups are the next tree level (archive, letter,
    year, court district); entities carry an id and their page holds a table of books. A page that is not the node's, a child of another
    shape and a node without any child are PARSE_ERROR (a walk must never mistake a changed tree for an empty branch)."""
    if code not in _expanded(html):
        raise _bad("Stránka stromu DigiArchivu není stránkou hledané větve.", node=code)
    groups, entities, seen = [], [], set()
    for doctree, pid, label in _tree_nodes(html):
        if doctree == code or not doctree.startswith(code) or not label or (doctree, pid) in seen:
            continue
        seen.add((doctree, pid))
        if len(doctree) != len(code) + 1 or not _SECTION_TREE.fullmatch(doctree):
            raise _bad("Větev stromu DigiArchivu má potomka neznámého tvaru.", node=code, child=doctree)
        (entities if pid is not None else groups).append(TreeNode(doctree, pid, label))
    if not groups and not entities:
        raise _bad("Větev stromu DigiArchivu nemá žádné potomky; struktura se změnila.", node=code)
    return groups, entities


def _letter_of(groups: list[TreeNode]) -> list[Letter]:
    return [Letter(g.code, g.label, tuple(dict.fromkeys(normalize(x) for x in g.label.split("/"))))
            for g in groups if _LETTER_LABEL.fullmatch(g.label)]


def group_score(label: str, term: str, hint: str | None) -> int:
    """0 when the node's name is the one asked for (the term or the district hint begins its words), else 1. Nodes scoring 0 are
    walked alone; when none does, all are (best first), within the budget."""
    words = _tokens(label)
    for text in (hint, term):
        wanted = _tokens(text or "")
        if wanted and all(any(w.startswith(t) for w in words) for t in wanted):
            return 0
    return 1


@dataclass
class UnitRow:
    id: int
    cells: dict[str, str]          # by column key (col_nazev ...)
    headers: dict[str, str]        # the header text of each column
    online: bool


@dataclass
class UnitPage:
    id: int
    title: str
    fund_name: str | None
    localities: list[str]
    info: dict[str, str]
    year: int | None = None        # the census year the page itself states ("Rok 1880")
    rows: list[UnitRow] = field(default_factory=list)
    not_online: int = 0


# Header text -> column key, for tables whose header cells have no usable id (the census tables: every id is just "col_").
_HEADER_KEYS = (("inventarni cislo", "col_inventarni_cislo"), ("signatura", "col_signatura"), ("scitaci obec", "col_obec"), ("obsah", "col_nazev"),
                ("nazev", "col_nazev"), ("datace", "col_datace_od"), ("ukladaci jednotka", "col_ukladaci_jednotka"), ("poradove cislo", "col_poradove_cislo"),
                ("lokality", "col_zapisy_pro"))


def _column_key(th, index: int) -> tuple[str, str]:
    """(key, header text) of a header cell: the id when it names a column, else the header text."""
    text = _text(th.find("div", id=re.compile(r"^dcol_")) or th)
    ident = th.get("id", "")
    if re.fullmatch(r"col_[a-z_]+", ident):
        return ident, text
    norm = normalize(text)
    for prefix, key in _HEADER_KEYS:
        if norm.startswith(prefix):
            return key, text
    return "", text


def parse_unit_page(html: str, entity_id: int) -> UnitPage:
    """The page of one fund, estate or municipality: its description and the table of books. Columns are read by the header cell's id
    or, when the ids are empty (census), by its text; a table without a title column (Název / Obsah) is a changed structure. Rows may be
    unclosed `<tr>`s and are opened by a cell link (`o(id)` or `DA?menu=3&id=<id>`)."""
    soup = BeautifulSoup(html, "lxml")
    titles, tables = soup.select("div.nadpis"), soup.select("table.tab_popis")
    if len(titles) != 1 or len(tables) != 1 or not _text(titles[0]):
        raise _bad("Stránka fondu nebo obce v DigiArchivu nemá očekávaný nadpis a popis.")
    ids = set(re.findall(rf"https://{re.escape(TREBON_HOST)}/(\d{{1,9}})(?![\d/])", tables[0].get_text(" ")))
    if ids != {str(entity_id)}:
        raise _bad("Stránka je jiná než hledaný fond nebo obec, nebo nemá její odkaz.", expected=entity_id)
    info_cells = _info_rows(tables[0])
    fund = next((_text(c) for k, c in info_cells.items() if k.startswith(("nazev archivniho souboru", "archival fond", "archivni soubor"))), None)
    localities = next((_entities(c) for k, c in info_cells.items() if k in ("lokality", "localities")), [])
    info = {k: _text(c)[:400] for k, c in info_cells.items() if k and _text(c)}
    year = next((int(v) for k, v in info.items() if k in ("rok", "year") and re.fullmatch(r"1[89]\d\d", v)), None)
    header = soup.select_one("tr.header")
    if header is None:
        raise _bad("Tabulka knih fondu nebo obce nemá záhlaví.")
    keyed = [_column_key(th, i) for i, th in enumerate(header.find_all("th"))]
    columns, headers = [k for k, _ in keyed], {k: t for k, t in keyed if k}
    if "col_nazev" not in columns:
        raise _bad("Tabulka knih fondu nebo obce nemá sloupec názvu (Název nebo Obsah).")
    page = UnitPage(entity_id, _text(titles[0]), fund, localities, info, year)
    seen = set()
    for tr in header.find_parent("table").find_all("tr", id=re.compile(r"^\d{1,9}$")):
        if tr["id"] in seen:
            raise _bad("Tabulka knih uvádí jednu knihu dvakrát.", book=tr["id"])
        seen.add(tr["id"])
        tds = tr.find_all("td", recursive=False)
        if len(tds) < len(columns):
            raise _bad("Řádek knihy má méně sloupců než záhlaví tabulky.", book=tr["id"])
        opener = re.compile(rf"(?:\bo\({tr['id']}\)|[?&]id={tr['id']}\b)")
        if not any(opener.search(td.get("onclick") or "") for td in tds):
            page.not_online += 1
            continue
        cells = {col: _text(td) for col, td in zip(columns, tds) if col}
        if not cells.get("col_nazev") and not cells.get("col_obec"):
            raise _bad("Řádek knihy nemá název.", book=tr["id"])
        page.rows.append(UnitRow(int(tr["id"]), cells, headers, True))
    if not seen:
        raise _bad("Tabulka knih fondu nebo obce nemá žádné řádky.")
    return page


def parse_row_dating(raw: str | None) -> Periods:
    """A book's dating as the table writes it: „1695–1715“, „1908“, „(1580) 1598–1919“ (the earlier year in brackets only widens
    the span), „asi 1700“ (uncertain). Anything else stays uncertain, so it can never exclude a book."""
    text = " ".join((raw or "").replace("\xa0", " ").split())
    if not text:
        return Periods()
    m = re.fullmatch(r"\((\d{4})\)\s*(\d{4})\s*[-\u2013\u2014]\s*(\d{4})", text)
    if m and int(m[1]) <= int(m[3]):
        return Periods(status="known", spans=[YearSpan(start=int(m[1]), end=int(m[3]))], raw=text)
    return parse_periods(text)


def unit_relevance(book: TrebonBook, query) -> dict:
    """Relevance of a land, seigniorial or census unit from its dating (the census year for census sets). Only a known dating that does
    not overlap the query's years excludes a unit; a house number outside the parsed ranges is reported, never excluding."""
    base = {"category": book.category}
    if book.section == "census":
        hn = book.house_numbers or HouseNumbers()
        base["house_number_covered"] = house_covered(hn, query.person.house_number)
    if query.year_from is None and query.year_to is None:
        return {**base, "status": "needs_review", "reason": "Zadání neurčuje období."}
    lo, hi = query.year_from or 0, query.year_to or 9999
    if book.dating.status == "known":
        if any((sp.start or 0) <= hi and (sp.end or 9999) >= lo for sp in book.dating.spans):
            return {**base, "status": "candidate", "reason": "Datace knihy se překrývá se zadaným obdobím."}
        return {**base, "status": "excluded", "reason": "Datace knihy se zadaným obdobím nepřekrývá."}
    return {**base, "status": "needs_review", "reason": "Datace chybí nebo je nejistá; knihu nelze bezpečně vyloučit."}


# ---------------------------------------------------------------- a parish page and its books

@dataclass
class BookRow:
    id: int
    number: str
    label: str | None                       # "register B", "index B" (without the brackets)
    types: list[tuple[str, str]]            # [("B", "1615-1630")]
    localities: list[str]
    other_localities: list[str]
    note: str


@dataclass
class ParishPage:
    id: int
    title: str
    district: str | None
    parish_district: list[str] = field(default_factory=list)
    other_localities: list[str] = field(default_factory=list)
    books: list[BookRow] = field(default_factory=list)
    not_online: list[tuple[str, str | None]] = field(default_factory=list)      # (number, first year online)


def _entities(cell) -> list[str]:
    names = [_text(n) for n in cell.select(".entita")]
    return [n for n in names if n] or [x.strip() for x in _text(cell).split(",") if x.strip()]


def _info_rows(table) -> dict[str, object]:
    rows = {}
    for tr in table.find_all("tr"):
        head, value = tr.find("td", class_="popis3b_nadpis"), tr.find("td", class_="popis3b")
        if head is not None and value is not None:
            rows.setdefault(normalize(_text(head)), value)
    return rows


def parse_book_row(tr) -> BookRow:
    cells = tr.find_all("td", recursive=False)
    if len(cells) != 7:
        raise _bad("Řádek knihy v přehledu farnosti má jiný počet sloupců, než Rubrika zná.", book=tr.get("id"))
    types = []
    for row in cells[3].select("table tr"):
        parts = row.find_all("td", recursive=False)
        if len(parts) != 2 or not _text(parts[0]):
            raise _bad("Tabulka let knihy v přehledu farnosti má neznámý tvar.", book=tr.get("id"))
        types.append((_text(parts[0]), _text(parts[1])))
    number, label = _text(cells[0]), _text(cells[1]).strip("[]").strip()
    kept, others = cells[4].select_one(".mat_vedena"), cells[4].select_one(".mat_nevedena")
    if not number or (kept is None and others is None) or cells[4].select_one(".zapisymk") is None:
        # a book may be kept for no locality and only mention some (then only `mat_nevedena` is there); an empty tag is falsy: test for None
        raise _bad("Řádek knihy nemá číslo nebo seznam lokalit („Book for“).", book=tr.get("id"))
    return BookRow(int(tr["id"]), number, label or None, types, _entities(kept) if kept is not None and _text(kept) else [],
                   _entities(others) if others is not None and _text(others) else [], _text(cells[5]))


def parse_parish(html: str, parish_id: int) -> ParishPage:
    soup = BeautifulSoup(html, "lxml")
    titles = soup.select("div.nadpis")
    tables = soup.select("table.tab_popis")
    if len(titles) != 1 or len(tables) != 1 or not _text(titles[0]):
        raise _bad("Stránka farnosti nemá očekávaný nadpis a popis.")
    ids = set(re.findall(rf"https://{re.escape(TREBON_HOST)}/(\d{{1,7}})(?![\d/])", tables[0].get_text(" ") + " ".join(
        b.get("data-clipboard-text", "") for b in tables[0].find_all(attrs={"data-clipboard-text": True}))))
    if ids != {str(parish_id)}:
        raise _bad("Stránka farnosti je jiná farnost, než se hledala, nebo nemá její odkaz.", expected=parish_id)
    info = _info_rows(tables[0])
    page = ParishPage(parish_id, _text(titles[0]), None)
    for key, cell in info.items():
        if key in ("district", "okres", "bezirk"):
            page.district = _text(cell) or None
        elif "parish district" in key or "farni obvod" in key or "pfarrbezirk" in key:
            page.parish_district = _entities(cell)
        elif any(w in key for w in ("other localities", "dalsi lokality", "ostatni lokality", "jine lokality", "weitere orte")):
            page.other_localities = _entities(cell)
    if not soup.select("td.m_k_n"):
        raise _bad("Stránka farnosti nemá tabulku knih.")
    seen = set()
    for tr in soup.find_all("tr", id=re.compile(r"^\d{1,7}$")):
        classes = tr.get("class") or []
        if tr["id"] in seen:
            raise _bad("Přehled farnosti uvádí jednu knihu dvakrát.", book=tr["id"])
        if "p_m_p" in classes:
            seen.add(tr["id"])
            page.books.append(parse_book_row(tr))
        elif "p_m_p0" in classes:
            seen.add(tr["id"])
            cells = tr.find_all("td", recursive=False)
            year = re.search(r"\b(\d{4})\b", tr.get("title", ""))
            page.not_online.append((_text(cells[0]) if cells else tr["id"], year[1] if year else None))
    if not seen:
        raise _bad("Tabulka knih farnosti nemá žádné řádky.")
    return page


_KIND = re.compile(r"(register|matrika|index|rejst[řr][ií]k)\b", re.I)


def coverage_of(label: str | None, types: list[tuple[str, str]], note: str | None = None) -> dict[str, EventCoverage]:
    """Years per event from the overview's table of types. A register has records for the listed types, an index has index
    years for them. An event the table does not list is `uncertain`, not absent (a book may hold more than it is listed for),
    so the catalogue alone never excludes a book on it. A note saying the book has an index („s indexem“) gives the listed events the same years
    as index years (never `absent`). A label or table the parser does not understand makes everything uncertain."""
    unclear = Periods(status="uncertain", raw=label or "")
    kind = _KIND.match(label or "")
    if not kind or not types or len({_LETTER_OF_EVENT.get(t) for t, _ in types}) != len(types) or any(t not in _LETTER_OF_EVENT for t, _ in types):
        return {e: EventCoverage(records=unclear, index=unclear) for e in EVENTS}
    is_index = kind[1].lower().startswith(("index", "rejst"))
    absent = Periods(status="absent", raw="")
    other = Periods(status="uncertain", raw="druh události kniha v přehledu neuvádí")
    years = {_LETTER_OF_EVENT[t]: parse_periods(y) for t, y in types}
    with_index = not is_index and "index" in normalize(note or "")
    return {e: EventCoverage(records=absent if is_index else years.get(e, other),
                             index=years.get(e, other) if is_index or with_index else absent) for e in EVENTS}


# ---------------------------------------------------------------- the viewer page

@dataclass
class Viewer:
    book_id: int
    scan: int
    total: int          # number of scans of the book


def parse_viewer(html: str, book_id: int, scan: int) -> Viewer:
    """The viewer page of one scan, read only for the number of scans of the book (and to check it is that book and scan)."""
    soup = BeautifulSoup(html, "lxml")
    pushed = re.findall(r'pushState\(null,\s*null,\s*"/(\d{1,7})/(\d{1,5})"\)', html)
    select = soup.find("select", id="page")
    hidden = {i.get("value") for i in soup.find_all("input", attrs={"type": "hidden", "name": "id"})}
    if pushed != [(str(book_id), str(scan))] or select is None or hidden != {str(book_id)}:
        raise _bad("Prohlížeč DigiArchivu nemá očekávanou strukturu (kniha, číslo snímku nebo seznam snímků).", book=book_id, scan=scan)
    options = select.find_all("option")
    values = [o.get("value", "") for o in options]
    selected = [o.get("value") for o in options if o.has_attr("selected")]
    if values != [str(i) for i in range(1, len(values) + 1)] or selected != [str(scan)]:
        raise _bad("Seznam snímků knihy v prohlížeči DigiArchivu má neočekávaný tvar.", book=book_id, scan=scan)
    return Viewer(book_id, scan, len(values))


# ---------------------------------------------------------------- catalogue client

class TrebonCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    async def _page(self, sid, owner, url: str) -> str:
        body = await self.source.fetch(sid, owner, url, kind="html")
        return body.decode("utf-8-sig", errors="replace")

    async def places(self, sid, owner, term: str, kind: str = "parish_registers",
                      district: str | None = None) -> tuple[list[Place], list[str]]:
        """Parishes and congregations whose names begin with the words of the term (the tree has no search that robots.txt allows);
        for land records and census the municipalities and estates found by walking the branch."""
        if not _tokens(term):
            raise ResearchError("INVALID_ARGUMENT", "Zadejte název farnosti." if kind == "parish_registers" else "Zadejte název obce.")
        if kind != "parish_registers":
            return await self._section_places(sid, owner, term, kind, district)
        warnings = ["Farnosti SOA Třeboň se hledají podle začátku názvu v abecedním seznamu farností (vyhledávání DigiArchivu robots.txt "
                    "zakazuje a Rubrika ho nepoužívá). Obec, která není sídlem farnosti, tak nenajdete: zadejte farnost a obec poznáte "
                    "ve výpisu knih podle sloupce localities."]
        letters = parse_letters(await self._page(sid, owner, letters_url()))
        letter = pick_letter(letters, term)
        found: list[ParishRef] = []
        if letter is None:
            warnings.append("Seznam římskokatolických farností nemá písmeno, kterým název začíná. Nejde o důkaz, že farnost v archivu není.")
        else:
            found = match_parishes(parse_letter(await self._page(sid, owner, letter_url(letter.code)), letter), term)
            if not found:
                warnings.append(f"Mezi římskokatolickými farnostmi na písmeno {letter.label} žádná nezačíná zadaným názvem. Nejde o důkaz, "
                                "že farnost nebo obec v archivu není; může být v jiné farnosti.")
        others, skipped = [], []
        for root, confession in CONFESSIONS:
            try:
                others += match_parishes(parse_confession_root(await self._page(sid, owner, letter_url(root)), root, confession), term)
            except ResearchError as e:
                if not node_error_is_local(e):
                    raise
                skipped.append(confession)
        if skipped:
            warnings.append("Nepodařilo se přečíst větve matrik: " + ", ".join(skipped) + ". Jejich farnosti a úřady v tomto výsledku chybějí; "
                            "nejde o důkaz, že v nich nic není.")
        warnings.append("Nekatolické farnosti (evangelické, husitské, pravoslavné) a civilní matriky se hledají podle názvu místa, kde sbor nebo úřad sídlí; "
                        "v označení místa je vždy církev nebo druh matrik. Židovské matriky DigiArchiv nevede (viz Národní archiv).")
        found = found + others
        if len(found) > MAX_PLACES:
            warnings.append(f"Nalezeno {len(found)} farností a úřadů; zobrazeno prvních {MAX_PLACES}. Upřesněte název.")
        if len({(p.base, p.confession) for p in found}) < len(found):
            warnings.append("Město má víc farností (liší se patronem v závorce). Městské farnosti se dělí podle čtvrtí a ulic, proto zkontrolujte "
                            "všechny, ne jen první.")
        places = []
        for parish in found[:MAX_PLACES]:
            place = Place(source_id=f"trebon:place:{parish.id}", label=parish.label,
                          names=list(dict.fromkeys([parish.base, *re.findall(r"\(([^)]+)\)", parish.name)])),
                          source_url=parish_url(parish.tree, parish.id))
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.put_entity("trebon_place", place.source_id, {"tree": parish.tree, "id": parish.id, "name": parish.name,
                                                                   "section": "parish_registers", "confession": parish.confession})
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
            places.append(place)
        return places, warnings

    def _cached(self, url: str) -> bool:
        """Whether the page is already in the cache (fetching it then costs no request)."""
        return self.source.cache_get(url, self.source.cfg.metadata_ttl) is not None

    async def _section_places(self, sid, owner, term: str, kind: str, district: str | None) -> tuple[list[Place], list[str]]:
        """Walk the branches of `kind` (land: land registers, seigniorial registers, urbariums; census) best first. Pages already in the
        cache are free, so a repeated call continues where the last one stopped; at most MAX_WALK_REQUESTS new pages are fetched per call.
        A letter is picked by the term's first letter; nodes named like the term or the district are walked first, the others only while
        the budget lasts. Whatever is left unwalked is named in a warning (never a proof that the place has nothing)."""
        used, steps, pending_left, hits, warnings, skipped = 0, 0, [], [], [], []
        seen_hits: set[int] = set()
        roots = SECTIONS[kind]
        for index, (root, category, section_label) in enumerate(roots):
            frontier = [(0, 0, root, (section_label,))]
            root_hits = len(hits)
            while frontier:
                frontier.sort(key=lambda f: (f[0], -f[1]))
                url = letter_url(frontier[0][2])
                cached = self._cached(url)
                if len(hits) > root_hits and frontier[0][0] > 0 and not cached:
                    # A place was found in this tree: its other branches are fetched only when named like the term or the district.
                    skipped += [" › ".join(f[3]) for f in frontier if f[0] > 0]
                    frontier = [f for f in frontier if f[0] == 0]
                    continue
                if steps >= MAX_WALK_STEPS or (not cached and used >= MAX_WALK_REQUESTS):
                    pending_left += [" › ".join(f[3]) for f in frontier]
                    pending_left += [f"{r[2]} (větev nezačata)" for r in roots[index + 1:]]
                    break
                score, depth, code, path = frontier.pop(0)
                steps += 1
                used += 0 if cached else 1
                try:
                    groups, entities = parse_section_node(await self._page(sid, owner, url), code)
                except ResearchError as e:
                    if not node_error_is_local(e):
                        raise
                    warnings.append(f"Větev {' › '.join(path)} se nepodařilo přečíst ({e.code}); její obce v tomto výsledku chybějí.")
                    continue
                for ref in match_parishes([ParishRef(e.id, e.code, e.label) for e in entities], term):
                    if ref.id not in seen_hits:
                        seen_hits.add(ref.id)
                        hits.append((ref, path, category, section_label))
                letters = _letter_of(groups)
                if letters:
                    chosen = pick_letter(letters, term)
                    if chosen is None:
                        warnings.append(f"Ve větvi {' › '.join(path)} není písmeno, kterým název začíná.")
                    else:
                        frontier.append((0, depth + 1, chosen.code, path))
                    groups = [g for g in groups if not _LETTER_LABEL.fullmatch(g.label)]
                frontier += [(group_score(g.label, term, district), depth + 1, g.code, (*path, g.label)) for g in groups]
            else:
                continue
            break
        if pending_left:
            warnings.append(f"Strom DigiArchivu má víc větví, než se vejde do jednoho hledání ({MAX_WALK_REQUESTS} nových stránek); neprohledáno: "
                            + "; ".join(pending_left[:8]) + (f" a dalších {len(pending_left) - 8}" if len(pending_left) > 8 else "")
                            + ". Zopakujte hledání (načtené stránky se berou z mezipaměti a hledání pokračuje dál), zadejte okres (district) "
                            "nebo přesnější název; nejde o důkaz, že tam obec není.")
        if skipped:
            warnings.append("Místo se našlo, proto se další větve bez shody s názvem nebo okresem nenačítaly: "
                            + "; ".join(skipped[:8]) + (f" a dalších {len(skipped) - 8}" if len(skipped) > 8 else "")
                            + ". Hledáte-li jinou obec stejného jména, zadejte okres (district).")
        warnings.append("Obce se v DigiArchivu hledají podle názvu uzlu stromu (obec, panství, statek); obec, která je jen mezi lokalitami "
                        "fondu (např. vsi panství), tak nenajdete: zkuste název panství nebo města, k němuž patřila.")
        if len(hits) > MAX_PLACES:
            warnings.append(f"Nalezeno {len(hits)} míst; zobrazeno prvních {MAX_PLACES}. Upřesněte název nebo zadejte okres.")
        places = []
        for ref, path, category, section_label in hits[:MAX_PLACES]:
            trail = " › ".join(x for x in path if x != section_label)
            year = next((int(x) for x in path if _YEAR_LABEL.fullmatch(x)), None)
            pid = place_id_of(kind, ref.id)
            known = self.store.entity("trebon_place", pid)
            if known and known.get("tree") != ref.tree:
                warnings.append(f"Id {ref.id} (uzel {ref.tree}) je v DigiArchivu už použito pro jiný uzel ({known.get('tree')}); uzel {ref.tree} vynechán.")
                continue
            place = Place(source_id=pid, label=f"{ref.name} ({section_label}" + (f"; {trail}" if trail else "") + ")",
                          names=[ref.name], source_url=parish_url(ref.tree, ref.id))
            self.store.put_entity("place", pid, place.model_dump())
            self.store.put_entity("trebon_place", pid, {"tree": ref.tree, "id": ref.id, "name": ref.name, "section": kind,
                                                       "category": category, "path": list(path), "census_year": year})
            self.store.link(sid, owner, "place_option", pid, place.model_dump())
            places.append(place)
        return places, warnings

    def _book(self, row: BookRow, page: ParishPage, denomination: str | None = None) -> TrebonBook:
        book = TrebonBook(source_id=f"trebon:book:{row.id}", number=row.number, creator=page.title, parish=page.title, parish_id=page.id,
                          register_label=row.label, coverage=coverage_of(row.label, row.types, row.note), localities=row.localities,
                          other_localities=row.other_localities, note=row.note or None, detail_url=detail_url(row.id), denomination=denomination,
                          raw_notes="; ".join(f"{t} {y}" for t, y in row.types)[:400])
        return book.model_copy(update={"citation": citation_of(book)})

    async def books(self, sid, owner, place_id: str) -> tuple[list[TrebonBook], ParishPage, list[str]]:
        """All digitised books of a parish from its overview page, and the parish page itself (district, parish district)."""
        info = self.store.entity("trebon_place", place_id)
        parish_id = parish_id_of(place_id)
        if (not info or info.get("id") != parish_id or info.get("section", "parish_registers") != "parish_registers"
                or not (_PARISH_TREE.fullmatch(info.get("tree", "")) or _ENTITY_TREE.fullmatch(info.get("tree", "")))):
            raise ResearchError("NOT_FOUND", "Farnost SOA Třeboň není známá; nejprve použijte find_place.")
        page = parse_parish(await self._page(sid, owner, parish_url(info["tree"], parish_id)), parish_id)
        warnings = []
        if page.not_online:
            years = sorted({y for _, y in page.not_online if y})
            warnings.append(f"{len(page.not_online)} knih farnosti archiv ještě nezveřejnil"
                            + (f" (online mají být od roku {years[0]}{'' if len(years) == 1 else ' až ' + years[-1]})" if years else "")
                            + "; ve výpisu nejsou.")
        books = [self._book(row, page, info.get("confession")) for row in page.books]
        for book in books:
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return books, page, warnings

    def _unit(self, row: UnitRow, page: UnitPage, info: dict) -> TrebonBook:
        c = row.cells
        kind = info["section"]
        title = c.get("col_nazev") or c.get("col_obec")
        storage = c.get("col_ukladaci_jednotka") or None
        number = c.get("col_signatura") or c.get("col_inventarni_cislo") or storage or c.get("col_poradove_cislo") or str(row.id)
        used = ("col_nazev", "col_signatura", "col_inventarni_cislo", "col_ukladaci_jednotka", "col_poradove_cislo", "col_datace_od", "col_zapisy_pro")
        extra = [f"{row.headers.get(col) or col}: {text}" for col, text in c.items() if text and col not in used]
        # a census set is dated by the row when it says so; the year of the page and of the tree only fill a missing dating,
        # and the dating is uncertain (never excluding) when they disagree with the row or with each other
        years = sorted({y for y in ((page.year, info.get("census_year")) if kind == "census" else ()) if y})
        year = page.year or info.get("census_year") if kind == "census" else info.get("census_year")
        dating = parse_row_dating(c.get("col_datace_od"))
        if kind == "census" and years:
            if dating.status == "known":
                if not all(any((sp.start or 0) <= y <= (sp.end or 9999) for sp in dating.spans) for y in years):
                    dating = Periods(status="uncertain", spans=dating.spans, raw=dating.raw)
            else:
                dating = Periods(status="known" if len(years) == 1 else "uncertain", spans=[YearSpan(start=y, end=y) for y in years],
                                 raw=c.get("col_datace_od") or ", ".join(map(str, years)))
        # house numbers only from the title (Obsah) and the census municipality, never from signatures or storage units
        house = parse_description_house_numbers(" ".join(x for x in (c.get("col_nazev"), c.get("col_obec")) if x)) if kind == "census" else None
        book = TrebonBook(source_id=f"trebon:book:{row.id}", number=number, creator=page.fund_name or page.title, parish=info["name"],
                          parish_id=page.id, coverage={}, detail_url=detail_url(row.id), section=kind,
                          title=title, fund_name=page.fund_name, storage=storage, category=info.get("category", "other") if kind == "land_records" else "other",
                          dating=dating, census_year=year, house_numbers=house, tree_path=info.get("path", []), note="; ".join(extra)[:400] or None,
                          raw_notes=(c.get("col_datace_od") or "")[:400])
        return book.model_copy(update={"citation": citation_of(book)})

    async def units(self, sid, owner, place_id: str) -> tuple[list[TrebonBook], UnitPage, list[str]]:
        """The books of one fund, estate or municipality from its page (land records, seigniorial registers, urbariums, census)."""
        info = self.store.entity("trebon_place", place_id)
        entity_id = parish_id_of(place_id)
        if not info or info.get("id") != entity_id or info.get("section") not in ("land_records", "census") \
                or info.get("section") != kind_of_place(place_id) or not _SECTION_ENTITY.fullmatch(info.get("tree", "")):
            raise ResearchError("NOT_FOUND", "Místo SOA Třeboň není známé; nejprve použijte find_place.")
        page = parse_unit_page(await self._page(sid, owner, parish_url(info["tree"], entity_id)), entity_id)
        warnings = []
        if page.not_online:
            warnings.append(f"{page.not_online} knih {'fondu' if info['section'] == 'land_records' else 'obce'} nemá v DigiArchivu odkaz na snímky "
                            "(zřejmě nejsou zveřejněny); ve výpisu nejsou.")
        books = [self._unit(row, page, info) for row in page.rows]
        for book in books:
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return books, page, warnings

    # -- pages
    def shell_page(self, book: TrebonBook, n: int) -> Page:
        """A scan's page: the viewer address is built from the book and scan number (no request); the scan must be supplied by the user."""
        book_id, total = book_id_of(book.source_id), book.image_count
        if not total or not 1 <= n <= total:
            raise ResearchError("NOT_FOUND", "Snímek leží mimo rozsah knihy.")
        link = viewer_url(book_id, n)
        return Page(page_id=f"{book.source_id}:{n}", book_id=book.source_id, scan_number=n, image_key=f"scan-{n}", source_url=link,
                    viewer_url=link, source_type="manual", manifest_sha256=manifest_hash(book_id, total), source="trebon",
                    citation=book.citation or citation_of(book), archive="trebon")

    async def _viewer(self, sid, owner, book_id: int, n: int) -> Viewer:
        url = viewer_url(book_id, n)
        body = await self.source.fetch(sid, owner, url, kind="html")
        try:
            return parse_viewer(body.decode("utf-8-sig", errors="replace"), book_id, n)
        except ResearchError:
            self.source.invalidate(url)
            raise

    async def open_register(self, sid, owner, book: TrebonBook) -> tuple[TrebonBook, list[str]]:
        """The book with its number of scans, read from the viewer page of the first scan (one request per book). The scans
        themselves are never fetched: their pages only carry the address of the archive's viewer."""
        viewer = await self._viewer(sid, owner, book_id_of(book.source_id), 1)
        opened = book.model_copy(update={"image_count": viewer.total})
        return opened.model_copy(update={"citation": opened.citation or citation_of(opened)}), []
