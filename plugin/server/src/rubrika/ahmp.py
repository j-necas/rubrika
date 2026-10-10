"""Prague City Archives (AHMP): parish registers of the Sbírka matrik through the shared Bach adapter.

The catalogue is a tree (katolické → obvody → okresy → farnosti → knihy) with no search API. Parishes are found by
walking the tree lazily and remembering every node in the user's own cache; a book's scans are resolved one
permalink request at a time, only when a scan is actually opened."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .bach import AHMP_SITE, BachSite, BookRow, manifest_hash, parse_viewer, walk_node
from .catalog import normalize, parse_periods, relevance
from .errors import ResearchError
from .models import Book, EventCoverage, HouseNumbers, Model, Page, Periods, Place, SearchQuery
from .soupis import (FlatList, SearchBudget, collect, given_key, house_of, loose, lower_bound, parse_name, parse_soupis_label,
                     same_surname, search_targets, surname_before)
from .soupis import given_forms, in_table, same_given, surname_key as surname_key_of

PREFIX = "ahmp:"
ROOT_ID = 108399                  # Sbírka matrik › katolické (labels of its parishes start at the district)
# The other children of the Sbírka matrik (node 108341): non-Catholic churches (ČCE, Czechoslovak, Orthodox, Old Catholic,
# Greek Catholic), civil registers of the Magistrate and the district offices, and the death protocols. Their labels start
# with the name given here, because the catalogue groups them by church or office, not by district.
EXTRA_ROOTS = {111341: "Nekatolické matriky", 111659: "Civilní matriky Magistrátu", 108342: "Úmrtní protokoly"}
CATHOLIC_BRANCH = "Katolické matriky"
MAX_LIST_PAGES = 40               # pages of one node list per call (9 rows each)
TREE_REQUESTS_PER_CALL = 40       # HTTP requests (opener included) one find_place may spend on loading the parish tree
MIN_NODE_REQUESTS = 3
MAX_PLACES = 25
CITATION = "Archiv hlavního města Prahy, Sbírka matrik, sign. "

# Soupis pražského obyvatelstva 1830–1910 (1920): two flat lists under node 168667, sorted by surname.
SOUPIS_TITLE = "Soupis pražského obyvatelstva 1830–1910"
SOUPIS_SERIES = {168668: ("Pražští příslušníci", "domovská příslušnost Praha"),
                 290432: ("Cizí příslušníci", "domovská příslušnost mimo Prahu")}
# Všeobecné sčítání lidu (fond 61): … › Spisy › year › district; a district lists one row per house number.
CENSUS_TITLE = "Všeobecné sčítání lidu"
CENSUS_ROOT_ID = 514342
CENSUS_YEARS = frozenset({"1869", "1880", "1890"})       # the years the archive has put online
SOUPIS_PARENT_ID = 168667
MAX_REQUESTS_PER_CALL = 35        # every HTTP request of one list_sources call on a flat list: opener, node page, jumps, pages
YEAR_BAND = 5                     # birth years collected around the years of the research; ±2 of them are candidates
MAX_SHEETS = 90                   # rows of one surname listed per call (ten pages of nine)
MAX_COLLECT_PAGES = 10
MAX_HOUSES = 20
_GENERIC_PRAGUE = frozenset({"praha", "prahy", "prag", "prague"})
_STOP = frozenset({"kostel", "kostela", "farnost", "fara", "fary", "u", "v", "ve", "na", "z", "do"})
_SAINT = frozenset({"svaty", "svateho", "svatemu", "svatym", "svata", "svate", "svatou", "svatych"})
_ROMAN = {str(i): r for i, r in enumerate("i ii iii iv v vi vii viii ix x xi xii xiii xiv xv".split(), 1)}
_EVENTS = {"N": "birth", "O": "marriage", "Z": "death"}
NODE_ID = re.compile(r"ahmp:node:(\d{1,9})")
BOOK_ID = re.compile(r"ahmp:book:([0-9A-F]{32})")
PAGE_ID = re.compile(r"(ahmp:book:[0-9A-F]{32}):([1-9]\d{0,4})")


class SoupisSheet(Book):
    """One conscription sheet of the Soupis: a person (or a family head) with one scan, rarely two."""
    series: str
    order_no: int | None = None       # "Pořadové číslo"
    surname: str | None = None
    given_name: str | None = None
    birth_year: int | None = None
    box: str | None = None            # "karton/205"; read from the sheet's own page when it is opened


class CensusHouse(Book):
    """One house of one census district: its household sheets are the scans."""
    census_year: int | None = None
    district: str
    house_number: int | None = None
    house_suffix: str = ""
    house_numbers: HouseNumbers = HouseNumbers()


def is_ahmp_id(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def node_id_of(place_id: str) -> int:
    m = NODE_ID.fullmatch(place_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Místa AHMP musí mít ID ve tvaru ahmp:node:<číslo> (z find_place).")
    return int(m[1])


def xid_of(book_id: str) -> str:
    m = BOOK_ID.fullmatch(book_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Knihu AHMP otevřete podle ID ve tvaru ahmp:book:<xid> z list_sources.")
    return m[1]


def tokens(text: str) -> list[str]:
    out = []
    for raw in re.findall(r"[a-z0-9]+", normalize(text)):
        token = "sv" if raw in _SAINT else _ROMAN.get(raw, raw)
        out.append(token)
    return out


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def word_matches(term: str, word: str) -> bool:
    """Equal, or one is the other's inflection (Jindřich / Jindřicha, Nové Město / Nového Města); short words must be equal."""
    if term == word:
        return True
    if min(len(term), len(word)) < 4:
        return False
    common = _common_prefix(term, word)
    # A shared stem of at least four letters and only a short ending left on either side (not Testa / Testovice).
    return common >= 4 and len(term) - common <= 3 and len(word) - common <= 3


