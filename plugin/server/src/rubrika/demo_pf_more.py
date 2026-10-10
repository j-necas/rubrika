"""Synthetic Porta fontium official books (amtsbuch tab) and census (census tab), shaped like the live Drupal pages.
Places, books and images are invented; NEVER real archive data."""
import html
import json
import re

import httpx

from .catalog import normalize

NID0 = 91000000
SOA = "Plzeň (státní archiv)"
DOM = "Domažlice (státní archiv)"
BOOK_TYPES = {"ratsprotokoll": "radní protokol", "grundbuch": "pozemková kniha", "urbar": "urbář", "stadtbuch": "městská kniha"}
# slug, title, archive, places [(name, german)], dating, book type key, signature, fund title, fund mark, images
AMTSBUCH = [
    ("soap-pn/vs-demolov/k001", "Pozemková kniha", SOA, [("Demolov", "Demolau"), ("Horní Demolov", "Ober-Demolau")], "1764–1853",
     "grundbuch", "K 1", "Velkostatek Demolov", "Vs Demolov", 3),
    ("soap-pn/vs-demolov/k002", "Urbář", SOA, [("Demolov", "Demolau")], "1650-1700, 1712", "urbar", "K 2", "Velkostatek Demolov",
     "Vs Demolov", 2),
    ("soap-pn/vs-demolov/k003", "Kniha spisů", SOA, [("Demolov", "Demolau"), ("Testov", "Teststadt")], "kolem 1800", "stadtbuch",
     "K 3", "Velkostatek Demolov", "Vs Demolov", 2),
    ("soap-pn/vs-testov/k010", "Pozemková kniha", SOA, [("Testov", "Teststadt")], "1790–1850", "grundbuch", "K 10",
     "Velkostatek Testov", "Vs Testov", 2),
    ("soap-pn/vs-testov/k011", "Pozemková kniha – domkáři", SOA, [("Testov", "Teststadt"), ("Demolov", "Demolau")], "1600–1650",
     "grundbuch", "K 11", "Velkostatek Testov", "Vs Testov", 2),
    # a town: its own land book and one that names only a district ("Obvod ...")
    ("amp/am-mestov/k100", "Pozemková kniha", "Plzeň (archiv města)", [("Mestov", "Stadtau")], "1800–1850", "grundbuch", "K 100",
     "Archiv města Mestov", "AM Mestov", 2),
    ("amp/am-mestov/k101", "Pozemková kniha", "Plzeň (archiv města)", [("Obvod Mestov I-vnitřní město", "")], "1780–1800", "grundbuch",
     "K 101", "Archiv města Mestov", "AM Mestov", 2),
    ("soap-pn/vs-nikolov/k030", "Urbář", SOA, [("Nikolov", "")], "1700-1750", "urbar", "K 30", "Velkostatek Nikolov", "Vs Nikolov", 2),
    # council minutes with an inventory number only (no signature), like the live am-cheb/i00001
    ("amp/am-radov/i00001", "Radní protokol", "Plzeň (archiv města)", [("Radov", "")], "1700-1720", "ratsprotokoll", "", "Archiv města Radov",
     "AM Radov", 2),
    ("soap-pn/vs-lhota/k020", "Pozemková kniha", SOA, [("Lhota", "")], "1780–1840", "grundbuch", "K 20", "Velkostatek Lhota",
     "Vs Lhota", 2),
]
AMTS = {b[0]: i for i, b in enumerate(AMTSBUCH)}
# census node: path, title, place cell items, date, houses [Nr.], index of the house the node shows, archive label
CENSUS = [
    dict(path="soap-do/1869/demolov", title="Census 1869 Demolov (Demolau)", items=["Demolov (Demolau)"], date="1869", nrs=[1, 2, 3], cur=0),
    dict(path="soap-do/1880/demolov", title="Census 1880 Demolov (Demolau)", items=["Demolov (Demolau)"], date="1880", nrs=[1, 2, 4], cur=0),
    dict(path="soap-do/1869/demolov-pec", title="Census 1869 Demolov - Pec (Demolau - Hochofen)",
         items=["Demolov - Pec (Demolau - Hochofen)"], date="1869", nrs=[1, 2], cur=0),
    dict(path="soap-do/1857/demolov-predmesti", title="Census 1857 Demolov, Dolejší předměstí (Demolau, Untere Vorstadt)",
         items=["Demolov", "Dolejší předměstí (Untere Vorstadt)"], date="1857", nrs=[1, 2], cur=0),
    dict(path="soap-do/1869/zadni-demolov", title="Census 1869 Zadní Demolov (Hinterdemolau)", items=["Zadní Demolov (Hinterdemolau)"],
         date="1869", nrs=[1], cur=0),
    dict(path="soap-do/1869/lhota", title="Census 1869 Lhota", items=["Lhota"], date="1869", nrs=[1, 2], cur=0),
    # the live portal lists 1921 as one node per house (empty place and date cells)
    dict(path="soap-tc/1921/demolov/cp5", title="Census 1921 Demolov (Demolau), Nr. 5", items=[], date="", nrs=[5, 6, 7], cur=0),
    dict(path="soap-tc/1921/demolov/cp6", title="Census 1921 Demolov (Demolau), Nr. 6", items=[], date="", nrs=[5, 6, 7], cur=1),
    dict(path="soap-tc/1921/demolov/cp7", title="Census 1921 Demolov (Demolau), Nr. 7", items=[], date="", nrs=[5, 6, 7], cur=2),
]
CENSUS_BY_PATH = {c["path"]: c for c in CENSUS}
_nid = {}


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def amts_nid(slug):
    return NID0 + AMTS[slug] + 1


