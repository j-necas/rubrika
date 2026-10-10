import json
import httpx
import pytest
from rubrika.catalog import (parse_periods,parse_books,parse_places,parse_pages,
                                 book_id_from_url,normalize,relevance)
from rubrika.demo import DemoArchive
from rubrika.errors import ResearchError
from rubrika.models import Book,SearchQuery,EventCoverage

URL='https://www.mza.cz/actapublica/matrika/hledani?typ=obec&obec_id=900001'

def fixture_html():
    return DemoArchive().handle(httpx.Request('GET',URL)).text

def with_pagination(html,links):
    # Inside the document like on the live page; libxml2 on Windows drops markup after </html>.
    return html.replace('</html>','<div class="pagination">'+links+'</div></html>')

@pytest.mark.parametrize('text,status,spans',[
    (None,'unknown',[]),('','unknown',[]),('-','absent',[]),('—','absent',[]),
    ('1840','known',[(1840,1840)]),('1840 – 1850','known',[(1840,1850)]),
    ('1840-1842; 1845-1848','known',[(1840,1842),(1845,1848)]),
    ('1840-','known',[(1840,None)]),('-1840','known',[(None,1840)]),
    ('1840-1850 s mezerami','uncertain',[(1840,1850)]),('1850-1840','uncertain',[]),
    ('pouze část obcí','uncertain',[]),
    # ARON VČA writes month-level bounds ("month.year"); the years alone decide overlap.
    ('5.1866-5.1903','known',[(1866,1903)]),('1835-4.1866','known',[(1835,1866)]),
    ('8.1741-1764','known',[(1741,1764)]),('4.1752-1.1785','known',[(1752,1785)]),
    ('12.1920-1933','known',[(1920,1933)]),('3.1.1890-31.12.1891','known',[(1890,1891)]),
    ('4.1866-5.1866 s mezerami','uncertain',[(1866,1866)]),('13.1866','uncertain',[]),
    ('5.1866-5.1903','known',[(1866,1903)]),('5.1866 – 5.1903','known',[(1866,1903)]),
    ('1.1.1866-31.12.1903','known',[(1866,1903)]),('5.1866','known',[(1866,1866)]),
    ('5.1866-','known',[(1866,None)]),('5.1903-5.1866','uncertain',[]),
    ('5.1866-5.1903; 1.1.1910','known',[(1866,1903),(1910,1910)]),
    ('5.1866-5.1903 část','uncertain',[(1866,1903)]),
    ('1866-5.1903','known',[(1866,1903)]),
])
def test_periods(text,status,spans):
    p=parse_periods(text)
    assert p.status==status
    assert [(s.start,s.end) for s in p.spans]==spans


def test_unicode_and_aliases():
    assert normalize('  KROMĚŘÍŽ  ')==normalize('Kromeriz')
    p=parse_places(['',None,{'id':2045,'label':'Slavkov u Brna (Austerlitz), obec: Slavkov u Brna, okres: Vyškov'}],URL)
    assert p[0].source_id=='2045'
    assert p[0].names==['Slavkov u Brna','Austerlitz']
    assert 'Vyškov' in p[0].label

@pytest.mark.parametrize('data',[{},[42],[{'id':'../../bad','label':'x'}],[{'id':'2'}]])
def test_invalid_places_fail(data):
    with pytest.raises(ResearchError):parse_places(data,URL)


def test_event_and_index_periods_are_separate():
    books,pagers,warnings=parse_books(fixture_html(),URL)
    b=books[0]
    assert b.source_id=='900001' and b.number=='DEMO-1'
    assert b.coverage['birth'].records.spans[0].start==1840
    assert b.coverage['marriage'].records.spans[0].start==1850
    assert b.coverage['death'].records.spans[0].start==1842
    assert b.coverage['birth'].index.status=='known'
    assert b.coverage['death'].index.status=='absent'
    assert b.village_count==3 and b.image_count==2
    assert not pagers and not warnings
    assert relevance(b,SearchQuery(raw_query='test',event_type='death',year_from=1840,year_to=1841))['status']=='excluded'
    assert relevance(b,SearchQuery(raw_query='test',event_type='birth',year_from=1840,year_to=1841))['status']=='candidate'


