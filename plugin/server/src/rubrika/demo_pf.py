"""Entirely synthetic responses shaped like Porta fontium (Drupal 7 search, book detail, viewer with imagesSet,
iipsrv IIIF). Places, books and images are invented; NEVER real archive data."""
import html
import io
import json
import re
from urllib.parse import parse_qs

import httpx
from PIL import Image

from .catalog import normalize
from .demo_pf_more import handle_more

ROWS_PER_PAGE = 4          # the live portal shows 40; tests patch this to force pagination
CRAWL_DELAY = 10           # robots.txt of the portal asks for 10 s between requests
NID0 = 90000000

SOA = "Plzeň (státní archiv)"
AMP = "Demolov (archiv města)"
# slug, title, archive label, places [(name, german variants)], dating, pages, originator, image folder, images
BOOKS = [
    ("soap-pn/demolov-01", "Demolov 01", SOA, [("Demolov", "Demolau"), ("Horní Demolov", "Ober-Demolau")],
     "1785-1830 *, *i", "Demolov (Římskokatolická církev)", "demolov", 3),
    ("soap-pn/demolov-02", "Demolov 02", SOA, [("Demolov", "Demolau"), ("Horní Demolov", "Ober-Demolau")],
     "1785-1830 oo, ooi", "Demolov (Římskokatolická církev)", "demolov", 2),
    ("soap-pn/demolov-03", "Demolov 03", SOA, [("Demolov", "Demolau")],
     "1785-1830 +", "Demolov (Římskokatolická církev)", "demolov", 2),
    ("soap-pn/demolov-04", "Demolov 04", SOA, [("Demolov", "Demolau"), ("Testov", "Teststadt")],
     "1850-1899 *", "Demolov (Římskokatolická církev)", "demolov", 2),
    ("soap-pn/demolov-05", "Demolov 05", SOA, [("Demolov", "Demolau")],
     "1900-1910 ?", "Demolov (Římskokatolická církev)", "demolov", 2),
    ("amp/index-demolov-001", "Index Demolov 001", AMP, [("Demolov", "Demolau")],
     "1648-1733 *i", "Demolov (Římskokatolická církev)", "demolov-index", 2),
    ("soap-pn/testov-01", "Testov 01", SOA, [("Testov", "Teststadt"), ("Malá Ves", "")],
     "1790-1850 *, oo, +", "Testov (Římskokatolická církev)", "testov", 2),
    ("soap-pn/starynov-01", "Starynov 01", SOA, [("Starý Demolovec", "Alt-Demolau")],
     "1801-1820 *", "Starynov (Římskokatolická církev)", "starynov", 2),
    # a town: its own books, a district book that names only "Obvod …", and villages that merely list the town
    ("soap-pn/mestov-01", "Mestov 01", SOA, [("Mestov", "Stadtau")], "1800-1830 *", "Mestov (Římskokatolická církev)", "mestov", 2),
    ("soap-pn/mestov-02", "Mestov 02", SOA, [("Mestov", "Stadtau"), ("Předměstí", "")], "1800-1830 oo", "Mestov (Římskokatolická církev)", "mestov", 2),
    ("soap-pn/obvod-mestov-01", "Mestov 101", SOA, [("Obvod Mestov I-vnitřní město", "")], "1780-1800 *", "Mestov (Římskokatolická církev)", "mestov", 2),
    ("soap-pn/obvod-mestov-02", "Mestov 102", SOA, [("Obvod Mestov I-vnitřní město", "")], "1800-1810 *", "Mestov (Římskokatolická církev)", "mestov", 2),
    ("soap-pn/vesnice-01", "Vesnice 01", SOA, [("Horní Ves", ""), ("Dolní Ves", ""), ("Mestov", "Stadtau"), ("Nová Ves", ""), ("Lhota", "")],
     "1800-1830 *", "Vesnice (Římskokatolická církev)", "vesnice", 2),
]
SLUGS = {b[0]: b for b in BOOKS}
FUND = ("10014", "Sbírka matrik západních Čech")
TYPE_LABELS = {"register-birth": "Matrika narozených", "register-marriage": "Matrika oddaných",
               "register-death": "Matrika zemřelých", "index-birth": "Index narozených",
               "index-marriage": "Index oddaných", "index-death": "Index zemřelých"}
