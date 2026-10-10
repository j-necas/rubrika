"""Allowlisted read routes per source: path, HTTP method and the throttling group they share."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs

from .errors import ResearchError

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ARON_API = "/aron/api/aron"
ARON_LIST_TYPES = frozenset({"ENTITY", "ARCH_DESC"})
ACTA_PATHS = frozenset({"/actapublica/matrika/hledani", "/actapublica/ajax/matrika/autocomplete_obec",
                        "/actapublica/dokumenty"})
CENSUS_APP = "/scitacioperaty/digisada"
SKETCH_APP = "/indikacniskici"
SKETCH_ID = r"MOR\d{6,14}"
_SKETCH_TILE = re.compile(
    rf"{SKETCH_APP}/data/obrazky/{SKETCH_ID}/Img/(?:map\.jpg|(?:Preview/[AR]\d{{2}}[A-Z]|little/[AR]\d{{2}}[A-Z]-[A-C][1-3]"
    r"|Small/[AR]\d{2}[A-Z]-[A-C][1-3]-[A-B][1-2]|Normal/[AR]\d{2}[A-Z]-[A-C][1-3]-[A-B][1-2]-[A-B][1-2])\.jpg)")
_SKETCH_DETAIL = re.compile(rf"{SKETCH_APP}/skica/detail/({SKETCH_ID}|\d{{1,8}})")
_SKETCH_QUERY = {"nazev": r"[\w .,'()/-]{1,100}", "okres": r"[\w -]{2,40}", "signatura": r"\d{1,5}", "page": r"[1-9]\d{0,2}"}


@dataclass(frozen=True)
class Route:
    source: str   # acta_publica | aron | census | mza_images | robots | vca | vca_images
    group: str    # cooldown key: a refusal pauses only this group
    method: str
    archive: str = "mza"


def _check_iipsrv(query: str, prefix: str | None = None):
    """prefix: the image path must start with it (Porta fontium serves only register/, amtsbuch/ and census/)."""
    values = parse_qs(query)
    if len(values) != 1 or next(iter(values)).lower() not in ("deepzoom", "iiif"):
        raise ResearchError("INVALID_SOURCE", "Nepovolená operace obrazového serveru.")
    v = next(iter(values.values()))
    if len(v) != 1 or ".." in v[0].split("/") or "\\" in v[0] or "%" in v[0] or "\x00" in v[0]:
        raise ResearchError("INVALID_SOURCE", "Neplatný obrazový identifikátor.")
    if prefix is not None and (not v[0].startswith(prefix) or "%" in query or not _PF_IMAGE.fullmatch(v[0])):
        raise ResearchError("INVALID_SOURCE", "Neplatný obrazový identifikátor.")


def match_sketch_route(path: str, query: str) -> Route | None:
    """MZA indication sketches (GET only): the search (by name, district or signature, paged), one sketch's detail page (by its
    MOR id, which the site redirects to a numeric id, or by that number with the side of the sheet) and the static tiles."""
    if not path.startswith(SKETCH_APP + "/"):
        return None
    if not query and _SKETCH_TILE.fullmatch(path):
        return Route("sketches", "mza_sketch_images", "GET")
    if path == SKETCH_APP + "/skica":
        try:
            values = parse_qs(query, keep_blank_values=True, max_num_fields=6, strict_parsing=True)
        except ValueError:
            return None
        if (values and set(values) <= set(_SKETCH_QUERY) and all(len(v) == 1 for v in values.values())
                and (values.get("nazev", [""])[0] or values.get("signatura", [""])[0])
                and all(v[0] == "" or re.fullmatch(_SKETCH_QUERY[k], v[0]) for k, v in values.items())):
            return Route("sketches", "mza_sketch_catalog", "GET")
        return None
    m = _SKETCH_DETAIL.fullmatch(path)
    if m:
        if not query:
            return Route("sketches", "mza_sketch_catalog", "GET")
        values = parse_qs(query, keep_blank_values=True, max_num_fields=2)
        if m[1].isdigit() and list(values) == ["site"] and values["site"] in (["FRONT"], ["BACK"]):
            return Route("sketches", "mza_sketch_catalog", "GET")
    return None


def match_route(path: str, query: str, method: str) -> Route | None:
    """path must already be unquoted and traversal-checked by SourcePolicy."""
    if method == "GET":
        if path == "/robots.txt":
            return Route("robots", "robots", "GET")
        if path.startswith(SKETCH_APP + "/"):
            return match_sketch_route(path, query)
        if path in ACTA_PATHS or re.fullmatch(r"/actapublica/matrika/detail/\d+/?", path):
            return Route("acta_publica", "acta_publica", "GET")
        if path.startswith("/iipsrv/"):
            _check_iipsrv(query)
            return Route("mza_images", "mza_images", "GET")
        if path.startswith("/iiif/"):
            return Route("mza_images", "mza_images", "GET")
        if not query and re.fullmatch(rf"{ARON_API}/(?:apu|script/citation)/{UUID}", path):
            return Route("aron", "aron", "GET")
        if path == CENSUS_APP + "/autocomplete/":
            values = parse_qs(query, keep_blank_values=True)
            if (sorted(values) == ["field", "term"] and all(len(v) == 1 for v in values.values())
                    and values["field"][0] in ("obec", "cobec")):
                return Route("census", "census", "GET")
            return None
        if not query and (re.fullmatch(rf"{CENSUS_APP}/advanced_search(?:/\d{{1,5}})?", path)
                          or re.fullmatch(rf"{CENSUS_APP}/detail/\d{{1,9}}", path)):
            return Route("census", "census", "GET")
    if method == "POST" and path == ARON_API + "/apu/listresults":
        values = parse_qs(query, keep_blank_values=True)
        if list(values) == ["listType"] and len(values["listType"]) == 1 and values["listType"][0] in ARON_LIST_TYPES:
            return Route("aron", "aron", "POST")
    if method == "POST" and not query and path == CENSUS_APP + "/do_advanced_search":
        return Route("census", "census", "POST")
    return None


VCA_HOST = "aron.vychodoceskearchivy.cz"
VCA_IMAGES = "imgx.vychodoceskearchivy.cz"
VCA_API = "/api/aron"
VCA_LIST_TYPES = frozenset({"ENTITY", "ARCH_DESC"})
_VCA_TILE = re.compile(rf"/tiles/[0-9a-f]{{2}}/[0-9a-f]{{2}}/{UUID}/image(?:\.dzi|_files/\d{{1,2}}/\d{{1,4}}_\d{{1,4}}\.jpg)")


def match_vca_route(host: str, path: str, query: str, method: str) -> Route | None:
    """ARON of the East Bohemian archives: catalogue API on VCA_HOST, static DZI tiles on VCA_IMAGES."""
    if method == "GET" and path == "/robots.txt" and not query:
        return Route("robots", "vca_robots", "GET", "vca")
    if host == VCA_HOST:
        if method == "GET" and not query and re.fullmatch(rf"{VCA_API}/(?:apu|script/citation)/{UUID}", path):
            return Route("vca", "vca", "GET", "vca")
        if method == "POST" and path == VCA_API + "/apu/listview":
            values = parse_qs(query, keep_blank_values=True)
            if list(values) == ["listType"] and len(values["listType"]) == 1 and values["listType"][0] in VCA_LIST_TYPES:
                return Route("vca", "vca", "POST", "vca")
    if host == VCA_IMAGES and method == "GET" and not query and _VCA_TILE.fullmatch(path):
        return Route("vca_images", "vca_images", "GET", "vca")
    return None


_ZOOMIFY_FILE = r"(?:ImageProperties\.xml|TileGroup\d{1,4}/\d{1,2}-\d{1,4}-\d{1,4}\.jpg)"
AHMP_IMAGES = "images.ahmp.cz"
NA_HOST = "vademecum.nacr.cz"
_AHMP_IMAGE = re.compile(r"/mrimage/ahmp/zoomify/cz/archives/CZ-321100010/(?:NAD-\d{1,5}|spisy)/dao/images/\d{4}/[0-9a-f]{32}\.jpg/" + _ZOOMIFY_FILE)
# The double slash after "zoomify" is part of the real NA path.
_NA_IMAGE = re.compile(rf"/mrimage/vademecum/zoomify//cz/archives/CZ-100000010/inventare/dao/images/\d{{4}}/{UUID}\.jpg/" + _ZOOMIFY_FILE)


AHMP_HOST = "katalog.ahmp.cz"
AHMP_BASE = "/pragapublica"
# A Bach "entity" reference exactly as the catalogue writes it for a tree node: (^n)(((localArchiv,^n,hot_)(unidata))(<id>))
_BACH_ENTITY = re.compile(r"\(\^n\)\(\(\(localArchiv,\^n,hot_\)\(unidata\)\)\(\d{1,9}\)\)")
_BACH_TOKEN = re.compile(r"[A-Za-z0-9_=-]{16,600}")


def _only_params(query: str, *allowed: set[str]) -> dict[str, list[str]] | None:
    """Parsed query when its names are exactly one of the allowed sets, each given once; otherwise None."""
    try:
        values = parse_qs(query, keep_blank_values=True, max_num_fields=6)
    except ValueError:
        return None
    if set(values) not in allowed or any(len(v) != 1 for v in values.values()):
        return None
    return values


def match_ahmp_route(host: str, path: str, query: str, method: str) -> Route | None:
    """Prague City Archives. Catalogue (Bach application, GET only, no captcha, basket, login or search form) and
    the Zoomify descriptors and tiles of the `mrimage` proxy. Zoomify.action is not needed: a permalink with
    &scan=N already carries the scan's image root."""
    if method != "GET":
        return None
    if path == "/robots.txt" and not query:
        return Route("robots", "ahmp_robots", "GET", "ahmp")
    if host == AHMP_IMAGES and not query and _AHMP_IMAGE.fullmatch(path):
        return Route("ahmp_images", "ahmp_images", "GET", "ahmp")
    if host != AHMP_HOST:
        return None
    if path == AHMP_BASE + "/NavigBean.action":
        values = _only_params(query, {"entity"})
        if values and _BACH_ENTITY.fullmatch(values["entity"][0]):
            return Route("ahmp", "ahmp_catalog", "GET", "ahmp")
    elif path == AHMP_BASE + "/PaginatorNavig.action":
        # The forward arrow (row) and the "go to row" box of the pager (rowTxt, a plain GET form with both tokens).
        values = _only_params(query, {"_sourcePage", "row"}, {"rowTxt", "_sourcePage", "__fp"})
        number = "row" if values and "row" in values else "rowTxt"
        if (values and _BACH_TOKEN.fullmatch(values["_sourcePage"][0]) and re.fullmatch(r"\d{1,6}", values[number][0])
                and ("__fp" not in values or _BACH_TOKEN.fullmatch(values["__fp"][0]))):
            return Route("ahmp", "ahmp_catalog", "GET", "ahmp")
    elif path == AHMP_BASE + "/permalink":
        values = _only_params(query, {"xid"}, {"xid", "scan"})
        if (values and re.fullmatch(r"[0-9A-Fa-f]{32}", values["xid"][0])
                and ("scan" not in values or re.fullmatch(r"[1-9]\d{0,4}", values["scan"][0]))):
            return Route("ahmp", "ahmp_catalog", "GET", "ahmp")
    return None


