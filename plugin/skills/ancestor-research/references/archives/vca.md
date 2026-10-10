# Státní oblastní archiv v Hradci Králové, ARchiv ONline (kód `vca`)

Přečti před prací s VČA. Obecný postup je v SKILL.md.

Působnost: Královéhradecký a Pardubický kraj (okresy Hradec Králové, Chrudim, Jičín, Náchod,
Pardubice, Rychnov nad Kněžnou, Svitavy, Trutnov, Ústí nad Orlicí) a okresy Havlíčkův Brod,
Semily a Žďár nad Sázavou. Na hranicích krajů (např. Svitavy, Semily, Havlíčkův Brod, Žďár nad
Sázavou) může být část matrik i jinde; list_archives(district=…) vrátí primární i případný druhý
archiv. Okres je vždy dnešní.

Aplikace je ARON VČA (https://aron.vychodoceskearchivy.cz/), stejný produkt jako ARON MZA, ale
s vlastním API. VČA je součástí pluginu, ale když list_archives ukazuje pro `vca` prázdné
`available_kinds` (vypnutý přepínač), je v not_searched s odkazem a uživatel hledá v prohlížeči.

## Druhy pramene

- **parish_registers**: archiválie typu „matrika“ ze „Sbírky matrik Východočeského kraje“. Signatura
  má tvar `sign.: 51-10 (poř.č. …)`. Popis knihy obsahuje typ matriky, **farnost a seznam obcí**
  farnosti; podle něj se řadí relevance, proto obec hledej i podle farnosti, ne jen podle názvu.
  Matriky jsou v ARONu VČA u větších měst často svázané s **částmi města** (např. „Pardubice-Staré
  Město“, „Svítkov“), ne s obcí ani s katastrálním územím. Ze štítků míst (nesou i popis ARONu, např.
  „část obce X v okrese Y“) vyber příslušné části a předej jich list_sources víc najednou (nejvýš 5
  place_ids). Záznamy s upřesněním (zámek, katastrální území, úřad) matriky obvykle nemají.
- **land_records**: městské pozemkové knihy (typ „evidence nemovitostí“: kniha trhová, gruntovní,
  purkrechtní, převodů, kvitancí, inventářů po zemřelých) z archivů měst, např. Pardubice, Chrudim,
  Vysoké Mýto, Holice, Sezemice, Bohdaneč. Mají `category="land_book"` a jsou ve výpisu první.
  Archiv je k místu většinou nepřipojuje; Rubrika je proto hledá i podle fondu „Archiv města/obce
  <název místa>“ a takové řádky mají `match="fund_name"` a ve výpisu varování. U části města
  (např. „Pardubice-Staré Město“) fond nenajde; zkus místo s názvem celé obce.
  Za nimi následují ostatní digitalizované knihy místa (`category="other"`: městské knihy, kšafty,
  kroniky); jejich druh poznáš z názvu a popisu, kategorii neodhaduj. Matriky a sčítací operáty
  ve výpisu land_records nejsou. **Digitalizované katastry (tereziánský, josefinský) ARON VČA
  nemá**; pozemkové knihy venkovských panství tu nehledej, ověřeno jen město.
- **census**: sčítací operáty (typ „sčítací operát“) uložené po jednotkách, název nese úřad a číslo
  (`poř. č.: 182`), popis rozsah čísel popisných („Hradec Králové čp. 1-50“). Výpis vrací
  `house_numbers` a `relevance.house_number_covered` podle `person.house_number` zadání (null, když
  číslo neznáš nebo rozsah nejde přečíst). Roky lze zadat jako rozsah (`year_from`, `year_to`;
  filtruje archiv), `fund_marks` a `district_archive` se odmítají. Snímky jdou podle čísel
  popisných v rozsahu jednotky; dům dohledej listováním (view_scan).

ID: `vca:place:<uuid>` (místo), `vca:unit:<uuid>` (archiválie, kniha) a `vca:fund:<uuid>` (fond),
snímek `vca:unit:<uuid>:<n>`. Žádné ID z VČA nezačíná `aron:`; ID z MZA ani z VČA nepředávej
nástrojům pro druhý archiv.

## Zvláštnosti

- **`fund_marks` VČA nepodporuje**; parametr u něj vrací chybu. Výpis zužuj místem a roky.
- **Druhopisy matrik** jsou uloženy v příslušných státních okresních archivech a v ARONu VČA
  nejsou. Chybí-li v ARONu kniha nebo ročník, zmiň druhopis jako možný další krok, ale netvrď,
  že tam je.
- `scan_number` je pořadí snímku v archivu, nikoli folio. Foliaci ověř na přečtených snímcích.
- Matriky podléhají ochranným lhůtám (narozených 100 let, oddaných 75, zemřelých 30); mladší
  zápisy archiv nezpřístupňuje. Chybějící mladší kniha není chyba Rubriky.
- Snímky se skládají z dlaždic; přehled i detail proto trvají déle než u MZA. Neopakuj
  požadavky a řiď se stropem snímků na knihu.
- Citaci vrací ARON (`citation`); uveď ji v reportu spolu s `viewer_url`. Nevyráběj vlastní.
- Archiv nabízí ke stažení i celé sady a soubory z úložiště. Rubrika je nikdy nepoužívá; nabízej
  jen snímky přes view_scan a zoom_scan.
