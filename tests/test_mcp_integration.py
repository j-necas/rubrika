"""Runs against the real pinned SDK, never a stub. Optional when SDK is unavailable."""
import json
import pytest
mcp_sdk=pytest.importorskip('mcp',reason='Official MCP SDK unavailable; do not count this as a protocol pass')
from mcp import Client
from rubrika.server import make_server

pytestmark=pytest.mark.mcp

@pytest.mark.asyncio
async def test_real_sdk_lists_typed_tools_and_returns_image(auto_service):
    service,_=auto_service
    mcp=make_server(service)
    async with Client(mcp,raise_exceptions=True) as client:
        tools=await client.list_tools()
        # SDK Client returns ListToolsResult (not a list of raw dictionaries).
        assert {t.name for t in tools.tools}=={'start_research','revise_research','get_research_status','finish_research','view_scan','zoom_scan',
            'show_saved_evidence','record_finding','list_findings','export_family_tree','list_archives','find_place','list_sources','open_source','import_scan','continue_research'}
        result=await client.call_tool('start_research',{'query':{'raw_query':'SYNTHETIC SDK TEST','event_type':'birth','year_from':1840,'year_to':1845}})
        assert not result.is_error
        sid=json.loads(result.content[0].text)['research_id']
        result=await client.call_tool('find_place',{'research_id':sid,'term':'Demo Lhota','kind':'parish_registers','archives':['mza']})
        place=json.loads(result.content[0].text)['places'][0]['source_id']
        result=await client.call_tool('list_sources',{'research_id':sid,'place_ids':[place],'kind':'parish_registers'})
        bid=json.loads(result.content[0].text)['sources'][0]['source_id']
        result=await client.call_tool('open_source',{'research_id':sid,'source_id':bid})
        pid=json.loads(result.content[0].text)['pages'][0]['page_id']
        result=await client.call_tool('view_scan',{'research_id':sid,'page_id':pid})
        assert not result.is_error
        assert result.content[1].type=='image' and result.content[1].data
        image_id=json.loads(result.content[0].text)['image']['image_id']
        result=await client.call_tool('record_finding',{'research_id':sid,'idempotency_key':'sdk-tree',
            'observation':{'kind':'record','image_id':image_id,'text':'TEST ONLY'}})
        oid=json.loads(result.content[0].text)['observation_id']
        export={t.name:t for t in tools.tools}['export_family_tree']
        assert export.annotations.read_only_hint is True
        result=await client.call_tool('export_family_tree',{'variant':'documented_only','tree':{
            'title':'SDK test','root_person':'a','persons':[{'id':'a','given_name':'Test','events':[
                {'type':'birth','confidence':'documented','sources':[{'research_id':sid,'observation_id':oid}]}]}]}})
        assert not result.is_error
        assert json.loads(result.content[0].text)['gedcom'].startswith('0 HEAD')


@pytest.mark.asyncio
async def test_real_sdk_runs_the_aron_flow(auto_service):
    from rubrika import demo_aron as D
    service,_=auto_service
    mcp=make_server(service)
    async with Client(mcp,raise_exceptions=True) as client:
        tools={t.name:t for t in (await client.list_tools()).tools}
        assert {'find_place','list_sources','open_source','import_scan'}<=set(tools)
        assert tools['open_source'].annotations.read_only_hint is False
        result=await client.call_tool('start_research',{'query':{'raw_query':'SYNTHETIC SDK ARON','year_from':1740,'year_to':1760}})
        sid=json.loads(result.content[0].text)['research_id']
        result=await client.call_tool('find_place',{'research_id':sid,'term':'Demo Lhota','kind':'land_records','archives':['mza']})
        ids=[p['source_id'] for p in json.loads(result.content[0].text)['places']]
        result=await client.call_tool('list_sources',{'research_id':sid,'place_ids':ids,'kind':'land_records','fund_marks':['C 17']})
        units=json.loads(result.content[0].text)['sources']
        assert units[0]['source_id']=='aron:'+D.UNIT_LAND
        result=await client.call_tool('open_source',{'research_id':sid,'source_id':units[0]['source_id'],'limit':1})
        pid=json.loads(result.content[0].text)['pages'][0]['page_id']
        result=await client.call_tool('view_scan',{'research_id':sid,'page_id':pid})
        assert not result.is_error and result.content[1].type=='image'
        assert json.loads(result.content[0].text)['image']['source']=='aron'


@pytest.mark.asyncio
async def test_real_sdk_runs_the_census_flow(auto_service):
    service,_=auto_service
    mcp=make_server(service)
    async with Client(mcp,raise_exceptions=True) as client:
        tools={t.name:t for t in (await client.list_tools()).tools}
        assert {'find_place','list_sources','open_source','import_scan'}<=set(tools)
        assert tools['open_source'].annotations.read_only_hint is False
        result=await client.call_tool('start_research',{'query':{'raw_query':'SYNTHETIC SDK census','year_from':1880,'year_to':1900}})
        sid=json.loads(result.content[0].text)['research_id']
        result=await client.call_tool('find_place',{'research_id':sid,'term':'Demo Lhota','kind':'census','archives':['mza']})
        pid=json.loads(result.content[0].text)['places'][0]['source_id']
        result=await client.call_tool('list_sources',{'research_id':sid,'place_ids':[pid],'kind':'census','district_archive':'Blansko','year_from':1890,'year_to':1890})
        sets=json.loads(result.content[0].text)['sources']
        result=await client.call_tool('open_source',{'research_id':sid,'source_id':sets[0]['source_id'],'limit':1})
        page=json.loads(result.content[0].text)['pages'][0]['page_id']
        result=await client.call_tool('view_scan',{'research_id':sid,'page_id':page})
        assert not result.is_error and result.content[1].type=='image'
        assert json.loads(result.content[0].text)['image']['source']=='census'



@pytest.mark.asyncio
async def test_real_sdk_offers_the_cadastral_maps_kind(tmp_path):
    from rubrika.config import Settings
    from rubrika.demo import DemoArchive
    from rubrika.network import SafeSource
    from rubrika.service import ResearchService
    from rubrika.storage import Store
    cfg=Settings(_env_file=None,data_dir=tmp_path,live_enabled=True,sketches_enabled=True,request_interval=0)
    store=Store(cfg); archive=DemoArchive()
    service=ResearchService(cfg,store,SafeSource(cfg,store,transport=archive.transport,source_mode='synthetic_demo'))
    try:
        async with Client(make_server(service),raise_exceptions=True) as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            for name in ('find_place','list_sources'):
                assert 'cadastral_maps' in tools[name].input_schema['properties']['kind']['enum']
            result=await client.call_tool('start_research',{'query':{'raw_query':'SYNTHETIC SDK sketch'}})
            sid=json.loads(result.content[0].text)['research_id']
            result=await client.call_tool('find_place',{'research_id':sid,'term':'Mezistrání','kind':'cadastral_maps','district':'Prostějov'})
            pid=json.loads(result.content[0].text)['places'][0]['source_id']
            result=await client.call_tool('list_sources',{'research_id':sid,'place_ids':[pid],'kind':'cadastral_maps'})
            sketch=json.loads(result.content[0].text)['sources'][0]['source_id']
            result=await client.call_tool('open_source',{'research_id':sid,'source_id':sketch})
            page=json.loads(result.content[0].text)['pages'][0]['page_id']
            result=await client.call_tool('view_scan',{'research_id':sid,'page_id':page})
            assert not result.is_error and result.content[1].type=='image'
            assert json.loads(result.content[0].text)['image']['source']=='sketch'
    finally:
        await service.close(); store.close()
