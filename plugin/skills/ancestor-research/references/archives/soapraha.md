# Státní oblastní archiv v Praze, eBadatelna (kód `soapraha`)

Přečti před prací s eBadatelnou. Obecný postup je v SKILL.md.

Působnost: Středočeský kraj (okresy Benešov, Beroun, Kladno, Kolín, Kutná Hora, Mělník, Mladá Boleslav,
Nymburk, Praha-východ, Praha-západ, Příbram, Rakovník); hlavní město Praha patří AHMP. Matriky jsou ve
fondu NAD 28 „Sbírka matrik a průvodní listinný materiál, Středočeský kraj“ (asi 11 800 knih). Rubrika čte
**matriky** (`parish_registers`) a ze záložky Archival records (`SearchArchivaliePage`) také **sčítání lidu**
(`census`) a **pozemkové knihy** měst a velkostatků (`land_records`). Každý z těch dvou druhů má vlastní vypínač
(`RUBRIKA_SOAPRAHA_CENSUS_ENABLED`, `RUBRIKA_SOAPRAHA_LAND_RECORDS_ENABLED`, server výchozí false); vypnutý druh
vrací `LIVE_DISABLED` nebo skončí v `not_searched`. Jiné archiválie (listiny, mapy, fotografie, církevní kroniky,
spolky, školy) Rubrika nevypisuje.

## Podmínky a citace

eBadatelna **nezveřejňuje podmínky užití** snímků a nemá robots.txt (odpoví HTML chybovou stránkou, tedy
žádná pravidla). Badatelský řád SOA Praha žádá při využití informací uvést název archivu, fond a bližší
označení archiválie. Proto každý doklad nese `citation` („Státní oblastní archiv v Praze, Sbírka matrik
a průvodní listinný materiál, Středočeský kraj (NAD 28), <název knihy>“) a `viewer_url`; uveď je u každého nálezu. Snímky nešiř.
Stahují se bez přihlášení; přihlášení a ZIP celé knihy Rubrika nikdy nepoužívá.

## Hledání místa

`find_place` porovnává výraz lokálně se **seznamem lokalit, který archiv vkládá do stránky hledání**
(asi 9 000 lokalit s německými a staršími názvy, obcí a okresem). Seznam se stáhne jednou (jedna relace,
asi 800 kB, bez hledání) a uloží do mezipaměti uživatele; další `find_place` nedělá žádný požadavek.
Hledá se česky i německy (celá slova, jinak slova od začátku). ID `soapraha:place:<název>-<hash>` určuje
jednu položku seznamu (název, obec, okres); label nese obec a okres, takže stejnojmenné lokality se
rozliší. Seznam neříká, který název je český; německý název se při výpisu hledá přes sousední název téže
obce a Rubrika to řekne varováním. Server hledá vždy v celém rozsahu let (1000–2100); roky zadání jen
řadí relevanci.

## Výpis knih

`list_sources` (nejvýš 3 místa na volání) hledá v eBadatelně podle přesného názvu lokality (server hledá
začátky slov; jedno hledání čte nejvýš 8 stran po 50 matrikách, jinak `listing_complete=false`) a vezme
matriky, které mají lokalitu **přesně** v seznamu; ostatní nalezené (např. Starý Kolín) vynechá a řekne
kolik. Má-li název víc lokalit, ponechá matriky okresu místa nebo matriky, které jmenují jeho obec. Roky a druhy událostí
plynou ze šesti sloupců výsledků (narození, oddaní, zemřelí, jejich rejstříky). Řádek bez jediného roku je
`needs_review`, nečitelný rok také; `excluded` jen podle známých dat. `year_from`, `year_to`, `fund_marks`
a `district_archive` se odmítají. Výsledek hledání se pamatuje v mezipaměti uživatele.

Kniha, u níž není jisté, že patří k vybrané lokalitě (německý název hledaný přes sousední název, stejnojmenná
lokalita v jiném okrese), nikdy není `candidate`: je `needs_review` s důvodem v `review_reason`; ověř lokality knihy
v detailu (`open_source`). `find_place` řadí místa okresu z argumentu `district` (nebo z place_hint) první; přesné
shody se neřežou při 15, a je-li výpis přesto zkrácen, varování jmenuje vynechané okresy. Nepodařilo-li se
přiřadit žádnou nalezenou matriku, je `listing_complete=false`. `excluded` u události vychází jen ze sloupců katalogu.
Pole `description` je text z popisu knihy v eBadatelně, ne tvrzení Rubriky.

## Otevření a snímky

`open_source` načte detail knihy (lokality s variantami názvů, popis, počet snímků) a vrátí snímky
`soapraha:book:<matrikaId>:<n>`; `scan_number` je pořadí snímku, ne folio. Snímek je **celý JPEG**
(asi 1 až 3 MB, dvoustrana kolem 4000×3000 px); archiv nezná výřezy ani dlaždice. `view_scan` v jedné
relaci otevře stránku prohlížeče snímku a stáhne JPEG (bez ní ho server nevydá), uloží ho do mezipaměti
a výřezy (`zoom_scan`) se dělají lokálně; opakovaný pohled na stejný snímek nedělá žádný požadavek.

