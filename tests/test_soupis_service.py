"""Soupis and census of the Prague City Archives end to end on the synthetic archive."""
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

BASE = {"raw_query": "SYNTHETIC SOUPIS", "event_type": "birth", "year_from": 1878, "year_to": 1878}
NOVAK = {**BASE, "person": {"surname": "Novák", "given_name": "Josef"}}


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    monkeypatch.setattr("rubrika.ahmp.ROOT_ID", D.ROOT)
    monkeypatch.setattr("rubrika.ahmp.EXTRA_ROOTS", D.EXTRA_ROOTS)
    monkeypatch.setattr("rubrika.ahmp.CENSUS_ROOT_ID", D.CENSUS_ROOT)
    monkeypatch.setattr("rubrika.ahmp.CENSUS_YEARS", frozenset({"1869", "1880"}))
    for code in ("ahmp", "na"):
        monkeypatch.setitem(A.ARCHIVES, code, dataclasses.replace(A.ARCHIVES[code], min_interval=0))


@pytest_asyncio.fixture
async def env(tmp_path):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, ahmp_enabled=True, request_interval=0)
    store = Store(cfg)
    archive = D.AhmpArchive()
    service = ResearchService(cfg, store, SafeSource(cfg, store, transport=archive.transport, source_mode="synthetic_demo"))
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


async def research(service, query=NOVAK):
    return (await call(service, "start_research", query=query)).data["research_id"]


async def places(service, sid, term="Praha", **args):
    return (await call(service, "find_place", research_id=sid, term=term, kind="census", archives=["ahmp"], **args)).data


async def listing(service, sid, place, **args):
    return (await call(service, "list_sources", research_id=sid, place_ids=[f"ahmp:node:{place}"], kind="census", **args)).data


def in_window(rows, low=1876, high=1880):
    return [r for r in rows if r[2].isdigit() and low <= int(r[2]) <= high]


def census_place(found):
    return next(p for p in found["places"] if p["label"].startswith("Všeobecné sčítání lidu"))


def expected(lemma, series=D.PRAGUE_SERIES):
    return [r for r in D.SOUPIS[series] if r[4] == lemma]


@pytest.mark.asyncio
async def test_find_place_names_both_series_without_a_request(env):
    service, archive, _ = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", archives=["ahmp"])).data
    soupis = [p for p in data["places"] if p["source_id"] in ("ahmp:node:168668", "ahmp:node:290432")]
    assert [p["source_id"] for p in soupis] == ["ahmp:node:168668", "ahmp:node:290432"]
    assert "domovská příslušnost Praha" in soupis[0]["label"] and "mimo Prahu" in soupis[1]["label"]
    assert soupis[0]["label"].startswith("Soupis pražského obyvatelstva 1830–1910 › Pražští příslušníci")
    assert any("dvě řady" in w for w in data["warnings"]) and data["searched"] == ["ahmp"]


@pytest.mark.asyncio
async def test_census_districts_of_the_online_years_only(env):
    service, archive, _ = env
    sid = await research(service)
    data = await places(service, sid, "Nové Město")
    labels = [p["label"] for p in data["places"]][2:]
    assert labels == ["Všeobecné sčítání lidu 1880 › II-Zkušov Nové Město"]
    year = await places(service, sid, "1880")
    assert len(year["places"]) == 2 + 2                           # every non-empty district of the year
    assert not any("1900" in p["label"] for p in year["places"])


@pytest.mark.asyncio
async def test_unspecified_census_search_leaves_ahmp_out(env):
    service, archive, _ = env
    sid = await research(service)
    data = (await call(service, "find_place", research_id=sid, term="Nové Město", kind="census")).data
    assert "ahmp" not in data["searched"] and any(n["archive"] == "ahmp" for n in data["not_searched"])
    assert [u for u in archive.catalog_requests() if "robots" not in u] == []
    routed = (await call(service, "find_place", research_id=sid, term="Praha", kind="census", district="Praha")).data
    assert "ahmp" in routed["searched"]


