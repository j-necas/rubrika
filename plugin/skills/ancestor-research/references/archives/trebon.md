# Státní oblastní archiv v Třeboni, DigiArchiv (kód `trebon`)

Přečti před prací s DigiArchivem. Obecný postup je v SKILL.md.

Působnost: Jihočeský kraj (okresy České Budějovice, Český Krumlov, Jindřichův Hradec, Písek, Prachatice, Strakonice, Tábor) a na
hranici Pelhřimov a Jihlava; list_archives(district=…) vrátí primární i případný druhý archiv. Okres je vždy dnešní. Matriky jsou ve
fondu „Sbírka matrik Jihočeského kraje“ (česká stránka archivu: „Fond Sbírka matrik Jihočeského kraje je uložen v centrále Státního oblastního archivu v Třeboni“, 6 900 jednotek, 1587–1949; farnosti v hranicích kraje před 1. 1. 2000, nejstarší Vyšší Brod 1587,
Český Krumlov 1591). DigiArchiv (https://digi.ceskearchivy.cz/) provozuje SOA Třeboň a zpřístupňuje i další prameny (pozemkové knihy, sčítání,
kroniky). Rubrika čte matriky (`parish_registers`: římskokatolické, evangelické, československé husitské, pravoslavné, německé evangelické,
civilní a německé civilní a vojenské matriky), pozemkové a vrchnostenské knihy s urbáři (`land_records`) a sčítání lidu (`census`).
Pozemkové knihy a sčítání mají vlastní přepínače (`RUBRIKA_TREBON_LAND_RECORDS_ENABLED`, `RUBRIKA_TREBON_CENSUS_ENABLED`, výchozí false). Matriky občanů Třetí říše, kroniky, listiny a mapy Rubrika nečte.

## Omezený režim a podmínky použití: řekni je uživateli

Úvodní stránka DigiArchivu (anglicky: „Images are published only for viewing by DigiArchiv of SGA Trebon; copying, publishing, selling or using in other projects or webpages is prohibited“) říká: snímky jsou zveřejněny jen k prohlížení v DigiArchivu; kopírování, zveřejňování, prodej ani použití v jiných
projektech či na jiných webech je zakázáno a další použití vyžaduje souhlas vlastníků archiválií. Rubrika proto pracuje v **omezeném režimu**:

- **Automaticky** čte jen katalog (farnosti, knihy, počet snímků) a dává odkazy na snímky (`viewer_url`). Ze serveru DigiArchivu **nestahuje žádný
  snímek** ani jeho dlaždice, popis nebo náhled. `view_scan` a `zoom_scan` pro `trebon` nefungují: vrátí chybu `MANUAL_SOURCE` s `viewer_url` a
  navrženou citací snímku.
- **Snímek přinese uživatel**: otevře `viewer_url` ve svém prohlížeči, uloží snímek (nebo snímek obrazovky) do Stažených souborů a načte ho
  nástrojem `import_scan` s citací. Za jakékoli užití snímku podle podmínek archivu (uložení, sdílení, zveřejnění; souhlas lze žádat na
  digi@ceskearchivy.cz) odpovídá uživatel.
- **Jednou za hledání**, při prvním použití `trebon`, řekni uživateli tento postup vlastními slovy; neopakuj to u každého snímku.
- U každého nálezu uveď `citation` a `viewer_url`. Výřezy z importovaných snímků se předávají také AI službě uživatele (viz README); je to jeho
  rozhodnutí, ne tvoje.
- Vyhledávání DigiArchivu (`/pages/search.php`), nápovědu a OAI má archiv v robots.txt zakázané; Rubrika je nepoužívá. Čte jen strom farností a
  stránku prohlížeče, které zakázané nejsou.

## Tempo

Rubrika drží od požadavku k požadavku aspoň 2 s. `find_place` pro matriky stojí dva požadavky na římskokatolické farnosti (seznam písmen a stránka
písmene) a osm dalších na kořeny nekatolických větví a civilních matrik (robots.txt navíc jednou; stránky se ukládají do mezipaměti),
pro pozemkové knihy a sčítání procházení stromu nejvýš 24 stránek na jedno volání (viz níže),
`list_sources` jeden (stránka farnosti, u velké asi 400 kB), `open_source` jeden na knihu (stránka prvního snímku kvůli počtu snímků; stránky
dalších snímků se nečtou, `viewer_url` se sestaví z čísla knihy a snímku). `find_place` bez okresu DigiArchiv neprohledává; zadej okres z jeho
území nebo `archives=["trebon"]`. Denní strop je `RUBRIKA_TREBON_GLOBAL_DAILY_REQUESTS` (výchozí 800 požadavků).