def house_nid(path, nr):
    """One viewer node per (census node group, house); nodes of one 1921 set share the houses."""
    group = path.rsplit("/", 1)[0] if path.count("/") == 3 else path
    return _nid.setdefault((group, nr), NID0 + 1000 + len(_nid))


def amts_keys(slug):
    fond, sig = slug.split("/")[1], slug.split("/")[2]
    return [f"soap-pn_20025-{fond}_{sig}_{10 * (i + 1):04d}" for i in range(AMTSBUCH[AMTS[slug]][9])]


def amts_dir(slug):
    a, f, s = slug.split("/")
    return f"amtsbuch/{a}/{f}/{s}"


def house_keys(path, nr):
    year, name = path.split("/")[1], path.split("/")[2]
    return [f"{path.split('/')[0]}_00592_census-{year}-{name}-cp{nr:03d}_{10 * (i + 1):04d}" for i in range(2)]


def house_dir(path, nr):
    arch, year, name = path.split("/")[:3]
    return f"census/{arch}/census-{year}-okres-demolov/{name}/cp{nr:03d}"


def _place_lis(items):
    return "".join(f'<li class="{"first" if i == 0 else ""} {"last" if i == len(items) - 1 else ""}">\n  {html.escape(t)}  \n</li>\n'
                   for i, t in enumerate(items))


def _pager(section, current, last):
    if last <= 0:
        return ""
    base = f"/searching/{section}?mode=0&amp;search_api_views_fulltext_1=x&amp;page="
    items = "".join(f'<li class="pager-item"><a href="{base}{n}">{n + 1}</a></li>' if n != current else f'<li class="pager-current">{n + 1}</li>'
                    for n in range(last + 1))
    return f'<ul class="pager">{items}<li class="pager-last last"><a href="{base}{last}">poslední »</a></li></ul>'