NA_BASE = "/vademecum"
def _na_entity(ident: str) -> re.Pattern:
    return re.compile(r"\(\^n\)\(\(\(20cb6cc9d41144e5874020c1e918c34f\)\(de1e5208398e4a7eb52df0f4687e7dd2\)\)\(" + ident + r"\)\)")


# Entity of the inventory (the fund's finding aid, id 4137) and of one of its books, exactly as the application writes them.
_NA_VOLUME_ENTITY = _na_entity("4137")
_NA_BOOK_ENTITY = _na_entity(r"\d{1,9}")
_NA_XID = re.compile(r"[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# A page that has just issued a session cookie writes its links as Action;jsessionid=<id>; only that shape is accepted.
_NA_ACTION = re.compile(rf"{NA_BASE}/(BookVolume|PaginatorBookVolume|PaginatorMedia|Zoomify)\.action(?:;jsessionid=[A-Za-z0-9._-]{{8,200}})?")


def match_na_route(host: str, path: str, query: str, method: str) -> Route | None:
    """National Archives (VadeMeCum), GET only: the Zoomify descriptors and tiles of the `mrimage` proxy, the fund
    permalink that opens a session, the inventory's table of contents and its pager, and the viewer with its scan
    pager. No captcha ("uložit"), basket, search form, index, login or download action is reachable."""
    if method != "GET":
        return None
    if not query:
        if path == "/robots.txt":
            return Route("robots", "na_robots", "GET", "na")
        if host == NA_HOST and _NA_IMAGE.fullmatch(path):
            return Route("na_images", "na_images", "GET", "na")
        return None
    if host != NA_HOST:
        return None
    if path == NA_BASE + "/permalink":
        values = _only_params(query, {"xid"}, {"xid", "scan"}, {"xid", "scan", "parentType"})
        if (values and _NA_XID.fullmatch(values["xid"][0]) and ("scan" not in values or re.fullmatch(r"[0-9a-f]{32}", values["scan"][0], re.I))
                and ("parentType" not in values or re.fullmatch(r"\d{1,6}", values["parentType"][0]))):
            return Route("na", "na_catalog", "GET", "na")
        return None
    m = _NA_ACTION.fullmatch(path)
    if not m:
        return None
    action = m[1]
    if action == "BookVolume":
        values = _only_params(query, {"browseAid", "entity"})
        ok = bool(values) and values["browseAid"][0] == "" and bool(_NA_VOLUME_ENTITY.fullmatch(values["entity"][0]))
    elif action == "PaginatorBookVolume":
        values = _only_params(query, {"_sourcePage", "row"}, {"alter", "_sourcePage", "zanchor", "rowTxt"})
        ok = bool(values) and bool(_BACH_TOKEN.fullmatch(values["_sourcePage"][0])) and (
            (re.fullmatch(r"\d{1,6}", values["row"][0]) is not None) if "row" in values else
            (values["alter"][0] == "" and re.fullmatch(r"#bookResume\d{1,6}", values["zanchor"][0]) is not None
             and re.fullmatch(r"\d{1,6}", values["rowTxt"][0]) is not None))
    elif action == "PaginatorMedia":
        values = _only_params(query, {"_sourcePage", "row"})
        ok = bool(values) and bool(_BACH_TOKEN.fullmatch(values["_sourcePage"][0])) and re.fullmatch(r"\d{1,6}", values["row"][0]) is not None
    else:
        # The viewer of one book; `captcha`, `showOne` and `baseUrl` variants carry other parameters and never match.
        values = _only_params(query, {"entityRef", "scanIndex"})
        ok = bool(values) and bool(_NA_BOOK_ENTITY.fullmatch(values["entityRef"][0])) and re.fullmatch(r"\d{1,5}", values["scanIndex"][0]) is not None
    return Route("na", "na_catalog", "GET", "na") if ok else None


PF_HOST = "www.portafontium.eu"
PF_SEARCH_PARAMS = frozenset({"search_api_views_fulltext", "search_api_views_fulltext_1", "search_api_views_fulltext_2",
                              "search_api_aggregation_3", "field_doc_dates_field_doc_dates_from", "page"})
PF_REGISTER_TYPES = ("register-birth", "register-marriage", "register-death", "index-birth", "index-marriage", "index-death")
PF_TYPE_PARAM = "field_register_type[]"
PF_SLUG = r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}"
PF_BOOK_TYPE_PARAM = "field_book_type[]"
PF_BOOK_TYPES = ("grundbuch", "urbar", "mannschaftsbuch", "stadtbuch", "ratsprotokoll", "briefprotokoll", "musterungsakten")
_PF_IMAGE = re.compile(r"(?:register|amtsbuch|census)(?:/[A-Za-z0-9_,:-][A-Za-z0-9_.,:-]{0,120}){1,10}")
_PF_IMAGE_KINDS = {"register/": "pf_images", "amtsbuch/": "pf_land_images", "census/": "pf_census_images"}