## Hledání místa: farnosti a sbory

DigiArchiv nemá pro Rubriku použitelné hledání obcí. `find_place` proto najde **farnost podle začátku jejího názvu**: podle prvního písmene názvu
vybere stránku písmene ve stromu farností (C/Č, CH, R/Ř, S/Š, Z/Ž mají každé jednu stránku) a porovná slova výrazu se začátky slov názvů
farností. Vrací farnosti (`trebon:place:<id farnosti>`, label „České Budějovice (sv. Mikuláš), římskokatolická farnost“; patron v závorce je česky, DigiArchiv volí jazyk podle hlavičky Accept-Language, kterou Rubrika posílá jako prohlížeč: cs-CZ). **Obec, která není sídlem farnosti, tak nenajdeš; zatím ji hledej podle názvu farnosti.** Zjisti farnost (podle farního obvodu, okolí, uživatele) a hledej
ji; potom v `list_sources` zjistíš, které knihy jsou vedeny pro obec ze zadání. Výraz „Budějovice“ nenajde „České Budějovice“ (zadej i první slovo).
Víc farností jednoho města (České Budějovice: sv. Mikuláš, sv. Jan Nepomucký) se dělí podle čtvrtí a ulic: prohlédni všechny. Písmeno, které strom nemá (např. E, G, U), dá varování bez míst; prázdný výsledek není důkaz, že farnost nebo obec v archivu není.

**Nekatolické matriky a civilní matriky** (`1nC` Českobratrská církev evangelická, `1nc` Církev československá husitská, `1ne` Evangelická
církev, `1ng` Německá evangelická církev, `1np` Pravoslavná církev, `1nb` civilní matriky, `1nh` německé civilní matriky, `1ni` německé vojenské
matriky) mají v DigiArchivu jeden seznam sborů nebo úřadů bez písmen. `find_place(kind="parish_registers")` je prohledá vedle římskokatolických
farností; místo nese v labelu církev nebo druh matrik („Písek, Českobratrská církev evangelická“, „Třeboň, civilní matriky“) a kniha má
`denomination`. Větev, kterou se nepodařilo přečíst, jmenuje varování (v ostatních se hledá dál). Židovské matriky DigiArchiv nevede (Národní
archiv, `archives=["na"]`). Pro sňatek nebo narození bez vyznání (od roku 1870, později civilní matriky) hledej ve větvi civilních matrik; matriky
jiné církve než římskokatolické najdeš jen tady, pokud jde o jihočeské sbory.

## Pozemkové knihy, vrchnostenské knihy, urbáře (`land_records`)

Tři větve stromu: Pozemkové knihy (`1q`), Vrchnostenské knihy (`1t`), Urbáře (`1U`). Členění: archiv (SOA v Třeboni – oddělení Třeboň a Český
Krumlov, SOkA České Budějovice, Český Krumlov, Jindřichův Hradec, Písek, Prachatice, Strakonice, Tábor) → písmeno → **uzel s id** (město, panství,
statek), jehož stránka má popis archivního souboru („Velkostatek Blatná, (1327) 1503–1948“), seznam lokalit fondu a tabulku knih (inventární číslo,
signatura, název, datace, ukládací jednotka). `find_place(kind="land_records", archives=["trebon"])` prochází strom nejlepší větví napřed (písmeno
podle prvního písmene názvu, archiv podle `district`, je-li zadán), nejvýš 24 nových stránek na volání (načtené jsou v mezipaměti a nepočítají se, takže opakované hledání pokračuje tam, kde předchozí skončilo); co se nevešlo, jmenuje varování včetně nezačatých větví vrchnostenských knih (1t, soupisy poddaných) a urbářů (1U). ID místa nese větev: `trebon:place:l<id>` (pozemkové, vrchnostenské, urbáře), `trebon:place:c<id>` (sčítání). Obec, která je jen
mezi lokalitami fondu (ves panství), se nenajde: hledej název panství nebo města, k němuž patřila (lokality fondu vrací `list_sources` v `places`).
Kniha má `category` `land_book` jen z větve Pozemkové knihy, vrchnostenské knihy a urbáře jsou `other`. Datace „(1580) 1598–1919“ se čte jako
1580–1919, „asi 1700“ jako nejistá (`needs_review`, nikdy `excluded`). Knihy bez odkazu na snímky se vynechají a spočítají ve varování.

