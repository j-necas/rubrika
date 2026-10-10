"""AHMP end to end on synthetic servers: find_place → list_sources → open_source → view_scan → zoom_scan."""
import base64
import dataclasses
import io
import re

import pytest
import pytest_asyncio
from PIL import Image

from rubrika import archives as A
from rubrika import demo_ahmp as D
from rubrika.config import Settings
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

TESTA_LABEL = "Zkušov I - Horní Město › Kostel sv. Testa (TST)"
CALLS_TO_WALK_THE_TREE = 12     # budget 3 = opener + 2 nodes per call; exact for the synthetic tree, matching branches first
QUERY = {"raw_query": "SYNTHETIC AHMP", "place_hint": "Testa", "event_type": "birth", "year_from": 1790, "year_to": 1795}


@pytest.fixture(autouse=True)
def fast_hosts(monkeypatch):
    monkeypatch.setattr("rubrika.ahmp.ROOT_ID", D.ROOT)          # the synthetic tree has its own numbers
    monkeypatch.setattr("rubrika.ahmp.EXTRA_ROOTS", D.EXTRA_ROOTS)
    for code in ("ahmp", "na"):
        monkeypatch.setitem(A.ARCHIVES, code, dataclasses.replace(A.ARCHIVES[code], min_interval=0))


def build(tmp_path, **cfg_args):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, ahmp_enabled=True, request_interval=0, **cfg_args)
    store = Store(cfg)
    archive = D.AhmpArchive()
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


async def find(service, sid, term="sv. Testa", **args):
    args.setdefault("archives", ["ahmp"])
    return (await call(service, "find_place", research_id=sid, term=term, kind="parish_registers", **args)).data


async def listing(service, sid, term="sv. Testa", **args):
    places = await find(service, sid, term)
    listed = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]],
                        kind="parish_registers", **args)
    return places, listed.data


async def opened(service, sid, number="TST N1"):
    _, listed = await listing(service, sid, limit=20)
    book = next(s for s in listed["sources"] if s["number"] == number)
    return (await call(service, "open_source", research_id=sid, source_id=book["source_id"])).data


def catalog_paths(archive):
    return [re.sub(r"\?.*", "", u.split("/pragapublica/")[1]) for u in archive.catalog_requests() if "/pragapublica/" in u]


# ---------------------------------------------------------------- find_place

@pytest.mark.asyncio
async def test_find_place_walks_the_tree_with_one_session_and_names_the_path(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid)
    assert data["searched"] == ["ahmp"] and data["catalog_exhaustive"] is False
    [place] = data["places"]
    assert place["source_id"] == f"ahmp:node:{D.TESTA}" and place["archive"] == "ahmp" and place["label"] == TESTA_LABEL
    assert place["names"] == ["Kostel sv. Testa", "TST"]
    calls = [(u, c) for m, u, c in archive.requests if "katalog.ahmp.cz/pragapublica" in u]
    assert "permalink" in calls[0][0] and calls[0][1] is None                 # the opener carries no cookie
    assert all(c and c.startswith("JSESSIONID=") for u, c in calls[1:])       # everything after it runs in the session
    assert len({c for u, c in calls[1:]}) == 1                                # one session for the whole call
    assert not archive.forbidden_hits


@pytest.mark.asyncio
async def test_district_term_lists_its_parishes_across_pages_and_is_cached(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "Dolní Město")
    assert len(data["places"]) == 11 and {p["archive"] for p in data["places"]} == {"ahmp"}
    assert data["places"][0]["label"].startswith("Zkušov II - Dolní Město › ")
    assert any(u.startswith("PaginatorNavig.action") for u in catalog_paths(archive))
    # the whole tree was needed only up to the requested branch first, but the index is now complete and remembered
    first = len(archive.catalog_requests())
    again = await find(service, sid, "Demeta")
    assert [p["label"] for p in again["places"]] == ["Zkušov II - Dolní Město › Kostel sv. Demeta (DEM)"]
    assert again["catalog_exhaustive"] is False and not any("neúpln" in w for w in again["warnings"])
    assert len(archive.catalog_requests()) == first                          # served from the user's own cache


