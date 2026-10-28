"""Navigation inside an opened book: jump to a scan by its number and keep what was already read."""
import pytest
from conftest import call,open_demo
from test_images import as_dzi


async def layout(service,sid,image_id,key,**extra):
    obs={'kind':'layout','image_id':image_id,'text':'Orientation only',**extra}
    return await call(service,'record_finding',research_id=sid,observation=obs,idempotency_key=key)


@pytest.mark.asyncio
async def test_view_scan_jumps_by_scan_number_without_another_open(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    page=data['pages'][1]
    by_id=await call(service,'view_scan',research_id=sid,page_id=page['page_id'])
    by_number=await call(service,'view_scan',research_id=sid,source_id=data['unit']['source_id'],scan_number=page['scan_number'])
    assert by_number.data['image']['page_id']==page['page_id']
    assert by_number.data['image']['sha256']==by_id.data['image']['sha256']


@pytest.mark.asyncio
async def test_view_scan_opens_a_listed_book_itself(auto_service):
    service,_=auto_service
    out=await call(service,'start_research',query={'raw_query':'Synthetic test','place_hint':'Demo Lhota','event_type':'birth','year_from':1840,'year_to':1845})
    sid=out.data['research_id']
    out=await call(service,'find_place',research_id=sid,term='Demo Lhota',kind='parish_registers',archives=['mza'])
    out=await call(service,'list_sources',research_id=sid,place_ids=[out.data['places'][0]['source_id']],kind='parish_registers')
    book=out.data['sources'][0]['source_id']
    img=await call(service,'view_scan',research_id=sid,source_id=book,scan_number=2)
    assert img.data['image']['book_id']==book and img.data['image']['scan_number']==2
    assert service.store.has_link(sid,'alice','opened_book',book)


@pytest.mark.asyncio
async def test_scan_number_needs_an_listed_source_and_an_existing_scan(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    book=data['unit']['source_id']
    missing=await service.call('view_scan','alice',{'research_id':sid,'source_id':book,'scan_number':99999})
    assert missing.is_error and missing.data['error']['code']=='NOT_FOUND' and 'snímky' in missing.data['error']['message']
    other=await service.call('view_scan','alice',{'research_id':sid,'source_id':'not-opened','scan_number':1})
    assert other.is_error and other.data['error']['code']=='NOT_FOUND'
    foreign=await service.call('view_scan','bob',{'research_id':sid,'source_id':book,'scan_number':1})
    assert foreign.is_error and foreign.data['error']['code']=='NOT_FOUND'


@pytest.mark.asyncio
@pytest.mark.parametrize('args',[{},{'source_id':'x'},{'scan_number':1},{'page_id':'p','scan_number':1},
                                 {'page_id':'p','source_id':'x','scan_number':1}])
async def test_view_scan_takes_page_id_or_source_with_scan_number(auto_service,args):
    service,_=auto_service
    sid,_=await open_demo(service)
    out=await service.call('view_scan','alice',{'research_id':sid,**args})
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_manifest_from_before_scan_numbers_still_resolves(auto_service):
    service,_=auto_service
    store=service.store
    sid,data=await open_demo(service)
    book=data['unit']['source_id']
    manifest=next(m for m in store.links(sid,'alice','manifest') if m['id']==book)
    store.link(sid,'alice','manifest',book,{k:v for k,v in manifest.items() if k not in ('id','scan_numbers')})
    page=data['pages'][1]
    out=await call(service,'view_scan',research_id=sid,source_id=book,scan_number=page['scan_number'])
    assert out.data['image']['page_id']==page['page_id']


@pytest.mark.asyncio
async def test_open_source_returns_known_points_latest_reading_wins(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    book=data['unit']['source_id']
    first,second=data['pages'][0],data['pages'][1]
    a=await call(service,'view_scan',research_id=sid,page_id=first['page_id'])
    b=await call(service,'view_scan',research_id=sid,page_id=second['page_id'])
    await layout(service,sid,a.data['image']['image_id'],'a1',year=1841,section='narození Demo Lhota')
    await layout(service,sid,b.data['image']['image_id'],'b1',year=1849)
    await layout(service,sid,b.data['image']['image_id'],'b2',year=1842)     # a correction after a zoomed reading
    await call(service,'record_finding',research_id=sid,idempotency_key='n1',
               observation={'kind':'next_step','text':'Pokračovat v roce 1843'})
    out=await call(service,'open_source',research_id=sid,source_id=book)
    points=out.data['known_points']
    assert [(p['scan_number'],p['year']) for p in points]==[(first['scan_number'],1841),(second['scan_number'],1842)]
    assert points[0]['section']=='narození Demo Lhota' and points[1]['section'] is None
    assert not any('klesají' in w for w in out.data['warnings'])


@pytest.mark.asyncio
async def test_falling_years_warn_of_a_book_in_parts(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    a=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    b=await call(service,'view_scan',research_id=sid,page_id=data['pages'][1]['page_id'])
    await layout(service,sid,a.data['image']['image_id'],'a1',year=1890)
    await layout(service,sid,b.data['image']['image_id'],'b1',year=1866)
    out=await call(service,'open_source',research_id=sid,source_id=data['unit']['source_id'])
    assert any('klesají' in w for w in out.data['warnings'])


@pytest.mark.asyncio
async def test_known_points_stay_in_their_own_book_and_research(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    await layout(service,sid,img.data['image']['image_id'],'a1',year=1841)
    other,fresh=await open_demo(service)
    assert 'known_points' not in fresh
    again=await call(service,'open_source',research_id=other,source_id=data['unit']['source_id'])
    assert 'known_points' not in again.data


@pytest.mark.asyncio
async def test_layout_year_is_range_checked(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    img=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    out=await service.call('record_finding','alice',{'research_id':sid,'idempotency_key':'y',
        'observation':{'kind':'layout','image_id':img.data['image']['image_id'],'year':18900}})
    assert out.is_error and out.data['error']['code']=='INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_small_preview_is_smaller_and_still_zoomable(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    small=await call(service,'view_scan',research_id=sid,page_id=data['pages'][1]['page_id'],preview='small')
    assert max(small.data['image']['width'],small.data['image']['height'])<=service.cfg.navigation_side
    assert small.data['image']['role']=='overview' and 'zoom_scan' in small.data['note']
    crop=await call(service,'zoom_scan',research_id=sid,image_id=small.data['image']['image_id'],bbox_px={'x':10,'y':10,'width':100,'height':60})
    assert crop.data['image']['role']=='detail'
    bad=await service.call('view_scan','alice',{'research_id':sid,'page_id':data['pages'][0]['page_id'],'preview':'tiny'})
    assert bad.is_error and bad.data['error']['code']=='INVALID_ARGUMENT'


@pytest.mark.asyncio
async def test_small_preview_needs_far_fewer_tiles(auto_service):
    service,archive=auto_service
    sid,data=await open_demo(service)
    page=as_dzi(service._page(sid,'alice',data['pages'][0]['page_id']))
    desc,_,_=await service.images.descriptor(sid,'alice',page)
    whole=(0,0,archive.width,archive.height)
    _,normal=await service.images.render_dzi(sid,'alice',desc,whole,service.cfg.preview_side)
    _,small=await service.images.render_dzi(sid,'alice',desc,whole,service.cfg.navigation_side)
    assert len(small)*3<=len(normal)


@pytest.mark.asyncio
async def test_each_image_reports_what_it_cost_the_archive(auto_service):
    service,_=auto_service
    sid,data=await open_demo(service)
    first=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    cost=first.data['archive_requests']
    assert cost>=1
    again=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    assert 0<=again.data['archive_requests']<=cost
    crop=await call(service,'zoom_scan',research_id=sid,image_id=first.data['image']['image_id'],bbox_px={'x':10,'y':10,'width':80,'height':40})
    assert crop.data['archive_requests']>=0
    total=service.store.usage(sid,'alice')['archive_requests']
    assert total>=cost+again.data['archive_requests']+crop.data['archive_requests']


def test_town_parts_names_parts_with_books_of_their_own():
    from rubrika.service import town_parts
    places=[{'label':'Třebíč (Trebitsch), okres: Třebíč'},
            {'label':'Jejkov (Jeikow, Třebíč-Jejkov), obec: Třebíč, okres: Třebíč'},
            {'label':'Podklášteří (Unterkloster, Třebíč-Podklášteří), obec: Třebíč, okres: Třebíč'},
            {'label':'Všeobecná nemocnice Třebíč-Jejkov, obec: Třebíč, okres: Třebíč'},
            {'label':'Číchov (třebíčská část), okres: Třebíč'},
            {'label':'Lhota, obec: Třebíčko, okres: Brno'}]
    assert town_parts('Třebíč',places)==['Jejkov','Podklášteří','Všeobecná nemocnice Třebíč-Jejkov']
    assert town_parts('Hodonín',places)==[]
