"""Entirely synthetic census-records app shaped like the live one. NEVER real MZA data."""
import html
import json
import re
import secrets
from urllib.parse import parse_qs

import httpx

from .catalog import normalize

APP = "https://www.mza.cz/scitacioperaty"
BLANSKO, HB = "226201010", "226101010"
(SET_LHOTA_1890, SET_PASEKA_1890, SET_LHOTA_1921, SET_HB_1890, SET_NO_SCANS, SET_LHOTA_1921_TEXT,
 SET_YEAR_UNKNOWN) = 9001, 9002, 9003, 9004, 9005, 9006, 9007
BIG_PLACE = "Demo Velkoves"
# id: (obec, cobec, od-do, rozpis, year, archive name, archive code, fund, code, scan numbers)
SETS = {
    SET_LHOTA_1890: ("Demo Lhota", "", "1-40", "1-38, 40", 1890, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9001", [10, 20, 30]),
    SET_PASEKA_1890: ("Demo Lhota", "Demo Paseka", "1-12", "1-12", 1890, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9002", [10, 20]),
    SET_LHOTA_1921: ("Demo Lhota", "", "1-45", "1-45", 1921, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9003", [10]),
    SET_HB_1890: ("Demo Lhota", "", "1-20", "1-20", 1890, "Havlíčkův Brod", HB, "SYNTHETIC Okresní úřad Jinde", "HB9004", [10]),
    SET_NO_SCANS: ("Demo Lhota", "", "1-40", "1-40", 1900, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9005", []),
    # Malformed/unusual "Rok sčítání" header text, used to test fail-closed images_available.
    SET_LHOTA_1921_TEXT: ("Demo Sumar Test", "", "1-10", "1-10", 1921, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9006", [10]),
    SET_YEAR_UNKNOWN: ("Demo Sumar Test", "", "1-10", "1-10", 1900, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", "BK9007", [10]),
}
SETS.update({9100 + i: (BIG_PLACE, "", "1-9", "1-9", 1880, "Blansko", BLANSKO, "SYNTHETIC Okresní úřad Testov", f"BK91{i:02d}", [10])
             for i in range(25)})
PLACES = {"obec": ["Demo Lhota", "Demo Velkoves", "Brno - Královo Pole"], "cobec": ["Demo Paseka"]}
# Detail-page-only override of the "Rok sčítání" header text (search-form matching still uses the plain year above).
YEAR_DISPLAY = {SET_LHOTA_1921_TEXT: "1921 (sumář)", SET_YEAR_UNKNOWN: ""}


def _field(label, value, strong=False):
    v = f"<strong>{html.escape(value)}</strong>" if strong else html.escape(value)
    return (f'<div class="col-12 col-sm-4"><span class="small text-muted ">{label}</span>'
            f"<p>{v}</p></div>")


def _item(set_id):
    obec, cobec, cp, rozpis, year, archive, _, fund, code, _ = SETS[set_id]
    return ('<li class="list-group-item"><div class="row">' + _field("Město/obec", obec, True)
            + _field("Číslo popisné od-do", cp) + _field("Archiv", archive) + '</div><div class="row">'
            + _field("Část obce/osada", cobec, True) + _field("Rozpis čísel popisných", rozpis)
            + _field("Rok sčítání", str(year)) + '</div><div class="row">' + _field("Ulice", "", True)
            + _field("Název fondu", fund) + _field("Kód sady", code + " ") + '</div>'
            f'<div class="row"><div class="col"><a href="{APP}/digisada/detail/{set_id}" '
            'class="btn btn-outline-secondary">Zobrazit</a></div></div></li>')


def results_page(ids, offset, total):
    items = "".join(_item(i) for i in ids[offset:offset + 10])
    paging = "".join(f'<li class="page-item"><a class="page-link" href="{APP}/digisada/advanced_search/{o}">{o // 10 + 1}</a></li>'
                     for o in range(0, total, 10) if total > 10 and o != offset)
    shown = f"{offset + 1} - {min(offset + 10, total)}" if total else "0 - 0"
    return (f'<html><body><form action="{APP}/digisada/do_advanced_search" method="post"></form>'
            '<div class="card"><h2 class="card-header">Vyhledané záznamy</h2>'
            f'<ul class="list-group">{items}</ul><div class="card-footer"><ul class="pagination">{paging}</ul>'
            f"<span>Zobrazené záznamy: {shown}, celkem: {total}</span></div></div>"
            "<p>SYNTHETIC FIXTURE</p></body></html>")


def detail_page(set_id):
    obec, cobec, cp, rozpis, year, archive, archive_code, fund, code, scans = SETS[set_id]
    year_text = YEAR_DISPLAY.get(set_id, str(year))
    def nav(label, value):
        return (f'<li class="nav-item px-3"><span class="small font-italic">{label}</span>'
                f'<br><span class="font-weight-bolder">{html.escape(value)}</span></li>')
    header = ('<div id="digisada-header"><ul class="nav mb-2">' + nav("Kód sady", code + " ") + nav("Rok sčítání", year_text)
              + nav("Archiv", archive) + nav("Název fondu", fund) + '</ul><ul class="nav">' + nav("Město/obec", obec)
              + nav("Část obce/osada", cobec)
              + '<li class="nav-item px-3"><span class="small font-italic">Ulice</span><div class="font-weight-bolder limited-width"></div></li>'
              + nav("Číslo popisné od-do", cp)
              + '<li class="nav-item px-3"><span class="small font-italic">Rozpis čísel popisných</span>'
              f'<div class="font-weight-bolder limited-width"><a class="clickable" data-toggle="popover" data-content="{html.escape(rozpis)}">'
              f"{html.escape(rozpis)}</a></div></li></ul></div>")
    rows = [("Archiv", f"{archive_code} (SOkA {archive})"), ("Název fondu", fund), ("Číslo NAD", "999"),
            ("Číslo pomůcky", "1"), ("Inventární číslo", "77"), ("Signatura", ""), ("Kód sady", code + " "),
            ("Rok sčítání", str(year)), ("Politický okres", "SYNTHETIC Testov")]
    extra = "".join(f'<div class="row"><div class="col-4 col-md-3 text-muted">{k}</div>'
                    f'<div class="col-8 col-md-9">{html.escape(v)}&nbsp;</div></div>' for k, v in rows)
    # Always emit CreateSeadragon, even with empty arrays when there are no scans:
    # a genuinely missing manifest script is a distinct, unparseable page, tested separately.
    base = f"/srv/DEMO/{archive_code}-{code}/{archive_code}-{code}"
    sources = [f"https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom={base}-{n:05d}.jp2.dzi" for n in scans]
    names = [f"{archive_code}-{code}-{n:05d}.jp2" for n in scans]
    script = ("<script>var viewer = CreateSeadragon(" + json.dumps(sources).replace("/", "\\/") + ","
              + json.dumps(names) + ",0);</script>")
    return (f'<html><body><main>{header}<div id="pill_images">{script}</div>'
            f'<div id="pill_data_extra" class="tab-pane fade"><div class="mb-3"><h3>Identifikační údaje</h3>{extra}</div></div>'
            "<p>SYNTHETIC FIXTURE</p></main></body></html>")


def _matches(set_id, form):
    obec, cobec, _, _, year, _, archive_code, *_ = SETS[set_id]
    get = lambda k: (form.get(k) or [""])[0]
    return ((not get("obec") or normalize(get("obec")) == normalize(obec))
            and (not get("cobec") or normalize(get("cobec")) == normalize(cobec))
            and (not get("archiv") or get("archiv") == archive_code)
            and (not get("rscitan") or get("rscitan") == str(year)))


def handle_census(request: httpx.Request, sessions: dict) -> httpx.Response:
    path = request.url.path
    page = {"content-type": "text/html; charset=UTF-8"}
    if path == "/scitacioperaty/digisada/autocomplete/":
        if request.headers.get("x-requested-with") != "XMLHttpRequest":
            return httpx.Response(200, text="Na tuto stránku nelze přistupovat přímo.", headers={"content-type": "text/plain"})
        q = parse_qs(request.url.query.decode())
        field, term = q.get("field", [""])[0], normalize(q.get("term", [""])[0])
        kind = "město/obec" if field == "obec" else "část obce/osada"
        return httpx.Response(200, json=[{"value": v, "label": f"{v} ({kind} - česky)"}
                                         for v in PLACES.get(field, []) if normalize(v).startswith(term)])
    if request.method == "POST" and path == "/scitacioperaty/digisada/do_advanced_search":
        sid = secrets.token_hex(8)
        sessions[sid] = parse_qs(request.content.decode(), keep_blank_values=True)
        return httpx.Response(302, headers={"location": f"{APP}/digisada/advanced_search",
                                            "set-cookie": f"ci_session={sid}; path=/; HttpOnly; SameSite=Lax"})
    m = re.fullmatch(r"/scitacioperaty/digisada/advanced_search(?:/(\d+))?", path)
    if m:
        c = re.search(r"ci_session=(\w+)", request.headers.get("cookie") or "")
        form = sessions.get(c[1]) if c else None
        if form is None:
            return httpx.Response(200, text="<html><body><form></form><p>SYNTHETIC form without results</p></body></html>", headers=page)
        ids = [i for i in SETS if _matches(i, form)]
        return httpx.Response(200, text=results_page(ids, int(m[1] or 0), len(ids)), headers=page)
    m = re.fullmatch(r"/scitacioperaty/digisada/detail/(\d+)", path)
    if m and int(m[1]) in SETS:
        return httpx.Response(200, text=detail_page(int(m[1])), headers=page)
    return httpx.Response(404, text="Synthetic census fixture has no such page", headers=page)
