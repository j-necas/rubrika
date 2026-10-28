# Instalace Rubriky: návod pro AI asistenta

Uživatel tě sem poslal, abys mu nainstaloval Rubriku, plugin pro hledání předků
v digitalizovaných českých archivech. Mluv s ním jeho jazykem a jednoduše; nemusí
znát terminál. Nic neinstaluj bez jeho souhlasu, nepoužívej `sudo` ani práva
správce a neměň jiná nastavení než ta, která tu jsou. Když tvoje bezpečnostní
pravidla nějaký krok zastaví, neobcházej je: ukaž uživateli příkaz, řekni, co
dělá, a nech ho, ať ho povolí nebo spustí sám.

## 1. Kde běžíš

- **Claude Code** nebo **Codex** (v terminálu i v aplikaci ChatGPT, část Codex):
  máš shell, pokračuj krokem 2.
- **Claude Desktop, claude.ai nebo běžný chat ChatGPT bez shellu:** sám nic
  nenainstaluješ. V Claude Desktop proveď uživatele ručním postupem z
  [README, oddíl Claude Desktop](../README.md#claude-desktop): stáhnout
  `rubrika.mcpb` a `rubrika-skill.zip` z
  https://github.com/j-necas/rubrika/releases/latest, otevřít `.mcpb`,
  nahrát skill a zapnout Code execution. Na claude.ai na webu, v mobilu ani
  v běžném chatu ChatGPT Rubrika nefunguje; řekni to rovnou.

## 2. uv a git

Ověř `uv --version` a `git --version`. Když jeden chybí, zeptej se uživatele,
jestli ho smíš nainstalovat, a ukaž příkaz:

- macOS, Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (git na Macu nabídne systém sám při prvním použití; instalaci stačí potvrdit)
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  a `winget install Git.Git`

Instalátor uv si můžeš před spuštěním stáhnout a přečíst. Po instalaci ověř
verze v novém terminálu, případně celou cestou, kterou instalátor vypíše.

## 3. Plugin

- Claude Code:
  `claude plugin marketplace add j-necas/rubrika` a
  `claude plugin install rubrika@rubrika`
- Codex a aplikace ChatGPT:
  `codex plugin marketplace add j-necas/rubrika` a
  `codex plugin add rubrika@rubrika`
  (v aplikaci ChatGPT pak uživatel plugin najde v části Codex pod **Plugins**)

## 4. Spuštění

Řekni uživateli, ať aplikaci úplně ukončí a znovu spustí a začne **novou
konverzaci**. První start může trvat minutu nebo dvě, protože uv připravuje
Python a knihovny. V nové konverzaci má být dostupný nástroj `list_archives`
(i s předponou pluginu); jeho výsledek ukáže `rubrika_version`.

Pak ať napíše, koho hledá: jméno, obec a přibližný rok. Data a nálezy se
ukládají jen na jeho počítač do složky `Rubrika` v domovské složce.

## Aktualizace a odinstalace

- Claude Code: `claude plugin marketplace update rubrika` a
  `claude plugin update rubrika@rubrika`
- Codex a ChatGPT: `codex plugin marketplace upgrade rubrika`
- Odinstalace: `claude plugin uninstall rubrika@rubrika`, v Codexu
  `codex plugin remove rubrika@rubrika`. Složka `Rubrika` s nálezy zůstává;
  smazat ji může jen uživatel sám.
