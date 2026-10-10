"""Rubrika launcher: the plugin's MCP server (Claude Code) and the MCPB "uv" entry point (Claude Desktop).

Everything the server can do is enabled; data lives in ~/Rubrika. Add a future source here with one line.
Nothing may be written to stdout: it carries the MCP stdio protocol."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL_ENV = {
    "RUBRIKA_LIVE_ENABLED": "true",
    "RUBRIKA_ARON_ENABLED": "true",
    "RUBRIKA_CENSUS_ENABLED": "true",
    "RUBRIKA_VCA_ENABLED": "true",
    "RUBRIKA_PF_ENABLED": "true",   # Porta fontium: terms notice (§ 92 AZ) is shown with every result
    "RUBRIKA_AHMP_ENABLED": "true",   # robots.txt tolerated for user-triggered requests; no captcha, 2 s pace
    "RUBRIKA_NA_ENABLED": "true",     # searched only with archives=["na"]; same rules as AHMP
    "RUBRIKA_SOAPRAHA_ENABLED": "true",   # eBadatelna: no robots rules, no published terms; 1 s pace
    "RUBRIKA_TREBON_ENABLED": "true",     # DigiArchiv, limited mode: catalogue and links only, scans via import_scan
    "RUBRIKA_TREBON_LAND_RECORDS_ENABLED": "true",
    "RUBRIKA_TREBON_CENSUS_ENABLED": "true",
    "RUBRIKA_SOAPRAHA_CENSUS_ENABLED": "true",
    "RUBRIKA_SOAPRAHA_LAND_RECORDS_ENABLED": "true",
    "RUBRIKA_SKETCHES_ENABLED": "true",   # MZA indication sketches (D 9): view only, never republish
    "RUBRIKA_PF_LAND_RECORDS_ENABLED": "true",  # Porta fontium official books; same § 92 notice
    "RUBRIKA_PF_CENSUS_ENABLED": "true",
    "RUBRIKA_PRIHLASKY_ENABLED": "true",  # NA police registrations: plain HTTP host, robots tolerated on user request
}


def configure_environment(environ, home: Path) -> Path:
    for key, value in LOCAL_ENV.items():
        environ.setdefault(key, value)
    environ.setdefault("RUBRIKA_DATA_DIR", str(Path(home) / "Rubrika"))
    return Path(environ["RUBRIKA_DATA_DIR"])


def prepare_data_dir(data: Path) -> Path:
    """Private research data (searches, evidence images): readable by the owner only."""
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    data.chmod(0o700)
    return data


def main():
    data = prepare_data_dir(configure_environment(os.environ, Path.home()))
    # The settings loader reads ".env" from the working directory; never pick up a stranger's file.
    os.chdir(data)
    sys.path.insert(0, str(HERE / "src"))
    from rubrika.cli import main as cli_main
    sys.argv = [sys.argv[0], "serve"]
    cli_main()


if __name__ == "__main__":
    main()
