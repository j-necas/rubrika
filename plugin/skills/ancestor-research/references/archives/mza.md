# Moravský zemský archiv v Brně (kód `mza`)

Přečti před prací s MZA. Obecný postup je v SKILL.md; tady jsou jen znalosti o tomto archivu.

Působnost: okresy Blansko, Brno-město, Brno-venkov, Břeclav, Hodonín, Jihlava, Kroměříž,
Prostějov, Třebíč, Uherské Hradiště, Vyškov, Zlín, Znojmo a Žďár nad Sázavou (kromě
příhraničních farností, které mohou ležet v sousedním archivu). Čtyři aplikace, čtyři druhy pramene:

| kind | Aplikace | ID |
|---|---|---|
| parish_registers | Acta Publica (matriky) | číselné ID obce i knihy, snímky `page_…` |
| land_records | ARON (pozemkové knihy, katastry) | `aron:<uuid>`, snímek `aron:<uuid>:<n>` |
| census | Sčítací operáty | `census:obec:…`, `census:cobec:…`, sada `census:<číslo>` |
| cadastral_maps | Indikační skici stabilního katastru (fond D 9) | katastr `mza:sketch:place:<signatura>`, skica `mza:sketch:MOR<číslo>`, snímek `mza:sketch:MOR<číslo>:<1\|2>` |

Místa sčítání nesou `place_kind` (obec / cobec). Hledání podle obce vrací i sady jejích částí;
hledání podle části obce není omezené na mateřskou obec, proto u něj vždy použij district_archive.

## Snímky: ruční režim (od listopadu 2026)

MZA automatizované stahování snímků nedovoluje: prohlížeč matrik v Acta Publica chrání ověřením proti robotům
a obrazový server snímky ARONu, sčítacích operátů a skic nástrojům odmítá (403). Katalogy (find_place,
list_sources, open_source) fungují dál, ale **view_scan a zoom_scan vrátí u všech čtyř druhů MANUAL_SOURCE**
(s `viewer_url` a `scan_number`) a archiv tím nic nestojí. Neobcházej to a nezkoušej snímek získat jinudy.

- Z katalogu vyber knihu a snímek jako obvykle (období, rejstřík, odhad poměrem), pak uživateli dej `viewer_url`
  a číslo snímku. U matrik vede `viewer_url` na stránku knihy; „Části matriky“ tam ukazují rozsahy snímků
  po obcích a druzích zápisů. Uživatel přejde na snímek, přiblíží zápis, uloží snímek obrazovky do Stažených
  souborů a ty ho načteš přes import_scan (SKILL.md, 6a) s citací archivu, knihy a snímku.
- Na orientaci (rok v záhlaví, hranice částí) stačí snímek celé dvoustrany; ke čtení jmen požádej o přiblížený
  zápis. Žádej jen snímky, které opravdu potřebuješ, a řekni proč (který rok nebo zápis na nich čekáš).
- Dřívější doklady a výřezy z MZA uložené v hledání zůstávají platné (show_saved_evidence, zpráva, GEDCOM).

## Matriky (Acta Publica)

- Katalog řadí knihy k **obcím**, ne k farnostem: hledej obec, kde se událost stala, ne sídlo
  farnosti. Fara vedla pro přifařené vsi často samostatné knihy, které se pod sídlem farnosti
  neukážou, a kniha sídla ostatní vsi obsahovat nemusí (`village_count` je počet obcí v knize).
  Chybí-li hledaná osoba ve správném měsíci knihy, ověř nejdřív, že kniha obec vůbec pokrývá.
- Katalog má pro každou knihu samostatná období narozených, oddaných a zemřelých a jejich
  rejstříků. Chybějící nebo nejisté období knihu nevylučuje (needs_review).
- `book_url` z uživatelova odkazu obsahuje číselné ID knihy v **cestě detailu**; zobrazené číslo
  knihy je jiné a nedá se z něj ID odvodit.
- list_sources u matrik přijímá jediné místo a **žádné roky ani fund_marks**; relevance se
  počítá ze zadání hledání.