def _pf_search_ok(query: str, section: str = "register") -> bool:
    """The register search (typed by the register marks), the official-books search (typed by the book type) or the census
    search (which lists results only with mode=0, the list view). Every other parameter is refused."""
    values = parse_qs(query, keep_blank_values=True)
    typed = {"register": (PF_TYPE_PARAM, PF_REGISTER_TYPES), "amtsbuch": (PF_BOOK_TYPE_PARAM, PF_BOOK_TYPES), "census": (None, ())}[section]
    if section == "census" and values.get("mode") != ["0"]:
        return False
    for key, v in values.items():
        if key == typed[0]:
            if len(v) > len(typed[1]) or len(set(v)) != len(v) or any(x not in typed[1] for x in v):
                return False
        elif section == "census" and key == "mode":
            if v != ["0"]:
                return False
        elif key not in PF_SEARCH_PARAMS or len(v) != 1 or len(v[0]) > 128:
            return False
    page = values.get("page")
    return not page or bool(re.fullmatch(r"\d{1,3}", page[0]))


def match_pf_route(host: str, path: str, query: str, method: str) -> Route | None:
    """Porta fontium (SOA Plzeň): search form, book detail, viewer page and the iipsrv image server. GET only."""
    if method != "GET" or host != PF_HOST:
        return None
    if path == "/robots.txt" and not query:
        return Route("robots", "pf_robots", "GET", "pf")
    if path == "/searching/register":
        return Route("pf", "pf_catalog", "GET", "pf") if _pf_search_ok(query) else None
    if path == "/searching/amtsbuch":
        return Route("pf_land", "pf_catalog", "GET", "pf") if _pf_search_ok(query, "amtsbuch") else None
    if path == "/searching/census":
        return Route("pf_census", "pf_catalog", "GET", "pf") if _pf_search_ok(query, "census") else None
    if not query and re.fullmatch(rf"/amtsbuch/{PF_SLUG}/{PF_SLUG}/{PF_SLUG}", path):
        return Route("pf_land", "pf_catalog", "GET", "pf")
    if not query and re.fullmatch(rf"/census/{PF_SLUG}/\d{{4}}/{PF_SLUG}(?:/{PF_SLUG})?", path):
        return Route("pf_census", "pf_catalog", "GET", "pf")
    if not query and (re.fullmatch(rf"/register/{PF_SLUG}/{PF_SLUG}", path)
                      or re.fullmatch(r"/iipimage/\d{1,12}(?:/[\w.-]{1,120})?", path)):
        return Route("pf", "pf_catalog", "GET", "pf")
    if path == "/fcgi-bin/iipsrv.fcgi":
        image = next(iter(parse_qs(query).values()), [""])[0]
        prefix = next((p for p in _PF_IMAGE_KINDS if image.startswith(p)), "register/")
        _check_iipsrv(query, prefix)
        return Route(_PF_IMAGE_KINDS[prefix], "pf_images", "GET", "pf")
    return None


