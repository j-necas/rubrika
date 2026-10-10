import base64
import io
import json
import math
import httpx
import pytest
from PIL import Image, ImageChops
from rubrika.images import DZI,parse_dzi,source_bbox,decode_image
from rubrika.models import ImageRef,BBox
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.demo import DemoArchive
from rubrika.errors import ResearchError
from conftest import call,open_demo


def as_dzi(page):
    url=page.source_url.replace('?IIIF=','?Deepzoom=').removesuffix('/info.json')+'.dzi'
    return page.model_copy(update={'source_type':'dzi','source_url':url})


def make_ref():
    return ImageRef(image_id='i',page_id='p',book_id='b',source_width=4000,source_height=3000,
        source_bbox=(1000,500,2000,1000),width=1000,height=500,role='detail',sha256='x',
        viewer_url='https://www.mza.cz/',manifest_sha256='x',scan_number=1)


def test_nested_coordinate_transform():
    assert source_bbox(make_ref(),BBox(x=100,y=50,width=200,height=100))==(1200,600,400,200)
    assert source_bbox(make_ref(),BBox(x=0,y=0,width=1000,height=500))==(1000,500,2000,1000)
    with pytest.raises(ResearchError):source_bbox(make_ref(),BBox(x=999,y=0,width=2,height=1))

@pytest.mark.parametrize('x',[float('nan'),float('inf'),-1])
def test_invalid_coordinates(x):
    with pytest.raises(ValueError):BBox(x=x,y=0,width=10,height=10)

@pytest.mark.parametrize('raw',[
    b'<Image TileSize="256" Format="jpg"><Size Width="0" Height="1"/></Image>',
    b'<!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]><Image/>',
    b'<Image TileSize="256" Format="gif"><Size Width="10" Height="10"/></Image>',
    b'<Image TileSize="256" Format="jpg"><Size Width="100000" Height="100000"/></Image>',
])
def test_invalid_descriptors(raw):
    with pytest.raises(ResearchError):parse_dzi(raw,'https://www.mza.cz/a.dzi',80000000)


def test_reject_truncated_and_non_image():
    with pytest.raises(ResearchError):decode_image(b'<html>access denied</html>',100000)
    im=Image.new('RGB',(100,100));out=io.BytesIO();im.save(out,'JPEG')
    with pytest.raises(ResearchError):decode_image(out.getvalue()[:200],100000)


@pytest.mark.asyncio
async def test_dzi_stitching_overlap_and_edges_exact(auto_service):
    service,archive=auto_service
    sid,data=await open_demo(service)
    page=as_dzi(service._page(sid,'alice',data['pages'][0]['page_id']))
    desc,_,_=await service.images.descriptor(sid,'alice',page)
    full,_=await service.images.render_dzi(sid,'alice',desc,(0,0,archive.width,archive.height),2000)
    assert full.size==archive.image.size
    assert ImageChops.difference(full,archive.image).getbbox() is None
    region=(509,382,190,111)
    crop,_=await service.images.render_dzi(sid,'alice',desc,region,1000)
    assert ImageChops.difference(crop,archive.image.crop((509,382,699,493))).getbbox() is None


@pytest.mark.asyncio
async def test_missing_tile_does_not_create_complete_evidence(cfg,store):
    archive=DemoArchive()
    def handler(req):
        if '_files/' in str(req.url) or ('IIIF=' in str(req.url) and not str(req.url).endswith('/info.json')):
            return httpx.Response(404)
        return archive.handle(req)
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),source_mode='synthetic_demo')
    service=ResearchService(cfg,store,source)
    try:
        sid,data=await open_demo(service)
        out=await service.call('view_scan','alice',{'research_id':sid,'page_id':data['pages'][0]['page_id']})
        assert out.is_error and out.data['error']['code']=='INCOMPLETE_IMAGE'
        assert not store.links(sid,'alice','downloaded_page')
        assert store.usage(sid,'alice')['archive_images']==0
    finally:await service.close()


@pytest.mark.asyncio
async def test_image_blocks_payload_and_crop(auto_service):
    service,archive=auto_service
    sid,data=await open_demo(service)
    out=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    ref=out.data['image']
    wire=out.wire()
    assert wire['content'][1]['type']=='image'
    assert wire['content'][1]['mimeType']=='image/jpeg'
    assert len(json.dumps(wire,ensure_ascii=True))<service.cfg.payload_max_chars
    assert decode_image(base64.b64decode(out.image_b64),80000000).size==(ref['width'],ref['height'])
    expected=source_bbox(ImageRef.model_validate(ref),BBox(x=10,y=10,width=100,height=100))
    crop=await call(service,'zoom_scan',research_id=sid,image_id=ref['image_id'],bbox_px={'x':10,'y':10,'width':100,'height':100})
    assert tuple(crop.data['image']['source_bbox'])==expected
    assert crop.data['image']['role']=='detail'
    assert len(store_images(service,sid))==2


def store_images(service,sid):
    return service.store.db.execute('SELECT id FROM images WHERE sid=?',(sid,)).fetchall()