- Kniha mívá rejstřík vzadu, vpředu nebo zvlášť; postup práce s ním je v SKILL.md, oddíl 3.

## Pozemkové knihy a katastry (ARON MZA)

- Obec bývá v ARONu vedená víc záznamy (obec, osada, část); do list_sources jich lze předat
  nejvýš 5. find_place vrací jen nejlepší shody, proto při neúspěchu přidej okres nebo farnost.
- Roky filtruje server ARONu a může vynechat nedatované archiválie (varování v odpovědi).
- `fund_marks` (např. `["C 17","D 4"]`) zužuje výpis na fondy. Orientační přehled, neúplný,
  řiď se výsledky výpisu:
  - D 1 lánové rejstříky (od 1656): držitelé usedlostí podle lánů;
  - D 2 rektifikační akta, D 3 dominikální fase, D 5 urbariální fase (1667–1777);
  - D 4 tereziánský katastr (kolem 1750): poddaní a jejich pozemky;
  - D 6 josefinský katastr (1785–1789): po obcích, s čísly domů a parcel;
  - C 17 pozemkové (gruntovní) knihy: převody usedlostí; knihy jednotlivých obcí často začínají
    až kolem 1800, starší zápisy bývají v knihách panství.
- Stabilní katastr, písemné operáty (**D 10**): viz oddíl níže; většina je digitalizovaná.
- Formální názvy bývají německé (Grund Buch, Kaufbuch, Waisenbuch).
- Citace archiválie pochází z ARONu a patří do reportu spolu s viewer_url.

## Stabilní katastr: písemné operáty (D 10, ARON)

- Jednotkou je **katastrální obec** (ne dnešní obec), řazená v archivu abecedně podle **německého** názvu
  (Lesoňovice = Lesseniowitz); obec proto hledej česky i německy, a je-li dnes částí jiné obce, i pod názvem katastru.
  Pod katastrem jsou podjednotky „Originál“, „Duplikát“, „Triplikát“ a „Reambulace: parcelní protokol, archy pozemkové“.
  V list_sources mají jen takový holý název; Rubrika k němu připojí katastr („Duplikát – Lesoňovice (Lesseniowitz), obec: …, okres: …“).
- Obsah jsou **parcelní protokoly**: každá parcela s držitelem (jméno, číslo domu), kulturou a výměrou, sepsané při zaměřování
  v letech 1824–1843; kopie a reambulace sahají do 70. let 19. století. Slouží ke spojení čísla popisného, parcely a majitele
  kolem roku 1830. Písmo je německé (kurent), jména jsou zapsána německy.
- Podle ARONu je **digitalizováno** zhruba 3 600 jednotek (kopií protokolů) z asi 3 200 katastrů, na stejném obrazovém serveru jako matriky;
  otevírají se normálně (list_sources s `fund_marks=["D 10"]`, open_source), snímek ale dodá uživatel (ruční režim výše). Neplatí, že by D 10 bývalo
  bez digitalizátu; nedigitalizovaná jednotka se pozná stejně jako jinde (UNSUPPORTED_SOURCE).
- Výpis místa s mnoha fondy stáhne značky fondů (nejvýš 10 na výpis); použij `fund_marks=["D 10"]`, a nenašel-li se D 10, zkus
  vypsat bez `digitized_only`.
- Související fondy: D 8 vceňovací operáty, **D 9 indikační skici** (mapa téhož katastru, další oddíl), D 11 sbírka katastrálních
  map, D 12 popisy hranic.

## Indikační skici stabilního katastru (D 9, kind=cadastral_maps)

- Fond „D 9 Stabilní katastr – indikační skici“: ručně kreslená mapa každého katastru (kolem 1825–1845) s parcelami,
  **čísly popisnými a jmény držitelů** přímo v obraze; k některým katastrům jsou i pozdější revize (1870., 1890., 1910. léta).
  Morava a část Slezska, zhruba 4 400 skic. Nejužitečnější pro otázku „kde stál dům čp. X“ a „kdo držel pozemky kolem“;
  spolu s parcelním protokolem D 10 téhož katastru (viz výše) dává číslo domu → parcela → držitel.
