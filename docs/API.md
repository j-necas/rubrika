# MCP a datový kontrakt, verze 0.13.2

Oficiální MCP adaptér je v `server.py`, samostatně testované operace jsou
v `service.py`. Server běží lokálně a s Claude komunikuje přes stdio. Každý nástroj vrací
textový JSON blok (aplikační data) a obrazové nástroje navíc nativní image
blok `mimeType=image/jpeg`. Datové modely jsou v `models.py` a SDK z nich
odvozuje vstupní JSON schémata. Záměrně se neduplikuje velký obsah do
structuredContent.

| Nástroj | Vstup | Výsledek |
|---|---|---|
| start_research | query: SearchQuery | research_id, revision, checkpoint_images |
| revise_research | research_id, query, expected_revision | nová revize; doklady zachovány |
| continue_research | research_id | pokračování po kontrolním bodu (CHECKPOINT) se souhlasem uživatele: images_total, next_checkpoint_after |
| list_archives | district? | archivy, které Rubrika zná: kód, název, aplikace, druhy (`kinds`), zapnuté druhy (`available_kinds`), `link_only`, důvod, podmínky užití; s okresem i archiv, kam okres patří. Bez sítě |
| find_place | research_id, term, kind (parish_registers/land_records/census/cadastral_maps), district?, archives? (1–3 kódy) | places (každé s `archive`; u sčítání MZA `place_kind` obec/cobec), ambiguous, `searched`, `not_searched` (archiv, důvod, odkaz), warnings |
| list_sources | research_id, place_ids (1–5, z jednoho archivu), kind, year_from?, year_to?, digitized_only=true, fund_marks?, district_archive?, cursor, limit (max20) | sources (řádek nese `archive`, `kind`) + relevance, total, next_cursor, listing_complete, warnings |
| open_source | research_id, source_id, cursor, limit (max20) | `archive`, `kind`, `unit` (metadata knihy, archiválie nebo sady), citation, stránkované snímky, total, next_cursor, warnings, note |
| view_scan | research_id, page_id | image ref a obrazový přehled |
| zoom_scan | research_id, image_id, bbox_px | nový obrazový detail s převodem do původního zdroje |
| import_scan | research_id, file_name?, archive?, citation?, source_url?, scan_label?, folder? | ruční režim, viz níže. Bez `file_name`: výpis nedávných obrázků (`files`, `import_folders`); s ním: import jako `view_scan` (image ref, obrazový přehled) a `page_id`, `provenance=user_supplied`, `viewer_link_verified=false` |
| show_saved_evidence | research_id, image_id | již uložený důkaz; bez stahování ze zdroje, nic nečerpá |
| record_finding | research_id, observation, idempotency_key | observation_id; kontrola příslušnosti důkazu |
| get_research_status | research_id | revize, shrnutí kandidátů, rozsah, `usage` (požadavky a snímky z archivů, snímky do kontrolního bodu, dnešní stropy archivů a otevřených knih) |
| list_findings | research_id, cursor=0, limit=5 (max20) | úplná stránkovaná pozorování |
| finish_research | research_id, stop_reason | kandidáti, stav běhu, důvod zastavení, rozsah |
| export_family_tree | tree: FamilyTree, variant=all/documented_only | gedcom (5.5.1), mermaid, summary, suggested_filename; nic neukládá ani nestahuje |

### Ruční režim: import_scan

Pro archivy, které Rubrika nečte (Opava, Litoměřice, dosud nenapojené) i jiné portály: uživatel snímek uloží
v prohlížeči a Rubrika ho přijme jako doklad. Nic se nestahuje, nástroj funguje i při vypnutém `RUBRIKA_LIVE_ENABLED`.

- Složky: stažené soubory uživatele (`~/Downloads`, existuje-li), `<data_dir>/import` (vzniká při prvním použití, práva 0700)
  a `RUBRIKA_IMPORT_DIRS` (JSON seznam cest navíc, štítky `extra1`...). `list_archives` a `get_research_status` vracejí
  jen popisky (`import_folders`: `folder`, `name`), bez cest a bez zakládání složky; cesty (`path`) dává jen `import_scan`, který
  složku import také vytváří. Soubor ve Stažených souborech starší než 30 dní vrací `FILE_TOO_OLD` (přesunout do složky import).
  Stejný soubor v témže hledání vrátí existující stránku (`deduplicated`) bez nového obrazu a bez poplatku.
- `file_name` vynechán: výpis `files` (nejnovější první, nejvýš 10, změněné za posledních 7 dní, jen obrázky poznané
  podle prvních bajtů): `name`, `folder`, `folder_path`, `size`, `type`, `modified`. Obsah souborů se nečte. Názvy jsou
  nedůvěryhodný text.