@pytest.mark.asyncio
async def test_detail_payload_not_silently_downscaled(auto_service):
    service,archive=auto_service
    sid,data=await open_demo(service)
    out=await call(service,'view_scan',research_id=sid,page_id=data['pages'][0]['page_id'])
    service.cfg.payload_max_chars=8000
    ref=out.data['image']
    result=await service.call('zoom_scan','alice',{'research_id':sid,'image_id':ref['image_id'],
        'bbox_px':{'x':0,'y':0,'width':ref['width'],'height':ref['height']}})
    assert result.is_error and result.data['error']['code']=='PAYLOAD_TOO_LARGE'
    assert len(store_images(service,sid))==1

@pytest.mark.asyncio
async def test_iiif_explicit_region_and_dimensions(cfg,store,sid):
    from rubrika.models import Page
    from rubrika.images import ImageService
    archive=DemoArchive()
    base='https://iipserver.mza.cz/iiif/synthetic'
    requested=[]
    def handler(req):
        requested.append(str(req.url))
        if req.url.path.endswith('/info.json'):
            return httpx.Response(200,json={'width':archive.width,'height':archive.height})
        parts=req.url.path.split('/')
        x,y,w,h=map(int,parts[-4].split(','));tw,th=map(int,parts[-3].split(','))
        out=io.BytesIO();archive.image.crop((x,y,x+w,y+h)).resize((tw,th)).save(out,'JPEG')
        return httpx.Response(200,content=out.getvalue(),headers={'content-type':'image/jpeg'})
    source=SafeSource(cfg,store,transport=httpx.MockTransport(handler),respect_robots=False,source_mode='synthetic_demo')
    page=Page(page_id='iiif-page',book_id='demo',scan_number=1,image_key='image.jp2',
        source_url=base+'/info.json',source_type='iiif',
        viewer_url='https://www.mza.cz/actapublica/matrika/detail/900001?image=image.jp2',
        manifest_sha256='test')
    try:
        images=ImageService(source,store,cfg)
        out=await images.get(sid,'alice',page,(200,100,300,200),role='detail')
        assert out.data['image']['source_bbox']==(200,100,300,200)
        assert out.data['image']['width']==300 and out.data['image']['height']==200
        assert requested[-1].endswith('/200,100,300,200/300,200/0/default.jpg')
    finally:await source.close()

@pytest.mark.asyncio
async def test_iiif_service_limits_enforced_before_fetch(auto_service):
    from rubrika.models import Page
    service,archive=auto_service
    sid,data=await open_demo(service)
    page=Page.model_validate(data['pages'][0]);page.source_type='iiif';page.source_url='https://iipserver.mza.cz/iiif/test/info.json'
    n=len(archive.requests)
    with pytest.raises(ResearchError) as e:
        await service.images.render_iiif(sid,'alice',page,{'maxWidth':100},(0,0,500,200),1600)
    assert e.value.code=='REGION_TOO_LARGE' and len(archive.requests)==n


class FloorHalvingArchive(DemoArchive):
    """iipsrv (MZA) halves each level with floor, unlike the ceil in the Deep Zoom spec."""
    def level_size(self,level):
        w,h=self.width,self.height
        for _ in range(math.ceil(math.log2(max(w,h)))-level): w,h=w//2,h//2
        return w,h


@pytest.mark.asyncio
async def test_dzi_accepts_floor_halved_levels(cfg,store):
    archive=FloorHalvingArchive()
    source=SafeSource(cfg,store,transport=archive.transport,source_mode='synthetic_demo')
    service=ResearchService(cfg,store,source)
    try:
        sid,data=await open_demo(service)
        page=as_dzi(service._page(sid,'alice',data['pages'][0]['page_id']))
        desc,_,_=await service.images.descriptor(sid,'alice',page)
        # side=640 selects level 10: ceil gives 641x449, iipsrv serves 640x448.
        out,_=await service.images.render_dzi(sid,'alice',desc,(0,0,archive.width,archive.height),640)
        assert out.size==(640,448)
        # No unpainted (black) strip along the edges.
        assert out.crop((0,out.height-1,out.width,out.height)).convert('L').getextrema()[0]>60
        assert out.crop((out.width-1,0,out.width,out.height)).convert('L').getextrema()[0]>60
    finally:await service.close()


@pytest.mark.asyncio
async def test_demo_page_crop_uses_single_iiif_request(auto_service):
    service,archive=auto_service
    sid,data=await open_demo(service)
    page=service._page(sid,'alice',data['pages'][0]['page_id'])
    assert page.source_type=='iiif'
    n=len(archive.requests)
    out=await service.images.get(sid,'alice',page,(509,382,190,111),role='detail')
    assert [u for u in archive.requests[n:] if '_files/' in u]==[]
    assert archive.requests[-1].endswith('/509,382,190,111/190,111/0/default.jpg')
    image=decode_image(base64.b64decode(out.image_b64),10**7)
    assert image.size==(190,111)
    diff=ImageChops.difference(image,archive.image.crop((509,382,699,493))).convert('L')
    assert max(diff.getextrema())<60  # JPEG round trip, same content
