"""Present-day district → archive holding its parish registers. Registers were split by the regions of 1960–1990,
not by today's regions (e.g. Havlíčkův Brod → Hradec Králové, Pelhřimov → Třeboň). The first archive is primary;
a second one is listed where parishes or later boundary changes cross the old regional border.
Border cases are unverified against the archives' own coverage lists."""
from __future__ import annotations

import re

from .catalog import normalize

_REGIONS = {
    "soapraha": ("Benešov", "Beroun", "Kladno", "Kolín", "Kutná Hora", "Mělník", "Mladá Boleslav", "Nymburk",
                 "Praha-východ", "Praha-západ", "Příbram", "Rakovník"),
    "trebon": ("České Budějovice", "Český Krumlov", "Jindřichův Hradec", "Pelhřimov", "Písek", "Prachatice",
               "Strakonice", "Tábor"),
    "pf": ("Domažlice", "Cheb", "Karlovy Vary", "Klatovy", "Plzeň-město", "Plzeň-jih", "Plzeň-sever", "Rokycany",
           "Sokolov", "Tachov"),
    "soalitomerice": ("Česká Lípa", "Děčín", "Chomutov", "Jablonec nad Nisou", "Liberec", "Litoměřice", "Louny",
                      "Most", "Teplice", "Ústí nad Labem"),
    "vca": ("Havlíčkův Brod", "Hradec Králové", "Chrudim", "Jičín", "Náchod", "Pardubice", "Rychnov nad Kněžnou",
            "Semily", "Svitavy", "Trutnov", "Ústí nad Orlicí"),
    "mza": ("Blansko", "Brno-město", "Brno-venkov", "Břeclav", "Hodonín", "Jihlava", "Kroměříž", "Prostějov",
            "Třebíč", "Uherské Hradiště", "Vyškov", "Zlín", "Znojmo", "Žďár nad Sázavou"),
    "zao": ("Bruntál", "Frýdek-Místek", "Jeseník", "Karviná", "Nový Jičín", "Olomouc", "Opava", "Ostrava-město",
            "Přerov", "Šumperk", "Vsetín"),
    "ahmp": ("Praha",),
}
_SECONDARY = {
    "Havlíčkův Brod": "mza", "Pelhřimov": "mza", "Jihlava": "trebon", "Žďár nad Sázavou": "vca", "Svitavy": "mza",
    "Semily": "soalitomerice", "Prostějov": "zao", "Přerov": "mza", "Vsetín": "mza", "Rakovník": "pf",
    "Praha-východ": "ahmp", "Praha-západ": "ahmp", "Praha": "soapraha",
}
DISTRICTS: dict[str, tuple[str, ...]] = {
    d: (code, _SECONDARY[d]) if d in _SECONDARY else (code,) for code, ds in _REGIONS.items() for d in ds}
_ALIASES = {"brno": "Brno-město", "plzen": "Plzeň-město", "ostrava": "Ostrava-město", "gottwaldov": "Zlín",
            "hlavni mesto praha": "Praha"}


def _key(name: str) -> str:
    text = normalize(name).replace("-", " ")
    text = re.sub(r"^(okres|district)\s+", "", text).strip()
    return re.sub(r"^praha\s+\d{1,2}$", "praha", text)


_INDEX = {_key(d): d for d in DISTRICTS}


def district_name(name: str) -> str | None:
    key = _key(name)
    return _INDEX.get(key) or _ALIASES.get(key)


# Indication sketches (stable cadastre, 1820s-1840s): the MZA sketch search offers these districts (Moravia and the Moravian part of
# Silesia); every other district's sketches are in the ČÚZK archive (link only). Districts that straddle the historic Bohemia-Moravia or
# Moravia-Silesia border list both. Unverified border cases: check the katastr in both.
_SKETCH_MZA = frozenset({
    "Blansko", "Břeclav", "Brno-město", "Brno-venkov", "Bruntál", "Frýdek-Místek", "Hodonín", "Jeseník", "Jihlava", "Jindřichův Hradec",
    "Kroměříž", "Nový Jičín", "Olomouc", "Opava", "Ostrava-město", "Pelhřimov", "Přerov", "Prostějov", "Šumperk", "Svitavy", "Třebíč",
    "Uherské Hradiště", "Ústí nad Orlicí", "Vsetín", "Vyškov", "Žďár nad Sázavou", "Zlín", "Znojmo"})
_SKETCH_MIXED = frozenset({"Jindřichův Hradec", "Pelhřimov", "Ústí nad Orlicí", "Svitavy", "Jihlava", "Žďár nad Sázavou",
                           "Bruntál", "Jeseník", "Opava", "Ostrava-město", "Frýdek-Místek", "Nový Jičín"})


def sketch_archives_for_district(district: str) -> tuple[str, ...]:
    """Archives with the indication sketches of a canonical district: MZA for Moravia, ČÚZK (link) for Bohemia and Silesia."""
    if district not in _SKETCH_MZA:
        return ("cuzk",)
    return ("mza", "cuzk") if district in _SKETCH_MIXED else ("mza",)


def archives_for_district(name: str, kind: str = "parish_registers") -> tuple[str, ...] | None:
    district = district_name(name)
    if not district:
        return None
    if kind == "cadastral_maps":
        return sketch_archives_for_district(district)
    return DISTRICTS.get(district)


def mza_sketch_district(name: str) -> str | None:
    """Canonical district name as the MZA sketch search spells it, or None when MZA has no sketches for that district."""
    district = district_name(name)
    return district if district in _SKETCH_MZA else None
