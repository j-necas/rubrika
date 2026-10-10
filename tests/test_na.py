"""Národní archiv (Jewish registers, fund HBMa) end to end on the synthetic VadeMeCum: find_place → list_sources →
open_source → view_scan → zoom_scan, plus the parsers that read the application's HTML."""
import base64
import dataclasses
import io
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from PIL import Image

from rubrika import archives as A
from rubrika import demo_na as D
from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.na import (BookRow, build_places, coverage_of, match_score, parse_viewer, parse_volume, _names)
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

QUERY = {"raw_query": "SYNTHETIC NA", "place_hint": "Plzeň", "event_type": "birth", "year_from": 1865, "year_to": 1868}
VOLUME_URL = "https://vademecum.nacr.cz/vademecum/BookVolume.action?browseAid=&entity=%28%5En%29%28%28%2820cb6cc9d41144e5874020c1e918c34f%29%28de1e5208398e4a7eb52df0f4687e7dd2%29%29%284137%29%29"


@pytest.fixture(autouse=True)
def fast_hosts(monkeypatch):
    for code in ("ahmp", "na"):
        monkeypatch.setitem(A.ARCHIVES, code, dataclasses.replace(A.ARCHIVES[code], min_interval=0))


def build(tmp_path, archive=None, **cfg_args):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, na_enabled=True, request_interval=0, **cfg_args)
    store = Store(cfg)
    archive = archive or D.NaArchive()
    source = SafeSource(cfg, store, transport=archive.transport, source_mode="synthetic_demo")
    return ResearchService(cfg, store, source), archive, store


@pytest_asyncio.fixture
async def env(tmp_path):
    service, archive, store = build(tmp_path)
    yield service, archive, store
    await service.close()
    store.close()


async def call(service, name, **args):
    out = await service.call(name, "alice", args)
    assert not out.is_error, out.data
    return out


async def failing(service, name, **args):
    out = await service.call(name, "alice", args)
    assert out.is_error, out.data
    return out.data["error"]


async def research(service, query=QUERY):
    return (await call(service, "start_research", query=query)).data["research_id"]


async def find(service, sid, term="Plzeň", **args):
    args.setdefault("archives", ["na"])
    return (await call(service, "find_place", research_id=sid, term=term, kind="parish_registers", **args)).data


async def listing(service, sid, term="Plzeň", index=0, **args):
    places = await find(service, sid, term)
    listed = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][index]["source_id"]],
                        kind="parish_registers", **args)
    return places, listed.data


async def opened(service, sid, number="1541"):
    _, listed = await listing(service, sid, limit=20)
    book = next(s for s in listed["sources"] if s["number"] == number)
    return (await call(service, "open_source", research_id=sid, source_id=book["source_id"])).data


def paths(archive):
    return [re.sub(r"[?;].*", "", u.split("/vademecum/")[1]) for u in archive.catalog_requests() if "/vademecum/" in u]


# ---------------------------------------------------------------- parsers

def volume_html(start=0, **kw):
    archive = D.NaArchive(**kw)
    return archive._volume("ABC123456789", start), archive


def test_volume_page_rows_pager_and_table_of_contents():
    html, _ = volume_html()
    page = parse_volume(html, VOLUME_URL, want_toc=True)
    assert (page.start, page.total, len(page.rows)) == (0, D.TOTAL, 12)
    assert [r.kind for r in page.rows[:8]] == ["header", "header", "book", "book", "book", "book", "book", "header"]
    book = page.rows[2]
    assert (book.text, book.scans, book.descr, book.entity_id) == ("1", 4, "N 1849-1871 O 1841-1856 Z 1850-1870", D.entity_of(2))
    assert page.next_url.startswith("https://vademecum.nacr.cz/vademecum/PaginatorBookVolume.action?_sourcePage=") and page.next_url.endswith("&row=12")
    assert len(page.toc) == sum(1 for r in D.ROWS if r[0] == "h")
    plzen = next(t for t in page.toc if t.text == "PLZEŇ (o. Plzeň)")
    assert plzen.n == D.n_of("PLZEŇ") and plzen.level == 40
    assert "zanchor=%23bookResume" in plzen.url and f"rowTxt={plzen.n}" in plzen.url and "jsessionid" not in plzen.url


def test_kinds_of_inventory_rows():
    kinds = {}
    for start in range(0, D.TOTAL, 12):
        kinds.update({r.text: r.kind for r in parse_volume(volume_html(start=start)[0], VOLUME_URL).rows})
    assert kinds["— ANNÍN - viz Kundratice"] == "reference" and kinds["inv. č. 6"] == "undigitised" and kinds["5"] == "book"


def test_links_written_with_a_session_id_in_the_path_are_understood():
    html, _ = volume_html(jsessionid_links=True)
    assert "jsessionid" in html
    page = parse_volume(html, VOLUME_URL, want_toc=True)
    assert "jsessionid" not in page.next_url and all("jsessionid" not in t.url for t in page.toc)


def test_expired_and_foreign_pages_are_never_an_empty_result():
    with pytest.raises(ResearchError) as e:
        parse_volume("<html><h1>Page has Expired</h1></html>", VOLUME_URL)
    assert e.value.code == "SOURCE_UNAVAILABLE" and e.value.details["reason"] == "session_expired"
    with pytest.raises(ResearchError) as e:
        parse_volume("<html><body>nothing</body></html>", VOLUME_URL)
    assert e.value.code == "PARSE_ERROR"