- Zapnutí: `sketches_enabled` (vypínač `RUBRIKA_SKETCHES_ENABLED`, výchozí vypnuto, plugin zapíná). Skici prohlížej na
  výslovný pokyn uživatele; výřezy ukazuj jen v konverzaci a vždy uveď odkaz na archiv (`viewer_url`).
- Hledání: find_place s `kind="cadastral_maps"`; hledá se podle **začátku** názvu katastru, česky nebo německy
  (`Znojmo`, `Znaim`); okres (`district`) hledání zúží. Místo je katastr s okresem a signaturou (`mza:sketch:place:<signatura>`);
  katastry stejného jména v různých okresech jsou různá místa, nesměšuj je. Bez výsledku zkus německý název nebo sousední katastr;
  nález nebo nenález nic neříká o existenci skici v jiné podobě.
- list_sources vypíše všechny skici katastru (bez filtru podle let, `fund_marks` ani `district_archive`), nejstarší první. `year` je rok
  vyhotovení nebo revize, 0 (`year` null) je skica bez data. Má-li rok víc listů (`sheets_in_year` > 1), patří k jedné skice; otevři všechny.
  `note` archivu („neúplná“, „Bez data“) a `other_names` / `other_municipalities` (další názvy, osady spadající do katastru) čti,
  vysvětlují chybějící části.
- open_source dá dvě „stránky“: líc (`scan_number` 1) a rub (2, je-li). Na rubu mohou být poznámky; jeho obsah se liší, proto ho prohlédni. Citace:
  „Moravský zemský archiv v Brně, fond D 9 Stabilní katastr – indikační skici, <katastr>, <MOR…>“.
- Snímek skici dodá uživatel (ruční režim výše): v prohlížeči archivu přiblíží oblast kolem hledaného domu a uloží snímek
  obrazovky; na čtení čísel parcel a domů stačí výřez zhruba 650×520 px skici v plném rozlišení.
- Podmínky: obrazy jsou k prohlížení; publikovat je jinde bez souhlasu MZA nelze a zasahovat do technické podstaty stránek se nesmí.
  Rubrika skici nestahuje; snímek obrazovky ukazuj jen v konverzaci.
- **Čechy a Slezsko mimo MZA**: skici jsou v Národním archivu (Čechy) a Zemském archivu v Opavě (Slezsko), obojí přes archiv ČÚZK
  (https://ags.cuzk.gov.cz/archiv/). Ten Rubrika nevolá (mapové služby vyžadují token, který ČÚZK vydává jen vlastní aplikaci,
  a data jsou placená); vrací jen odkaz (`not_searched`, archiv `cuzk`) a pokyn, jak katastr v mapě najít.

## Sčítací operáty (1857–1921)

- Sada je obec (nebo část obce) a rok. Hledá se jen podle místa a roku, ne podle osoby ani čísla domu.
- Místa nejsou rozlišená podle okresu. Parametr `district_archive` v list_sources (okresní archiv,
  např. `"Znojmo"`) zúží sady na sbírku daného okresního archivu; u výsledků porovnej
  `district_archive` a fond. Rok se zadává jako `year_from = year_to`; fund_marks se nepoužívá.
- Sada nese rozpis čísel popisných; `house_number_covered` říká, zda hledaný dům v rozpisu je
  (null = nelze určit). Domy se mezi sčítáními přečíslovávaly, sadu mimo rozsah proto nevylučuj
  bez rozmyslu. Archy bývají řazené podle čísel domů: odhadni snímek poměrem a upřesni podle
  přečtených čísel.
- **Sčítání 1921** je podle MZA přístupné jen v síti badatelen MZA. Server vrátí
  UNSUPPORTED_SOURCE s `reason=reading_room_only`, metadata a citaci. Řekni uživateli, že sadu
  musí prohlédnout v badatelně, a snímky nezískávej jinak.
