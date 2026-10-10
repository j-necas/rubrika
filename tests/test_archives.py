import pytest
from rubrika import archives as A
from rubrika.config import Settings
from rubrika.errors import ResearchError
from rubrika.network import SourcePolicy

UUID='00000000-0000-4000-8000-0000000000a1'

def test_registry_codes():
    assert set(A.ARCHIVES)=={'mza','vca','pf','soapraha','trebon','ahmp','na','na_prihlasky','cuzk','zao','soalitomerice'}
    assert A.ARCHIVES['mza'].kinds==('parish_registers','land_records','census','cadastral_maps')
    assert dict(A.ARCHIVES['mza'].kind_settings)['cadastral_maps']=='sketches_enabled'
    for code in ('zao','soalitomerice'):
        a=A.ARCHIVES[code]
        assert a.link_only and a.homepage.startswith('https://') and a.link_reason and not a.hosts
    cuzk=A.ARCHIVES['cuzk']
    assert cuzk.kinds==('cadastral_maps',) and cuzk.link_only and not cuzk.hosts and cuzk.route is None
    assert 'token' in cuzk.link_reason and 'poplatek' in cuzk.link_reason and cuzk.homepage=='https://ags.cuzk.gov.cz/archiv/'
    na=A.ARCHIVES['na']
    assert na.kinds==('parish_registers',) and not na.link_only and na.link_reason is None
    assert na.hosts=={'vademecum.nacr.cz'} and na.enabled_setting=='na_enabled' and na.daily_requests_setting=='na_global_daily_requests'
    assert na.robots_mode=='user_triggered' and na.min_interval==2.0 and na.full_res_captcha
    ahmp=A.ARCHIVES['ahmp']
    assert ahmp.kinds==('parish_registers','census') and not ahmp.link_only and ahmp.link_reason is None
    assert ahmp.hosts=={'katalog.ahmp.cz','images.ahmp.cz'} and ahmp.enabled_setting=='ahmp_enabled'
    assert ahmp.robots_mode=='user_triggered' and ahmp.min_interval==2.0 and ahmp.full_res_captcha
    assert {c for c,a in A.ARCHIVES.items() if a.robots_mode!='honor'}=={'ahmp','na','na_prihlasky'}
    assert A.ARCHIVES['vca'].kinds==('parish_registers','land_records','census') and A.ARCHIVES['vca'].link_reason is None and not A.ARCHIVES['vca'].link_only
    assert A.ARCHIVES['vca'].daily_requests_setting=='vca_global_daily_requests'

def test_prihlasky_registry_entry():
    p=A.ARCHIVES['na_prihlasky']
    assert p.kinds==('census',) and not p.link_only and p.link_reason is None and p.name=='Národní archiv'
    assert p.hosts=={'digi.nacr.cz'} and p.enabled_setting=='prihlasky_enabled' and p.daily_requests_setting=='prihlasky_global_daily_requests'
    assert p.robots_mode=='user_triggered' and p.min_interval==2.0 and p.allow_plain_http and not p.auto_search and not p.full_res_captcha
    assert p.homepage.startswith('http://digi.nacr.cz/') and 'HTTP' in p.terms_notice and 'robots' in p.terms_notice.lower()
    assert [c for c,a in A.ARCHIVES.items() if a.allow_plain_http]==['na_prihlasky']
    na=A.ARCHIVES['na']
    assert na.kinds==('parish_registers',) and not na.allow_plain_http and na.hosts=={'vademecum.nacr.cz'}
    assert A.archive_for_host('digi.nacr.cz').code=='na_prihlasky' and A.archive_of('na_prihlasky:form:103:220')=='na_prihlasky'

def test_hosts_resolve_to_archives():
    assert A.archive_for_host('www.mza.cz').code=='mza'
    assert A.archive_for_host('imgx.vychodoceskearchivy.cz').code=='vca'
    assert A.archive_for_host('storage.vychodoceskearchivy.cz') is None
    assert A.archive_for_host('www.portafontium.eu').code=='pf'
    assert set(A.integrated_hosts())=={'www.mza.cz','mza.cz','iipserver.mza.cz',
                                       'aron.vychodoceskearchivy.cz','imgx.vychodoceskearchivy.cz','www.portafontium.eu',
                                       'images.ahmp.cz','katalog.ahmp.cz','vademecum.nacr.cz','ebadatelna.soapraha.cz','digi.ceskearchivy.cz','digi.nacr.cz'}