SOAPRAHA_HOST = "ebadatelna.soapraha.cz"
_SP_SID = r"(?:;jsessionid=[A-Za-z0-9._-]{8,200})?"     # a page that has just issued a session may write its links so
_SP_PAGE_VERSION = re.compile(r"\d{1,4}")
_SP_SEARCH_LISTENER = re.compile(r"\d{1,4}-\d{1,3}\.\d{1,3}-searchForm-searchButton")
_SP_NEXT_LISTENER = re.compile(r"\d{1,4}-\d{1,3}\.\d{1,3}-tableContainer-pagination-results-nextPageUrl")
_SP_FILE = r"[A-Za-z0-9][A-Za-z0-9_.,:()-]{0,300}"       # archival records name a scan after their title: "Scitani_lidu_1869:_Okres_X_-_obec_Y,_cp._1-50__001.jpg"
SOAPRAHA_SEARCH_FIELDS = ("searchType", "keywordBox:searchTokenIn", "keywordBox:searchToken", "dateYearFrom", "dateYearTo")
SOAPRAHA_ARCHIVE_FIELDS = ("searchLocality", "searchArchivalie", "typArchivalie", "mistoUlozeni", "archivniFondId", "dateYearFrom", "dateYearTo")


def check_soapraha_search_form(form: dict) -> None:
    """The two forms Rubrika may post to eBadatelna: the register search (keyword in localities over a year range) and the
    archival-records search (one locality from the archive's own list, optionally one archival type, over a year range).
    Exact names, fixed values, a bounded term and years within 1000-2100; anything else is refused before it leaves the machine."""
    if not isinstance(form, dict) or not all(isinstance(v, str) for v in form.values()):
        raise ResearchError("INVALID_SOURCE", "Formulář hledání eBadatelny má nepovolená pole.")
    if list(form) == list(SOAPRAHA_ARCHIVE_FIELDS):
        place, years = form["searchLocality"], (form["dateYearFrom"], form["dateYearTo"])
        if (not 1 <= len(place.strip()) <= 200 or any(ord(c) < 32 or c in "<>" for c in place)
                or form["searchArchivalie"] != "" or form["mistoUlozeni"] != "" or form["archivniFondId"] != ""
                or not re.fullmatch(r"(?:\d{1,3})?", form["typArchivalie"])
                or not all(re.fullmatch(r"\d{4}", y) for y in years) or not 1000 <= int(years[0]) <= int(years[1]) <= 2100):
            raise ResearchError("INVALID_SOURCE", "Hodnoty formuláře hledání archiválií eBadatelny jsou nepovolené.")
        return
    if list(form) != list(SOAPRAHA_SEARCH_FIELDS):
        raise ResearchError("INVALID_SOURCE", "Formulář hledání eBadatelny má nepovolená pole.")
    term = form["keywordBox:searchToken"]
    years = (form["dateYearFrom"], form["dateYearTo"])
    if (form["searchType"] != "1" or form["keywordBox:searchTokenIn"] != "radio0" or not 1 <= len(term.strip()) <= 100
            or any(ord(c) < 32 or c in "<>" for c in term) or not all(re.fullmatch(r"\d{4}", y) for y in years)
            or not 1000 <= int(years[0]) <= int(years[1]) <= 2100):
        raise ResearchError("INVALID_SOURCE", "Hodnoty formuláře hledání eBadatelny jsou nepovolené.")


