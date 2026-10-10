import time

from rubrika.cli import start_service
from rubrika.config import Settings
from rubrika.models import SearchQuery
from rubrika.network import SafeSource


async def test_server_start_never_deletes_research(cfg,store,sid):
    # Several servers share ~/Rubrika (each Claude session starts one); research is the user's own and only
    # an explicit `rubrika cleanup` removes it.
    old=store.create('alice',SearchQuery(raw_query='old'))['research_id']
    with store.tx() as db:db.execute('UPDATE searches SET touched=? WHERE id=?',(time.time()-86400*3650,old))
    evidence=cfg.data_dir/'private'/old/'evidence.jpg';evidence.parent.mkdir(parents=True);evidence.write_bytes(b'x')
    service=start_service(cfg)
    try:
        assert store.db.execute('SELECT count(*) FROM searches WHERE id IN (?,?)',(sid,old)).fetchone()[0]==2
        assert evidence.exists()
    finally:
        await service.close();service.store.close()


async def test_server_start_trims_the_cache(cfg,store):
    cache=SafeSource(cfg,store).cache_dir;cache.mkdir(parents=True,exist_ok=True)
    stale=cache/'stale.bin';stale.write_bytes(b'x')
    past=time.time()-cfg.metadata_ttl-10
    import os;os.utime(stale,(past,past))
    service=start_service(cfg)
    try: assert not stale.exists()
    finally: await service.close();service.store.close()


async def test_failed_housekeeping_does_not_stop_the_server(cfg,store,monkeypatch,capsys):
    def boom(self): raise OSError('disk busy')
    monkeypatch.setattr(SafeSource,'cleanup_cache',boom)
    service=start_service(cfg)
    try: assert 'disk busy' in capsys.readouterr().err
    finally: await service.close();service.store.close()


def test_cache_cleanup_skips_files_that_vanish(cfg,store):
    # Another server on the same data dir may delete a cache file between listing and stat.
    source=SafeSource(cfg,store);source.cache_dir.mkdir(parents=True,exist_ok=True)
    (source.cache_dir/'gone.bin').symlink_to(source.cache_dir/'missing-target')
    assert source.cleanup_cache()==0


def test_research_never_expires_by_default():
    assert Settings(_env_file=None).retention_days is None