@dataclass(frozen=True)
class Parish:
    id: int
    name: str
    path: tuple[str, ...]       # names from below "katolické" down to the parish itself
    count: int | None

    @property
    def label(self) -> str:
        # The generic "vnitřní/vnější obvody" container is dropped only when a named district or place precedes the
        # parish; a parish directly below it keeps it, so that every label shows where the parish sits.
        shown = [n for n in self.path if not re.fullmatch(r"(?:vnitřní|vnější) obvody", n)] if len(self.path) > 2 else self.path
        return " › ".join(shown)


def match_score(term_tokens: list[str], parish: Parish) -> int | None:
    """Every term word must occur in the parish name or in the names above it; own name counts double."""
    own = tokens(parish.name)
    above = [t for name in parish.path[:-1] for t in tokens(name)]
    score = 0
    for t in term_tokens:
        if any(word_matches(t, w) for w in own):
            score += 2
        elif any(word_matches(t, w) for w in above):
            score += 1
        else:
            return None
    return score


def branch_of(path: tuple[str, ...]) -> str:
    """The branch of the Sbírka matrik a node path belongs to: one of the EXTRA_ROOTS labels, otherwise the Catholic tree."""
    return path[0] if path and path[0] in EXTRA_ROOTS.values() else CATHOLIC_BRANCH


def coverage_of(signature: str, years: str | None) -> dict[str, EventCoverage]:
    """Event kinds follow the signature (N births, O marriages, Z deaths, i index); years come from the title."""
    m = re.search(r"(?:^|\s)(?P<kind>[NOZ]{1,3}|i)\s*\d+[A-Za-z]*$", signature)
    combined = re.search(r"(?:^|\s)(?P<all>(?:[NOZ]\d+[a-z]?){2,})$", signature)      # "N1O1Z1": births, marriages and deaths in one book
    if m is None and combined:
        m = {"kind": "".join(dict.fromkeys(re.findall(r"[NOZ]", combined["all"])))}
    span = parse_periods(years) if years else Periods(status="uncertain", raw="")
    absent = Periods(status="absent", raw="")
    # What the signature does not name is not proof of absence (combined or mislabelled books): keep it for review.
    other = Periods(status="uncertain", raw="podle signatury")
    unclear = Periods(status="uncertain", raw=years or "")
    if m is None and re.match(r"UP\b", signature):
        # Death protocols: only deaths are named, the rest is unknown; "IZ" may be an index, so no index is ruled out.
        return {e: EventCoverage(records=span if e == "death" else other,
                                 index=Periods(status="uncertain", spans=span.spans, raw=years or "") if e == "death" else other)
                for e in _EVENTS.values()}
    if not m:
        return {e: EventCoverage(records=unclear, index=unclear) for e in _EVENTS.values()}
    kind = m["kind"]
    if kind == "i":
        # An index of unknown scope: never used to exclude a book, only to keep it for review.
        return {e: EventCoverage(records=absent, index=Periods(status="uncertain", spans=span.spans, raw=years or ""))
                for e in _EVENTS.values()}
    return {e: EventCoverage(records=span if letter in kind else other, index=absent if letter in kind else other)
            for letter, e in _EVENTS.items()}