def match_soapraha_route(host: str, path: str, query: str, method: str) -> Route | None:
    """State Regional Archives in Prague (eBadatelna, Apache Wicket). Reads only: the search page and the one AJAX
    listener that runs the register search (its next-page listener too), a register's detail and viewer pages, and
    the whole-scan JPEG download of a scan; the same for archival records (census, land books): their search page, the one
    search listener and next-page listener, a record's detail and the viewer /a/<id>/<n>. No login, registration, ZIP, notes
    or any other listener is reachable; the only POSTs are the two searches."""
    if host != SOAPRAHA_HOST:
        return None
    route = lambda source, group, m="GET": Route(source, group, m, "soapraha")
    if method == "GET":
        if path == "/robots.txt" and not query:
            return route("robots", "soapraha_robots")
        # robots.txt is answered by a redirect to Wicket's error page (200, HTML); the chain is followed, nothing else is
        if re.fullmatch(rf"/pages/ErrorPage{_SP_SID}", path) and (not query or _SP_PAGE_VERSION.fullmatch(query)):
            return route("robots", "soapraha_robots")
        if re.fullmatch(rf"/pages/(?:SearchMatrikaPage|MatrikaPage/matrikaId/\d{{1,9}}){_SP_SID}", path):
            if not query or _SP_PAGE_VERSION.fullmatch(query) or (path.startswith("/pages/SearchMatrikaPage") and _SP_NEXT_LISTENER.fullmatch(query)):
                return route("soapraha", "soapraha_catalog")
            return None
        if re.fullmatch(rf"/pages/(?:SearchArchivaliePage|ArchivaliePage/archivalieId/\d{{1,9}}){_SP_SID}", path):
            if not query or _SP_PAGE_VERSION.fullmatch(query) or (path.startswith("/pages/SearchArchivaliePage") and _SP_NEXT_LISTENER.fullmatch(query)):
                return route("soapraha_archivalie", "soapraha_catalog")
            return None
        if not query and (re.fullmatch(rf"/pages/DataArchivaliePage/archivalieId/\d{{1,9}}(?:/idx/[1-9]\d{{0,4}})?{_SP_SID}", path)
                          or re.fullmatch(r"/a/\d{1,9}/[1-9]\d{0,4}", path)):
            return route("soapraha_archivalie", "soapraha_catalog")
        if not query and (re.fullmatch(rf"/pages/DataMatrikaPage/idx/[1-9]\d{{0,4}}/matrikaId/\d{{1,9}}{_SP_SID}", path)
                          or re.fullmatch(r"/d/\d{1,9}/[1-9]\d{0,4}", path)):
            return route("soapraha", "soapraha_catalog")
        if not query and re.fullmatch(rf"/wicket/resource/org\.apache\.wicket\.Application/{_SP_FILE}__\d{{3,4}}\.jpg", path):
            return route("soapraha_images", "soapraha_images")
    elif method == "POST" and re.fullmatch(rf"/pages/SearchMatrikaPage{_SP_SID}", path) and _SP_SEARCH_LISTENER.fullmatch(query):
        return route("soapraha", "soapraha_catalog", "POST")
    elif method == "POST" and re.fullmatch(rf"/pages/SearchArchivaliePage{_SP_SID}", path) and _SP_SEARCH_LISTENER.fullmatch(query):
        return route("soapraha_archivalie", "soapraha_catalog", "POST")
    return None


