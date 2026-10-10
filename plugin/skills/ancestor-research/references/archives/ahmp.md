# Archiv hlavního města Prahy, Archivní katalog (kód `ahmp`)

Přečti před prací s AHMP. Obecný postup je v SKILL.md.

Působnost: Praha (dnešní hlavní město). `list_archives(district="Praha")` vrátí AHMP jako primární
archiv; část středočeských okresů u Prahy má matriky ve SOA Praha (odkaz, Rubrika z něj nečte).
Server má AHMP ve výchozím stavu vypnutý (`RUBRIKA_AHMP_ENABLED`), plugin ho zapíná; vypnutý
archiv je v `not_searched` jen jako odkaz a uživatel hledá v prohlížeči
(https://katalog.ahmp.cz/pragapublica/).

## Co archiv nabízí

- Druh pramene je jen **parish_registers**: „Sbírka matrik“ (fond 156, NAD 156), přibližně 1584–1944.
  Rubrika čte čtyři větve: **katolické** (vnitřní obvody Praha I–VI, vnější obvody), **nekatolické**
  (Českobratrská církev evangelická, Církev československá (husitská), pravoslavná, starokatolická, řeckokatolická u sv. Klimenta,
  Jednota českobratrská a unitáři bez knih), **civilní** (Magistrát hl. m. Prahy, magistrátní úřadovny, okresní správy politické,
  Okresní úřad Praha - venkov, německý Standesamt Praha) a **Úmrtní protokoly** (1924–1939). Židovské matriky v této sbírce nejsou.
  Další sbírky AHMP Rubrika nečte, pro ně odkaž do prohlížeče.
- Uzly, které mají vlastní knihy i podřízené uzly (např. Bohnice), se nabízejí jako farnost i jako kontejner.
- Strom katalogu: katolické → obvod → (městská část) → farnost (kostel) → kniha. Katalog nemá
  vyhledávání podle obce ani roku, farnost se hledá ve stromu podle názvu kostela nebo čtvrti
  („sv. Jindřich“, „Nové Město“, „Vinohrady“). Stromu se stahuje jen tolik, kolik hledání vyžaduje,
  a co se stáhlo, zůstává v paměti uživatele (výchozí týden, `RUBRIKA_METADATA_TTL`). **První hledání může trvat i minutu**
  (každý požadavek nejdřív po 2 s). Vrátí-li `find_place` varování, že se strom načítá postupně,
  zopakuj hledání; načtené větve se neopakují.
- Nekatolické a civilní matriky jsou seskupené podle církve nebo úřadu, ne podle obvodu: `label` začíná „Nekatolické matriky ›“
  (např. „Nekatolické matriky › Českobratrská církev evangelická (ČCE) › ČCE (Staré Město) u sv. Salvátora (EVS)“),
  „Civilní matriky Magistrátu ›“ (např. „… › Magistrátní úřadovny › Dejvice (MGDEJ)“) nebo je to „Úmrtní protokoly“. Hledej podle
  církve (evangelická, československá, pravoslavná, starokatolická), úřadovny, čtvrti nebo kostela; hledání „sv. Klimenta“ vrátí
  katolickou farnost první a pak nekatolické u téhož kostela. Nejprve se dočítají tyto malé větve (řádově desítky požadavků jednou, uložené na `metadata_ttl`),
  potom zbytek katolického stromu. Dřív uložený katolický index není „hotový“, dokud jedno výslovné hledání v AHMP
  (`archives=["ahmp"]`) nedočte i tyto větve; do té doby AHMP bez výslovné žádosti neprohledává; varování o neúplném stromu vyjmenuje větve, které se ještě nečetly. Uzel s vlastními knihami
  i podřízenou farností (Pravoslavná církev v Praze) se nabízí jako dvě místa. Větev bez jediné knihy (Jednota českobratrská, unitáři)
  místem není; nedigitalizované knihy (část německého Standesamtu) jsou ve varování výpisu.
- Bez `archives=["ahmp"]` nebo okresu Praha `find_place` strom nezačne stahovat; AHMP prohledá jen
  tehdy, když už je strom načtený.
- Výsledky jsou farnosti, `label` nese cestu („Praha II - Nové Město › Kostel sv. Jindřicha (JCH)“).
  Hledání „Nové Město“ vrátí všechny farnosti čtvrti (nejvýš 25); upřesni název.

## Knihy a signatury

- `list_sources` vypíše všechny knihy jedné farnosti (po jedné; katalog vrací 9 řádků na stránku, velká
  farnost proto stojí i desítky požadavků; výpis se pak drží v paměti). Roky ani `fund_marks` se
  neposílají (odmítnou se), relevance se počítá z roků a typu události zadání hledání.
- Signatura má tvar `<zkratka farnosti> <druh><číslo>`, např. `JCH N4`: **N** narození, **O** oddaní,
  **Z** zemřelí (i spojené `NOZ` a `N1O1Z1`, které nekatolické a civilní řady používají pro společné knihy), **i** rejstřík (index).
  `UP-IZ 1a` jsou úmrtní protokoly (jen zemřelí, ostatní události `needs_review`). Roky jsou v názvu knihy („JCH N4 • 1638-1656“);
  kniha bez roků nebo se signaturou, které Rubrika nerozumí, je `needs_review`, nikdy `excluded`.
  Rejstříky (`i`) jsou vždy `needs_review`: nevíme, které události rejstřík pokrývá.
- Jiné druhy událostí v knize (např. oddaní v knize `N`) signatura neuvádí, ale Rubrika je z toho
  nevyvozuje jako nepřítomné: kniha jiného druhu je `needs_review`. `excluded` je jen kniha, jejíž vlastní
  druh události má v názvu roky bez překryvu se zadáním.
- První snímky knihy bývají hřbet nebo obálka, čtení proto začni o několik snímků později.
- `listing_complete: true` s `next_cursor` znamená, že celý výpis je znám a jen se stránkuje; pokračuj `cursor`em.
- `scan_number` je pořadí snímku v knize, ne folio. Kniha nese počet snímků z katalogu.

ID: `ahmp:node:<číslo>` (farnost), `ahmp:book:<32 hex>` (kniha, trvalý identifikátor `xid` z katalogu),
snímek `ahmp:book:<xid>:<n>`. `viewer_url` je trvalý odkaz `…/permalink?xid=<xid>&scan=<n>`.
Citace: „Archiv hlavního města Prahy, Sbírka matrik, sign. <signatura>“; uveď ji spolu s `viewer_url`.

## Soupis pražského obyvatelstva 1830–1910 a sčítání (`kind="census"`)

- **Co to je**: konskripční listy pražských obyvatel (jméno, rok narození, pořadové číslo, karton; adresa je jen na
  snímku). Dvě řady: **Pražští příslušníci** (domovská příslušnost Praha, 121 763 listů) a **Cizí příslušníci**
  (domovská příslušnost jinde, 146 554 listů, digitalizováno jen zčásti). Nevíš-li, prohledej obě.
- **Použití**: `find_place(kind="census", archives=["ahmp"])` (nebo okres Praha) vrátí obě řady jako místa
  `ahmp:node:168668` / `ahmp:node:290432`. `list_sources` na jedné z nich potřebuje v zadání `person.surname`
  (jinak `INVALID_ARGUMENT`: doplň přes `revise_research`), lépe i `person.given_name` a rok narození.
  Jednotka `ahmp:book:<xid>` je jeden list; `open_source` cituje „… Soupis pražského obyvatelstva 1830–1910, <řada>,
  karton n, příjmení, jméno (*rok)“ (karton se čte z detailu listu jedním požadavkem).
- **Jak se hledá**: seznam je plochý a řazený foneticky podle příjmení, ne po písmenech. Rubrika v jedné relaci
  půlí seznam skokem na řádek (čistý GET) a pak sbírá řádky příjmení. Cena: nalezení jména zhruba 15–25 požadavků
  (asi 40 s při tempu 2 s); celé volání má jediný strop 35 požadavků (otevření relace, stránka seznamu, skoky a
  sběr; obnova po vypršení relace se počítá do téhož stropu). Přehled jednoho snímku je asi 25 dlaždic (asi 50 s)
  a každý výřez další dlaždice, proto výřezy volte na oblast jména, ne naslepo na střed listu. Nalezená hranice se pamatuje.
- **Pravopisné běhy**: každý pravopis příjmení (jak je zadán, a bez diakritiky, např. Dvořák a Dvorak; Šimek a Simek jsou
  v seznamu dvě různá místa) a každý tvar křestního jména je samostatný běh půlení (nejdřív zadaný tvar). Tvary jmen
  určuje malá tabulka českých a německých ekvivalentů: Josef/Joseph, František/Franz,
  Jan/Johann, Václav/Wenzel/Wenzl, Karel/Karl, Antonín/Anton, Jiří/Georg, Tomáš/Thomas, Matěj/Mathias, Vojtěch/Adalbert,
  Jakub/Jakob, Petr/Peter, Pavel/Paul, Ondřej/Andreas, Michal/Michael, Štěpán/Stefan, Marie/Maria, Kateřina/Katharina,
  Barbora/Barbara, Terezie/Theresia, Alžběta/Elisabeth, Magdaléna/Magdalena, Josefa/Josepha, Františka/Franziska,
  Johana/Johanna, Karolína/Karoline (Anna a Ludmila jsou bez páru). Všechny běhy sdílejí strop volání; nestihne-li se
  některý, varování jmenuje neprohledané tvary a `listing_complete=false`.
- **Pravidla řazení** (odvozená z řádků katalogu): W = V, Š je za S, Č za C, Ř za R, Ž za Z, Ch za H, y = i;
  německé a polské zápisy českých hlásek (sch, tsch, cz, cž) se řadí jako š, č; ženské tvary (-ová, -ová s pomlčkou,
  -ská) leží u mužského příjmení (Maděrová u Maděra). Novák, Nowak a Nowák jsou tedy vedle sebe; jméno se v rámci
  příjmení řadí podle prvního křestního jména, pak podle roku narození sestupně. Josef a Joseph tvoří dvě řady
  (Rubrika je pro relevanci považuje za totéž), Jan a Johann nikdy.
- **Zúžení křestním jménem a rokem**: je-li v zadání `person.given_name`, vypisují se jen listy tohoto jména a jeho tvarů
  z tabulky. Seznam je řazen příjmení → první křestní jméno → rok narození sestupně, proto při
  `event_type=birth` s `year_from`/`year_to` (a křestním jménem) Rubrika půlí i podle roku a sebere pásmo narození
  **±5 let** kolem zadání; shoda ±2 je `candidate`, okraj pásma je `needs_review` s důvodem (rok narození proti zadání).
  Listy mimo pásmo se nehledaly a varování to říká: `listing_complete=true` platí jen pro sebrané pásmo. Strop 90 listů se
  pak vztahuje na pásmo. Bez roku narození (nebo bez křestního jména, nebo u jiné události) se vypíše prvních 90 listů
  jména a varování žádá rok narození přes `revise_research`. Řádky jsou seřazeny podle vzdálenosti roku od středu zadání.
  Prázdný výsledek je varování, ne chyba: říká, co se hledalo („žádný list pro <příjmení> <jména> v letech A–B“), a dvě
  nejbližší příjmení v seznamu uvádí jen tehdy, když příjmení samo v seznamu není.
- **Známá rizika řazení** (odvozeno z pár desítek řádků, nepotvrzeno): umístění „ch“ za „h“, německých ä/ö/ü (bereme jako a/o/u),
  „ß“ (jako ss), zdvojených písmen (řadí se po písmenech, při porovnání jmen se sloučí), jinak psaných křestních jmen mimo
  Josef/Joseph a diakritiky u příjmení (hledá se i bez ní). Když příjmení leží v sousední skupině, pomůže zkusit variantu.
- **Sčítání 1869, 1880, 1890** (fond 61 Všeobecné sčítání lidu, uzel 514342): `find_place(kind="census",
  archives=["ahmp"], term="Nové Město" nebo "1880")` vrátí obvody příslušných let jako místa; `list_sources`
  potřebuje `person.house_number` a najde řádek domu (číslo popisné) půlením seznamu. Jednotka nese
  `house_numbers` a `relevance.house_number_covered`; snímky jsou sčítací listy domácností (desítky na dům).
  Roky 1900, 1910 a 1921 se nevypisují (nejsou online). 1869 (obvody jsou obce, např. Dejvice), 1880 a 1890 (I–VII, Stará Libeň…) mají stejnou stavbu obvod → řádek domu `čp. n`.

## Snímky

- `open_source` snímky jen vypíše. Zdroj obrazu (`images.ahmp.cz`, Zoomify) se zjistí až při
  `view_scan`, jedním požadavkem na trvalý odkaz snímku; poté se čtou dlaždice. Proto první otevření
  snímku trvá déle.
- **Vodoznak**: AHMP vkládá do každé reprodukce průsvitku „Archiv hlavního města Prahy www.ahmp.cz“.
  Rubrika obraz nijak neupravuje; vodoznak může zhoršit čitelnost, zmiň to v poznámce.
- **Captcha**: celý snímek ve vysokém rozlišení („uložit“) archiv vydává jen za captchou. Rubrika ji
  nikdy nevolá ani neobchází. Proto platí strop: detail, který by z nejvyšší úrovně dlaždic vykreslil
  víc než polovinu plochy snímku (`RUBRIKA_FULL_RES_PAGE_SHARE`), se odmítne (`BUDGET_EXCEEDED`). Součet se počítá po celou dobu, ne po dnech.
  Čti přehled a **menší výřezy** (řádky, zápisy), celý snímek otevři v prohlížeči.
- Snímky mají často netypický poměr stran (úzké pruhy desek, široké dvojstrany); souřadnice výřezu
  vždy odvozuj z rozměrů vráceného obrazu.

## robots.txt, ochrany a tempo

- `robots.txt` obou hostů (`katalog.ahmp.cz`, `images.ahmp.cz`) zakazuje vše. Rubrika čte jen na
  výslovný pokyn uživatele (tzv. požadavky vyvolané uživatelem), zápis do `coverage.robots_tolerated`
  a `robots_disallowed` u obrazů to dokládá; v reportu to uveď.
- Nejvýš jeden požadavek za 2 s na host. Při kódu 401, 403, 429 nebo stránce s ochranou proti botům
  Rubrika přestane a host na čas pozastaví; ochranu neobchází. Nikdy nevolá přihlášení, košík,
  formulář hledání ani cestu s captchou.
- Podmínky užití katalog nezveřejňuje („Text se připravuje“); nápověda uvádí, že reprodukce se poskytují
  na písemnou objednávku (posta@praha.eu). Publikace a vodoznak řeší Badatelský řád AHMP.