@pytest.mark.asyncio
async def test_listing_needs_a_surname(env):
    service, archive, _ = env
    sid = await research(service, BASE)
    await places(service, sid)
    before = len(archive.requests)
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668"], kind="census")
    assert err["code"] == "INVALID_ARGUMENT" and "person.surname" in err["message"] and "revise_research" in err["message"]
    assert len(archive.requests) == before


@pytest.mark.asyncio
async def test_listing_needs_a_found_place(env):
    service, _, _ = env
    sid = await research(service)
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668"], kind="census")
    assert err["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_given_name_narrows_the_search_and_ranks_by_birth_year(env):
    service, archive, _ = env
    sid = await research(service)
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    want = in_window([r for r in expected("Novák") if r[1] in ("Josef", "Joseph")])   # Josef = Joseph, Jan and Johann stay apart
    band = in_window([r for r in expected("Novák") if r[1] in ("Josef", "Joseph")], 1873, 1883)     # ±5 around 1878
    assert want and data["total"] == len(band) and len(band) > len(want) and data["listing_complete"] is True
    assert {s["given_name"] for s in data["sources"]} <= {"Josef", "Joseph"} and all(1873 <= s["birth_year"] <= 1883 for s in data["sources"])
    candidates = [s for s in data["sources"] if s["relevance"]["status"] == "candidate"]
    assert {s["birth_year"] for s in candidates} <= set(range(1876, 1881)) and len(candidates) == len(want)
    outer = [s for s in data["sources"] if s["relevance"]["status"] == "needs_review"]
    assert outer and all("±5" in s["relevance"]["reason"] and "1878–1878" in s["relevance"]["reason"] for s in outer)
    assert any("1873–1883" in w and "listing_complete=true platí jen pro ně" in w for w in data["warnings"])
    assert "pásmu ±5" in data["note"] and "asi 40 s" in data["note"]
    years = [abs(s["birth_year"] - 1878) for s in data["sources"]]
    assert years == sorted(years) or data["sources"][0]["relevance"]["status"] == "candidate"
    assert "1878" in data["sources"][0]["relevance"]["reason"] and "1878–1878" in data["sources"][0]["relevance"]["reason"]
    top = data["sources"][0]
    assert top["relevance"]["status"] == "candidate" and abs(top["birth_year"] - 1878) <= 2
    assert all(s["archive"] == "ahmp" and s["kind"] == "census" and s["source_id"].startswith("ahmp:book:") for s in data["sources"])
    assert [s["relevance"]["status"] for s in data["sources"]] == sorted((s["relevance"]["status"] for s in data["sources"]),
                                                                        key={"candidate": 0, "needs_review": 1, "excluded": 2}.get)
    assert len(archive.catalog_requests()) <= 2 + 25 + 10
    assert not archive.forbidden_hits and all(m == "GET" for m, _, _ in archive.requests)


@pytest.mark.asyncio
async def test_surname_alone_collects_every_spelling_across_pages(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "person": {"surname": "Nowak"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    want = expected("Novák")
    assert data["total"] == len(want) and len(want) > 27 and data["listing_complete"] is True
    assert {s["surname"] for s in data["sources"]} >= {"Novák", "Nowak", "Nováková", "Novák-ová"}
    assert any(s["birth_year"] is None for s in data["sources"]) is False
    assert all(s["relevance"]["status"] == "needs_review" or s["relevance"]["status"] == "candidate" for s in data["sources"])


@pytest.mark.asyncio
async def test_unknown_surname_is_an_empty_verified_listing_not_an_error(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "person": {"surname": "Zzyzx"}})
    await places(service, sid)
    data = await listing(service, sid, 168668)
    assert data["sources"] == [] and data["listing_complete"] is True
    assert any("Zzyzx" in w and "nebyl nalezen" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_missing_given_name_is_reported_not_guessed(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "person": {"surname": "Novák", "given_name": "Eduard"}})
    await places(service, sid)
    data = await listing(service, sid, 168668)
    assert data["sources"] == [] and any("Eduard" in w for w in data["warnings"])
    assert any("není v tabulce českých a německých tvarů" in w and "jen zadaný tvar" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_row_cap_is_reported(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_SHEETS", 10)
    sid = await research(service, {**BASE, "person": {"surname": "Novák"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    assert data["total"] == 10 and data["listing_complete"] is False and any("person.given_name" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_request_budget_cap_gives_an_incomplete_listing(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_REQUESTS_PER_CALL", 3)
    monkeypatch.setattr("rubrika.ahmp.MAX_COLLECT_PAGES", 0)
    sid = await research(service, {**BASE, "person": {"surname": "Vondrák"}})
    await places(service, sid)
    data = await listing(service, sid, 168668)
    assert data["listing_complete"] is False and any("limit" in w for w in data["warnings"])
    assert len([u for u in archive.catalog_requests() if "PaginatorNavig" in u or "NavigBean" in u]) <= 4


@pytest.mark.asyncio
async def test_expired_tokens_restart_the_search_once(env):
    service, archive, _ = env
    sid = await research(service)
    await places(service, sid)
    original, calls = archive.handle, []

    def handle(request):
        calls.append(str(request.url))
        if sum("rowTxt" in u for u in calls) == 2 and "rowTxt" in str(request.url) and not getattr(archive, "_dropped", False):
            archive._dropped = True
            archive.sessions.clear()
        return original(request)
    archive.transport = type(archive.transport)(handle)
    service.source.client._transport = archive.transport                 # noqa: SLF001 (the mock transport of the test client)
    service.source.client._mounts = {}                                   # noqa: SLF001
    data = await listing(service, sid, 168668, limit=20)
    want = in_window([r for r in expected("Novák") if r[1] in ("Josef", "Joseph")], 1873, 1883)
    assert data["total"] == len(want) and data["listing_complete"] is True
    assert len({c for _, _, c in archive.requests if c}) >= 2            # a second session was opened


@pytest.mark.asyncio
async def test_second_search_for_the_same_name_uses_the_remembered_boundary(env):
    service, archive, _ = env
    sid = await research(service)
    await places(service, sid)
    await listing(service, sid, 168668)
    first = len(archive.catalog_requests())
    await listing(service, sid, 168668)
    second = len(archive.catalog_requests()) - first
    assert second < first - 3 and second <= 8


@pytest.mark.asyncio
async def test_foreign_series_is_a_separate_list(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "person": {"surname": "Adler"}})
    await places(service, sid)
    data = await listing(service, sid, 290432, limit=20)
    assert data["total"] == len(expected("Adler", D.FOREIGN_SERIES)) and data["listing_complete"] is True


async def open_sheet(service, sid, index=0):
    await places(service, sid)
    listed = await listing(service, sid, 168668, limit=20)
    sheet = listed["sources"][index]
    return sheet, (await call(service, "open_source", research_id=sid, source_id=sheet["source_id"])).data


@pytest.mark.asyncio
async def test_open_sheet_cites_series_karton_person_and_year(env):
    service, archive, _ = env
    sid = await research(service)
    sheet, opened = await open_sheet(service, sid)
    assert opened["archive"] == "ahmp" and opened["kind"] == "census" and opened["total"] == sheet["image_count"]
    m = re.fullmatch(r"Archiv hlavního města Prahy, Soupis pražského obyvatelstva 1830–1910, Pražští příslušníci, "
                     r"karton \d+, Novák, Josef \(\*(\d{4})\)", opened["citation"]) or re.fullmatch(
        r"Archiv hlavního města Prahy, Soupis pražského obyvatelstva 1830–1910, Pražští příslušníci, karton \d+, "
        r"Nov[áa][k]\w*, Josef \(\*(\d{4})\)", opened["citation"])
    assert m and int(m[1]) == sheet["birth_year"] and opened["unit"]["box"].startswith("karton/")
    assert opened["pages"][0]["page_id"].startswith(sheet["source_id"] + ":") and opened["pages"][0]["viewer_url"].endswith(f"&scan=1")


@pytest.mark.asyncio
async def test_scan_of_a_sheet_can_be_viewed_and_zoomed(env):
    service, archive, _ = env
    sid = await research(service)
    sheet, opened = await open_sheet(service, sid, index=1)
    page = opened["pages"][0]
    view = await call(service, "view_scan", research_id=sid, page_id=page["page_id"])
    ref = view.data["image"]
    xid = page["page_id"].split(":")[2]
    assert (ref["source_width"], ref["source_height"]) == D.scan_size(1) and ref["archive"] == "ahmp"
    assert ref["citation"].startswith("Archiv hlavního města Prahy, Soupis pražského obyvatelstva")
    assert any(u == D.root_of(xid, 1, "NAD-4") + "/ImageProperties.xml" for u in archive.image_requests())
    zoom = await call(service, "zoom_scan", research_id=sid, image_id=ref["image_id"],
                      bbox_px={"x": 0, "y": 0, "width": ref["width"] / 4, "height": ref["height"] / 4})
    Image.open(io.BytesIO(base64.b64decode(zoom.image_b64))).load()
    assert not archive.forbidden_hits


@pytest.mark.asyncio
async def test_opened_sheet_reaches_the_report_and_the_gedcom_title(env):
    service, _, _ = env
    sid = await research(service)
    sheet, opened = await open_sheet(service, sid)
    report = service._report(sid, "alice")                                    # noqa: SLF001
    [entry] = [b for b in report["coverage"]["opened_books"] if b["book_id"] == sheet["source_id"]]
    assert entry["source"] == "ahmp" and entry["surname"] == "Novák" and entry["census"] is True
    title = service._tree_source({"id": sheet["source_id"], **opened["unit"]})["title"]      # noqa: SLF001
    assert title == f"Archiv hl. m. Prahy, Soupis obyvatelstva, Novák {sheet['given_name']}"


# ---------------------------------------------------------------- census houses

def census_query(house):
    return {**BASE, "year_from": None, "year_to": None, "person": {"house_number": house}}


@pytest.mark.asyncio
async def test_census_house_is_found_by_number_and_marked_covered(env):
    service, archive, _ = env
    sid = await research(service, census_query("31"))
    found = await places(service, sid, "Nové Město")
    place = census_place(found)["source_id"]
    data = (await call(service, "list_sources", research_id=sid, place_ids=[place], kind="census")).data
    [row] = data["sources"]
    assert row["house_number"] == 31 and row["house_numbers"]["numbers"] == [31] and row["relevance"]["house_number_covered"] is True
    assert row["district"] == "II-Zkušov Nové Město" and row["census_year"] == 1880
    opened = (await call(service, "open_source", research_id=sid, source_id=row["source_id"])).data
    assert opened["citation"] == "Archiv hlavního města Prahy, Všeobecné sčítání lidu 1880, II-Zkušov Nové Město, čp. 31"
    assert opened["kind"] == "census"
    view = await call(service, "view_scan", research_id=sid, page_id=opened["pages"][0]["page_id"])
    assert view.data["image"]["archive"] == "ahmp"
    assert any("/spisy/" in u for u in archive.image_requests())


@pytest.mark.asyncio
async def test_missing_house_number_is_explained_with_its_neighbours(env):
    service, _, _ = env
    sid = await research(service, census_query("30"))
    place = census_place(await places(service, sid, "Nové Město"))["source_id"]
    data = (await call(service, "list_sources", research_id=sid, place_ids=[place], kind="census")).data
    assert data["sources"] == [] and data["listing_complete"] is True
    assert any("čp. 30" in w or "číslo popisné 30" in w.lower() or "Číslo popisné 30" in w for w in data["warnings"])
    assert any("29" in w and "31" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_census_district_needs_a_house_number(env):
    service, _, _ = env
    sid = await research(service, BASE)
    place = census_place(await places(service, sid, "Nové Město"))["source_id"]
    err = await failing(service, "list_sources", research_id=sid, place_ids=[place], kind="census")
    assert err["code"] == "INVALID_ARGUMENT" and "house_number" in err["message"]


@pytest.mark.asyncio
async def test_years_cannot_be_filtered_and_one_place_only(env):
    service, _, _ = env
    sid = await research(service)
    await places(service, sid)
    assert (await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668"], kind="census", year_from=1880,
                          year_to=1880))["code"] == "INVALID_ARGUMENT"
    assert (await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668", "ahmp:node:290432"],
                          kind="census"))["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- very common names and the birth-year window

def kucera(year_from=None, year_to=None, given="Josef", event="birth"):
    q = {"raw_query": "SYNTHETIC KUCERA", "event_type": event, "person": {"surname": "Kučera", "given_name": given}}
    if year_from:
        q.update(year_from=year_from, year_to=year_to or year_from)
    return q


@pytest.mark.asyncio
async def test_synthetic_common_name_really_exceeds_the_cap():
    assert len([r for r in D.SOUPIS[D.PRAGUE_SERIES] if r[4] == "Kučera" and r[1] == "Josef"]) > 200


@pytest.mark.asyncio
async def test_birth_year_window_reaches_past_the_old_cap(env):
    service, archive, _ = env
    sid = await research(service, kucera(1835))
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    want = [r for r in D.SOUPIS[D.PRAGUE_SERIES] if r[4] == "Kučera" and r[1] == "Josef" and 1833 <= int(r[2]) <= 1837]
    assert len(want) == 15 and data["total"] == 33 and data["listing_complete"] is True          # band 1830–1840, ±2 are candidates
    assert all(1830 <= s["birth_year"] <= 1840 for s in data["sources"])
    assert sum(s["relevance"]["status"] == "candidate" for s in data["sources"]) == 15
    assert data["sources"][0]["birth_year"] == 1835 and data["sources"][0]["relevance"]["status"] == "candidate"
    assert len([u for u in archive.catalog_requests() if "robots" not in u]) <= 2 + 25 + 10


@pytest.mark.asyncio
async def test_without_a_year_the_cap_applies_and_the_warning_asks_for_one(env):
    service, _, _ = env
    sid = await research(service, kucera())
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    assert data["total"] == 90 and data["listing_complete"] is False
    assert any("rok narození" in w and "revise_research" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_a_window_that_itself_overflows_is_reported_as_such(env):
    service, _, _ = env
    sid = await research(service, kucera(1830, 1900))
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    assert data["total"] == 90 and data["listing_complete"] is False and any("V pásmu let narození 1825–1905" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_a_year_needs_a_birth_event_and_a_given_name(env):
    service, _, _ = env
    for query in (kucera(1835, event="marriage"), {**kucera(1835), "person": {"surname": "Kučera"}}):
        sid = await research(service, query)
        await places(service, sid)
        data = await listing(service, sid, 168668)
        assert data["listing_complete"] is False           # the cap of the whole name still applies


@pytest.mark.asyncio
async def test_no_match_names_the_nearest_surnames(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "person": {"surname": "Novotný"}})
    await places(service, sid)
    data = await listing(service, sid, 168668)
    text = " ".join(data["warnings"])
    assert data["sources"] == [] and "nejbližší příjmení v seznamu: Nový a Pospíšil" in text



def requests_of(archive):
    return [u for u in archive.catalog_requests() if "robots" not in u]


@pytest.mark.asyncio
async def test_a_restart_after_token_expiry_stays_inside_the_one_budget(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_REQUESTS_PER_CALL", 12)
    sid = await research(service, kucera(1835))
    await places(service, sid)
    before = len(requests_of(archive))
    original, seen = archive.handle, []

    def handle(request):
        seen.append(str(request.url))
        if sum("rowTxt" in u for u in seen) == 3 and "rowTxt" in str(request.url):
            archive.sessions.clear()
        return original(request)
    archive.transport = type(archive.transport)(handle)
    service.source.client._transport = archive.transport                 # noqa: SLF001
    data = await listing(service, sid, 168668, limit=20)
    assert len(requests_of(archive)) - before <= 12 and len({c for _, _, c in archive.requests if c}) >= 2
    assert data["listing_complete"] is False and any("limit 12 požadavků" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_german_forms_of_the_given_name_are_searched_as_runs_of_their_own(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "year_from": None, "year_to": None, "person": {"surname": "Novák", "given_name": "Václav"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    assert {s["given_name"] for s in data["sources"]} == {"Václav", "Wenzl"} and data["listing_complete"] is True
    assert any(s["relevance"]["status"] in ("candidate", "needs_review") for s in data["sources"])
    assert any("Václav" in w and "Wenzel" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_variants_the_budget_did_not_reach_are_named(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_REQUESTS_PER_CALL", 5)
    sid = await research(service, {**BASE, "year_from": None, "year_to": None, "person": {"surname": "Novák", "given_name": "Václav"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    assert data["listing_complete"] is False and any("neprohledáno" in w and "Wenzl" in w for w in data["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("surname, plain, marked", [("Dvořák", "Dvorak", "Dvořák"), ("Šimek", "Simek", "Šimek")])
async def test_spellings_with_and_without_diacritics_are_both_found(env, surname, plain, marked):
    service, _, _ = env
    sid = await research(service, {**BASE, "year_from": None, "year_to": None, "person": {"surname": surname, "given_name": "Jan"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    found = {s["surname"] for s in data["sources"]}
    assert plain in found and any(n.startswith(marked[:2]) and n != plain for n in found)
    assert data["total"] >= len([r for r in D.SOUPIS[D.PRAGUE_SERIES] if r[4] in (plain, marked) and r[1] == "Jan"]) and data["listing_complete"] is True


@pytest.mark.asyncio
async def test_a_miss_says_what_was_searched_and_names_neighbours_only_for_a_missing_surname(env):
    service, _, _ = env
    sid = await research(service, {**BASE, "year_from": 1700, "year_to": 1700, "person": {"surname": "Novák", "given_name": "Josef"}})
    await places(service, sid)
    data = await listing(service, sid, 168668)
    text = " ".join(data["warnings"])
    assert data["sources"] == [] and "žádný list pro Novák Josef/Joseph v letech 1695–1705" in text
    assert "nejbližší příjmení" not in text and "v seznamu není" not in text


@pytest.mark.asyncio
async def test_cut_by_the_page_cap_is_not_reported_as_too_many_sheets(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_COLLECT_PAGES", 1)
    sid = await research(service, {**BASE, "person": {"surname": "Novák"}})
    await places(service, sid)
    data = await listing(service, sid, 168668, limit=20)
    text = " ".join(data["warnings"])
    assert data["listing_complete"] is False and "limitu stránek" in text and "více listů, než se vejde" not in text


@pytest.mark.asyncio
async def test_a_place_of_the_other_kind_is_refused(env):
    service, _, _ = env
    sid = await research(service)
    await places(service, sid)
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668"], kind="parish_registers")
    assert err["code"] in ("INVALID_ARGUMENT", "NOT_FOUND")
    found = (await call(service, "find_place", research_id=sid, term="sv. Testa", kind="parish_registers", archives=["ahmp"])).data
    err = await failing(service, "list_sources", research_id=sid, place_ids=[found["places"][0]["source_id"]], kind="census")
    assert err["code"] in ("INVALID_ARGUMENT", "NOT_FOUND")
    err = await failing(service, "list_sources", research_id=sid, place_ids=["ahmp:node:168668"], kind="parish_registers")


@pytest.mark.asyncio
@pytest.mark.parametrize("given", ["31a", "čp. 31", " 31 "])
async def test_house_numbers_with_extras_are_read_by_their_digits(env, given):
    service, _, _ = env
    sid = await research(service, census_query(given))
    place = census_place(await places(service, sid, "Nové Město"))["source_id"]
    data = (await call(service, "list_sources", research_id=sid, place_ids=[place], kind="census")).data
    assert [s["house_number"] for s in data["sources"]] == [31]
    assert (given.strip() == "31") or any("eviduje jen čísla popisná" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_district_and_year_come_from_the_census_node(env):
    service, _, _ = env
    sid = await research(service, census_query("3"))
    place = census_place(await places(service, sid, "Josefov"))
    assert place["names"] == ["V-Zkušov Josefov", "1880"]
    data = (await call(service, "list_sources", research_id=sid, place_ids=[place["source_id"]], kind="census")).data
    assert data["sources"][0]["district"] == "V-Zkušov Josefov" and data["sources"][0]["census_year"] == 1880


@pytest.mark.asyncio
async def test_unsorted_house_rows_are_reported_with_what_was_found(env, monkeypatch):
    service, _, _ = env
    monkeypatch.setitem(D.CENSUS_ROWS, D.JOSEFOV, list(reversed(D.CENSUS_ROWS[D.JOSEFOV])))
    monkeypatch.setattr(D, "_book_index", D._book_index)
    service.source.client._transport = service.source.client._transport   # noqa: SLF001
    sid = await research(service, census_query("3"))
    place = census_place(await places(service, sid, "Josefov"))["source_id"]
    data = (await call(service, "list_sources", research_id=sid, place_ids=[place], kind="census")).data
    assert data["listing_complete"] is False and any("neodpovídá číselnému pořadí" in w and "Nalezeno:" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_boundaries_found_by_a_cut_call_let_a_repeat_call_finish(env, monkeypatch):
    service, archive, _ = env
    monkeypatch.setattr("rubrika.ahmp.MAX_REQUESTS_PER_CALL", 8)
    sid = await research(service, {**BASE, "year_from": None, "year_to": None, "person": {"surname": "Novák", "given_name": "Václav"}})
    await places(service, sid)
    used, complete = [], []
    for _ in range(4):
        before = len(requests_of(archive))
        data = await listing(service, sid, 168668, limit=20)
        used.append(len(requests_of(archive)) - before)
        complete.append(data["listing_complete"])
        if data["listing_complete"]:
            break
    assert complete[0] is False and complete[-1] is True and len(complete) >= 2 and max(used) <= 8
    want = [r for r in expected("Novák") if r[1] in ("Václav", "Wenzl")]
    assert data["total"] == len(want)


@pytest.mark.asyncio
async def test_a_changed_list_total_discards_the_remembered_boundary(env):
    service, archive, store = env
    sid = await research(service)
    await places(service, sid)
    await listing(service, sid, 168668)
    first = len(requests_of(archive))
    original = D.SOUPIS[D.PRAGUE_SERIES]
    D.SOUPIS[D.PRAGUE_SERIES] = original + [("Zeman", "Jan", "1900", 1, "Žák")]
    try:
        await listing(service, sid, 168668)
    finally:
        D.SOUPIS[D.PRAGUE_SERIES] = original
    assert len(requests_of(archive)) - first > 5           # no shortcut: the total differs, the hint was dropped


@pytest.mark.asyncio
async def test_house_number_without_surname_ranks_districts_first_and_drops_the_surname_hint(env):
    service, _, _ = env
    sid = await research(service, census_query("31"))
    data = await places(service, sid, "Nové Město")
    labels = [p["label"] for p in data["places"]]
    assert labels[0].startswith("Všeobecné sčítání lidu 1880") and labels[-2].startswith("Soupis")
    assert not any("dvě řady" in w for w in data["warnings"]) and any("person.house_number" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_surname_without_house_number_keeps_the_soupis_first(env):
    service, _, _ = env
    sid = await research(service)
    data = await places(service, sid, "Nové Město")
    assert data["places"][0]["label"].startswith("Soupis") and any("dvě řady" in w for w in data["warnings"])