TREBON_HOST = "digi.ceskearchivy.cz"
_TB_DOCTREE_LETTER = re.compile(r"1nr[a-z]")                       # a letter of the Roman Catholic parishes: 1nrc = C/Č
_TB_DOCTREE_PARISH = re.compile(r"1nr[a-z][A-Za-z0-9]{1,3}")       # one parish under a letter: 1nrcl
# Non-Catholic confessions and civil registers: a root (1nC evangelical, 1nb civil ...) lists its congregations directly, each with an id.
_TB_CONFESSION_ROOTS = frozenset({"1nC", "1nc", "1ne", "1ng", "1np", "1nb", "1nh", "1ni"})
_TB_CONFESSION_ENTITY = re.compile(r"1n[Ccegpbhi][A-Za-z0-9]{1,5}")
# Land registers (1q), census (1s), seigniorial registers (1t), urbariums (1U): archive → (year → court district →) letter → entity with an id.
_TB_SECTION_NODE = re.compile(r"1[qstU][A-Za-z0-9]{0,8}")             # a branch node: no id
_TB_SECTION_LEAF = re.compile(r"1[qstU][A-Za-z0-9]{3,8}")             # a municipality, estate or fund with its table of books: has an id


def match_trebon_route(host: str, path: str, query: str, method: str) -> Route | None:
    """State Regional Archive in Třeboň (DigiArchiv), GET only. The tree of parishes, congregations and units (letters, entities with
    a table of books), the viewer page of one scan (HTML only). No image request of any kind (the archive allows its scans to be viewed
    in DigiArchiv only). Nothing under /pages/ (search, help; robots.txt disallows it), no /oai/, no login, registration, export,
    bookmark or download script is reachable."""
    if method != "GET" or host != TREBON_HOST:
        return None
    if path == "/robots.txt" and not query:
        return Route("robots", "trebon_robots", "GET", "trebon")
    if path == "/tree.php":
        values = _only_params(query, {"menu", "doctree", "lang"}, {"menu", "doctree", "id", "lang"})
        if not values or values["menu"][0] != "0" or values["lang"][0] != "cs":
            return None
        doctree = values["doctree"][0]
        has_id = "id" in values
        if has_id and not re.fullmatch(r"\d{1,7}", values["id"][0]):
            return None
        letters = doctree == "1nr" or _TB_DOCTREE_LETTER.fullmatch(doctree) or doctree in _TB_CONFESSION_ROOTS
        if ((letters and not has_id) or (_TB_DOCTREE_PARISH.fullmatch(doctree) and has_id)
                or (_TB_CONFESSION_ENTITY.fullmatch(doctree) and has_id)
                or (_TB_SECTION_LEAF.fullmatch(doctree) if has_id else _TB_SECTION_NODE.fullmatch(doctree))):
            return Route("trebon", "trebon_catalog", "GET", "trebon")
        return None
    if path == "/view.php":
        values = _only_params(query, {"menu", "id", "page"})
        if (values and values["menu"][0] == "3" and re.fullmatch(r"\d{1,7}", values["id"][0])
                and re.fullmatch(r"[1-9]\d{0,4}", values["page"][0])):
            return Route("trebon", "trebon_catalog", "GET", "trebon")
        return None
    return None


