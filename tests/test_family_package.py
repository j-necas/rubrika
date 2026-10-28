"""save_family_package: a new folder with GEDCOM linked to the cited crops, a self-contained HTML page and a ZIP."""
import zipfile
from pathlib import Path

import pytest

from conftest import call, open_demo

pytestmark = pytest.mark.asyncio


async def finding(service, sid, pages, text, key):
    view = await call(service, 'view_scan', research_id=sid, page_id=pages['pages'][0]['page_id'])
    image = view.data['image']['image_id']
    zoom = await call(service, 'zoom_scan', research_id=sid, image_id=image, bbox_px=dict(x=10, y=10, width=200, height=120))
    rec = await call(service, 'record_finding', research_id=sid, idempotency_key=key,
                     observation=dict(kind='record', image_id=zoom.data['image']['image_id'], text=text))
    return rec.data['observation_id'], zoom.data['image']['image_id']


def tree(sid, oid, oid2):
    return {'title': 'Linie <Dvořák>', 'root_person': 'jan', 'persons': [
        {'id': 'jan', 'given_name': 'Jan', 'surname': 'Dvořák', 'events': [
            {'type': 'baptism', 'date': {'year': 1841}, 'place': 'Demo Lhota', 'confidence': 'documented',
             'sources': [{'research_id': sid, 'observation_id': oid}]}]},
        {'id': 'josef', 'given_name': 'Josef', 'surname': 'Dvořák', 'events': [
            {'type': 'death', 'confidence': 'documented', 'sources': [{'research_id': sid, 'observation_id': oid2}]},
            {'type': 'occupation', 'value': 'chalupník', 'confidence': 'probable'}]}],
        'families': [{'id': 'f1', 'husband': 'josef', 'children': ['jan']}]}


async def test_package_holds_gedcom_crops_html_and_zip(auto_service, cfg):
    service, _ = auto_service
    sid, pages = await open_demo(service)
    oid, image = await finding(service, sid, pages, 'Joannes <script>alert(1)</script>', 'k1')
    oid2, _ = await finding(service, sid, pages, 'Josephus obiit', 'k2')
    out = (await call(service, 'save_family_package', tree=tree(sid, oid, oid2))).data
    folder = Path(out['folder'])
    assert folder.parent == cfg.data_dir / 'export' and folder.name.startswith('linie-dvorak-')
    assert out['crops'] == 2 and out['privacy'] and out['summary']['citations'] == 2
    assert sorted(p.name for p in (folder / 'vyrezy').iterdir()) == ['001.jpg', '002.jpg']
    saved = service.store.image(sid, 'alice', image)
    assert (folder / 'vyrezy' / '001.jpg').read_bytes() == Path(saved['file']).read_bytes()

    ged = Path(out['gedcom_file']).read_text(encoding='utf-8')
    assert ged.startswith('0 HEAD') and ged.count('3 OBJE\n4 FILE vyrezy/') == 2 and '5 FORM jpg' in ged
    assert 'SYNTETICKÁ TESTOVACÍ DATA' in ged

    page = Path(out['html_file']).read_text(encoding='utf-8')
    assert '<script>' not in page and '&lt;script&gt;' in page and 'Linie &lt;Dvořák&gt;' in page
    assert page.count('src="data:image/jpeg;base64,') == 2 and "default-src 'none'" in page
    assert 'Kde linie končí' in page and 'SYNTETICKÁ TESTOVACÍ DATA' in page
    assert page.index('Jan Dvořák (kořen)') < page.index('Josef Dvořák')

    with zipfile.ZipFile(out['zip']) as z:
        names = set(z.namelist())
        assert {f'{folder.name}/vyrezy/001.jpg', f'{folder.name}/{Path(out["gedcom_file"]).name}',
                f'{folder.name}/{Path(out["html_file"]).name}'} <= names
        assert z.read(f'{folder.name}/vyrezy/001.jpg') == (folder / 'vyrezy' / '001.jpg').read_bytes()


async def test_package_never_overwrites_and_documented_only_drops_crops(auto_service):
    service, _ = auto_service
    sid, pages = await open_demo(service)
    oid, _ = await finding(service, sid, pages, 'Joannes', 'k1')
    t = tree(sid, oid, oid)
    first = (await call(service, 'save_family_package', tree=t)).data
    second = (await call(service, 'save_family_package', tree=t, variant='documented_only')).data
    assert first['folder'] != second['folder'] and Path(first['zip']).exists() and Path(second['zip']).exists()
    assert second['crops'] == 1 and 'chalupník' not in Path(second['gedcom_file']).read_text(encoding='utf-8')


async def test_package_refuses_a_tampered_crop_and_other_owners(auto_service):
    service, _ = auto_service
    sid, pages = await open_demo(service)
    oid, image = await finding(service, sid, pages, 'Joannes', 'k1')
    t = tree(sid, oid, oid)
    other = await service.call('save_family_package', 'bob', dict(tree=t))
    assert other.is_error and other.data['error']['code'] == 'NOT_FOUND'
    saved = Path(service.store.image(sid, 'alice', image)['file'])
    saved.write_bytes(saved.read_bytes() + b'x')
    out = await service.call('save_family_package', 'alice', dict(tree=t))
    assert out.is_error and out.data['error']['code'] == 'INCOMPLETE_IMAGE'
    assert not (service.cfg.data_dir / 'export').exists()   # checked before anything is written


async def test_list_archives_tells_the_version_without_network(auto_service):
    from rubrika import __version__
    service, archive = auto_service
    out = (await call(service, 'list_archives')).data
    assert out['rubrika_version'] == __version__ and out['releases_url'].startswith('https://github.com/')