@pytest.mark.parametrize("change", [
    lambda h: h.replace("Celkem", "Hotovo"),                                              # no total
    lambda h: h.replace("rowPg=1\"", "rowPg=5\"", 1),                                    # rows out of order
    lambda h: h.replace("entityType=9019", "entityType=777", 1),                          # unknown record type
    lambda h: h.replace("entityType=10018", "entityType=9019", 1),                        # a header that says it is a book
    lambda h: re.sub(r"(<aricle.*?</aricle>)", "", h, count=1, flags=re.S),               # a row missing
    lambda h: h.replace("&amp;row=12", "&amp;row=24"),                                    # pager skips a page
    lambda h: h.replace("scanIndex=0", "scanIndex=3", 1),
    lambda h: h.replace("skenů", "obrázků", 1),
    lambda h: h.replace("PAGER_RESULT_BOOK", "OTHER", 1),
    lambda h: h.replace("BookVolume.action?show", "BasketBean.action?show", 1),
])
def test_unexpected_inventory_structure_is_a_parse_error(change):
    html, _ = volume_html()
    with pytest.raises(ResearchError) as e:
        parse_volume(change(html), VOLUME_URL, want_toc=True)
    assert e.value.code == "PARSE_ERROR"


@pytest.mark.parametrize("change", [
    lambda h: h.replace("zanchor=%23bookResume2", "zanchor=%23bookResume9", 1),           # link does not match the item
    lambda h: h.replace('id="bookResume2"', 'id="bookResumeX"', 1),
    lambda h: h.replace("padding-left:40px", "padding-left:41px", 1),
])
def test_unexpected_table_of_contents_is_a_parse_error(change):
    html, _ = volume_html()
    with pytest.raises(ResearchError):
        parse_volume(change(html), VOLUME_URL, want_toc=True)


def items_of(html):
    page = parse_volume(html, VOLUME_URL, want_toc=True)
    return [(t.n, t.level, t.text) for t in page.toc], page.total


def test_places_index_sections_and_prague_quarters():
    html, _ = volume_html()
    places = {p.n: p for p in build_places(*items_of(html))}
    labels = [p.label for p in places.values()]
    assert "index" not in " ".join(labels) and "indexy" not in " ".join(labels)          # index sub-headings belong to the place above
    zkusov = places[D.n_of("ZKUŠOV")]
    assert zkusov.rows == 7 and zkusov.okres == "Testov" and zkusov.names == ("ZKUŠOV",)     # six books and the index heading
    assert places[D.n_of("PRAHA - matriky narozených")].label == "PRAHA - matriky narozených"
    assert places[D.n_of("PRAHA-KARLÍN", 0)].label == "PRAHA - matriky narozených › PRAHA-KARLÍN"
    assert places[D.n_of("PRAHA-KARLÍN", 1)].label == "PRAHA - matriky oddaných › PRAHA-KARLÍN"
    assert places[D.n_of("LOUNY")].end == D.n_of("Citoliby")                                   # a second-level name is a place of its own
    assert places[D.n_of("Citoliby")].label == "LOUNY (o. Louny) › Citoliby" and places[D.n_of("Citoliby")].end == D.TOTAL + 1
    assert places[D.n_of("DAVLE")].rows == 0
    assert places[D.n_of("PRAHA-KARLÍN", 0)].context == "PRAHA - matriky narozených"
    assert 1 not in places                                                                     # the root heading is no place


@pytest.mark.parametrize("header,names,okres", [
    ("PLZEŇ (o. Plzeň)", ("PLZEŇ",), "Plzeň"),
    ("BABČICE ((o. Tábor)", ("BABČICE",), "Tábor"),
    ("BARCHŮVEK (o, Hradec Králové)", ("BARCHŮVEK",), "Hradec Králové"),
    ("BĚLOHRAD (Lázně Bělohrad, o. Jičín)", ("BĚLOHRAD", "Lázně Bělohrad"), "Jičín"),
    ("CETORAZ (o. Pelhřim", ("CETORAZ",), "Pelhřim"),
    ("DOLNÍ BOLÍKOV (o.", ("DOLNÍ BOLÍKOV",), None),
    ("DOUPOV (dř. o. Kadaň)", ("DOUPOV",), "Kadaň"),
    ("VELIŠ (Benešov)", ("VELIŠ", "Benešov"), None),
    ("DAVLE viz Roztoky", ("DAVLE",), None),
    ("PRAHA-KARLÍN", ("PRAHA-KARLÍN",), None),
])
def test_headers_as_the_inventory_really_writes_them(header, names, okres):
    base, alt, found = _names(header)
    assert (tuple(dict.fromkeys([base, *alt])), found) == (names, okres)


def test_matching_places_by_name_district_and_context():
    html, _ = volume_html()
    places = build_places(*items_of(html))
    from rubrika.ahmp import tokens
    def hits(term):
        return [p.header for p in sorted((p for p in places if match_score(tokens(term), p)), key=lambda p: -match_score(tokens(term), p))]
    assert hits("Plzeň")[0] == "PLZEŇ (o. Plzeň)" and "ŠTĚNOVICE (o. Plzeň-jih)" in hits("Plzeň")     # own name first, then the district
    assert hits("Plzni") == ["PLZEŇ (o. Plzeň)"] or hits("Plzni") == []                                 # inflection is only best effort
    assert hits("Lázně Bělohrad") == ["BĚLOHRAD (Lázně Bělohrad, o. Jičín)"]
    assert hits("Karlín") == ["PRAHA-KARLÍN", "PRAHA-KARLÍN"]
    assert hits("Praha oddaných") == ["PRAHA - matriky oddaných", "PRAHA-KARLÍN"]
    assert hits("Xanadu") == []