- `file_name` zadán: povinné `archive` (kód z registru, nebo `other`) a `citation` (≤ 500 znaků, od uživatele); volitelné
  `source_url` (http/https, ≤ 2000), `scan_label` (≤ 100) a `folder` (rozliší stejný název ve dvou složkách, jinak
  `AMBIGUOUS_FILE`). Hledání musí být aktivní, uzavřené vrací `RESEARCH_CLOSED` (znovu otevře `revise_research`).
- Typy podle obsahu: JPEG, PNG, TIFF (první snímek), WebP; nejvýš `RUBRIKA_IMPORT_MAX_BYTES` (60 MB) a `max_source_pixels`
  (`IMAGE_TOO_LARGE`), úplné dekódování (`INCOMPLETE_IMAGE`), PDF a ostatní `UNSUPPORTED_FILE`.
  Uloží se normalizovaná kopie JPEG (orientace podle EXIF) do `private/<research_id>/scan_<n>.jpg`; sha256 originálu je
  `original_sha256` a `manifest_sha256` stránky.
- Stránka `user:<research_id>:<n>` (`source_type=user_supplied`, `source=user`, `archive` = zadaný kód, `viewer_url` = `source_url`
  nebo null, `viewer_link_verified=false`). ImageRef nese `provenance=user_supplied` a `scan_label`. Import nečerpá
  kontrolní bod snímků ani denní stropy archivu a knihy. `zoom_scan` ořezává lokálně.
- Zpráva (`coverage.opened_books`) a `show_saved_evidence` nesou „Snímek dodal uživatel, odkaz neověřen.“; GEDCOM má
  `SOUR TITL` z citace, `AUTH`/`REPO` podle archivu (u `other` „jiný archiv“), `PAGE` ze `scan_label`, poznámku o původu
  a odkaz označený jako neověřený.

Nástroje nemají předponu archivu. Archiv
a druh pramene se volí parametry: `kind` je `parish_registers`, `land_records`, `census` nebo `cadastral_maps`, `district`
je dnešní okres a určuje archiv podle tabulky v `territory.py`. `find_place` neprohledává archivy,
kam Rubrika nesmí nebo zatím neumí; vrátí je v `not_searched` s důvodem a odkazem na archiv. Pravidla
parametrů `list_sources`: `year_from`/`year_to` u sčítání se zadávají jako jeden rok (`year_from` =
`year_to`), u matrik MZA se odmítají, `fund_marks` platí jen pro pozemkové knihy MZA, `district_archive`
(dříve `archive`) zužuje sčítání MZA na okresní archiv. U řádků sčítání MZA je držící okresní archiv
v poli `district_archive`; pole `archive` je vždy kód archivu Rubriky.

ID (natrvalo uložená): MZA beze změny (číselné ID knih a obcí Acta Publica, `aron:<uuid>`, `census:…`,
snímky `page_…`); SOA Hradec Králové (`vca`) `vca:place:<uuid>`, `vca:unit:<uuid>`, `vca:fund:<uuid>` a
snímek `vca:unit:<uuid>:<n>`. ID z VČA nikdy nezačíná `aron:`. Hledání uložená verzí 0.7.1 (bez pole
`archive`) se čtou dál, chybějící `archive` se bere jako `mza`.

SOA Hradec Králové (kód `vca`, ARchiv ONline): `parish_registers` (archiválie s `UNIT~TYPE`
matrika), `land_records` (nejdřív `UNIT~TYPE` „evidence nemovitostí“ s `category="land_book"`, pak ostatní digitalizované knihy místa bez matrik, sčítání a evidence nemovitostí jako `category="other"`; tři dotazy: knihy připojené k místu, knihy fondu „Archiv města/obce <název místa>“ s `match="fund_name"` a varováním, pak ostatní; stránkování i `listing_complete` platí pro všechny) a `census` (`UNIT~TYPE` „sčítací operát“; řádek nese `house_numbers` z popisu a `relevance.house_number_covered`; `year_from`/`year_to` jako rozsah, filtruje archiv; `open_source` řadí podle detailu, typ „matrika“ má přednost, jinak platí druh z výpisu). `fund_marks` a `district_archive` nepodporuje. Server ho má ve výchozím stavu vypnuté (`RUBRIKA_VCA_ENABLED`), plugin ho zapíná; `list_archives` ukáže v `available_kinds`, co je zapnuté. Zdrojový katalog je
`/api/aron` (`apu/listview`), dlaždice na `imgx.vychodoceskearchivy.cz`.