@pytest.mark.asyncio
async def test_city_district_routes_to_ahmp_and_the_other_archive_is_reported(env):
    service, archive, store = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="sv. Testa", kind="parish_registers", district="Praha")).data
    assert data["searched"] == ["ahmp"] and data["places"][0]["label"] == TESTA_LABEL
    assert [n["archive"] for n in data["not_searched"]] == ["soapraha"]


@pytest.mark.asyncio
async def test_unspecified_search_does_not_start_the_tree_walk(env):
    service, archive, store = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="sv. Testa", kind="parish_registers")).data
    assert "ahmp" not in data["searched"] and any(n["archive"] == "ahmp" and "výslovnou" in n["reason"] for n in data["not_searched"])
    assert archive.catalog_requests() == []
    await find(service, sid)                                                   # once loaded it is searched like any other archive
    again = (await call(service, "find_place", research_id=sid, term="sv. Testa", kind="parish_registers")).data
    assert "ahmp" in again["searched"] and again["places"][0]["archive"] == "ahmp"


@pytest.mark.asyncio
async def test_tree_walk_stops_at_the_request_budget_and_resumes(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.ahmp.TREE_REQUESTS_PER_CALL", 3)
    sid = await research(service)

    def cached():
        return sum(1 for n in D.TREE if store.entity("ahmp_node", str(n), 10**9) is not None)

    data = await find(service, sid, "Výplně")
    assert any("postupně" in w for w in data["warnings"]) and len(archive.catalog_requests()) <= 6
    calls, seen = 1, cached()
    assert seen >= 1
    while any("postupně" in w for w in data["warnings"]):
        assert calls < CALLS_TO_WALK_THE_TREE, "the walk does not finish in the expected number of calls"
        data = await find(service, sid, "Výplně")
        calls += 1
        assert cached() > seen, "a call that walks must cache at least one new node"
        seen = cached()
    assert calls == CALLS_TO_WALK_THE_TREE
    assert len(data["places"]) >= 2


@pytest.mark.asyncio
async def test_too_many_matches_are_capped_and_reported(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.ahmp.MAX_PLACES", 3)
    sid = await research(service)
    data = await find(service, sid, "kostel sv.")
    assert len(data["places"]) == 3 and any("prvních 3" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_switch_off_is_reported_and_nothing_is_fetched(tmp_path):
    service, archive, store = build(tmp_path)
    service.cfg = service.cfg.model_copy(update={"ahmp_enabled": False})
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
    assert status["coverage"]["robots_tolerated"] == ["katalog.ahmp.cz"]
    assert any(u.endswith("/robots.txt") for m, u, c in archive.requests)


# ---------------------------------------------------------------- list_sources

@pytest.mark.asyncio
async def test_list_sources_pages_the_parish_and_ranks_by_event_and_years(env):
    service, archive, store = env
    sid = await research(service)
    places, listed = await listing(service, sid, limit=20)
    assert listed["archive"] == "ahmp" and listed["kind"] == "parish_registers"
    assert listed["total"] == 12 and listed["listing_complete"] and listed["next_cursor"] is None
    rows = {r["number"]: r for r in listed["sources"]}
    assert rows["TST N1"]["relevance"]["status"] == "candidate" and rows["TST N1"]["source_id"].startswith("ahmp:book:")
    assert {n for n, r in rows.items() if r["relevance"]["status"] == "candidate"} == {"TST N1"}
    # Only a year mismatch of the book's own event excludes it; other event kinds are kept for review.
    assert {n for n, r in rows.items() if r["relevance"]["status"] == "excluded"} == {"TST N2", "TST N10", "TST NOZ4"}
    assert {n for n, r in rows.items() if r["relevance"]["status"] == "needs_review"} == {
        "TST N3", "TST i1", "TST i2", "TST X9", "TST O1", "TST O2", "TST Z1", "TST Z2"}
    assert [r["relevance"]["status"] for r in listed["sources"]] == sorted((r["relevance"]["status"] for r in listed["sources"]),
                                                                          key=["candidate", "needs_review", "excluded"].index)
    first = listed["sources"][0]
    assert first["image_count"] == 3 and first["creator"] == "Kostel sv. Testa" and first["archive"] == "ahmp"
    assert first["detail_url"] == f"https://katalog.ahmp.cz/pragapublica/permalink?xid={first['source_id'].split(':')[2]}"
    assert first["coverage"]["birth"]["records"]["spans"] == [{"start": 1780, "end": 1800}]
    assert any(u.startswith("PaginatorNavig") for u in catalog_paths(archive))              # 12 books need a second page
    openers = [(u, c) for m, u, c in archive.requests if "permalink?xid=" in u and "scan=" not in u]
    assert len(openers) == 2 and all(c is None for u, c in openers)           # find_place and list_sources: a session each, never reused


@pytest.mark.asyncio
async def test_listing_is_cached_and_paginated(env):
    service, archive, store = env
    sid = await research(service)
    places, page1 = await listing(service, sid, limit=5)
    assert len(page1["sources"]) == 5 and page1["next_cursor"] == 5 and page1["total"] == 12
    before = len(archive.catalog_requests())
    page3 = await call(service, "list_sources", research_id=sid, place_ids=[places["places"][0]["source_id"]],
                       kind="parish_registers", limit=5, cursor=10)
    assert len(page3.data["sources"]) == 2 and page3.data["next_cursor"] is None
    assert len(archive.catalog_requests()) == before


@pytest.mark.asyncio
async def test_list_sources_rules_follow_the_mza_registers(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    pid = places["places"][0]["source_id"]
    for extra in ({"year_from": 1790}, {"year_to": 1800}, {"fund_marks": ["C 1"]}, {"district_archive": "Praha"}):
        err = await failing(service, "list_sources", research_id=sid, place_ids=[pid], kind="parish_registers", **extra)
        assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=[pid, pid.replace("20", "21")], kind="parish_registers")
    assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:1"], kind="parish_registers")
    assert err["code"] == "NOT_FOUND"                                        # never listed by find_place
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:x"], kind="parish_registers")
    assert err["code"] == "INVALID_ARGUMENT"
    err = await failing(service, "list_sources", research_id=sid, place_ids=[pid], kind="census")
    assert err["code"] in ("LIVE_DISABLED", "INVALID_ARGUMENT")


@pytest.mark.asyncio
async def test_a_container_node_is_not_a_parish(env):
    service, archive, store = env
    sid = await research(service)
    places = await find(service, sid)
    store.link(sid, "alice", "place_option", f"ahmp:node:{D.INNER}",
               {"source_id": f"ahmp:node:{D.INNER}", "label": "x", "names": ["x"], "source_url": D.SITE.node_url(D.INNER)})
    store.put_entity("place", f"ahmp:node:{D.INNER}", {"source_id": f"ahmp:node:{D.INNER}", "label": "x", "names": ["x"],
                                                       "source_url": D.SITE.node_url(D.INNER)})
    err = await failing(service, "list_sources", research_id=sid, place_ids=[f"ahmp:node:{D.INNER}"], kind="parish_registers")
    assert err["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- open_source and images

@pytest.mark.asyncio
async def test_open_source_lists_pages_without_touching_the_scans(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid, limit=20)
    book = next(s for s in listed["sources"] if s["number"] == "TST N1")
    before = len(archive.requests)
    out = (await call(service, "open_source", research_id=sid, source_id=book["source_id"])).data
    assert len(archive.requests) == before                                    # scan roots are resolved when a scan is opened
    assert out["archive"] == "ahmp" and out["kind"] == "parish_registers" and out["total"] == 3
    assert out["citation"] == "Archiv hlavního města Prahy, Sbírka matrik, sign. TST N1"
    assert out["unit"]["number"] == "TST N1" and out["unit"]["image_count"] == 3
    xid = book["source_id"].split(":")[2]
    p = out["pages"][1]
    assert p["page_id"] == f"ahmp:book:{xid}:2" and p["scan_number"] == 2 and p["source_type"] == "zoomify" and p["archive"] == "ahmp"
    assert p["viewer_url"] == f"https://katalog.ahmp.cz/pragapublica/permalink?xid={xid}&scan=2"
    page = await call(service, "open_source", research_id=sid, source_id=book["source_id"], cursor=1, limit=1)
    assert [x["scan_number"] for x in page.data["pages"]] == [2] and page.data["next_cursor"] == 2


@pytest.mark.asyncio
async def test_open_requires_a_listed_book(env):
    service, archive, store = env
    sid = await research(service)
    err = await failing(service, "open_source", research_id=sid, source_id="ahmp:book:" + "A" * 32)
    assert err["code"] == "NOT_FOUND"
    err = await failing(service, "open_source", research_id=sid, source_id="ahmp:book:xyz")
    assert err["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_view_and_zoom_scan_use_one_permalink_request_per_scan(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    page = book["pages"][1]
    view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    ref = view.data["image"]
    w, h = D.scan_size(2)
    xid = page["page_id"].split(":")[2]
    assert (ref["source_width"], ref["source_height"]) == (w, h) and ref["archive"] == "ahmp" and ref["scan_number"] == 2
    assert ref["robots_disallowed"] is True and ref["viewer_url"] == page["viewer_url"]
    assert ref["citation"].endswith("sign. TST N1") and ref["role"] == "overview"
    viewer_hits = [u for u in archive.catalog_requests() if "scan=" in u]
    assert viewer_hits == [f"https://katalog.ahmp.cz/pragapublica/permalink?xid={xid}&scan=2"]
    assert all(c is None for m, u, c in archive.requests if "scan=" in u)     # no cookie leaves the tree-walking call
    root = D.root_of(xid, 2)
    assert any(u == root + "/ImageProperties.xml" for u in archive.image_requests())
    zoom = await call(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                      bbox_px={"x": 0, "y": 0, "width": ref["width"] / 4, "height": ref["height"] / 4})
    z = zoom.data["image"]
    assert z["role"] == "detail" and z["source_bbox"][2] < w
    Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).load()
    again = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    assert len([u for u in archive.catalog_requests() if "scan=" in u]) == 1  # the resolved page is remembered
    status = (await call(service, "get_research_status", research_id=sid)).data
    assert set(status["coverage"]["robots_tolerated"]) == {"katalog.ahmp.cz", "images.ahmp.cz"}


@pytest.mark.asyncio
async def test_each_scan_gets_its_own_image(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    sizes = []
    for page in book["pages"]:
        view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
        sizes.append((view.data["image"]["source_width"], view.data["image"]["source_height"]))
    assert sizes == [D.scan_size(n) for n in (1, 2, 3)]


@pytest.mark.asyncio
async def test_full_resolution_cap_still_applies(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    page = book["pages"][0]
    view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    ref = view.data["image"]
    # a detail request for (nearly) the whole page comes from the top tier: the app offers that only behind a captcha
    err = await failing(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                        bbox_px={"x": 0, "y": 0, "width": ref["width"], "height": ref["height"]})
    assert err["code"] == "BUDGET_EXCEEDED" and err["details"]["metric"] == "pixels"


@pytest.mark.asyncio
async def test_scan_count_change_is_source_changed(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    xid = book["pages"][0]["page_id"].split(":")[2]
    node, label, scans = archive.books[xid]
    archive.books[xid] = (node, label, scans + 1)
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][1]["page_id"])
    assert err["code"] == "SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_unknown_or_unopened_pages_are_refused_without_a_request(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid)
    xid = listed["sources"][0]["source_id"].split(":")[2]
    before = len(archive.requests)
    for pid in (f"ahmp:book:{xid}:1", f"ahmp:book:{xid}:99", "ahmp:book:" + "B" * 32 + ":1"):
        err = await failing(service, "view_scan", research_id=sid, page_id=pid)
        assert err["code"] == "NOT_FOUND"
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_viewer_of_another_shape_is_a_parse_error_and_not_cached(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    original = archive._viewer
    archive._viewer = lambda xid, scan: original(xid, scan).replace("var zoomifyImgPath", "var other")
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert err["code"] == "PARSE_ERROR"
    archive._viewer = original
    await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])       # not poisoned by the failure


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
    assert again["places"] == [] and len(archive.catalog_requests()) == hits           # the whole host is paused
    archive.gate = False
    assert (await find(service, sid))["places"] == []                                  # still paused


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMITED"), (403, "SOURCE_ACCESS_DENIED"), (401, "SOURCE_ACCESS_DENIED")])
@pytest.mark.asyncio
async def test_refusals_stop_and_are_not_retried(env, status, code):
    service, archive, store = env
    sid = await research(service)
    archive.status["/pragapublica/NavigBean.action"] = status
    data = await find(service, sid)
    assert data["places"] == [] and code in data["not_searched"][0]["reason"]
    nav = [u for u in archive.catalog_requests() if "NavigBean" in u]
    assert len(nav) == 1
    await find(service, sid)
    assert len([u for u in archive.catalog_requests() if "NavigBean" in u]) == 1


@pytest.mark.asyncio
async def test_expired_session_is_replaced_once(env):
    service, archive, store = env
    sid = await research(service)
    original = archive.handle
    state = {"dropped": False}

    def drop_once(request):
        if "NavigBean" in str(request.url) and not state["dropped"] and archive.sessions:
            state["dropped"] = True
            archive.sessions.clear()
        return original(request)

    service.source.client._transport = __import__("httpx").MockTransport(drop_once)
    data = await find(service, sid)
    assert state["dropped"] and data["places"][0]["label"] == TESTA_LABEL
    assert len([1 for m, u, c in archive.requests if "permalink?xid=" in u and "scan" not in u]) == 2


@pytest.mark.asyncio
async def test_forbidden_actions_are_never_requested(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert archive.forbidden_hits == []
    assert not any(re.search(r"Zoomify\.action|captcha|Basket|login|SearchBean|PaginatorMedia", u) for m, u, c in archive.requests)
    assert {m for m, u, c in archive.requests} == {"GET"}


# ---------------------------------------------------------------- reports and GEDCOM

@pytest.mark.asyncio
async def test_report_and_gedcom_name_the_book(env):
    service, archive, store = env
    sid = await research(service)
    _, listed = await listing(service, sid, limit=20)
    status = (await call(service, "get_research_status", research_id=sid)).data
    not_opened = status["coverage"]["catalog_books_not_opened"]
    assert len(not_opened) == 12 and {b["source"] for b in not_opened} == {"ahmp"}
    assert next(b for b in not_opened if b["number"] == "TST N1")["relevance"]["status"] == "candidate"
    assert status["coverage"]["catalog_listings"][0]["source"] == "ahmp"
    book = await opened(service, sid)
    view = await call(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    await call(service, "record_finding", research_id=sid, idempotency_key="k1", observation={
        "kind": "record", "image_id": view.data["image"]["image_id"], "text": "SYNTHETIC zápis", "is_candidate": True})
    status = (await call(service, "get_research_status", research_id=sid)).data
    summary = status["coverage"]["opened_books"][0]
    assert summary["source"] == "ahmp" and summary["number"] == "TST N1" and summary["title"] == "Kostel sv. Testa"
    obs = (await call(service, "list_findings", research_id=sid)).data["observations"][0]
    tree = await call(service, "export_family_tree", tree={
        "title": "SYNTHETIC", "root_person": "p1", "persons": [{"id": "p1", "given_name": "Test", "surname": "Synthetic", "events": [
            {"type": "baptism", "confidence": "documented",
             "sources": [{"research_id": sid, "observation_id": obs["observation_id"]}]}]}]})
    assert "Archiv hl. m. Prahy, TST N1" in tree.data["gedcom"]
    assert "Archiv hlavního města Prahy" in tree.data["gedcom"]


@pytest.mark.asyncio
async def test_other_event_query_never_excludes_a_book_by_signature(env):
    service, archive, store = env
    sid = await research(service, {**QUERY, "event_type": "death", "year_from": 1781, "year_to": 1785})
    _, listed = await listing(service, sid, limit=20)
    rows = {r["number"]: r["relevance"]["status"] for r in listed["sources"]}
    assert rows["TST N1"] == "needs_review" and rows["TST Z1"] == "candidate"     # N book vs death query
    assert rows["TST Z2"] == "excluded"                                            # own event, years do not overlap


@pytest.mark.asyncio
async def test_container_with_own_books_is_a_place_and_lists_them(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "Testovice")
    ids = {p["source_id"]: p["label"] for p in data["places"]}
    assert f"ahmp:node:{D.TESTOVICE}" in ids and f"ahmp:node:{D.TESTOVICE_SUB}" in ids
    listed = await call(service, "list_sources", research_id=sid, place_ids=[f"ahmp:node:{D.TESTOVICE}"], kind="parish_registers")
    assert [r["number"] for r in listed.data["sources"]] == ["TSV N1"] and listed.data["listing_complete"]


@pytest.mark.asyncio
async def test_a_call_never_exceeds_its_request_budget(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.ahmp.TREE_REQUESTS_PER_CALL", 6)
    sid = await research(service)
    before = len([1 for m, u, c in archive.requests if "katalog.ahmp.cz/pragapublica" in u])
    data = await find(service, sid, "Výplně")
    made = len([1 for m, u, c in archive.requests if "katalog.ahmp.cz/pragapublica" in u]) - before
    assert made <= 6 and any("postupně" in w for w in data["warnings"])
    # a partial walk says so, and how to go on: repeat (the branches read so far are kept) or narrow the term
    warning = next(w for w in data["warnings"] if "postupně" in w)
    assert "catalog_exhaustive=false" in warning and "zopakujte" in warning.lower() and "zúžte" in warning.lower()


@pytest.mark.asyncio
async def test_labels_of_outer_parishes_keep_their_path_too(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "Demovice")
    assert [p["label"] for p in data["places"]] == ["vnější obvody › Demovice (DMV) - kostel sv. Prubora"]
    nested = await find(service, sid, "Zkušebního")
    assert [p["label"] for p in nested["places"]] == ["Testovice › Testovice (TES) - kostel sv. Zkušebního"]


@pytest.mark.asyncio
async def test_expiry_retry_counts_against_the_budget(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.ahmp.TREE_REQUESTS_PER_CALL", 4)
    original = archive.handle
    def always_expire(request):
        if "NavigBean" in str(request.url) or "Paginator" in str(request.url):
            archive.sessions.clear()
        return original(request)
    service.source.client._transport = __import__("httpx").MockTransport(always_expire)
    sid = await research(service)
    data = await find(service, sid)
    assert data["places"] == []
    assert len([1 for m, u, c in archive.requests if "katalog.ahmp.cz/pragapublica" in u]) <= 4


@pytest.mark.asyncio
async def test_checkpoint_is_checked_before_a_scan_is_resolved(env):
    service, archive, store = env
    sid = await research(service)
    book = await opened(service, sid)
    with store.tx() as db: store._count(db, sid, "images", store.cfg.checkpoint_images)
    before = len(archive.requests)
    err = await failing(service, "view_scan", research_id=sid, page_id=book["pages"][0]["page_id"])
    assert err["code"] == "CHECKPOINT" and len(archive.requests) == before


# ---- non-Catholic churches and civil registers (the other children of the Sbírka matrik) ------------------------

async def list_place(service, sid, node, **args):
    return (await call(service, "list_sources", research_id=sid, place_ids=[f"ahmp:node:{node}"], kind="parish_registers", limit=20, **args)).data


@pytest.mark.asyncio
async def test_non_catholic_congregation_is_found_with_church_in_the_label(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "Evangelisty")
    [place] = data["places"]
    assert place["source_id"] == f"ahmp:node:{D.EVANGELIC_TESTA}"
    assert place["label"] == "Nekatolické matriky › Českobratrská církev evangelická (ČCE) › ČCE (Ukázkov) u sv. Evangelisty (EVT)"
    listed = await list_place(service, sid, D.EVANGELIC_TESTA)
    rows = {r["number"]: r for r in listed["sources"]}
    assert set(rows) == {"EVT N1O1Z1", "EVT N2", "EVT O2", "EVT Z3"} and listed["listing_complete"]
    # birth 1790-1795: the combined book covers births, the marriage/death books only stay for review or fall out on years
    assert rows["EVT N1O1Z1"]["relevance"]["status"] == "candidate"
    assert rows["EVT N1O1Z1"]["coverage"]["birth"]["records"]["spans"] == [{"start": 1783, "end": 1836}]
    assert rows["EVT N1O1Z1"]["coverage"]["marriage"]["records"]["spans"] == [{"start": 1783, "end": 1836}]
    assert rows["EVT N1O1Z1"]["coverage"]["death"]["records"]["spans"] == [{"start": 1783, "end": 1836}]
    assert rows["EVT N2"]["relevance"]["status"] == "excluded"          # births 1802-1826 do not reach 1790
    assert rows["EVT O2"]["relevance"]["status"] == "needs_review"      # a marriage book says nothing about births
    assert rows["EVT N1O1Z1"]["creator"] == "ČCE (Ukázkov) u sv. Evangelisty (EVT)"


@pytest.mark.asyncio
async def test_church_shared_name_finds_catholic_and_non_catholic_parishes_catholic_first(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "sv.")
    labels = [p["label"] for p in data["places"]]
    assert not labels[0].startswith(("Nekatolické matriky", "Civilní matriky", "Úmrtní protokoly"))      # the first result is Catholic
    assert any(l.startswith("Nekatolické matriky ›") for l in labels)
    first_extra = min(i for i, l in enumerate(labels) if l.startswith("Nekatolické matriky ›"))
    assert all(not l.startswith("Nekatolické matriky") for l in labels[:first_extra])


@pytest.mark.asyncio
async def test_container_with_hidden_child_list_yields_both_places(env):
    service, archive, store = env
    sid = await research(service)
    data = await find(service, sid, "Pravoslavná")
    ids = {p["source_id"]: p["label"] for p in data["places"]}
    assert set(ids) == {f"ahmp:node:{D.PRAVOSLAVNA}", f"ahmp:node:{D.PRAVOSLAVNA_SUB}"}
    own = await list_place(service, sid, D.PRAVOSLAVNA)
    assert [r["number"] for r in own["sources"]] == ["PRAV N1O1Z1", "PRAV N2"] and own["listing_complete"]
    sub = await list_place(service, sid, D.PRAVOSLAVNA_SUB)
    assert [r["number"] for r in sub["sources"]] == ["ČPRAV N1"]


@pytest.mark.asyncio
async def test_civil_registers_and_the_other_branches_are_found(env):
    service, archive, store = env
    sid = await research(service)
    labels = {p["source_id"]: p["label"] for p in (await find(service, sid, "Magistrát"))["places"]}
    assert labels[f"ahmp:node:{D.CIVIL_CENTRAL}"] == "Civilní matriky Magistrátu › Magistrát hlavního města Ukázkova - centrální úřad (MAG)"
    assert labels[f"ahmp:node:{D.CIVIL_OFFICE_ZK}"].endswith("Magistrátní úřadovny › Ukázkov (MGZK)")
    # an office without any book is not a place
    assert not any(p["label"].endswith("(STA)") for p in (await find(service, sid, "Standesamt"))["places"])
    old = (await find(service, sid, "Starokatolická"))["places"]
    assert [p["source_id"] for p in old] == [f"ahmp:node:{D.OLD_CATHOLIC}"]
    listed = await list_place(service, sid, D.OLD_CATHOLIC)
    assert {r["number"] for r in listed["sources"]} == {"STK N1", "STKZ O1"}
    protocols = (await find(service, sid, "Úmrtní protokoly"))["places"]
    assert [p["label"] for p in protocols] == ["Úmrtní protokoly"]
    rows = (await list_place(service, sid, D.DEATH_PROTOCOLS))["sources"]
    assert rows[0]["number"] == "UP-IZ 1a" and rows[0]["coverage"]["death"]["records"]["spans"] == [{"start": 1924, "end": 1939}]
    assert rows[0]["relevance"]["status"] == "needs_review"            # a birth query never excludes it


@pytest.mark.asyncio
async def test_civil_books_open_like_catholic_ones(env):
    service, archive, store = env
    sid = await research(service, {**QUERY, "place_hint": "Magistrát", "year_from": 1875, "year_to": 1880})
    await find(service, sid, "Magistrát")
    listed = await list_place(service, sid, D.CIVIL_CENTRAL)
    book = next(r for r in listed["sources"] if r["number"] == "MAG N1")
    assert book["relevance"]["status"] == "candidate"
    out = (await call(service, "open_source", research_id=sid, source_id=book["source_id"])).data
    assert out["citation"] == "Archiv hlavního města Prahy, Sbírka matrik, sign. MAG N1" and len(out["pages"]) == 3


@pytest.mark.asyncio
async def test_partial_walk_names_the_branches_not_yet_walked(env, monkeypatch):
    service, archive, store = env
    monkeypatch.setattr("rubrika.ahmp.TREE_REQUESTS_PER_CALL", 4)
    sid = await research(service)
    data = await find(service, sid, "Výplně")
    warning = next(w for w in data["warnings"] if "postupně" in w)
    assert "Katolické matriky" in warning and "catalog_exhaustive=false" in warning and "zopakujte" in warning.lower()
    assert "Civilní matriky Magistrátu" in warning or "Nekatolické matriky" in warning
    for _ in range(12):
        data = await find(service, sid, "Výplně")
        if not any("postupně" in w for w in data["warnings"]):
            break
    assert not any("postupně" in w for w in data["warnings"])


def test_coverage_of_combined_and_special_signatures():
    from rubrika.ahmp import coverage_of
    combo = coverage_of("EVS N1O1Z1", "1783-1836")
    assert all(combo[e].records.status != "uncertain" and combo[e].records.spans[0].start == 1783 for e in ("birth", "marriage", "death"))
    two = coverage_of("PRAV N5O4Z3", "1894-1927")
    assert two["birth"].records.spans and two["death"].records.spans
    plain = coverage_of("MAG N1", "1872-1908")
    assert plain["birth"].records.spans and plain["marriage"].records.status == "uncertain"
    protocol = coverage_of("UP-IZ 1a", "1924-1939")
    assert protocol["death"].index.status == "uncertain"          # "IZ" may be an index
    assert protocol["death"].records.spans and protocol["birth"].records.status == "uncertain"
    assert coverage_of("STKZ O1", "1940-1947")["marriage"].records.spans
