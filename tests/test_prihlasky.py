"""Parsers and relevance of the police registration forms of Prague, on synthetic pages (rubrika.demo_prihlasky)."""
import pytest

from rubrika import demo_prihlasky as D
from rubrika import prihlasky as P
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery

TOKEN = D.token_for(1)
OTHER = D.token_for(2)


def code(fn, *args):
    with pytest.raises(ResearchError) as e:
        fn(*args)
    return e.value


def test_entry_page_gives_the_session_token():
    assert P.parse_entry(D.entry_html(TOKEN)) == TOKEN
    assert code(P.parse_entry, "<html>bez formuláře</html>").code == "PARSE_ERROR"
    two = D.entry_html(TOKEN).replace(f"?session={TOKEN}", f"?session={OTHER}", 1)
    assert code(P.parse_entry, two).code == "PARSE_ERROR"          # two different tokens: not a page we understand


def test_results_page():
    rows = [(1, "Vzorný", "Josef", 1878), (2, "Vzorná", "Marie", None), (3, "Ukázková", "Marie", 1880)]
    page = P.parse_results(D.results_html(TOKEN, 7, rows, 0), TOKEN)
    assert page.total == 7 and page.next_offset == 3
    assert [(r.record, r.surname, r.given_name, r.birth_year) for r in page.rows] == [
        (1, "Vzorný", "Josef", 1878), (2, "Vzorná", "Marie", None), (3, "Ukázková", "Marie", 1880)]
    last = P.parse_results(D.results_html(TOKEN, 7, [(7, "Vzorný", "Karel", 1905)], 6), TOKEN)
    assert last.next_offset is None and [r.record for r in last.rows] == [7]


def test_not_found_is_an_empty_result_not_an_error():
    page = P.parse_results(D.empty_html(TOKEN), TOKEN)
    assert page.total == 0 and page.rows == [] and page.next_offset is None


def test_a_page_under_another_session_is_a_lost_session():
    err = code(P.parse_results, D.results_html(OTHER, 7, [(1, "Vzorný", "Josef", 1878)], 0), TOKEN)
    assert err.code == "SOURCE_UNAVAILABLE" and err.details["reason"] == "session_expired"
    assert code(P.parse_detail, D.detail_html(OTHER, 901, 11, 7, 1), TOKEN).details["reason"] == "session_expired"


@pytest.mark.parametrize("broken", [
    lambda h: h.replace("table_seznam", "x"),
    lambda h: h.replace("pocet_zaznamu", "x"),
    lambda h: h.replace("zaznamy", "x"),
    lambda h: h.replace("Počet nalezených záznamů:7", "Počet: sedm"),
    lambda h: h.replace("&amp;record=1", "&amp;rec=1"),
    lambda h: h.replace('class="table_rok">1878', 'class="table_rok">18x8'),
])
def test_a_changed_results_page_is_a_parse_error(broken):
    html = D.results_html(TOKEN, 7, [(1, "Vzorný", "Josef", 1878)], 0)
    assert code(P.parse_results, broken(html), TOKEN).code == "PARSE_ERROR"


def test_results_page_with_a_total_but_no_rows_is_a_parse_error():
    assert code(P.parse_results, D.results_html(TOKEN, 7, [], 0), TOKEN).code == "PARSE_ERROR"


def test_detail_page_household_citation_and_permalink():
    d = P.parse_detail(D.detail_html(TOKEN, 901, 11, 5, 2), TOKEN)
    assert (d.karton, d.folium, d.image) == (901, 11, "images/00000901-00000011.jpg")
    assert d.citation == "Národní archiv, Policejní ředitelství I, konskripce, karton 901, obraz 11"
    assert d.permalink == "http://digi.nacr.cz/prihlasky2/index.php?action=link&ref=czarch:CZ-100000010:874&karton=901&folium=11"
    head, wife, son = d.members
    assert (head.surname, head.given_name, head.birth_year, head.head, head.other_surnames) == ("Vzorný", "Josef", 1878, True, [])
    assert (wife.surname, wife.other_surnames, wife.given_name, wife.birth_year, wife.head) == ("Vzorná", ["Ukázková"], "Marie", 1880, False)
    assert (son.surname, son.given_name, son.birth_year) == ("Vzorný", "Karel", 1905)
    assert wife.raw == "Vzorná (Ukázková) Marie (1880)"


