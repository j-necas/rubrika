import httpx
import pytest
from pydantic import ValidationError
from rubrika.models import ArchivalUnit, ImageRef, Observation, Page
from rubrika.images import ImageService
from rubrika.network import SafeSource
from rubrika.demo import DemoArchive

OLD_PAGE={'page_id':'page_x','book_id':'900001','scan_number':1,'image_key':'page-1.jp2',
    'source_url':'https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/DEMO/page-1.jp2/info.json',
    'viewer_url':'https://www.mza.cz/actapublica/matrika/detail/900001?image=page-1.jp2',
    'source_type':'iiif','manifest_sha256':'x','historical_page':None,'viewer_link_verified':False}
OLD_REF={'image_id':'image_x','page_id':'page_x','book_id':'900001','source_width':10,'source_height':10,
    'source_bbox':[0,0,10,10],'width':10,'height':10,'role':'overview','sha256':'x',
    'viewer_url':'https://www.mza.cz/','manifest_sha256':'x','viewer_link_verified':False,'scan_number':1}

def test_v01_payloads_still_load_with_register_defaults():
    page=Page.model_validate(OLD_PAGE)
    ref=ImageRef.model_validate(OLD_REF)
    assert page.source=='acta_publica' and page.citation is None
    assert ref.source=='acta_publica' and ref.citation is None and ref.image_key is None

def test_observation_links_are_typed_and_bounded():
    obs=Observation(kind='next_step',text='x',links=[{'observation_id':'obs_1','relation':'same_person'}])
    assert obs.links[0].relation=='same_person'
    assert Observation(kind='next_step',text='x').links==[]
    with pytest.raises(ValidationError):
        Observation(kind='next_step',text='x',links=[{'observation_id':'obs_1','relation':'cousin'}])
    with pytest.raises(ValidationError):
        Observation(kind='next_step',text='x',links=[{'observation_id':f'obs_{i}','relation':'related'} for i in range(11)])

def test_archival_unit_defaults():
    unit=ArchivalUnit(source_id='aron:x',title='SYNTHETIC',detail_url='https://www.mza.cz/aron/apu/x')
    assert unit.category=='other' and unit.dating.status=='unknown' and unit.fund.mark is None

@pytest.mark.asyncio
async def test_image_ref_carries_source_citation_and_image_key(cfg,store,sid):
    archive=DemoArchive()
    source=SafeSource(cfg,store,transport=archive.transport,respect_robots=False,source_mode='synthetic_demo')
    page=Page.model_validate({**OLD_PAGE,'source':'aron','citation':'SYNTHETIC citation','book_id':'aron:x'})
    try:
        out=await ImageService(source,store,cfg).get(sid,'alice',page)
        image=out.data['image']
        assert image['source']=='aron' and image['citation']=='SYNTHETIC citation' and image['image_key']=='page-1.jp2'
    finally:await source.close()


def test_old_page_and_imageref_default_to_mza():
    from rubrika.models import Page, ImageRef
    page=Page(page_id='page_x',book_id='1',scan_number=1,image_key='k',source_url='https://www.mza.cz/x',
              viewer_url='https://www.mza.cz/v',source_type='dzi',manifest_sha256='0'*64)
    assert page.archive=='mza'
    ref=ImageRef(image_id='image_x',page_id='page_x',book_id='1',source_width=10,source_height=10,source_bbox=(0,0,10,10),
                 width=10,height=10,role='overview',sha256='0'*64,viewer_url='https://www.mza.cz/v',manifest_sha256='0'*64,scan_number=1)
    assert ref.archive=='mza' and ref.robots_disallowed is False