SOA Plzeň (kód `pf`, Porta fontium): `parish_registers` (Sbírka matrik západních Čech), `land_records` (záložka Úřední knihy:
pozemkové knihy s `category="land_book"` podle filtru typu knihy v portálu, ostatní úřední knihy jako `other`; ID `pf:amtsbuch:<archiv>/<fond>/<kniha>`,
`list_sources` hledá dvakrát, s filtrem a bez něj, pod jedním limitem stran) a `census` (záložka Sčítání lidu, jen v seznamovém zobrazení `mode=0`;
jednotka je obec a rok `pf:census:<archiv>/<rok>/<obec>`, snímky jsou po domech: `open_source` otevře dům z `person.house_number`, jiný dům
`…@<číslo domu>`; `house_numbers` z rozpisu domů, `house_number_covered` jen řadí). Úřední knihy a sčítání mají vlastní vypínače
`RUBRIKA_PF_LAND_RECORDS_ENABLED` a `RUBRIKA_PF_CENSUS_ENABLED` (server i plugin výchozí false; `list_archives` je bez zapnutí v `available_kinds`
nevede), roky u sčítání jen jeden (`year_from` = `year_to`). Podmínky (`terms_notice`) platí i pro ně. Portál nemá
JSON API ani našeptávač; `find_place` čte první stranu hledání matrik podle pole Místo (celá slova) a místa odvodí
z řádků výsledků (`pf:place:<název bez diakritiky>`, německé názvy v `names`). `list_sources` znovu spustí hledání pro každé
místo (nejvýš 2 místa, celkem nejvýš 12 stran po 40 knihách na volání, jinak `listing_complete=false`), bez filtru typu
(knihy bez značek zůstanou, `needs_review`); prázdný výsledek dá varování a `listing_complete=false`. `find_place` bez okresu
a bez `archives` pf neprohledává (`Archive.auto_search=false`, varování) a řadí podle značek datace (`*` narození, `oo` oddaní, `+` zemřelí,
`i` index). Roky, `fund_marks` a `district_archive` odmítá. Knihy `pf:book:<archiv>/<kniha>`, snímky
`pf:book:<archiv>/<kniha>:<n>`; `open_source` čte detail a prohlížeč (`imagesSet`) a vrací `citation` s adresou knihy,
snímky jsou IIIF (`fcgi-bin/iipsrv.fcgi?IIIF=register/…`). Výstupy `find_place`, `list_sources` a `open_source` nesou
`terms_notice` (§ 92 autorského zákona); `list_archives` ho vrací u každého archivu (`null`, když žádné není), `viewer_url`
je adresa snímku v portálu (`/iipimage/<nid>/<klíč>`). Zapíná ho `RUBRIKA_PF_ENABLED` (server výchozí false, plugin true),
denní strop `RUBRIKA_PF_GLOBAL_DAILY_REQUESTS` (800). Robots.txt žádá `Crawl-delay: 10`, který se dodržuje. Vypínače pro úřední knihy a sčítání
hlídá i síťová vrstva (trasy `pf_land`, `pf_census` a jejich obrazové varianty): vypnutý druh nevyšle ani jeden požadavek.


SOA Třeboň (kód `trebon`, DigiArchiv): `parish_registers` (římskokatolické i nekatolické a civilní matriky, „Sbírka matrik Jihočeského kraje“), `land_records` (pozemkové knihy jako `land_book`, vrchnostenské knihy a urbáře jako `other`) a `census` (přepínače `RUBRIKA_TREBON_LAND_RECORDS_ENABLED`, `RUBRIKA_TREBON_CENSUS_ENABLED`, výchozí false). Vyhledávání
DigiArchivu (`/pages/*`) a OAI zakazuje robots.txt, proto `find_place` čte jen strom farností: stránku písmen (`tree.php?doctree=1nr`) a stránku
písmene podle prvního písmene výrazu; vrací farnosti (`trebon:place:<id farnosti>`, ne obce; slova výrazu se porovnávají se začátky slov názvů).
`list_sources` bere jednu farnost, čte její přehled (jeden požadavek) a vrací zveřejněné knihy (`trebon:book:<id>`, `number`, `register_label`,
roky podle typů B/M/D, `localities` = „Book for“, `parish` s okresem a farním obvodem); řádky „online až od roku …“ jen spočítá ve varování,
`year_*`, `fund_marks`, `district_archive` a víc míst odmítá, `place_hint` z hledání rozhoduje mezi knihami vedenými pro různé obce (jiná lokalita je
nanejvýš `needs_review`). `open_source` načte stránku prvního snímku (počet snímků, `citation`; jeden požadavek na knihu); snímky `trebon:book:<id>:<n>` mají
`source_type` `manual`, `viewer_link_verified` false a `viewer_url` `view.php?menu=3&id=<id>&page=<n>` sestavený bez požadavku. **Omezený režim:**
`view_scan`/`zoom_scan` pro `trebon` nedělají žádný požadavek a vrací `MANUAL_SOURCE` s `viewer_url` a citací snímku; snímek uživatel uloží z prohlížeče
a načte nástrojem `import_scan`. Žádný obrazový host, deskriptor ani dlaždice se nečtou. Výstupy `find_place`, `list_sources` a `open_source`
nesou `terms_notice` (jen k prohlížení v DigiArchivu, za užití snímků odpovídá uživatel), `list_archives` ho vrací u každého archivu. `find_place` bez okresu a bez `archives`
`trebon` neprohledává (`auto_search=false`); obec, která není sídlem farnosti, se hledá podle názvu farnosti.
Nekatolické větve (`1nC`, `1nc`, `1ne`, `1ng`, `1np`, `1nb` civilní, `1nh`, `1ni`) mají seznam sborů bez písmen; `find_place` je prochází vedle římskokatolických, label nese
církev (`denomination` na knize). `land_records` a `census` procházejí strom (archiv, písmeno nebo rok a soudní okres, uzel s id) nejvýš 24 nových stránek na volání (stránky z mezipaměti se nepočítají, opakované hledání pokračuje dál);
místo je fond, panství nebo obec v jednom roce (`trebon:place:l<id>` pozemkové a vrchnostenské knihy a urbáře, `trebon:place:c<id>` sčítání; matriky `trebon:place:<id>`), `list_sources` bere víc míst a vrací knihy z tabulky fondu (`title`, `storage`, `dating`,
`category`, u sčítání `census_year`, `house_numbers`, `relevance.house_number_covered`; `places` nese lokality fondu). Místo jednoho druhu se v jiném druhu odmítne
(`INVALID_ARGUMENT`). Snímky všech druhů jen přes `viewer_url` (`MANUAL_SOURCE`).
Pace 2 s, robots.txt se dodržuje (`robots_mode` `honor`). Zapíná ho `RUBRIKA_TREBON_ENABLED` (server výchozí false, plugin true),
strop `RUBRIKA_TREBON_GLOBAL_DAILY_REQUESTS` (800). Podrobnosti: `plugin/skills/ancestor-research/references/archives/trebon.md`.