def _table(rows):
    return ('<div class="view-content"><table class="views-table sticky-enabled cols-6"><thead><tr>'
            '<th scope="col"><a href="/x">Archiv</a></th><th scope="col">Nadpis</th><th scope="col">Místo</th>'
            '<th scope="col"><a href="/x">Datace</a></th><th scope="col">Obsah / Regest</th><th scope="col">Obr.data</th>'
            '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _row(n, arch, href, title, items, dating, nid):
    return (f'<tr class="{"odd" if n % 2 == 0 else "even"}"><td class="views-field views-field-field-archives">\n {html.escape(arch)} </td>\n'
            f'<td class="views-field views-field-title-field">\n<a href="{href}">{html.escape(title)}</a> </td>\n'
            f'<td class="views-field views-field-field-doc-place">\n' + (f"<ul>{_place_lis(items)}</ul>" if items else "") + ' </td>\n'
            f'<td class="views-field views-field-field-doc-dates-field-doc-dates-from-1 nowrap">\n<div>{html.escape(dating)}</div> </td>\n'
            '<td class="views-field views-field-nothing">SYNTHETIC regest... </td>\n'
            f'<td class="views-field views-field-nothing-1"><a class="intlink nowrap" href="/iipimage/{nid}" title="Odkaz"><span>&gt;</span></a></td></tr>\n')


def _form(section, term):
    form_id = "page-solr-census" if section == "census" else "page-1"
    return (f'<form accept-charset="UTF-8" action="/searching/{section}" id="views-exposed-form-solr-searching-{form_id}" method="get">'
            f'<div><input name="search_api_views_fulltext_1" type="text" value="{html.escape(term)}"/></div></form>')


def search_page(section, query, rows_per_page):
    term = (query.get("search_api_views_fulltext_1") or [""])[0]
    tokens = _tokens(term)
    page = int((query.get("page") or ["0"])[0])
    head = '<!doctype html><html lang="cs"><head><title>Vyhledávání | Porta fontium</title></head><body><h1>SYNTHETIC FIXTURE</h1>' + _form(section, term)
    if section == "census":
        if (query.get("mode") or [""])[0] != "0":
            return head + "</body></html>"          # the live census tab shows no list without mode=0
        hits = [c for c in CENSUS if tokens and (tokens <= _tokens(" ".join(c["items"]) or c["title"]) or tokens <= _tokens(c["title"]))]
        entries = [(DOM if c["path"].startswith("soap-do") else "Tachov (státní archiv)", "/census/" + c["path"], c["title"], c["items"], c["date"],
                    house_nid(c["path"], c["nrs"][c["cur"]])) for c in hits]
    else:
        wanted = set(query.get("field_book_type[]", []))
        hits = [b for b in AMTSBUCH if tokens and any(tokens <= _tokens(n) or (g and tokens <= _tokens(g)) for n, g in b[3])
                and (not wanted or b[5] in wanted)]
        entries = [(b[2], "/amtsbuch/" + b[0], b[1], [f"{n} ({g})" if g else n for n, g in b[3]], b[4], amts_nid(b[0])) for b in hits]
    if not entries:
        return head + "</body></html>"
    last = max(0, (len(entries) - 1) // rows_per_page)
    rows = [_row(n, *e) for n, e in enumerate(entries[page * rows_per_page:(page + 1) * rows_per_page])]
    return head + _table(rows) + _pager(section, page, last) + "</body></html>"


def _field(name, label, value):
    return (f'<div class="field field-name-{name}"><div class="field-label">{label}: </div><div class="field-items">'
            f'<div class="field-item even">{html.escape(value)}</div></div></div>')


def amtsbuch_detail(slug):
    b = AMTSBUCH[AMTS[slug]]
    places = ", ".join(f"<strong>{html.escape(n)}</strong>" + (f" ({html.escape(g)})" if g else "") + " <em>[okres Demolov]</em>" for n, g in b[3])
    block = ('<div class="block" id="block-iipimage-base-iipimage-base-thumbnails"><div class="iip-thumbnails">'
             + "".join(f'<div class="iip-thumbnail"><a href="/iipimage/{amts_nid(slug)}/{k}"><img alt="{k}"/></a></div>' for k in amts_keys(slug))
             + "</div></div>")
    return f'''<!doctype html><html lang="cs"><head><title>{html.escape(b[1])} | Porta fontium</title></head><body>
<article about="/amtsbuch/{slug}" typeof="sioc:Item foaf:Document"><div class="content clearfix">
{_field("field-archives", "Archiv", b[2])}{_field("field-fondsname", "Název fondu", b[7])}{_field("field-fonds", "Číslo fondu", "20025")}
{_field("field-fondsabbr", "Značka fondu", b[8])}{_field("field-sign", "Signatura", b[6]) if b[6] else _field("field-inventory", "Inv. č./poř. č.", "1")}{_field("field-doc-dates", "Datace", b[4])}
{_field("field-originator", "Původce", b[7])}{_field("field-book-type", "Typ knihy", BOOK_TYPES[b[5]])}
<div class="field field-name-field-doc-place"><div class="field-label">Místo: </div><div class="field-items"><div class="field-item even">{places}</div></div></div>
</div></article>{block}</body></html>'''


def census_detail(path):
    c = CENSUS_BY_PATH[path]
    nav = []
    for i, nr in enumerate(c["nrs"]):
        nav.append(f"<big>Nr. {nr}</big>" if i == c["cur"] else f'<a href="/iipimage/{house_nid(path, nr)}?language=cs">Nr. {nr}</a>')
    own = c["nrs"][c["cur"]]
    block = ('<div class="block" id="block-iipimage-base-iipimage-base-thumbnails"><div class="iip-thumbnails">'
             + "".join(f'<div class="iip-thumbnail"><a href="/iipimage/{house_nid(path, own)}/{k}"><img alt="{k}"/></a></div>'
                       for k in house_keys(path, own)) + "</div></div>")
    dating = _field("field-doc-dates", "Datace", c["date"]) if c["date"] else ""
    return f'''<!doctype html><html lang="cs"><head><title>{html.escape(c["title"])} | Porta fontium</title></head><body>
<article about="/census/{path}" typeof="sioc:Item foaf:Document"><div class="content clearfix">
{_field("field-archives", "Archiv", DOM if path.startswith("soap-do") else "Tachov (státní archiv)")}{_field("field-fonds", "Číslo fondu", "592")}
{_field("field-fondsabbr", "Značka fondu", "OÚ Demolov, I")}{_field("field-inventory", "Inv. číslo", "i725")}{dating}
<div class="field field-name-field-navi"><div class="field-items"><div class="field-item even"><p>{" · ".join(nav)}</p></div></div></div>
</div></article>{block}</body></html>'''


def viewer_page(nid):
    for slug in AMTS:
        if amts_nid(slug) == nid:
            images = [f"/fcgi-bin/iipsrv.fcgi?DeepZoom={amts_dir(slug)}/{k}.jp2.dzi" for k in amts_keys(slug)]
            urls = [f"iipimage/{nid}/{k}" for k in amts_keys(slug)]
            return _viewer(images, urls)
    for (group, nr), n in list(_nid.items()):
        if n == nid:
            path = next(c["path"] for c in CENSUS if (c["path"].rsplit("/", 1)[0] if c["path"].count("/") == 3 else c["path"]) == group)
            images = [f"/fcgi-bin/iipsrv.fcgi?DeepZoom={house_dir(path, nr)}/{k}.jp2.dzi" for k in house_keys(path, nr)]
            urls = [f"iipimage/{nid}/{k}" for k in house_keys(path, nr)]
            return _viewer(images, urls)
    return None


def _viewer(images, urls):
    return ('<!doctype html><html lang="cs"><head><title>Prohlížeč | Porta fontium</title></head><body>'
            '<script>hdv = new HDV({\n imagesSet : ' + json.dumps(images) + ',\n urlsSet : ' + json.dumps(urls) + '\n });</script></body></html>')


def handle_more(path, query, rows_per_page):
    """Response for the amtsbuch/census tabs, details and viewers of the synthetic portal, or None for any other path."""
    html_headers = {"content-type": "text/html; charset=utf-8"}
    if path in ("/searching/amtsbuch", "/searching/census"):
        return httpx.Response(200, text=search_page(path.rsplit("/", 1)[1], query, rows_per_page), headers=html_headers)
    m = re.fullmatch(r"/amtsbuch/([\w-]+/[\w-]+/[\w-]+)", path)
    if m and m[1] in AMTS:
        return httpx.Response(200, text=amtsbuch_detail(m[1]), headers=html_headers)
    m = re.fullmatch(r"/census/([\w-]+/\d{4}/[\w-]+(?:/[\w-]+)?)", path)
    if m and m[1] in CENSUS_BY_PATH:
        return httpx.Response(200, text=census_detail(m[1]), headers=html_headers)
    m = re.fullmatch(r"/iipimage/(\d+)(?:/[\w.-]+)?", path)
    if m:
        # every house viewer that a detail page can link must exist
        for c in CENSUS:
            for nr in c["nrs"]:
                house_nid(c["path"], nr)
        page = viewer_page(int(m[1]))
        if page is not None:
            return httpx.Response(200, text=page, headers=html_headers)
    return None
