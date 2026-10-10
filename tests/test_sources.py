import pytest
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

U='00000000-0000-4000-8000-0000000000a1'
P=SourcePolicy(['www.mza.cz','iipserver.mza.cz'])

@pytest.mark.parametrize('url,method,source',[
    ('https://www.mza.cz/actapublica/matrika/detail/123','GET','acta_publica'),
    ('https://www.mza.cz/actapublica/matrika/hledani?typ=obec&obec_id=1','GET','acta_publica'),
    ('https://www.mza.cz/robots.txt','GET','robots'),
    ('https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/x.jp2.dzi','GET','mza_images'),
    ('https://iipserver.mza.cz/iiif/x/info.json','GET','mza_images'),
    (f'https://www.mza.cz/aron/api/aron/apu/{U}','GET','aron'),
    (f'https://www.mza.cz/aron/api/aron/script/citation/{U}','GET','aron'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=ENTITY','POST','aron'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=ARCH_DESC','POST','aron'),
])
def test_allowed_routes(url,method,source):
    route=P.validate(url,method)
    assert route.source==source and route.method==method

@pytest.mark.parametrize('url,method',[
    ('https://www.mza.cz/actapublica/matrika/hledani','POST'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=ENTITY','GET'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=FUND','POST'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=ENTITY&listType=FUND','POST'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults?listType=ENTITY&debug=1','POST'),
    ('https://www.mza.cz/aron/api/aron/apu/listresults','POST'),
    ('https://www.mza.cz/aron/api/aron/apu/listview?listType=ENTITY','POST'),
    (f'https://www.mza.cz/aron/api/aron/apu/{U}/tree','GET'),
    (f'https://www.mza.cz/aron/api/aron/apu/{U}?x=1','GET'),
    ('https://www.mza.cz/aron/api/aron/apu/not-a-uuid','GET'),
    (f'https://www.mza.cz/aron/api/aron/apu/{U.upper()}','GET'),
    ('https://www.mza.cz/aron/api/orders','POST'),
    ('https://www.mza.cz/aron/api/aron/me','GET'),
    (f'https://www.mza.cz/aron/api/aron/digitalObjectFile/{U}/metadata/csv','GET'),
    (f'https://www.mza.cz/aron/apu/{U}','GET'),
    ('https://www.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/x.jp2.dzi','POST'),
    (f'https://www.mza.cz/aron/api/aron/apu/{U}','POST'),
    ('https://www.mza.cz/robots.txt','DELETE'),
])
def test_rejected_routes(url,method):
    with pytest.raises(ResearchError) as e:P.validate(url,method)
    assert e.value.code=='INVALID_SOURCE'

def test_groups_separate_sources_but_share_the_image_server():
    assert P.validate('https://www.mza.cz/actapublica/dokumenty').group=='acta_publica'
    assert P.validate(f'https://www.mza.cz/aron/api/aron/apu/{U}').group=='aron'
    assert P.validate('https://www.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/x.jp2/info.json').group=='mza_images'
    assert P.validate('https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/y.jp2/info.json').group=='mza_images'

C='https://www.mza.cz/scitacioperaty/digisada'

@pytest.mark.parametrize('url,method',[
    (C+'/autocomplete/?field=obec&term=Tr%C3%A1vn%C3%ADk','GET'),
    (C+'/autocomplete/?field=cobec&term=X','GET'),
    (C+'/do_advanced_search','POST'),
    (C+'/advanced_search','GET'),
    (C+'/advanced_search/10','GET'),
    (C+'/detail/4422','GET'),
])
def test_census_routes(url,method):
    route=P.validate(url,method)
    assert route.source=='census' and route.group=='census'

@pytest.mark.parametrize('url,method',[
    (C+'/autocomplete/?field=search&term=X','GET'),
    (C+'/autocomplete/?field=obec','GET'),
    (C+'/autocomplete/?field=obec&term=X&x=1','GET'),
    (C+'/autocomplete/?field=obec&field=cobec&term=X','GET'),
    (C+'/do_advanced_search?x=1','POST'),
    (C+'/do_advanced_search','GET'),
    (C+'/do_search','POST'),
    (C+'/search','GET'),
    (C+'/search/2','GET'),
    (C+'/advanced_search?page=2','GET'),
    (C+'/advanced_search/123456','GET'),
    (C+'/detail/abc','GET'),
    (C+'/detail/4422?x=1','GET'),
    ('https://www.mza.cz/scitacioperaty/contact-form','POST'),
    ('https://www.mza.cz/scitacioperaty/page/view/help','GET'),
])
def test_census_rejected_routes(url,method):
    with pytest.raises(ResearchError) as e:P.validate(url,method)
    assert e.value.code=='INVALID_SOURCE'
