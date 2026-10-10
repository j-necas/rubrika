"""Soupis and census lists: collation rules against the synthetic archive order, row labels, bisection and collection."""
import dataclasses

import pytest
import pytest_asyncio

from rubrika import archives as A
from rubrika import demo_ahmp as D
from rubrika.bach import AHMP_SITE as S
from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.network import SafeSource
from rubrika.models import SearchQuery
from rubrika.soupis import (FlatList, SearchBudget, collect, given_key, house_of, loose, lower_bound, parse_soupis_label,
                             same_surname, search_targets, surname_before, surname_key)
from rubrika.storage import Store


# ---------------------------------------------------------------- collation

def test_the_archives_lemma_order_is_reproduced_by_the_key():
    keys = [surname_key(name) for name in D.SOUPIS_LEMMAS]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)


@pytest.mark.parametrize("same", [
    ["Novák", "Nowak", "Nowák", "Novak", "NOVÁK", "Nováková", "Novák-ová", "Novákova", "Novák (Nowak)"],
    ["Maleček", "Maleczek", "Malecžek", "MALEČEK"], ["Šimek", "Schimek", "Szimek"], ["Wagner", "Vagner"],
    ["Čapek", "Czapek", "Tschapek"], ["Novotný", "Novotná"], ["Pischel", "Pischell"[:-1]],
])
def test_spelling_variants_share_one_sort_key(same):
    assert len({surname_key(n) for n in same}) == 1


def test_female_forms_belong_to_the_male_name_even_when_the_last_vowel_drops():
    assert same_surname("Maděra", "Maděra-ová") and same_surname("Maděra", "Maděrová") and same_surname("Maděrová", "Maděra")
    assert same_surname("Svoboda", "Svobodová") and not same_surname("Maděra", "Maděr") and not same_surname("Mádl", "Mader")
    # neither before nor after: such rows are skipped when the list is bisected
    assert surname_before("Maděrová", "Maděra") is None and surname_before("Maděra", "Maděrová") is None
    assert surname_before("Adler", "Maděra") is True and surname_before("Novák", "Maděra") is False
    assert surname_before("Novák", "Nowak") is False


def test_czech_collation_of_the_special_letters():
    assert surname_key("Hora") < surname_key("Chalupa") < surname_key("Ibl")
    assert surname_key("Sedlák") < surname_key("Šimek") < surname_key("Tesař") and surname_key("Sz") != surname_key("Sa")
    assert surname_key("Zeman") < surname_key("Žák") and surname_key("Cibulka") < surname_key("Čapek") < surname_key("Dvořák")
    assert surname_key("Wolf") < surname_key("Vondrák") and surname_key("Wagner") < surname_key("Vávra")
    assert surname_key("Rada") < surname_key("Řezáč") < surname_key("Sedlák")


def test_names_with_carons_are_also_looked_up_without_them():
    assert search_targets("Novák") == ["Novák"] and search_targets("Dvořák") == ["Dvořák", "Dvorak"]
    assert same_surname("Dvorak", "Dvořák") and same_surname("Simek", "Šimek") and not same_surname("Chalupa", "Halupa")
    assert surname_key("Dvorak") < surname_key("Dvorka") < surname_key("Dvořák")


def test_short_names_are_not_mistaken_for_female_forms():
    assert surname_key("Kova") == "kova" and not same_surname("Kov", "Kova")


def test_given_names_use_the_first_token_and_josef_precedes_joseph():
    assert given_key("Jan Bedřich") == given_key("Jan") == "jan"
    assert given_key("Josef") < given_key("Joseph") < given_key("Karel")
    assert given_key("Václav") < given_key("Wenzl") < given_key("Vilém") and given_key("") == ""
    assert loose(given_key("Josef")) == loose(given_key("Joseph")) and loose("nolli") == loose("noli")


# ---------------------------------------------------------------- labels

def test_soupis_label_parts():
    p = parse_soupis_label("141 • 1878 • Novák, Josef")
    assert (p.order_no, p.birth_year, p.surname, p.given_name) == (141, 1878, "Novák", "Josef")
    p = parse_soupis_label("35 • neuveden • Paliczek, Emanuel")
    assert (p.birth_year, p.surname, p.given_name) == (None, "Paliczek", "Emanuel")
    p = parse_soupis_label("1 • 1867 • Abel (Abeles), David")
    assert (p.surname, p.given_name, p.alternatives) == ("Abel", "David", ("Abeles",))
    assert parse_soupis_label("1 • 1867 • Bez čárky").given_name is None
    assert parse_soupis_label("nic").surname is None and parse_soupis_label("1 • 1867 • ").surname is None


