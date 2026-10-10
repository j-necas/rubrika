"""Flat, phonetically sorted lists of the Prague City Archives (Soupis pražského obyvatelstva, census houses).

The catalogue has no search API for them, but the pager of a list answers a plain GET "go to row N" (see bach.jump_url).
Sorted lists can therefore be searched by bisection: about a dozen requests find one surname among 120 000 rows.
This module holds the archive's collation rules, the row labels and the search itself; it never evaluates anything
the catalogue sends."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from .bach import BachSite, BookRow, JumpForm, NodePage, jump_url, parse_node
from .errors import ResearchError
from .network import SiteSession

# ---------------------------------------------------------------- collation

_CARON = {"č": "\ue001", "ř": "\ue002", "š": "\ue003", "ž": "\ue004"}
_AFTER = {"\ue001": "c\x7f", "\ue002": "r\x7f", "\ue003": "s\x7f", "\ue004": "z\x7f", "\ue005": "h\x7f"}
_FEMALE = re.compile(r"[-\s]?ov[aá]$")
_ADJECTIVE = (("ská", "ský"), ("cká", "cký"), ("ná", "ný"))
_VOWELS = "aeiou"


def _simplify(text: str) -> str:
    """Lower case, spelling variants of one sound folded together, everything but the letters dropped."""
    text = text.casefold()
    text = re.sub(r"tsch|tš|cž|cz", "č", text)
    text = re.sub(r"sch|sz", "š", text)
    text = text.replace("ch", "\ue005")
    for plain, mark in _CARON.items():
        text = text.replace(plain, mark)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("w", "v").replace("y", "i").replace("ß", "ss")
    return "".join(_AFTER.get(c, c) for c in text if c in _AFTER or "a" <= c <= "z")


@dataclass(frozen=True)
class Name:
    key: str
    female: bool         # a -ová form: the archive files it with the male name, whose last vowel the form has dropped


def parse_name(surname: str) -> Name:
    """Sort key of a surname as the archive files it: Czech collation (č after c, ř after r, š after s, ž after z, ch after
    h), W = V, Y = I, the German and Polish spellings of Czech sounds (sch, tsch, cz, cž) folded, no diacritics.
    Female forms (Maděrová, Maděra-ová, Nováková, Novotná) are reduced to the male stem."""
    base = re.sub(r"\s*\(.*?\)\s*", " ", surname).strip().casefold()
    stripped = _FEMALE.sub("", base) if len(base) > 5 else base
    female = stripped != base
    if not female:
        for feminine, masculine in _ADJECTIVE:
            if base.endswith(feminine) and len(base) > len(feminine) + 1:
                stripped, female = base[: -len(feminine)] + masculine, True
                break
    return Name(_simplify(stripped), female and not any(stripped.endswith(x) for x in ("ský", "cký", "ný")))


def surname_key(surname: str) -> str:
    return parse_name(surname).key


def _stem_of(short: Name, long: Name) -> bool:
    return short.female and len(long.key) == len(short.key) + 1 and long.key.startswith(short.key) and long.key[-1] in _VOWELS


def surname_before(row_surname: str, target_surname: str) -> bool | None:
    """Is the row's surname sorted before the target? None when the two are one name (or a female stem of it): those rows
    are not compared, because a female form sits among the male ones under a key that is one letter short."""
    r, t = parse_name(row_surname), parse_name(target_surname)
    if _stem_of(r, t) or _stem_of(t, r):
        return None
    return r.key < t.key          # strict, on the exact archive key: Simek and Šimek are different places in the list


def same_surname(a: str, b: str) -> bool:
    x, y = parse_name(a), parse_name(b)
    return loose(x.key) == loose(y.key) or _stem_of(x, y) or _stem_of(y, x)


def given_key(given: str) -> str:
    """Sort key of the first given name (the archive files 'Jan Bedřich' among the Jans)."""
    first = re.split(r"[\s,]+", given.strip(), maxsplit=1)[0] if given.strip() else ""
    return _simplify(first)


def loose(key: str) -> str:
    """Comparison key for 'the same name': doubled letters, ph/f, th/t and the carons do not matter (Josef = Joseph,
    Nolly = Noly, Dvorak = Dvořák)."""
    key = key.replace("ph", "f").replace("th", "t")
    for letter in "crsz":
        key = key.replace(letter + "\x7f", letter)
    return re.sub(r"(.)\1+", r"\1", key)


# Common Czech ↔ German given names. The Soupis often records the German form, and the archive files every spelling as a run of
# its own, so each form is searched separately. Forms are compared after the usual folding (case, diacritics, W = V, ph = f).
GIVEN_EQUIVALENTS = (
    ("Josef", "Joseph"), ("František", "Franz"), ("Jan", "Johann"), ("Václav", "Wenzel", "Wenzl"), ("Karel", "Karl"),
    ("Antonín", "Anton"), ("Jiří", "Georg"), ("Tomáš", "Thomas"), ("Matěj", "Mathias"), ("Vojtěch", "Adalbert"),
    ("Jakub", "Jakob"), ("Petr", "Peter"), ("Pavel", "Paul"), ("Ondřej", "Andreas"), ("Michal", "Michael"),
    ("Štěpán", "Stefan"), ("Marie", "Maria"), ("Kateřina", "Katharina"), ("Barbora", "Barbara"), ("Terezie", "Theresia"),
    ("Alžběta", "Elisabeth"), ("Magdaléna", "Magdalena"), ("Anna",), ("Ludmila",), ("Josefa", "Josepha"),
    ("Františka", "Franziska"), ("Johana", "Johanna"), ("Karolína", "Karoline"),
)


def given_forms(given: str) -> list[str]:
    """The given name as entered, then the other forms of its group (the entered name first: it is searched first)."""
    first = re.split(r"[\s,]+", given.strip(), maxsplit=1)[0] if given.strip() else ""
    if not first:
        return []
    mine = loose(given_key(first))
    for group in GIVEN_EQUIVALENTS:
        if any(loose(given_key(g)) == mine for g in group):
            rest = [g for g in group if given_key(g) != given_key(first)]      # forms with another sort key are runs of their own
            return [first, *rest]
    return [first]


def in_table(given: str) -> bool:
    """Is the given name in one of the GIVEN_EQUIVALENTS groups (else only the entered form can be searched)?"""
    first = re.split(r"[\s,]+", given.strip(), maxsplit=1)[0] if given.strip() else ""
    return bool(first) and any(loose(given_key(first)) == loose(given_key(g)) for group in GIVEN_EQUIVALENTS for g in group)


def same_given(a: str, b: str) -> bool:
    """Equal after folding, or forms of one group of GIVEN_EQUIVALENTS."""
    return loose(given_key(b)) in {loose(given_key(f)) for f in given_forms(a)}


def search_targets(surname: str) -> list[str]:
    """The spellings to look up: as given, and without diacritics (which the archive may file in another place)."""
    bare = "".join(c for c in unicodedata.normalize("NFKD", surname) if not unicodedata.combining(c))
    targets = [surname]
    if surname_key(bare) != surname_key(surname):
        targets.append(bare)
    return targets


# ---------------------------------------------------------------- row labels

@dataclass(frozen=True)
class SoupisLabel:
    order_no: int | None
    birth_year: int | None          # "neuveden" gives None
    surname: str | None
    given_name: str | None
    alternatives: tuple[str, ...] = ()


def parse_soupis_label(label: str) -> SoupisLabel:
    """'141 • 1878 • Novák, Josef' → order number, birth year, surname, given name. Anything that does not fit gives
    None fields (the row is kept, never guessed)."""
    parts = [p.strip() for p in label.split("•", 2)]
    if len(parts) != 3:
        return SoupisLabel(None, None, None, None)
    order = int(parts[0]) if parts[0].isdigit() else None
    year = int(parts[1]) if re.fullmatch(r"1[5-9]\d\d", parts[1]) else None
    surname, comma, given = parts[2].partition(",")
    surname, given = surname.strip(), given.strip()
    if not surname:
        return SoupisLabel(order, year, None, None)
    alternatives = tuple(a.strip() for a in re.findall(r"\(([^)]+)\)", surname))
    return SoupisLabel(order, year, re.sub(r"\s*\(.*?\)\s*", " ", surname).strip(), given or None, alternatives)


def house_of(label: str) -> tuple[int, str] | None:
    """'138 • 1880 • Staré Město, čp. 12a' → (12, 'a'); None when the label carries no house number."""
    m = re.search(r"čp\.\s*(\d{1,6})\s*([A-Za-z]?)\s*$", label.strip())
    return (int(m[1]), m[2].lower()) if m else None


# ---------------------------------------------------------------- the flat list

class SearchBudget(Exception):
    """The request budget of one search is spent."""


@dataclass
class Row:
    number: int          # 1-based position in the whole list
    book: BookRow


class FlatList:
    """One session's view of a flat list: the node page gives the tokens, a jump gives any window of nine rows.
    Every row seen is kept, so no row is fetched twice in one search."""

    def __init__(self, source, site: BachSite, session: SiteSession, node_id: int, limit: int, extra_rows: dict[int, int] | None = None):
        self.source, self.site, self.session, self.node_id = source, site, session, node_id
        self.extra_rows = extra_rows
        self.limit = limit           # absolute value of session.requests at which no further request may start
        self.rows: dict[int, BookRow] = {}
        self.total = 0
        self.form: JumpForm | None = None
        self.pages = 0

    def spent(self) -> bool:
        # a request may open the session first: that is one more request than the counter shows
        return self.session.requests + (1 if self.session.cookie is None else 0) >= self.limit

    async def _get(self, url: str) -> NodePage:
        if self.spent():
            raise SearchBudget()
        body = await self.source.session_get(self.session, url)
        page = parse_node(body.decode("utf-8-sig", errors="replace"), self.site, url, self.node_id, self.extra_rows)
        first = page.first
        if first is None and page.total == len(page.books):
            first = 1                                    # a list of up to nine rows has no pager at all: it is all on this page
        elif first is None or page.jump is None:
            raise ResearchError("PARSE_ERROR", "Stránka seznamu nemá číslo první řádky nebo formulář pro skok na řádek.")
        self.total, self.form = page.total, page.jump
        for i, book in enumerate(page.books):
            self.rows[first + i] = book
        self.pages += 1
        return page

    async def open(self) -> None:
        await self._get(self.site.node_url(self.node_id))

    async def window(self, row: int) -> range:
        """Fetch the window that the catalogue shows for 'go to row `row`'; returns the row numbers it holds."""
        if self.form is None and self.total <= len(self.rows):
            return range(1, self.total + 1)              # everything was on the node page already
        page = await self._get(jump_url(self.site, max(1, min(row, self.total or row)), self.form))
        return range(page.first, page.first + len(page.books))

    async def get(self, number: int, forward: bool) -> BookRow | None:
        """The row `number`, fetching a window that reaches on from it in the direction of the walk when needed."""
        if not 1 <= number <= self.total:
            return None
        if number not in self.rows:
            await self.window(number + 5 if forward else number - 3)
            if number not in self.rows:                  # the window did not start where documented: try the centre once
                await self.window(number + 1)
            if number not in self.rows:
                raise ResearchError("PARSE_ERROR", "Katalog po skoku na řádek nevrátil požadovaný řádek.", row=number)
        return self.rows[number]


async def lower_bound(flat: FlatList, before: Callable[[BookRow], bool | None], hint: int | None = None) -> int:
    """The number of the first row that is not `before` the target (rows are sorted). `before` returns None for a row
    it cannot classify; such rows are skipped. Returns total+1 when every row is before the target."""
    lo, hi = 1, flat.total + 1

    def take(rows) -> None:
        nonlocal lo, hi
        flags = [(n, before(flat.rows[n])) for n in rows]
        flags = [(n, f) for n, f in flags if f is not None]
        if not flags:
            return
        first_after = next((i for i, (_, f) in enumerate(flags) if not f), None)
        if first_after is None:
            lo = max(lo, flags[-1][0] + 1)
        else:
            n = flags[first_after][0]
            hi = min(hi, n)
            if first_after > 0:
                lo = max(lo, flags[first_after - 1][0] + 1)
        if lo > hi:                                      # unsorted noise: trust the later boundary
            lo = hi

    take(sorted(flat.rows))
    steps = 0
    while lo < hi:
        steps += 1
        if steps > 64:
            raise ResearchError("PARSE_ERROR", "Půlení seznamu se nezastavilo; řazení katalogu neodpovídá očekávání.")
        mid = max(lo, min(hint - 1, hi - 1)) if steps == 1 and hint else (lo + hi) // 2
        take(await flat.window(mid + 1))
    return lo


@dataclass
class Found:
    rows: list[tuple[int, BookRow]] = field(default_factory=list)
    cut: bool = False                # a cap stopped the walk while rows might still match
    reason: str | None = None        # "rows" (row cap), "pages" (page cap) or "requests" (request budget of the call)


async def collect(flat: FlatList, start: int, matches: Callable[[BookRow], bool], max_rows: int,
                  max_pages: int, tolerance: int = 3) -> Found:
    """The rows that match around `start` (the lower bound): forward until `tolerance` rows in a row do not match, then
    backward the same way. Only pages that are not already known count against `max_pages`."""
    found: dict[int, BookRow] = {}
    cut, reason, page_limit = False, None, flat.pages + max_pages
    for forward in (True, False):
        n, misses = (start if forward else start - 1), 0
        while 1 <= n <= flat.total and misses < tolerance:
            if n not in flat.rows and (flat.pages >= page_limit or flat.spent()):
                cut, reason = True, "requests" if flat.spent() else "pages"
                break
            try:
                book = await flat.get(n, forward)
            except SearchBudget:                          # keep what has been gathered, and say the walk was cut
                cut, reason = True, "requests"
                break
            if book is not None and matches(book):
                misses = 0
                found[n] = book
                if len(found) >= max_rows:
                    cut, reason = True, "rows"
                    break
            else:
                misses += 1
            n += 1 if forward else -1
        if cut:
            break
    return Found(rows=sorted(found.items()), cut=cut, reason=reason)