def test_permalink_page_needs_no_token():
    d = P.parse_detail(D.detail_html(OTHER, 901, 12, 0, None), None)
    assert (d.karton, d.folium) == (901, 12) and len(d.members) == 2


def test_household_member_without_a_year_or_with_two_surnames():
    m = P.parse_member("Hromadný Jan ()")
    assert (m.surname, m.given_name, m.birth_year) == ("Hromadný", "Jan", None)
    m = P.parse_member("Novák (Šletina, Drtinová) Barbora Marie (1876)")
    assert (m.surname, m.other_surnames, m.given_name, m.birth_year) == ("Novák", ["Šletina", "Drtinová"], "Barbora Marie", 1876)
    m = P.parse_member("Kovář Franz (1899?)")
    assert m.birth_year is None and m.given_name == "Franz" and m.raw == "Kovář Franz (1899?)"
    assert P.parse_member("Jednoslovo").given_name is None


@pytest.mark.parametrize("broken", [
    lambda h: h.replace("seznam_lidi", "x"),
    lambda h: h.replace('id="obrazek_prihlasky"', 'id="x"'),
    lambda h: h.replace("images/00000901-00000011.jpg", "images/00000901-00000012.jpg", 1),          # image and citation disagree
    lambda h: h.replace("karton 901, obraz 11", "karton 901, obraz 12"),
    lambda h: h.replace("folium=11", "folium=12"),
    lambda h: h.replace(D.REF, "czarch:CZ-999:1"),
    lambda h: h.replace('<div class="citace">', '<div class="x">'),
    lambda h: h.replace("images/00000901-00000011.jpg", "images/x.jpg"),
    lambda h: h.replace('<div class="seznam_lidi">', '<div class="seznam_lidi"></div><div>'),
])
def test_a_changed_detail_page_is_a_parse_error(broken):
    assert code(P.parse_detail, broken(D.detail_html(TOKEN, 901, 11, 5, 2)), TOKEN).code == "PARSE_ERROR"


def query(**person):
    return SearchQuery(raw_query="SYNTHETIC", event_type="birth", year_from=1878, year_to=1878, person=person)


def form(members, karton=901, folium=11):
    d = P.parse_detail(D.detail_html(TOKEN, karton, folium, 1, 1, members), TOKEN)
    return P.form_of(d)


def test_relevance_uses_the_household_member_with_the_researched_surname():
    q = query(surname="Vzorný", given_name="Josef")
    good = form([("Vzorný", [], "Josef", 1878), ("Vzorná", ["Ukázková"], "Marie", 1880)])
    r = P.form_relevance(good, q)
    assert r["status"] == "candidate" and "1878" in r["reason"] and r["reason"].startswith("Přihláška souhlasí")
    near = P.form_relevance(form([("Vzorný", [], "Josef", 1882)]), q)
    assert near["status"] == "needs_review" and "±5" in near["reason"]
    assert P.form_relevance(form([("Vzorný", [], "Adolf", 1890)]), q)["status"] == "excluded"
    assert P.form_relevance(form([("Vzorný", [], "Joseph", 1879)]), q)["status"] == "candidate"        # Josef = Joseph, ±2
    assert P.form_relevance(form([("Vzorný", [], "Adolf", 1878)]), q)["status"] == "needs_review"    # year fits, given differs: never excluded


def test_a_woman_is_found_under_her_maiden_name():
    q = query(surname="Ukázková", given_name="Marie")
    wife = form([("Vzorný", [], "Josef", 1878), ("Vzorná", ["Ukázková"], "Marie", 1880)])
    assert P.form_relevance(wife, q)["status"] == "candidate"
    assert P.form_relevance(form([("Vzorný", [], "Josef", 1878)]), q)["status"] == "needs_review"      # no member with that surname