def test_coverage_of_inventory_descriptions():
    c = coverage_of("N 1849-1871 O 1841-1856 Z 1850-1870")
    assert [(e, c[e].records.status, c[e].index.status) for e in ("birth", "marriage", "death")] == [
        ("birth", "known", "uncertain"), ("marriage", "known", "uncertain"), ("death", "known", "uncertain")]   # no "(i)" proves nothing
    assert c["marriage"].records.spans[0].start == 1841
    c = coverage_of("N 1835-1864 (i)")
    assert c["birth"].index.status == "known" and c["birth"].index.spans[0].end == 1864 and c["death"].records.status == "uncertain"
    c = coverage_of("N 1901-1915 (i v letech 1908-1915 pro každý rok)")
    assert c["birth"].records.spans[0].start == 1901 and [(s.start, s.end) for s in c["birth"].index.spans] == [(1908, 1915)]
    c = coverage_of("N 1842-1854, 1864")
    assert [(s.start, s.end) for s in c["birth"].records.spans] == [(1842, 1854), (1864, 1864)]
    c = coverage_of("NOZ 1802-1869, 1906-1938")                               # one span for several events
    assert all([(x.start, x.end) for x in c[e].records.spans] == [(1802, 1869), (1906, 1938)] for e in ("birth", "marriage", "death"))
    c = coverage_of("N 1835-1864 (i) Z 1829-1843")
    assert c["birth"].index.status == "known" and c["death"].index.status == "uncertain" and c["marriage"].records.status == "uncertain"
    c = coverage_of("N 1869-1906", index_section=True)                    # a book below an "index" heading
    assert c["birth"].records.status == "absent" and c["birth"].index.status == "known" and c["death"].records.status == "uncertain"
    c = coverage_of("")
    assert all(x.records.status == "uncertain" and x.index.status == "uncertain" for x in c.values())
    c = coverage_of("bez popisu")
    assert all(x.records.status == "uncertain" for x in c.values())


def test_misreadable_descriptions_are_never_known_and_never_exclude():
    from rubrika.catalog import relevance
    from rubrika.models import Book, SearchQuery
    def rel(descr, year):
        book = Book(source_id="na:book:1", number="1", creator="X", coverage=coverage_of(descr), image_count=1, detail_url="u")
        return relevance(book, SearchQuery(raw_query="x", event_type="birth", year_from=year, year_to=year))["status"]
    c = coverage_of("N 1849-71 O 1841-1856")
    assert [(s.start, s.end) for s in c["birth"].records.spans] == [(1849, 1871)]              # end year in the century of the start
    assert rel("N 1849-71 O 1841-1856", 1860) == "candidate"
    c = coverage_of("N 1800-1810, N 1820-1830")
    assert [(s.start, s.end) for s in c["birth"].records.spans] == [(1800, 1810), (1820, 1830)]  # repeated letter: all spans
    assert rel("N 1800-1810, N 1820-1830", 1825) != "excluded" and rel("N 1800-1810 N 1820-1830", 1825) == "candidate"
    for odd in ("N 1849-1871 a dále", "N 1871-49", "N 1849-1871 (jiné)", "N 1849-1871 (i) x", "N 1849-1871-1875", "N 1849-1871 1880"):
        c = coverage_of(odd)
        assert c["birth"].records.status == "uncertain" and not c["birth"].records.spans, odd
        assert rel(odd, 1900) != "excluded", odd
    assert rel("N 1849-1871", 1900) == "needs_review"                                            # no "(i)": an index is not ruled out


def viewer(archive, first, entity=None):
    entity = entity or D.entity_of(D.n_of("PLZEŇ") + 0)
    session = "abc"
    archive.tokens = {}
    return archive._viewer(session, entity_with_scans(archive), first)


def entity_with_scans(archive):
    return next(e for e, (row, inv, d, scans) in archive.books.items() if inv == "1541")


def window_of(first=1, **kw):
    archive = D.NaArchive(**kw)
    entity = entity_with_scans(archive)
    row = archive.books[entity][0]
    html = archive._viewer("abc", entity, first)
    return archive, entity, row, html


def test_viewer_window_gives_the_roots_of_ten_scans():
    archive, entity, row, html = window_of(1)
    xid = D.xid_of(row)
    w = parse_viewer(html, "https://vademecum.nacr.cz/vademecum/Zoomify.action", xid, 23, 80000000)
    assert (w.first, w.last, w.total, len(w.thumbs)) == (1, 10, 23, 10)
    assert [t.root for t in w.thumbs] == [D.root_of(row, s) for s in range(1, 11)] and w.thumbs[3].hash == D.scan_hash(row, 4)
    assert w.tokens and all(t.startswith(("", "")) for t in w.tokens)
    last = parse_viewer(window_of(21)[3], "https://vademecum.nacr.cz/vademecum/PaginatorMedia.action", xid, 23, 80000000, expect_first=21)
    assert (last.first, last.last) == (21, 23) and last.tokens


