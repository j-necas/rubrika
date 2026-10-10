# Bezpečnost a hranice

Rubrika běží jen na počítači uživatele: Claude ji spouští jako lokální MCP server přes
stdio. Server neotevírá žádný síťový port, nemá přihlašování ani vzdálený přístup
a všechna hledání patří jedinému lokálnímu vlastníkovi. Vlastnictví hledání se přesto
kontroluje při každém čtení/zápisu. Nejde o nezávislý audit.

Archivy jsou v registru (`archives.py`); každý má vlastní seznam povolených hostů a cest
(allowlist), vypínač, režim robots.txt, minimální rozestup a denní strop. K MZA jsou povolené jen
HTTPS GET cesty katalogu, robots a obrazového serveru, k VČA cesty popsané níže. Odkazové archivy
(`link_only`, dnes `zao`, `soalitomerice`) nemají žádný povolený host: Rubrika je jmenuje a odkazuje na ně, ale nic z nich nestahuje.
Redirect znovu prochází validací. Vlastní transport ověří DNS odpověď a připojí
se na schválenou číselnou veřejnou IP, zatímco TLS kontroluje původní jméno.
Zakázané jsou loopback, privátní/link-local adresy a jiné cíle. TLS není
vypínané; jediná výjimka je jeden host bez TLS (`digi.nacr.cz`, viz níže), kde platí výhradně `http://` a všechny ostatní hosty zůstávají jen HTTPS. Nenabízíme obecný download nástroj ani shell. Provozní limity jsou
uložené v DB a neobcházejí se restartem nebo novým hledáním.

Soubory cache obsahují veřejné zdrojové odpovědi; SQLite a private/ obsahují
osobní dotazy a interpretace. Disk není automaticky šifrovaný; šifrování a zálohy
jsou na uživateli. Datový adresář (`~/Rubrika`, u dřívější instalace původní složka) a soukromé složky se vytvářejí
s právy jen pro vlastníka. Hledání ve výchozím stavu nevyprší; smazat je lze jen ručním
`rubrika cleanup` s nastavenou `RUBRIKA_RETENTION_DAYS`. Server při každém startu jen
ořízne mezipaměť.

Ochrana před prompt injection spočívá v úzkém rozhraní a instrukci zacházet
s archivním obsahem jako s daty. Není zárukou správného modelového přepisu.
Schéma a hash ověřují původ/konzistenci obrazu, nikoli pravdivost interpretace.
Nezpřístupňujte citlivé další nástroje bez promyšlení společných oprávnění.

Nad jedním datovým adresářem může běžet více serverů současně: každá relace Claude
Code i rozšíření v Claude Desktop spouští vlastní. SQLite transakce chrání číselné
limity a úklid mezipaměti snáší soubory smazané jiným procesem.

## Pravidlo přístupu

Rubrika se archivu představuje jménem a odkazem na projekt: User-Agent je
`rubrika/<verze> (+https://github.com/j-necas/rubrika)`. Pro požadavky na archiv platí:

- **Jeden rozpracovaný požadavek na host, napříč procesy.** Výpůjčku hostu (lease) drží SQLite v datovém
  adresáři, takže souběžné relace nad týmž adresářem se řadí za sebe. Rozestup se počítá od skutečného
  zahájení požadavku: `max(request_interval 1,0 s, min_interval archivu, Crawl-delay)`. Čekání, do jehož
  průběhu jiný proces nastaví cooldown, se zastaví.
- **`Crawl-delay` se dodržuje přesně** a nikdy se nezkracuje. Žádá-li archiv víc než 60 s, požadavek se
  neodešle a vrátí se RATE_LIMITED.
- **429 a 503** spouštějí cooldown podle `Retry-After` (výchozí 60 s). 401 a 403 drží cooldown a končí
  SOURCE_ACCESS_DENIED.
- **Brány proti botům, přihlášení a captcha se nikdy neobcházejí.** Odpověď s bránou (např. výzva
  prohlížeči, přesměrování na kontrolu) končí SOURCE_ACCESS_DENIED s `details.reason=bot_protection`
  a hodinovým cooldownem celého hostu (všech skupin cest na něm). Bránu Rubrika hledá i v odpovědích 401, 403,
  429, 503 a v jakékoli jiné odpovědi s HTML (prvních 64 kB). Běžná stránka, která jen obsahuje odkaz
  s „captcha“ nebo skript Cloudflare `challenge-platform/scripts/jsd/main.js`, bránou není.
