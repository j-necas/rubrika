"""Registry of archives Rubrika knows: hosts, allowlisted routes, switches, robots mode, pace and daily caps.
An archive without kinds is link-only: Rubrika names it and links to it but never fetches from it."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .sources import (SKETCH_APP, AHMP_HOST, AHMP_IMAGES, NA_HOST, PF_HOST, PRIHLASKY_HOST, Route, SOAPRAHA_HOST, TREBON_HOST, VCA_HOST, VCA_IMAGES, match_ahmp_route,
                      match_na_route, match_pf_route, match_prihlasky_route, match_route, match_soapraha_route, match_trebon_route, match_vca_route)

Kind = Literal["parish_registers", "land_records", "census", "cadastral_maps"]
KINDS = ("parish_registers", "land_records", "census", "cadastral_maps")
NOT_YET = "Zatím není v Rubrice napojen; otevřete archiv v prohlížeči."
BOT_GATE = ("Archiv automatizovaný přístup blokuje ochranou proti botům; Rubrika ji neobchází. "
            "Matriku otevřete v prohlížeči.")


@dataclass(frozen=True)
class Archive:
    code: str
    name: str                    # full name for citations and GEDCOM
    app: str                     # the archive's application
    homepage: str
    terms_url: str
    kinds: tuple[str, ...] = ()
    hosts: frozenset[str] = frozenset()
    route: Callable[[str, str, str, str], Route | None] | None = None   # (host, path, query, method)
    enabled_setting: str | None = None           # Settings attribute; None = only RUBRIKA_LIVE_ENABLED
    kind_settings: tuple[tuple[str, str], ...] = ()   # (kind, Settings attribute) switches inside one archive
    daily_requests_setting: str | None = None    # Settings attribute: cap on every HTTP request to the archive
    robots_mode: Literal["honor", "user_triggered"] = "honor"
    min_interval: float = 0.0
    full_res_captcha: bool = False               # the app offers the whole scan at full resolution only behind a captcha
    link_reason: str | None = None
    note: str = ""
    auto_search: bool = True           # False: searched only when asked for (district of its territory or archives=[code])
    request_headers: tuple[tuple[str, str], ...] = ()   # sent with every request to this archive (a normal browser header, e.g. Accept-Language)
    terms_notice: str | None = None    # shown to the user next to this archive's results (list_archives, find_place, open_source)
    allow_plain_http: bool = False     # the archive's host offers no TLS: its addresses use http:// (and only http://); checked in SourcePolicy

    def __post_init__(self):
        # Only a normal browser header that picks the language may be sent to an archive; nothing that could get around its protection.
        if any(name != "Accept-Language" for name, _ in self.request_headers):
            raise ValueError("Archive.request_headers may contain only Accept-Language")

    @property
    def link_only(self) -> bool:
        """Named and linked, never fetched from: no kinds at all, or kinds it holds but no route Rubrika may use."""
        return not self.kinds or self.route is None
PF_TERMS_NOTICE = ("Porta fontium (SOA Plzeň): digitální kopie smíte mimo portál používat jen podle § 92 autorského zákona, "
                   "tedy pro osobní potřebu, pro vědecké nebo vyučovací účely, nevýdělečně a s uvedením zdroje včetně "
                   "internetové adresy (u každého dokladu viewer_url). Snímky nezveřejňujte ani nešiřte. "
                   "Podmínky: https://www.portafontium.eu/terms-of-use")
TREBON_TERMS_NOTICE = ("SOA Třeboň (DigiArchiv), omezený režim. Podle archivu jsou snímky zveřejněny jen k prohlížení v DigiArchivu SOA Třeboň; "
                       "kopírování, zveřejňování, prodej nebo užití v jiných projektech či na jiných webech je zakázáno a další užití vyžaduje souhlas "
                       "vlastníků archiválií. Rubrika proto z DigiArchivu žádné snímky nestahuje: automaticky čte jen katalog (farnosti, knihy) a dává "
                       "odkazy (viewer_url). Snímek si uživatel otevře ve svém prohlížeči, uloží ho (nebo snímek obrazovky) a načte nástrojem import_scan "
                       "s citací; za jakékoli užití snímku podle podmínek archivu odpovídá uživatel (souhlas lze žádat u archivu, digi@ceskearchivy.cz). "
                       "U každého dokladu uvádějte odkaz na archiv. Podmínky: https://digi.ceskearchivy.cz/")
PRIHLASKY_TERMS_NOTICE = ("Policejní přihlášky Prahy 1850–1914 (Národní archiv) jsou na serveru digi.nacr.cz dostupné jen přes nešifrované HTTP: "
                          "hledaná jména putují sítí čitelně a stránku by mohl cestou někdo změnit (obsah Rubrika bere jako nedůvěryhodná data). "
                          "Robots.txt serveru zakazuje celý web; Rubrika ho u dotazů, které uživatel spustil, toleruje, čte pomalu (2 s) a "
                          "žádnou ochranu neobchází. Archiv nezveřejňuje podmínky užití; citujte podle archivu (karton, obraz) a uvádějte odkaz "
                          "(viewer_url). Opravy čtení hlásí archiv e-mailem uvedeným na stránce aplikace.")


SKETCH_TERMS_NOTICE = ("Indikační skici MZA (fond D 9): podle podmínek služby (https://www.mza.cz/indikacniskici/page/view/podminky-uziti-sluzeb) "
                       "jsou digitalizované materiály určené k prohlížení; bez písemného souhlasu MZA je zakázáno je umísťovat na jiné internetové "
                       "stránky nebo je jakkoli publikovat a zasahovat do technické podstaty stránek. Rubrika skici čte jen na pokyn uživatele, "
                       "výřezy ukazuje jen v jeho konverzaci a nevytváří žádnou hromadnou kopii; u každého dokladu uvádějte odkaz na archiv (viewer_url).")
CUZK_REASON = ("Archiv ČÚZK (Archiv map, ags.cuzk.gov.cz/archiv) nabízí indikační skici a císařské otisky Čech, ale jeho mapové služby vyžadují "
               "přístupový token, který ČÚZK vydává jen vlastní aplikaci; Rubrika ho nezískává ani neobchází. Data (JPEG listů) ČÚZK vydává "
               "za poplatek přes internetový obchod Geoportálu. Otevřete https://ags.cuzk.gov.cz/archiv/ v prohlížeči, najděte místo v mapě "
               "(vyhledání adresy nebo obce, případně klik do mapy) a zapněte vrstvu indikačních skic nebo císařských otisků; katastrální území "
               "poznáte podle hranic v mapě.")


def _mza_route(host, path, query, method):
    if path.startswith(SKETCH_APP + "/") and host not in ("www.mza.cz", "mza.cz"):
        return None              # the sketch application lives on the web host only, not on the image server
    return match_route(path, query, method)


ARCHIVES: dict[str, Archive] = {a.code: a for a in (
    Archive("mza", "Moravský zemský archiv v Brně", "Acta Publica, ARON, Sčítací operáty", "https://www.mza.cz/",
            "https://www.mza.cz/aplikace/podminky-pouziti-aplikace-aron/", KINDS,
            frozenset({"www.mza.cz", "mza.cz", "iipserver.mza.cz"}), _mza_route,
            kind_settings=(("land_records", "aron_enabled"), ("census", "census_enabled"), ("cadastral_maps", "sketches_enabled"))),
    Archive("vca", "Státní oblastní archiv v Hradci Králové", "ARchiv ONline", f"https://{VCA_HOST}/",
            "https://soahradec.gov.cz/napoveda-archivu-online/co-v-archivu-online-najdete/",
            ("parish_registers", "land_records", "census"),
            frozenset({VCA_HOST, VCA_IMAGES}), match_vca_route, enabled_setting="vca_enabled",
            daily_requests_setting="vca_global_daily_requests"),
    Archive("pf", "Státní oblastní archiv v Plzni", "Porta fontium", f"https://{PF_HOST}/",
            "https://www.portafontium.eu/terms-of-use", ("parish_registers", "land_records", "census"), frozenset({PF_HOST}), match_pf_route,
            enabled_setting="pf_enabled", kind_settings=(("land_records", "pf_land_records_enabled"), ("census", "pf_census_enabled")), auto_search=False, daily_requests_setting="pf_global_daily_requests", terms_notice=PF_TERMS_NOTICE,
            note="Sbírka matrik západních Čech (Plzeňský a Karlovarský kraj), úřední knihy (pozemkové knihy, urbáře) a sčítání lidu; pomalé tempo, robots.txt žádá 10 s mezi požadavky."),
    Archive("soapraha", "Státní oblastní archiv v Praze", "eBadatelna", f"https://{SOAPRAHA_HOST}/",
            "https://soapraha.gov.cz/probadatele/", ("parish_registers", "land_records", "census"), frozenset({SOAPRAHA_HOST}), match_soapraha_route,
            enabled_setting="soapraha_enabled", daily_requests_setting="soapraha_global_daily_requests",
            kind_settings=(("land_records", "soapraha_land_records_enabled"), ("census", "soapraha_census_enabled")),
            robots_mode="honor", min_interval=1.0,
            note="Sbírka matrik a průvodní listinný materiál, Středočeský kraj (NAD 28); místa se hledají v seznamu lokalit archivu, snímky jsou celé JPEG bez výřezů na straně "
                 "archivu (jedno stažení 1 až 8 MB na snímek); podmínky užití eBadatelna nezveřejňuje, při užití citujte archiv a fond. Ze záložky Archival records "
                 "čte také sčítání lidu 1869 až 1910 (sčítací operáty okresních úřadů, po obcích a číslech popisných) a pozemkové knihy měst a velkostatků; "
                 "každý z těch druhů vyžaduje vlastní zapnutí (RUBRIKA_SOAPRAHA_CENSUS_ENABLED, RUBRIKA_SOAPRAHA_LAND_RECORDS_ENABLED)."),
    Archive("trebon", "Státní oblastní archiv v Třeboni", "DigiArchiv", f"https://{TREBON_HOST}/",
            f"https://{TREBON_HOST}/", ("parish_registers", "land_records", "census"), frozenset({TREBON_HOST}), match_trebon_route,
            enabled_setting="trebon_enabled", auto_search=False,
            kind_settings=(("land_records", "trebon_land_records_enabled"), ("census", "trebon_census_enabled")), daily_requests_setting="trebon_global_daily_requests",
            robots_mode="honor", min_interval=2.0, terms_notice=TREBON_TERMS_NOTICE,
            request_headers=(("Accept-Language", "cs-CZ,cs;q=0.9"),),      # the site picks its language by this header, not by lang=cs
            note="Omezený režim: katalog farností (římskokatolických i nekatolických a civilních matrik), knih, pozemkových knih, vrchnostenských knih, urbářů a sčítání a odkazy na snímky (viewer_url) automaticky, snímky Rubrika z DigiArchivu nestahuje (archiv je dovoluje jen prohlížet); snímek uživatel uloží z prohlížeče a načte nástrojem import_scan. Obec, která není sídlem farnosti, hledejte podle názvu farnosti; vyžaduje ruční zapnutí (RUBRIKA_TREBON_ENABLED)."),
    Archive("ahmp", "Archiv hlavního města Prahy", "Archivní katalog", "https://katalog.ahmp.cz/pragapublica/",
            "https://www.ahmp.cz/index.html?mid=87", ("parish_registers", "census"),
            frozenset({AHMP_HOST, AHMP_IMAGES}), match_ahmp_route,
            enabled_setting="ahmp_enabled", daily_requests_setting="ahmp_global_daily_requests",
            robots_mode="user_triggered", min_interval=2.0, full_res_captcha=True,
            note="Sbírka matrik 1584–1944, Soupis pražského obyvatelstva 1830–1910 a sčítání lidu 1869–1890; katalog i snímky vyžadují ruční zapnutí (RUBRIKA_AHMP_ENABLED)."),
    Archive("na", "Národní archiv", "Archivní VadeMeCum", "https://vademecum.nacr.cz/vademecum/",
            "https://na.gov.cz/verejnost/badatelna/badatelsky-rad", ("parish_registers",),
            frozenset({NA_HOST}), match_na_route,
            enabled_setting="na_enabled", daily_requests_setting="na_global_daily_requests",
            robots_mode="user_triggered", min_interval=2.0, full_res_captcha=True,
            note="Židovské matriky 1784–1949 z Čech, Moravy a části Slezska (fond HBMa, NAD 167); hledají se jen na výslovnou "
                 "žádost (archives=[\"na\"]), katalog i snímky vyžadují ruční zapnutí (RUBRIKA_NA_ENABLED)."),
    Archive("na_prihlasky", "Národní archiv", "Policejní přihlášky Prahy 1850–1914", "http://digi.nacr.cz/prihlasky2/",
            "https://na.gov.cz/verejnost/badatelna/digitalni-badatelna/konskripce-1850-1914", ("census",),
            frozenset({PRIHLASKY_HOST}), match_prihlasky_route,
            enabled_setting="prihlasky_enabled", daily_requests_setting="prihlasky_global_daily_requests",
            robots_mode="user_triggered", min_interval=2.0, auto_search=False, allow_plain_http=True,
            terms_notice=PRIHLASKY_TERMS_NOTICE,
            note="Pobytové přihlášky pražského policejního ředitelství 1850–1914 (fond Policejní ředitelství Praha I – konskripce), "
                 "celé domácnosti, sken je jeden JPEG 1000×1333 px; server nabízí jen HTTP. Hledá se podle příjmení (person.surname) jen na "
                 "výslovnou žádost (archives=[\"na_prihlasky\"]); vyžaduje ruční zapnutí (RUBRIKA_PRIHLASKY_ENABLED)."),
    Archive("cuzk", "Český úřad zeměměřický a katastrální (Archiv map)", "Archiv (ags.cuzk.gov.cz/archiv)", "https://ags.cuzk.gov.cz/archiv/",
            "https://geoportal.cuzk.gov.cz/Dokumenty/Podminky.pdf", ("cadastral_maps",), link_reason=CUZK_REASON,
            note="Jen odkaz: indikační skici a císařské otisky Čech, Moravy i Slezska v jedné mapové aplikaci; Rubrika ji nevolá."),
    Archive("zao", "Zemský archiv v Opavě", "Digitální badatelna", "https://digi.archives.cz/da/",
            "https://www.archives.cz/web/digitalni_badatelna/", link_reason=BOT_GATE),
    Archive("soalitomerice", "Státní oblastní archiv v Litoměřicích", "Digitální badatelna",
            "https://digi.soalitomerice.cz/digitalnibadatelna/", "https://www.soalitomerice.cz/", link_reason=BOT_GATE),
)}


def archive_for_host(host: str | None) -> Archive | None:
    return next((a for a in ARCHIVES.values() if host in a.hosts), None)


def integrated_hosts() -> list[str]:
    return sorted(h for a in ARCHIVES.values() if a.route for h in a.hosts)


def archive_of(source_id: str) -> str:
    """Archive of a stored ID. Pre-0.8 MZA IDs (numbers, aron:, census:, page_) carry no archive prefix."""
    prefix = source_id.split(":", 1)[0] if isinstance(source_id, str) and ":" in source_id else ""
    return prefix if prefix in ARCHIVES and prefix != "mza" else "mza"
