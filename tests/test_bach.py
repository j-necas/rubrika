"""Bach adapter: parsers on synthetic HTML (structure changes fail closed), routes, AHMP naming and coverage rules."""
import re

import pytest

from rubrika import archives as A
from rubrika import demo_ahmp as D
from rubrika.ahmp import Parish, coverage_of, match_score, sort_key, tokens
from rubrika.bach import AHMP_SITE as S
from rubrika.bach import BookRow, manifest_hash, parse_node, parse_viewer
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

XID = "0123456789ABCDEF0123456789ABCDEF"
SCAN_LIMIT = 80000000


@pytest.fixture
def arch():
    return D.AhmpArchive()


def node(arch, node_id, first=0, session="s"):
    return arch._node_page(session, node_id, first)


def parsed(arch, node_id, first=0):
    return parse_node(node(arch, node_id, first), S, S.node_url(node_id), node_id)


def code_of(fn, *args):
    with pytest.raises(ResearchError) as e:
        fn(*args)
    return e.value.code


# ---------------------------------------------------------------- tree nodes

def test_container_node_lists_child_nodes_with_kind_and_count(arch):
    page = parsed(arch, D.INNER)
    assert [(c.id, c.expandable, c.count) for c in page.children] == [
        (D.DISTRICT_1, True, D.count(D.DISTRICT_1)), (D.DISTRICT_2, True, D.count(D.DISTRICT_2)), (D.VYSEHRAD, False, 2)]
    assert page.name == "vnitřní obvody" and page.books == [] and page.total == 3 and page.next_url is None
    assert [n for _, n in page.ancestors] == ["katolické", "vnitřní obvody"] and page.ancestors[-1][0] == D.INNER


def test_paged_node_gives_forward_link_and_last_page_stops(arch):
    first = parsed(arch, D.DISTRICT_2)
    assert len(first.children) == 9 and first.total == 11 and (first.first, first.last) == (1, 9)
    assert first.next_url.startswith("https://katalog.ahmp.cz/pragapublica/PaginatorNavig.action?_sourcePage=")
    assert first.next_url.endswith("&row=9")
    assert SourcePolicy(A.integrated_hosts()).validate(first.next_url).group == "ahmp_catalog"
    last = parsed(arch, D.DISTRICT_2, 9)
    assert len(last.children) == 2 and (last.first, last.last) == (10, 18) and last.next_url is None   # "10 - 18 / 1 - 11"


def test_book_rows_yield_signature_years_scans_and_upper_case_xid(arch):
    page = parsed(arch, D.TESTA)
    assert page.children == [] and page.total == 12 and len(page.books) == 9
    first = page.books[0]
    assert (first.signature, first.years, first.scans) == ("TST N1", "1780-1800", 3)
    assert first.xid == D.xid_of(D.TESTA, "TST N1 • 1780-1800") and re.fullmatch(r"[0-9A-F]{32}", first.xid)
    undated = next(b for b in page.books if b.signature == "TST N3")
    assert undated.years is None and undated.label == "TST N3"


def test_undigitised_row_is_reported_never_dropped(arch):
    html = node(arch, D.MARIE)
    html = re.sub(r'<a href="[^"]*Zoomify\.action[^"]*"[^>]*class="attachments"[^>]*>.*?</a>', "", html, count=1)
    page = parse_node(html, S, S.node_url(D.MARIE), D.MARIE)
    assert len(page.books) == 2 and len(page.warnings) == 1 and "PMD N1" in page.warnings[0]


def test_empty_node_is_an_explicit_zero_not_a_parse_failure(arch):
    page = parsed(arch, 900200)
    assert page.total == 0 and page.books == [] and page.children == []


@pytest.mark.parametrize("mutate,label", [
    (lambda h: h.replace("categoryBlock", "categoryBox"), "tree markup renamed"),
    (lambda h: h.replace("contentBlock", "contentBox"), "content markup renamed"),
    (lambda h: re.sub(r'<div class="navigatorLine listArticle navigatorLineTerminal">.*?</div>', "", h, count=1), "row lost"),
    (lambda h: h.replace("Nalezeny", "Zobrazeno").replace("paginationBlock", "pg"), "no count"),
    (lambda h: h.replace("icon-minus", "icon-nothing"), "unknown icon"),
    (lambda h: h.replace("title=\"Zobrazit v plné kvalitě 3 obrázků\"", "title=\"?\""), "no scan count"),
    (lambda h: h.replace("&amp;entityType=10099", "&amp;entityType=1"), "other record type"),
])
def test_structure_changes_fail_with_parse_error(arch, mutate, label):
    for node_id in (D.INNER, D.TESTA):
        original = node(arch, node_id)
        html = mutate(original)
        if html == original:
            continue
        assert code_of(parse_node, html, S, S.node_url(node_id), node_id) == "PARSE_ERROR", label