def test_without_a_given_name_or_year_only_review_is_possible():
    q = SearchQuery(raw_query="SYNTHETIC", person={"surname": "Vzorný"})
    r = P.form_relevance(form([("Vzorný", [], "Josef", 1878)]), q)
    assert r["status"] == "needs_review" and "chybí" in r["reason"]


def test_a_named_parent_in_the_household_is_noted_and_rescues_an_excluded_form():
    q = query(surname="Vzorný", given_name="Josef", father="Jan Vzorný", mother="Marie Ukázková")
    f = form([("Vzorný", [], "Adolf", 1890), ("Vzorný", [], "Jan", 1850), ("Vzorná", ["Ukázková"], "Marie", 1855)])
    r = P.form_relevance(f, q)
    assert r["status"] == "needs_review" and "otec" in r["reason"] and "matka" in r["reason"]
    assert P.form_relevance(f, query(surname="Vzorný", given_name="Josef"))["status"] == "excluded"
    ok = form([("Vzorný", [], "Josef", 1878), ("Vzorný", [], "Jan", 1850)])
    assert "otec" in P.form_relevance(ok, q)["reason"] and P.form_relevance(ok, q)["status"] == "candidate"


def test_ids():
    assert P.form_id(901, 11) == "na_prihlasky:form:901:11"
    assert P.parse_form_id("na_prihlasky:form:901:11") == (901, 11)
    for bad in ("na_prihlasky:form:901", "na_prihlasky:form:x:1", "ahmp:book:" + "A" * 32, "na_prihlasky:form:1:2:3", ""):
        assert code(P.parse_form_id, bad).code == "INVALID_ARGUMENT"
    assert P.PAGE_ID.fullmatch("na_prihlasky:form:901:11:1")
    assert P.image_url(901, 11) == "http://digi.nacr.cz/prihlasky2/images/00000901-00000011.jpg"
    assert P.permalink_url(901, 11).startswith("http://digi.nacr.cz/prihlasky2/index.php?action=link&ref=czarch:CZ-100000010:874&karton=901")


def test_a_page_without_any_session_link_is_a_lost_session():
    # a fresh session of the server whose page carries no token at all must not pass as ours
    fresh = D.results_html(TOKEN, 7, [(1, "Vzorný", "Josef", 1878)], 0).replace(f"session={TOKEN}", "x=1")
    assert code(P.parse_results, fresh, TOKEN).details["reason"] == "session_expired"
    assert code(P.parse_detail, D.detail_html(TOKEN, 901, 11, 5, 2).replace(f"session={TOKEN}", "x=1"), TOKEN).details["reason"] == "session_expired"
    assert code(P.parse_results, D.empty_html(TOKEN).replace(f"session={TOKEN}", "x=1"), TOKEN).details["reason"] == "session_expired"


def test_the_detail_must_agree_with_the_row_it_came_from():
    d = P.parse_detail(D.detail_html(TOKEN, 901, 11, 5, 2), TOKEN)
    row = P.ResultRow(1, "Vzorný", "Josef", 1878)
    assert [P.row_agrees(m, row) for m in d.members] == [True, False, False]
    assert not P.row_agrees(d.members[0], P.ResultRow(1, "Vzorný", "Josef", 1879))          # year
    assert not P.row_agrees(d.members[0], P.ResultRow(1, "Vzorný", "Jan", 1878))            # given name
    assert not P.row_agrees(d.members[0], P.ResultRow(1, "Novák", "Josef", 1878))            # surname
    assert P.row_agrees(d.members[0], P.ResultRow(1, "vzorny", "JOSEF", 1878))               # case and diacritics do not matter


def test_head_is_the_first_person_and_the_maiden_name_is_parsed():
    d = P.parse_detail(D.detail_html(TOKEN, 901, 11, 5, 2), TOKEN)
    assert [m.head for m in d.members] == [True, False, False] and [m.matched for m in d.members] == [False] * 3
    assert d.members[1].maiden_name == "Ukázková" and d.members[1].other_surnames == ["Ukázková"]
    assert P.parse_member("Novák (Šletina, Drtinová) Barbora (1876)").maiden_name == "Šletina, Drtinová"