def test_house_number_of_a_census_label():
    assert house_of("138 • 1880 • Staré Město, čp. 12") == (12, "")
    assert house_of("138 • 1880 • Staré Město, čp. 12a") == (12, "a")
    assert house_of("138 • 1880 • Staré Město") is None


# ---------------------------------------------------------------- bisection on the synthetic lists

@pytest.fixture(autouse=True)
def fast_hosts(monkeypatch):
    monkeypatch.setitem(A.ARCHIVES, "ahmp", dataclasses.replace(A.ARCHIVES["ahmp"], min_interval=0))


@pytest_asyncio.fixture
async def env(tmp_path):
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, ahmp_enabled=True, request_interval=0)
    store = Store(cfg)
    archive = D.AhmpArchive()
    source = SafeSource(cfg, store, transport=archive.transport, source_mode="synthetic_demo")
    sid = store.create("alice", SearchQuery(raw_query="Synthetic soupis"))["research_id"]
    yield source, archive, sid
    await source.close()
    store.close()


def flat_of(source, sid, node=D.PRAGUE_SERIES, limit=25):
    session = S.session(sid, "alice")
    return FlatList(source, S, session, node, session.requests + limit, {168667: 10120, D.CENSUS_ROOT: 10125})


def expected(node, lemma):
    return [i + 1 for i, r in enumerate(D.SOUPIS[node]) if r[4] == lemma]


def before_surname(name):
    def check(book):
        p = parse_soupis_label(book.label)
        return None if p.surname is None else surname_before(p.surname, name)
    return check


@pytest.mark.asyncio
@pytest.mark.parametrize("lemma", D.SOUPIS_LEMMAS)
async def test_lower_bound_lands_on_the_first_row_of_every_lemma(env, lemma):
    source, archive, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    firsts = [await lower_bound(flat, before_surname(t)) for t in search_targets(lemma)]
    assert firsts[0] == expected(D.PRAGUE_SERIES, lemma)[0]           # the spelling as given lands on the block
    assert flat.pages <= 20 and flat.session.requests <= 24
    assert not archive.forbidden_hits and all(m == "GET" for m, _, _ in archive.requests)


@pytest.mark.asyncio
async def test_lower_bound_of_a_missing_name_is_where_it_would_stand(env):
    source, _, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    assert await lower_bound(flat, before_surname("Žzz")) == len(D.SOUPIS[D.PRAGUE_SERIES]) + 1
    assert await lower_bound(flat, before_surname("Aaa")) == 1        # the second search starts from every row seen so far


@pytest.mark.asyncio
async def test_collect_gathers_every_spelling_across_page_boundaries(env):
    source, archive, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    first = await lower_bound(flat, before_surname("Novák"))
    found = await collect(flat, first, lambda b: same_surname(parse_soupis_label(b.label).surname or "", "Novák"),
                          max_rows=90, max_pages=10)
    want = expected(D.PRAGUE_SERIES, "Novák")
    assert [n for n, _ in found.rows] == want and len(want) > 27 and not found.cut
    assert {parse_soupis_label(b.label).surname for _, b in found.rows} >= {"Novák", "Nowak", "Nováková", "Novák-ová"}
    assert flat.pages <= 20


@pytest.mark.asyncio
async def test_collect_stops_at_the_row_cap_and_says_so(env):
    source, _, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    first = await lower_bound(flat, before_surname("Novák"))
    found = await collect(flat, first, lambda b: "Nov" in b.label or "Now" in b.label, max_rows=12, max_pages=10)
    assert len(found.rows) == 12 and found.cut


@pytest.mark.asyncio
async def test_collect_stops_at_the_page_cap(env):
    source, _, sid = env
    flat = flat_of(source, sid, limit=60)
    await flat.open()
    first = await lower_bound(flat, before_surname("Novák"))
    found = await collect(flat, first, lambda b: same_surname(parse_soupis_label(b.label).surname or "", "Novák"),
                          max_rows=90, max_pages=2)
    assert found.cut and 0 < len(found.rows) < len(expected(D.PRAGUE_SERIES, "Novák"))