@pytest.mark.parametrize("change,kind", [
    (lambda h: h.replace("permalink?xid=", "permalink?xid=0", 1), "PARSE_ERROR"),                       # another book
    (lambda h: h.replace('var max= "23"', 'var max= "24"'), "PARSE_ERROR"),
    (lambda h: h.replace("| 23", "| 24"), "PARSE_ERROR"),
    (lambda h: h.replace("var zoomifyImgPath", "var other"), "PARSE_ERROR"),
    (lambda h: re.sub(r"(var zoomifyImgPath = \"[^\"]*)([0-9a-f]{4})(\.jpg\")", r"\g<1>ffff\3", h), "PARSE_ERROR"),   # root differs from the thumbnail
    (lambda h: h.replace("zImageProperties=<IMAGE_PROPERTIES", "zImageProps=<IMAGE_PROPERTIES"), "PARSE_ERROR"),
    (lambda h: h.replace("Sken 3 - ", "Sken 4 - ", 1), "PARSE_ERROR"),
    (lambda h: h.replace("parentType=9019", "parentType=1", 1), "PARSE_ERROR"),
    (lambda h: h.replace("nahled_maly.jpg", "nahled_velky.jpg", 1), "PARSE_ERROR"),
    (lambda h: h.replace('<span class="numImg">5</span>', '<span class="numImg">6</span>'), "PARSE_ERROR"),
    (lambda h: h.replace("PaginatorMedia.action", "BasketBean.action", 1), "PARSE_ERROR"),
    (lambda h: h.replace('var activePicture = "1"', 'var activePicture = "2"'), "PARSE_ERROR"),
    (lambda h: "<html><h1>Page has Expired</h1></html>", "SOURCE_UNAVAILABLE"),
])
def test_unexpected_viewer_is_refused(change, kind):
    archive, entity, row, html = window_of(1)
    with pytest.raises(ResearchError) as e:
        parse_viewer(change(html), "https://vademecum.nacr.cz/vademecum/Zoomify.action", D.xid_of(row), 23, 80000000)
    assert e.value.code == kind


def test_a_window_other_than_the_requested_one_is_refused():
    archive, entity, row, html = window_of(11)
    with pytest.raises(ResearchError) as e:
        parse_viewer(html, "https://vademecum.nacr.cz/vademecum/PaginatorMedia.action", D.xid_of(row), 23, 80000000, expect_first=1)
    assert e.value.code == "PARSE_ERROR"


# ---------------------------------------------------------------- find_place

@pytest.mark.asyncio
async def test_find_place_reads_the_table_of_contents_once_in_one_session(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid)
    assert data["searched"] == ["na"] and data["catalog_exhaustive"] is False
    assert [p["label"] for p in data["places"]][:2] == ["PLZEŇ (o. Plzeň)", "ŠTĚNOVICE (o. Plzeň-jih)"]
    place = data["places"][0]
    assert place["source_id"] == f"na:place:{D.n_of('PLZEŇ')}" and place["archive"] == "na" and place["names"] == ["PLZEŇ"]
    calls = [(u, c) for m, u, c in archive.requests if "/vademecum/" in u]
    assert [re.sub(r"[?;].*", "", u.split("/vademecum/")[1]) for u, c in calls] == ["permalink", "BookVolume.action"]
    assert calls[0][1] is None and calls[1][1].startswith("JSESSIONID=")           # the cookie exists only after the opener
    assert not archive.forbidden_hits
    again = await find(service, sid, "Praha karlín")
    assert len(again["places"]) == 2 and any("Praha je v inventáři rozdělena" in w for w in again["warnings"])
    assert len(archive.catalog_requests()) == 2                                       # the table of contents is remembered


@pytest.mark.asyncio
async def test_unspecified_search_and_district_routing_never_start_with_the_national_archives(env):
    service, archive, store = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="Plzeň", kind="parish_registers")).data
    assert "na" not in data["searched"] and any(n["archive"] == "na" and "výslovnou" in n["reason"] for n in data["not_searched"])
    data = (await call(service, "find_place", research_id=sid, term="Plzeň", kind="parish_registers", district="Plzeň-město")).data
    assert "na" not in data["searched"] and all(n["archive"] != "na" for n in data["not_searched"])
    assert archive.catalog_requests() == []