@pytest.mark.parametrize('url,method,source,group',[
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/'+UUID,'GET','vca','vca'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/script/citation/'+UUID,'GET','vca','vca'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/listview?listType=ARCH_DESC','POST','vca','vca'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/listview?listType=ENTITY','POST','vca','vca'),
    ('https://imgx.vychodoceskearchivy.cz/tiles/00/00/'+UUID+'/image.dzi','GET','vca_images','vca_images'),
    ('https://imgx.vychodoceskearchivy.cz/tiles/00/00/'+UUID+'/image_files/12/3_4.jpg','GET','vca_images','vca_images'),
    ('https://aron.vychodoceskearchivy.cz/robots.txt','GET','robots','vca_robots'),
])
def test_vca_routes_allowed(url,method,source,group):
    route=SourcePolicy(A.integrated_hosts()).validate(url,method)
    assert (route.source,route.group,route.archive)==(source,group,'vca')

@pytest.mark.parametrize('url,method',[
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/listview?listType=FUND','POST'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/listview?listType=ENTITY&x=1','POST'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/listresults?listType=ENTITY','POST'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/me','GET'),
    ('https://aron.vychodoceskearchivy.cz/api/aron/apu/'+UUID+'?x=1','GET'),
    ('https://aron.vychodoceskearchivy.cz/tiles/00/00/'+UUID+'/image.dzi','GET'),
    ('https://imgx.vychodoceskearchivy.cz/api/aron/apu/'+UUID,'GET'),
    ('https://storage.vychodoceskearchivy.cz/matriky/T/x.zip','GET'),
    ('https://imgx.vychodoceskearchivy.cz/tiles/00/00/'+UUID+'/image_files/12/3_4.png','GET'),
    ('https://imgx.vychodoceskearchivy.cz/tiles/00/00/'+UUID+'/image.dzi','POST'),
])
def test_vca_forbidden(url,method):
    with pytest.raises(ResearchError) as e: SourcePolicy(A.integrated_hosts()).validate(url,method)
    assert e.value.code=='INVALID_SOURCE'

def test_mza_routes_are_tagged_mza():
    route=SourcePolicy(A.integrated_hosts()).validate('https://www.mza.cz/actapublica/matrika/hledani?typ=obec&obec_id=1')
    assert route.archive=='mza' and route.source=='acta_publica'

def test_settings_defaults():
    from rubrika import __version__
    s=Settings(_env_file=None)
    assert s.vca_enabled is False and s.vca_global_daily_requests==2000 and s.book_daily_images==150
    assert s.max_crawl_delay==60.0
    assert s.user_agent==f'rubrika/{__version__} (+https://github.com/j-necas/rubrika)'
    assert set(s.allowed_hosts)==set(A.integrated_hosts())


@pytest.mark.parametrize('sid,code',[('900001','mza'),('aron:00000000-0000-4000-8000-0000000000a1','mza'),
    ('census:4422','mza'),('census:obec:Brno','mza'),('page_abc','mza'),
    ('vca:unit:00000000-0000-4000-8000-0000000000a1','vca'),('vca:unit:00000000-0000-4000-8000-0000000000a1:3','vca'),
    ('pf:register:1','pf'),('unknown:x','mza')])
def test_archive_of(sid,code):
    assert A.archive_of(sid)==code


PF='https://www.portafontium.eu'
SEARCH=PF+'/searching/register?search_api_views_fulltext=&search_api_views_fulltext_1=Plze%C5%88&search_api_views_fulltext_2=&search_api_aggregation_3=&field_doc_dates_field_doc_dates_from='

