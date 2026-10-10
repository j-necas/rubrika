"""SOA Třeboň end to end against the synthetic DigiArchiv: find_place → list_sources → open_source → view_scan → zoom_scan."""
import httpx
import pytest
import pytest_asyncio
from conftest import call
from rubrika import demo_trebon as D
from rubrika.config import Settings
from rubrika.demo import DemoArchive
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.storage import Store

HOST = 'digi.ceskearchivy.cz'
BUDEJOVICE, NEPOMUK, KRUMLOV = 'trebon:place:910001', 'trebon:place:910002', 'trebon:place:910003'
BOOK = 'trebon:book:911001'
FULL_FUND = 'Sbírka matrik Jihočeského kraje'


@pytest.fixture
def clock(monkeypatch):
    """Virtual time: a sleep advances the clock, so the archive's pace is measured, not waited for."""
    now = [1_800_000_000.0]
    import rubrika.network as N
    monkeypatch.setattr('rubrika.storage.time.time', lambda: now[0])
    async def fake_sleep(s):
        now[0] += s
    monkeypatch.setattr(N.asyncio, 'sleep', fake_sleep)
    return now


def build(tmp_path, clock, **settings):
    settings = {'trebon_enabled': True, **settings}
    cfg = Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, request_interval=0, **settings)
    store = Store(cfg)
    archive = DemoArchive()
    times = []
    def handler(request):
        if request.url.host == HOST:
            times.append((clock[0], request.url.path))
        return archive.handle(request)
    source = SafeSource(cfg, store, transport=httpx.MockTransport(handler), source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    service.times, service.store_ = times, store
    return service, archive


@pytest_asyncio.fixture
async def tb(tmp_path, clock):
    service, archive = build(tmp_path, clock)
    yield service, archive
    await service.close()
    service.store_.close()


def hits(archive):
    return [f'{p}?{q}' if q else p for p, q in archive.trebon.log]


async def research(service, **query):
    out = await call(service, 'start_research', query={'raw_query': 'SYNTHETIC trebon test', 'place_hint': 'České Budějovice', **query})
    return out.data['research_id']


async def budejovice(service, **query):
    sid = await research(service, event_type='birth', year_from=1620, year_to=1625, **query)
    places = await call(service, 'find_place', research_id=sid, term='České Budějovice', kind='parish_registers', archives=['trebon'])
    return sid, places.data


async def opened_book(service, book=BOOK, place=BUDEJOVICE, **query):
    sid, _ = await budejovice(service, **query)
    await call(service, 'list_sources', research_id=sid, place_ids=[place], kind='parish_registers')
    out = await call(service, 'open_source', research_id=sid, source_id=book)
    return sid, out.data


@pytest.mark.asyncio
async def test_end_to_end_find_list_open_view_zoom(tb):
    service, archive = tb
    sid, places = await budejovice(service)
    assert places['searched'] == ['trebon'] and places['not_searched'] == [] and places['ambiguous']
    assert [p['source_id'] for p in places['places']] == [BUDEJOVICE, NEPOMUK]
    assert {p['archive'] for p in places['places']} == {'trebon'}
    assert places['places'][0]['label'] == 'České Budějovice (St. Nicholas), římskokatolická farnost'
    assert places['places'][0]['names'] == ['České Budějovice', 'St. Nicholas']
    roots = [f'/tree.php?menu=0&doctree={r}&lang=cs' for r in ('1nC', '1nc', '1ne', '1ng', '1np', '1nb', '1nh', '1ni')]
    assert hits(archive) == ['/robots.txt', '/tree.php?menu=0&doctree=1nr&lang=cs', '/tree.php?menu=0&doctree=1nrc&lang=cs', *roots]
    assert any('Nepodařilo se přečíst větve matrik' in w and 'Evangelická církev' in w for w in places['warnings'])   # the demo has only three confession roots
    assert 'jen k prohlížení' in places['terms_notice'] and 'digi.ceskearchivy.cz' in places['terms_notice']
    assert any('začátku názvu' in w and 'nepoužívá' in w for w in places['warnings'])

    listed = await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')
    assert listed.data['archive'] == 'trebon' and listed.data['listing_complete'] is True
    assert 'jen k prohlížení' in listed.data['terms_notice']
    rows = listed.data['sources']
    assert rows[0]['source_id'] == BOOK and rows[0]['relevance']['status'] == 'candidate'
    assert rows[0]['archive'] == 'trebon' and rows[0]['kind'] == 'parish_registers' and rows[0]['parish'] == 'České Budějovice (sv. Mikuláš)'
    assert rows[0]['number'] == '1' and rows[0]['register_label'] == 'register B'
    assert rows[0]['detail_url'] == 'https://digi.ceskearchivy.cz/DA?menu=3&id=911001' and rows[0]['image_count'] is None
    assert rows[0]['coverage']['birth']['records']['spans'] == [{'start': 1615, 'end': 1630}]
    assert rows[0]['localities'] == ['České Budějovice', 'České Vrbné', 'Čtyři Dvory']
    status = {r['source_id']: r['relevance']['status'] for r in rows}
    assert status == {'trebon:book:911001': 'candidate', 'trebon:book:911002': 'excluded', 'trebon:book:911003': 'needs_review',
                      'trebon:book:911004': 'needs_review', 'trebon:book:911005': 'excluded', 'trebon:book:911006': 'excluded',
                      'trebon:book:911007': 'excluded', 'trebon:book:911008': 'excluded', 'trebon:book:911009': 'excluded'}
    assert '6 (excluded)' in listed.data['note'] and 'následují až po candidate a needs_review' in listed.data['note']  # a marriage or death book is never excluded on a birth query
    assert listed.data['parish']['district'] == 'České Budějovice' and 'Vidov' in listed.data['parish']['parish_district']
    assert any('1 knih farnosti archiv ještě nezveřejnil' in w and '2033' in w for w in listed.data['warnings'])
    assert hits(archive)[-1] == '/tree.php?menu=0&doctree=1nrcl&id=910001&lang=cs'

    opened = await call(service, 'open_source', research_id=sid, source_id=BOOK)
    data = opened.data
    assert data['archive'] == 'trebon' and data['kind'] == 'parish_registers' and data['total'] == 3
    assert data['citation'] == ('Státní oblastní archiv v Třeboni, ' + FULL_FUND +
                                ', České Budějovice (sv. Mikuláš), kniha č. 1 [register B] (DigiArchiv, id 911001)')
    assert data['unit']['image_count'] == 3 and 'jen k prohlížení' in data['terms_notice']
    page = data['pages'][0]
    assert page['page_id'] == 'trebon:book:911001:1' and page['source_type'] == 'manual' and page['archive'] == 'trebon'
    assert page['viewer_url'] == 'https://digi.ceskearchivy.cz/view.php?menu=3&id=911001&page=1' and page['citation'] == data['citation']
    assert hits(archive)[-1] == '/view.php?menu=3&id=911001&page=1'

    assert data['pages'][0]['source_type'] == 'manual' and data['pages'][0]['viewer_link_verified'] is False
    assert data['pages'][2]['viewer_url'] == 'https://digi.ceskearchivy.cz/view.php?menu=3&id=911001&page=3'
    assert 'Omezený režim' in data['note'] and 'import_scan' in data['note']
    assert len([h for h in hits(archive) if h.startswith('/view.php')]) == 1, 'one viewer request per book, none per scan'
    assert not any(h.startswith('/cgi-bin') for h in hits(archive))


@pytest.mark.asyncio
async def test_pace_between_requests_is_at_least_two_seconds(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    starts = [t for t, _ in service.times]
    assert len(starts) >= 5
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 2.0 - 1e-6


@pytest.mark.asyncio
async def test_the_search_help_and_oai_are_never_requested(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    paths = [p for p, _ in archive.trebon.log]
    assert paths.count('/robots.txt') == 1
    assert not any(p.startswith(('/pages/', '/oai/', '/help/')) for p in paths)
    assert {p for p in paths} <= {'/robots.txt', '/tree.php', '/view.php'}


@pytest.mark.asyncio
async def test_terms_notice_is_listed_next_to_the_archive(tb):
    service, _ = tb
    listing = await call(service, 'list_archives')
    row = next(a for a in listing.data['archives'] if a['code'] == 'trebon')
    assert row['available_kinds'] == ['parish_registers'] and not row['link_only']
    assert 'jen k prohlížení' in row['terms_notice'] and 'nestahuje' in row['terms_notice'] and 'import_scan' in row['terms_notice'] and row['robots_mode'] == 'honor'
    assert all(a['terms_notice'] is None for a in listing.data['archives'] if a['code'] not in ('trebon', 'pf', 'na_prihlasky'))


@pytest.mark.asyncio
async def test_disabled_archive_is_not_searched_and_makes_no_request(tmp_path, clock):
    service, archive = build(tmp_path, clock, trebon_enabled=False)
    try:
        sid = await research(service)
        out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
        assert out.data['searched'] == [] and out.data['places'] == []
        assert out.data['not_searched'][0]['archive'] == 'trebon' and 'vypnutý' in out.data['not_searched'][0]['reason']
        by_district = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', district='Tábor')
        assert by_district.data['searched'] == [] and by_district.data['not_searched'][0]['archive'] == 'trebon'
        assert archive.trebon.log == []
        res = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers'))
        assert res.is_error and res.data['error']['code'] == 'LIVE_DISABLED'
        listing = await call(service, 'list_archives')
        assert next(a for a in listing.data['archives'] if a['code'] == 'trebon')['available_kinds'] == []
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_view_scan_after_disable_is_live_disabled(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    service.cfg = service.cfg.model_copy(update={'trebon_enabled': False})
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
    assert out.is_error and out.data['error']['code'] == 'MANUAL_SOURCE'      # answered before anything else, without a request


@pytest.mark.asyncio
async def test_districts_of_south_bohemia_route_to_trebon_and_it_is_not_searched_unasked(tb):
    from rubrika.territory import archives_for_district
    for district in ('České Budějovice', 'Český Krumlov', 'Jindřichův Hradec', 'Písek', 'Prachatice', 'Strakonice', 'Tábor'):
        assert archives_for_district(district) == ('trebon',)
    assert archives_for_district('Pelhřimov') == ('trebon', 'mza') and archives_for_district('Jihlava') == ('mza', 'trebon')
    service, archive = tb
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', district='Tábor')
    assert out.data['searched'] == ['trebon'] and [p['source_id'] for p in out.data['places']] == ['trebon:place:920001']
    assert out.data['places'][0]['archive'] == 'trebon'
    plain = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers')
    assert 'trebon' not in plain.data['searched'] and any('SOA Třeboň' in w and 'podmínkám' in w for w in plain.data['warnings'])
    assert 'terms_notice' not in plain.data or 'digi.ceskearchivy' not in plain.data['terms_notice']


@pytest.mark.asyncio
async def test_letters_are_filed_by_the_first_letter_and_ch_has_its_own(tb):
    service, archive = tb
    sid = await research(service)
    ch = await call(service, 'find_place', research_id=sid, term='Chotoviny', kind='parish_registers', archives=['trebon'])
    assert [p['source_id'] for p in ch.data['places']] == ['trebon:place:940001']
    assert '/tree.php?menu=0&doctree=1nri&lang=cs' in hits(archive)
    none = await call(service, 'find_place', research_id=sid, term='Kaplice', kind='parish_registers', archives=['trebon'])
    assert none.data['places'] == [] and any('Nejde o důkaz' in w for w in none.data['warnings'])
    zd = await call(service, 'find_place', research_id=sid, term='Žďár', kind='parish_registers', archives=['trebon'])
    assert zd.data['places'] == [] and any('nemá písmeno' in w for w in zd.data['warnings'])


@pytest.mark.asyncio
async def test_letter_pages_are_remembered(tb):
    service, archive = tb
    sid, _ = await budejovice(service)
    before = len(hits(archive))
    await call(service, 'find_place', research_id=sid, term='Český Krumlov', kind='parish_registers', archives=['trebon'])
    assert len(hits(archive)) == before, 'index and the letter page come from the cache'


async def hinted(service, hint, first, last):
    sid = await research(service, event_type='birth', year_from=first, year_to=last)
    await call(service, 'revise_research', research_id=sid, expected_revision=1,
               query={'raw_query': 'SYNTHETIC', 'place_hint': hint, 'event_type': 'birth', 'year_from': first, 'year_to': last})
    await call(service, 'find_place', research_id=sid, term='České Budějovice', kind='parish_registers', archives=['trebon'])
    return await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')


@pytest.mark.asyncio
async def test_place_hint_keeps_the_books_kept_for_that_place_as_candidates(tb):
    service, archive = tb
    listed = await hinted(service, 'Vidov', 1800, 1810)
    rows = {r['source_id']: r for r in listed.data['sources']}
    assert rows['trebon:book:911005']['relevance']['status'] == 'candidate'                    # kept for Vidov, births 1785-1920
    assert rows['trebon:book:911006']['relevance']['status'] == 'candidate' and rows['trebon:book:911006']['relevance']['via'] == 'index'
    assert rows['trebon:book:911001']['relevance']['status'] == 'excluded'                     # births 1615-1630
    assert not any('Místo ze zadání' in w for w in listed.data['warnings'])


@pytest.mark.asyncio
async def test_a_book_kept_for_other_places_is_only_needs_review(tb):
    service, archive = tb
    listed = await hinted(service, 'Mokré', 1620, 1625)
    first = next(r for r in listed.data['sources'] if r['source_id'] == BOOK)
    assert first['relevance']['status'] == 'needs_review' and 'jiné lokality' in first['relevance']['reason']
    assert not any('Místo ze zadání' in w for w in listed.data['warnings']), 'a book is kept for Mokré'


@pytest.mark.asyncio
async def test_a_place_no_book_is_kept_for_is_said_where_it_is_found(tb):
    service, archive = tb
    other = await hinted(service, 'Adamov', 1620, 1625)
    assert any('Adamov' in w and 'dalšími lokalitami' in w for w in other.data['warnings'])
    assert next(r for r in other.data['sources'] if r['source_id'] == BOOK)['relevance']['status'] == 'needs_review'
    unknown = await hinted(service, 'Neznámá Lhota', 1620, 1625)
    assert any('Neznámá Lhota' in w and 'není mezi lokalitami farnosti' in w for w in unknown.data['warnings'])


@pytest.mark.asyncio
async def test_no_place_hint_leaves_relevance_to_the_years(tb):
    service, archive = tb
    sid = await research(service, event_type='birth', year_from=1620, year_to=1625)
    await call(service, 'revise_research', research_id=sid, expected_revision=1,
               query={'raw_query': 'SYNTHETIC', 'event_type': 'birth', 'year_from': 1620, 'year_to': 1625})
    await call(service, 'find_place', research_id=sid, term='České Budějovice', kind='parish_registers', archives=['trebon'])
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')
    assert next(r for r in listed.data['sources'] if r['source_id'] == BOOK)['relevance']['status'] == 'candidate'


@pytest.mark.asyncio
async def test_changed_letter_page_is_not_searched_with_parse_error(tb, monkeypatch):
    service, archive = tb
    monkeypatch.setattr(D, 'letter_page', lambda code: D.wrap('<div>Please wait</div>'))
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
    assert out.data['places'] == [] and out.data['searched'] == [] and 'PARSE_ERROR' in out.data['not_searched'][0]['reason']


@pytest.mark.asyncio
async def test_changed_parish_page_is_a_parse_error_not_an_empty_listing(tb, monkeypatch):
    service, archive = tb
    sid, _ = await budejovice(service)
    monkeypatch.setattr(D, 'parish_page', lambda pid: D.wrap('<div class="nadpis">x</div>'))
    out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers'))
    assert out.is_error and out.data['error']['code'] == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_a_book_not_online_yet_and_unknown_books_cannot_be_opened(tb):
    service, archive = tb
    sid, _ = await budejovice(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')
    for book in ('trebon:book:911099', 'trebon:book:999999'):
        out = await service.call('open_source', 'alice', dict(research_id=sid, source_id=book))
        assert out.is_error and out.data['error']['code'] == 'NOT_FOUND'
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id='trebon:book:x'))
    assert out.is_error and out.data['error']['code'] == 'INVALID_ARGUMENT'
    assert not any(h.startswith('/view.php') for h in hits(archive))


@pytest.mark.asyncio
async def test_place_ids_must_come_from_find_place(tb):
    service, archive = tb
    sid = await research(service)
    for place, code in ((BUDEJOVICE, 'NOT_FOUND'), ('trebon:place:abc', 'INVALID_ARGUMENT')):
        out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[place], kind='parish_registers'))
        assert out.is_error and out.data['error']['code'] == code
    _, _ = await budejovice(service)
    for extra in ({'place_ids': [BUDEJOVICE, NEPOMUK]}, {'year_from': 1600}, {'fund_marks': ['C 1']}, {'district_archive': 'Tábor'}):
        args = {'research_id': sid, 'place_ids': [BUDEJOVICE], 'kind': 'parish_registers', **extra}
        out = await service.call('list_sources', 'alice', args)
        assert out.is_error and out.data['error']['code'] in ('INVALID_ARGUMENT', 'NOT_FOUND')
    assert not any(h.startswith('/tree.php?menu=0&doctree=1nrcl') for h in hits(archive))
    for kind in ('land_records', 'census'):
        out = await service.call('list_sources', 'alice', dict(research_id=sid, place_ids=[BUDEJOVICE], kind=kind))
        assert out.is_error


@pytest.mark.asyncio
async def test_a_book_from_a_pasted_link_can_be_opened(tb):
    service, archive = tb
    sid = await research(service, book_url='https://digi.ceskearchivy.cz/911001/1')
    out = await call(service, 'open_source', research_id=sid, source_id=BOOK)
    assert out.data['total'] == 3 and out.data['unit']['number'] == '' and out.data['unit']['parish'] == ''
    other = await service.call('open_source', 'alice', dict(research_id=sid, source_id='trebon:book:911002'))
    assert other.is_error and other.data['error']['code'] == 'NOT_FOUND'
    bad = await service.call('start_research', 'alice', dict(query={'raw_query': 'x', 'book_url': 'https://digi.ceskearchivy.cz/pages/search.php'}))
    assert bad.is_error


@pytest.mark.asyncio
async def test_other_archives_do_not_touch_this_one_and_are_not_touched_by_it(tb):
    service, archive = tb
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Demo Lhota', kind='parish_registers', archives=['mza'])
    assert out.data['searched'] == ['mza'] and archive.trebon.log == [] and 'terms_notice' not in out.data
    listing = await call(service, 'list_archives')
    assert 'parish_registers' in {a['code']: a['available_kinds'] for a in listing.data['archives']}['mza']


@pytest.mark.asyncio
async def test_robots_txt_of_the_archive_is_honoured(tmp_path, clock):
    service, archive = build(tmp_path, clock)
    try:
        archive.trebon.robots = 'User-agent: *\nDisallow: /tree.php\n'
        sid = await research(service)
        out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
        assert out.data['places'] == [] and 'SOURCE_ACCESS_DENIED' in out.data['not_searched'][0]['reason']
        assert [p for p, _ in archive.trebon.log] == ['/robots.txt']
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_a_refusal_pauses_the_group_and_a_bot_wall_is_not_bypassed(tb, monkeypatch):
    service, archive = tb
    original = archive.trebon.handle
    monkeypatch.setattr(archive.trebon, 'handle', lambda request: httpx.Response(200, headers={'content-type': 'text/html'}, text='<html><title>Please wait</title>Verifying your browser before access</html>') if request.url.path == '/tree.php' else original(request))
    sid = await research(service)
    out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
    assert out.data['places'] == [] and 'SOURCE_ACCESS_DENIED' in out.data['not_searched'][0]['reason']
    before = len(archive.trebon.log)
    again = await call(service, 'find_place', research_id=sid, term='Písek', kind='parish_registers', archives=['trebon'])
    assert again.data['places'] == [] and len(archive.trebon.log) == before, 'the host is paused: no further request'


@pytest.mark.asyncio
async def test_the_daily_request_cap_of_the_archive_applies(tmp_path, clock):
    service, archive = build(tmp_path, clock, trebon_global_daily_requests=2)
    try:
        sid = await research(service)
        out = await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
        assert out.data['searched'] == [] and 'BUDGET_EXCEEDED' in out.data['not_searched'][0]['reason']
        assert len(archive.trebon.log) == 2
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_view_scan_and_zoom_scan_make_no_request_and_point_to_the_browser_and_import_scan(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    before = len(archive.requests)
    for page in data['pages']:
        out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=page['page_id']))
        err = out.data['error']
        assert out.is_error and err['code'] == 'MANUAL_SOURCE'
        assert 'jen prohlížet v DigiArchivu' in err['message'] and 'import_scan' in err['message'] and 'Stažených souborů' in err['message']
        assert err['details']['viewer_url'] == page['viewer_url']
        assert err['details']['citation'] == f"{data['citation']}, snímek {page['scan_number']}"
    zoom = await service.call('zoom_scan', 'alice', dict(research_id=sid, image_id='image_x', bbox_px={'x': 0, 'y': 0, 'width': 10, 'height': 10}))
    assert zoom.is_error
    assert len(archive.requests) == before, 'not a single request, and none to the image CGI'
    usage = (await call(service, 'get_research_status', research_id=sid)).data['usage']
    assert usage['archive_images'] == 0


