"""Entirely synthetic responses shaped like the MZA indication sketches application (server-rendered search table, detail page with
the viewer script, static 217 x 174 px JPEG tiles). Katastrs, ids and images are invented; NEVER real archive data.
Excluded from the desktop bundle (file name starts with "demo")."""
import html
import io
import math
from urllib.parse import parse_qs

import httpx
from PIL import Image, ImageDraw

from .catalog import normalize

APP = "/indikacniskici"
HTML = {"content-type": "text/html; charset=UTF-8"}
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TILE = (217, 174)
ROBOTS = "User-agent: *\nDisallow: /wp-admin/\nDisallow: /wp-includes/\n"
# signature, Czech name, German name, district, [(year, sheet, note, other names, other municipalities)]
KATASTRS = [
    ("0901", "Demovice", "Demowitz", "Znojmo", [(1825, 0, None, None, None), (1876, 0, None, None, None)]),
    ("0902", "Demovice", "Demowitz", "Vyškov", [(1827, 0, None, None, "Podlesí (Unterwald)")]),
    ("0903", "Demovice u Lhoty", "Demowitz bei Lhota", "Jihlava", [(1835, 0, "neúplná", None, None)]),
    ("0904", "Testov-Horní", "Ober Testow", "Brno-město", [(1911, 0, None, "Horní Testov", None), (1911, 1, "neúplná", None, None),
                                                            (0, 0, "Bez data", None, None)]),
    ("0905", "Mezistrání", "Mittelwand", "Prostějov", [(1830, 0, None, None, None)]),
]
# Sheets whose grid differs from the default: (cols, rows, front tiles, back tiles) keyed by the MOR id
DEFAULT_GRID = (2, 2, ["A01A", "A01B", "A02A"], ["R01A", "R01B", "R02A"])
GRIDS = {"MOR090419111": (1, 1, ["A01A"], [])}          # a sheet with no back side (no R tiles)
NUMBERS = {}                                          # MOR id -> numeric id the site redirects to
ROWS = []
for _sig, _cz, _de, _district, _sketches in KATASTRS:
    for _year, _sheet, _note, _names, _others in _sketches:
        _mor = f"MOR{_sig}{_year:04d}{_sheet}"
        NUMBERS[_mor] = 700 + len(ROWS)
        ROWS.append((_mor, _sig, _cz, _de, _district, _year, _note, _names, _others))
BY_NUMBER = {n: m for m, n in NUMBERS.items()}


def grid_of(mor):
    return GRIDS.get(mor, DEFAULT_GRID)


def wrap(inner: str, script: str = "") -> str:
    return ('<!DOCTYPE html><html lang="cs"><head><meta charset="utf-8"><title>SYNTHETIC Indikační skici</title></head><body>'
            f'<main id="main" role="main">{inner}</main>{script}</body></html>')


def _tip(label: str, text: str) -> str:
    title = html.escape(f"<strong>{label}</strong><br>{text}", quote=True)
    return f'<span class="pr-1" data-toggle="tooltip" data-html="true" title="{title}"><i class="far fa-comment-alt"></i></span>'


def _row(r) -> str:
    mor, sig, cz, de, district, year, note, names, others = r
    cells = "".join(f'<td class="clickable" onclick="window.location=\'https://www.mza.cz{APP}/skica/detail/{mor}\';">{html.escape(v)}</td>'
                    for v in (mor, sig, cz, de, district, str(year)))
    tips = "".join(_tip(label, text) for label, text in (("Poznámka", note), ("Další názvy", names), ("Další obce", others)) if text)
    return (f"<tr>{cells}<td class=\"px-1\">{tips}</td><td class=\"px-1\"></td><td class=\"px-1\"></td>"
            f'<td class="px-1 text-center"><a href="https://www.mza.cz{APP}/skica/detail/{mor}">map</a></td>'
            '<td class="px-1 text-center"><a href="https://ags.cuzk.cz/archiv/?xy=-1,-1">ČÚZK</a></td></tr>')


