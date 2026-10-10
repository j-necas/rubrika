# Státní oblastní archiv v Plzni, Porta fontium (kód `pf`)

Přečti před prací s Porta fontium. Obecný postup je v SKILL.md.

Působnost: Plzeňský a Karlovarský kraj (okresy Domažlice, Klatovy, Plzeň-město, Plzeň-jih,
Plzeň-sever, Rokycany, Tachov, Cheb, Karlovy Vary, Sokolov), na hranici i Rakovník;
list_archives(district=…) vrátí primární i případný druhý archiv. Okres je vždy dnešní.
Portál (https://www.portafontium.eu) provozuje SOA Plzeň. Matriky jsou ve fondu č. 10014
„Sbírka matrik západních Čech“, i pro oblasti Chebska, Karlovarska a Sokolovska; v okresních
archivech (SOkA Cheb, Karlovy Vary, Sokolov) je tedy nehledej. Portál nabízí i kroniky,
úřední knihy a sčítání, Rubrika čte **matriky** (`parish_registers`), **úřední knihy**
(`land_records`) a **sčítání lidu** (`census`); kroniky, listiny, fotografie a mapy ne. Zápisy jsou
česky i německy, nejstarší kolem roku 1545. Úřední knihy a sčítání mají vlastní vypínače
(`RUBRIKA_PF_LAND_RECORDS_ENABLED`, `RUBRIKA_PF_CENSUS_ENABLED`); nejsou-li zapnuté, `list_archives`
je v `available_kinds` neukáže.

## Podmínky použití: řekni je uživateli

Snímky a jejich kopie smí uživatel mimo portál použít jen podle § 92 autorského zákona: pro
osobní potřebu, pro vědecké nebo vyučovací účely, nevýdělečně a s uvedením zdroje včetně
internetové adresy. Bez souhlasu provozovatele je nezveřejňuj a nešiř; komerční užití je
zakázáno. Podmínky: https://www.portafontium.eu/terms-of-use. Rubrika proto vrací pole
`terms_notice` (list_archives, find_place, list_sources, open_source) a u každého dokladu
`viewer_url` (adresa snímku v portálu) a `citation` s adresou knihy.

- **Jednou za hledání**, při prvním použití pf, uživateli krátce řekni: Porta fontium dovoluje
  kopie používat mimo portál jen pro osobní, vědecké nebo vyučovací účely, nevýdělečně a s uvedením
  zdroje a adresy; snímky nezveřejňuj. Neopakuj to u každého snímku.
- U každého nálezu z pf uveď `citation` (obsahuje adresu knihy v portálu) a `viewer_url`.
- Výřezy se předávají také AI službě uživatele (viz README); je to jeho rozhodnutí, ne tvoje.
  Nabízej výřezy jen pro uživatelovo bádání.

## Tempo

`robots.txt` portálu žádá **Crawl-delay 10 s** mezi požadavky a Rubrika ho dodržuje. Znamená to:
hledání místa trvá desítky sekund (načtení robots.txt a stránky výsledků), výpis knih
jednu až deset stran po 10 s, otevření knihy asi 20 s (detail a prohlížeč) a každý nový
snímek asi 20 s (popis snímku a výřez), další výřez z téhož snímku asi 10 s. `find_place` bez okresu Porta fontium neprohledává (je pomalé); zadej okres z jeho území nebo `archives=["pf"]`. Neopakuj volání
a nespouštěj je souběžně; drž se rozpočtu. Denní strop je `RUBRIKA_PF_GLOBAL_DAILY_REQUESTS`
(výchozí 800 požadavků).

## Hledání místa

Portál nemá našeptávač obcí. `find_place` prohledá matriky podle pole „Místo“ (celá slova,
ne části slov; „Demol“ nenajde Demolov) a **z řádků výsledků odvodí místa**, včetně německých
názvů v závorkách (Plzeň (Pilsen), Abertamy (Abertham)); hledat lze česky i německy. Používá se
jen první strana výsledků (40 knih); varování ti řekne, že jsou další. Místa stejného jména se
neodlišují podle okresu, ověř obec podle původce knihy a okolních míst.

ID: `pf:place:<název bez diakritiky>` (místo, společné pro všechny druhy pramenů), `pf:book:<archiv>/<kniha>` (kniha, např.
`pf:book:soap-pn/abertamy-04`), snímek `pf:book:<archiv>/<kniha>:<n>`. `archiv` v ID je část
adresy portálu (`soap-pn` = SOA Plzeň, `amp` = Archiv města Plzně, který portál také zpřístupňuje).

## Výpis knih

`list_sources` znovu spustí hledání pro každé místo (nejvýš 2 místa na volání), načte celkem nejvýš
12 stran po 40 knihách (jinak `listing_complete=false` a varování) a knihy bez daného místa vynechá
s varováním. Hledání se nezužuje podle typu události, aby nezmizely knihy bez značek; relevance
vychází z roků a typu události v zadání a kniha bez čitelných značek je `needs_review`. Prázdný
výsledek portálu není důkaz, že knihy neexistují (celá slova, německý název); výpis pak není úplný.
`year_from`, `year_to`, `fund_marks` a `district_archive` se odmítají. Roky knihy a typy plynou ze značek výpisu: `*` narození, `oo` oddaní,
`+` zemřelí, `i` index. „1648-1713 *, *i“ je matrika narozených i index narozených.
Nečitelná datace nebo značky dají `needs_review`, nikdy `excluded`.
Signaturu (`number`) tvoří nadpis knihy (např. „Plzeň 141“, „Plzeň - U Panny Marie Růžencové 08“),
původce (`creator`) zjistíš až po otevření.

## Otevření a snímky

`open_source` načte detail a prohlížeč knihy: archiv, fond, signatura, původce, místa, typy,
`citation` a všechny snímky (`total`). Snímky jsou celé stránky matriky včetně desek (klíč končí
`-x`, `-n` běžné strany, `-r` rejstřík). `scan_number` je pořadí snímku, ne folio. `view_scan` a
`zoom_scan` používají IIIF; `viewer_url` vede na snímek v prohlížeči portálu
(`/iipimage/<nid>/<klíč snímku>`).

## Úřední knihy (`land_records`)

Záložka „Úřední knihy“ (`/searching/amtsbuch`): pozemkové knihy panství a měst, urbáře, městské a
radní knihy, listinné protokoly, soupisy lidu. `find_place(kind="land_records", archives=["pf"])`
odvodí místa z řádků výsledků stejně jako u matrik (pole „Místo“, celá slova, i německy; okrsky
města „Obvod <město> …“ patří k městu). `list_sources` hledá místo **dvakrát**: nejdřív s filtrem
typu knihy „pozemková kniha“ (ty jsou `category="land_book"`), pak bez filtru (ostatní úřední knihy,
urbáře a spol., jsou `category="other"`). Dvě hledání znamenají dvakrát tolik stran po 10 s a sdílí
limit 12 stran na volání; nestihne-li se první průchod, jsou knihy `category_certain=false` a
varování to řekne. Kniha panství uvádí ve svém seznamu míst všechny vsi panství, proto výpis obsahuje
i knihy, které se obce týkají jen okrajově. Datace („1764–1853“) je rozsah celé knihy; nečitelná
datace dá `needs_review`, nikdy `excluded`. `year_from`/`year_to` filtruje Rubrika sama a
knihy bez čitelné datace ponechá. Rozsah `fund_marks` a `district_archive` se odmítá.

ID: `pf:amtsbuch:<archiv>/<fond>/<kniha>` (např. `pf:amtsbuch:soap-pn/vs-dolni-lukavice/k076`),
snímek `…:<n>`. `open_source` přečte detail (signatura, fond, původce, typ knihy, místa) a
prohlížeč; podle „Typu knihy“ v detailu se upřesní `category`. Citace:
„Státní oblastní archiv v Plzni, <název fondu>, sign. K 76, <adresa knihy>“. Snímky jsou IIIF
(`IIIF=amtsbuch/…`).

## Sčítání lidu (`census`)

Záložka „Sčítání lidu“ (`/searching/census`) vypisuje výsledky jen v seznamovém zobrazení
(`mode=0`); Rubrika ho tak vždy volá. `find_place(kind="census", archives=["pf"])` bere jako místo
obec (první položku sloupce Místo); část obce („Chodov - Pec“, předměstí) je v poli `part` a patří k
obci. Jednotka (`pf:census:<archiv>/<rok>/<obec>`) je **obec (nebo její část) v jednom roce**
(1857, 1869, 1880, 1890, 1900, 1910, 1921 podle obce); řádky, které portál vede po domech (1921),
Rubrika sloučí do jedné jednotky. `list_sources(kind="census", year_from=N, year_to=N)` bere jeden
rok (nebo žádný). `house_numbers` ve výpisu jsou jen z názvů řádků (`parsed=false`, takže
`house_number_covered` je `true` nebo neznámé, nikdy nevyloučí); po otevření jsou v `unit.house_numbers`
čísla z rozpisu domů portálu („Nr. 1 · Nr. 2 …“).

**Snímky jsou po domech.** Otevření jednotky přečte detail (rozpis domů) a prohlížeč jednoho domu:
domu z `person.house_number`, nebo nenajde-li se, prvního domu, který portál ukazuje (`unit.selected_house`).
Jiný dům otevři ID `pf:census:<archiv>/<rok>/<obec>@<číslo domu>` (rozsah domů je v poznámce
odpovědi); každé takové otevření se počítá jako otevřená kniha. Rozpis může mít rozsah („Nr. 1 – Nr. 500“
jeden prohlížeč pro celý rozsah). `scan_number` je pořadí snímku uvnitř domu.

## Ochranné lhůty

Narození 100 let, oddaní 75 let, zemřelí 30 let. Novější zápisy portál nezveřejňuje; chybějící
mladší kniha není chyba Rubriky. Rubrika z portálu čte matriky, úřední knihy a sčítání, nikoli
kroniky ani listiny.

## Zvláštnosti

- Portál je Drupal s hledáním v Solru; občas ukazuje v hlavičce PHP varování, výsledky jsou
  přesto správné. Změní-li se struktura stránky, Rubrika skončí chybou PARSE_ERROR (nevydává ji za
  prázdný výsledek); pak hledej v prohlížeči.
- Druhopisy matrik jsou v okresních archivech a na portálu nejsou.
- Matriky, které portál řadí k jiným archivům (např. „Plzeň (archiv města)“), mají jinou
  citaci; Rubrika ji sestaví podle pole „Archiv“ v detailu.