@pytest.mark.asyncio
async def test_opened_book_is_summarised_and_named_in_a_gedcom_source(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    status = await call(service, 'get_research_status', research_id=sid)
    opened = status.data['coverage']['opened_books']
    assert opened == [{'book_id': BOOK, 'source': 'trebon', 'archive': 'trebon', 'number': '1',
                       'title': 'České Budějovice (sv. Mikuláš), kniha 1', 'detail_url': 'https://digi.ceskearchivy.cz/DA?menu=3&id=911001'}]
    not_opened = {b['book_id']: b for b in status.data['coverage']['catalog_books_not_opened']}
    assert 'trebon:book:911002' in not_opened and not_opened['trebon:book:911002']['archive'] == 'trebon'
    tree_source = ResearchService._tree_source(service.store.links(sid, 'alice', 'opened_book')[0])
    assert tree_source['title'] == 'SOA Třeboň, České Budějovice (sv. Mikuláš), kniha 1' and tree_source['archive'] == 'Státní oblastní archiv v Třeboni'


@pytest.mark.asyncio
async def test_a_changed_viewer_page_makes_open_source_a_parse_error(tb, monkeypatch):
    service, archive = tb
    sid, _ = await budejovice(service)
    await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')
    monkeypatch.setattr(D, 'viewer_page', lambda book, n: D.wrap('<div>Please wait</div>'))
    out = await service.call('open_source', 'alice', dict(research_id=sid, source_id=BOOK))
    assert out.is_error and out.data['error']['code'] == 'PARSE_ERROR'


@pytest.mark.asyncio
async def test_manual_source_comes_before_the_checkpoint_and_never_blocks_other_images(tb):
    service, archive = tb
    sid, data = await opened_book(service)
    with service.store.tx() as db: service.store._count(db, sid, 'images', service.store.cfg.checkpoint_images)
    out = await service.call('view_scan', 'alice', dict(research_id=sid, page_id=data['pages'][0]['page_id']))
    assert out.data['error']['code'] == 'MANUAL_SOURCE'
    # a zoom of an image that does not come from a trebon page is not a manual-source case
    zoom = await service.call('zoom_scan', 'alice', dict(research_id=sid, image_id='image_none', bbox_px={'x': 0, 'y': 0, 'width': 5, 'height': 5}))
    assert zoom.data['error']['code'] != 'MANUAL_SOURCE'
    missing = await service.call('view_scan', 'alice', dict(research_id=sid, page_id='trebon:book:911001:99'))
    assert missing.data['error']['code'] == 'NOT_FOUND'


@pytest.mark.asyncio
async def test_a_manual_page_never_reaches_the_image_service(tb):
    from rubrika.errors import ResearchError
    from rubrika.models import Page
    service, archive = tb
    sid, data = await opened_book(service)
    page = Page.model_validate(service.store.entity('page', data['pages'][0]['page_id']))
    with pytest.raises(ResearchError) as e:
        await service.images.descriptor(sid, 'alice', page)
    assert e.value.code == 'MANUAL_SOURCE' and e.value.details['viewer_url'] == page.viewer_url


@pytest.mark.asyncio
async def test_a_book_from_a_link_gets_a_minimal_citation_and_a_warning(tb):
    service, archive = tb
    sid = await research(service, book_url='https://digi.ceskearchivy.cz/911001/1')
    out = await call(service, 'open_source', research_id=sid, source_id=BOOK)
    assert out.data['citation'] == 'Státní oblastní archiv v Třeboni, ' + FULL_FUND + ', DigiArchiv, id 911001'
    assert 'neověřeno' not in str(out.data)
    assert 'neověřeno' not in out.data['citation'] and 'neověřeno' not in out.data['pages'][0]['citation']
    assert any('otevřena podle odkazu' in w for w in out.data['warnings'])


@pytest.mark.asyncio
async def test_catalogue_requests_ask_for_czech_and_other_archives_do_not(tmp_path, clock):
    service, archive = build(tmp_path, clock)
    seen = []
    orig = archive.handle
    def spy(request):
        seen.append((request.url.host, request.headers.get('accept-language')))
        return orig(request)
    service.source.client._transport = httpx.MockTransport(spy)
    try:
        sid = await research(service)
        await call(service, 'find_place', research_id=sid, term='Tábor', kind='parish_registers', archives=['trebon'])
        await call(service, 'find_place', research_id=sid, term='Demo Lhota', kind='parish_registers', archives=['mza'])
        assert {h for host, h in seen if host == HOST} == {'cs-CZ,cs;q=0.9'}
        assert {h for host, h in seen if host != HOST} == {None}
    finally:
        await service.close()
        service.store_.close()


@pytest.mark.asyncio
async def test_several_parishes_of_a_town_are_to_be_checked_all(tb):
    service, archive = tb
    _, places = await budejovice(service)
    assert any('víc farností' in w and 'zkontrolujte všechny' in w for w in places['warnings'])


@pytest.mark.asyncio
async def test_a_book_opened_from_a_link_is_summarised_without_placeholders(tb):
    service, archive = tb
    sid = await research(service, book_url='https://digi.ceskearchivy.cz/911001/1')
    await call(service, 'open_source', research_id=sid, source_id=BOOK)
    status = await call(service, 'get_research_status', research_id=sid)
    assert status.data['coverage']['opened_books'][0]['title'] == 'DigiArchiv, id 911001' and 'neověřeno' not in str(status.data)
    source = ResearchService._tree_source(service.store.links(sid, 'alice', 'opened_book')[0])
    assert source['title'] == 'SOA Třeboň, DigiArchiv, id 911001'


@pytest.mark.asyncio
async def test_listing_note_names_the_czech_letters(tb):
    service, _ = tb
    sid, _ = await budejovice(service)
    listed = await call(service, 'list_sources', research_id=sid, place_ids=[BUDEJOVICE], kind='parish_registers')
    assert 'N narození, O oddaní, Z zemřelí' in listed.data['note'] and 'B/M/D' in listed.data['note']