def test_wrong_node_is_refused(arch):
    assert code_of(parse_node, node(arch, D.INNER), S, S.node_url(D.OUTER), D.OUTER) == "PARSE_ERROR"


@pytest.mark.parametrize("bad", [
    "https://evil.example/pragapublica/PaginatorNavig.action?_sourcePage=aaaaaaaaaaaaaaaaaaaa&amp;row=9",
    "/pragapublica/PaginatorNavig.action?_sourcePage=aaaaaaaaaaaaaaaaaaaa&amp;row=9&amp;captcha=1",
    "/pragapublica/PaginatorNavig.action;jsessionid=ABC?_sourcePage=aaaaaaaaaaaaaaaaaaaa&amp;row=9",
    "/pragapublica/BasketBean.action?_sourcePage=aaaaaaaaaaaaaaaaaaaa&amp;row=9",
])
def test_pager_never_leads_outside_the_catalogue_reading_routes(arch, bad):
    html = re.sub(r'href="[^"]*PaginatorNavig\.action[^"]*"', f'href="{bad}"', node(arch, D.DISTRICT_2), count=1)
    assert code_of(parse_node, html, S, S.node_url(D.DISTRICT_2), D.DISTRICT_2) == "PARSE_ERROR"


def test_expired_session_page_is_recognised_not_taken_for_an_empty_tree():
    html = "<html><body><h1>Platnost stránky vypršela</h1></body></html>"
    with pytest.raises(ResearchError) as e:
        parse_node(html, S, S.node_url(1), 1)
    assert e.value.code == "SOURCE_UNAVAILABLE" and e.value.details["reason"] == "session_expired"


# ---------------------------------------------------------------- viewer

def viewer(arch, scan=2, xid=None):
    xid = xid or D.xid_of(D.TESTA, "TST N1 • 1780-1800")
    return xid, arch._viewer(xid, scan)


def test_viewer_gives_root_size_signature_and_total(arch):
    xid, html = viewer(arch)
    v = parse_viewer(html, S, xid, 2, SCAN_LIMIT)
    assert v.image_root == D.root_of(xid, 2) and (v.width, v.height) == D.scan_size(2)
    assert (v.signature, v.scan, v.total, v.xid) == ("TST N1 • 1780-1800", 2, 3, xid)


def test_viewer_comments_are_not_source(arch):
    xid, html = viewer(arch)
    other = D.root_of(xid, 3)
    html = html.replace("    // alert(", f'    // var zoomifyImgPath = "{other}";\n    // alert(')
    assert parse_viewer(html, S, xid, 2, SCAN_LIMIT).image_root == D.root_of(xid, 2)


@pytest.mark.parametrize("mutate", [
    lambda h: h.replace("var zoomifyImgPath", "var zoomifyImgPaths"),
    lambda h: h + '\n<script>\nvar zoomifyImgPath = "https://images.ahmp.cz/x.jpg";\n</script>',   # a second root
    lambda h: h.replace('name="rowTxt"', 'name="rowText"'),
    lambda h: h.replace('class="number"', 'class="num"'),
    lambda h: h.replace("a class=\"text headline\"", "a class=\"text title\""),
    lambda h: h.replace("NUMTILES='", "NUMTILES='9"),                                          # descriptor contradicts itself
    lambda h: re.sub(r'zoomifyImgPath = "https://images\.ahmp\.cz', 'zoomifyImgPath = "https://elsewhere.example', h),
    lambda h: h.replace(".jpg\";", ".jpg?x=1\";", 1),
])
def test_viewer_structure_changes_fail_closed(arch, mutate):
    xid, html = viewer(arch)
    assert code_of(parse_viewer, mutate(html), S, xid, 2, SCAN_LIMIT) == "PARSE_ERROR"


def test_viewer_must_show_the_requested_scan_and_book(arch):
    xid, html = viewer(arch)
    assert code_of(parse_viewer, html, S, xid, 3, SCAN_LIMIT) == "PARSE_ERROR"
    assert code_of(parse_viewer, html, S, XID, 2, SCAN_LIMIT) == "PARSE_ERROR"


def test_manifest_hash_binds_book_and_scan_count():
    assert manifest_hash(S, XID.lower(), 5) == manifest_hash(S, XID, 5) != manifest_hash(S, XID, 6)


# ---------------------------------------------------------------- names, matching, coverage