PRIHLASKY_HOST = "digi.nacr.cz"
PRIHLASKY_APP = "/prihlasky2"
PRIHLASKY_REF = "czarch:CZ-100000010:874"        # the National Archives' reference of the fund, fixed in every permalink
PRIHLASKY_SEARCH_FIELDS = ("prijmeni", "jmeno", "odroku", "doroku", "action", "tlacitko")
_PR_TOKEN = re.compile(r"[0-9a-f]{64}")
_PR_IMAGE = re.compile(rf"{PRIHLASKY_APP}/images/\d{{8}}-\d{{8}}\.jpg")


def check_prihlasky_search_form(form: dict) -> None:
    """The one form Rubrika may post to the police registration forms: the extended search (surname and given name begin with,
    birth years from-to). Exact names in the archive's order, the fixed action and button, bounded names and years; anything
    else is refused before it leaves the machine."""
    if not isinstance(form, dict) or list(form) != list(PRIHLASKY_SEARCH_FIELDS) or not all(isinstance(v, str) for v in form.values()):
        raise ResearchError("INVALID_SOURCE", "Formulář hledání přihlášek má nepovolená pole.")
    surname, given = form["prijmeni"], form["jmeno"]
    years = (form["odroku"], form["doroku"])
    names_ok = (1 <= len(surname.strip()) <= 60 and len(given) <= 60
                and not any(ord(c) < 32 or c in "<>" for c in surname + given))
    years_ok = (years == ("", "") or (all(re.fullmatch(r"\d{4}", y) for y in years) and 1700 <= int(years[0]) <= int(years[1]) <= 1930))
    if form["action"] != "hledani2" or form["tlacitko"] != "Najít" or not names_ok or not years_ok:
        raise ResearchError("INVALID_SOURCE", "Hodnoty formuláře hledání přihlášek jsou nepovolené.")