def search_page(rows, page: int, size: int = 20) -> str:
    shown = rows[(page - 1) * size:page * size]
    head = ("<thead><tr><th>Identifikátor</th><th>Signatura</th><th>Název katastru</th><th style=\"min-width: 12em;\">Katastr (německy)</th>"
            "<th>Okres</th><th>Rok</th><th colspan=\"3\">Ostatní</th><th>web MZA</th><th>web ČÚZK</th></tr></thead>")
    if rows:
        footer = f"<span>Zobrazené záznamy: {(page - 1) * size + 1} - {(page - 1) * size + len(shown)}, celkem: {len(rows)}</span>"
    else:
        footer = "<span>Nebyly nalezeny žádné vyhovující záznamy.</span>"
    return wrap('<div class="card-header h5">Vyhledané skici</div><div class="table-responsive">'
                f'<table id="table" class="table">{head}<tbody>{"".join(_row(r) for r in shown)}</tbody></table></div>'
                f'<div class="card-footer">{footer}</div>')


def detail_page(mor: str, side: str) -> str:
    _, sig, cz, de, district, year, note, names, others = next(r for r in ROWS if r[0] == mor)
    cols, rows, front, back = grid_of(mor)
    number = NUMBERS[mor]
    other = "FRONT" if side == "R" else "BACK"

    def item(label, value):
        return (f'<li class="nav-item px-3"><span class="small font-italic">{label}</span><br>'
                f'<span class="font-weight-bolder">{html.escape(value)}</span></li>')
    tips = "".join(_tip(label, text) for label, text in (("Poznámka", note), ("Další názvy", names), ("Další obce", others)) if text)
    header = ('<div class="card"><div class="card-header bg-dark text-white"><ul class="nav">'
              + item("Identifikátor", mor) + item("Signatura", sig) + item("Název katastru", cz) + item("Katastr (německy)", de)
              + item("Okres", district) + item("Rok", str(year)) + f'<li><span class="small font-italic">Ostatní</span><br>{tips}</li></ul></div>'
              f'<div class="card-body py-2"><a href="https://www.mza.cz{APP}/skica/detail/{number}?site={other}" class="btn">opačná strana</a>'
              f'<a href="https://www.mza.cz{APP}/skica/detail/{number}?site={"BACK" if side == "A" else "FRONT"}&mode=ORDERING">objednat</a></div></div>')
    tiles = ", ".join(f'"{t}"' for t in front + back)
    script = f'''<script src="https://www.mza.cz{APP}/assets/openseadragon/openseadragon.min.js"></script>
<script>
    var dragon;
    var letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    var siteLetter = "{side}";
    var tiles = [{tiles}];
    function getMZATileUrl(level, x, y) {{
        var url = "https://www.mza.cz{APP}/data/obrazky/{mor}/Img/";
        if (level == 0) {{ return url + "map.jpg"; }}
        return null;
    }}
    function createSeadragon() {{
        var rows = {rows};
        var cols = {cols};
        dragon = OpenSeadragon({{
            id: "openseadragon",
            maxZoomLevel: 50,
            tileSources: {{
                width: cols * 12 * {TILE[0]},
                height: rows * 12 * {TILE[1]},
                minLevel: siteLetter == "A" ? 0 : 1,
                maxLevel: 4,
                getTileWidth: function(level) {{
                    return level > 0 ? {TILE[0]} : cols * 54;
                }},
                getTileHeight: function(level) {{
                    return level > 0 ? {TILE[1]} : rows * 43;
                }},
                getLevelScale: function(level) {{
                    var levelScales = [(43 / 174) / 12, 1 / 12, 1 / 4, 1 / 2, 1];
                    return levelScales[level];
                }}
            }}
        }});
    }}
</script>'''
    return wrap(header + '<div id="openseadragon"></div>', script)


