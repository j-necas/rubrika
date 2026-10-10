"""Police registration forms of Prague 1850-1914 (Národní archiv, fond Policejní ředitelství Praha I – konskripce, digi.nacr.cz).

A PHP application on plain HTTP with no API. The search state lives in a server session whose token sits in every address; an unknown
token silently starts a new, unfiltered session, so every page is checked to carry the token the client holds. A result row shows only surname, given name and birth year; the form (karton, obraz) and its whole
household come from the row's detail page. A form has a permalink that needs no session, and its scan is one JPEG file.
Source text is data; nothing the pages send is evaluated."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .ahmp import person_relevance, tokens
from .catalog import normalize
from .errors import ResearchError
from .models import Book, Model, Page, Place, SearchQuery
from .soapraha import check_jpeg
from .soupis import given_forms, in_table, same_given, same_surname, search_targets
from .sources import PRIHLASKY_APP, PRIHLASKY_HOST, PRIHLASKY_REF

PREFIX = "na_prihlasky:"
BASE = "http://" + PRIHLASKY_HOST
APP_URL = BASE + PRIHLASKY_APP + "/"
TITLE = "Policejní přihlášky Prahy 1850–1914"
FUND = "Policejní ředitelství Praha I – konskripce"
ARCHIVE_NAME = "Národní archiv"
PLACE_ID = "na_prihlasky:place:praha"
FORM_ID = re.compile(r"na_prihlasky:form:(\d{1,4}):(\d{1,6})")
PAGE_ID = re.compile(r"(na_prihlasky:form:\d{1,4}:\d{1,6}):(1)")
MAX_REQUESTS_PER_CALL = 30        # every HTTP request of one list_sources call: entry page, searches, result pages, detail pages
MAX_SEARCHES = 3                  # the spelling variants tried one after another, only while the search finds nothing
MAX_RESULT_PAGES = 5              # result pages read per search (20 rows each on the live application)
MAX_DETAILS = 15                  # detail pages (forms) read per call, best rows first
YEAR_BAND = 5                     # birth years searched around the years of the research; ±2 of them are candidates
_TOKEN = re.compile(r"session=([0-9a-f]{64})")
_EMPTY = "nenalezen žádný záznam"
_CITATION = re.compile(r"Citace:\s*Národní archiv, Policejní ředitelství I, konskripce, karton (\d{1,4}), obraz (\d{1,6})")
_IMAGE = re.compile(r"images/(\d{8})-(\d{8})\.jpg")
_LINK = re.compile(r"\?action=link&ref=(\S+?)&karton=(\d{1,4})&folium=(\d{1,6})")
_TOTAL = re.compile(r"Počet nalezených záznamů:\s*(\d{1,9})")
_NEXT = re.compile(r"\?session=[0-9a-f]{64}&action=list&od=(\d{1,5})")
_RECORD = re.compile(r"\?session=[0-9a-f]{64}&action=image&record=([1-9]\d{0,5})")


def _bad(message: str, **details) -> ResearchError:
    return ResearchError("PARSE_ERROR", message, **details)


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


# ---------------------------------------------------------------- ids and addresses

def form_id(karton: int, folium: int) -> str:
    return f"{PREFIX}form:{int(karton)}:{int(folium)}"


def is_prihlasky_id(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def parse_form_id(source_id: str) -> tuple[int, int]:
    m = FORM_ID.fullmatch(source_id or "")
    if not m:
        raise ResearchError("INVALID_ARGUMENT", "Přihlášku otevřete podle ID ve tvaru na_prihlasky:form:<karton>:<obraz> z list_sources.")
    return int(m[1]), int(m[2])


def permalink_url(karton: int, folium: int) -> str:
    return f"{APP_URL}index.php?action=link&ref={PRIHLASKY_REF}&karton={int(karton)}&folium={int(folium)}"


def image_url(karton: int, folium: int) -> str:
    return f"{APP_URL}images/{int(karton):08d}-{int(folium):08d}.jpg"


def citation_of(karton: int, folium: int) -> str:
    return f"{ARCHIVE_NAME}, {FUND}, karton {int(karton)}, obraz {int(folium)}"


def manifest_hash(karton: int, folium: int) -> str:
    return hashlib.sha256(f"na_prihlasky\0{int(karton)}\0{int(folium)}".encode()).hexdigest()


# ---------------------------------------------------------------- models

class HouseholdMember(Model):
    """One person of a form as the archive lists it: "Surname (earlier surnames) Given names (birth year)"."""
    raw: str
    surname: str
    other_surnames: list[str] = []      # what stands in the brackets after the surname: the maiden name(s), as the form writes them
    maiden_name: str | None = None      # the same, joined ("Šletina, Drtinová"); the index knows the woman under the married surname only
    given_name: str | None = None
    birth_year: int | None = None
    head: bool = False                  # the first person listed, the head of the household
    matched: bool = False               # the person a search row led to (set by list_sources only)


class PrihlaskaForm(Book):
    """One registration form (arch): a karton and an image number, the whole household, the scan. `index_hits` are the rows of the
    search that led to it (a form is often hit by several members)."""
    karton: int
    folium: int
    image: str
    citation: str
    household: list[HouseholdMember] = []
    index_hits: list[str] = []


# ---------------------------------------------------------------- parsers

def parse_entry(html: str) -> str:
    """The session token of the entry page (in the action of its forms)."""
    tokens_ = set(_TOKEN.findall(html))
    soup = BeautifulSoup(html, "lxml")
    if len(tokens_) != 1 or not soup.select_one("div.hledani2 form input[name=prijmeni]"):
        raise _bad("Vstupní stránka přihlášek nemá očekávanou strukturu (formulář hledání nebo token relace).")
    return tokens_.pop()


def _same_session(html: str, token: str | None) -> None:
    """Every session link of a page must carry the token we hold, and at least one must be there; another token (or none) means the
    server did not know ours and opened a new session (over the whole index), so nothing on the page belongs to our search."""
    found = set(_TOKEN.findall(html))
    if token is not None and (found - {token} or token not in found):
        raise ResearchError("SOURCE_UNAVAILABLE", "Relace přihlášek vypršela.", reason="session_expired")


@dataclass(frozen=True)
class ResultRow:
    record: int              # the row's number in the session's result list ("p. číslo"); the detail page is addressed by it
    surname: str
    given_name: str | None
    birth_year: int | None


@dataclass(frozen=True)
class ResultPage:
    total: int
    rows: list[ResultRow]
    next_offset: int | None


def parse_results(html: str, token: str) -> ResultPage:
    _same_session(html, token)
    soup = BeautifulSoup(html, "lxml")
    if _EMPTY in _text(soup.body or soup).casefold() and not soup.select_one("table.table_seznam"):
        return ResultPage(0, [], None)
    count = soup.select_one("div.pocet_zaznamu")
    table = soup.select_one("table.table_seznam")
    m = _TOTAL.search(_text(count)) if count else None
    if not m or table is None:
        raise _bad("Seznam přihlášek nemá očekávanou strukturu (počet záznamů nebo tabulka).")
    rows = []
    for tr in table.select("tr.zaznamy"):
        cells = tr.find_all("td", recursive=False)
        link = cells[1].find("a", href=True) if len(cells) == 4 else None
        rec = _RECORD.fullmatch(link["href"].replace("&amp;", "&")) if link else None
        year = _text(cells[3]) if len(cells) == 4 else ""
        if not rec or not _text(link) or (year and not re.fullmatch(r"1[5-9]\d\d", year)):
            raise _bad("Řádek seznamu přihlášek nemá očekávanou strukturu.")
        rows.append(ResultRow(int(rec[1]), _text(link), _text(cells[2]) or None, int(year) if year else None))
    total = int(m[1])
    if total and not rows:
        raise _bad("Seznam přihlášek hlásí záznamy, ale žádný řádek nenašel.")
    nxt = None
    menu = soup.select_one("div.menu")
    for a in menu.find_all("a", href=True) if menu else []:
        n = _NEXT.fullmatch(a["href"].replace("&amp;", "&"))
        if n and "Další" in _text(a):
            nxt = int(n[1])
    return ResultPage(total, rows, nxt)


@dataclass(frozen=True)
class Detail:
    karton: int
    folium: int
    image: str
    citation: str
    permalink: str
    members: list[HouseholdMember]


_MEMBER = re.compile(r"^(?P<surname>\S+)(?:\s+\((?P<others>[^)]*)\))?(?:\s+(?P<given>.+?))?\s*$")


def parse_member(text: str, head: bool = False) -> HouseholdMember:
    """'Novák (Šletina, Drtinová) Barbora (1876)' → surname, earlier surnames, given names, birth year. A year that is not four
    digits (or missing) gives None; the raw text is always kept."""
    raw = " ".join(text.replace("\xa0", " ").split())
    body, year = raw, None
    y = re.search(r"\s*\(([^()]*)\)\s*$", raw)
    if y and not re.search(r"[A-Za-zÀ-ž]", y[1]):
        body = raw[: y.start()]
        year = int(y[1]) if re.fullmatch(r"1[5-9]\d\d", y[1].strip()) else None
    m = _MEMBER.match(body)
    if not m:
        return HouseholdMember(raw=raw, surname=body or raw, head=head)
    others = [o.strip() for o in (m["others"] or "").split(",") if o.strip()]
    return HouseholdMember(raw=raw, surname=m["surname"], other_surnames=others, maiden_name=", ".join(others) or None,
                           given_name=m["given"], birth_year=year, head=head)


def parse_detail(html: str, token: str | None) -> Detail:
    """A detail page (of a result row, or a permalink: `token` None): image, citation, permalink and the household."""
    _same_session(html, token)
    soup = BeautifulSoup(html, "lxml")
    img = soup.select_one("img#obrazek_prihlasky")
    cite = soup.select_one("div.citace")
    link = soup.select_one("p.link a[href]")
    people = soup.select_one("div.seznam_lidi")
    image = _IMAGE.fullmatch(img.get("src", "")) if img else None
    c = _CITATION.fullmatch(_text(cite)) if cite else None
    lk = _LINK.fullmatch(link["href"].replace("&amp;", "&")) if link else None
    if not (image and c and lk and people is not None):
        raise _bad("Detail přihlášky nemá očekávanou strukturu (obraz, citace, odkaz nebo seznam osob).")
    karton, folium = int(image[1]), int(image[2])
    if (int(c[1]), int(c[2])) != (karton, folium) or (int(lk[2]), int(lk[3])) != (karton, folium) or lk[1] != PRIHLASKY_REF:
        raise _bad("Obraz, citace a odkaz detailu přihlášky si neodpovídají.")
    members = [parse_member(_text(d), head=i == 0) for i, d in enumerate(people.find_all("div", recursive=False))]
    if not members or not all(m.raw for m in members):
        raise _bad("Detail přihlášky neuvádí osoby domácnosti.")
    return Detail(karton, folium, image[0], _text(cite).removeprefix("Citace:").strip(), permalink_url(karton, folium), members)


def form_of(detail: Detail, hit: str | None = None) -> PrihlaskaForm:
    return PrihlaskaForm(
        source_id=form_id(detail.karton, detail.folium), number=f"karton {detail.karton}, obraz {detail.folium}",
        creator=FUND, coverage={}, image_count=1, detail_url=detail.permalink, karton=detail.karton, folium=detail.folium,
        image=detail.image, citation=detail.citation, household=detail.members, index_hits=[hit] if hit else [],
        raw_notes="; ".join(m.raw for m in detail.members)[:4000])


# ---------------------------------------------------------------- relevance

_RANK = {"candidate": 0, "needs_review": 1, "excluded": 2}
WORDING = ("Přihláška", "na přihlášce")


def _names(member: HouseholdMember) -> list[str]:
    return [member.surname, *member.other_surnames]


def _is_parent(member: HouseholdMember, text: str) -> bool:
    words = set(tokens(text))
    given = tokens(member.given_name or "")
    return bool(given) and given[0] in words and any(t in words for name in _names(member) for t in tokens(name))


def row_relevance(row: ResultRow, query: SearchQuery) -> dict:
    """The relevance of a result row alone (before its form is read): surname, given name and year of the row against the request."""
    wanted = (query.person.surname or "").strip()
    if wanted and not same_surname(row.surname, wanted):
        return {"status": "needs_review", "reason": "Příjmení řádku se s hledaným shoduje jen podle předpony."}
    return person_relevance(row.given_name, row.birth_year, query, WORDING)


def _fold(text: str | None) -> str:
    return normalize(text or "")


def row_agrees(member: HouseholdMember, row: ResultRow) -> bool:
    """Does a household member match the result row it was reached from: the same surname (the married one; an earlier one is
    tolerated), given name and birth year? A detail page that does not agree is somebody else's."""
    return (any(_fold(n) == _fold(row.surname) for n in _names(member)) and _fold(member.given_name) == _fold(row.given_name)
            and member.birth_year == row.birth_year)


