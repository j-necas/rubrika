import pytest
import pytest_asyncio

from rubrika.config import Settings
from rubrika.storage import Store
from rubrika.models import SearchQuery
from rubrika.network import SafeSource
from rubrika.service import ResearchService
from rubrika.demo import DemoArchive


@pytest.fixture
def cfg(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, live_enabled=True, aron_enabled=True, census_enabled=True, vca_enabled=True, request_interval=0)


@pytest.fixture
def store(cfg):
    value = Store(cfg)
    yield value
    value.close()


@pytest.fixture
def sid(store):
    return store.create('alice', SearchQuery(raw_query='Synthetic test', event_type='birth',
                                           year_from=1840, year_to=1845))['research_id']


@pytest_asyncio.fixture
async def auto_service(cfg, store):
    archive = DemoArchive()
    source = SafeSource(cfg, store, transport=archive.transport, source_mode='synthetic_demo')
    service = ResearchService(cfg, store, source)
    yield service, archive
    await service.close()


async def call(service, name, owner='alice', **args):
    out = await service.call(name, owner, args)
    assert not out.is_error, out.data
    return out


async def open_demo(service, owner='alice'):
    out = await call(service, 'start_research', owner, query={'raw_query':'Synthetic test',
        'place_hint':'Demo Lhota', 'event_type':'birth', 'year_from':1840,'year_to':1845})
    sid=out.data['research_id']
    out=await call(service,'find_place',owner,research_id=sid,term='Demo Lhota',kind='parish_registers',archives=['mza'])
    out=await call(service,'list_sources',owner,research_id=sid,place_ids=[out.data['places'][0]['source_id']],kind='parish_registers')
    out=await call(service,'open_source',owner,research_id=sid,source_id=out.data['sources'][0]['source_id'])
    return sid,out.data