def citation_of(book: Book) -> str:
    if isinstance(book, SoupisSheet):
        who = ", ".join(x for x in (book.surname, book.given_name) if x) + (f" (*{book.birth_year})" if book.birth_year else "")
        return ", ".join(x for x in ("Archiv hlavního města Prahy", SOUPIS_TITLE, book.series,
                                     book.box and book.box.replace("/", " "), who or book.number) if x)
    if isinstance(book, CensusHouse):
        return f"Archiv hlavního města Prahy, {CENSUS_TITLE} {book.census_year or ''}, {book.district}, čp. {book.house_number}{book.house_suffix}".replace("  ", " ")
    return CITATION + book.number


def unit_of(value: dict) -> Book:
    """A stored AHMP unit as the model it was saved from (register book, Soupis sheet or census house)."""
    if "series" in value:
        return SoupisSheet.model_validate(value)
    if "district" in value:
        return CensusHouse.model_validate(value)
    return Book.model_validate(value)


def unit_relevance(unit: Book, query: SearchQuery) -> dict:
    if isinstance(unit, SoupisSheet):
        return soupis_relevance(unit, query)
    if isinstance(unit, CensusHouse):
        return {"status": "candidate" if unit.house_numbers.parsed and _covers(unit, query) else "needs_review",
                "reason": "Číslo popisné v seznamu domů odpovídá zadání." if _covers(unit, query)
                else "Dům z vybraného obvodu a roku; číslo popisné zadání nepotvrzuje."}
    return relevance(unit, query)


def _covers(unit: CensusHouse, query: SearchQuery) -> bool:
    from .census import house_covered
    return house_covered(unit.house_numbers, query.person.house_number) is True


# How a person-level judgement speaks of the record it judges: (subject, "on the sheet") of the Soupis, of a registration form.
SOUPIS_WORDING = ("List", "na listu")


def person_relevance(given_name: str | None, birth_year: int | None, query: SearchQuery, wording: tuple[str, str] = SOUPIS_WORDING) -> dict:
    """Given name (tolerant: case, diacritics, Josef/Joseph and the other Czech↔German forms of soupis.GIVEN_EQUIVALENTS) and
    birth year (±2 around the years of a birth request is a candidate, up to ±5 is listed for review) of one person against the
    request. Missing data never excludes; both known and both wrong does."""
    subject, on = wording
    wanted = query.person.given_name
    given = None
    if wanted and given_name:
        given = same_given(wanted, given_name)
    year, gap, low, high = None, 0, None, None
    if birth_year and query.event_type == "birth" and (query.year_from or query.year_to):
        low, high = query.year_from or query.year_to, query.year_to or query.year_from
        gap = max(low - birth_year, birth_year - high, 0)
        year = gap <= 2
    near = year is False and gap <= YEAR_BAND
    if (year and given is not False) or (given and year is not False):
        reasons = [t for t, ok in (("křestní jméno odpovídá", given),
                                   (f"rok narození {birth_year} leží v zadání {low}–{high} (±2)", year)) if ok]
        return {"status": "candidate", "reason": f"{subject} souhlasí: " + ", ".join(reasons) + "."}
    if given is False and year is False and not near:
        return {"status": "excluded", "reason": f"Křestní jméno i rok narození {on} neodpovídají zadání."}
    known = []
    if given is False:
        known.append("křestní jméno se liší od zadání a není v tabulce českých a německých tvarů")
    if near:
        known.append(f"rok narození {birth_year} je mimo zadání {low}–{high} (±2), ale v prohledaném pásmu ±{YEAR_BAND}")
    elif year is False:
        known.append("rok narození je mimo zadání")
    return {"status": "needs_review", "reason": ("; ".join(known) + "." if known else
            f"Příjmení odpovídá; křestní jméno nebo rok narození v zadání či {on} chybí.").capitalize()}


def soupis_relevance(sheet: SoupisSheet, query: SearchQuery) -> dict:
    return person_relevance(sheet.given_name, sheet.birth_year, query)


def is_soupis_node(node_id: int) -> bool:
    return node_id in SOUPIS_SERIES


