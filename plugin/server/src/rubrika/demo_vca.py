"""Entirely synthetic responses shaped like the East Bohemian ARON (listview API, DZI tiles). NEVER real archive data."""
import io
import json
import math
import re

import httpx
from PIL import Image

from .catalog import normalize
from .demo_aron import _item, file_id, u
from .sources import UUID, VCA_API, VCA_HOST, VCA_IMAGES

PLACE_CITY, PLACE_CITY_DUP = u(0xE1), u(0xE2)
FUND_REGISTERS = u(0xF1)
UNIT_BIRTHS, UNIT_DEATHS, UNIT_LAND = u(0xA1), u(0xA2), u(0xA3)
UNIT_CENSUS, UNIT_LANDBOOK, UNIT_CHRONICLE = u(0xA4), u(0xA5), u(0xA6)
UNIT_LANDBOOK_GEO, UNIT_LANDBOOK_DECOY = u(0xA8), u(0xA9)
SERIES = u(0xB1)
TILE, OVERLAP = 254, 1
MISSING_TILES: set[tuple[int, int, int]] = set()     # (level, x, y) answered with 404; tests patch this set

PLACE_CORP, PLACE_CASTLE = u(0xE3), u(0xE4)
ENTITIES = {
    # Ranked by the synthetic "score" before the town, like the live ENTITY search does.
    PLACE_CORP: "Demo Hradec (Testov : působnost od 1349-) SYNTHETIC",
    PLACE_CASTLE: "Demo Hradec (Testov, TEST, Česko : zámek) SYNTHETIC",
    PLACE_CITY: "Demo Hradec (Testov, TEST, Česko) SYNTHETIC",
    PLACE_CITY_DUP: "Demo Hradec (Jinov, JINY, Česko) SYNTHETIC",
}
DESCRIPTIONS = {PLACE_CITY: "SYNTHETIC obec Demo Hradec v okrese Testov", PLACE_CORP: "SYNTHETIC korporace",
                PLACE_CASTLE: "SYNTHETIC zámek"}
# uuid: (name, description, unit type, scans, places)
UNITS = {
    UNIT_BIRTHS: ("SYNTHETIC archiv: Sbírka matrik, sign.: 1-1 (poř.č. 1), 1780-1800",
                  "SYNTHETIC římskokatolická matrika narozených; farnost Demo Hradec; obce: Demo Hradec, Demo Ves",
                  "matrika", 3, [PLACE_CITY]),
    UNIT_DEATHS: ("SYNTHETIC archiv: Sbírka matrik, sign.: 1-2 (poř.č. 2), 1801-1820",
                  "SYNTHETIC římskokatolická matrika zemřelých; farnost Demo Hradec; obce: Demo Hradec",
                  "matrika", 2, [PLACE_CITY]),
    UNIT_LAND: ("SYNTHETIC archiv: Velkostatek Demo, pozemková kniha, 1700-1750",
                "SYNTHETIC pozemková kniha velkostatku Demo", "úřední kniha", 1, [PLACE_CITY]),
    # Live shapes: census "..., poř. č.: 182, 1890" + "<place> čp. 1-50"; town land books "inv. č.: 88, 1605-1699".
    UNIT_CENSUS: ("SYNTHETIC archiv: Okresní úřad Demo I, poř. č.: 182, 1890", "Demo Hradec čp. 1-50",
                  "sčítací operát", 4, [PLACE_CITY]),
    # Live: town land books are NOT geo-linked (REG~GEO~REF absent); only the fund name "Archiv města <Town>" ties them.
    UNIT_LANDBOOK: ("SYNTHETIC archiv: Archiv města Demo Hradec, inv. č.: 88, 1605-1699", "Kniha trhová (tzv. zelená)",
                    "evidence nemovitostí", 2, []),
    UNIT_LANDBOOK_GEO: ("SYNTHETIC archiv: Velkostatek Demo, inv. č.: 5, 1700-1710", "Kniha gruntovní",
                        "evidence nemovitostí", 1, [PLACE_CITY]),
    UNIT_LANDBOOK_DECOY: ("SYNTHETIC archiv: Archiv města Demo Hradecká Lhota, inv. č.: 9, 1700-1710", "Kniha gruntovní",
                          "evidence nemovitostí", 1, []),
    UNIT_CHRONICLE: ("SYNTHETIC archiv: Archiv města Demo Hradec, inv. č.: 91, 1850-1900", "Pamětní kniha města",
                     "úřední kniha", 1, [PLACE_CITY]),
}
_LEVELS: dict[tuple[int, int], tuple[Image.Image, Image.Image]] = {}