def form_relevance(form: PrihlaskaForm, query: SearchQuery) -> dict:
    """The household member(s) with the researched surname (an earlier surname counts: women are indexed under both) are judged by
    given name and birth year like a Soupis sheet; the best one decides. A named father or mother in the household is noted, and
    lifts an excluded form to review (the person may be a child of a household that is not the person's own record)."""
    wanted = (query.person.surname or "").strip()
    candidates = [m for m in form.household if not wanted or any(same_surname(n, wanted) for n in _names(m))]
    if candidates:
        best = min((person_relevance(m.given_name, m.birth_year, query, WORDING) for m in candidates), key=lambda r: _RANK[r["status"]])
    else:
        best = {"status": "needs_review", "reason": "V domácnosti není nikdo s hledaným příjmením."}
    parents = [f"{label} ({m.raw})" for label, text in (("otec", query.person.father), ("matka", query.person.mother))
               if text and text.strip() for m in form.household if _is_parent(m, text)]
    if parents:
        best = {"status": "needs_review" if best["status"] == "excluded" else best["status"],
                "reason": best["reason"] + " V domácnosti je i " + "; ".join(parents) + "."}
    return best


def sort_key(number: str):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", number)]


# ---------------------------------------------------------------- catalogue client

class PrihlaskyCatalog:
    def __init__(self, source, store):
        self.source, self.store = source, store

    def places(self, sid: str, owner: str) -> tuple[list[Place], list[str]]:
        """The one place of this source; Prague's index is a single list, searched by person."""
        place = Place(source_id=PLACE_ID, label=TITLE, names=[TITLE, "Praha"], source_url=APP_URL)
        self.store.put_entity("place", place.source_id, place.model_dump())
        self.store.link(sid, owner, "place_option", place.source_id, place.model_dump())
        return [place], ["Přihlášky se hledají podle osoby: příjmení (person.surname, povinné), podle možnosti křestní jméno a rok narození "
                         "(event_type=birth s year_from/year_to) z revise_research. Území: Praha bez Vinohrad, 1850–1914 (podle archivu do 1918)."]

    async def forms(self, sid: str, owner: str, query: SearchQuery) -> tuple[list[PrihlaskaForm], bool, list[str]]:
        surname = (query.person.surname or "").strip()
        given = (query.person.given_name or "").strip()
        given_alternatives = given_forms(given) if given else []
        years = ("", "")
        warnings: list[str] = []
        band = None
        if query.event_type == "birth" and (query.year_from or query.year_to):
            low, high = (query.year_from or query.year_to) - YEAR_BAND, (query.year_to or query.year_from) + YEAR_BAND
            low, high = max(low, 1700), min(high, 1930)
            if low <= high:
                band, years = (low, high), (str(low), str(high))
        used = 0
        limit = MAX_REQUESTS_PER_CALL
        cut: set[str] = set()

        def spend() -> bool:
            nonlocal used
            if used >= limit:
                cut.add("requests")
                return False
            used += 1
            return True

        # the spellings of the request, one search each, tried only while the previous ones found nothing
        variants = [(t, given_alternatives[0] if given_alternatives else "") for t in search_targets(surname)]
        variants += [(search_targets(surname)[0], g) for g in given_alternatives[1:]]
        variants = variants[:MAX_SEARCHES]
        token, rows, total, tried = None, [], 0, []
        for name, first in variants:
            if token is None:
                if not spend():
                    break
                token = parse_entry((await self.source.state_get(sid, owner, APP_URL)).decode("utf-8-sig", errors="replace"))
            if not spend():
                break
            form = {"prijmeni": name, "jmeno": first, "odroku": years[0], "doroku": years[1], "action": "hledani2", "tlacitko": "Najít"}
            body = await self.source.state_post(sid, owner, f"{APP_URL}?session={token}", form)
            result = parse_results(body.decode("utf-8-sig", errors="replace"), token)
            tried.append((name, first))
            rows, total, pages = list(result.rows), result.total, 1
            while result.next_offset is not None and total:
                if pages >= MAX_RESULT_PAGES:
                    cut.add("pages")
                    break
                if not spend():
                    break
                body = await self.source.state_get(sid, owner, f"{APP_URL}?session={token}&action=list&od={result.next_offset}")
                result = parse_results(body.decode("utf-8-sig", errors="replace"), token)
                if result.total != total or not result.rows:
                    raise ResearchError("SOURCE_UNAVAILABLE", "Relace přihlášek vypršela.", reason="session_expired")
                rows += result.rows
                pages += 1
            if total:
                break
        if not rows:
            what = " / ".join(dict.fromkeys(f"{n} {g}".strip() for n, g in tried)) + (f" v letech {band[0]}–{band[1]}" if band else "")
            warnings.append(f"V přihláškách nebyl nalezen nikdo pro {what or surname}. Index je přepsán s transliteracemi (Novak a Novák jsou "
                            "samostatné záznamy) a hledá se jen od začátku jména; zkuste jinou podobu příjmení nebo jména. Vdaná žena je v indexu pod příjmením "
                            "manžela (dívčí je na přihlášce jen v závorce): hledejte ji pod jeho příjmením. " + ("Osoby bez roku narození v indexu jsou mimo "
                            "hledání s roky; zkuste hledání bez let (bez event_type=birth a year_from/year_to)." if band else ""))
            return [], not cut, warnings
        # rows that cannot be the person are not opened; the best rows first, then the ones nearest the researched years
        centre = ((query.year_from or query.year_to) + (query.year_to or query.year_from)) / 2 if band else None
        ranked = sorted(rows, key=lambda r: (_RANK[row_relevance(r, query)["status"]],
                                             abs(r.birth_year - centre) if centre and r.birth_year else 99, r.record))
        openable = [r for r in ranked if row_relevance(r, query)["status"] != "excluded"]
        forms: dict[str, PrihlaskaForm] = {}
        skipped = 0
        for row in openable:
            if len(forms) >= MAX_DETAILS or not spend():
                skipped = len(openable) - openable.index(row)
                cut.add("details" if len(forms) >= MAX_DETAILS else "requests")
                break
            body = await self.source.state_get(sid, owner, f"{APP_URL}?session={token}&action=image&record={row.record}")
            detail = parse_detail(body.decode("utf-8-sig", errors="replace"), token)
            hit_at = next((i for i, m in enumerate(detail.members) if row_agrees(m, row)), None)
            if hit_at is None:
                warnings.append(f"Detail záznamu č. {row.record} neodpovídá řádku seznamu; přihláška karton {detail.karton}, obraz {detail.folium} vynechána.")
                continue
            hit = f"{row.surname} {row.given_name or ''} ({row.birth_year or '?'})".replace("  ", " ")
            unit = forms.get(form_id(detail.karton, detail.folium)) or form_of(detail)
            household = [m.model_copy(update={"matched": m.matched or i == hit_at}) for i, m in enumerate(unit.household)]
            forms[unit.source_id] = unit.model_copy(update={"index_hits": [*unit.index_hits, hit], "household": household})
        for unit in forms.values():
            self.store.put_entity("book", unit.source_id, unit.model_dump())
            self.store.link(sid, owner, "book_option", unit.source_id, unit.model_dump())
        tried_text = ", ".join(f"{n} {g}".strip() for n, g in tried)
        if total > len(rows):
            warnings.append(f"Nalezeno {total} záznamů, přečteno prvních {len(rows)} (nejvýš {MAX_RESULT_PAGES} stran po 20). Zúžte hledání "
                            "(křestní jméno, rok narození) přes revise_research a hledejte znovu.")
        elif "pages" in cut:
            warnings.append(f"Nalezeno {total} záznamů, přečteno jen {len(rows)}; zúžte hledání (křestní jméno, rok narození).")
        if skipped or "details" in cut or "requests" in cut:
            warnings.append(f"Detail (arch s celou domácností) se načetl u {len(forms)} přihlášek; dalších {skipped} nejméně shodných záznamů "
                            f"se neotevřelo (nejvýš {MAX_DETAILS} přihlášek a {MAX_REQUESTS_PER_CALL} požadavků na volání). Výpis je neúplný "
                            "(listing_complete=false); zúžte hledání a zopakujte ho.")
        dropped = len(rows) - len(openable)
        if dropped:
            warnings.append(f"{dropped} záznamů, jejichž jméno i rok narození zadání neodpovídají, se neotevřelo.")
        if len(tried) > 1:
            warnings.append(f"První podoba jména nic nenašla; nalezeno až s: {tried_text}.")
        elif len(given_alternatives) > 1 and in_table(given):
            warnings.append(f"Hledalo se jen s křestním jménem „{given_alternatives[0]}“ (začátek jména); jiný tvar ({', '.join(given_alternatives[1:])}) "
                            "se zkouší, jen když tvar zadání nic nenajde.")
        if band:
            warnings.append(f"Hledáno jen v ročnících narození {band[0]}–{band[1]} (roky zadání ±{YEAR_BAND}); za shodu (candidate) se považuje ±2, okraj pásma je needs_review. "
                            "Osoby bez roku narození v indexu jsou mimo hledání s roky a nenajdou se; listing_complete platí jen pro toto pásmo.")
        warnings.append("Každá osoba přihlášky je v indexu zvlášť, vdaná žena pod příjmením manžela (dívčí příjmení je jen v závorce na přihlášce, hledat podle něj nelze); přepis obsahuje transliterace (Novak a Novák jsou samostatné záznamy).")
        complete = not cut and total == len(rows) and not skipped
        return sorted(forms.values(), key=lambda f: (f.karton, f.folium)), complete, warnings

    # -- one form
    async def open_form(self, sid: str, owner: str, book: PrihlaskaForm) -> tuple[PrihlaskaForm, list[str]]:
        """A form listed in this research already carries its detail (household, image, citation), read with the listing: it is used
        as it is, no second request. Only a form without a household is read by its permalink (no session, never cached)."""
        if book.household:
            return book, []
        karton, folium = parse_form_id(book.source_id)
        body = await self.source.state_get(sid, owner, permalink_url(karton, folium))
        detail = parse_detail(body.decode("utf-8-sig", errors="replace"), None)
        if (detail.karton, detail.folium) != (karton, folium):
            raise ResearchError("SOURCE_CHANGED", "Odkaz přihlášky vede na jiný karton nebo obraz; vypište přihlášky znovu.")
        return form_of(detail).model_copy(update={"index_hits": book.index_hits}), []

    def shell_page(self, book: PrihlaskaForm) -> Page:
        """The one page (scan) of a form: the whole JPEG, whose address is known from the form itself."""
        url = image_url(book.karton, book.folium)
        return Page(page_id=f"{book.source_id}:1", book_id=book.source_id, scan_number=1, image_key=f"{book.karton:08d}-{book.folium:08d}.jpg",
                    source_url=url, viewer_url=book.detail_url, source_type="jpeg", manifest_sha256=manifest_hash(book.karton, book.folium),
                    source="prihlasky", citation=book.citation, archive="na_prihlasky")

    async def scan(self, sid: str, owner: str, page: Page) -> tuple[bytes, str]:
        """The whole JPEG of a form, through the user's cache (the plain image address needs no session)."""
        limit = self.source.cfg.max_source_pixels
        raw = await self.source.fetch(sid, owner, page.source_url, kind="scan")
        try:
            check_jpeg(raw, limit)
        except ResearchError:
            self.source.invalidate(page.source_url)
            raise
        return raw, page.image_key
