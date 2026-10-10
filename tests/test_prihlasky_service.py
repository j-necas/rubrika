"""Police registration forms of Prague end to end on the synthetic application: find_place → list_sources → open_source → view_scan."""
import base64
import dataclasses
import io

import pytest
import pytest_asyncio
from PIL import Image

from rubrika import archives as A
from rubrika import demo_prihlasky as D
from rubrika import prihlasky as P
from rubrika.config import Settings
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

CODE = "na_prihlasky"
PLACE = "na_prihlasky:place:praha"
VZORNY = {"raw_query": "SYNTHETIC", "event_type": "birth", "year_from": 1878, "year_to": 1878,
          "person": {"surname": "Vzorný", "given_name": "Josef"}}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setitem(A.ARCHIVES, CODE, dataclasses.replace(A.ARCHIVES[CODE], min_interval=0))


def make(tmp_path, **settings):
    cfg = Settings(_env_file=None, data_dir=tmp_path, **{"live_enabled": True, "prihlasky_enabled": True, "request_interval": 0, **settings})
    store = Store(cfg)
    archive = D.DemoPrihlasky()
    service = ResearchService(cfg, store, SafeSource(cfg, store, transport=archive.transport, source_mode="synthetic_demo"))
    return service, archive, store


@pytest_asyncio.fixture
async def env(tmp_path):
    service, archive, store = make(tmp_path)
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


async def research(service, query=VZORNY):
    sid = (await call(service, "start_research", query=query)).data["research_id"]
    await call(service, "find_place", research_id=sid, term="Praha", kind="census", archives=[CODE])
    return sid