- **robots.txt** má u každého archivu režim (`Archive.robots_mode`): `honor` (výchozí) zákaz respektuje
  a požadavek zastaví; `user_triggered` zákaz toleruje jen u požadavku vyvolaného uživatelem, zapíše
  ho do logu, do dokladu (`ImageRef.robots_disallowed`) a do reportu (`coverage.robots_tolerated`).
  `Crawl-delay` platí v obou režimech. `user_triggered` používají AHMP a Národní archiv. robots.txt vrácený
  jako HTML se stavem 200 (VČA) se čte jako „žádná pravidla“.
- **Žádné implicitní cookies.** Klient HTTP nemá cookie jar; session cookie (sčítání MZA) se posílá jen
  explicitně v rámci jednoho volání.
- **Denní stropy:** na všechny požadavky na archiv (`Archive.daily_requests_setting`, u VČA `vca_global_daily_requests`, katalog, deskriptory,
  dlaždice, robots.txt) a na obrazové výsledky jedné knihy (`book_daily_images` = 150 za den).
  Cesty určené k hromadnému stahování (OAI, exporty, ZIP celých knih) v allowlistu nejsou nikdy.

## ARON

- Registr zdrojů (`sources.py`) povoluje jen GET detailu a citace archiválie (UUID)
  a POST `apu/listresults?listType=ENTITY|ARCH_DESC`. Tělo POST ≤ 8 KB skládá server
  z pevných šablon; agent nepředává surové filtry. POST nenásleduje přesměrování.
- Cooldown po 401/403/429 platí per skupina (acta_publica, aron, mza_images, robots);
  tempo požadavků per host. Globální denní strop ARON `RUBRIKA_ARON_GLOBAL_DAILY_REQUESTS`.
- Gzip se dekóduje a limit velikosti platí pro rozbalená data.
- Ve výchozím nastavení serveru je ARON vypnutý (`RUBRIKA_ARON_ENABLED=false`); plugin ho
  zapíná (`plugin/server/main.py`) a uživatel instalací přebírá podmínky užití: publikace digitalizátů bez písemného souhlasu vlastníka je zakázána. Obrazy se
  stahují jen na vyžádání, cache a doklady jsou soukromé na disku uživatele.

## Sčítací operáty

- Povoleny jen: našeptávač (`field=obec|cobec`, `term`), POST `do_advanced_search` s pevnými poli
  (≤ 2 KB), stránky `advanced_search[/n]` a `detail/<n>`; index, jednoduché hledání a kontaktní
  formulář povoleny nejsou.
- `form_search` přijme jen 302/303 na stránku výsledků s `ci_session`; cookie žije jen v rámci jednoho
  volání, klientský cookie jar se po každé odpovědi vyprázdní (žádné sdílení relací mezi uživateli).
- Cooldown skupina `census`, denní strop `RUBRIKA_CENSUS_GLOBAL_DAILY_REQUESTS`, vypínač `RUBRIKA_CENSUS_ENABLED`
  (výchozí false) hlídá i snímky již otevřených sad.
- Sčítání 1921 se nezpřístupňuje (MZA: jen v síti badatelen), i když je server technicky vydává.

## Indikační skici MZA (D 9)

- Povolený host `www.mza.cz` (ne image server), jen GET pod `/indikacniskici/`: `skica` s parametry `nazev`, `okres`, `signatura`, `page`
  (každý nejvýš jednou, aspoň `nazev` nebo `signatura`, pevné tvary hodnot), `skica/detail/MOR<číslo>` (web ho přesměruje na číselné
  ID) a `skica/detail/<číslo>` s volitelným `site=FRONT|BACK`, a statické dlaždice `data/obrazky/MOR<číslo>/Img/{map.jpg,
  Preview,little,Small,Normal}/<název>.jpg` s názvy přesně podle pravidel prohlížeče. Objednávání (`mode=ORDERING`), košík, našeptávač,
  stránky s podmínkami ani `assets` povoleny nejsou; robots.txt hostu (`/wp-admin/`, `/wp-includes/`) se dodržuje.
- Skupiny cooldownu `mza_sketch_catalog` a `mza_sketch_images`, denní strop `RUBRIKA_SKETCHES_GLOBAL_DAILY_REQUESTS` (2000),
  vypínač `RUBRIKA_SKETCHES_ENABLED` (server výchozí false, plugin zapíná) hlídá i dlaždice již otevřených skic.
- Prohlížeč skic ukládá mřížku dlaždic do inline JavaScriptu detailu. Rubrika ho nespouští: čte z něj jen pevné vzory (rozměry mřížky,
  seznam existujících dlaždic, adresář dlaždic) a odmítne stránku, jejíž konstanty se liší (PARSE_ERROR). Session cookie, kterou
  web vydává při přesměrování, se nepoužívá.