@pytest.mark.asyncio
async def test_too_many_matches_are_capped_and_reported(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.na.MAX_PLACES", 3)
    sid = await research(service)
    data = await find(service, sid, "Praha")
    assert len(data["places"]) == 3 and any("prvních 3" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_search_needs_a_meaningful_term(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "okres matriky")
    assert data["places"] == [] and "INVALID_ARGUMENT" in data["not_searched"][0]["reason"]


@pytest.mark.asyncio
async def test_switch_off_is_reported_and_nothing_is_fetched(tmp_path):
    service, archive, store = build(tmp_path)
    service.cfg = service.cfg.model_copy(update={"na_enabled": False})
    try:
        sid = await research(service)
        data = await find(service, sid)
        assert data["searched"] == [] and "vypnutý" in data["not_searched"][0]["reason"] and archive.requests == []
    finally:
        await service.close(); store.close()


@pytest.mark.asyncio
async def test_robots_disallow_is_tolerated_and_recorded(env):
    service, archive, store = env
    sid = await research(service)
    await find(service, sid)
    status = (await call(service, "get_research_status", research_id=sid)).data
    assert status["coverage"]["robots_tolerated"] == ["vademecum.nacr.cz"]
    assert any(u.endswith("/robots.txt") for m, u, c in archive.requests)


# ---------------------------------------------------------------- list_sources

@pytest.mark.asyncio
async def test_list_sources_jumps_to_the_header_inside_one_session(env):
    service, archive, store = env
    sid = await research(service)
    places, listed = await listing(service, sid, limit=20)
    assert listed["archive"] == "na" and listed["kind"] == "parish_registers" and listed["listing_complete"]
    assert [r["number"] for r in listed["sources"] if r["relevance"]["status"] == "candidate"] == ["1541"]
    numbers = sorted(r["number"] for r in listed["sources"])
    assert numbers == sorted(["1541", "1542", "1543", "1544", "1549", "1550", "1554", "1557", "1559", "1560", "1561"])
    assert listed["total"] == 11 and listed["next_cursor"] is None
    # opener, inventory, jump to the header, then the next page of the block (found through the pager's own link)
    seq = paths(archive)
    assert seq == ["permalink", "BookVolume.action", "permalink", "BookVolume.action", "PaginatorBookVolume.action", "PaginatorBookVolume.action"]
    calls = [(u, c) for m, u, c in archive.requests if "/vademecum/" in u][2:]
    assert calls[0][1] is None and all(c and c.startswith("JSESSIONID=") for u, c in calls[1:])
    assert "alter=&_sourcePage=" in calls[2][0] and f"rowTxt={D.n_of('PLZEŇ')}" in calls[2][0] and "row=" in calls[3][0]
    assert len({c for u, c in calls[1:]}) == 1                                          # one session, and a new one per call
    assert not archive.forbidden_hits


@pytest.mark.asyncio
async def test_rows_carry_inventory_number_years_scans_and_ranking(env):
    service, archive, store = env
    sid = await research(service)
    places, listed = await listing(service, sid, limit=20)
    rows = {r["number"]: r for r in listed["sources"]}
    first = rows["1541"]
    assert first["source_id"] == f"na:book:{D.entity_of(D.n_of('PLZEŇ'))}" and first["image_count"] == 23
    assert first["creator"] == "PLZEŇ" and first["archive"] == "na" and first["coverage"]["birth"]["records"]["spans"] == [{"start": 1864, "end": 1869}]
    assert first["detail_url"].startswith("https://vademecum.nacr.cz/vademecum/permalink?xid=") and first["detail_url"].count("-") == 4
    assert rows["1541"]["relevance"]["status"] == "candidate"
    assert rows["1542"]["relevance"]["status"] == "needs_review" or rows["1542"]["relevance"]["status"] == "excluded"
    # a birth book with other years is excluded; a book that names only other events stays for review (uncertain)
    assert rows["1542"]["relevance"]["status"] == "needs_review" and rows["1549"]["relevance"]["status"] == "needs_review"
    assert rows["1557"]["relevance"]["status"] == "needs_review"                            # years do not overlap, but an index is not ruled out
    # a book below the "indexy" heading indexes the events it names
    assert rows["1559"]["coverage"]["birth"]["records"]["status"] == "absent" and rows["1559"]["coverage"]["birth"]["index"]["status"] == "known"
    order = ["candidate", "needs_review", "excluded"]
    assert [order.index(r["relevance"]["status"]) for r in listed["sources"]] == sorted(order.index(r["relevance"]["status"]) for r in listed["sources"])


@pytest.mark.asyncio
async def test_undigitised_and_cross_reference_rows_are_reported_not_lost(env):
    service, archive, store = env
    sid = await research(service)
    places, listed = await listing(service, sid, "Babčice")
    assert [r["number"] for r in listed["sources"]] == ["5"]
    assert any("Odkaz v inventáři: — ANNÍN - viz Kundratice" in w for w in listed["warnings"])
    assert any(w.startswith("Záznam bez digitalizace: inv. č. 6 ") for w in listed["warnings"])


@pytest.mark.asyncio
async def test_header_without_books_says_so(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid, "Davle")
    listed = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="parish_registers")
    assert listed.data["sources"] == [] and any("Záhlaví bez knih: DAVLE viz Roztoky" in w for w in listed.data["warnings"])


@pytest.mark.asyncio
async def test_prague_quarter_and_second_level_place_list_their_own_rows(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid, "Karlín")
    assert [p["label"] for p in places["places"]] == ["PRAHA - matriky narozených › PRAHA-KARLÍN", "PRAHA - matriky oddaných › PRAHA-KARLÍN"]
    second = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][1]["source_id"]], kind="parish_registers")
    assert [r["number"] for r in second.data["sources"]] == ["2301"]
    _, citoliby = await listing(service, sid, "Citoliby")
    assert [r["number"] for r in citoliby["sources"]] == ["2602"]
    assert citoliby["sources"][0]["raw_notes"].startswith("LOUNY (o. Louny) › Citoliby")


@pytest.mark.asyncio
async def test_listing_is_cached_and_paginated(env):
    service, archive, store = env
    sid = await research(service)
    places, page1 = await listing(service, sid, limit=5)
    assert len(page1["sources"]) == 5 and page1["next_cursor"] == 5 and page1["total"] == 11
    before = len(archive.catalog_requests())
    page3 = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]],
                       kind="parish_registers", limit=5, cursor=10)
    assert len(page3.data["sources"]) == 1 and page3.data["next_cursor"] is None
    assert len(archive.catalog_requests()) == before