def sort_key(number: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", number)]


class AhmpCatalog:
    def __init__(self, source, store, site: BachSite = AHMP_SITE):
        self.source, self.store, self.site = source, store, site

    # ------------------------------------------------------------------ tree
    def _node(self, node_id: int) -> dict | None:
        return self.store.entity("ahmp_node", str(node_id), self.source.cfg.metadata_ttl)

    def index(self, root: int | None = None, top: frozenset | None = None) -> tuple[list[Parish], list[tuple[int, tuple[str, ...]]]]:
        """Parishes known from cached nodes, and the nodes still to be fetched (with the path above them). `top` limits
        the names accepted directly below the root (the census years)."""
        parishes, pending = [], []

        def visit(node_id: int, path: tuple[str, ...]):
            node = self._node(node_id)
            if node is None:
                pending.append((node_id, path))
                return
            if node.get("books") and path:
                parishes.append(Parish(node_id, node["name"], path, node["books"]))
            for child in node["children"]:
                if top is not None and not path and child["name"] not in top:
                    continue
                below = path + (child["name"],)
                if child["expandable"]:
                    visit(child["id"], below)
                elif child["count"] != 0:
                    parishes.append(Parish(child["id"], child["name"], below, child["count"]))

        if root is None:
            visit(ROOT_ID, ())
            for node_id, label in EXTRA_ROOTS.items():
                visit(node_id, (label,))
        else:
            visit(root, ())
        return parishes, pending

    def index_ready(self, kind: str = "parish_registers") -> bool:
        return not (self.index(CENSUS_ROOT_ID, CENSUS_YEARS) if kind == "census" else self.index())[1]

    async def _crawl(self, sid, owner, term_tokens, budget, root: int | None = None, top: frozenset | None = None) -> tuple[list[Parish], bool]:
        """Fetch missing tree nodes (matching branches first) within `budget` HTTP requests of this call, opener and
        retries included; every complete node is kept as it arrives. A node is not started with fewer than
        MIN_NODE_REQUESTS left unless it is the first of the call (a paged node needs several pages)."""
        session = self.site.session(sid, owner)
        while True:
            parishes, pending = self.index(root, top)
            remaining = budget - session.requests
            if not pending or remaining < 1 or (session.requests and remaining < MIN_NODE_REQUESTS):
                return parishes, not pending
            wanted = [p for p in pending if any(word_matches(t, w) for t in term_tokens for n in p[1] for w in tokens(n))]
            # The small non-Catholic and civil branches go before the rest of the big Catholic tree, so that one search reaches them.
            node_id, _ = (wanted or sorted(pending, key=lambda p: branch_of(p[1]) == CATHOLIC_BRANCH))[0]
            node = await walk_node(self.source, self.site, session, node_id, remaining)
            if not node.complete:
                return self.index(root, top)[0], False
            self.store.put_entity("ahmp_node", str(node_id), {
                "id": node_id, "name": node.name, "fetched": time.time(), "books": len(node.books),
                "children": [{"id": c.id, "name": c.name, "count": c.count, "expandable": c.expandable} for c in node.children]})

    async def places(self, sid, owner, term: str) -> tuple[list[Place], list[str]]:
        term_tokens = [t for t in tokens(term) if t not in _STOP]
        if not term_tokens:
            raise ResearchError("INVALID_ARGUMENT", "Zadejte název farnosti, kostela nebo pražské čtvrti.")
        parishes, complete = await self._crawl(sid, owner, term_tokens, TREE_REQUESTS_PER_CALL)
        scored = [(s, p) for p in parishes if (s := match_score(term_tokens, p)) is not None]
        scored.sort(key=lambda sp: (-sp[0], branch_of(sp[1].path) != CATHOLIC_BRANCH, sp[1].label))      # Catholic parishes first among equals
        warnings = []
        if not complete:
            open_branches = list(dict.fromkeys(branch_of(path) for _, path in self.index()[1]))
            warnings.append("Strom farností AHMP se načítá postupně; tento výběr je neúplný (catalog_exhaustive=false). "
                            f"Dosud nedočtené větve Sbírky matrik: {', '.join(open_branches)}. "
                            "Zopakujte hledání, načtené větve zůstávají v paměti a strom se dočte, nebo hledání zúžte "
                            "názvem čtvrti, kostela, církve nebo úřadu.")
        if len(scored) > MAX_PLACES:
            warnings.append(f"Nalezeno {len(scored)} farností; zobrazeno prvních {MAX_PLACES}. Upřesněte název (např. doplňte čtvrť).")
        places = []
        for _, parish in scored[:MAX_PLACES]:
            base = re.sub(r"\s*\([^)]*\)", "", parish.name).strip() or parish.name
            if branch_of(parish.path) != CATHOLIC_BRANCH:
                base = parish.name            # a church or office keeps its full name, e.g. "ČCE (Staré Město) u sv. Salvátora (EVS)"
            place = Place(source_id=f"ahmp:node:{parish.id}", label=parish.label,
                          names=list(dict.fromkeys([base, *re.findall(r"\(([^)]+)\)", parish.name)])),
                          source_url=self.site.node_url(parish.id))
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
            places.append(place)
        return places, warnings

    # ------------------------------------------------------------------ Soupis and census
    def soupis_places(self) -> list[Place]:
        places = []
        for node_id, (name, holds) in SOUPIS_SERIES.items():
            place = Place(source_id=f"ahmp:node:{node_id}", label=f"{SOUPIS_TITLE} › {name} ({holds})",
                          names=[SOUPIS_TITLE, name], source_url=self.site.node_url(node_id))
            self.store.put_entity("place", place.source_id, place.model_dump())
            places.append(place)
        return places

    async def census_places(self, sid, owner, term: str, houses_first: bool = False) -> tuple[list[Place], list[str]]:
        """The two Soupis lists (always) and the census districts of the years online that match the term. `houses_first`
        (the research has a house number and no surname) puts the districts first and drops the search-by-surname hint."""
        places = self.soupis_places()
        soupis = list(places)
        warnings = [] if houses_first else ["Soupis pražského obyvatelstva má dvě řady: Pražští příslušníci (domovská příslušnost Praha) a Cizí příslušníci "
                    "(domovská příslušnost jinde); nevíte-li, prohledejte obě. Hledá se podle příjmení z revise_research (person.surname)."]
        term_tokens = [t for t in tokens(term) if t not in _STOP and t not in _GENERIC_PRAGUE]
        parishes, complete = await self._crawl(sid, owner, term_tokens, TREE_REQUESTS_PER_CALL, CENSUS_ROOT_ID, CENSUS_YEARS)
        scored = [(s, p) for p in parishes if (s := match_score(term_tokens, p) if term_tokens else 0) is not None]
        scored.sort(key=lambda sp: (-sp[0], sp[1].label))
        if not complete:
            warnings.append("Strom sčítání AHMP se načítá postupně; výběr obvodů je neúplný (catalog_exhaustive=false). "
                            "Zopakujte hledání, načtené větve zůstávají v paměti.")
        if len(scored) > MAX_PLACES:
            warnings.append(f"Nalezeno {len(scored)} obvodů; zobrazeno prvních {MAX_PLACES}. Upřesněte název obvodu nebo rok.")
        for _, parish in scored[:MAX_PLACES]:
            year = parish.path[0]
            place = Place(source_id=f"ahmp:node:{parish.id}", label=f"{CENSUS_TITLE} {year} › {' › '.join(parish.path[1:])}",
                          names=[parish.name, year], source_url=self.site.node_url(parish.id))
            self.store.put_entity("place", place.source_id, place.model_dump())
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
            places.append(place)
        for place in soupis:
            self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
        if houses_first:
            places = [p for p in places if p not in soupis] + soupis
            warnings.insert(0, "Sčítání lidu AHMP se hledá podle čísla popisného (person.house_number) v obvodu a roce; Soupis pražského "
                               "obyvatelstva by potřeboval příjmení (person.surname).")
        return places, warnings

    async def _flat_run(self, sid, owner, node_id, work, spent_result):
        """Run `work(flat)` in one site session with ONE request budget for the whole call (opener included); an expired session
        (one-time tokens) restarts the run once, on the same budget. Rows other than register books are accepted only below the
        Soupis and census nodes."""
        session = self.site.session(sid, owner)
        limit = session.requests + MAX_REQUESTS_PER_CALL
        allow = {SOUPIS_PARENT_ID: 10120, CENSUS_ROOT_ID: 10125}
        for attempt in (0, 1):
            flat = FlatList(self.source, self.site, session, node_id, limit, allow)
            try:
                try:
                    await flat.open()
                except SearchBudget:
                    return flat, spent_result
                return flat, await work(flat)
            except ResearchError as e:
                if e.details.get("reason") != "session_expired" or attempt:
                    raise
                session.reset()
        raise AssertionError("unreachable")

    async def sheets(self, sid, owner, place: Place, query: SearchQuery) -> tuple[list[SoupisSheet], bool, list[str]]:
        """Sheets of the researched surname (and given name, when known) in one Soupis series, found by bisection. Every
        spelling of the surname (as given, without diacritics) and every Czech/German form of the given name is a run of its
        own, primary form first; all runs share the budget of the call."""
        node_id = node_id_of(place.source_id)
        series = SOUPIS_SERIES[node_id][0]
        surname = (query.person.surname or "").strip()
        given_text = (query.person.given_name or "").strip()
        forms = given_forms(given_text) if given_text else []
        wanted_given = given_key(given_text)
        warnings: list[str] = []
        band = None      # birth years collected; usable only with a given name, because the list runs surname → given name → year
        if wanted_given and query.event_type == "birth" and (query.year_from or query.year_to):
            band = ((query.year_from or query.year_to) - YEAR_BAND, (query.year_to or query.year_from) + YEAR_BAND)

        def before_of(target, gk):
            def check(book):
                p = parse_soupis_label(book.label)
                if p.surname is None:
                    return None
                b = surname_before(p.surname, target)
                if b is None or b or not gk:
                    return b
                if parse_name(p.surname).key != parse_name(target).key:
                    return False
                if not p.given_name:
                    return None
                row_given = given_key(p.given_name)
                if row_given != gk or band is None:
                    return row_given < gk
                return None if p.birth_year is None else p.birth_year > band[1]      # years run downwards within a name
            return check

        def matches(book):
            p = parse_soupis_label(book.label)
            names = [n for n in (p.surname, *p.alternatives) if n]
            if not any(same_surname(n, surname) for n in names):
                return False
            if not given_text:
                return True
            if not (p.given_name and same_given(given_text, p.given_name)):
                return False
            return band is None or p.birth_year is None or band[0] <= p.birth_year <= band[1]

        targets = search_targets(surname)
        runs = [(t, given_key(f) if f else "") for t in targets for f in (forms or [""])]

        async def work(flat):
            out, reasons, near, missed = {}, set(), [], []
            for idx, (target, gk) in enumerate(runs):
                hint_key = f"{node_id}:{surname_key_of(target)}:{gk}:{band}"
                hint = self.store.entity("ahmp_soupis_hint", hint_key, self.source.cfg.metadata_ttl)
                hint_row = hint["first"] if hint and hint.get("total") == flat.total else None
                try:
                    first = await lower_bound(flat, before_of(target, gk), hint_row)
                    # every run that got its boundary is remembered, empty ones too; a repeat call then starts from it
                    self.store.put_entity("ahmp_soupis_hint", hint_key, {"first": first, "total": flat.total})
                    found = await collect(flat, first, matches, max(MAX_SHEETS - len(out), 1), MAX_COLLECT_PAGES)
                except SearchBudget:
                    missed = runs[idx:]
                    break
                if found.cut:
                    reasons.add(found.reason)
                    if found.reason == "requests":
                        missed = runs[idx + 1:]
                if not found.rows:
                    for n in (first - 1, first):
                        p = parse_soupis_label(flat.rows[n].label) if n in flat.rows else None
                        if p and p.surname and p.surname not in near:
                            near.append(p.surname)
                for n, book in found.rows:
                    out[n] = book
                if len(out) >= MAX_SHEETS or missed:
                    if len(out) >= MAX_SHEETS:
                        reasons.add("rows")
                        missed = runs[idx + 1:]
                    break
            return out, reasons, near, missed

        flat, (rows, reasons, near, missed) = await self._flat_run(sid, owner, node_id, work, ({}, set(), [], list(runs)))
        complete = not reasons and not missed
        if missed:
            names = ", ".join(dict.fromkeys(f"{t} {next((f for f in forms if given_key(f) == g), '')}".strip() for t, g in missed))
            warnings.append(f"Vyčerpán limit {MAX_REQUESTS_PER_CALL} požadavků na jedno volání; neprohledáno: {names}. "
                            "Výpis je neúplný (listing_complete=false); zopakujte hledání, nalezené hranice se pamatují.")
        if "rows" in reasons:
            warnings.append(
                f"V pásmu let narození {band[0]}–{band[1]} je více listů, než se vejde do výpisu ({MAX_SHEETS}); zúžte zadání přes revise_research."
                if band else
                f"Nalezeno více listů, než se vejde do jednoho výpisu ({MAX_SHEETS}); doplňte přes revise_research křestní jméno (person.given_name) "
                "a rok narození (event_type=birth s year_from/year_to) a hledejte znovu.")
        if reasons & {"pages", "requests"}:
            warnings.append("Sběr listů se zastavil na limitu stránek nebo požadavků jednoho volání; výpis může být neúplný "
                            "(listing_complete=false), zopakujte hledání.")
        if given_text and not in_table(given_text):
            warnings.append(f"Jméno „{forms[0]}“ není v tabulce českých a německých tvarů; hledal se jen zadaný tvar, "
                            "jiný tvar (např. německý) zkuste zadat zvlášť přes revise_research.")
        if band:
            warnings.append(f"Vypsány jsou jen listy narozené v letech {band[0]}–{band[1]} (roky zadání ±{YEAR_BAND}); listy mimo toto pásmo "
                            f"se nehledaly a listing_complete=true platí jen pro ně. Za shodu (candidate) se považuje ±2, okraj pásma je needs_review.")
        if not rows and not missed and not reasons:
            seen = any(same_surname(n, surname) for r in flat.rows.values()
                       if (n := parse_soupis_label(r.label).surname))
            what = " / ".join(dict.fromkeys(targets)) + (f" {'/'.join(forms)}" if forms else "") + (f" v letech {band[0]}–{band[1]}" if band else "")
            warnings.append(f"V řadě {series} nebyl nalezen žádný list pro {what}."
                            + ("" if seen else f" Příjmení „{surname}“ v seznamu není"
                               + (f" (nejbližší příjmení v seznamu: {' a '.join(near)})" if near else "")
                               + "; jinak psané příjmení může ležet jinde, zkuste jeho variantu.")
                            + " Hledáno podle fonetického řazení archivu (W = V, Š u S, německé a české pravopisy).")
        elif complete:
            warnings.append("Hledáno podle fonetického řazení archivu; jinak psané varianty příjmení mohou ležet jinde."
                            + (f" S křestním jménem se vypisují jen listy jeho tvarů ({', '.join(forms)}; tabulka českých a německých tvarů)." if forms else ""))
        sheets = []
        for n, book in sorted(rows.items()):
            sheet = self._sheet(book, series)
            sheets.append(sheet)
            self.store.put_entity("book", sheet.source_id, sheet.model_dump())
            self.store.link(sid, owner, "book_option", sheet.source_id, sheet.model_dump())
        return sheets, complete, warnings

    def _sheet(self, row: BookRow, series: str) -> SoupisSheet:
        p = parse_soupis_label(row.label)
        who = ", ".join(x for x in (p.surname, p.given_name) if x) or row.label
        return SoupisSheet(source_id=f"ahmp:book:{row.xid}", number=who + (f" (*{p.birth_year})" if p.birth_year else ""),
                           creator=series, coverage={}, image_count=row.scans, detail_url=self.site.permalink_url(row.xid),
                           raw_notes=f"{SOUPIS_TITLE}; {series}; {row.label}"[:4000], series=series, order_no=p.order_no,
                           surname=p.surname, given_name=p.given_name, birth_year=p.birth_year)

    async def houses(self, sid, owner, place: Place, house: int) -> tuple[list[CensusHouse], bool, list[str]]:
        """The row(s) of one house number in a census district (a list sorted by house number). The district is the census node
        itself (its own name, place.names[0]), the year the node one level up (place.names[1])."""
        node_id = node_id_of(place.source_id)
        district, year_text = place.names[0], (place.names[1] if len(place.names) > 1 else "")

        def before(book):
            h = house_of(book.label)
            return None if h is None else h[0] < house

        async def work(flat):
            try:
                first = await lower_bound(flat, before)
                found = await collect(flat, first, lambda b: (house_of(b.label) or (None,))[0] == house, MAX_HOUSES, 3, tolerance=2)
            except SearchBudget:
                return {}, False, True
            return dict(found.rows), found.cut, False

        flat, (rows, cut, spent) = await self._flat_run(sid, owner, node_id, work, ({}, False, True))
        warnings = []
        seen = [h[0] for _, b in sorted(flat.rows.items()) if (h := house_of(b.label))]
        if seen != sorted(seen):
            found_text = ", ".join(f"čp. {house_of(b.label)[0]}{house_of(b.label)[1]}" for b in rows.values()) or "žádný řádek"
            warnings.append(f"Řazení domů v seznamu neodpovídá číselnému pořadí; výpis nemusí být úplný. Nalezeno: {found_text}.")
            cut = True
        if spent:
            warnings.append(f"Vyčerpán limit {MAX_REQUESTS_PER_CALL} požadavků na jedno volání; zopakujte hledání.")
        if not rows and not spent:
            near = sorted({h[0] for b in flat.rows.values() if (h := house_of(b.label))})
            lower = [n for n in near if n < house][-1:], [n for n in near if n > house][:1]
            warnings.append(f"Číslo popisné {house} v seznamu obvodu není (nejbližší čísla: {', '.join(map(str, lower[0] + lower[1])) or 'žádné'}); "
                            "mohlo být sloučeno s jiným domem nebo patřit do jiného obvodu.")
        out = []
        for n, book in sorted(rows.items()):
            h = house_of(book.label)
            unit = CensusHouse(source_id=f"ahmp:book:{book.xid}", number=f"{district}, čp. {h[0]}{h[1]}", creator=district, coverage={},
                               image_count=book.scans, detail_url=self.site.permalink_url(book.xid), raw_notes=f"{place.label}; {book.label}"[:4000],
                               census_year=int(year_text) if year_text.isdigit() else None, district=district, house_number=h[0], house_suffix=h[1],
                               house_numbers=HouseNumbers(start=h[0], end=h[0], raw=str(h[0]), numbers=[h[0]], parsed=True))
            out.append(unit)
            self.store.put_entity("book", unit.source_id, unit.model_dump())
            self.store.link(sid, owner, "book_option", unit.source_id, unit.model_dump())
        return out, not cut and not spent, warnings

    @staticmethod
    def place_kind(place: Place) -> str:
        """Which kind a stored AHMP place belongs to: the two Soupis lists and the census districts are "census"."""
        return "census" if is_soupis_node(node_id_of(place.source_id)) or place.label.startswith(CENSUS_TITLE) else "parish_registers"

    async def with_box(self, sid, owner, sheet: Book) -> tuple[Book, list[str]]:
        """A sheet's karton comes from its own page (one request); the citation is only complete with it."""
        if not isinstance(sheet, SoupisSheet) or sheet.box:
            return sheet, []
        body = await self.source.fetch(sid, owner, self.site.permalink_url(xid_of(sheet.source_id)), kind="html")
        text = " ".join(BeautifulSoup(body.decode("utf-8-sig", errors="replace"), "lxml").get_text(" ", strip=True).split())
        m = re.search(r"Ukládací jednotka:\s*(karton/\d{1,6})", text)
        if not m or sheet.number.split(" (")[0] not in text:
            return sheet, ["Číslo kartonu se z katalogu nepodařilo přečíst; citace ho neobsahuje."]
        sheet = sheet.model_copy(update={"box": m[1]})
        self.store.put_entity("book", sheet.source_id, sheet.model_dump())
        return sheet, []

    # ------------------------------------------------------------------ books
    def _book(self, row: BookRow, place: Place) -> Book:
        return Book(source_id=f"ahmp:book:{row.xid}", number=row.signature, creator=place.names[0],
                    coverage=coverage_of(row.signature, row.years), image_count=row.scans,
                    detail_url=self.site.permalink_url(row.xid), raw_notes=f"{place.label}; {row.label}"[:4000])

    async def books(self, sid, owner, place: Place) -> tuple[list[Book], bool, list[str]]:
        node_id = node_id_of(place.source_id)
        cached = self.store.entity("ahmp_books", str(node_id), self.source.cfg.metadata_ttl)
        if cached is None:
            node = await walk_node(self.source, self.site, self.site.session(sid, owner), node_id, MAX_LIST_PAGES)
            if not node.books and not node.warnings:
                raise ResearchError("INVALID_ARGUMENT", "Tento uzel AHMP neobsahuje knihy; vyberte farnost z find_place.")
            cached = {"rows": [row.__dict__ for row in node.books], "complete": node.complete, "warnings": node.warnings}
            if node.complete:
                self.store.put_entity("ahmp_books", str(node_id), cached)
        books = {}
        for row in cached["rows"]:
            book = self._book(BookRow(**row), place)
            books[book.source_id] = book
            self.store.put_entity("book", book.source_id, book.model_dump())
            self.store.link(sid, owner, "book_option", book.source_id, book.model_dump())
        return list(books.values()), cached["complete"], cached["warnings"]

    # ------------------------------------------------------------------ pages
    def shell_page(self, book: Book, n: int) -> Page:
        """A scan's page before its image root is known: the permalink stands in for the source until it is resolved."""
        xid = xid_of(book.source_id)
        link = self.site.permalink_url(xid, n)
        return Page(page_id=f"{book.source_id}:{n}", book_id=book.source_id, scan_number=n, image_key=f"scan-{n}",
                    source_url=link, viewer_url=link, source_type="zoomify",
                    manifest_sha256=manifest_hash(self.site, xid, book.image_count), source="ahmp",
                    citation=citation_of(book), archive="ahmp")

    async def resolve(self, sid, owner, book: Book, n: int) -> Page:
        """One permalink request yields the scan's image root; the page is then a regular Zoomify page."""
        xid, total = xid_of(book.source_id), book.image_count
        if not total or not 1 <= n <= total:
            raise ResearchError("NOT_FOUND", "Snímek leží mimo rozsah knihy.")
        url = self.site.permalink_url(xid, n)
        body = await self.source.fetch(sid, owner, url, kind="html")
        try:
            viewer = parse_viewer(body.decode("utf-8-sig", errors="replace"), self.site, xid, n, self.source.cfg.max_source_pixels)
        except ResearchError:
            self.source.invalidate(url)
            raise
        if viewer.total != total:
            self.source.invalidate(url)
            raise ResearchError("SOURCE_CHANGED", "Počet snímků knihy se v katalogu změnil; znovu vypište a otevřete knihu.",
                                listed=total, shown=viewer.total)
        source_url = viewer.image_root + "/ImageProperties.xml"
        self.source.policy.validate(source_url)
        return self.shell_page(book, n).model_copy(update={"source_url": source_url, "image_key": viewer.image_root.rsplit("/", 1)[1]})