def _types(unit_type):
    return ["úřední kniha", unit_type] if unit_type == "matrika" else [unit_type]


def _storage(uuid):
    m = re.search(r"(?:sign\.|poř\. č\.|inv\. č\.):\s*([^,]+?)\s*(?:,|$)", UNITS[uuid][0])
    return m[1] if m else None


def _dating(uuid):
    return UNITS[uuid][0].rsplit(",", 1)[1].strip()


def fund_detail(uuid):
    return {"id": uuid, "name": "SYNTHETIC Státní oblastní archiv: Sbírka matrik", "type": "FUND", "parent": None,
            "digitalObjects": [],
            "parts": [{"id": None, "type": "PT~FUND~INFO", "value": None, "childParts": [],
                       "items": [_item("TITLE", "SYNTHETIC Sbírka matrik"), _item("CISLO~NAD", "0")]}]}


def unit_detail(uuid):
    name, description, unit_type, scans, places = UNITS[uuid]
    a, _, b = _dating(uuid).partition("-")
    b = b or a
    # Live shape: 'úřední kniha' first, then 'matrika' for registers, plus record types.
    types = _types(unit_type)
    records = ["záznamy narozených", "rejstřík narozených"] if unit_type == "matrika" else []
    desc = [_item("DIGITAL", "Ano"), _item("STORAGE", _storage(uuid) or ""), _item("NOTE", description),
            _item("UNIT~DATE", json.dumps({"from": f"{a}-01-01T00:00:00", "valueFromEstimated": False,
                                           "to": f"{b}-12-31T23:59:59", "valueToEstimated": False, "format": "Y-Y"})),
            _item("UNIT~DATE~TEXT", _dating(uuid))]
    parts = [{"id": None, "type": "PT~ARCH~DESC", "value": None, "items": desc,
              "childParts": [{"id": None, "type": "PT~ENTITY~ROLE", "value": None, "childParts": [],
                              "items": [_item("UNIT~TYPE", t) for t in types]},
                             {"id": None, "type": "PT~RECORD~TYPE", "value": None, "childParts": [],
                              "items": [_item("RECORD~TYPE", r) for r in records]}]},
             {"id": None, "type": "PT~ARCH~DESC~FUND", "value": None, "childParts": [],
              "items": [_item("FUND~REF", FUND_REGISTERS)] + [_item("REG~GEO~REF", p) for p in places]}]
    files = []
    for n in range(1, scans + 1):
        fid = file_id(uuid, n)
        files.append({"id": fid, "permalink": None, "order": n, "type": "TILE", "file": None, "name": None,
                      "referencedFile": f"https://{VCA_IMAGES}/tiles/00/00/{fid}/image.dzi",
                      "contentType": "application/octetstream", "size": 0})
    files.append({"id": file_id(uuid, 999), "permalink": None, "order": scans + 1, "type": "PUBLISHED", "file": None,
                  "name": "x.jpg", "referencedFile": "/mnt/images/x.jpg", "contentType": "image/jpeg", "size": 0})
    return {"id": uuid, "name": name, "type": "ARCH_DESC", "permalink": None,
            "parent": {"id": SERIES, "name": "SYNTHETIC Série", "type": "ARCH_DESC",
                       "parent": {"id": FUND_REGISTERS, "name": "SYNTHETIC Sbírka matrik", "type": "FUND", "parent": None}},
            "parts": parts,
            "digitalObjects": [{"id": uuid, "name": None, "permalink": None, "order": 1, "licenseCode": None,
                                "files": files}]}


def _tile(image: Image.Image, level: int, x: int, y: int) -> bytes | None:
    w, h = image.size
    top = math.ceil(math.log2(max(w, h)))
    if not 0 <= level <= top or (level, x, y) in MISSING_TILES:
        return None
    key = (id(image), level)
    if key not in _LEVELS or _LEVELS[key][0] is not image:
        factor = 2 ** (top - level)
        _LEVELS[key] = (image, image.resize((math.ceil(w / factor), math.ceil(h / factor)), Image.Resampling.LANCZOS))
    scaled = _LEVELS[key][1]
    box = (max(0, x * TILE - OVERLAP), max(0, y * TILE - OVERLAP),
           min(scaled.width, (x + 1) * TILE + OVERLAP), min(scaled.height, (y + 1) * TILE + OVERLAP))
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    out = io.BytesIO()
    scaled.crop(box).save(out, format="JPEG", quality=95)
    return out.getvalue()