def match_prihlasky_route(host: str, path: str, query: str, method: str) -> Route | None:
    """Police registration forms of Prague 1850-1914 (National Archives, digi.nacr.cz, PHP, plain HTTP). Reads: the entry page
    (it issues a session token), the paging of a result list, one result's detail page, the permalink of a form (no session)
    and the whole-scan JPEG of a form. The one POST is the extended search. Simple and detailed search, new query, ordering,
    language, the information pages and files are not reachable."""
    if host != PRIHLASKY_HOST:
        return None
    route = lambda source, group, m="GET": Route(source, group, m, "na_prihlasky")
    if method == "GET" and path == "/robots.txt" and not query:
        return route("robots", "prihlasky_robots")
    if method == "GET" and not query and _PR_IMAGE.fullmatch(path):
        return route("prihlasky_images", "prihlasky_images")
    if path not in (PRIHLASKY_APP + "/", PRIHLASKY_APP + "/index.php"):
        return None
    if method == "POST":
        values = _only_params(query, {"session"})
        return route("prihlasky", "prihlasky_catalog", "POST") if values and _PR_TOKEN.fullmatch(values["session"][0]) else None
    if method != "GET":
        return None
    if not query:
        return route("prihlasky", "prihlasky_catalog")
    values = _only_params(query, {"session"}, {"session", "action", "od"}, {"session", "action", "record"},
                          {"action", "ref", "karton", "folium"})
    if not values:
        return None
    if "session" in values and not _PR_TOKEN.fullmatch(values["session"][0]):
        return None
    if "od" in values:
        ok = values["action"][0] == "list" and re.fullmatch(r"\d{1,5}", values["od"][0])
    elif "record" in values:
        ok = values["action"][0] == "image" and re.fullmatch(r"[1-9]\d{0,5}", values["record"][0])
    elif "ref" in values:
        ok = (values["action"][0] == "link" and values["ref"][0] == PRIHLASKY_REF
              and re.fullmatch(r"\d{1,4}", values["karton"][0]) and re.fullmatch(r"\d{1,6}", values["folium"][0]))
    else:
        ok = True
    return route("prihlasky", "prihlasky_catalog") if ok else None


# Forms an archive may be sent: checked by the network layer before a POST leaves the machine.
FORM_CHECKS = {"soapraha": check_soapraha_search_form, "na_prihlasky": check_prihlasky_search_form}