SOA Praha (kód `soapraha`, eBadatelna, Apache Wicket): `parish_registers` (fond NAD 28) a také `census` a `land_records`
z archiválií (`soapraha:unit:<archivalieId>`, snímky `soapraha:unit:<id>:<n>`, `viewer_url` `/a/<id>/<n>`; názvy souborů JPEG nesou název
archiválie). Místa (`soapraha:place:…`) jsou u všech tří druhů stejná. `census`: jedno hledání na lokalitu s typem „Sčítací operáty“, z názvu
jednotky `year`, `district`, `municipality`, `part`, `house_numbers` (rozsah `čp.`; bez rozsahu `parsed=false`, `house_number_covered` `null`),
`digitized_only` skrývá jednotky bez snímků. `land_records`: tři hledání (typy Města a obce, Velkostatky, Rodinné archivy), `category` `land_book`
u pozemkových / gruntovních / purkrechtních knih, jinak `other`; `year_from`/`year_to` vynechají jen jednotky se známou datací mimo rozsah.
Každý druh má vlastní vypínač `RUBRIKA_SOAPRAHA_CENSUS_ENABLED`, `RUBRIKA_SOAPRAHA_LAND_RECORDS_ENABLED` (výchozí false; vedle
`RUBRIKA_SOAPRAHA_ENABLED`). Matriky: `list_sources` hledá matriky
podle přesného názvu lokality (AJAX odpověď v XML, nejvýš 8 stran po 50 řádcích) a místa hledá lokálně v seznamu lokalit ze stránky hledání (jednou stažený, v mezipaměti; česky i německy;
`soapraha:place:<název>-<hash>`, obec a okres v labelu); (nejvýš 3 místa) bere jen knihy s přesnou lokalitou a vynechané spočítá ve varování, roky ze šesti
sloupců (nejasné = `uncertain`), `year_*`, `fund_marks` a `district_archive` odmítá. Knihy `soapraha:book:<matrikaId>`, snímky
`soapraha:book:<matrikaId>:<n>` (`source_type` `jpeg`, `viewer_url` `/d/<id>/<n>`); `open_source` čte detail, `citation` nese
archiv, fond („Sbírka matrik a průvodní listinný materiál, Středočeský kraj (NAD 28)“) a název knihy. `view_scan` v jedné relaci stáhne celý JPEG do mezipaměti a výřezy se dělají lokálně (`zoom_scan` z
mezipaměti nedělá požadavek). Session žije jen uvnitř jednoho volání. Zapíná ho `RUBRIKA_SOAPRAHA_ENABLED` (server výchozí
false, plugin true), strop `RUBRIKA_SOAPRAHA_GLOBAL_DAILY_REQUESTS` (1000). Podrobnosti:
`plugin/skills/ancestor-research/references/archives/soapraha.md`.

