# Jak přispět k Rubrice

Rubrika je nezávislý plugin pro bádání v českých archivech. Uvítáme opravy,
ověření instalace, návrhy postupu i hlášení změn archivních portálů.

## Hlášení chyby

Otevřete [GitHub issue](https://github.com/j-necas/rubrika/issues/new/choose).
Uveďte verzi Rubriky, aplikaci (Claude Desktop, Claude Code, Codex nebo ChatGPT),
operační systém, archiv, kroky k zopakování a očekávaný a skutečný výsledek.
Pomůže přesný kód chyby a veřejný odkaz na archiválii.

Veřejné issue čte kdokoli. Použijte smyšlené zadání nebo veřejnou historickou
osobu. Nepřikládejte rodinná data, celou konverzaci, SQLite databázi, `.env`,
tokeny relací, soubory ze soukromých složek ani archivní snímky bez oprávnění
ke zveřejnění. Pokud chyba vyžaduje citlivé podklady, popište nejprve jen
technický problém a domluvte se se správcem na dalším postupu.

Bezpečnostní chyby se řídí [bezpečnostními pokyny](SECURITY.md).

## Změny kódu a dokumentace

Vývojové příkazy, architekturu a vydávání popisuje
[dokumentace pro vývojáře](docs/DEVELOPMENT.md). Pro změnu parseru doplňte
malý syntetický příklad a regresní test. Testovací data nesmí obsahovat
uživatelské relace, autentizaci nebo soukromý výzkum. Skutečné archivní
snímky se do repozitáře nepřidávají.

Při změně chování zdroje ověřte jeho podmínky, povolené cesty a limity.
Živé ověření je ruční (`scripts/archive_smoke.py`, `scripts/aron_smoke.py`)
a nespouští se v CI. Přístupové chyby musí zůstat odlišitelné od nenalezení.
Bez souhlasu správce projektu neměňte režim robots.txt ani obcházení
technických ochran.

Dokumentace a uživatelské texty jsou česky, identifikátory a kód anglicky.
Pull request má stručně říct, jaký problém řeší, výsledné chování a způsob
ověření. Příspěvky se poskytují pod licencí MIT platnou pro kód projektu;
licence archivních a dalších cizích materiálů zůstávají samostatné.
