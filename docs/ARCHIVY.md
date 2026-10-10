# Archivy a jejich podmínky

Podrobný přehled pramenů, ve kterých Rubrika hledá, a podmínek, za kterých archivy
snímky zpřístupňují. Stručná verze je v [README](../README.md#kde-rubrika-hledá).

## Co Rubrika prohledává

Prohledává digitalizované prameny sedmi archivů: [Moravského zemského archivu v Brně](https://www.mza.cz) (MZA),
státních oblastních archivů v [Hradci Králové](https://aron.vychodoceskearchivy.cz/) (ARchiv ONline),
v [Plzni](https://www.portafontium.eu/) (Porta fontium), v [Praze](https://ebadatelna.soapraha.cz/) (eBadatelna)
a v [Třeboni](https://digi.ceskearchivy.cz/) (DigiArchiv, jen katalog a odkazy),
[Archivu hlavního města Prahy](https://katalog.ahmp.cz/pragapublica/) a [Národního archivu](https://vademecum.nacr.cz/vademecum/)
(židovské matriky a pražské policejní přihlášky). Kód v závorce je označení archivu v Rubrice.

| Archiv | Území | Pramen | K čemu je dobrý |
|---|---|---|---|
| **MZA** | Morava a část Vysočiny | **Matriky** (Acta Publica) | narození, sňatky a úmrtí, včetně rejstříků |
| | | **Pozemkové knihy a katastry** (ARON) | kdo vlastnil usedlost, převody, dědictví; posune pátrání před začátek matrik |
| | | **Sčítací operáty** 1857–1910 | složení domácnosti, věk, původ a zaměstnání členů rodiny |
| | | **Stabilní katastr** (1824–1843): parcelní protokoly (fond D 10) a **indikační skici** (D 9) | kdo držel parcelu a dům kolem roku 1830; skica je mapa katastru s čísly domů a jmény držitelů |
| **SOA Hradec Králové** (`vca`) | Královéhradecký a Pardubický kraj, okresy Havlíčkův Brod a Semily | **Matriky** | narození, sňatky a úmrtí ze „Sbírky matrik Východočeského kraje“ |
| | | **Městské pozemkové knihy** („evidence nemovitostí“) a další městské knihy | trhové, gruntovní a purkrechtní knihy z městských archivů, kšafty a kroniky; digitalizované katastry archiv nemá |
| | | **Sčítací operáty** | jednotky s rozsahem čísel popisných, ze kterých Rubrika poznává, zda pokrývají hledaný dům |
| **SOA Plzeň, Porta fontium** (`pf`) | Plzeňský a Karlovarský kraj (i Cheb, Karlovy Vary, Sokolov) | **Matriky** | narození, sňatky a úmrtí ze „Sbírky matrik západních Čech“; hledá se podle názvu místa (i německého), snímky se čtou pomalu (10 s na požadavek) |
| | | **Úřední a pozemkové knihy** | pozemkové knihy, radní protokoly a další úřední knihy měst a panství |
| | | **Sčítání lidu** | operáty po obcích a domech (1869–1921, podle dochování) |
| **SOA Praha** (`soapraha`, eBadatelna) | Středočeský kraj | **Matriky** | narození, sňatky a úmrtí ze Sbírky matrik Středočeského kraje (asi 11 800 knih); hledá se podle obce nebo osady |
| | | **Sčítání lidu** 1869–1910 (vybrané okresy) | operáty po obcích a rozsazích čísel popisných |
| | | **Pozemkové knihy** měst a panství | pozemkové, gruntovní a purkrechtní knihy, urbáře |
| **Archiv hlavního města Prahy** (`ahmp`) | Praha | **Matriky** | katolické, nekatolické (evangelické, husitské, pravoslavné…) a civilní matriky ze Sbírky matrik (1584–1949); hledá se podle farnosti, sboru nebo části Prahy |
| | | **Soupis pražského obyvatelstva** 1830–1910 | list rodiny s daty narození, bydlištěm a domovskou příslušností; hledá se podle příjmení, křestního jména a roku narození |
| | | **Sčítání lidu** 1869, 1880, 1890 | operáty po domech; hledá se podle čtvrti a čísla popisného |
| **SOA Třeboň** (`trebon`, DigiArchiv) | Jihočeský kraj | **Matriky** (jen katalog a odkazy) | farnosti a knihy ze Sbírky matrik Jihočeského kraje (i nekatolické a civilní) s odkazem na každý snímek; snímky si uložíte z prohlížeče a Rubrika je načte (viz níže) |
| | | **Pozemkové knihy a sčítání** (jen katalog a odkazy) | pozemkové knihy panství, soupisy poddaných, urbáře a sčítací operáty; snímky stejně jako u matrik |
| **Národní archiv** (`na`) | Čechy, Morava a část Slezska | **Židovské matriky** | narození, sňatky a úmrtí židovských náboženských obcí 1784–1949 (fond HBMa); hledá se jen na výslovné přání, když byl předek Žid |
| **Národní archiv** (`na_prihlasky`) | Praha | **Policejní přihlášky** 1850–1914 | celá domácnost s daty narození a domovskou obcí; hledá se podle příjmení a jména, vdané ženy pod příjmením manžela; web má jen nešifrované HTTP |

Sčítání z roku 1921 zpřístupňuje MZA jen ve svých badatelnách, Rubrika ho proto neotevírá.
Sčítání z SOA Hradec Králové Rubrika otevírá po jednotkách s rozsahem čísel popisných.

Který archiv obec spravuje, Rubrika pozná podle dnešního okresu: matriky se dělily podle krajů
z let 1960–1990, takže třeba matriky z Havlíčkobrodska leží v Hradci Králové.

## Archivy, které Rubrika jen odkazuje

Tyto archivy Rubrika nehledá. Asistent řekne, že v nich nehledal, a dá vám odkaz; matriku nebo skicu
si tam otevřete sami v prohlížeči (a můžete ji pak Rubrice předat jako snímek uložený z prohlížeče, viz níže).

| Archiv | Pramen | Proč jen odkaz |
|---|---|---|
| **Zemský archiv v Opavě** | matriky | automatizovaný přístup blokuje ochranou proti botům, kterou Rubrika neobchází |
| **SOA Litoměřice** | matriky | automatizovaný přístup blokuje ochranou proti botům, kterou Rubrika neobchází |
| **Archiv map ČÚZK** | indikační skici Čech a Slezska | mapové služby vyžadují přístupový token vydávaný jen jejich aplikaci |

## Snímek uložený z prohlížeče

Když Rubrika sama sken stáhnout nemůže (Opava, Litoměřice, Třeboň nebo jiný portál), uložte snímek
v prohlížeči (Uložit obrázek, případně snímek obrazovky) do Stažených souborů. Asistent ho nástrojem
`import_scan` najde, zeptá se, zda jde o správný soubor, a s vaší citací a odkazem ho načte jako doklad:
pak funguje zvětšování, nálezy i rodokmen jako u ostatních archivů. Doklad je označený, že snímek dodal
uživatel a odkaz není ověřený.

Nahrát obrázek přímo do chatu jde také, ale jen pro rychlé přečtení: aplikace ho zmenší a Rubrika ho
nedostane, takže z něj nevznikne doklad ani výřez v plném rozlišení. Rubrika čte jen obrázky ve Stažených
souborech (nejvýš 30 dní starých) a ve své složce `import`, nic jiného.

## Podmínky jednotlivých archivů

**Podmínky MZA.** Instalací a použitím přebíráte
[podmínky užití archivních aplikací MZA](https://www.mza.cz/aplikace/podminky-pouziti-aplikace-aron/).
Skeny slouží jen vašemu vlastnímu bádání; bez souhlasu archivu je nezveřejňujte.

**Indikační skici MZA.** Podmínky služby dovolují skici prohlížet a zakazují je zveřejňovat jinde; Rubrika načte
jen dlaždice, které potřebuje pro náhled nebo výřez, a u každého dokladu uvádí citaci a odkaz na skicu v MZA.
Vypnout je lze proměnnou `RUBRIKA_SKETCHES_ENABLED=false`.

**Podmínky SOA Hradec Králové.** Archiv na svých stránkách dovoluje snímky prohlížet i stahovat;
matriky mladší než zákonné ochranné lhůty (100 let narození, 75 let sňatky, 30 let úmrtí) nezpřístupňuje.
Při zveřejnění výřezu je třeba uvést archiv, fond a citaci, kterou Rubrika u nálezu ukazuje.

**Podmínky Porta fontium (SOA Plzeň).** Portál dovoluje používat kopie mimo portál jen podle § 92 autorského
zákona: pro osobní potřebu, pro vědecké nebo vyučovací účely, nevýdělečně a s uvedením zdroje včetně internetové
adresy ([podmínky užití](https://www.portafontium.eu/terms-of-use)). Snímky nezveřejňujte. Rubrika u každého dokladu
z Porta fontium ukazuje citaci a adresu snímku v portálu a asistent vám podmínky připomene při prvním použití
tohoto archivu. Portál žádá 10 s mezi požadavky, hledání proto trvá déle. Matriky mladší než ochranné lhůty portál
nezveřejňuje. Vypnout ho lze proměnnou `RUBRIKA_PF_ENABLED=false`.

**Podmínky SOA Praha.** eBadatelna nemá robots.txt ani zveřejněné podmínky užití. Badatelský řád archivu žádá
při využití a zveřejnění uvést archiv a fond; Rubrika je uvádí v citaci každého dokladu. Snímky se stahují celé
(jak je nabízí prohlížeč archivu) a výřezy se dělají u vás v počítači. Vypnout ji lze proměnnou `RUBRIKA_SOAPRAHA_ENABLED=false`.

**Podmínky SOA Třeboň.** Archiv zveřejňuje snímky jen k prohlížení ve svém DigiArchivu; kopírování,
zveřejňování, prodej nebo užití v jiných projektech zakazuje bez souhlasu vlastníka. Obrazový server navíc
dlaždice vydává jen prohlížeči. Rubrika proto u Třeboně snímky nestahuje ani tuto ochranu neobchází: najde
farnost a knihy a dá odkaz na každý snímek. Snímek si otevřete a případně uložíte sami a za jeho užití
odpovídáte vy. Vypnout ji lze proměnnou `RUBRIKA_TREBON_ENABLED=false`.

**Podmínky AHMP a Národního archivu.** Ani jeden archiv samostatné podmínky užití svého katalogu nezveřejňuje.
AHMP dává do snímků vodoznak a reprodukce k dalšímu užití poskytuje na objednávku; Národní archiv v patičce
katalogu uvádí, že zveřejnění jakékoli části databáze vyžaduje jeho souhlas a řádnou citaci. Snímky proto
nezveřejňujte bez souhlasu archivu. Oba archivy vydávají celý snímek ve vysokém rozlišení jen za captchou;
Rubrika ji nepoužívá a z jednoho snímku načte v plném rozlišení nejvýš polovinu plochy. Vypnout je lze
proměnnými `RUBRIKA_AHMP_ENABLED=false` a `RUBRIKA_NA_ENABLED=false`.

**Policejní přihlášky Národního archivu.** Aplikace přihlášek (`digi.nacr.cz`) běží jen na nešifrovaném HTTP:
hledaná jména a stránky jdou sítí čitelně a cestou je teoreticky může někdo změnit. Rubrika proto u tohoto
jediného serveru HTTP povoluje, obsah bere jako nedůvěryhodný a asistent vás na to upozorní. Samostatné podmínky
užití archiv nezveřejňuje; platí totéž co výše (zveřejnění jen se souhlasem). Vypnout je lze proměnnou
`RUBRIKA_PRIHLASKY_ENABLED=false`.

Jak Rubrika s portály archivů zachází (tempo, robots.txt, identifikace), popisuje
[Ohleduplnost k archivům a odpovědnost](PRAVIDLA.md).