Archiv hlavního města Prahy (kód `ahmp`, Archivní katalog firmy Bach): jen `parish_registers`
ze Sbírky matrik: větve katolické, nekatolické (církve), civilní (Magistrát a úřadovny) a Úmrtní protokoly. `find_place` prochází strom katalogu (obvod, městská část, farnost, církev, úřad) a jeho
uzly si pamatuje v paměti uživatele (`metadata_ttl`); jedno volání udělá nejvýš 40 HTTP požadavků (včetně otevření relace; první sestavení indexu s nekatolickými a civilními větvemi stojí řádově desítky požadavků jednou a dřív uložený katolický index se musí jedním výslovným hledáním doplnit) a při neúplném
stromu vrátí varování s výčtem nedočtených větví, hledání se pak zopakuje. Labely nekatolických a civilních míst začínají názvem větve („Nekatolické matriky ›“, „Civilní matriky Magistrátu ›“). Bez výslovného `archives=["ahmp"]` nebo okresu Praha strom
nezačne stahovat. Místa `ahmp:node:<číslo>` (label nese cestu), `list_sources` bere jedno místo, roky ani
`fund_marks` neposílejte; kniha `ahmp:book:<xid>` má signaturu (`JCH N4`; N/O/Z/i určuje událost), roky z názvu,
`image_count` a `detail_url` (trvalý odkaz). `open_source` vrací snímky `ahmp:book:<xid>:<n>` (`source_type`
`zoomify`, `viewer_url` = trvalý odkaz `&scan=<n>`) bez požadavku na archiv; zdroj obrazu se zjišťuje při
`view_scan` jedním požadavkem na snímek. Vypnutý archiv (`RUBRIKA_AHMP_ENABLED`; server výchozí false, plugin true) je jen v `not_searched`.
Session katalogu (cookie) žije jen uvnitř jednoho volání nástroje. Podrobnosti:
`plugin/skills/ancestor-research/references/archives/ahmp.md`.

`kind="census"` u `ahmp`: `find_place` vrací vždy dvě řady Soupisu pražského obyvatelstva 1830–1910 (`ahmp:node:168668`
Pražští příslušníci, `ahmp:node:290432` Cizí příslušníci; bez požadavku na archiv) a obvody sčítání 1869/1880/1890.
`list_sources` na řadě Soupisu vyžaduje `person.surname` v zadání (jinak `INVALID_ARGUMENT`), bere i `person.given_name` a
rok narození (při `event_type=birth` se sebere pásmo ±5 let, ±2 je candidate); půlením seznamu skokem na řádek najde příjmení (jeden strop 35
požadavků na celé volání včetně obnovy relace, 10 stránek a 90 listů na sběr; každý pravopis příjmení a každý česko-německý tvar jména je vlastní běh; při seříznutí `listing_complete=false` a varování). Jednotka
`ahmp:book:<xid>` má `surname`, `given_name`, `birth_year`, `order_no`, `series`. Na obvodu sčítání vyžaduje
`person.house_number`; jednotka nese `house_numbers` a `relevance.house_number_covered`. Roky ani `fund_marks` se nefiltrují.

Policejní přihlášky Prahy 1850–1914 (Národní archiv, kód `na_prihlasky`, aplikace `digi.nacr.cz/prihlasky2`): druh `census`, hledá se jen na výslovné
`archives=["na_prihlasky"]` (`auto_search` je false; bez toho je v `find_place` jen varování, co zapnout) a jen po zapnutí `RUBRIKA_PRIHLASKY_ENABLED`
(server výchozí false; zapnutí v pluginu je věc spouštěče `plugin/server/main.py`). **Server nabízí jen HTTP**, `Archive.allow_plain_http`; hledaná jména putují nešifrovaně
(`terms_notice` u každého výsledku a v `list_archives`). `find_place` vrací vždy jedno místo `na_prihlasky:place:praha` bez požadavku na archiv.
`list_sources` (jedno toto místo; `year_*`, `fund_marks`, `district_archive` se odmítají) vyžaduje `person.surname` (jinak `INVALID_ARGUMENT`), bere
`person.given_name` a při `event_type=birth` pásmo let `year_from`–`year_to` ±5 (do pole `odroku`/`doroku` hledacího formuláře; v jeho HTML jsou pole v opačném
pořadí, než jsou popisky). Jedno volání = jedna relace: vstupní stránka (token v adrese), POST hledání, stránky výsledků po 20, potom detail nejlépe
shodných řádků (celá domácnost); nejvýš 30 požadavků, 3 hledání, 5 stran a 15 přihlášek, při seříznutí `listing_complete=false` a varování. Další
pravopis příjmení (bez diakritiky) a další tvar křestního jména (Josef/Joseph) se zkusí, jen když předchozí hledání nic nenašlo. Jednotka
`na_prihlasky:form:<karton>:<obraz>` má `karton`, `folium`, `citation`, `household` (osoby: `raw`, `surname`, `other_surnames` a `maiden_name` = dívčí příjmení ze závorky (index zná vdanou ženu jen pod příjmením manžela, podle dívčího se nehledá),
`given_name`, `birth_year`, `head` = první uvedená osoba, `matched` = osoba, ke které vedl řádek hledání; sloupec vztahu „Charakter“ se nečte), `index_hits` (řádky hledání, které k ní vedly), `detail_url` = trvalý odkaz bez relace a `relevance` (křestní jméno a rok
narození člena domácnosti s hledaným příjmením jako u Soupisu, ±2 candidate, ±5 needs_review; zadaný otec nebo matka v domácnosti se uvede v důvodu).
`listing_complete` u hledání s roky platí jen pro hledané pásmo let narození (jako u Soupisu); osoby bez roku narození v indexu jsou mimo každé hledání s roky, nenajde-li se nic, zkuste hledání bez let. Detail přihlášky se čte při výpisu (u každého řádku se ověří shoda příjmení, jména a roku s členem domácnosti) a `open_source` ho bez dalšího požadavku převezme z výpisu téhož hledání (jen přihláška bez uložené domácnosti se čte z trvalého odkazu bez relace); vrací jeden snímek `na_prihlasky:form:<k>:<f>:1` (`source_type` `jpeg`, `source` `prihlasky`,
`source_url` `images/<karton 8 číslic>-<obraz 8 číslic>.jpg`, `viewer_url` = trvalý odkaz); `view_scan` stáhne celý JPEG 1000×1333 px do mezipaměti,
`zoom_scan` dělá výřezy lokálně. Podrobnosti: `plugin/skills/ancestor-research/references/archives/na.md` (oddíl o přihláškách).

