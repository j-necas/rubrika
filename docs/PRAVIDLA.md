# Ohleduplnost k archivům a odpovědnost

Jak Rubrika zachází s portály archivů a kdo za co odpovídá. Stručná verze je v
[README](../README.md#soukromí-a-pravidla). Podmínky jednotlivých archivů jsou v
[přehledu archivů](ARCHIVY.md#podmínky-jednotlivých-archivů).

## Ohleduplnost k archivům

Rubrika stojí na práci archivů, které matriky a další prameny digitalizovaly a zdarma
zpřístupnily. Na vaše zadání k jejich portálům přistupuje programově a omezuje zátěž:

- **Jen na pokyn člověka.** Každý dotaz do archivu patří ke konkrétnímu hledání, které zadal
  uživatel. Rubrika neprochází katalogy plošně, nestahuje celé knihy a nepracuje na pozadí.
- **Šetrně k serverům.** Klade dotazy pomalu (zhruba jeden za sekundu, u některých archivů
  jeden za 2 nebo 10 sekund podle jejich pravidel), má denní stropy
  a mezipaměť, aby se nic nestahovalo dvakrát. Ze skenu načte jen tu část a v tom rozlišení,
  které je potřeba přečíst.
- **S popsanými pravidly přístupu.** U Archivu hlavního města Prahy
  a Národního archivu (židovské matriky i policejní přihlášky), jejichž robots.txt zakazuje automatický přístup celému webu, Rubrika
  na vaše zadání požadavky přesto provádí. Je to výslovná politika projektu, nikoli souhlas archivu.
  Policejní přihlášky Národního archivu jsou dostupné jen přes nešifrované HTTP; jména, která
  v nich hledáte, tak putují bez šifrování a Rubrika na to upozorní.
  U ostatních archivů se řídí robots.txt, včetně požadovaného tempa. Nikdy
  neobchází přihlášení, ochranu proti robotům ani odmítnutí: když ho archiv odmítne, zastaví
  se a řekne to uživateli. Nemění kvůli tomu host, nepoužívá proxy, neobchází captchu
  a nestahuje hromadně.
- **Představuje se.** Archivu se hlásí jménem a odkazem na projekt: jeho správce v záznamech
  uvidí `rubrika/<verze> (+https://github.com/j-necas/rubrika)` a ví, kam se obrátit.
- **Nic nezveřejňuje.** Snímky nikam nenahrává ani nešíří. Výřez zápisu zůstává u uživatele
  jako doklad a jde jen do jeho vlastní konverzace s AI.
- **Vždy s odkazem na zdroj.** Každý nález nese citaci archiválie a odkaz na sken v portálu
  archivu, takže cesta k originálu vede vždy zpět do archivu.

## Kdo za co odpovídá

Rubrika je nástroj ve vašich rukou, v podstatě webový prohlížeč, který místo myši ovládáte
zadáním v konverzaci. Běží na vašem počítači a do archivu jde jen tehdy, když se na něco
zeptáte; stránky a skeny za vás otevírá AI podle vašeho zadání. Každý požadavek do archivu
tak odchází z vašeho počítače, na váš pokyn a vaším jménem, stejně jako když archiv
procházíte sami.

Při používání Rubriky:

- dodržujete podmínky užití archivů, jejichž portály procházíte (podmínky příslušného archivu, dnes MZA, SOA Hradec Králové, SOA Plzeň, SOA Praha, SOA Třeboň, AHMP a Národního archivu; u MZA [podmínky ARON](https://www.mza.cz/aplikace/podminky-pouziti-aplikace-aron/)),
- sami rozhodujete, co se skeny a výřezy uděláte; archivy obvykle dovolují užití pro osobní
  potřebu a zveřejnění podmiňují svým souhlasem,
- doporučujeme v nastavení AI vypnout použití konverzací ke zlepšování modelů.

Autor poskytuje zdarma software a neprovozuje službu, která by přijímala vaše hledání nebo
archivní snímky. Odpovědnost za konkrétní užití a konstrukci nástroje se posuzuje podle
příslušných pravidel; samotné lokální spuštění ji automaticky nepřesouvá na uživatele.
Software je poskytován tak, jak je, bez záruky (licence [MIT](../LICENSE)); licence kódu
neuděluje oprávnění k archivním materiálům.