def test_pf_registry_entry():
    a=A.ARCHIVES['pf']
    assert a.kinds==('parish_registers','land_records','census') and not a.link_only and a.link_reason is None
    assert a.hosts==frozenset({'www.portafontium.eu'}) and a.enabled_setting=='pf_enabled'
    assert a.daily_requests_setting=='pf_global_daily_requests' and a.robots_mode=='honor'
    assert a.terms_url=='https://www.portafontium.eu/terms-of-use'
    assert '§ 92' in a.terms_notice and 'osobní potřeb' in a.terms_notice and 'internetov' in a.terms_notice
    assert all(x.terms_notice is None for x in A.ARCHIVES.values() if x.code not in ('pf','trebon','na_prihlasky'))

def test_pf_settings_defaults():
    s=Settings(_env_file=None)
    assert s.pf_enabled is False and s.pf_global_daily_requests==800
    assert s.pf_land_records_enabled is False and s.pf_census_enabled is False

@pytest.mark.parametrize('url,source,group',[
    (SEARCH,'pf','pf_catalog'),
    (SEARCH+'&page=3','pf','pf_catalog'),
    (SEARCH+'&field_register_type%5B%5D=register-birth&field_register_type%5B%5D=index-birth','pf','pf_catalog'),
    (PF+'/register/soap-pn/abertamy-04','pf','pf_catalog'),
    (PF+'/register/amp/lp-358-1-index-plzen-001','pf','pf_catalog'),
    (PF+'/iipimage/30060004','pf','pf_catalog'),
    (PF+'/iipimage/30060004/abertamy-04_0040-n','pf','pf_catalog'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/abertamy/abertamy-04/abertamy-04_0040-n.jp2/info.json','pf_images','pf_images'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/abertamy/abertamy-04/abertamy-04_0040-n.jp2/0,0,256,256/256,/0/default.jpg','pf_images','pf_images'),
    (PF+'/fcgi-bin/iipsrv.fcgi?DeepZoom=register/soap-pn/abertamy/abertamy-04/abertamy-04_0010-x.jp2.dzi','pf_images','pf_images'),
    (PF+'/robots.txt','robots','pf_robots'),
])
def test_pf_routes_allowed(url,source,group):
    route=SourcePolicy(A.integrated_hosts()).validate(url)
    assert (route.source,route.group,route.archive,route.method)==(source,group,'pf','GET')

@pytest.mark.parametrize('url,method',[
    (SEARCH,'POST'),
    (PF+'/searching/register?search_api_views_fulltext_1=x&order=field_archives','GET'),
    (PF+'/searching/register?search_api_views_fulltext_1=x&search_api_views_fulltext_1=y','GET'),
    (PF+'/searching/register?search_api_views_fulltext_1=x&page=abc','GET'),
    (PF+'/searching/register?search_api_views_fulltext_1=x&field_register_type%5B%5D=evil','GET'),
    (PF+'/searching/census?search_api_views_fulltext_1=x','GET'),        # the census tab lists results only with mode=0
    (PF+'/searching/register/x','GET'),
    (PF+'/register/soap-pn','GET'),
    (PF+'/register/soap-pn/abertamy-04/extra','GET'),
    (PF+'/register/soap-pn/abertamy-04?x=1','GET'),
    (PF+'/register/soap-pn/A%20B','GET'),
    (PF+'/iipimage/abc','GET'),
    (PF+'/iipimage/30060004/../x','GET'),
    (PF+'/iipimage/30060004?download=1','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?FIF=/register/soap-pn/a/b/c.jp2&wid=100&cvt=jpeg','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/a/b/c.jp2/info.json&x=1','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=other/soap-pn/a/b/c.jp2/info.json','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=/etc/passwd','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/../secret/c.jp2/info.json','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/a%2Fb.jp2/info.json','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi','GET'),
    (PF+'/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/a/b/c.jp2/info.json','POST'),
    (PF+'/admin/config','GET'),
    (PF+'/user/login','GET'),
    (PF+'/search/node/x','GET'),
    (PF+'/robots.txt','POST'),
    ('http://www.portafontium.eu/register/soap-pn/abertamy-04','GET'),
])
def test_pf_routes_forbidden(url,method):
    with pytest.raises(ResearchError) as e: SourcePolicy(A.integrated_hosts()).validate(url,method)
    assert e.value.code=='INVALID_SOURCE'