def tile_color(level: int, x: int, y: int) -> tuple[int, int, int]:
    """Colour of a whole synthetic tile: distinct for neighbouring tiles of one level, and level-dependent."""
    return (40 + 50 * (x % 4) + 5 * level, 40 + 50 * (y % 4) + 5 * level, 60 + 30 * level)


def tile_image(level: int, x: int, y: int) -> bytes:
    image = Image.new("RGB", TILE, tile_color(level, x, y))
    ImageDraw.Draw(image).text((6, 6), f"SYNTH L{level} {x},{y}", fill=(255, 255, 255))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def parse_tile(name: str) -> tuple[int, int, int]:
    """(level, x, y) from a tile file name such as A01B-C2-B1-A2 (mirror image of the viewer's naming)."""
    parts = name.split("-")
    level = len(parts)
    first, rest = parts[0], parts[1:]
    x, y = LETTERS.index(first[3]), int(first[1:3]) - 1
    for i, part in enumerate(rest):
        n = 3 if i == 0 else 2          # level 2 splits a level-1 tile 3 x 3, levels 3 and 4 split 2 x 2
        x, y = x * n + LETTERS.index(part[0]), y * n + int(part[1]) - 1
    return level, x, y


class DemoSketches:
    def __init__(self):
        self.log = []
        self.last_ua = None

    def _matching(self, query):
        nazev = normalize(query.get("nazev", [""])[0])
        okres = query.get("okres", [""])[0]
        signatura = query.get("signatura", [""])[0]
        return [r for r in ROWS
                if (not nazev or normalize(r[2]).startswith(nazev) or any(normalize(n.strip()).startswith(nazev) for n in r[3].split(",")))
                and (not okres or r[4] == okres) and (not signatura or r[1] == signatura)]

    def handle(self, request):
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        self.log.append(path + ("?" + request.url.query.decode() if request.url.query else ""))
        self.last_ua = request.headers.get("user-agent")
        if path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS, headers={"content-type": "text/plain"})
        if path == APP + "/skica":
            page = int(query.get("page", ["1"])[0])
            return httpx.Response(200, text=search_page(self._matching(query), page), headers=HTML)
        if path.startswith(APP + "/skica/detail/"):
            ident = path.rsplit("/", 1)[1]
            if ident in NUMBERS:
                return httpx.Response(302, headers={**HTML, "location": f"https://www.mza.cz{APP}/skica/detail/{NUMBERS[ident]}",
                                                    "set-cookie": "ci_session=synthetic; path=/; HttpOnly"})
            if ident.isdigit() and int(ident) in BY_NUMBER:
                side = "R" if query.get("site") == ["BACK"] else "A"
                return httpx.Response(200, text=detail_page(BY_NUMBER[int(ident)], side), headers=HTML)
            return httpx.Response(404, text="<html>not found</html>", headers=HTML)
        parts = path.split("/")
        if path.startswith(APP + "/data/obrazky/") and len(parts) in (7, 8) and parts[5] == "Img":
            mor, folder, name = parts[4], parts[6], (parts[7] if len(parts) == 8 else "").removesuffix(".jpg")
            if mor in NUMBERS and parts[6:] == ["map.jpg"]:
                cols, rows = grid_of(mor)[:2]
                out = io.BytesIO()
                Image.new("RGB", (cols * 54, rows * 43), (200, 190, 170)).save(out, format="JPEG")
                return httpx.Response(200, content=out.getvalue(), headers={"content-type": "image/jpeg"})
            if mor in NUMBERS and folder in ("Preview", "little", "Small", "Normal"):
                _, _, front, back = grid_of(mor)
                if name.split("-")[0] in front + back:
                    level, x, y = parse_tile(name)
                    return httpx.Response(200, content=tile_image(level, x, y), headers={"content-type": "image/jpeg"})
            return httpx.Response(404, text="<html>not found</html>", headers=HTML)
        return httpx.Response(404, text="Synthetic fixture has no such endpoint", headers=HTML)