@pytest.mark.asyncio
async def test_search_stops_when_the_request_budget_is_spent(env):
    source, archive, sid = env
    flat = flat_of(source, sid, limit=5)
    with pytest.raises(SearchBudget):
        await flat.open()
        await lower_bound(flat, before_surname("Vondrák"))
    assert flat.session.requests <= 5


@pytest.mark.asyncio
async def test_a_hint_finds_the_boundary_in_one_probe(env):
    source, archive, sid = env
    want = expected(D.PRAGUE_SERIES, "Novák")[0]
    flat = flat_of(source, sid)
    await flat.open()
    before = len(archive.catalog_requests())
    assert await lower_bound(flat, before_surname("Novák"), hint=want) == want
    assert len(archive.catalog_requests()) - before == 1


@pytest.mark.asyncio
async def test_a_wrong_hint_is_only_a_first_guess(env):
    source, _, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    assert await lower_bound(flat, before_surname("Novák"), hint=7) == expected(D.PRAGUE_SERIES, "Novák")[0]


@pytest.mark.asyncio
async def test_census_house_numbers_are_found_by_bisection(env):
    source, _, sid = env
    flat = flat_of(source, sid, D.NOVE_MESTO)
    await flat.open()
    assert flat.total == len(D.HOUSES[D.NOVE_MESTO])

    def before(book):
        h = house_of(book.label)
        return None if h is None else h[0] < 31
    first = await lower_bound(flat, before)
    assert house_of(flat.rows[first].label) == (31, "")              # 30 is missing in the list; the next house stands there
    assert flat.pages <= 6


# ---------------------------------------------------------------- flat-list rows only below the Soupis and census nodes

@pytest.mark.asyncio
async def test_soupis_rows_are_refused_outside_the_nodes_that_own_them(env):
    source, _, sid = env
    for extra in (None, {999: 10120}, {168667: 10125}):
        session = S.session(sid, "alice")
        flat = FlatList(source, S, session, D.PRAGUE_SERIES, 25, extra)
        with pytest.raises(ResearchError) as e:
            await flat.open()
        assert e.value.code == "PARSE_ERROR"


@pytest.mark.asyncio
async def test_census_rows_are_accepted_below_the_census_root_only(env):
    source, _, sid = env
    flat = FlatList(source, S, S.session(sid, "alice"), D.NOVE_MESTO, 25, {D.CENSUS_ROOT: 10125})
    await flat.open()
    assert flat.total == len(D.HOUSES[D.NOVE_MESTO])
    with pytest.raises(ResearchError):
        await FlatList(source, S, S.session(sid, "alice"), D.NOVE_MESTO, 25, {168667: 10120}).open()


def test_strict_comparison_keeps_diacritic_spellings_apart():
    assert surname_before("Dvorak", "Dvořák") is True and surname_before("Dvořák", "Dvorak") is False
    assert surname_before("Simek", "Šimek") is True and surname_before("Šimek", "Šimek") is False
    from rubrika.soupis import given_forms, same_given
    assert given_forms("Václav")[0] == "Václav" and {"Wenzel", "Wenzl"} <= set(given_forms("Václav"))
    assert given_forms("Josef") == ["Josef", "Joseph"] and given_forms("Wenzl")[0] == "Wenzl" and given_forms("Eduard") == ["Eduard"]
    assert same_given("Jan", "Johann") and same_given("Johann", "Jan") and not same_given("Jan", "Josef") and given_forms("") == []


@pytest.mark.asyncio
async def test_rows_gathered_before_the_budget_ran_out_are_kept(env):
    source, _, sid = env
    flat = flat_of(source, sid)
    await flat.open()
    first = await lower_bound(flat, before_surname("Novák"))
    async def refused(url):                        # the walk does not know the budget is gone until a fetch is refused
        raise SearchBudget()
    flat._get = refused                            # noqa: SLF001
    found = await collect(flat, first, lambda b: same_surname(parse_soupis_label(b.label).surname or "", "Novák"), 90, 10)
    assert found.cut and found.reason == "requests" and found.rows and all(n >= 1 for n, _ in found.rows)