@pytest.mark.asyncio
async def test_list_sources_rules_follow_the_other_registers(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    pid = places["places"][0]["source_id"]
    for extra in ({"year_from": 1790}, {"year_to": 1800}, {"fund_marks": ["C 1"]}, {"district_archive": "Praha"}):
        err = await failing(service, "list_sources", research_id=sid, place_ids=[pid], kind="parish_registers", **extra)
        assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=[pid, "na:place:3"], kind="parish_registers")
    assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=["na:place:2"], kind="parish_registers")
    assert err["code"] == "NOT_FOUND"                                        # never offered by find_place
    err = await failing(service, "list_sources", research_id=sid, place_ids=["na:place:x"], kind="parish_registers")
    assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=[pid], kind="census")
    assert err["code"] in ("LIVE_DISABLED", "INVALID_ARGUMENT")


@pytest.mark.asyncio
async def test_a_changed_inventory_is_source_changed_not_a_wrong_list(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    pid = places["places"][0]["source_id"]
    place = store.entity("place", pid)
    store.put_entity("place", pid, {**place, "label": "NĚCO JINÉHO (o. Jinde)"})
    err = await failing(service, "list_sources", research_id=sid, place_ids=[pid], kind="parish_registers")
    assert err["code"] == "SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_a_block_longer_than_the_page_limit_is_reported_incomplete(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.na.MAX_LIST_PAGES", 1)
    sid = await research(service)
    places, listed = await listing(service, sid, limit=20)
    assert listed["listing_complete"] is False and any("useknutý" in w for w in listed["warnings"])
    again = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="parish_registers", limit=20)
    assert again.data["listing_complete"] is False           # an incomplete list is not remembered as complete


@pytest.mark.asyncio
async def test_single_use_tokens_and_session_links_do_not_matter(tmp_path):
    service, archive, store = build(tmp_path, archive=D.NaArchive(single_use_tokens=True, jsessionid_links=True))
    try:
        sid = await research(service)
        places, listed = await listing(service, sid, limit=20)
        assert listed["total"] == 11 and not archive.forbidden_hits
    finally:
        await service.close(); store.close()


@pytest.mark.asyncio
async def test_session_expiring_in_the_middle_restarts_once(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    original = archive.handle
    state = {"dropped": False}

    def drop_once(request):
        if "PaginatorBookVolume" in str(request.url) and not state["dropped"]:
            state["dropped"] = True
            archive.expire_sessions()
        return original(request)

    service.source.client._transport = httpx.MockTransport(drop_once)
    listed = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="parish_registers", limit=20)
    assert state["dropped"] and listed.data["total"] == 11
    assert len([1 for m, u, c in archive.requests if "permalink?xid=" in u]) == 3       # find_place, list_sources, and the replacement


@pytest.mark.asyncio
async def test_a_session_that_always_expires_is_an_error_not_an_empty_list(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    original = archive.handle

    def always(request):
        if "BookVolume" in str(request.url):
            archive.expire_sessions()
        return original(request)

    service.source.client._transport = httpx.MockTransport(always)
    err = await failing(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]], kind="parish_registers")
    assert err["code"] == "SOURCE_UNAVAILABLE"
    assert len(archive.catalog_requests()) <= 2 + 2 * 2


# ---------------------------------------------------------------- open_source and images

@pytest.mark.asyncio
async def test_open_source_lists_pages_without_touching_the_scans(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid, limit=20)
    book = next(s for s in listed["sources"] if s["number"] == "1541")
    before = len(archive.requests)
    out = (await call(service, "open_source", research_id=sid, source_id=book["source_id"])).data
    assert len(archive.requests) == before
    assert out["archive"] == "na" and out["kind"] == "parish_registers" and out["total"] == 23
    assert out["citation"] == "Národní archiv, Matriky židovských náboženských obcí v českých krajích (HBMa, NAD 167), inv. č. 1541"
    assert out["unit"]["number"] == "1541" and out["unit"]["image_count"] == 23
    p = out["pages"][1]
    assert p["page_id"] == f"{book['source_id']}:2" and p["scan_number"] == 2 and p["source_type"] == "zoomify" and p["archive"] == "na"
    assert p["viewer_url"] is None and p["citation"] == out["citation"]          # the scan link exists only after view_scan
    assert "viewer_url" in out["note"] and "view_scan" in out["note"]
    page = await call(service, "open_source", research_id=sid, source_id=book["source_id"], cursor=1, limit=1)
    assert [x["scan_number"] for x in page.data["pages"]] == [2] and page.data["next_cursor"] == 2
    assert "captch" in out["note"] and "souhlasem Národního archivu" in out["note"]