## Sčítání lidu (kind=census)

Sčítací operáty okresních úřadů jsou v eBadatelně po **obcích a osadách a rozsazích čísel popisných**, rok od rok
(v katalogu 1869, 1880, 1890, 1900, 1910; co je digitalizováno, je vidět z výpisu, u obce chybí někdy celé roky).
Název archiválie má tvar „Sčítání lidu 1900: Okres Slaný - obec Hnidousy - osada Motyčín, čp. 1-50“; z něj Rubrika
čte `year`, `district` (okres sčítání, historický politický okres), `municipality`, `part` a `house_numbers`
(`start`, `end`, `numbers`; `parsed=true` jen když rozsah pochopila celý). Jednotka bez rozsahu v názvu (celá obec)
má `parsed=false`, `house_number_covered` je `null` a číslo domu z ní nikdy nevylučuje; jednotka s rozsahem, který číslo
nepokrývá, má `false`, je `needs_review` s důvodem „dům N není v rozsahu čp. X–Y této jednotky“ a řadí se poslední. Okres v názvu sčítání je **historický politický okres** (např. Slaný pro dnešní okres Kladno); Rubrika to řekne varováním a stejnojmennou lokalitu nikdy nevyřadí jen podle názvu okresu (vynechá ji, jen když okres i obec v názvu ukazují na jinou lokalitu, a vynechané názvy vypíše). První snímek jednotky je zpravidla první dům rozsahu, další následují za sebou; snímek pro dům N odhadni z pořadí. Jednotka otevřená jen odkazem se řídí typem archiválie z detailu (sčítání, pozemková kniha); neznámý typ se neotevře. `find_place(kind="census", archives=["soapraha"])` vrací tytéž lokality jako
u matrik (obec, okres v labelu); `list_sources` (nejvýš 3 místa, jeden rok jako `year_from`=`year_to` nebo bez let)
pošle archivu jedno hledání na lokalitu s typem „Sčítací operáty“. **Hledá se podle lokality archiválie:** sčítání
osady je pod její vlastní lokalitou (najdi osadu zvlášť), sčítání obce s osadami pod lokalitou obce jen u částí
psaných jako obec. Jednotky bez snímků (0 skenů) se skrývají, `digitized_only=false` je ukáže. `year_from`/`year_to`
vynechávají jen jednotky se známým rokem mimo rozsah. Snímky sčítání jsou velké (i 3 až 8 MB, kolem 8000×5000 px).

## Pozemkové knihy (kind=land_records)

`list_sources(kind="land_records")` pošle na lokalitu tři hledání podle typu archiválie („Města a obce“,
„Velkostatky“, „Rodinné archivy“). Kategorie: `land_book` u názvů s „pozemková / gruntovní / purkrechtní kniha“
(včetně panských „pozemková kniha dominikální / emfyteutická“), `other` u ostatního (urbáře, knihy listin a smluv,
soupisy poddaných, rejstříky, kroniky měst). Panství (Velkostatek …) vypisuje knihy pro všechny lokality, které
uvádí, takže výpis vsi obsahuje i knihy celého panství; podle `fund` a `title` poznáš, čí kniha to je. Datace je
z archivního sloupce (rozsah let, rok, přesné datum); bez ní je jednotka `needs_review`. Signatura a původní
(často německý) název knihy jsou v `signature` a `original_title`, `storage_location` (SOkA, kde je kniha uložena)
přibude po `open_source`. Citace: „<SOkA> (eBadatelna SOA Praha), fond <fond>, sign. <signatura>, <název>“.

Stejnojmenná lokalita v jiném okrese: sčítání s okresem jiné lokality téhož jména se vynechá a Rubrika řekne
kolik; u ostatních jednotek, u nichž z názvu nelze poznat, zda patří k obci místa, je `needs_review`
(`review_reason`).

## Tempo a zvláštnosti

Vyhledání archiválií čte nejvýš 8 stran po 50 řádcích na hledání (jinak `listing_complete=false`); úplná neprázdná
odpověď se pamatuje v mezipaměti. Snímek archiválie je `soapraha:unit:<archivalieId>:<n>` (prohlížeč `/a/<id>/<n>`;
název souboru nese název archiválie, např. `Scitani_lidu_1900:_Okres_X_-_obec_Y,_cp._1-50__001.jpg`).
Rozestup požadavků 1 s, denní strop `RUBRIKA_SOAPRAHA_GLOBAL_DAILY_REQUESTS` (1000). Úvodní stránka
hledání má asi 800 kB (stahuje se jednou za `metadata_ttl`). Relace (cookie) žije jen uvnitř jednoho volání. Změní-li se struktura stránek,
Rubrika skončí chybou PARSE_ERROR; pak hledej v prohlížeči. Zapíná se `RUBRIKA_SOAPRAHA_ENABLED`
(server výchozí false, plugin true).
