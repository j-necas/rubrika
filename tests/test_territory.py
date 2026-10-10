import pytest
from rubrika.territory import DISTRICTS, archives_for_district
from rubrika.archives import ARCHIVES

def test_every_district_has_known_archives():
    assert len(DISTRICTS)==77          # 76 okresů + Praha
    for name,codes in DISTRICTS.items():
        assert codes and all(c in ARCHIVES for c in codes) and len(set(codes))==len(codes), name

@pytest.mark.parametrize('query,primary',[
    ('Hradec Králové','vca'),('Havlickuv Brod','vca'),('okres Semily','vca'),('Pelhřimov','trebon'),
    ('Plzeň-jih','pf'),('okres Plzeň-jih','pf'),('plzen jih','pf'),('Plzeň','pf'),('Brno','mza'),('Brno-venkov','mza'),
    ('Znojmo','mza'),('Ostrava','zao'),('Jeseník','zao'),('Liberec','soalitomerice'),('Kladno','soapraha'),
    ('Praha','ahmp'),('Praha 1','ahmp'),('Hlavní město Praha','ahmp'),('Zlín','mza'),('Gottwaldov','mza'),
])
def test_routing(query,primary):
    assert archives_for_district(query)[0]==primary

def test_border_districts_have_a_secondary_archive():
    assert archives_for_district('Havlíčkův Brod')==('vca','mza')
    assert archives_for_district('Pelhřimov')==('trebon','mza')
    assert archives_for_district('Semily')==('vca','soalitomerice')

def test_unknown_district():
    assert archives_for_district('Atlantida') is None


@pytest.mark.parametrize('query,archives',[
    ('Znojmo',('mza',)),('Brno',('mza',)),('Prostějov',('mza',)),('Jihlava',('mza','cuzk')),('Ústí nad Orlicí',('mza','cuzk')),
    ('Opava',('mza','cuzk')),('Karviná',('cuzk',)),('Tábor',('cuzk',)),('Plzeň-jih',('cuzk',)),('Praha',('cuzk',)),('Hradec Králové',('cuzk',)),
])
def test_cadastral_maps_go_to_mza_for_moravia_and_to_a_cuzk_link_elsewhere(query,archives):
    got=archives_for_district(query,'cadastral_maps')
    assert got==(archives if isinstance(archives,tuple) else (archives,))


def test_cadastral_maps_unknown_district_and_default_kind_unchanged():
    assert archives_for_district('Atlantida','cadastral_maps') is None
    assert archives_for_district('Znojmo')==('mza',) and archives_for_district('Tábor')==('trebon',)


def test_mza_sketch_district_spelling():
    from rubrika.territory import mza_sketch_district
    assert mza_sketch_district('okres Brno-město')=='Brno-město' and mza_sketch_district('brno')=='Brno-město'
    assert mza_sketch_district('Tábor') is None and mza_sketch_district('Karviná') is None and mza_sketch_district('Atlantida') is None
