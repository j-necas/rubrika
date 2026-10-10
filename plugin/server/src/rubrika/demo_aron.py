"""Entirely synthetic ARON responses shaped like the live API. NEVER real MZA data."""
import json

import httpx

from .catalog import normalize


def u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012x}"


PLACE_LHOTA, PLACE_LHOTA_DUP, PLACE_DEMOVICE = u(0xE1), u(0xE2), u(0xE3)
FUND_LAND, FUND_CADASTRE, FUND_OTHER, FUND_D10 = u(0xF1), u(0xF2), u(0xF3), u(0xF4)
UNIT_LAND, UNIT_CADASTRE, UNIT_UNDATED, UNIT_LINK_ONLY, UNIT_LATE = u(0xA1), u(0xA2), u(0xA3), u(0xA4), u(0xA5)
UNIT_D10, D10_KATASTR = u(0xA6), u(0xB2)
SERIES = u(0xB1)

ENTITIES = {
    PLACE_LHOTA: "Demo Lhota (Testov, TEST, Česko : obec) SYNTHETIC",
    PLACE_LHOTA_DUP: "Demo Lhota (Testov, TEST, Česko : nižší sídelní jednotka) SYNTHETIC",
    PLACE_DEMOVICE: "Demovice (Testov, TEST, Česko : obec) SYNTHETIC",
}
# uuid: (title, mark, NAD)
FUNDS = {
    FUND_LAND: ("SYNTHETIC Pozemkové knihy", "C 17", "142"),
    FUND_CADASTRE: ("SYNTHETIC Tereziánský katastr", "D 4", "168"),
    FUND_OTHER: ("SYNTHETIC Velkostatek Testov", "F 999", "9999"),
    FUND_D10: ("SYNTHETIC Stabilní katastr – písemné operáty", "D 10", "1"),
}
# uuid: (title, fund, dating text, UNIT~DATE (from, to, estimated) | None, unit type, storage, places, scan numbers)
UNITS = {
    UNIT_LAND: ("SYNTHETIC Demo Lhota: pozemková kniha", FUND_LAND, "1700–1780", (1700, 1780, False),
                "pozemková kniha", "9001 kniha", [PLACE_LHOTA], [10, 20, 40]),
    UNIT_CADASTRE: ("SYNTHETIC Demo Lhota: tereziánský katastr", FUND_CADASTRE, "1754", (1754, 1754, False),
                    "úřední kniha", "9002 kniha", [PLACE_LHOTA], [10, 20]),
    UNIT_UNDATED: ("SYNTHETIC Demo Lhota: nedatovaný soupis", FUND_OTHER, None, None,
                   "úřední kniha", "9003 kniha", [PLACE_LHOTA], [10]),
    UNIT_LINK_ONLY: ("SYNTHETIC Demo Lhota: externí digitalizát", FUND_OTHER, "1760–1770", (1760, 1770, True),
                     "úřední kniha", "9004 kniha", [PLACE_LHOTA], []),
    UNIT_LATE: ("SYNTHETIC Demo Lhota: mladší pozemková kniha", FUND_LAND, "1850–1860", (1850, 1860, False),
                "pozemková kniha", "9005 kniha", [PLACE_LHOTA_DUP], [10]),
    # a D 10 unit is titled only by the kind of copy; the cadastral community is its parent (see PARENTS)
    UNIT_D10: ("Duplikát", FUND_D10, "1826–1878", (1826, 1878, False),
               "úřední kniha", "1250 fascikl", [PLACE_DEMOVICE], [10, 20, 30]),
}
PARENTS = {UNIT_D10: (D10_KATASTR, "Demovice (Demowitz), obec: Testov, okres: TEST SYNTHETIC")}
API = "/aron/api/aron"


def file_id(unit: str, n: int) -> str:
    return u(int(unit[-12:], 16) * 0x100000 + n)


def _cell(t, values):
    return [{"t": t, "v": values}]


def _item(t, v):
    return {"id": None, "type": t, "value": v, "visible": True, "href": None, "label": None}


def entity_row(uuid):
    return {"id": uuid, "t": "AP_G", "tn": None, "tn_link": None, "l": [_cell("N", [{"v": ENTITIES[uuid], "id": None}])]}


def unit_row(uuid):
    title, fund, dating, _, _, storage, places, _ = UNITS[uuid]
    lines = [_cell("N", [{"v": title, "id": None}])]
    if dating:
        lines.append(_cell("D", [{"v": dating, "id": None}]))
    lines += [_cell("J_G", [{"v": ENTITIES[p].split(" (")[0], "id": p} for p in places]),
              _cell("J_UC", [{"v": storage}]),
              _cell("J_F", [{"v": FUNDS[fund][0], "id": fund}])]
    if uuid in PARENTS:
        lines.append(_cell("J_C", [{"v": PARENTS[uuid][1], "id": PARENTS[uuid][0]}]))
    return {"id": uuid, "t": "A_IB", "tn": None, "tn_link": None, "l": lines}


