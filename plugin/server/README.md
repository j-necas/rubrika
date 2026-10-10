# Rubrika – hledání předků v archivech

MCP server pluginu Rubrika. Běží na vašem počítači: v Claude Code a Codexu jako součást pluginu,
v Claude Desktop jako rozšíření `rubrika.mcpb` (postup hledání, skill `ancestor-research`,
se do Claude Desktop nahrává zvlášť jako `rubrika-skill.zip` v nastavení v sekci Skills).
Podporuje MZA, SOA Hradec Králové, SOA Plzeň, SOA Praha, SOA Třeboň,
Archiv hlavního města Prahy a Národní archiv. Dostupnost druhů pramenů
říká nástroj `list_archives`. U Třeboně čte jen katalog a odkazy,
snímky dodává uživatel. Opavu, Litoměřice a archiv map ČÚZK jen odkazuje.
Sčítání MZA z roku 1921 neotevírá.

Server se spouští nástrojem `uv` (https://docs.astral.sh/uv/); při prvním spuštění stáhne
Python a potřebné knihovny (je potřeba internet, první start může trvat déle). Claude Desktop
si `uv` stáhne sám, pokud ho v počítači nenajde; pro Claude Code a Codex ho musíte nainstalovat. Vaše
hledání, doklady a mezipaměť se ukládají do složky `Rubrika` ve vašem domovském adresáři.

Licence MIT pokrývá software, nikoli archivní data. Dodržujte podmínky
každého archivu. Výsledky a výřezy nástrojů dostává váš poskytovatel AI.
Instalace, soukromí, podmínky zdrojů a hlášení chyb: https://github.com/j-necas/rubrika .

Výsledky jsou pracovní badatelské hypotézy s odkazy na zdroj, ne ověřená genealogie.