@pytest.mark.parametrize("term,name,path,hit", [
    ("sv. Jindřich", "Kostel sv. Jindřicha (JCH)", ("vnitřní obvody", "Praha II - Nové Město"), True),
    ("svatý Jindřich", "Kostel sv. Jindřicha (JCH)", (), True),
    ("JCH", "Kostel sv. Jindřicha (JCH)", (), True),
    ("Nové Město", "Kostel sv. Jindřicha (JCH)", ("vnitřní obvody", "Praha II - Nové Město"), True),
    ("Nový Jindřich", "Kostel sv. Jindřicha (JCH)", ("Praha II - Nové Město",), False),
    ("Praha 2", "Kostel sv. Jindřicha (JCH)", ("Praha II - Nové Město",), True),
    ("Vinohrady", "Kostel sv. Ludmily (LUD)", ("Praha XII - Královské Vinohrady",), True),
    ("Kateřina", "Kostel sv. Kateřiny (KAT)", (), True),
    ("sv. Petr", "Kostel sv. Petra na Poříčí (PE) fara sv. Petra a sv. Klimenta", (), True),
    ("sv. Pavel", "Kostel sv. Petra na Poříčí (PE)", (), False),
    ("Bohnice", "Bohnice", (), True),
    ("kostel", "Bohnice", (), None),          # only stop words: no term left
])
def test_place_matching_is_fuzzy_on_word_forms_and_uses_names_above(term, name, path, hit):
    parish = Parish(1, name, (*path, name), 1)
    words = [t for t in tokens(term) if t not in {"kostel", "kostela", "farnost", "fara", "fary"}]
    if hit is None:
        assert words == []
    else:
        assert (match_score(words, parish) is not None) is hit


def test_own_name_outranks_a_match_in_the_path():
    inside = Parish(1, "Kostel sv. Jindřicha (JCH)", ("Praha II - Nové Město", "Kostel sv. Jindřicha (JCH)"), 1)
    under = Parish(2, "Kostel sv. Apolináře (AP)", ("Jindřichovo Předměstí", "Kostel sv. Apolináře (AP)"), 1)
    assert match_score(["jindrich"], under) == 1 and match_score(["jindrich"], inside) == 2


def test_label_drops_the_obvod_level_only_when_something_else_names_the_place():
    p = Parish(9, "Kostel sv. Jindřicha (JCH)", ("vnitřní obvody", "Praha II - Nové Město", "Kostel sv. Jindřicha (JCH)"), 89)
    assert p.label == "Praha II - Nové Město › Kostel sv. Jindřicha (JCH)"
    # directly below the container nothing else names the place, so the container stays in the label
    assert Parish(9, "Braník (BRN)", ("vnější obvody", "Braník (BRN)"), 2).label == "vnější obvody › Braník (BRN)"
    assert Parish(9, "sv. Ludmila", ("vnější obvody", "Královské Vinohrady", "sv. Ludmila"), 2).label == "Královské Vinohrady › sv. Ludmila"


def periods(cov, event, which):
    p = getattr(cov[event], which)
    return p.status, [(s.start, s.end) for s in p.spans]


def test_coverage_follows_signature_letters():
    cov = coverage_of("JCH N4", "1638-1656")
    assert periods(cov, "birth", "records") == ("known", [(1638, 1656)])
    assert cov["marriage"].records.status == cov["death"].records.status == "uncertain"      # not named is not proof of absence
    both = coverage_of("TST NOZ4", "1700-1779")
    assert all(periods(both, e, "records") == ("known", [(1700, 1779)]) for e in ("birth", "marriage", "death"))
    assert periods(coverage_of("TST Z2", "1821-1850"), "death", "records") == ("known", [(1821, 1850)])
    assert periods(coverage_of("TST O10", "1811"), "marriage", "records") == ("known", [(1811, 1811)])


def test_undated_index_and_unknown_books_are_never_excluded():
    assert coverage_of("TST N3", None)["birth"].records.status == "uncertain"
    index = coverage_of("TST i1", "1780-1820")
    assert all(index[e].records.status == "absent" and index[e].index.status == "uncertain" for e in index)
    assert index["birth"].index.spans[0].start == 1780
    unknown = coverage_of("TST X9", "1700-1710")
    assert all(unknown[e].records.status == unknown[e].index.status == "uncertain" for e in unknown)
    assert coverage_of("Bez signatury", "1700")["birth"].records.status == "uncertain"


def test_natural_signature_order():
    assert sorted(["TST N10", "TST N2", "TST N1"], key=sort_key) == ["TST N1", "TST N2", "TST N10"]


# ---------------------------------------------------------------- routes

def route(url, method="GET"):
    return SourcePolicy(A.integrated_hosts()).validate(url, method)


ENTITY = "%28%5En%29%28%28%28localArchiv%2C%5En%2Chot_%29%28unidata%29%29%28108399%29%29"
TOKEN = "SyntheticPagerToken_" * 5 + "%3D"  # synthetic; never reuse an archive session token
BASE = "https://katalog.ahmp.cz/pragapublica/"