def fund_detail(uuid):
    name, mark, nad = FUNDS[uuid]
    return {"id": uuid, "name": "MZA v Brně: " + name, "type": "FUND", "parent": None, "digitalObjects": [],
            "parts": [{"id": None, "type": "PT~FUND~INFO", "value": None, "childParts": [],
                       "items": [_item("TITLE", name), _item("FUND~MARK", mark), _item("CISLO~NAD", nad)]}]}


def unit_detail(uuid):
    title, fund, dating, date, unit_type, storage, places, scans = UNITS[uuid]
    desc = [_item("DIGITAL", "Ano"), _item("STORAGE", storage), _item("FORMAL~TITLE", "SYNTHETIC Grund Buch"),
            _item("NOTE", "SYNTHETIC; rejstřík: neobsahuje"), _item("PART", "1.")]
    if date:
        a, b, estimated = date
        desc.append(_item("UNIT~DATE", json.dumps({"from": f"{a}-01-01T00:00:00", "valueFromEstimated": estimated,
                                                   "to": f"{b}-12-31T23:59:59", "valueToEstimated": estimated,
                                                   "format": "Y-Y"})))
        desc.append(_item("UNIT~DATE~TEXT", dating))
    if not scans:
        desc.append(_item("DAO~LINK", "https://example.invalid/synthetic-viewer"))
    parts = [{"id": None, "type": "PT~ARCH~DESC", "value": None, "items": desc,
              "childParts": [{"id": None, "type": "PT~ENTITY~ROLE", "value": None, "childParts": [],
                              "items": [_item("UNIT~TYPE", unit_type)]}]},
             {"id": None, "type": "PT~ARCH~DESC~FUND", "value": None, "childParts": [],
              "items": [_item("FUND~REF", fund)] + [_item("REG~GEO~REF", p) for p in places]}]
    number = storage.split()[0]
    files = [{"id": file_id(uuid, n), "permalink": None, "order": i, "type": "TILE", "file": None,
              "name": f"216000010-DEMO-{number}-{n:05d}.jp2.dzi",
              "referencedFile": f"https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/DEMO/216000010-DEMO-{number}-{n:05d}.jp2.dzi",
              "contentType": "text/plain", "size": 0} for i, n in enumerate(scans, 1)]
    return {"id": uuid, "name": title, "type": "ARCH_DESC", "permalink": None,
            "parent": {"id": (PARENTS.get(uuid) or (SERIES,))[0], "name": (PARENTS.get(uuid) or (0, "SYNTHETIC Jednotlivé obce"))[1], "type": "ARCH_DESC",
                       "parent": {"id": fund, "name": "SYNTHETIC " + FUNDS[fund][0], "type": "FUND", "parent": None}},
            "parts": parts,
            "digitalObjects": [{"id": uuid, "name": None, "permalink": None, "order": 1, "licenseCode": None,
                                "files": files}] if scans else []}


def handle_aron(request: httpx.Request, posts: list) -> httpx.Response:
    path = request.url.path
    if request.method == "POST" and path == API + "/apu/listresults":
        body = json.loads(request.content)
        posts.append(body)
        filters = body["filters"]
        kind = next(f["value"] for f in filters if f.get("field") == "type")
        if kind == "ENTITY":
            words = normalize(next(f["value"] for f in filters if f.get("operation") == "FTX")).split()
            rows = [entity_row(e) for e, label in ENTITIES.items() if all(w in normalize(label) for w in words)]
        else:
            wanted = {f["value"] for f in next(f for f in filters if f.get("operation") == "OR")["filters"]}
            rows = [unit_row(x) for x, v in UNITS.items() if wanted & set(v[6])]
        offset, size = body["offset"], body["size"]
        return httpx.Response(200, json={"items": rows[offset:offset + size], "count": len(rows),
                                         "searchAfter": None, "aggregations": None})
    if request.method == "GET":
        uuid = path.rsplit("/", 1)[-1]
        if path == f"{API}/apu/{uuid}" and uuid in UNITS:
            return httpx.Response(200, json=unit_detail(uuid))
        if path == f"{API}/apu/{uuid}" and uuid in FUNDS:
            return httpx.Response(200, json=fund_detail(uuid))
        if path == f"{API}/script/citation/{uuid}" and uuid in UNITS:
            if uuid == UNIT_UNDATED:
                return httpx.Response(200, json={"citation": None})
            title, fund, *_ = UNITS[uuid]
            return httpx.Response(200, json={"citation": f"SYNTHETIC archiv; {FUNDS[fund][0]} ({FUNDS[fund][1]}); "
                                                         f"ukl. j. {UNITS[uuid][5]}; {title}"})
    return httpx.Response(404, json={"message": "Synthetic ARON fixture has no such endpoint"})