async def listing(service, sid, **args):
    return (await call(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census", **args)).data


def sources(data):
    return {r["source_id"]: r for r in data["sources"]}


@pytest.mark.asyncio
async def test_find_place_names_the_one_place_without_a_request(env):
    service, archive, _ = env
    sid = (await call(service, "start_research", query=VZORNY)).data["research_id"]
    data = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", archives=[CODE])).data
    assert [p["source_id"] for p in data["places"]] == [PLACE] and data["searched"] == [CODE] and data["places"][0]["archive"] == CODE
    assert data["places"][0]["label"] == "Policejní přihlášky Prahy 1850–1914" and "person.surname" in data["warnings"][0]
    assert "HTTP" in data["terms_notice"] and "robots" in data["terms_notice"].lower()
    assert archive.requests == []


@pytest.mark.asyncio
async def test_default_census_search_leaves_the_source_out_and_says_how_to_ask_for_it(env):
    service, archive, _ = env
    sid = (await call(service, "start_research", query=VZORNY)).data["research_id"]
    data = (await call(service, "find_place", research_id=sid, term="Praha", kind="census")).data
    assert CODE not in data["searched"] and any("Policejní přihlášky" in w and 'archives=["na_prihlasky"]' in w for w in data["warnings"])
    routed = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", district="Praha")).data
    assert CODE not in routed["searched"] and not any(p["archive"] == CODE for p in routed["places"])
    assert archive.requests == []
    other = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", archives=["na"])).data
    assert other["places"] == [] and other["searched"] == [] and other["not_searched"][0]["archive"] == "na"


@pytest.mark.asyncio
async def test_list_archives_shows_the_source_with_its_notice(env):
    service, _, _ = env
    row = next(a for a in (await call(service, "list_archives")).data["archives"] if a["code"] == CODE)
    assert row["available_kinds"] == ["census"] and not row["link_only"] and row["robots_mode"] == "user_triggered"
    assert "HTTP" in row["terms_notice"] and row["homepage"].startswith("http://digi.nacr.cz/")


@pytest.mark.asyncio
async def test_listing_needs_a_surname(env):
    service, archive, _ = env
    sid = await research(service, {"raw_query": "SYNTHETIC", "event_type": "birth", "year_from": 1878, "year_to": 1878})
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census")
    assert err["code"] == "INVALID_ARGUMENT" and "person.surname" in err["message"] and "revise_research" in err["message"]
    assert archive.catalog_requests() == []


@pytest.mark.asyncio
async def test_listing_searches_with_the_year_fields_in_the_archives_order_and_reads_every_household(env):
    service, archive, _ = env
    sid = await research(service)
    data = await listing(service, sid)
    assert archive.forms == [{"prijmeni": "Vzorný", "jmeno": "Josef", "odroku": "1873", "doroku": "1883", "action": "hledani2", "tlacitko": "Najít"}]
    rows = sources(data)
    assert sorted(rows) == ["na_prihlasky:form:901:11", "na_prihlasky:form:901:12", "na_prihlasky:form:902:5", "na_prihlasky:form:906:4"]
    first = rows["na_prihlasky:form:901:11"]
    assert first["karton"] == 901 and first["folium"] == 11 and first["archive"] == CODE and first["kind"] == "census"
    assert first["citation"] == "Národní archiv, Policejní ředitelství I, konskripce, karton 901, obraz 11"
    assert first["detail_url"] == "http://digi.nacr.cz/prihlasky2/index.php?action=link&ref=czarch:CZ-100000010:874&karton=901&folium=11"
    assert [m["raw"] for m in first["household"]] == ["Vzorný Josef (1878)", "Vzorná (Ukázková) Marie (1880)", "Vzorný Karel (1905)"]
    assert first["index_hits"] == ["Vzorný Josef (1878)"] and first["relevance"]["status"] == "candidate"
    assert all(r["relevance"]["status"] == "candidate" for r in data["sources"])       # 1876, 1878, 1879 are within ±2
    assert rows["na_prihlasky:form:906:4"]["household"][0]["other_surnames"] == ["Vzorná"]
    assert data["listing_complete"] is True and data["counts"] == {"candidate": 4}
    assert any("1873–1883" in w and "±2" in w for w in data["warnings"]) and any("transliterac" in w for w in data["warnings"])
    # entry page, one search, the second result page (3 rows to a page), one detail page per form; all plain http
    log = archive.catalog_requests()
    assert [m for m, _ in archive.requests if m == "POST"] == ["POST"] and len(log) == 1 + 1 + 1 + 4
    assert all(u.startswith("http://digi.nacr.cz/prihlasky2/") for u in log)
    assert sum("action=image&record=" in u for u in log) == 4 and sum("action=list&od=3" in u for u in log) == 1


@pytest.mark.asyncio
async def test_the_second_spelling_is_tried_only_when_the_first_finds_nothing(env):
    service, archive, _ = env
    sid = await research(service, {**VZORNY, "person": {"surname": "Čech", "given_name": "Petr"}, "year_from": 1870, "year_to": 1870})
    data = await listing(service, sid)
    assert [f["prijmeni"] for f in archive.forms] == ["Čech", "Cech"]
    assert list(sources(data)) == ["na_prihlasky:form:907:6"] and any("Cech" in w and "nic nenašla" in w for w in data["warnings"])
    both = await research(service)
    archive.forms.clear()
    await listing(service, both)
    assert len(archive.forms) == 1


@pytest.mark.asyncio
async def test_the_german_form_of_the_given_name_is_the_last_resort(env):
    service, archive, _ = env
    sid = await research(service, {**VZORNY, "person": {"surname": "Zkoušený", "given_name": "Josef"}, "year_from": 1870, "year_to": 1870})
    data = await listing(service, sid)
    assert [(f["prijmeni"], f["jmeno"]) for f in archive.forms] == [("Zkoušený", "Josef"), ("Zkouseny", "Josef"), ("Zkoušený", "Joseph")]
    assert list(sources(data)) == ["na_prihlasky:form:908:1"]
    assert sources(data)["na_prihlasky:form:908:1"]["relevance"]["status"] == "candidate"            # Josef = Joseph


@pytest.mark.asyncio
async def test_nothing_found_is_an_empty_complete_listing_with_a_hint(env):
    service, archive, _ = env
    sid = await research(service, {**VZORNY, "person": {"surname": "Řezníček"}})
    data = await listing(service, sid)
    assert data["sources"] == [] and data["total"] == 0 and data["listing_complete"] is True
    assert any("Řezníček" in w and "transliterac" in w for w in data["warnings"])
    assert len(archive.forms) == 2 and sum("action=image" in u for u in archive.catalog_requests()) == 0


@pytest.mark.asyncio
async def test_no_years_means_empty_year_fields(env):
    service, archive, _ = env
    sid = await research(service, {"raw_query": "SYNTHETIC", "person": {"surname": "Hromadný", "given_name": "Jan"}})
    data = await listing(service, sid)
    assert archive.forms[0]["odroku"] == "" and archive.forms[0]["doroku"] == ""
    assert data["total"] == 15 and data["listing_complete"] is False              # 31 records; the first pages hold fewer than exist


@pytest.mark.asyncio
async def test_paging_is_followed_and_capped(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr(P, "MAX_RESULT_PAGES", 2)
    sid = await research(service, {"raw_query": "SYNTHETIC", "event_type": "birth", "year_from": 1875, "year_to": 1875,
                                   "person": {"surname": "Hromadný"}})
    data = await listing(service, sid, limit=20)
    lists = [u for u in archive.catalog_requests() if "action=list" in u]
    assert len(lists) == 1 and "od=3" in lists[0]                                # page 2 only; the cap stops the walk
    assert data["listing_complete"] is False and any("Nalezeno 11 záznamů, přečteno prvních 6" in w for w in data["warnings"])
    assert data["total"] == 6


@pytest.mark.asyncio
async def test_detail_reads_are_capped_and_the_best_rows_come_first(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr(P, "MAX_DETAILS", 3)
    sid = await research(service, {"raw_query": "SYNTHETIC", "event_type": "birth", "year_from": 1875, "year_to": 1875,
                                   "person": {"surname": "Hromadný", "given_name": "Jan"}})
    data = await listing(service, sid, limit=20)
    assert data["total"] == 3 and data["listing_complete"] is False
    years = sorted(m["birth_year"] for r in data["sources"] for m in r["household"])
    assert years == [1874, 1875, 1876]                                            # nearest the researched year, not the first in the list
    assert any("dalších 8" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_request_budget_of_one_call_is_kept(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr(P, "MAX_REQUESTS_PER_CALL", 5)
    sid = await research(service, {"raw_query": "SYNTHETIC", "event_type": "birth", "year_from": 1875, "year_to": 1875,
                                   "person": {"surname": "Hromadný"}})
    data = await listing(service, sid, limit=20)
    assert len(archive.catalog_requests()) <= 5 and data["listing_complete"] is False
    assert any("neúplný" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_a_lost_session_is_an_error_not_somebody_elses_list(env):
    service, archive, store = env
    archive.after_search = archive.forget
    sid = await research(service, {**VZORNY, "person": {"surname": "Hromadný"}, "year_from": 1875, "year_to": 1875})
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census")
    assert err["code"] == "SOURCE_UNAVAILABLE" and err["details"]["reason"] == "session_expired"
    assert store.links(sid, "alice", "book_option") == []


@pytest.mark.asyncio
async def test_a_changed_page_structure_is_a_parse_error(env):
    service, archive, store = env
    archive.break_structure = True
    sid = await research(service)
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census")
    assert err["code"] == "PARSE_ERROR" and store.links(sid, "alice", "book_option") == []


@pytest.mark.asyncio
async def test_open_source_reuses_the_detail_read_by_the_listing(env):
    service, archive, _ = env
    sid = await research(service)
    listed = sources(await listing(service, sid))["na_prihlasky:form:901:11"]
    before = len(archive.requests)
    data = (await call(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11")).data
    assert len(archive.requests) == before, "the listing already read the form: no second request"
    assert data["archive"] == CODE and data["kind"] == "census" and data["total"] == 1 and data["next_cursor"] is None
    assert data["citation"] == "Národní archiv, Policejní ředitelství I, konskripce, karton 901, obraz 11"
    assert [m["given_name"] for m in data["unit"]["household"]] == ["Josef", "Marie", "Karel"]
    # head is the first person listed in both; the search hit is marked apart, and the maiden name is parsed from the bracket
    for household in (listed["household"], data["unit"]["household"]):
        assert [(m["head"], m["matched"]) for m in household] == [(True, True), (False, False), (False, False)]
    assert data["unit"]["household"][1]["maiden_name"] == "Ukázková" and data["unit"]["household"][0]["maiden_name"] is None
    page = data["pages"][0]
    assert page["page_id"] == "na_prihlasky:form:901:11:1" and page["source_type"] == "jpeg" and page["archive"] == CODE
    assert page["source_url"] == "http://digi.nacr.cz/prihlasky2/images/00000901-00000011.jpg" and page["source"] == "prihlasky"
    assert page["viewer_url"] == data["unit"]["detail_url"] and page["citation"] == data["citation"]
    assert "HTTP" in data["terms_notice"]


@pytest.mark.asyncio
async def test_a_form_without_a_household_is_read_by_its_permalink_uncached(env):
    service, archive, store = env
    sid = await research(service)
    await listing(service, sid)
    book = store.entity("book", "na_prihlasky:form:901:11")
    store.put_entity("book", "na_prihlasky:form:901:11", {**book, "household": []})
    before = len(archive.requests)
    data = (await call(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11")).data
    new = [u for _, u in archive.requests[before:] if "robots" not in u]
    assert new == ["http://digi.nacr.cz/prihlasky2/index.php?action=link&ref=czarch:CZ-100000010:874&karton=901&folium=11"]
    assert len(data["unit"]["household"]) == 3


@pytest.mark.asyncio
async def test_married_women_are_indexed_under_the_married_surname_only(env):
    service, archive, _ = env
    sid = await research(service, {**VZORNY, "year_from": 1880, "year_to": 1880, "person": {"surname": "Ukázková", "given_name": "Marie"}})
    data = await listing(service, sid)
    assert data["sources"] == [] and any("pod příjmením manžela" in w and "bez let" in w for w in data["warnings"])
    wife = await research(service, {**VZORNY, "year_from": 1880, "year_to": 1880, "person": {"surname": "Vzorná", "given_name": "Marie"}})
    data = await listing(service, wife)
    assert "na_prihlasky:form:901:11" in sources(data)
    assert any("pod příjmením manžela" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_a_detail_of_another_person_is_dropped(env):
    service, archive, store = env
    archive.shift_details = True
    sid = await research(service)
    data = await listing(service, sid)
    assert any("neodpovídá řádku" in w for w in data["warnings"])
    # only a form whose member happens to be identical to the row (Josef 1878 in two forms) survives; the others are dropped
    assert set(sources(data)) <= {"na_prihlasky:form:906:4"} and len(data["sources"]) < 4


@pytest.mark.asyncio
async def test_the_note_says_what_complete_means_and_what_is_not_read(env):
    service, _, _ = env
    sid = await research(service)
    note = (await listing(service, sid))["note"]
    assert "pásmo" in note and "bez roku narození" in note and "Charakter" in note and "maiden_name" in note


@pytest.mark.asyncio
async def test_open_source_refuses_what_was_not_listed_or_is_malformed(env):
    service, archive, _ = env
    sid = await research(service)
    assert (await failing(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11"))["code"] == "NOT_FOUND"
    for bad in ("na_prihlasky:form:x:1", "na_prihlasky:form:901", "na_prihlasky:place:praha"):
        assert (await failing(service, "open_source", research_id=sid, source_id=bad))["code"] == "INVALID_ARGUMENT"
    assert archive.catalog_requests() == []


@pytest.mark.asyncio
async def test_view_and_zoom_the_whole_scan(env):
    service, archive, _ = env
    sid = await research(service)
    await listing(service, sid)
    page = (await call(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11")).data["pages"][0]
    view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    ref = view.data["image"]
    assert ref["archive"] == CODE and ref["source"] == "prihlasky" and (ref["source_width"], ref["source_height"]) == D.SCAN_SIZE
    assert ref["viewer_url"] == page["viewer_url"] and ref["citation"] == page["citation"] and ref["robots_disallowed"] is True
    scans = [u for _, u in archive.requests if u.endswith(".jpg")]
    assert scans == ["http://digi.nacr.cz/prihlasky2/images/00000901-00000011.jpg"]
    before = len(archive.requests)
    sx, sy = ref["width"] / ref["source_width"], ref["height"] / ref["source_height"]
    x0, y0, x1, y1 = D.MARKER
    zoom = await call(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                      bbox_px={"x": (x0 - 20) * sx, "y": (y0 - 20) * sy, "width": (x1 - x0 + 40) * sx, "height": (y1 - y0 + 40) * sy})
    assert len(archive.requests) == before, "the second look must come from the cache"
    z = zoom.data["image"]
    x, y, w, h = z["source_bbox"]
    got = Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).convert("RGB")
    r, g, b = got.getpixel(((x0 + x1) // 2 - x, (y0 + y1) // 2 - y))
    assert b > 150 and r < 130 and got.getpixel((2, 2))[0] > 200
    status = (await call(service, "get_research_status", research_id=sid)).data
    assert status["coverage"]["robots_tolerated"] == ["digi.nacr.cz"]
    opened = status["coverage"]["opened_books"][0]
    assert opened["source"] == "prihlasky" and opened["archive"] == CODE and opened["census"] is True
    assert opened["household"][1] == "Vzorná (Ukázková) Marie (1880)" and opened["detail_url"].startswith("http://digi.nacr.cz/")


@pytest.mark.asyncio
async def test_a_broken_scan_is_not_kept(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr(D, "scan_jpeg", lambda k, f: b"\xff\xd8\xff" + b"garbage")
    sid = await research(service)
    await listing(service, sid)
    page = (await call(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11")).data["pages"][0]
    err = await failing(service, "view_scan", research_id=sid, page_id=page["page_id"])
    assert err["code"] == "INCOMPLETE_IMAGE"
    assert service.source.cache_get(page["source_url"], 999) is None


@pytest.mark.asyncio
async def test_unlisted_pages_are_refused(env):
    service, archive, _ = env
    sid = await research(service)
    err = await failing(service, "view_scan", research_id=sid, page_id="na_prihlasky:form:901:11:1")
    assert err["code"] == "NOT_FOUND"
    assert [u for u in archive.catalog_requests() if u.endswith(".jpg")] == []


@pytest.mark.asyncio
async def test_switch_off_means_not_searched_and_no_request(tmp_path):
    service, archive, store = make(tmp_path, prihlasky_enabled=False)
    sid = (await call(service, "start_research", query=VZORNY)).data["research_id"]
    data = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", archives=[CODE])).data
    assert data["places"] == [] and data["not_searched"][0]["archive"] == CODE and "vypnutý" in data["not_searched"][0]["reason"]
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census")
    assert err["code"] == "LIVE_DISABLED"
    assert archive.requests == []
    await service.close(); store.close()


@pytest.mark.asyncio
async def test_years_cannot_be_filtered_and_other_kinds_are_refused(env):
    service, archive, _ = env
    sid = await research(service)
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census", year_from=1880, year_to=1880)
    assert err["code"] == "INVALID_ARGUMENT" and "year_from" in err["message"]
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="parish_registers")
    assert err["code"] == "LIVE_DISABLED"
    err = await failing(service, "list_sources", research_id=sid, place_ids=["na_prihlasky:place:jine"], kind="census")
    assert err["code"] == "INVALID_ARGUMENT"
    assert archive.catalog_requests() == []


@pytest.mark.asyncio
async def test_list_before_find_place_is_refused(env):
    service, archive, _ = env
    sid = (await call(service, "start_research", query=VZORNY)).data["research_id"]
    err = await failing(service, "list_sources", research_id=sid, place_ids=[PLACE], kind="census")
    assert err["code"] == "NOT_FOUND" and archive.catalog_requests() == []


@pytest.mark.asyncio
async def test_the_family_tree_cites_the_form(env):
    service, archive, _ = env
    sid = await research(service)
    await listing(service, sid)
    page = (await call(service, "open_source", research_id=sid, source_id="na_prihlasky:form:901:11")).data["pages"][0]
    view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    obs = (await call(service, "record_finding", research_id=sid, idempotency_key="k1", observation={
        "kind": "record", "image_id": view.data["image"]["image_id"], "text": "Synthetic household", "is_candidate": True})).data
    tree = {"title": "Synthetic", "root_person": "p1", "persons": [{"id": "p1", "given_name": "Josef", "surname": "Vzorný", "sex": "M", "events": [
        {"type": "residence", "place": "Praha", "confidence": "documented", "sources": [{"research_id": sid, "observation_id": obs["observation_id"]}]}]}]}
    out = (await call(service, "export_family_tree", tree=tree)).data
    assert "Národní archiv, Policejní ředitelství I – konskripce, karton 901, obraz 11" in str(out)