@pytest.mark.parametrize("url", [
    BASE + "NavigBean.action?entity=" + ENTITY,
    BASE + "PaginatorNavig.action?_sourcePage=" + TOKEN + "&row=9",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26",
    BASE + "permalink?xid=f4e5d3a7fd2a4974976108ad65cb9d26&scan=413",
    "https://katalog.ahmp.cz/robots.txt",
])
def test_catalogue_routes_allowed(url):
    r = route(url)
    assert (r.archive, r.method) == ("ahmp", "GET") and r.group in ("ahmp_catalog", "ahmp_robots")
    assert route(url).group == ("ahmp_robots" if url.endswith("robots.txt") else "ahmp_catalog")


@pytest.mark.parametrize("url", [
    BASE + "Zoomify.action?captcha=&baseUrl=https://images.ahmp.cz/mrimage/ahmp/",
    BASE + "Zoomify.action?xid=F4E5D3A7FD2A4974976108AD65CB9D26&entityType=10099&entityRef=" + ENTITY + "&scanIndex=0",
    BASE + "Zoomify.action?showOne=&_sourcePage=" + TOKEN + "&rowPg=0",
    BASE + "PaginatorMedia.action?_sourcePage=" + TOKEN + "&row=4",
    BASE + "BasketBean.action",
    BASE + "BasketBean.action?add=1",
    BASE + "pages/badatel/login.jsp",
    BASE + "SearchBean.action?searchType=basic",
    BASE + "VysledekBean.action?show=&_sourcePage=" + TOKEN + "&rowPg=1",
    BASE + "NavigBean.action?entity=" + ENTITY + "&captcha=1",
    BASE + "NavigBean.action?entity=" + ENTITY + "&entity=" + ENTITY,
    BASE + "NavigBean.action?entity=%28%5En%29%28%28%28localArchiv%2C%5En%2Chot_%29%28unidata%29%29%28abc%29%29",
    BASE + "NavigBean.action?entity=%28%5En%29%28%28%28localArchiv%2C%5En%2Chot_%29%28unidata%29%29%281%29%29%3Bx",
    BASE + "NavigBean.action?eventNavigPager=&_sourcePage=" + TOKEN + "&rowPg=0",
    BASE + "NavigBean.action",
    BASE + "PaginatorNavig.action?_sourcePage=" + TOKEN,
    BASE + "PaginatorNavig.action?_sourcePage=" + TOKEN + "&row=9&x=1",
    BASE + "PaginatorNavig.action?_sourcePage=a%20b&row=9",
    BASE + "PaginatorNavig.action?_sourcePage=" + TOKEN + "&row=-1",
    BASE + "permalink",
    BASE + "permalink?xid=xyz",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26&scan=0",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26&scan=1&captcha=1",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26&scan=1&scan=2",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26&scan=-3",
    BASE + "index.jsp",
    "https://katalog.ahmp.cz/pragapublica/../admin",
    "https://katalog.ahmp.cz/robots.txt?x=1",
    "http://katalog.ahmp.cz/pragapublica/permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26",
])
def test_catalogue_routes_forbidden(url):
    with pytest.raises(ResearchError) as e:
        route(url)
    assert e.value.code == "INVALID_SOURCE"


@pytest.mark.parametrize("url", [
    BASE + "NavigBean.action?entity=" + ENTITY,
    BASE + "PaginatorNavig.action?_sourcePage=" + TOKEN + "&row=9",
    BASE + "permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26",
])
def test_catalogue_is_read_only(url):
    for method in ("POST", "PUT", "DELETE"):
        with pytest.raises(ResearchError):
            route(url, method)


def test_no_query_on_a_permalink_of_another_host():
    with pytest.raises(ResearchError):
        route("https://images.ahmp.cz/pragapublica/permalink?xid=F4E5D3A7FD2A4974976108AD65CB9D26")
    with pytest.raises(ResearchError):
        route("https://images.ahmp.cz/pragapublica/NavigBean.action?entity=" + ENTITY)


def test_url_builders_match_the_real_shapes():
    assert S.node_url(108399) == BASE + "NavigBean.action?entity=" + ENTITY
    assert S.permalink_url(XID, 7) == BASE + f"permalink?xid={XID}&scan=7"
    assert S.permalink_url(XID) == BASE + f"permalink?xid={XID}"
    with pytest.raises(ResearchError):
        S.permalink_url("not-hex")
    assert S.entity_re.fullmatch("(0)(((localArchiv,^n,hot_)(unidata))(1))")
    assert S.entity_re.fullmatch("(^n)(((localArchiv,^n,hot_)(unidata))(108399))")
    assert not S.entity_re.fullmatch("(^n)(((localArchiv,^n,hot_)(unidata))(x))")
