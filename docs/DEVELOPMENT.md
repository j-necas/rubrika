# Rubrika pro vývojáře

Rubrika je **plugin pro Claude** ze dvou částí:

- **skill `ancestor-research`** (`plugin/skills/`): postup pátrání, práce se zdroji a formát výsledků,
- **MCP server** (`plugin/server/`, Python): nástroje pro katalogy a snímky archivů, stav hledání a doklady.

Claude rozhoduje a čte rukopis; server zajišťuje přístup ke zdrojům, limity a uložený stav.
Není zde vlastní model, OCR ani API klíč. Server běží vždy lokálně u uživatele (stdio),
nic se nehostuje.

## Struktura repozitáře

- `.claude-plugin/marketplace.json`: marketplace pro Claude Code, nabízí plugin ze složky `plugin/`.
- `.agents/plugins/marketplace.json`: totéž pro Codex a desktopovou aplikaci ChatGPT (formát Agent Plugins).
- `plugin/`: samostatný plugin; Claude Code kopíruje jen tuto složku, nic v ní nesmí odkazovat ven.
  - `.claude-plugin/plugin.json`: manifest pro Claude, server se spouští `uv run --directory ${CLAUDE_PLUGIN_ROOT}/server main.py`.
  - `plugin.json` a `mcp.json`: manifest a server pro Codex a ChatGPT ([Agent Plugins](https://agent-plugins.org)),
    stejný server spouští `uv run main.py` ve složce `${PLUGIN_ROOT}/server`. Test hlídá, že se verze a popis shodují.
  - `skills/ancestor-research/`: skill a jeho reference; znalosti o jednotlivých archivech jsou
    v `references/archives/<kód>.md`.
  - `server/`: `main.py` (spouštěč, zapíná všechny zdroje, data v `~/Rubrika`), `pyproject.toml`, `uv.lock`, `src/rubrika/`.
- `scripts/build_desktop.py`: sestaví `dist/rubrika.mcpb` a `dist/rubrika-skill.zip` pro Claude Desktop.
- `scripts/aron_smoke.py`: ruční živé ověření ARONu a sčítacích operátů MZA.
- `scripts/archive_smoke.py`: ruční živé ověření archivu podle kódu (`--archive vca`).
- `tests/`: automatické testy včetně testů skutečného MCP SDK.
- `docs/`: [API a datový kontrakt](API.md), [bezpečnost](SECURITY.md), [archivy](ARCHIVY.md)
  a [pravidla přístupu](PRAVIDLA.md).

## Co je v kódu

Archivy definuje registr `archives.py` (kód, hosty, povolené cesty, vypínače, režim robots.txt, tempo,
denní strop; archiv bez druhů pramene je jen odkazový). `territory.py` převádí dnešní okres na archiv,
který drží jeho matriky (podle krajů z let 1960–1990). `robots.py` je parser robots.txt podle RFC 9309
včetně `Crawl-delay` a `gates.py` rozpoznává brány proti botům. Nástroje jsou obecné
(`find_place`, `list_sources`, `open_source`), archiv a druh pramene se volí parametry; přidání
archivu je nový záznam v registru, adaptér a soubor `references/archives/<kód>.md`.

Katalogový klient MZA načítá našeptávač obcí, HTML seznamy knih a skutečné obrazové manifesty.
Zachovává historické názvy, jednotlivá období narození, sňatků a úmrtí i jejich rejstříků
a stránkuje výsledky. Neúplná metadata neznamenají automatické vyřazení knihy. Katalog se
plní průběžně, nikoli hromadným procházením všech možných čísel obcí.

Obrazová vrstva skládá potřebné dlaždice DZI a podporuje regionové požadavky na skutečně
uvedený IIIF zdroj. Vrací obrazové bloky MCP, přehledy a výřezy s přesným převodem souřadnic,
identifikátory a kontrolními součty. Chybějící dlaždice nejsou nahrazovány prázdným místem.

SQLite ukládá hledání, revize dotazu, kandidáty a přečtené/nečitelné oblasti. Aktualizace
zadání nezahazuje doklady. Zdrojové reference doplňuje server, ne model.
Server vynucuje denní stropy archivů a kontrolní bod hledání, dodržuje pravidlo přístupu (robots.txt, `Crawl-delay`, jeden požadavek na host napříč procesy,
denní stropy), nekopíruje cookies návštěvníka a neobchází 403/429 ani brány proti botům. Parser se při změně struktury stránek zastaví, místo aby
vydal prázdný výsledek.

Nástroj `export_family_tree` sestaví z uložených nálezů rodokmen: GEDCOM 5.5.1 (import do
MyHeritage, Family Tree Builderu 8+ aj.) s citací knihy, snímku a odkazu do MZA a diagram
Mermaid. Doložený údaj musí citovat uložený přepis zápisu; nedoložené údaje jsou označené
a varianta `documented_only` je vynechá.

## Vývoj a testy

Vyžaduje Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e "./plugin/server[dev]"

python -m pytest -q -ra
rubrika doctor
rubrika demo --data-dir .demo-data --output .demo-data/report.html
```

Demo simuluje archivní rozhraní a obrazovou pyramidu nad syntetickými daty a vytvoří
HTML/JSON technický report. **Nepoužívá živé MZA, nezapíná Claude a neověřuje čtení rukopisu.**

Plugin z pracovní kopie: `claude --plugin-dir plugin`, případně `/plugin marketplace add ./`
a `/plugin install rubrika@rubrika`. Kontrola manifestů: `claude plugin validate --strict .`
a `claude plugin validate --strict plugin`.

Přímé závislosti v `plugin/server/pyproject.toml` jsou připnuté včetně `mcp==2.2.0`;
`plugin/server/uv.lock` fixuje celý strom. Po změně závislostí spusťte
`cd plugin/server && uv lock` (CI kontroluje `uv lock --check`). `scripts/check_wheels.py` ověří,
že se lock nainstaluje jen z hotových balíčků na Macu (Apple Silicon, Intel) i Windows (x64, ARM);
běží v CI. Když některá knihovna přestane pro platformu dodávat balíčky, omezte její verzi pro
tu platformu v `[tool.uv] constraint-dependencies` (tak je řešená `cryptography`).
CI spouští testy na Linuxu, macOS i Windows. lxml na Windows obsahuje starší libxml2 (2.11
proti 2.14), takže testovací HTML musí být úplný dokument jako živé stránky.

## Ověření proti skutečnému MZA

Živé požadavky jsou ve výchozím nastavení serveru vypnuté (`RUBRIKA_LIVE_ENABLED`,
`RUBRIKA_ARON_ENABLED`, `RUBRIKA_CENSUS_ENABLED`); plugin je zapíná ve `plugin/server/main.py`.

```bash
# matriky: obec, kniha, první snímek a výřez
rubrika probe --allow-live --place Znojmo --image \
  --data-dir .probe-data --output .probe-data/source-probe.json

# pozemkové knihy a katastry (ARON)
RUBRIKA_LIVE_ENABLED=true RUBRIKA_ARON_ENABLED=true .venv/bin/python scripts/aron_smoke.py "Znojmo" 1700 1800

# sčítací operáty
RUBRIKA_LIVE_ENABLED=true RUBRIKA_CENSUS_ENABLED=true .venv/bin/python scripts/aron_smoke.py --census "Brno" 1890
```

Další archivy se ověřují skriptem `scripts/archive_smoke.py`, například VČA:

```bash
RUBRIKA_LIVE_ENABLED=true RUBRIKA_VCA_ENABLED=true .venv/bin/python scripts/archive_smoke.py --archive vca "Hradec Králové" 1800 1850
```

Probe se při nejednoznačné obci zastaví, nevybírá potichu první možnost. 403, 429, zákaz
v robots.txt nebo změna HTML jsou chyby přístupu, ne prázdné výsledky.

## Vydání nové verze

1. Zvyšte verzi v `plugin/server/src/rubrika/__init__.py`, `plugin/server/pyproject.toml`,
   `plugin/.claude-plugin/plugin.json` a `plugin/plugin.json` (test hlídá, že se shodují).
   Aktualizujte verzi skillu, README a spusťte `uv lock`.
   Aktualizujte také výchozí `Observation.skill_version` v `models.py`.
2. Push do `main`: uživatelé Claude Code dostanou novou verzi přes `/plugin marketplace update`.
3. `python scripts/build_desktop.py` vytvoří `dist/rubrika.mcpb` a `dist/rubrika-skill.zip`;
   přiložte je k novému vydání v GitHub Releases.

## Limity a data

Hledání je dlouhý projekt (linie přes několik generací), proto není přídělem omezené: knihy, stránky,
požadavky ani počet hledání. Meze mají dva účely:

- **Ohled na archivy** (nepřekročitelné, sdílené všemi hledáními, obnoví se o půlnoci UTC): denní strop
  požadavků per archiv (`RUBRIKA_<ARCHIV>_GLOBAL_DAILY_REQUESTS`), u MZA per aplikace (Acta Publica 1 000,
  obrazový server 5 000, ARON 2 000, sčítání 1 000, skici 2 000), 150 snímků z jedné knihy denně
  (`RUBRIKA_BOOK_DAILY_IMAGES`), nejvýš polovina plného rozlišení snímku za captchou, tempo a robots.txt.
  Vycházejí z podmínek archivů, viz [archivy](ARCHIVY.md).
- **Kontrolní bod proti zacyklenému agentovi:** po 100 snímcích z archivů v jednom hledání
  (`RUBRIKA_CHECKPOINT_IMAGES`) vrátí view_scan a zoom_scan CHECKPOINT; model shrne stav, zeptá se uživatele
  a na jeho souhlas zavolá continue_research. Import, opětovné ukázání dokladu a výřezy z importů nic nečerpají.

`get_research_status` ukazuje v `usage`, kolik snímků zbývá do kontrolního bodu a co archivy a otevřené
knihy dnes ještě dají. Starší volby `RUBRIKA_MAX_*` a `RUBRIKA_USER_DAILY_*` se ignorují.

Hledání a doklady nikdy nevyprší a nic se nemaže samo; server při startu jen ořízne mezipaměť.
Kdo chce stará hledání mazat, nastaví `RUBRIKA_RETENTION_DAYS` (počet dní nečinnosti) a spustí
ruční úklid. Nad daty pluginu:

```bash
RUBRIKA_DATA_DIR=~/Rubrika RUBRIKA_RETENTION_DAYS=365 rubrika cleanup
RUBRIKA_DATA_DIR=~/Rubrika rubrika export search_SKUTECNE_ID --output report.html
```

Export je samostatný HTML dokument se zdrojovými výřezy.