def test_index_only_book_can_be_candidate():
    b=parse_books(fixture_html(),URL)[0][0]
    b.coverage['birth'].records=parse_periods('-')
    assert relevance(b,SearchQuery(raw_query='test',event_type='birth',year_from=1840,year_to=1841))['via']=='index'
    b.coverage['birth']=EventCoverage()
    assert relevance(b,SearchQuery(raw_query='test',event_type='birth',year_from=1000,year_to=1001))['status']=='needs_review'


def test_changed_html_is_not_empty_result():
    with pytest.raises(ResearchError) as e:parse_books('<html>Please log in</html>',URL)
    assert e.value.code=='PARSE_ERROR'


def test_pagination_must_not_change_place():
    html=with_pagination(fixture_html(),'<a href="?typ=obec&obec_id=999">Next</a>')
    with pytest.raises(ResearchError):parse_books(html,URL)
    html=with_pagination(fixture_html(),'<a href="?typ=obec&obec_id=900001&page=2">Next</a>')
    assert 'page=2' in parse_books(html,URL)[1][0]


def test_parse_manifest_keeps_order_and_ids_not_guessed():
    book=parse_books(fixture_html(),URL)[0][0]
    html=DemoArchive().handle(httpx.Request('GET',book.detail_url)).text
    pages=parse_pages(html,book)
    assert [p.scan_number for p in pages]==[1,2]
    assert pages[0].image_key=='page-1.jp2'
    assert '?image=page-1.jp2' in pages[0].viewer_url
    assert pages[0].historical_page is None and not pages[0].viewer_link_verified
    assert len(set(p.page_id for p in pages))==2

@pytest.mark.parametrize('html',[
    '<script>CreateSeadragon([danger()],["x"]);</script>',
    '<script>CreateSeadragon(["https://www.mza.cz/iipsrv/a?Deepzoom=/x.jp2.dzi"],[]);</script>',
    '<script>CreateSeadragon(["https://www.mza.cz/iipsrv/a?Deepzoom=/x.jp2.dzi"],["../x.jp2"]);</script>',
    '<html>No image manifest</html>',
])
def test_no_eval_missing_manifest_and_invalid_names(html):
    book=parse_books(fixture_html(),URL)[0][0]
    with pytest.raises(ResearchError):parse_pages(html,book)

@pytest.mark.parametrize('url',[
    'http://www.mza.cz/actapublica/matrika/detail/1',
    'https://evil.example/actapublica/matrika/detail/1',
    'https://user:pass@www.mza.cz/actapublica/matrika/detail/1',
    'https://www.mza.cz/actapublica/matrika/hledani?book=1',
])
def test_only_real_detail_url_form(url):
    with pytest.raises(ResearchError):book_id_from_url(url)


def test_iipsrv_deepzoom_is_read_through_iiif():
    # MZA's iipsrv also serves IIIF: one request per region instead of dozens of DZI tiles.
    book=parse_books(fixture_html(),URL)[0][0]
    html=('<script>CreateSeadragon(["https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/L/a-1.jp2.dzi",'
          '"https://example.mza.cz/other/a-2.dzi"],["a-1.jp2","a-2.jp2"]);</script>')
    pages=parse_pages(html,book)
    assert pages[0].source_type=='iiif'
    assert pages[0].source_url=='https://www.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/L/a-1.jp2/info.json'
    assert pages[1].source_type=='dzi' and pages[1].source_url=='https://example.mza.cz/other/a-2.dzi'


def test_register_with_month_year_dating_is_candidate():
    from rubrika.models import Book
    b=parse_books(fixture_html(),URL)[0][0]
    b.coverage['birth']=EventCoverage(records=parse_periods('5.1866-5.1903'),index=parse_periods('-'))
    assert relevance(b,SearchQuery(raw_query='test',event_type='birth',year_from=1890,year_to=1890))['status']=='candidate'
    assert relevance(b,SearchQuery(raw_query='test',event_type='birth',year_from=1910,year_to=1910))['status']=='excluded'