@pytest.mark.asyncio
async def test_open_requires_a_listed_book(env):
    service, archive, store = env
    sid = await research(service)
    err = await failing(service, "open_source", research_id=sid, source_id="na:book:292112")
    assert err["code"] == "NOT_FOUND"
    err = await failing(service, "open_source", research_id=sid, source_id="na:book:xyz")
    assert err["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_first_window_needs_one_viewer_request_and_serves_ten_scans(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    before = len(archive.catalog_requests())
    view = await call(service, "view_scan", research_id=sid, page_id=book["pages"][2]["page_id"])
    ref = view.data["image"]
    row = archive.books[int(book["pages"][0]["page_id"].split(":")[2])][0]
    hits = archive.catalog_requests()[before:]
    assert [re.sub(r"[?;].*", "", u.split("/vademecum/")[1]) for u in hits] == ["permalink", "Zoomify.action"]
    assert "scanIndex=0" in hits[1]
    w, h = D.scan_size(3)
    assert (ref["source_width"], ref["source_height"]) == (w, h) and ref["archive"] == "na" and ref["scan_number"] == 3
    assert ref["robots_disallowed"] is True and ref["citation"].endswith("inv. č. 1541") and ref["role"] == "overview"
    assert ref["viewer_url"] == f"https://vademecum.nacr.cz/vademecum/permalink?xid={D.xid_of(row)}&scan={D.scan_hash(row, 3)}&parentType=9019"
    assert any(u == D.root_of(row, 3) + "/ImageProperties.xml" for u in archive.image_requests())
    # the other nine scans of the window are known now: no further catalogue request
    for n in (1, 10):
        await call(service, "view_scan", research_id=sid, page_id=book["pages"][n - 1]["page_id"])
    assert len(archive.catalog_requests()) == before + 2
    status = (await call(service, "get_research_status", research_id=sid)).data
    assert set(status["coverage"]["robots_tolerated"]) == {"vademecum.nacr.cz"}


@pytest.mark.asyncio
async def test_a_later_window_takes_one_more_request_and_odd_sizes_render(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    before, mark = len(archive.catalog_requests()), len(archive.requests)
    pid = lambda n: f"{book['unit']['source_id']}:{n}"
    view = await call(service, "view_scan", research_id=sid, page_id=pid(23))                                # the last window has 3 scans
    hits = archive.catalog_requests()[before:]
    assert [re.sub(r"[?;].*", "", u.split("/vademecum/")[1]) for u in hits] == ["permalink", "Zoomify.action", "PaginatorMedia.action"]
    assert hits[2].endswith("&row=20")
    w, h = D.scan_size(23)
    ref = view.data["image"]
    assert (ref["source_width"], ref["source_height"]) == (w, h) and w % 2 == 1                          # an odd width, halved with ceil by the server
    cookies = {c for m, u, c in archive.requests[mark:mark + 4] if "/vademecum/" in u and c}
    assert len(cookies) == 1
    view12 = await call(service, "view_scan", research_id=sid, page_id=pid(22))                              # scan 22 came with the window
    assert len(archive.catalog_requests()) == before + 3 and view12.data["image"]["scan_number"] == 22
    view11 = await call(service, "view_scan", research_id=sid, page_id=pid(11))                              # scan 11: window 2
    assert view11.data["image"]["scan_number"] == 11
    assert [re.sub(r"[?;].*", "", u.split("/vademecum/")[1]) for u in archive.catalog_requests()[before + 3:]][-1] == "PaginatorMedia.action"


@pytest.mark.asyncio
async def test_zoom_scan_and_the_full_resolution_cap(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    view = await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    ref = view.data["image"]
    zoom = await call(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                      bbox_px={"x": ref["width"] / 2, "y": ref["height"] / 2, "width": ref["width"] / 4, "height": ref["height"] / 4})
    z = zoom.data["image"]
    assert z["role"] == "detail" and z["source_bbox"][2] < ref["source_width"]
    Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).load()
    # a detail request for the whole page comes from the top tier: the app offers that only behind a captcha
    err = await failing(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                        bbox_px={"x": 0, "y": 0, "width": ref["width"], "height": ref["height"]})
    assert err["code"] == "BUDGET_EXCEEDED" and err["details"]["metric"] == "pixels"


@pytest.mark.asyncio
async def test_scan_count_change_is_source_changed(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    entity = int(book["pages"][0]["page_id"].split(":")[2])
    row, inv, descr, scans = archive.books[entity]
    archive.books[entity] = (row, inv, descr, scans + 1)
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][1]["page_id"])
    assert err["code"] in ("SOURCE_CHANGED", "PARSE_ERROR")


@pytest.mark.asyncio
async def test_unknown_or_unopened_pages_are_refused_without_a_request(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid)
    book_id = listed["sources"][0]["source_id"]
    before = len(archive.requests)
    for pid in (f"{book_id}:1", f"{book_id}:99", "na:book:999999999:1"):
        err = await failing(service, "view_scan", research_id=sid, page_id=pid)
        assert err["code"] == "NOT_FOUND"
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_checkpoint_is_checked_before_a_scan_is_resolved(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    with store.tx() as db: store._count(db, sid, "images", store.cfg.checkpoint_images)
    before = len(archive.requests)
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert err["code"] == "CHECKPOINT" and len(archive.requests) == before


@pytest.mark.asyncio
async def test_switch_off_after_opening_stops_the_scans(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    service.cfg = service.cfg.model_copy(update={"na_enabled": False})
    before = len(archive.requests)
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert err["code"] == "LIVE_DISABLED" and len(archive.requests) == before


@pytest.mark.asyncio
async def test_viewer_of_another_shape_is_a_parse_error_and_not_cached(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    original = archive._viewer
    archive._viewer = lambda *a, **k: original(*a, **k).replace("var zoomifyImgPath", "var other")
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert err["code"] == "PARSE_ERROR"
    archive._viewer = original
    await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])


# ---------------------------------------------------------------- protections are never bypassed

@pytest.mark.asyncio
async def test_bot_gate_stops_everything_and_pauses_the_host(env):
    service, archive, store = env
    sid = await research(service)
    archive.gate = True
    data = await find(service, sid)
    assert data["places"] == [] and "SOURCE_ACCESS_DENIED" in data["not_searched"][0]["reason"]
    hits = len(archive.catalog_requests())
    again = await find(service, sid)
    assert again["places"] == [] and len(archive.catalog_requests()) == hits
    archive.gate = False
    assert (await find(service, sid))["places"] == []


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMITED"), (403, "SOURCE_ACCESS_DENIED"), (401, "SOURCE_ACCESS_DENIED")])
@pytest.mark.asyncio
async def test_refusals_stop_and_are_not_retried(env, status, code):
    service, archive, store = env
    sid = await research(service)
    archive.status["BookVolume.action"] = status
    data = await find(service, sid)
    assert data["places"] == [] and code in data["not_searched"][0]["reason"]
    assert len([u for u in archive.catalog_requests() if "BookVolume" in u]) == 1
    await find(service, sid)
    assert len([u for u in archive.catalog_requests() if "BookVolume" in u]) == 1


@pytest.mark.asyncio
async def test_forbidden_actions_are_never_requested(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    for n in (1, 15):
        await call(service, "view_scan", research_id=sid, page_id=f"{book['unit']['source_id']}:{n}")
    assert archive.forbidden_hits == []
    assert not any(re.search(r"captcha|Basket|login|SearchBean|Rejstriky|Autocomplete|showOne|baseUrl|show=", u) for m, u, c in archive.requests)
    assert {m for m, u, c in archive.requests} == {"GET"}
    assert all("scanIndex=0" in u for m, u, c in archive.requests if "Zoomify.action" in u)


# ---------------------------------------------------------------- reports and GEDCOM

@pytest.mark.asyncio
async def test_report_and_gedcom_name_the_book(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid, limit=20)
    status = (await call(service, "get_research_status", research_id=sid)).data
    not_opened = status["coverage"]["catalog_books_not_opened"]
    assert len(not_opened) == 11 and {b["source"] for b in not_opened} == {"na"}
    assert next(b for b in not_opened if b["number"] == "1541")["relevance"]["status"] == "candidate"
    assert status["coverage"]["catalog_listings"][0]["source"] == "na"
    book = await opened(service, sid)
    view = await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    await call(service, "record_finding", research_id=sid, idempotency_key="k1", observation={
        "kind": "record", "image_id": view.data["image"]["image_id"], "text": "SYNTHETIC zápis", "is_candidate": True})
    status = (await call(service, "get_research_status", research_id=sid)).data
    summary = status["coverage"]["opened_books"][0]
    assert summary["source"] == "na" and summary["number"] == "1541" and summary["title"] == "PLZEŇ"
    obs = (await call(service, "list_findings", research_id=sid)).data["observations"][0]
    tree = await call(service, "export_family_tree", tree={
        "title": "SYNTHETIC", "root_person": "p1", "persons": [{"id": "p1", "given_name": "Test", "surname": "Synthetic", "events": [
            {"type": "baptism", "confidence": "documented",
             "sources": [{"research_id": sid, "observation_id": obs["observation_id"]}]}]}]})
    assert "Národní archiv, HBMa (NAD 167), inv. č. 1541, PLZEŇ" in tree.data["gedcom"]
    assert "Národní archiv" in tree.data["gedcom"]


@pytest.mark.asyncio
async def test_list_archives_describes_the_national_archives(env):
    service, archive, store = env
    rows = {r["code"]: r for r in (await call(service, "list_archives")).data["archives"]}
    na = rows["na"]
    assert na["kinds"] == ["parish_registers"] and na["available_kinds"] == ["parish_registers"] and not na["link_only"]
    assert na["robots_mode"] == "user_triggered" and "židovské" in na["note"].lower() or "Židovské" in na["note"]


@pytest.mark.asyncio
async def test_other_archives_are_untouched(env):
    service, archive, store = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="Plzeň", kind="parish_registers", archives=["na", "ahmp"])).data
    assert data["searched"] == ["na"] and [n["archive"] for n in data["not_searched"]] == ["ahmp"]


def test_multi_word_prague_quarters_keep_their_context():
    items = [(1, 20, "ÚŘEDNÍ KNIHY"), (2, 40, "PRAHA - matriky zemřelých"), (5, 40, "PRAHA-STARÉ MĚSTO"), (7, 40, "PRAHA-NOVÉ MĚSTO"),
             (9, 40, "PRAHA-KARLÍN"), (11, 40, "PRAHA - ohlédací listy, zapsaná úmrtí"), (13, 40, "PRAHA-VINOHRADY"), (15, 40, "PLZEŇ (o. Plzeň)")]
    places = {p.n: p for p in build_places(items, 20)}
    assert places[5].label == "PRAHA - matriky zemřelých › PRAHA-STARÉ MĚSTO" and places[5].context == "PRAHA - matriky zemřelých"
    assert places[7].context == "PRAHA - matriky zemřelých" and places[9].context == "PRAHA - matriky zemřelých"
    assert places[11].label == places[11].header and places[11].context is None                 # not a quarter
    assert places[13].context is None and places[15].context is None                           # the context ended


@pytest.mark.asyncio
async def test_prague_warning_appears_for_a_multi_word_quarter(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr(D, "ROWS", D.ROWS + [("h", 40, "PRAHA - matriky zemřelých"), ("b", 0, ("2700", "Z 1850-1860", 2)), ("h", 40, "PRAHA-STARÉ MĚSTO"), ("b", 0, ("2701", "N 1850-1860", 2))])
    monkeypatch.setattr(D, "TOTAL", len(D.ROWS))
    monkeypatch.setattr("rubrika.demo_na.TOTAL", len(D.ROWS))
    sid = await research(service)
    data = await find(service, sid, "Staré Město")
    assert [p["label"] for p in data["places"]] == ["PRAHA - matriky zemřelých › PRAHA-STARÉ MĚSTO"]
    assert any("Praha je v inventáři rozdělena" in w for w in data["warnings"])