Národní archiv (kód `na`, Archivní VadeMeCum firmy Bach): jen `parish_registers`, židovské matriky
z Čech, Moravy a části Slezska (fond HBMa, NAD 167, inventář 1646, 1784–1949). Území okresů na `na` nesměruje;
Rubrika ho prohledá jen na výslovné `archives=["na"]` (skill se ptá, byl-li předek Žid). `find_place` čte obsah
inventáře (jednou, 2 požadavky včetně otevření relace; pamatuje se v paměti uživatele, `metadata_ttl`) a hledá v
jeho záhlavích; místa `na:place:<číslo řádku obsahu>`. Praha je rozdělena podle druhu matriky a čtvrti; `label`
nese druh matriky před čtvrtí. `list_sources` bere jedno místo (roky ani `fund_marks` neposílejte), skočí na záhlaví
a přečte jeho řádky po dvanácti (nejvýš 20 stránek inventáře na volání, každá asi 600 kB; celé volání je jedna relace);
kniha `na:book:<entityId>` má číslo inventáře (`number`), druh události a roky z popisu inventáře (`N 1864-1869`,
`(i)` = index), `image_count` a `detail_url` (trvalý odkaz `permalink?xid=<uuid>`). `open_source` vrací snímky
`na:book:<entityId>:<n>` (`source_type` `zoomify`) bez požadavku na archiv; zdroj obrazu se zjišťuje při `view_scan`
po skupinách deseti snímků (relace, prohlížeč knihy, případně jedno listování). Vypnutý archiv
(`RUBRIKA_NA_ENABLED`; server výchozí false, plugin true) je jen v `not_searched`. Podrobnosti:
`plugin/skills/ancestor-research/references/archives/na.md`.

Odkazové archivy (`link_only`: `zao` a `soalitomerice` s ochranou proti botům)
`list_archives` jmenuje, `find_place` je vrací v `not_searched`; nic z nich se nestahuje.

ARON: ID mají prefix `aron:`, snímky `aron:<uuid>:<n>`; view_scan, zoom_scan,
show_saved_evidence, record_finding a report jsou společné s matrikami. Page a ImageRef nesou
`source` (acta_publica/aron), `citation` a ImageRef i `image_key`. Observation má volitelné
`links` [{observation_id, relation: same_person/same_property/related}] v rámci téhož hledání.
Pokrytí v reportu uvádí u knih i archiválií `source`. Roky v `list_sources` (pozemkové knihy) filtruje ARON
na serveru (varování: může vynechat nedatované archiválie); relevance se počítá podle let
v zadání. Archiválie bez podporovaného digitalizátu → UNSUPPORTED_SOURCE s citací v details.

Sčítací operáty: sada = obec/část obce + rok (`census:<číslo>`, snímek `census:<číslo>:<n>`).
Hledání jde jen podle místa, archivu a roku; server posílá pevný formulář s izolovanou session
(POST → 302 → výsledky, cookie jen v rámci jednoho volání). Rok 1921 → UNSUPPORTED_SOURCE
s reason=reading_room_only (metadata a citace, žádné snímky, books se nečerpá). `source` u Page,
ImageRef a položek reportu může být `census`.

