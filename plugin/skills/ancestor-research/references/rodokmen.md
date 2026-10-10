# Rodokmen z nálezů

## Sestavení stromu
- `root_person` je osoba, od které linie vychází (proband); předkové jsou nad ní.
- Osoba: `id` (krátké, např. `jan-1801`), `given_name`, `surname`, `sex`, `events`.
  Jméno piš v podobě ze zápisu, normalizovanou česky (Matthias → Matěj); původní
  podoba patří do `note`. Neznámé příjmení manželky nech prázdné, nevymýšlej ho.
- Rodina: `husband`, `wife`, `children`; sňatek jen jako `marriage` rodiny.
- Datum po částech: `{year, month, day, qualifier}`. Věk z úmrtního zápisu
  („48 let“) → `age: "48"`; odhad narození z věku → `qualifier: "about"`, `probable`.
- Křest je `baptism`, ne `birth`, pokud zápis neuvádí den narození.
- Povolání (`occupation`) a bydliště/číslo domu (`residence`, místo v `place`).

## Jistota
- `documented`: cituje přepis zápisu (`record`) o této události.
- `probable`: odvozeno ze zápisu jiné události (rodiče z křtu dítěte, věk
  z úmrtí), nebo doloženo jen indexem.
- `hypothesis`: domněnka bez přímého dokladu, jen na výslovnou žádost.
- Rozpor mezi zápisy nevyřešený čtením uveď v `note` události.

## Předání výsledku
Nabídni obě varianty: `all` (s nedoloženými údaji) a `documented_only`.
- **Claude Code:** ulož `<suggested_filename>.ged` (obsah `gedcom` beze změny)
  a `<suggested_filename>.md`: nadpis, blok ```mermaid, seznam nedoložených
  údajů ze `summary` a zdroje (kniha, snímek, viewer_url). Soubor `.ged` neupravuj ručně.
- **claude.ai / Desktop:** nabídni GEDCOM jako soubor ke stažení (jinak celý
  v bloku kódu) a diagram Mermaid zobraz.
- Řekni, kam soubor naimportovat: MyHeritage, Family Tree Builder 8+ a další
  programy načtou GEDCOM 5.5.1. Formát UZED (Family Tree Builder 7) se nevytváří.
- Vždy uveď, že přepisy a totožnost osob nejsou lidsky ověřené, a vyjmenuj
  nedoložené údaje a varování ze `summary`.
