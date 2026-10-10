import time
import pytest
from rubrika.errors import ResearchError
from rubrika.models import SearchQuery
from rubrika.storage import Store


def test_ownership_and_missing_are_indistinguishable(store,sid):
    for target in (sid,'unknown'):
        with pytest.raises(ResearchError) as e:store.search(target,'bob')
        assert e.value.code=='NOT_FOUND'


def test_research_counters_persist(store,sid,cfg):
    for _ in range(3):store.link(sid,'alice','page_attempt','page-1')
    store.count_request(sid,'alice',source='acta_publica')
    store.count_request(sid,'alice',source='acta_publica')
    assert len(store.links(sid,'alice','page_attempt'))==1
    other=Store(cfg)
    try:assert other.usage(sid,'alice')['archive_requests']==2
    finally:other.close()


def test_checkpoint_pauses_until_the_user_agrees(store,sid):
    store.cfg.checkpoint_images=2
    with store.tx() as db:store._count(db,sid,'images',2)
    with pytest.raises(ResearchError) as e:store.check_checkpoint(sid,'alice')
    assert e.value.code=='CHECKPOINT' and e.value.details['images_since_checkpoint']==2
    assert store.usage(sid,'alice')['images_until_checkpoint']==0
    assert store.confirm_checkpoint(sid,'alice')=={'images_total':2,'next_checkpoint_after':2}
    store.check_checkpoint(sid,'alice')
    assert store.usage(sid,'alice')['images_until_checkpoint']==2
    with store.tx() as db:store._count(db,sid,'images',2)
    with pytest.raises(ResearchError):store.check_checkpoint(sid,'alice')


def test_checkpoint_belongs_to_one_research(store,sid):
    store.cfg.checkpoint_images=1
    with store.tx() as db:store._count(db,sid,'images',1)
    second=store.create('alice',SearchQuery(raw_query='second'))['research_id']
    store.check_checkpoint(second,'alice')
    with pytest.raises(ResearchError) as e:store.confirm_checkpoint(sid,'bob')
    assert e.value.code=='NOT_FOUND'


def test_archive_daily_cap_cannot_be_reset_by_new_search(store,sid):
    store.cfg.aron_global_daily_requests=2
    store.count_request(sid,'alice',source='aron')
    store.count_request(sid,'alice',source='aron')
    second=store.create('alice',SearchQuery(raw_query='second'))['research_id']
    with pytest.raises(ResearchError) as e:store.count_request(second,'alice',source='aron')
    assert e.value.code=='BUDGET_EXCEEDED' and e.value.details['scope']=='global:aron'
    assert store.usage(second,'alice')['archive_requests']==0
    assert store.usage(second,'alice')['today']['global:aron']=={'metric':'requests','used':2,'limit':2,'remaining':0}
    store.count_request(second,'alice',source='acta_publica')     # other applications are unaffected


def test_searches_are_not_rationed(store):
    for n in range(30):store.create('alice',SearchQuery(raw_query=f'search {n}'))


def test_optimistic_revision_and_idempotency(store,sid):
    a=store.record_finding(sid,'alice',{'kind':'next_step','text':'x'},'one')
    b=store.record_finding(sid,'alice',{'kind':'next_step','text':'x'},'one')
    assert a['observation_id']==b['observation_id'] and b['deduplicated']
    with pytest.raises(ResearchError):store.record_finding(sid,'alice',{'kind':'next_step','text':'y'},'one')
    for _ in range(3):store.count_request(sid,'alice')
    store.update(sid,'alice',SearchQuery(raw_query='revised'),1)
    assert store.observations(sid,'alice')[0]['query_revision']==1
    assert store.usage(sid,'alice')['archive_requests']==3
    with pytest.raises(ResearchError) as e:store.update(sid,'alice',SearchQuery(raw_query='conflict'),1)
    assert e.value.code=='REVISION_CONFLICT'


def test_research_never_expires_by_default(store,sid,cfg):
    # The user's own genealogy research on their own disk: kept until they delete it.
    p=cfg.data_dir/'private'/sid/'evidence.jpg';p.parent.mkdir(parents=True);p.write_bytes(b'test')
    store.db.execute('UPDATE searches SET touched=? WHERE id=?',(time.time()-86400*3650,sid))
    assert store.search(sid,'alice')['id']==sid
    assert store.cleanup()['deleted_searches']==0 and p.exists()


def test_optional_retention_removes_private_data_and_not_daily_budget(store,sid,cfg):
    cfg.retention_days=30
    for _ in range(3):store.count_request(sid,'alice',source='aron')
    p=cfg.data_dir/'private'/sid/'evidence.jpg';p.parent.mkdir(parents=True);p.write_bytes(b'test')
    store.db.execute('UPDATE searches SET touched=? WHERE id=?',(time.time()-86400*(cfg.retention_days+1),sid))
    with pytest.raises(ResearchError) as e:store.search(sid,'alice')
    assert e.value.code=='SEARCH_EXPIRED'
    assert store.cleanup()['deleted_searches']==1
    assert not p.exists()
    assert store.db.execute("SELECT value FROM daily WHERE scope='global:aron' AND metric='requests'").fetchone()[0]==3


def test_archive_daily_cap_counts_every_request_and_robots(cfg):
    cfg.vca_global_daily_requests=3
    s=Store(cfg)
    try:
        sid=s.create('alice',SearchQuery(raw_query='Synthetic test',event_type='birth',year_from=1840,year_to=1845))['research_id']
        s.count_request(sid,'alice',source='vca_images',archive='vca')
        s.count_request(sid,'alice',source='robots',archive='vca')
        s.count_request(sid,'alice',source='vca',archive='vca')
        with pytest.raises(ResearchError) as e:s.count_request(sid,'alice',source='vca_images',archive='vca')
        assert e.value.code=='BUDGET_EXCEEDED' and e.value.details['scope']=='archive:vca'
        s.count_request(sid,'alice',source='acta_publica',archive='mza')   # other archives are unaffected
    finally:s.close()


def test_book_daily_image_cap_is_independent_of_the_search(cfg):
    cfg.book_daily_images=2
    s=Store(cfg)
    try:
        s.charge_book_image('alice','book-1'); s.charge_book_image('bob','book-1')
        with pytest.raises(ResearchError) as e:s.charge_book_image('alice','book-1')
        assert e.value.code=='BUDGET_EXCEEDED' and e.value.details['scope']=='book:book-1'
        s.charge_book_image('alice','book-2')
        s.link(sid:=s.create('alice',SearchQuery(raw_query='x'))['research_id'],'alice','opened_book','book-1')
        assert s.usage(sid,'alice')['today']=={'book:book-1':{'metric':'images','used':2,'limit':2,'remaining':0}}   # only books of this research
    finally:s.close()