_MARK = {"*": "birth", "oo": "marriage", "+": "death"}


def nid(slug: str) -> int:
    return NID0 + [b[0] for b in BOOKS].index(slug) + 1


def types_of(dating: str) -> list[str]:
    found = []
    for mark, index in re.findall(r"(\*|oo|\+)\s?(i?)", dating):
        t = ("index-" if index else "register-") + _MARK[mark]
        if t not in found:
            found.append(t)
    return found


def keys(slug: str) -> list[str]:
    """Image keys of a book: cover, pages, index pages (x = extra, n = normal, r = register)."""
    count = SLUGS[slug][7]
    name = slug.split("/")[1]
    return [f"{name}_{10 * (i + 1):04d}-{'x' if i == 0 else 'n'}" for i in range(count)]


def image_dir(slug: str) -> str:
    arch, name = slug.split("/")
    return f"register/{arch}/{SLUGS[slug][6]}/{name}"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def _matches(book, term_tokens: set[str]) -> bool:
    for name, german in book[3]:
        if term_tokens <= _tokens(name) or (german and term_tokens <= _tokens(german)):
            return True
    return False


def _place_li(name: str, german: str, first: bool, last: bool) -> str:
    cls = " ".join(x for x in ("first" if first else "", "last" if last else "") if x)
    label = f"{name} ({german})" if german else name
    return f'<li class="{cls}">\n  \n    {html.escape(label)}  \n\n</li>\n'


def _form(term: str) -> str:
    return ('<form accept-charset="UTF-8" action="/searching/register" id="views-exposed-form-solr-searching-page-solr-register" '
            'method="get"><div><div class="views-exposed-form"><input class="form-text" name="search_api_views_fulltext_1" '
            f'type="text" value="{html.escape(term)}"/></div></div></form>')


def _pager(current: int, last: int) -> str:
    if last <= 0:
        return ""
    base = "/searching/register?search_api_views_fulltext=&amp;search_api_views_fulltext_1=x&amp;page="
    items = "".join(f'<li class="pager-item"><a href="{base}{n}" title="Přejít na stranu {n + 1}">{n + 1}</a></li>'
                    if n != current else f'<li class="pager-current">{n + 1}</li>' for n in range(last + 1))
    return (f'<ul class="pager">{items}<li class="pager-last last"><a href="{base}{last}" title="Přejít na poslední stranu">'
            'poslední »</a></li></ul>')


