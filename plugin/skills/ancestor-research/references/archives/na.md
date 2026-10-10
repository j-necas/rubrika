# Národní archiv, židovské matriky, Archivní VadeMeCum (kód `na`)

Přečti před prací s Národním archivem. Obecný postup je v SKILL.md.

Působnost: **židovské matriky** z Čech, Moravy a části Slezska. `list_archives(district=…)` na tento archiv
nikdy nesměruje (nejde o území, ale o vyznání). Zeptej se uživatele, jestli byl předek Žid, nebo jestli křesťanské
matriky obce o rodině nic nedaly, a teprve pak hledej s `archives=["na"]`. Bez výslovného
`archives` se Národní archiv v `find_place` neprohledává a v `not_searched` je s tímto vysvětlením. Archiv je ve
výchozím stavu serveru vypnutý (`RUBRIKA_NA_ENABLED`), plugin ho zapíná; vypnutý archiv je v `not_searched`
a uživatel hledá v prohlížeči (https://vademecum.nacr.cz/vademecum/).

## Co archiv nabízí

- Druh pramene je jen **parish_registers**: fond „Matriky židovských náboženských obcí v českých krajích“
  (HBMa, NAD 167), inventář č. 1646 „Židovské matriky 1784–1949 (1960)“, 2 955 svazků. Slovo „krajích“ v názvu
  znamená české země; obsah je z Čech, Moravy a části Slezska. Pokrytí Slezska a některých obcí ověř v inventáři.
- Inventář je jeden dlouhý seznam (asi 3 900 řádků) s obsahem podle míst („PLZEŇ (o. Plzeň)“, „BABČICE (o. Tábor)“,
  asi 560 záhlaví). Katalog nemá hledání podle obce ani roku; Rubrika hledá v záhlavích obsahu. Obsah se stáhne
  jednou (2 požadavky) a zůstává v paměti uživatele (výchozí týden, `RUBRIKA_METADATA_TTL`).
- Hledej názvem obce, případně názvem okresu: „Plzeň“ vrátí Plzeň a pak obce okresu Plzeň-jih a Plzeň-sever
  (vlastní název má přednost). Některé záhlaví jsou odkazy („DAVLE viz Roztoky“, bez knih) nebo mají pozměněný
  zápis (chybějící závorka); odkaz `list_sources` vrátí jako varování „Záhlaví bez knih“.
- **Praha** je rozdělena podle druhu matriky a čtvrti: „PRAHA - matriky narozených“, „… oddaných“, „… zemřelých“,
  „PRAHA - indexy …“ a pod každým čtvrti (Karlín, Smíchov, Vinohrady, Žižkov …). `label` má tvar
  „PRAHA - matriky oddaných › PRAHA-KARLÍN“. Stejná čtvrť se opakuje, u zemřelých i dvakrát; rozliš je podle
  čísel inventáře po `list_sources`. Hledání „Praha“ vrátí desítky míst (nejvýš 25), zadej čtvrť nebo druh matriky.
- Sekce „index“/„indexy“ pod obcí patří k obci: jejich knihy mají `coverage.<událost>.records` `absent` a
  `index` z popisu. Záhlaví druhé úrovně, které není index (např. „Citoliby“ pod „HŘÍŠKOV (o. Louny)“), je
  samostatné místo s cestou v `label`.

## Knihy a relevance

- `list_sources` vypíše všechny knihy jednoho místa (nejvýš 20 stránek inventáře po dvanácti řádcích, jedno volání
  je jedna relace: otevření, celý inventář asi 600 kB, skok na záhlaví, další stránky). Velké pražské bloky stojí
  10 až 15 stránek a několik MB; výpis se pak drží v paměti. Roky ani `fund_marks` neposílej (odmítnou se).
- Řádek knihy: `number` = číslo inventáře (`inv. č. 1541`), popis ve tvaru `N 1849-1871 O 1841-1856 Z 1850-1870`
  (N narození, O oddaní, Z zemřelí, `NOZ` všechny tři; `(i)` = k události je index; `(i v letech …)` index jen
  v uvedených letech). Událost, kterou popis nejmenuje, zůstává **nejistá**: kniha, která nese jen N, je při
  dotazu na úmrtí `needs_review`, nikdy `excluded`. `excluded` je jen kniha, jejíž vlastní událost dotazu
  má roky bez překryvu.
- Řádek bez digitalizace a odkaz („— ANNÍN - viz Kundratice“) se nevypíše jako kniha, ale skončí ve `warnings`
  výpisu; nikdy se neztratí mlčky.
- `scan_number` je pořadí snímku v knize, ne folio. Kniha nese počet snímků z inventáře.

ID: `na:place:<číslo>` (číslo řádku obsahu; při změně inventáře `list_sources` vrátí `SOURCE_CHANGED` a hledání
se opakuje), `na:book:<entityId>` (kniha), snímek `na:book:<entityId>:<n>`. `detail_url` knihy je trvalý odkaz
`…/permalink?xid=<uuid>`, `viewer_url` snímku `…/permalink?xid=<uuid>&scan=<hash>&parentType=9019` (ten se zjistí až
při otevření snímku).

Citace: „Národní archiv, Matriky židovských náboženských obcí v českých krajích (HBMa, NAD 167), inv. č. <číslo>“;
uveď ji spolu s `viewer_url` a číslem snímku. Patička archivu: „Publikování jakékoliv textové i obrazové části
databáze je podmíněno souhlasem Národního archivu a uvedením řádné citace.“ Rubrika nic nepublikuje; upozorni
uživatele, že zveřejnění (i sdílení nálezu) vyžaduje souhlas archivu.

## Snímky

- `open_source` snímky jen vypíše. Zdroj obrazu (`vademecum.nacr.cz/mrimage/…`, Zoomify) se zjistí při `view_scan`
  z prohlížeče knihy, který ukazuje deset snímků najednou: první skupina (snímky 1–10) stojí otevření relace a
  jeden požadavek, každá další jeden požadavek navíc. Zjištěných deset snímků si Rubrika pamatuje, takže
  sousední snímky (čtení knihy po řadě) už na katalog nic nestojí. Prohlížeč pořadí snímku v odkazu ignoruje,
  proto Rubrika listuje po skupinách.
- **Captcha**: celý snímek v plném rozlišení („uložit“) archiv vydává jen za captchou. Rubrika ji nikdy nevolá
  ani neobchází. Proto platí strop (`RUBRIKA_FULL_RES_PAGE_SHARE`): detail, který by z nejvyšší úrovně dlaždic
  vykreslil víc než polovinu plochy snímku, se odmítne (`BUDGET_EXCEEDED`, součet za celou dobu). Čti přehled a
  **menší výřezy**.
- Snímky jsou Zoomify s lichými rozměry; server je při půlení zaokrouhluje nahoru, Rubrika rozpozná obě
  zaokrouhlení podle první krajní dlaždice.

## robots.txt, ochrany a tempo

- `robots.txt` hostu zakazuje vše (`User-agent: * / Disallow: /`) a každá stránka nese `ROBOTS: NONE`. Rubrika
  čte jen na výslovný pokyn uživatele (požadavky vyvolané uživatelem); `coverage.robots_tolerated` a
  `robots_disallowed` u obrazů to dokládá, v reportu to uveď.
- Nejvýš jeden požadavek za 2 s. Při kódu 401, 403, 429 nebo stránce s ochranou proti botům Rubrika přestane a
  host na čas pozastaví. Nikdy nevolá „uložit“ (captcha), košík, hledání, rejstříky ani přihlášení.
- Podmínky užití samostatně nezveřejněny; jen patička výše a Badatelský řád Národního archivu.

# Policejní přihlášky Prahy 1850–1914, Národní archiv (kód `na_prihlasky`)

Jiný zdroj téhož archivu než židovské matriky výše: samostatná aplikace `http://digi.nacr.cz/prihlasky2/` (ne VadeMeCum), vlastní kód
`na_prihlasky`, druh `census`. Přečti před prací s ním.

## Co obsahují

- Pobytové přihlášky pražského policejního ředitelství, fond „Policejní ředitelství Praha I – konskripce“: asi 664 651 archů v 739 kartonech,
  převážně 1850–1914 (Národní archiv uvádí i 1918), území čtyř pražských měst a okolních obcí (Josefov, Karlín, Libeň, Smíchov, Nusle,
  Vršovice, Břevnov, Košíře, Bubeneč, Holešovice a další), **bez Vinohrad**. Části kartonů (Bí–Bo) měly na začátku projektu indexované jen hlavy
  domácností; stav doplnění archiv neuvádí.
- Jeden arch = **celá domácnost** (hlava, manželka, děti, podnájemníci a služebnictvo, jak je hlášeno). Každá osoba archu je v indexu zvlášť,
  **vdaná žena pod příjmením manžela**; dívčí příjmení je jen v závorce na přihlášce (`Dvořák (Špilar) Marie (1863)`: příjmení, dívčí příjmení, jméno,
  rok narození; Rubrika ho čte do `maiden_name`) a **podle něj se hledat nedá**. Vdanou ženu hledej pod příjmením manžela. Dítě najdeš i hledáním jeho
  jména a arch ti ukáže rodiče; ve výpisu je to `household` (`head` = první uvedená osoba, `matched` = osoba, ke které vedl řádek hledání) a `index_hits`.
  Sloupec vztahu k hlavě domácnosti („Charakter“) Rubrika nečte; vztah zjistíš na snímku.
- **Roky:** hledání s `year_from`/`year_to` (pásmo ±5) bere jen osoby s rokem narození v indexu; `listing_complete=true` platí jen pro toto pásmo (stejně jako u
  Soupisu). Nenajde-li se nic, zkus hledání bez let.
- Index je **transliterovaný**: `Novak` a `Novák` jsou samostatné záznamy, stejně `Čech`/`Cech`. Hledá se jen od začátku příjmení a jména. Rubrika zkusí
  další pravopis (bez diakritiky) a další tvar jména (Josef/Joseph), ale jen když předchozí hledání nic nenašlo; ostatní varianty zkus přes
  `revise_research` sám. Jedna osoba může mít víc archů (stěhování mezi čtvrtěmi a roky).
- Seznam výsledků ukazuje jen příjmení, jméno a rok narození (žádné místo ani adresu); adresa, místo narození, povolání a další údaje jsou jen **na snímku**.
  Sken je **jeden celý JPEG 1000×1333 px** (asi 80–90 kB, bez dlaždic): rukopis je čitelný omezeně, výřezy se zvětšují lokálně a nepřidají detail.
  Další rozlišení archiv nenabízí.

## Postup

1. V `revise_research` ať je `person.surname`, podle možnosti `person.given_name`, `event_type=birth` s `year_from`/`year_to` (rok narození osoby) a
   případně `person.father`, `person.mother`.
2. `find_place(kind="census", archives=["na_prihlasky"])` → místo `na_prihlasky:place:praha` (bez požadavku na archiv).
3. `list_sources(place_ids=["na_prihlasky:place:praha"], kind="census")`: hledá se v pásmu roků narození ±5 (candidate ±2), roky a jména se nefiltrují
   parametry. Jedno volání stojí až 30 požadavků (2 s od sebe, tedy asi minutu); při seříznutí dostaneš `listing_complete=false` a varování.
   Kombinuj s **Soupisem pražského obyvatelstva** (AHMP, `archives=["ahmp"]`): přihláška 1850–1914 dá domácnost, Soupis domovskou příslušnost a adresu.
4. `open_source(source_id="na_prihlasky:form:<karton>:<obraz>")` → citace, `household`, jeden snímek; `view_scan` a `zoom_scan` jako u ostatních archivů.
   U dokladu uveď citaci („Národní archiv, Policejní ředitelství I, konskripce, karton 426, obraz 707“) a trvalý odkaz `viewer_url`.

ID: `na_prihlasky:place:praha`, `na_prihlasky:form:<karton>:<obraz>`, snímek `na_prihlasky:form:<karton>:<obraz>:1`. Trvalý odkaz
`http://digi.nacr.cz/prihlasky2/index.php?action=link&ref=czarch:CZ-100000010:874&karton=<k>&folium=<o>` nepotřebuje relaci a je stabilní; odkazy s
`?session=` stabilní nejsou a Rubrika je neukládá.

## HTTP, robots.txt a tempo

- **Server nabízí jen nešifrované HTTP.** Rubrika pro tento jediný host povoluje `http://` (ostatní hosty zůstávají HTTPS). Hledaná příjmení a jména proto
  putují sítí čitelně a stránku by mohl cestou někdo změnit; obsah se bere jako nedůvěryhodná data. Řekni to uživateli, než hledáš.
- `robots.txt` hostu zakazuje vše (`Disallow: /`). Rubrika ho u požadavků vyvolaných uživatelem toleruje (`coverage.robots_tolerated`, `robots_disallowed` u
  obrazů; v reportu to uveď), čte nejvýš jednou za 2 s a žádnou ochranu neobchází (přihlášení, captcha a ochrana proti botům neexistují; 401/403/429 host zastaví).
- Archiv nezveřejňuje podmínky užití. Opravy chybně přečtených údajů archiv přijímá e-mailem s odkazem na přihlášku (adresa je na stránce aplikace). Sken nezveřejňuj.
- Archiv je ve výchozím stavu serveru vypnutý (`RUBRIKA_PRIHLASKY_ENABLED`); vypnutý je v `not_searched` a uživatel hledá v prohlížeči.

