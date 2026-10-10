# Rubrika – hledání předků v archivech (0.13.2)

Plugin pro Claude Code a Codex, se skillem `ancestor-research` a lokálním
MCP serverem v `server/`. Claude Desktop používá stejný server v balíčku
`rubrika.mcpb` a samostatně nahraný skill `rubrika-skill.zip`.

Rubrika hledá v pramenech MZA, SOA Hradec Králové, SOA Plzeň, SOA Praha,
SOA Třeboň, Archivu hlavního města Prahy a Národního archivu: podle archivu
v matrikách, pozemkových knihách, katastrech, sčítání a soupisech obyvatel.
SOA Třeboň poskytuje jen katalog a odkazy; snímky dodává uživatel.
Opavu, Litoměřice a archiv map ČÚZK plugin pouze odkazuje.

Vstupem je osoba, obec, událost a přibližné období. Asistent čte obrazy
a výřezy, ukládá pracovní přepisy s citacemi a exportuje rodokmen jako
GEDCOM. Výsledky ověřujte proti pramenům; shoda jména sama nepotvrzuje
totožnost osoby. Číslo hledání (`research_id`) uschovejte pro pokračování.

## Instalace a data

Postupy pro jednotlivé aplikace jsou v
[README projektu](https://github.com/j-necas/rubrika#instalace).
Claude Code, Codex a ChatGPT vyžadují [uv](https://docs.astral.sh/uv/) a `git`.
První spuštění stahuje Python a závislosti; potřebuje internet a může
trvat déle. Jde o verzi pro vyzkoušení.

Server běží lokálně. Hledání, doklady a cache ukládá do `~/Rubrika`.
Autor tato data nepřijímá; výsledky a výřezy nástrojů ale dostává váš
poskytovatel AI. Plugin neobsahuje archivní data ani vlastní API klíč.

## Podmínky a hlášení chyb

Při použití dodržujte podmínky archivů; licence MIT platí pro kód,
nikoli pro archivní snímky. Přenos do AI nebo ruční import sám neuděluje
oprávnění k dalšímu užití. Rubrika neobchází přihlášení, captchu ani
ochrany proti botům; 1921 ze sčítání MZA neotevírá. U AHMP a NA zachovává
výslovnou politiku požadavků na zadání uživatele i při zákazu robots.txt;
nejde o doložený souhlas archivu. Policejní přihlášky NA používají HTTP,
takže hledaná jména nejsou při přenosu šifrovaná.

[Pravidla přístupu k archivům](https://github.com/j-necas/rubrika/blob/main/docs/PRAVIDLA.md),
[jak nahlásit chybu](https://github.com/j-necas/rubrika/blob/main/CONTRIBUTING.md)
a [bezpečnostní hlášení](https://github.com/j-necas/rubrika/blob/main/SECURITY.md).
Veřejně neposílejte rodinná data, databáze, celé konverzace ani tokeny.
Rubrika je nezávislý projekt; není službou uvedených archivů.