Stabilní katastr, písemné operáty D 10 (ARON MZA, `kind="land_records"`, `fund_marks=["D 10"]`): jednotka je katastrální
obec řazená podle německého názvu; podjednotky „Originál“, „Duplikát“ … nesou jen holý název, proto Rubrika ve výpisu i detailu
připojí název katastru z nadřazené úrovně („Duplikát – Lesoňovice (Lesseniowitz), obec: …“). Výpis s D 10 nese v `note` popis fondu.
Digitalizace je stejná jako u ostatních jednotek ARONu (`TILE`, iipsrv).

Indikační skici stabilního katastru (MZA, fond D 9, `kind="cadastral_maps"`; vypínač `RUBRIKA_SKETCHES_ENABLED`,
výchozí false, plugin ho zapíná): `find_place` (parametr `term` je začátek názvu katastru česky nebo německy, `district`
zužuje na okres) vrací katastry jako místa `mza:sketch:place:<signatura>` (label: katastr, německý název, okres, signatura, počet
skic); `list_sources` vypíše všechny skici katastru (`mza:sketch:MOR<číslo>`; rok = rok vyhotovení nebo revize, `year` null = bez
data, `sheet` a `sheets_in_year` u více listů téhož roku, `note`, `other_names`, `other_municipalities`; `relevance.status` je vždy
`candidate`, roky, `fund_marks` ani `district_archive` se odmítají). `open_source` přečte detail skici (jeden požadavek) a vrací
stránky: líc (`scan_number` 1) a rub (2, je-li), `source`/`source_type` `sketch`, `citation` „Moravský zemský archiv v Brně, fond
D 9 Stabilní katastr – indikační skici, <katastr>, <MOR…>“ a `viewer_url` na prohlížeč MZA. `view_scan` a `zoom_scan` skládají obraz
z pevné pyramidy dlaždic 217 × 174 px (4 úrovně; rozměr a seznam existujících dlaždic čte Rubrika z detailu skici, nespouští ho);
úroveň se volí tak, aby dala aspoň 85 % požadovaného rozlišení, a jeden výřez smí mít nejvýš 64 dlaždic (REGION_TOO_LARGE).
Místa bez dlaždice jsou bílá. `find_place`, `list_sources` a `open_source` vracejí `terms_notice`. Změněná struktura stránky
(tabulka, hlavička, skript prohlížeče) je PARSE_ERROR, nikdy prázdný výsledek. Pro Čechy a Slezsko mimo MZA je v registru odkazový
archiv `cuzk` (druh `cadastral_maps`, `link_only`): `find_place` ho vrací v `not_searched` s odkazem na
https://ags.cuzk.gov.cz/archiv/ a důvodem (token, placená data); `district` u tohoto druhu směruje Moravu na `mza`, příhraniční okresy
na `mza` i `cuzk` a ostatní na `cuzk` (`list_archives(district)` to ukáže v `district.cadastral_maps`). Rubrika ČÚZK nevolá.

Rodokmen: FamilyTree = title, root_person, persons [TreePerson: id, given_name,
surname, sex M/F/U, events, note], families [TreeFamily: id, husband, wife, children, marriage].
TreeEvent: type birth/baptism/death/burial/occupation/residence (marriage jen u rodiny), date
(TreeDate: year, month?, day?, qualifier exact/about/before/after), place, value, age, cause,
confidence documented/probable/hypothesis, sources [{research_id, observation_id}], note.
Citovat lze jen uložený `record` nebo `index_entry` s obrazovým dokladem téhož vlastníka (max 20
hledání). `documented` vyžaduje aspoň jeden `record`; samotný index stačí jen na `probable`.
GEDCOM: jeden SOUR na knihu, citace PAGE „snímek N[, folio]“, QUAY 3/2/1 podle jistoty, DATA TEXT
= pracovní přepis, NOTE = viewer_url; nedoložené údaje nesou poznámku. `documented_only` vynechá
nedoložené údaje a osoby, osoby mezi doloženými generacemi ponechá jen se jménem (warnings).
Mermaid: flowchart TB, uzly osob a rodin (⚭), popisky escapované; plná čára = doloženo.

SearchQuery: raw_query (povinné), place_hint, event_type birth/marriage/death,
year_from, year_to, person, book_url (vše ostatní může být null).
PersonConstraints: given_name, surname, father, mother, spouse, house_number, notes.
Odhadované údaje patří do explicitních poznámek, ne do „ověřených“ tvrzení.

Period metadata: stav known/absent/unknown/uncertain, seznam intervalů a raw.
Události a rejstříky jsou oddělené. Neznámé/nejisté nepřekrytí nesmí knihu
bezpečně vyloučit. source_id knihy není její zobrazené number.

