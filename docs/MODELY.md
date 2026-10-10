# Jaký model použít a kolik to stojí

Podrobné srovnání modelů. Stručné doporučení je v [README](../README.md#jaký-model-použít).

## Srovnání modelů

Kvalitu čtení rukopisu jsme vyzkoušeli na dvou skutečných zadáních a výsledek ohodnotili podle skutečných
snímků (10 = úplný a přesný přepis, 1 = selhání). Zadání byla bez dalších pokynů:

1. **Čapek:** „Vyhledej v matrikách Malých Svatoňovic narození Karla Čapka 9. ledna 1890“ (SOA Hradec Králové),
2. **TGM:** „Kdo byli kmotři při narození Tomáše Garrigue Masaryka, který se narodil 7. března 1850 v Hodoníně?“
   (MZA, německý zápis psaný kurentem; úzký údaj ze špatně čitelného sloupce, kde se ověřuje i to, zda model jména
   nevymyslí).

| Model | Čapek | TGM | **Kvalita** (průměr) | **Rychlost** | Průměrná doba | Verdikt |
|---|---|---|---|---|---|---|
| Claude Opus 5.5 | 9 | 9 | **9,0** | 9 | 4 min 23 s | doporučeno: nejúplnější přepisy; u kmotrů jména i poctivá nejistota |
| Claude Sonnet 5.5 | 10 | 7 | **8,5** | 9 | 4 min 05 s | doporučeno: nejpřesnější; u kmotrů raději přiznal, že nepřečetl jistě |
| GPT-6-Astra | 7 | 8 | **7,5** | 9 | 5 min 51 s | použitelné: správné hlavní údaje, méně podrobností |
| GPT-6.1-Sol | 6 | 3 | **4,5** | 8 | 6 min 16 s | použitelné: zápis najde, ale kmotry uvedl jinak než ostatní; ověřujte |
| Claude Haiku 5.5 | 4 | 2 | **3,0** | – | 10 min 44 s | nevhodné: bez pokynu Rubriku nepoužije; Čapka přečetl jen z rejstříku |
| GPT-6-Luna | 2 | 3 | **2,5** | – | 0 min 38 s | nevhodné: zápis často nepřečte, opírá se o weby |

**Kvalita** je správnost a úplnost odpovědi ověřená proti snímku; u kmotrů proti shodě čtení více modelů a čtení
autora (kurent je tam těžko čitelný a odborník jej zatím nepotvrdil). **Rychlost** se hodnotí jen u modelů, které zápis našly
u obou zadání: do 4 min 10, do 6 min 9, do 7 min 8, do 8 min 7, do 9 min 6, do 10 min 5. Jde o jeden běh na
konfiguraci a střední uvažování, tedy o orientační srovnání ([podrobnosti](#podrobnosti-zkoušky)).

Doporučujeme **Claude Sonnet 5.5** nebo **Opus 5.5**. Modely GPT jsou použitelné, ale přečtou méně a GPT-6.1-Sol
u kmotrů uvedl jiná jména než ostatní. Malé modely nestačí: Luna se opírala o weby místo o snímek a Haiku 5.5 bez výslovného
pokynu Rubriku nepoužil. S pokynem „Použij Rubriku“ našel Masarykův zápis za 2 minuty, kmotry ale přečetl jen zčásti (skóre 5); poctivě přitom označil, co nepřečetl.

## Úroveň uvažování (reasoning)

Doporučujeme střední (`medium`) nebo vysokou. Ve zkoušce s Čapkem u Sonnetu 5.5 nízká úroveň nestačila (kvalita 3,
zápis nenašel), vysoká dala stejně dobrý výsledek jako střední (9, nejpodrobnější přepis, 8 min 33 s). U GPT-6.1-Sol
se výsledek se změnou úrovně téměř neměnil (6 až 7). Vyšší úroveň částečně pomohla malé Luně (z 2 na 5).

## Spotřeba a doba

Nalezení jednoho zápisu trvalo v naší zkoušce zhruba **6 až 8 minut** a asistent přitom pořídil asi 6 až 7 náhledů
a výřezů snímků. Pátrání je **náročné na tokeny**: asistent listuje knihami, čte snímky jako obrázky a přibližuje si
nejasná místa, takže jedno hledání může spotřebovat velkou část limitu tarifu u Claude nebo ChatGPT. Procenta limitu
jsme neměřili, protože se liší podle tarifu. Nejlépe se osvědčí zadávat konkrétní osobu, obec a roky a rozsáhlé
pátrání rozdělit do více konverzací.

## Podrobnosti zkoušky

Zkouška proběhla 30. 9. 2026 (Rubrika 0.13.0) a u Claude Haiku 5.5 8. 10. 2026 (0.13.2). Každý běh začínal v čerstvé
relaci s prázdnými daty Rubriky. Jde o jeden běh na model a úroveň uvažování, tedy orientační údaje, ne statistiku.

### Čapek

Správné čtení (SOA Hradec Králové, matrika narozených 167-7239, snímek 112): Karel Antonín, narozen 9. 1. 1890,
pokřtěn 13. 1., Malé Svatoňovice čp. 1; otec MUDr. Antonín Čapek, báňský lékař; matka Božena, dcera Karla Novotného,
obchodníka v Hronově čp. 136, a Heleny roz. Holbechrové z Velkého Dřevíče čp. 75; sňatek rodičů Hronov 6. 11. 1884;
bába Marie Petrášová (Malé Svatoňovice čp. 39); kmotr Karel Novotný; svědek Josef Čapek, rolník na Žernově čp. 63.
Číslo folia potvrzené není (modely uvádějí 109 nebo 119).

U každé odpovědi se proti snímku ověřilo 15 údajů (datum, křest, dům, jméno, otec, matka, děd z matčiny strany
včetně čísla domu, babička, sňatek rodičů, bába, svědek, křtící kněz, otcovi rodiče, signatura, odkaz na snímek).
10 = všechny správně a poctivě označená nejistota; 9 = drobná chyba v příjmení nebo nedokončené rozhodnutí u čtení;
7 = správný zápis a hlavní údaje, ale méně než polovina podrobností; 6 = totéž s chybou v údaji; 5 = správný zápis
a snímek, jen základní údaje; 3 = kniha nalezena, zápis ne; 2 = zápis nepřečten, ale poctivě přiznáno;
1 = nepravdivé tvrzení, že zápis nelze v katalogu najít.

| Model | Uvažování | Doba | Údajů ze 15 | Skóre | Poznámka |
|---|---|---|---|---|---|
| Claude Sonnet 5.5 | medium | 6 min 24 s | 15 | **10** | všechno správně; u dědečka zvolil Josefa (souhlasí s opravou v zápisu); uvedl, co neprohledal |
| Claude Sonnet 5.5 | high | 8 min 33 s | 15 | 9 | úplný přepis; svědka Josefa Čapka označil za kmotra, „Holcbechrové“ |
| Claude Sonnet 5.5 | low | 3 min 28 s | 1 | 3 | kniha nalezena, zápis nenašel |
| Claude Opus 5.5 | medium | 6 min 40 s | 15 | 9 | nejúplnější; Antonín/Josef u dědečka nerozhodl; příjmení „Holcbechrová“ |
| GPT-6-Astra | medium | 8 min 22 s | 9 | 7 | správný zápis a odkaz; bez báby, kmotra, sňatku rodičů |
| GPT-6.1-Sol | low | 5 min 32 s | 8 | 7 | správný zápis, jen hlavní údaje |
| GPT-6.1-Sol | medium | 6 min 20 s | 8 | 6 | chybné číslo domu Novotného (130 místo 136) |
| GPT-6.1-Sol | high | 6 min 28 s | 8 | 7 | správný zápis, jen hlavní údaje |
| GPT-6-Luna | high | 9 min 5 s | 6 | 5 | zápis přečetla (jméno, data, rodiče), ale bez čísla domu a podrobností |
| GPT-6-Luna | medium | 58 s | 2 | 2 | zápis neotevřela, hledala na webu a chybně uvedla archiv Zámrsk; přiznala, že zápis nepřečetla |
| Claude Haiku 5.5 | medium | 21 min 02 s | – | 4 | z rejstříku správně přečetl hlavní údaje a odkaz na stranu 109; v matrice listoval vedle správného snímku a zápis nepřečetl; nejistotu uvedl |

### TGM (kmotři)

Zápis: MZA, Hodonín, římskokatolická matrika narozených 1838–1858 (sign. 5306), snímek 155, Seite 304, čtvrtý zápis
vlevo pod „1850 März“. Thomas Johann Masarik, narozen 7. a pokřtěn 8. března 1850; otec Josef Masarik,
„herrschaftlicher Kutscher von hier“; matka Theresia, dcera Josepha Kropaczka z Auspitz. Sloupec kmotrů je těžko
čitelný kurent: autor čte „Franz Mutuschina/Matuschina“ (povolání nejisté, patrně „Amtsdiener“) a jeho ženu
„Eleonora“; čtyři modely se na jménu Franz, příjmení Matuschina/Mutschina/Matušný a na Eleonoře shodly. Čtení kmotrů
**není ověřeno odborníkem**; hodnotí se shoda se čtením a poctivost.

| Model | Skóre | Doba | Poznámka |
|---|---|---|---|
| Claude Opus 5.5 | 9 | 2 min 06 s | Franz Mutschina, úřední sluha, a žena Eleonora; nejistotu poctivě označil |
| Claude Sonnet 5.5 | 7 | 1 min 45 s | přiznal, že jména nepřečetl jistě; jako kandidáta uvedl Franz Matuschina, ženu neuvedl |
| GPT-6-Astra | 8 | 3 min 19 s | František Matuschny a Eleonora; správně, ale bez zmínky o nejistotě čtení |
| GPT-6.1-Sol | 3 | 6 min 12 s | Franz a Veronica, „měšťan“, příjmení Wrbitsky: jiná jména než ostatní |
| GPT-6-Luna | 3 | 17 s | zápis nečetla, „Jiří Matušina a Eleonora“ z webu; křestní jméno chybné |
| Claude Haiku 5.5 | 2 | 26 s | Rubriku nepoužil, jen hledal na webu; kmotry nenašel a přiznal to |
| Claude Haiku 5.5, zadání s „Použij Rubriku.“ | 5 | 1 min 55 s | správný snímek; z kmotrů jen „Franz“ s nečitelným příjmením, druhou kmotru četl „Antonia(?)“; vše označil jako nejisté |

### Spotřeba tokenů

Výstupní tokeny / tokeny přečtené z mezipaměti u zadání Čapek: Sonnet 5.5 low 5,4 tis. / 1,3 mil., medium 17,2 tis. /
3,2 mil., high 33,8 tis. / 3,8 mil.; Opus 13,5 tis. / 3,5 mil.; Haiku 5.5 20,0 tis. / 4,7 mil.; Astra 6,0 tis. /
2,4 mil.; Sol low 3,6 tis. / 1,4 mil., medium 3,9 tis. / 1,4 mil., high 4,8 tis. / 1,5 mil.; Luna medium 0,9 tis. /
0,15 mil., high 10,9 tis. / 4,6 mil. U zadání TGM (výstupní): Opus 18,2 tis., Sonnet 16,6 tis., Astra 3,8 tis.,
Sol 4,5 tis., Luna 0,4 tis.