- Podmínky ([podmínky užití](https://www.mza.cz/indikacniskici/page/view/podminky-uziti-sluzeb), 20. 5. 2024): materiály jsou určené
  k prohlížení; bez písemného souhlasu MZA je zakázáno je umísťovat na jiné stránky či jakkoli publikovat a zasahovat do technické
  podstaty stránek. Stejný profil jako ARON: obrazy se stahují jen na pokyn uživatele, do jeho konverzace a soukromé cache, výřez má
  nejvýš 64 dlaždic, `terms_notice` a citace s `viewer_url` jdou s každým dokladem. Výslovné svolení MZA k automatizovanému prohlížení
  nebylo vyžádáno.
- **ČÚZK (Archiv map, `ags.cuzk.gov.cz/archiv`)** je jen odkazový archiv (`cuzk`, bez hostů a bez tras): jeho mapové služby
  (ArcGIS) vyžadují token, který ČÚZK vydává jen vlastní aplikaci, a data jsou placená (vyhláška č. 645/2004 Sb.). Rubrika nevolá
  jeho služby ani `GenerateToken` a nikdy nepodvrhuje `referer`; testy hlídají, že žádný jeho host není povolený.

## SOA Plzeň, Porta fontium

- Povolený host: `www.portafontium.eu`, jen GET: `/searching/register` s pevnými parametry (pole hledání, `page`,
  `field_register_type[]` s šesti známými hodnotami), `/register/<archiv>/<kniha>`, `/iipimage/<nid>[/<klíč>]`,
  `/fcgi-bin/iipsrv.fcgi` s jediným parametrem `IIIF=` nebo `DeepZoom=` a cestou pod `register/` (bez `..`, `%`, `\`)
  a `/robots.txt`. Navíc `/searching/amtsbuch` (stejná pole a `field_book_type[]` se sedmi známými hodnotami),
  `/searching/census` (jen s `mode=0`), `/amtsbuch/<archiv>/<fond>/<kniha>`, `/census/<archiv>/<rok>/<obec>[/<uzel>]` a obrazové cesty
  `amtsbuch/…` a `census/…` na iipsrv. Trasy mají zdroje `pf_land` a `pf_census` (obrazy `pf_land_images`, `pf_census_images`), které hlídají
  vlastní vypínače `RUBRIKA_PF_LAND_RECORDS_ENABLED` a `RUBRIKA_PF_CENSUS_ENABLED` (výchozí false; zapíná je až správce po živém ověření),
  sdílejí ale cooldown `pf_catalog` a `pf_images`, denní strop a tempo 10 s. Ostatní cesty (`/search/`, `/admin/`, `/user/…`, `FIF=`,
  ostatní záložky hledání) neprojdou. Podmínky (§ 92) platí i pro úřední knihy a sčítání a `terms_notice` je nese také.
- Robots.txt se dodržuje (`robots_mode="honor"`) včetně `Crawl-delay: 10`; požadavky jdou po jednom s odstupem 10 s.
  Skupiny cooldownu `pf_catalog`, `pf_images`, `pf_robots`; brána proti botům zastaví celý host na hodinu.
- Vypínač `RUBRIKA_PF_ENABLED` (server výchozí false, plugin zapíná), strop
  `RUBRIKA_PF_GLOBAL_DAILY_REQUESTS` (800). Vypnutý pramen je v `list_archives` bez `available_kinds`, `find_place` ho
  vrací v `not_searched` a `view_scan` odpoví LIVE_DISABLED.
- Podmínky ([terms-of-use](https://www.portafontium.eu/terms-of-use)): kopie smí uživatel mimo portál použít jen
  podle § 92 autorského zákona (osobní potřeba, věda a výuka, nevýdělečně, s uvedením zdroje a internetové adresy).
  Rubrika proto vrací `terms_notice` a s každým dokladem `citation` a `viewer_url`; skill uživateli podmínky
  připomene jednou za hledání. Jde o omezení, které Rubrika sama nevynucuje (nezveřejňování snímků).
- TLS: portál posílá jen koncový certifikát bez zprostředkující CA „GEANT TLS RSA 1“ (vydavatel HARICA TLS RSA Root CA
  2021, které Python důvěřuje); prohlížeče ji doplní přes AIA. Rubrika proto přibaluje veřejný certifikát
  `certs/geant-tls-rsa-1.pem` (SHA-256 5B:67:8D:C4:40:95:A5:28:95:B6:3B:31:F2:72:27:F4:B3:6C:3E:34:74:91:BF:2B:FA:69:18:37:A5:FB:8C:79,
  platný do 2039) a přidá ho do TLS kontextu; ověření řetězce i názvu hostu zůstává zapnuté. Soubor se před použitím
  kontroluje proti tomuto otisku.
- Katalog je HTML bez API: neznámá struktura je PARSE_ERROR, nikdy prázdný výsledek.

## SOA Hradec Králové, ARchiv ONline

- Povolené hosty: `aron.vychodoceskearchivy.cz` (API `/api/aron`) a `imgx.vychodoceskearchivy.cz`
  (dlaždice). Cesty: POST `apu/listview?listType=ENTITY|FUND|ARCH_DESC` (tělo skládá server z pevných
  šablon), GET detailu `apu/<uuid>`, citace `script/citation/<uuid>` a na obrazovém hostu
  `tiles/…/image.dzi` a `image_files/…`.
- **Nikdy** se nepovoluje `storage.vychodoceskearchivy.cz` ani soubory typu `PUBLISHED` (interní cesty,
  ZIP celých knih).
- Vypínač `RUBRIKA_VCA_ENABLED` (výchozí false), strop `RUBRIKA_VCA_GLOBAL_DAILY_REQUESTS`. Server má VČA
  ve výchozím stavu vypnuté; plugin ho zapíná (`plugin/server/main.py`). Vypnuté VČA je v `list_archives` jen odkaz.
- Podmínky: archiv nápovědou dovoluje prohlížet i stahovat jednotlivé snímky a celé sady digitalizátů;
  ochranné lhůty matrik (100/75/30 let) a omezení z důvodu ochrany osobních údajů platí. Rubrika stahuje
  jen snímky, které si uživatel vyžádá. Při zveřejnění se uvádí archiv, fond a citace.

## SOA Praha, eBadatelna

- Povolený host: `ebadatelna.soapraha.cz`. GET: `/pages/SearchMatrikaPage` (s verzí stránky), listener dalšího výsledku
  (`…-tableContainer-pagination-results-nextPageUrl`, Wicket AJAX GET; mimo POST hledání jediný listener), `/pages/MatrikaPage/matrikaId/<id>`, `/pages/DataMatrikaPage/idx/<n>/matrikaId/<id>`,
  `/d/<id>/<n>`, JPEG `/wicket/resource/org.apache.wicket.Application/<název>__NNN.jpg` a `/robots.txt` s řetězem přesměrování na
  chybovou stránku. Jediné POST jsou (vedle zmíněného GET listeneru dalšího výsledku) listener tlačítka hledání matrik
  (`…-searchForm-searchButton`, pět pevných polí) a listener tlačítka hledání archiválií (`/pages/SearchArchivaliePage?…-searchForm-searchButton`,
  sedm pevných polí: lokalita = položka seznamu lokalit archivu, prázdné `searchArchivalie`/`mistoUlozeni`/`archivniFondId`, `typArchivalie`
  prázdný nebo číslo, roky); hodnoty a délky se kontrolují (`FORM_CHECKS`), roky 1000–2100. Archiválie mají GET route
  `/pages/SearchArchivaliePage` (s verzí a listenerem dalšího výsledku), `/pages/ArchivaliePage/archivalieId/<id>`,
  `/pages/DataArchivaliePage/archivalieId/<id>[/idx/<n>]` a `/a/<id>/<n>` (zdroj `soapraha_archivalie`, skupina cooldownu `soapraha_catalog`);
  JPEG archiválie má název podle jejího názvu (povolené znaky `A-Za-z0-9_.,:()-`, jinak žádná route). Zdroj `soapraha_archivalie` je zavřen, dokud
  není zapnutý `RUBRIKA_SOAPRAHA_CENSUS_ENABLED` nebo `RUBRIKA_SOAPRAHA_LAND_RECORDS_ENABLED`; snímek se navíc vydá jen dokud je zapnutý druh jeho
  archiválie. Přihlášení, registrace, ZIP, poznámky, náhled, ostatní listenery (řazení, velikost stránky, skok na stranu) a ostatní stránky
  nemají route.
- Seznam lokalit z úvodní stránky se čte konzervativně (slova `okresTerm`/`obecTerm` a tvar položek se ověřují, jinak PARSE_ERROR)
  a do mezipaměti se ukládá jen seznam, nikdy session ani cookie.
- Session (JSESSIONID) se získá GET otevírací stránky, žije jen v paměti jednoho volání, posílá se jen na tento host, session id se
  z adres odstraňuje a nic se neukládá. Čísla verzí komponent se čtou ze stránky, nikdy nejsou napevno. Neznámá struktura je
  PARSE_ERROR, nikoli prázdný výsledek.
- Celý JPEG: limit velikosti odpovědi, typ obsahu `image/jpeg`, magická bajtová kontrola, plný dekód a limit pixelů, teprve pak
  do mezipaměti (klíč = adresa prohlížeče snímku). Skupiny cooldownu `soapraha_catalog`, `soapraha_images`, `soapraha_robots`; brána
  proti botům zastaví celý host na hodinu. Vypínač `RUBRIKA_SOAPRAHA_ENABLED` (server výchozí false, plugin true), strop 1000 požadavků denně.
- robots.txt je HTML chybová stránka, tedy bez pravidel. eBadatelna nezveřejňuje podmínky užití; doklady nesou citaci archivu a fondu.

## Ruční režim, import_scan

- Nic se nestahuje ani neotevírá po síti. Nástroj čte jen běžné soubory v povolených složkách: Stažené soubory
  uživatele, `<data_dir>/import` a `RUBRIKA_IMPORT_DIRS`. Nikdy libovolnou cestu (obrana proti prompt injection):
  `file_name` je holý název souboru; oddělovače, `..`, absolutní cesty, prázdné názvy a znak NUL se odmítnou.
  Povolená složka se otevře (`O_DIRECTORY|O_NOFOLLOW`) a soubor se otevře relativně k ní (`dir_fd`, `O_NOFOLLOW|O_NONBLOCK`),
  takže symbolický odkaz, FIFO ani zařízení nemůže nástroj zablokovat nebo přesměrovat; po `fstat` musí jít o běžný soubor
  s jediným pevným odkazem (`st_nlink == 1`, pevný odkaz na soubor jinde se nečte).
  Ve Windows (bez `dir_fd`) se soubor kontroluje `lstat` → otevření → `fstat` se stejným `(st_dev, st_ino)` a rozlišená nadřazená složka
  musí být povolená složka; na svazcích FAT je kontrola inode prázdná (`st_ino` 0), kontrola nadřazené složky platí dál.
- Nejdřív se přečtou prvních 16 bajtů a neobrázek se odmítne, aniž by se četl zbytek souboru. Čtení, dekódování
  i hash běží mimo smyčku událostí (`asyncio.to_thread`), takže platí `tool_timeout`. Soubor ve Stažených souborech starší
  než 30 dní se neimportuje (chyba `FILE_TOO_OLD` s cestou do složky import); složka import časové okno nemá.
  Stejný soubor (sha256) se v hledání neimportuje dvakrát a nespotřebuje další obrazový výsledek.
- Hranice důvěry: nástroj smí přečíst jakýkoli obrázek v povolených složkách. Krok „potvrďte s uživatelem, že je to ten sken“
  je pokyn modelu, ne technické vynucení. `RUBRIKA_IMPORT_DIRS` je důvěryhodné nastavení provozovatele. Stažené soubory,
  které jsou symbolickým odkazem, se přijmou (volba uživatele). Složku import vytváří jen `import_scan`; `list_archives`
  a `get_research_status` nic nezakládají a vracejí jen popisky složek, ne absolutní cesty.
- Výpis bez `file_name` čte jen prvních 16 bajtů souborů změněných za 7 dní; názvy souborů se očistí od řídicích znaků
  a jsou v odpovědi označeny jako nedůvěryhodné.
- Typ se určuje podle obsahu (JPEG, PNG, TIFF, WebP; PDF se odmítne s nápovědou), strop 60 MB, limit pixelů
  `max_source_pixels` před dekódováním, pak úplné dekódování (useknutý obraz se nepřijme). Uloží se normalizovaná
  kopie do soukromé složky hledání; originál se nemění ani nemaže.
- Cizí hledání snímek nevidí (vlastnictví se kontroluje jako u ostatních nástrojů); uzavřené hledání import odmítne.
- Původ zůstává vidět v dokladu, zprávě i GEDCOM: snímek dodal uživatel a odkaz je neověřený. Za dodržení podmínek
  archivu (Opava: zveřejňovat jen se souhlasem) odpovídá uživatel, který snímek stáhl; Rubrika jen dodává nástroj.

## SOA Třeboň, DigiArchiv

- Povolený host: `digi.ceskearchivy.cz`, jen GET. Cesty: `/robots.txt`; `/tree.php?menu=0&doctree=<1nr | 1nr<písmeno>>&lang=cs` (strom písmen a
  farností) a `/tree.php?menu=0&doctree=1nr<písmeno><1–3 znaky>&id=<číslo>&lang=cs` (přehled jedné farnosti); kořeny nekatolických větví
  `doctree=1nC|1nc|1ne|1ng|1np|1nb|1nh|1ni` bez id a jejich sbory `doctree=1n<C|c|e|g|p|b|h|i><1–5 znaků>&id=<číslo>`; uzly větví pozemkových knih,
  vrchnostenských knih, urbářů a sčítání `doctree=1<q|t|U|s><0–8 znaků>` (s id nebo bez, id nejvýš sedm číslic); `/view.php?menu=3&id=<kniha>&page=<n>`
  (prohlížeč jednoho snímku, jen HTML, jednou na knihu). **Žádný obrazový host ani cesta:** `/cgi-bin/isrv6.cgi` (deskriptor, dlaždice, náhled) nemá
  route a pro `trebon` neexistuje obrazový fetcher. Parametry jsou pevné a každý právě jednou (`menu=0`, `doctree`, případně `id`, `lang=cs`); `exportw.php`, `DA`, `/k/`, `description.php`, přihlášení, registrace, poznámky a záložky nemají route.
- **Nikdy** se nepoužívá `/pages/*` (vyhledávání `search.php`, nápověda `help.php`; robots.txt je zakazuje) ani `/oai/*` (zakázáno). Vyhledávání by
  šlo použít jen na pokyn uživatele (`robots_mode="user_triggered"`); Rubrika ho nepoužívá vůbec, `robots_mode` je `honor`, takže se robots.txt
  dodržuje na každé cestě a redirectu.
- Jazyk stránek volí server podle hlavičky `Accept-Language`, ne podle `lang=cs`; Rubrika posílá u tohoto archivu běžnou hlavičku prohlížeče `cs-CZ,cs;q=0.9` (`Archive.request_headers`), jinak nic navíc.
- Bez session a cookies: stránky stromu i prohlížeče se načítají bez přihlášení a bez cookie; Rubrika žádné neukládá.
  Vše se čte konzervativně: neznámá struktura (písmena, farnost, řádek knihy, prohlížeč, seznam snímků, náhledy) je PARSE_ERROR, nikoli prázdný
  výsledek; stránka farnosti musí nést odkaz vlastní farnosti, stránka snímku musí souhlasit s knihou, číslem snímku a náhledy.
- Nové druhy (pozemkové knihy, sčítání) mají každý vlastní přepínač (`RUBRIKA_TREBON_LAND_RECORDS_ENABLED`, `RUBRIKA_TREBON_CENSUS_ENABLED`, výchozí false)
  a stejná pravidla: žádný obrazový požadavek, žádný Referer, jen stránky stromu a prohlížeče; procházení stromu má strop 24 stránek na volání
  a hrozí-li neúplnost, je ve varování (pokrytí se z nejisté stránky nikdy nevylučuje).
- Podmínky: úvodní stránka archivu zveřejňuje snímky jen k prohlížení v DigiArchivu a zakazuje kopírování, zveřejňování, prodej a použití jinde bez
  souhlasu vlastníka. Zapnutí je proto vědomé (`RUBRIKA_TREBON_ENABLED`, server výchozí false, plugin true), `terms_notice` se ukazuje uživateli (list_archives, find_place,
  list_sources, open_source), výřezy Rubrika nikam nezveřejňuje (ukazuje je uživateli v konverzaci) a doklady nesou `viewer_url` a `citation`.
- Tempo a stropy: aspoň 2 s mezi požadavky (`min_interval`), skupiny cooldownu `trebon_catalog`, `trebon_robots`, denní strop
  `RUBRIKA_TREBON_GLOBAL_DAILY_REQUESTS` (800); brána proti botům zastaví celý host na hodinu, 401/403/429 zastaví skupinu.
- Snímky: omezený režim. Obrazový server vydává dlaždice jen prohlížeči (prázdná odpověď na prostý požadavek);
  Rubrika to bere jako ochranu archiva, kterou neobchází (žádný Referer ani jiné záhlaví), a snímky nestahuje. `view_scan` a `zoom_scan` pro `trebon`
  nedělají žádný požadavek a vracejí `MANUAL_SOURCE`.

## AHMP a Národní archiv: obrazové hosty, katalogy

- Povolené hosty: `images.ahmp.cz` a `vademecum.nacr.cz` (obrazové, jen cesty `mrimage/…/NAD-<n>` nebo `…/spisy/…` `/ImageProperties.xml`
  a `TileGroup…/*.jpg` bez dotazu) a `katalog.ahmp.cz`. Katalog AHMP smí jen GET na `/pragapublica/`:
  `NavigBean.action` (jediný parametr `entity` přesně ve tvaru `(^n)(((localArchiv,^n,hot_)(unidata))(<id>))`),
  `PaginatorNavig.action` (jen `_sourcePage` a `row` z odkazu předchozí stránky, nebo GET formulář „přejít na řádek“: přesně `rowTxt` (číslo), `_sourcePage` a `__fp` s tokeny z předchozí odpovědi; půlení plochých seznamů Soupisu a sčítání, žádný POST), `permalink` (`xid`, případně
  `scan`) a `robots.txt`. `Zoomify.action` (včetně `captcha`), `PaginatorMedia`, `BasketBean`, přihlášení,
  `SearchBean` a POST jsou zavřené; permalink s `&scan=N` už obsahuje zdroj obrazu, jiná cesta není potřeba.
  Skupiny prodlev: `ahmp_catalog`, `ahmp_robots`, `ahmp_images`. Vypínač `RUBRIKA_AHMP_ENABLED` je
  ve výchozím stavu serveru vypnutý a plugin ho zapíná, stejně jako `RUBRIKA_NA_ENABLED`. Denní stropy
  `RUBRIKA_AHMP_GLOBAL_DAILY_REQUESTS` a `RUBRIKA_NA_GLOBAL_DAILY_REQUESTS` mají hodnotu 1000, oba archivy se čtou
  nejvýše jednou za 2 s.
- Katalog Národního archivu (`vademecum.nacr.cz`, cesty `/vademecum/`) smí jen GET: `permalink` (`xid`, případně
  `scan` a `parentType`), `BookVolume.action` (přesně `browseAid=` a `entity` inventáře fondu HBMa),
  `PaginatorBookVolume.action` (buď `_sourcePage` a `row`, nebo skok z obsahu `alter`, `_sourcePage`, `zanchor`, `rowTxt`),
  `PaginatorMedia.action` (jen `_sourcePage` a `row`), `Zoomify.action` (jen `entityRef` knihy téhož fondu a
  `scanIndex`; Rubrika posílá vždy `scanIndex=0`, protože prohlížeč pořadí snímku ignoruje) a `robots.txt`. Část cesty
  `;jsessionid=…` se přijímá jen v tomto tvaru a Rubrika ji nikdy neposílá, relace jde v cookie. `Zoomify.action?captcha=`
  („uložit“), `showOne`, `baseUrl`, `BasketBean`, `SearchBean`, `Rejstriky`, `AutocompleteBean`, přihlášení, odkazy
  `BookVolume.action?show=` a POST jsou zavřené. Skupiny prodlev: `na_catalog`, `na_robots`, `na_images`.
  Cookie relace žije jen v paměti jednoho volání a posílá se jen na `vademecum.nacr.cz`; tokeny `_sourcePage` se berou
  jen z odkazů předchozí stránky téže relace. Stránka „Page has Expired“ se pozná a relace se jednou obnoví.
  Odpověď inventáře má asi 600 kB (obsah je na každé stránce), proto jedno volání `list_sources` stojí i několik MB.
  Patička archivu: „Publikování jakékoliv textové i obrazové části databáze je podmíněno souhlasem Národního archivu
  a uvedením řádné citace.“ Rubrika nic nepublikuje; uživatel odpovídá za další použití.
- Katalog AHMP je stavový (JSESSIONID). Session se získá prostým GET trvalého odkazu, žije jen v paměti jednoho
  volání nástroje, posílá se jen na `katalog.ahmp.cz` a nikdy se neukládá ani nesdílí mezi voláními (uložené
  cookie zůstávají zakázané). Stránka „Platnost stránky vypršela“ se pozná a session se jednou obnoví.
  Parsery HTML selžou s `PARSE_ERROR`, když nepoznají strukturu; nikdy nevrací prázdný výsledek.
- `robots.txt` všech tří hostů zakazuje vše; podle pravidla přístupu se pro požadavky vyvolané uživatelem
  toleruje (host se zapíše do `robots_tolerated`), ochrany proti botům, přihlášení a 401/403/429 se nikdy
  neobcházejí.
- Oba archivy vydávají celý snímek v plném rozlišení („uložit“) jen za captchou. Rubrika proto
  počítá plochu, kterou u stránky vykreslil z nejvyšší úrovně Zoomify (`fullres:<stránka>`, po celou dobu života dat, ne po dnech; sdílené všemi uživateli a nikdy se nemaže), a výřez, který
  by přesáhl polovinu plochy snímku (`RUBRIKA_FULL_RES_PAGE_SHARE`), odmítne dřív, než stáhne dlaždici.
  Přehledy ve zmenšení se nepočítají.

## Policejní přihlášky Prahy 1850–1914, Národní archiv (kód `na_prihlasky`)

- **Nešifrované HTTP.** Aplikace `http://digi.nacr.cz/prihlasky2/` TLS nenabízí. Příznak `Archive.allow_plain_http` (jen u tohoto archivu)
  dělá z `http://` jediné povolené schéma tohoto hostu (`https://` se odmítne, protože host TLS nemá); `SourcePolicy` ho kontroluje u každé
  adresy včetně přesměrování, a každý jiný host smí jen HTTPS (test to ověřuje pro všechny integrované hosty). Co to znamená pro uživatele:
  **hledaná jména putují sítí čitelně** a odpověď stránky by mohl cestou někdo změnit. Obsah stránek se proto bere jako nedůvěryhodná data
  (parsery selžou s `PARSE_ERROR`, když nepoznají strukturu; nic z odpovědi se nevyhodnocuje), sken se před uložením ověří jako úplný JPEG.
  Ochrana DNS (jedno ověření, připojení na veřejnou číselnou adresu, zákaz soukromých a loopback adres) platí pro tento host beze změny.
  Kořen `robots.txt` a jeho pravidla se pro tento host vedou pod `http://digi.nacr.cz`.
- **robots.txt** hostu zakazuje vše (`Disallow: /`). Rubrika ho u požadavků vyvolaných uživatelem toleruje
  (`robots_mode="user_triggered"`, host v `coverage.robots_tolerated`, `robots_disallowed` u obrazů), čte nejvýš jednou za 2 s a
  přihlášení, captchu, ochranu proti botům ani odpovědi 401/403/429 neobchází (bot brána zastaví host na hodinu).
- Povolené cesty (`match_prihlasky_route`), vše pod `/prihlasky2/`: GET vstupní stránky (vydá token relace), GET `?session=<64 hex>`,
  `&action=list&od=<n>` (stránkování výsledků), `&action=image&record=<n>` (detail výsledku), permalink bez relace
  `index.php?action=link&ref=czarch:CZ-100000010:874&karton=<n>&folium=<n>` (pevný `ref`), obraz `images/<8 číslic>-<8 číslic>.jpg` bez dotazu
  a `robots.txt`. Jediný POST je rozšířené hledání (`?session=<token>`) s pevnými poli `prijmeni`, `jmeno`, `odroku`, `doroku`, `action=hledani2`,
  `tlacitko=Najít` (`check_prihlasky_search_form`: přesná jména a pořadí, omezená délka, roky 1700–1930 nebo obojí prázdné). Jednoduché
  a podrobné hledání, nový dotaz, řazení, jazyk, informační stránky a soubory jsou zavřené.
- **Token relace je v adrese**, ne v cookie; žije jen v paměti jednoho volání `list_sources` a nikdy se neukládá (uložený je jen trvalý odkaz
  bez relace). Server, který token nezná, mlčky otevře novou, nefiltrovanou relaci. Rubrika proto
  u každé stránky kontroluje, že všechny odkazy nesou token, který drží; jiný token je `SOURCE_UNAVAILABLE` (`details.reason=session_expired`) a nic
  z takové stránky se nepoužije. Stránky s tokenem se nekešují a trvalý odkaz se čte také bez mezipaměti (`state_get`); každá stránka vázaná na relaci musí token nést alespoň jednou, stránka bez tokenu je stejně ztracená relace. Detail výsledku se ověřuje proti řádku, ze kterého vede (příjmení, jméno, rok).
- Zátěž jednoho volání: nejvýš 30 požadavků, 3 hledání (varianty pravopisu se zkouší, jen dokud předchozí nic nenašlo), 5 stran výsledků po 20 a 15
  přečtených přihlášek; při seříznutí `listing_complete=false` s varováním. Skupiny prodlev `prihlasky_catalog`, `prihlasky_images`, `prihlasky_robots`;
  vypínač `RUBRIKA_PRIHLASKY_ENABLED` (server výchozí false), strop `RUBRIKA_PRIHLASKY_GLOBAL_DAILY_REQUESTS` (600).
- Archiv nezveřejňuje podmínky užití; Rubrika sken nikam nezveřejňuje (cache a doklady jsou soukromé na disku uživatele) a doklady nesou citaci archivu
  (karton, obraz) a trvalý odkaz.