ImageRef: image_id, page_id, book_id, archive, robots_disallowed, source_width/height, source_bbox
(x,y,width,height), width/height dodaného obrazu, role, sha256, viewer_url,
scan_number, manifest_sha256, viewer_link_verified a u ručně importovaných snímků provenance a scan_label
(viewer_url je pak null, pokud uživatel adresu nezadal). Souřadnice zoom_scan
se vždy vztahují k width/height dodaného image_id, ne přímo k celému zdroji.
`robots_disallowed=true` znamená, že cestu zakazuje robots.txt archivu a archiv má režim `user_triggered`
(požadavek vyvolal uživatel, zákaz se toleruje a zaznamená); používají ho AHMP a Národní archiv, výchozí
je `false`. Report nese `coverage.robots_tolerated`, seznam hostů, u nichž se zákaz tolerovat musel (jinak prázdný).
Zdroj i výřezy jsou skutečné pixely; používá se změna velikosti a JPEG
komprese, nikoli generativní „oprava“ písma.

Observation: kind layout/index_entry/record/reviewed_region/next_step, image_id,
text, fields, normalized_fields, uncertainties, matches, contradictions,
is_candidate, coverage_status, historical_page. Server připojí evidence a
authorship=model_interpretation_not_human_verified. Ani vymyšlený text se
schématem nestává pravdivý; vazba na zdroj je nutná, nikoli dostatečná.

## Stavy a chyby

Chyba má isError=true a textový JSON `ok=false, error={code,message,details}`.
PARSE_ERROR není prázdná tabulka. SOURCE_ACCESS_DENIED/RATE_LIMITED zastavuje
nové požadavky a drží persistentní cooldown. SOURCE_ACCESS_DENIED s `details.reason=bot_protection`
znamená bránu proti botům (nebo přihlášení); Rubrika ji neobchází. RATE_LIMITED nastane i tehdy, když
archiv žádá `Crawl-delay` nad 60 s (nic se neodešle); 429 a 503 drží cooldown podle `Retry-After`.
Denní stropy chrání archivy: per archiv na všechny požadavky (`Archive.daily_requests_setting`, u VČA
`RUBRIKA_VCA_GLOBAL_DAILY_REQUESTS`), u MZA per aplikace (Acta Publica, obrazový server, ARON, sčítání, skici)
a per kniha 150 obrazových výsledků za den (`book_daily_images`); po vyčerpání BUDGET_EXCEEDED s `details.scope`.
Stropy platí pro všechna hledání stejně, novým hledáním se neobnoví; find_place archiv s vyčerpaným stropem
uvede v `not_searched` a ostatní prohledá. BUDGET_EXCEEDED dovoluje dál uložit pozorování a report, ale ne
obcházet strop novými zdrojovými požadavky.
Jedno hledání není přídělem omezené (knihy, stránky, požadavky). CHECKPOINT není strop: po
`RUBRIKA_CHECKPOINT_IMAGES` (100) snímcích z archivů od posledního potvrzení view_scan a zoom_scan stojí, dokud
uživatel nesouhlasí s pokračováním (continue_research). Ochrana proti zacyklenému agentovi; import_scan,
show_saved_evidence a výřezy z importovaných snímků se nepočítají.
Neúplný obraz není úspěšný obrazový výsledek. Neopakovatelné chyby nejsou
nekonečně retryovány.

Další kódy: INVALID_ARGUMENT, INVALID_SOURCE, INVALID_REGION, NOT_FOUND,
SEARCH_EXPIRED, REVISION_CONFLICT, IDEMPOTENCY_CONFLICT, LIVE_DISABLED,
SOURCE_UNAVAILABLE, SOURCE_CHANGED, UNSUPPORTED_SOURCE, INCOMPLETE_IMAGE,
REGION_TOO_LARGE, PAYLOAD_TOO_LARGE, CHECKPOINT a TOOL_TIMEOUT. Odpověď archivu větší než limit
daného druhu dat je SOURCE_UNAVAILABLE s `details.reason=too_large`.

finish_research rozlišuje stav běhu stopped/paused a stop_reason; kandidáti
jsou samostatný seznam, i přerušený běh může něco najít. full_book_search_verified
je v této verzi vždy false. coverage_status=read je tvrzení modelu o konkrétní
oblasti, ne osvědčení úplnosti. Kandidáti předchozí revize jsou označeni
requires_reassessment=true, nikoli potichu přepsáni podle nového dotazu.

Limity obrázků a payloadu jsou voleny konzervativně a musejí se potvrdit
v cílovém klientovi. Metadata a přenosy se počítají na serveru, nikoli podle
modelového odhadu. Obnovovat můžete známé research_id; server nenabízí seznam
všech hledání dostupný agentovi ani klientské administrační API.