def search_page(query: dict) -> str:
    term = (query.get("search_api_views_fulltext_1") or [""])[0]
    tokens = _tokens(term)
    wanted = set(query.get("field_register_type[]", []))
    hits = [b for b in BOOKS if tokens and _matches(b, tokens) and (not wanted or wanted & set(types_of(b[4])))]
    page = int((query.get("page") or ["0"])[0])
    last = max(0, (len(hits) - 1) // ROWS_PER_PAGE)
    head = ('<!doctype html><html lang="cs"><head><title>Vyhledávání | Porta fontium</title></head><body>'
            '<h1>SYNTHETIC FIXTURE</h1><div class="messages error">Warning : Invalid argument supplied for foreach() '
            've funkci search_api_solr_similar_words_search_api_views_query_alter()</div>' + _form(term))
    if not hits:
        return head + "</body></html>"
    rows = []
    for n, b in enumerate(hits[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]):
        slug, title, arch, places = b[0], b[1], b[2], b[3]
        lis = "".join(_place_li(nm, g, i == 0, i == len(places) - 1) for i, (nm, g) in enumerate(places))
        rows.append(
            f'<tr class="{"odd" if n % 2 == 0 else "even"}"><td class="views-field views-field-field-archives">\n'
            f'            {html.escape(arch)}          </td>\n'
            f'<td class="views-field views-field-title-field">\n<a href="/register/{slug}">{html.escape(title)}</a> </td>\n'
            f'<td class="views-field views-field-field-doc-place">\n<ul>{lis}</ul> </td>\n'
            f'<td class="views-field views-field-field-doc-dates-field-doc-dates-from-1 nowrap">\n<div>{html.escape(b[4])} </div> </td>\n'
            '<td class="views-field views-field-nothing"><p>SYNTHETIC regest...</p> </td>\n'
            f'<td class="views-field views-field-nothing-1">\n<a class="intlink typeMatrika nowrap" href="/iipimage/{nid(slug)}" '
            f'title="Odkaz na obrázek v plné velikosti"><span> &gt;</span></a> </td></tr>\n')
    table = ('<div class="view-content"><table class="views-table sticky-enabled cols-6"><thead><tr>'
             '<th scope="col"><a href="/x">Archiv</a></th><th scope="col">Nadpis</th><th scope="col">Místo</th>'
             '<th scope="col"><a href="/x">Datace</a></th><th scope="col">Obsah / Regest</th><th scope="col">Obr.data</th>'
             '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    return head + table + _pager(page, last) + "</body></html>"


def detail_page(slug: str, sidebar: bool = False) -> str:
    b = SLUGS[slug]
    places = ", ".join(f"<strong>{html.escape(n)}</strong>" + (f" ({html.escape(g)})" if g else "") for n, g in b[3])
    label = lambda k: TYPE_LABELS[k]
    types = "".join(f'<div class="field-item">{label(t)}</div>' for t in types_of(b[4]))
    links = "".join(f'<a href="/iipimage/{nid(slug)}/{k}">{k}</a> ' for k in keys(slug))
    block = ""
    if sidebar:     # live shape of long books: the viewer links are only in the sidebar thumbnails, not in the article
        block = ('<div class="block" id="block-iipimage-base-iipimage-base-thumbnails"><div class="iip-thumbnails">'
                 + "".join(f'<div class="iip-thumbnail"><a href="/iipimage/{nid(slug)}/{k}"><img alt="{k}"/></a></div>' for k in keys(slug))
                 + "</div></div>")
        links = ""
    arch_name = b[2]
    return f'''<!doctype html><html lang="cs"><head><title>{html.escape(b[1])} | Porta fontium</title></head><body>
<article about="/register/{slug}" typeof="sioc:Item foaf:Document"><div class="content clearfix">
<div class="field field-name-field-archives"><div class="field-label">Archiv: </div><div class="field-items"><div class="field-item even">{html.escape(arch_name)}</div></div></div>
<div class="field field-name-field-fonds"><div class="field-label">Číslo fondu: </div><div class="field-items"><div class="field-item even">{FUND[0]}</div></div></div>
<div class="field field-name-field-fondsabbr"><div class="field-label">Značka fondu: </div><div class="field-items"><div class="field-item even">{FUND[1]}</div></div></div>
<div class="field field-name-field-sign"><div class="field-label">Signatura: </div><div class="field-items"><div class="field-item even">{html.escape(b[1])}</div></div></div>
<div class="field field-name-field-doc-dates"><div class="field-items"><div class="field-item even">{html.escape(b[4])} </div></div></div>
<div class="field field-name-field-originator"><div class="field-label">Původce: </div><div class="field-items"><div class="field-item even">{html.escape(b[5])}</div></div></div>
<div class="field field-name-field-doc-place"><div class="field-label">Místo: </div><div class="field-items"><div class="field-item even">{places}</div></div></div>
<div class="field field-name-field-register-type"><div class="field-label">Typ: </div><div class="field-items">{types}</div></div>
<div class="field field-name-field-pages"><div class="field-label">Počet stran: </div><div class="field-items"><div class="field-item even">{len(keys(slug)) * 2}</div></div></div>
<div class="field field-name-field-fc-bookpart"><div class="field-items"><div class="field-item even">{links}</div></div></div>
</div></article>{block}</body></html>'''


def viewer_page(slug: str) -> str:
    n = nid(slug)
    images = [f"/fcgi-bin/iipsrv.fcgi?DeepZoom={image_dir(slug)}/{k}.jp2.dzi" for k in keys(slug)]
    urls = [f"iipimage/{n}/{k}" for k in keys(slug)]
    return ('<!doctype html><html lang="cs"><head><title>' + html.escape(SLUGS[slug][1]) + ' | Porta fontium</title></head><body>'
            '<img id="last-page-btn" onclick="if (hdv.isLastPage == false) {hdv.osdViewer.goToPage(hdv.options.imagesSet.length - 1);} return false;"/>'
            '<script>console.log("start hdv");\n hdv = new HDV({\n // pole URL obrázků\n imagesSet : ' + json.dumps(images) +
            ',\n urlsSet : ' + json.dumps(urls) + '\n });</script></body></html>')


def info_json(width: int, height: int, ident: str) -> dict:
    return {"@context": "http://iiif.io/api/image/2/context.json", "@id": ident, "protocol": "http://iiif.io/api/image",
            "width": width, "height": height,
            "sizes": [{"width": width // 8, "height": height // 8}, {"width": width // 2, "height": height // 2}],
            "tiles": [{"width": 256, "height": 256, "scaleFactors": [1, 2, 4, 8]}],
            "profile": ["http://iiif.io/api/image/2/level1.json",
                        {"formats": ["jpg"], "qualities": ["native", "color", "gray", "bitonal"],
                         "supports": ["regionByPct", "regionSquare", "sizeByForcedWh", "sizeByWh", "sizeAboveFull",
                                      "rotationBy90s", "mirroring"], "maxWidth": 5000, "maxHeight": 5000}]}


def handle_pf(request: httpx.Request, image: Image.Image, crawl_delay: float = CRAWL_DELAY) -> httpx.Response:
    path = request.url.path
    query = parse_qs(request.url.query.decode(), keep_blank_values=True)
    html_headers = {"content-type": "text/html; charset=utf-8"}
    if request.method != "GET":
        return httpx.Response(405)
    if path == "/robots.txt":
        return httpx.Response(200, text=f"User-agent: *\nCrawl-delay: {crawl_delay:g}\n# CSS, JS, Images\n"
                                        "Allow: /misc/*.css$\nDisallow: /includes/\nDisallow: /admin/\nDisallow: /search/\n"
                                        "Disallow: /user/login/\n", headers={"content-type": "text/plain"})
    if path == "/searching/register":
        return httpx.Response(200, text=search_page(query), headers=html_headers)
    more = handle_more(path, query, ROWS_PER_PAGE)
    if more is not None:
        return more
    m = re.fullmatch(r"/register/([\w-]+/[\w-]+)", path)
    if m and m[1] in SLUGS:
        return httpx.Response(200, text=detail_page(m[1]), headers=html_headers)
    m = re.fullmatch(r"/iipimage/(\d+)(?:/[\w.-]+)?", path)
    if m:
        slug = next((b[0] for b in BOOKS if str(nid(b[0])) == m[1]), None)
        if slug:
            return httpx.Response(200, text=viewer_page(slug), headers=html_headers)
    if path == "/fcgi-bin/iipsrv.fcgi":
        iiif = (query.get("IIIF") or [""])[0]
        if iiif.endswith(".jp2/info.json"):
            ident = "https://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?IIIF=" + iiif[:-len("/info.json")]
            return httpx.Response(200, json=info_json(image.width, image.height, ident),
                                  headers={"content-type": "application/ld+json"})
        r = re.search(r"\.jp2/(\d+),(\d+),(\d+),(\d+)/(\d+),(\d+)?/0/default\.jpg$", iiif)
        if r:
            x, y, w, h = map(int, r.groups()[:4])
            tw = int(r[5])
            th = int(r[6]) if r[6] else max(1, round(h * tw / w))
            if w <= 0 or h <= 0 or x + w > image.width or y + h > image.height:
                return httpx.Response(400)
            out = io.BytesIO()
            image.crop((x, y, x + w, y + h)).resize((tw, th), Image.Resampling.LANCZOS).save(out, format="JPEG", quality=95)
            return httpx.Response(200, content=out.getvalue(), headers={"content-type": "image/jpeg"})
    return httpx.Response(404, text="Synthetic Porta fontium fixture has no such endpoint")
