"""Entirely synthetic responses shaped like the police registration forms of Prague (digi.nacr.cz: PHP, plain HTTP, a session token
in the address, results kept in the server session, a detail page per result with the whole household, whole-scan JPEGs).
Persons, forms and images are invented; NEVER real archive data."""
import hashlib
import html
import io
import re
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image, ImageDraw

HTML = {"content-type": "text/html; charset=UTF-8"}
ROWS_PER_PAGE = 3                       # the live application shows 20; tests patch this to force paging
APP = "/prihlasky2/"
REF = "czarch:CZ-100000010:874"
CITATION = "Národní archiv, Policejní ředitelství I, konskripce, karton {k}, obraz {f}"
SCAN_SIZE = (1000, 1333)
MARKER = (400, 500, 600, 640)           # the blue rectangle every synthetic scan carries (x0, y0, x1, y1)
EMPTY = "Nenalezen žádný záznam"

# (surname, [other surnames], given name, birth year); the first person of a form is its head
FORMS = {
    (901, 11): [("Vzorný", [], "Josef", 1878), ("Vzorná", ["Ukázková"], "Marie", 1880), ("Vzorný", [], "Karel", 1905)],
    (901, 12): [("Vzorný", [], "Josef", 1879), ("Vzorná", ["Zkušební"], "Anna", 1881)],
    (902, 5): [("Vzorný", [], "Josef", 1876)],
    (902, 9): [("Vzorný", [], "Jan", 1878), ("Vzorná", ["Ukázková"], "Eva", 1882)],
    (903, 7): [("Vzorný", [], "Josef", 1890)],
    (904, 3): [("Vzorny", [], "Josef", 1878), ("Vzorna", ["Ukazkova"], "Marie", 1880)],
    (905, 2): [("Vzorný", [], "Adolf", 1878)],
    (906, 4): [("Modelová", ["Vzorná"], "Marie", 1880), ("Vzorný", [], "Josef", 1878)],
    (907, 6): [("Cech", [], "Petr", 1870)],                                  # indexed without the diacritics
    (908, 1): [("Zkoušený", [], "Joseph", 1870)],                            # indexed under the German form of the given name
}
# a surname with many records (paging, caps): Hromadný Jan 1860..1889 and a few others
for _n in range(30):
    FORMS[(910 + _n // 10, 1 + _n)] = [("Hromadný", [], "Jan", 1860 + _n)]
FORMS[(920, 1)] = [("Hromadný", [], "Jan", None)]           # a record without a birth year


def token_for(n: int) -> str:
    return hashlib.sha256(f"synthetic-prihlasky-{n}".encode()).hexdigest()


def display(person) -> str:
    surname, others, given, year = person
    alt = f" ({', '.join(others)})" if others else ""
    return f"{surname}{alt} {given} ({year if year else ''})"


def index() -> list[tuple[str, str, int | None, tuple[int, int]]]:
    """Every person of every form as the archive indexes it: under the surname on the form (the maiden name in the bracket is not indexed)."""
    rows = []
    for key, people in FORMS.items():
        for surname, others, given, year in people:
            rows.append((surname, given, year, key))            # a married woman only under the married surname; the bracket is on the form
    return sorted(rows, key=lambda r: (r[0].casefold(), r[1].casefold(), r[2] or 0, r[3]))


def page_shell(body: str, token: str) -> str:
    return f"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en">
<head><meta http-equiv="content-type" content="text/html; charset=utf-8" /><title>Policejni prihlasky</title></head>
<body>
<div class="sloupec1"><a href="http://www.nacr.cz"><img src="img/logo_NAm.jpg" alt="logoNA" class="logoNA" /></a></div>
<div class="sloupec2">
 <h1>SYNTHETIC FIXTURE - Digitalizované pobytové přihlášky (konskripce)</h1>
{body}
</div>
<div class="sloupec3">
 <div class="odkazy_vpravo"><a href="?session={token}&amp;action=rozs_hledani"><br/><br/>Podrobné vyhledávání</a></div>
</div>
<div class="zapati">Odborná správa: <a href="mailto:oprava&#0064;example.invalid">Synthetic</a>.</div><div class="cas">čas: 0 s</div>
</body>
</html>
"""


def forms_html(token: str) -> str:
    return f"""
 <div class="hledani1">
  <form action="?session={token}" method="post">
   <div><input class="hledani_jednoduche" type="text" name="prijmeni" value="" /><input type="hidden" name="action" value="hledani1" /></div>
   <div><input class="hledani_tlacitko" type="submit" name="tlacitko" value="Najít v příjmení" /><input type="hidden" name="action" value="hledani1" /></div>
  </form>
 </div>
 <div class="hledani2">
  <form action="?session={token}" method="post">
   <div class="hledani2_div"><span>Příjmení začíná</span><input class="hledani_bezne" type="text" name="prijmeni" value="" /><input type="hidden" name="action" value="hledani2" /></div>
   <div class="hledani2_div"><span>Jméno začíná</span><input class="hledani_bezne" type="text" name="jmeno" value="" /></div>
   <div class="hledani2_div"><span>Rok narození</span><input class="hledani_bezne_rok" type="text" name="doroku" value="" /><div class="hledani_bezne_span">-</div><input class="hledani_bezne_rok" type="text" name="odroku" value="" /></div>
   <div style="clear:both"><input class="hledani_tlacitko" type="submit" name="tlacitko" value="Najít" /><input type="hidden" name="action" value="hledani2" /></div>
  </form>
 </div>"""


def entry_html(token: str) -> str:
    return page_shell(forms_html(token), token)


def empty_html(token: str) -> str:
    return page_shell(f"<div>{EMPTY}</div>" + forms_html(token), token)


def results_html(token: str, total: int, rows: list[tuple[int, str, str, int | None]], offset: int, per_page: int = ROWS_PER_PAGE) -> str:
    """rows: (record number, surname, given name, birth year) of ONE page; `offset` is the number of records before it."""
    prev = (f'<a href="?session={token}&amp;action=list&amp;od={max(0, offset - per_page)}">&lt;&lt; Předchozí</a>' if offset else "&lt;&lt; Předchozí")
    nxt = (f'<a href="?session={token}&amp;action=list&amp;od={offset + per_page}">Další &gt;&gt;</a>' if offset + per_page < total else "Další &gt;&gt;")
    body = [f'<div class="pocet_zaznamu">Počet nalezených záznamů:{total}</div><div class="menu"><span>{prev}</span>'
            f'<span><a href="?session={token}&amp;action=novy_dotaz">Nový dotaz</a></span><span>&nbsp;</span><span>{nxt}</span></div>',
            ' <table class="table_seznam">', '  <tr class="table_zahlavi">',
            '   <td class="table_pcislo">p. číslo</td>',
            f'   <td class="table_prijmeni"><a href="?session={token}&amp;action=list&amp;razeni=prijmeni">příjmení</a></td>',
            '   <td class="table_jmeno">jméno</td><td class="table_rok">rok narození</td>', '  </tr>']
    for record, surname, given, year in rows:
        body.append(f'  <tr class="zaznamy">\n   <td class="table_pcislo">{record}</td>\n'
                    f'   <td class="table_prijmeni"><a href="?session={token}&amp;action=image&amp;record={record}">{html.escape(surname)}</a></td>\n'
                    f'   <td class="table_jmeno">{html.escape(given)}</td>\n   <td class="table_rok">{year if year else ""}</td>\n  </tr>')
    body.append(" </table>")
    return page_shell("\n".join(body), token)


def detail_html(token: str, karton: int, folium: int, total: int, record: int | None, people=None) -> str:
    people = FORMS[(karton, folium)] if people is None else people
    name = f"images/{karton:08d}-{folium:08d}.jpg"
    link = f"?action=link&amp;ref={REF}&amp;karton={karton}&amp;folium={folium}"
    where = f", záznam č.{record}" if record else ", záznam č.1"
    members = "".join(f"<div><strong>{html.escape(display(p))}</strong></div>" if i == 0 else f"<div>{html.escape(display(p))}</div>"
                      for i, p in enumerate(people))
    body = (f'<div class="pocet_zaznamu">Počet nalezených záznamů:{total}{where}</div><div class="menu"><span>&lt;&lt; Předchozí</span>'
            f'<span><a href="?session={token}">Nový dotaz</a></span><span><a href="?session={token}&amp;action=list&amp;od=0">Seznam</a></span></div>'
            f'<div class="obrazek">\n <img src="{name}" id="obrazek_prihlasky" style="width:100%;position:relative;left:0%;" alt="konskripce" />\n'
            f' <!-- pozn.: {name} -->\n</div><div class="citace">Citace: {CITATION.format(k=karton, f=folium)}</div>'
            f'<p class="link">Link: <a href="{link}">http://digi.nacr.cz/prihlasky2/index.php?action=link&amp;ref={REF}&amp;karton={karton}&amp;folium={folium}</a></p>'
            f'<div id="menu_obrazku"><span id="velikost_obrazku">100%</span></div>\n'
            f'<div class="seznam_lidi">{members}</div>')
    return page_shell(body, token)


def scan_jpeg(karton: int, folium: int) -> bytes:
    image = Image.new("RGB", SCAN_SIZE, (238, 232, 214))
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), f"SYNTHETIC TEST IMAGE - karton {karton}, obraz {folium} - NOT AN ARCHIVAL RECORD", fill=(20, 20, 20))
    draw.rectangle(MARKER, fill=(110, 145, 180))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


class DemoPrihlasky:
    """The application. Sessions live in memory: a token this server did not issue starts a NEW session that lists the whole
    index (as the live application does), so a client must notice that the token in the links changed."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        self.forms: list[dict] = []          # every POSTed search
        self.sessions: dict[str, list] = {}
        self.issued = 0
        self.per_page = ROWS_PER_PAGE
        self.break_structure = False         # rename the result table, as a redesign would
        self.shift_details = False           # answer every detail request with the NEXT record's form (a page that does not belong to the row)
        self.after_search = None             # called after a search has been answered (tests use it to lose the sessions)
        self.robots = "User-Agent: *\nDisallow: /\n"

    def new_session(self, rows=None) -> str:
        self.issued += 1
        token = token_for(self.issued)
        self.sessions[token] = rows if rows is not None else index()
        return token

    def forget(self):
        """The server loses every session (a restart or a timeout)."""
        self.sessions.clear()

    def catalog_requests(self) -> list[str]:
        return [u for _, u in self.requests if "/robots.txt" not in u]

    @property
    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        self.requests.append((request.method, str(request.url)))
        url = urlsplit(str(request.url))
        if url.hostname != "digi.nacr.cz" or url.scheme != "http":
            return httpx.Response(404, text="Synthetic fixture has no such host")
        if url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        m = re.fullmatch(r"/prihlasky2/images/(\d{8})-(\d{8})\.jpg", url.path)
        if m:
            key = (int(m[1]), int(m[2]))
            if key not in FORMS:
                return httpx.Response(404, text="Not found")
            return httpx.Response(200, content=scan_jpeg(*key), headers={"content-type": "image/jpeg"})
        if url.path not in ("/prihlasky2/", "/prihlasky2/index.php"):
            return httpx.Response(404, text="Synthetic fixture has no such endpoint")
        query = parse_qs(url.query)
        token = query.get("session", [None])[0]
        action = query.get("action", [None])[0]
        if request.method == "POST":
            return self.search(token, parse_qs(request.content.decode(), keep_blank_values=True))
        if action == "link":
            key = (int(query["karton"][0]), int(query["folium"][0]))
            if key not in FORMS or query.get("ref", [""])[0] != REF:
                return httpx.Response(200, text=page_shell("<div>Neplatný odkaz</div>", token_for(0)), headers=HTML)
            return self.page(detail_html(self.new_session([]), key[0], key[1], 0, None))
        if token is None:
            return self.page(entry_html(self.new_session()))
        if token not in self.sessions:
            token = self.new_session()               # unknown token: a fresh session over the whole index, links carry the new token
        rows = self.sessions[token]
        if action == "list":
            return self.page(self.listing(token, rows, int(query.get("od", ["0"])[0])))
        if action == "image":
            record = int(query["record"][0])
            if not 1 <= record <= len(rows):
                return self.page(empty_html(token))
            karton, folium = rows[(record if self.shift_details else record - 1) % len(rows)][3]
            return self.page(detail_html(token, karton, folium, len(rows), record))
        return self.page(entry_html(token))

    def page(self, text: str) -> httpx.Response:
        if self.break_structure:
            text = text.replace("table_seznam", "table_list").replace("seznam_lidi", "people").replace("pocet_zaznamu", "count")
        return httpx.Response(200, text=text, headers=HTML)

    def search(self, token, form):
        self.forms.append({k: v[0] for k, v in form.items()})
        f = self.forms[-1]
        if token not in self.sessions:
            token = self.new_session()
        if f.get("action") != "hledani2":
            return self.page(empty_html(token))
        low, high = (int(f["odroku"]), int(f["doroku"])) if f.get("odroku") and f.get("doroku") else (None, None)
        rows = [r for r in index() if r[0].startswith(f.get("prijmeni", "")) and r[1].startswith(f.get("jmeno", ""))
                and (low is None or (r[2] is not None and low <= r[2] <= high))]
        self.sessions[token] = rows
        answer = self.page(empty_html(token)) if not rows else self.page(self.listing(token, rows, 0))
        if self.after_search:
            self.after_search()
        return answer

    def listing(self, token, rows, offset):
        window = [(offset + i + 1, r[0], r[1], r[2]) for i, r in enumerate(rows[offset:offset + self.per_page])]
        return results_html(token, len(rows), window, offset, self.per_page)