def _imgx(request: httpx.Request, image: Image.Image) -> httpx.Response:
    path = request.url.path
    if request.method != "GET":
        return httpx.Response(404)
    if re.fullmatch(rf"/tiles/[0-9a-f]{{2}}/[0-9a-f]{{2}}/{UUID}/image\.dzi", path):
        xml = (f'<Image TileSize="{TILE}" Overlap="{OVERLAP}" Format="jpg" xmlns="http://schemas.microsoft.com/deepzoom/2008">'
               f'<Size Width="{image.width}" Height="{image.height}"/></Image>')
        return httpx.Response(200, text=xml, headers={"content-type": "text/xml"})
    m = re.fullmatch(rf"/tiles/[0-9a-f]{{2}}/[0-9a-f]{{2}}/{UUID}/image_files/(\d+)/(\d+)_(\d+)\.jpg", path)
    if m:
        data = _tile(image, *map(int, m.groups()))
        if data is not None:
            return httpx.Response(200, content=data, headers={"content-type": "image/jpeg"})
    return httpx.Response(404)


def _match(f: dict, unit_type: str) -> bool:
    """Honours the filters the client sends about a unit's type: UNIT~TYPE EQ and NOT (none of its children match)."""
    if f.get("operation") == "NOT":
        return not any(_match(c, unit_type) for c in f["filters"])
    if f.get("field") == "UNIT~TYPE":
        return f["value"] in _types(unit_type)
    return True


def _list(request: httpx.Request, posts: list) -> httpx.Response:
    body = json.loads(request.content)
    posts.append(body)
    filters = body["filters"]
    kind = next(f["value"] for f in filters if f.get("field") == "type")
    if kind == "ENTITY":
        words = normalize(next(f["value"] for f in filters if f.get("operation") == "FTX")).split()
        rows = [(e, label, None) for e, label in ENTITIES.items() if all(w in normalize(label) for w in words)]
    else:
        ors = next(f for f in filters if f.get("operation") == "OR")["filters"]
        wanted = {f["value"] for f in ors if f.get("field") == "REG~GEO~REF"}
        phrases = [normalize(f["value"].strip('"')) for f in ors if f.get("operation") == "FTX"]    # substring, like a phrase hit
        rows = [(x, v[0], v[1]) for x, v in UNITS.items()
                if (wanted & set(v[4]) or any(p in normalize(v[0]) for p in phrases)) and all(_match(f, v[2]) for f in filters)]
    if kind == "ENTITY":
        rows = [(e, n, DESCRIPTIONS.get(e)) for e, n, _ in rows]
    items = [{"id": i, "name": n, "description": d, "order": k} for k, (i, n, d) in enumerate(rows, 1)]
    offset, size = body["offset"], body["size"]
    return httpx.Response(200, json={"items": items[offset:offset + size], "count": len(items), "searchAfter": None})


def handle_vca(request: httpx.Request, posts: list, image: Image.Image) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if path == "/robots.txt":
        if host == VCA_HOST:
            return httpx.Response(200, text="<!doctype html><html><body>SYNTHETIC app shell</body></html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(404)
    if host == VCA_IMAGES:
        return _imgx(request, image)
    if request.method == "POST" and path == VCA_API + "/apu/listview":
        return _list(request, posts)
    if request.method == "GET":
        uuid = path.rsplit("/", 1)[-1]
        if path == f"{VCA_API}/apu/{uuid}":
            if uuid in UNITS:
                return httpx.Response(200, json=unit_detail(uuid))
            if uuid == FUND_REGISTERS:
                return httpx.Response(200, json=fund_detail(uuid))
        if path == f"{VCA_API}/script/citation/{uuid}" and uuid in UNITS:
            return httpx.Response(200, json={"citation": "SYNTHETIC Státní oblastní archiv; Sbírka matrik (NAD 0); "
                                                         f"sign. {(_storage(uuid) or '').split(' (')[0]}"})
    return httpx.Response(404, json={"message": "Synthetic VCA fixture has no such endpoint"})