def test_launcher_switches_pf_on_with_the_notice_in_the_registry():
    import importlib.util,pathlib
    spec=importlib.util.spec_from_file_location('main_launcher',pathlib.Path(__file__).parent.parent/'plugin'/'server'/'main.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    assert mod.LOCAL_ENV['RUBRIKA_PF_ENABLED']=='true'
    assert mod.LOCAL_ENV['RUBRIKA_AHMP_ENABLED']=='true' and mod.LOCAL_ENV['RUBRIKA_NA_ENABLED']=='true'
    assert mod.LOCAL_ENV['RUBRIKA_SOAPRAHA_ENABLED']=='true' and mod.LOCAL_ENV['RUBRIKA_TREBON_ENABLED']=='true'
    for key in ('PF_LAND_RECORDS','PF_CENSUS','TREBON_LAND_RECORDS','TREBON_CENSUS','SOAPRAHA_CENSUS','SOAPRAHA_LAND_RECORDS','SKETCHES','PRIHLASKY'):
        assert mod.LOCAL_ENV[f'RUBRIKA_{key}_ENABLED']=='true'

@pytest.mark.asyncio
async def test_list_archives_carries_pf_terms_notice(auto_service):
    service,_=auto_service
    out=await service.call('list_archives','alice',{})
    rows={a['code']:a for a in out.data['archives']}
    assert '§ 92' in rows['pf']['terms_notice'] and rows['pf']['terms_url']=='https://www.portafontium.eu/terms-of-use'
    assert rows['pf']['kinds']==['parish_registers','land_records','census'] and rows['pf']['available_kinds']==[]   # off in the shared test settings
    assert rows['mza']['terms_notice'] is None and rows['vca']['terms_notice'] is None

@pytest.mark.asyncio
async def test_pf_disabled_fetch_is_live_disabled_with_terms(cfg,store,sid):
    import httpx
    from rubrika.network import SafeSource
    source=SafeSource(cfg,store,transport=httpx.MockTransport(lambda r:httpx.Response(500)))
    try:
        with pytest.raises(ResearchError) as e:
            await source.fetch(sid,'alice','https://www.portafontium.eu/register/soap-pn/abertamy-04')
        assert e.value.code=='LIVE_DISABLED' and 'portafontium.eu/terms-of-use' in e.value.message
    finally:await source.close()

def test_iiif_from_deepzoom_serves_mza_and_pf():
    from rubrika.catalog import iiif_from_deepzoom as f
    assert f('https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?Deepzoom=/srv/x/p.jp2.dzi')=='https://iipserver.mza.cz/iipsrv/iipsrv.fcgi?IIIF=/srv/x/p.jp2/info.json'
    assert (f('https://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?DeepZoom=register/soap-pn/a/b/b_0010-x.jp2.dzi')
            =='https://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?IIIF=register/soap-pn/a/b/b_0010-x.jp2/info.json')
    assert f('https://www.portafontium.eu/fcgi-bin/iipsrv.fcgi?DeepZoom=x.jp2.dzi&y=1') is None
    assert f('https://www.portafontium.eu/fcgi-bin/other?DeepZoom=x.jp2.dzi') is None


def test_pf_image_path_rejects_dot_and_empty_segments():
    for bad in ('register//a/b.jp2/info.json','register/./a/info.json','register/a/./info.json','register/a//info.json','register/.hidden/x','register/a/b/'):
        with pytest.raises(ResearchError):
            SourcePolicy(A.integrated_hosts()).validate(PF+'/fcgi-bin/iipsrv.fcgi?IIIF='+bad)

def test_pf_is_not_auto_searched():
    assert A.ARCHIVES['pf'].auto_search is False and all(a.auto_search for c,a in A.ARCHIVES.items() if c not in ('pf','trebon','na_prihlasky'))

def test_tls_context_trusts_the_geant_intermediate_and_verifies():
    import hashlib,ssl
    from rubrika import network as N
    pem=(N.CERTS/'geant-tls-rsa-1.pem').read_text()
    assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()==N.GEANT_TLS_RSA_1_SHA256==(
        '5B:67:8D:C4:40:95:A5:28:95:B6:3B:31:F2:72:27:F4:B3:6C:3E:34:74:91:BF:2B:FA:69:18:37:A5:FB:8C:79'.replace(':','').lower())
    ctx=N.tls_context()
    assert ctx.verify_mode==ssl.CERT_REQUIRED and ctx.check_hostname
    assert any('GEANT TLS RSA 1' in str(c.get('subject')) for c in ctx.get_ca_certs())


def test_soapraha_registry_entry():
    a=A.ARCHIVES['soapraha']
    assert a.kinds==('parish_registers','land_records','census') and not a.link_only and a.link_reason is None
    assert a.kind_settings==(('land_records','soapraha_land_records_enabled'),('census','soapraha_census_enabled'))
    assert a.hosts==frozenset({'ebadatelna.soapraha.cz'}) and a.enabled_setting=='soapraha_enabled'
    assert a.daily_requests_setting=='soapraha_global_daily_requests' and a.robots_mode=='honor' and a.min_interval==1.0
    assert not a.full_res_captcha and a.auto_search and a.terms_notice is None
    assert A.archive_for_host('ebadatelna.soapraha.cz').code=='soapraha'
    assert A.archive_of('soapraha:book:6367')=='soapraha' and A.archive_of('soapraha:book:6367:3')=='soapraha'

def test_soapraha_settings_defaults():
    s=Settings(_env_file=None)
    assert s.soapraha_enabled is False and s.soapraha_global_daily_requests==1000
    assert s.soapraha_census_enabled is False and s.soapraha_land_records_enabled is False
    assert 'ebadatelna.soapraha.cz' in s.allowed_hosts


def test_trebon_registry_entry():
    a=A.ARCHIVES['trebon']
    assert a.kinds==('parish_registers','land_records','census') and not a.link_only and a.link_reason is None
    assert a.kind_settings==(('land_records','trebon_land_records_enabled'),('census','trebon_census_enabled'))
    assert a.hosts==frozenset({'digi.ceskearchivy.cz'}) and a.enabled_setting=='trebon_enabled'
    assert a.daily_requests_setting=='trebon_global_daily_requests' and a.min_interval==2.0
    assert a.robots_mode=='honor' and not a.full_res_captcha and a.auto_search is False
    assert a.terms_url=='https://digi.ceskearchivy.cz/' and A.archive_for_host('digi.ceskearchivy.cz').code=='trebon'
    assert A.archive_of('trebon:book:2711')=='trebon' and A.archive_of('trebon:book:2711:3')=='trebon'
    n=a.terms_notice
    assert 'jen k prohlížení' in n and 'kopírování' in n and 'zveřejňování' in n and 'souhlas' in n
    assert 'omezený režim' in n and 'nestahuje' in n and 'import_scan' in n and 'odpovídá uživatel' in n and 'viewer_url' in n and 'https://digi.ceskearchivy.cz/' in n

def test_trebon_settings_defaults():
    s=Settings(_env_file=None)
    assert s.trebon_enabled is False and s.trebon_global_daily_requests==800
    assert s.trebon_land_records_enabled is False and s.trebon_census_enabled is False
    assert 'digi.ceskearchivy.cz' in s.allowed_hosts


def test_trebon_notice_gives_the_archives_wording_in_czech_and_no_english_original():
    n=A.ARCHIVES['trebon'].terms_notice
    assert 'Podle archivu jsou snímky zveřejněny jen k prohlížení v DigiArchivu SOA Třeboň' in n
    assert 'kopírování, zveřejňování, prodej nebo užití v jiných projektech či na jiných webech je zakázáno' in n
    assert 'Images are published' not in n

def test_only_accept_language_may_be_a_request_header():
    assert dict(A.ARCHIVES['trebon'].request_headers)=={'Accept-Language':'cs-CZ,cs;q=0.9'}
    for bad in ('Referer','Cookie','User-Agent','accept-language'):
        with pytest.raises(ValueError):
            A.Archive('x','X','x','https://x.example/','https://x.example/',request_headers=((bad,'v'),))
    A.Archive('x','X','x','https://x.example/','https://x.example/',request_headers=(('Accept-Language','cs'),))