## Sčítání lidu (`census`)

Větev `1s`: archiv (SOkA) → rok (1857, 1869, 1880, 1890, 1900, 1910, 1921) → soudní okres → písmeno → obec s tabulkou knih. Místo je obec v jednom roce
(label „Zliv (sčítání lidu; SOkA České Budějovice › 1869 › Soudní okres Hluboká nad Vltavou)“); jiný rok téže obce je jiné místo. Strom se prochází
stejně jako u pozemkových knih; `district` zúží archiv a soudní okres se hledá i podle názvu obce, takže okresní sídlo se najde levněji než ves.
Tabulka sčítací obce má hlavičku bez id sloupců; Rubrika čte sloupce podle textu (Inventární číslo, Signatura, Sčítací obec, Obsah, Ukládací jednotka). Rok bere z řádku, jinak ze stránky obce („Rok 1880“), při rozporu s uzlem stromu je datace nejistá. Čísla popisná Rubrika čte jen z Obsahu a Sčítací obce („čp. 1–250“); pokud je nepozná, je `house_number_covered` null (nikdy false) a sada se neřadí dozadu.
`person.house_number` ze zadání řadí pokryté sady napřed. Rok sčítání mimo `year_from`–`year_to` zadání sadu vyloučí (`excluded`), ale jen při známém roce.

## Výpis knih

`list_sources` bere jednu farnost, načte její přehled a vrátí všechny **zveřejněné** knihy (řádky bez odkazu „bude online až od roku …“ vynechá
a spočítá je ve varování). Řádek: `number` (číslo knihy ve farnosti, např. „1“, „53C“), `register_label` („register B“, „index B“), roky podle
typů (B narození, M oddaní, D zemřelí), `localities` (obce, pro něž je kniha vedena, „Book for“), `other_localities`, `note`. Odpověď nese také
`parish` (název, okres, farní obvod z roku 1862 a počet dalších lokalit). Kniha typu „register“ má záznamy jen pro vypsané typy, „index“ má roky
jen jako index. Nečitelný štítek nebo tabulka dá `uncertain`, nikdy `excluded`. Je-li v zadání `place_hint` a kniha ho mezi `localities` nemá,
je nanejvýš `needs_review`; nemá-li ho žádná kniha, varování řekne, zda je obec ve farním obvodu nebo mezi dalšími lokalitami. `year_from`,
`year_to`, `fund_marks`, `district_archive` a víc než jedno místo v jednom výpisu se odmítají.

## Otevření a snímky

ID: kniha (pozemková, sčítací i matriční) `trebon:book:<id knihy v DigiArchivu>`, snímek `trebon:book:<id>:<n>`. `open_source` načte stránku prvního snímku, z ní počet snímků
(`total`) a `citation` („Státní oblastní archiv v Třeboni, Sbírka matrik Jihočeského kraje, <farnost>, kniha č. <n> [register B] (DigiArchiv, id <id>)“).
U pozemkových knih a sčítání je `citation` „Státní oblastní archiv v Třeboni, <fond>, <název knihy>, č. <n> (DigiArchiv, id <id>)“ a `open_source` vrací
`kind` `land_records` nebo `census`. Snímky mají `source_type="manual"`, `viewer_link_verified=false` a `viewer_url` `https://digi.ceskearchivy.cz/view.php?menu=3&id=<kniha>&page=<n>`
(sestavené bez požadavku). `scan_number` je pořadí snímku, ne folio. Snímek dál viz omezený režim výše (prohlížeč uživatele a `import_scan`).

## Ochranné lhůty a zvláštnosti

Mladší knihy archiv zveřejňuje až po uplynutí ochranných lhůt („will be available online after 2033“); chybějící mladší kniha není chyba Rubriky.
Změní-li se struktura stránek, Rubrika skončí chybou PARSE_ERROR (nevydává ji za prázdný výsledek); pak hledej v prohlížeči.
