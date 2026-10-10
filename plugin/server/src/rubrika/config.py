from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import __version__
from .archives import integrated_hosts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUBRIKA_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./rubrika-data")
    live_enabled: bool = False
    # ARON (MZA archival portal) has its own terms of use; the operator enables it deliberately.
    aron_enabled: bool = False
    aron_global_daily_requests: int = Field(default=2000, ge=1)
    # MZA Acta Publica (register catalogue) and the MZA image server (scans of registers, ARON and census, tile by tile).
    acta_global_daily_requests: int = Field(default=1000, ge=1)
    mza_images_global_daily_requests: int = Field(default=5000, ge=1)
    # MZA census records (Sčítací operáty): "Všechna práva vyhrazena"; the operator enables them deliberately.
    census_enabled: bool = False
    census_global_daily_requests: int = Field(default=1000, ge=1)
    # SOA Hradec Kralove (VCA): its own terms of use; the operator enables it deliberately.
    vca_enabled: bool = False
    vca_global_daily_requests: int = Field(default=2000, ge=1)
    # SOA Plzen (Porta fontium): copies may be used outside the portal only under section 92 of the Copyright Act;
    # the operator enables it deliberately (the plugin launcher does, with a notice to the user).
    pf_enabled: bool = False
    pf_global_daily_requests: int = Field(default=800, ge=1)
    # Porta fontium official books (land books, urbaria...) and census: own kind switches, off until the operator has checked them live.
    pf_land_records_enabled: bool = False
    pf_census_enabled: bool = False
    # SOA Praha (eBadatelna): no terms of use are published; the operator enables it after a live check.
    soapraha_enabled: bool = False
    soapraha_global_daily_requests: int = Field(default=1000, ge=1)
    # Archival records of the same archive (censuses of the district offices, land books of towns and estates): a switch per kind,
    # on top of soapraha_enabled; the operator enables them after a live check.
    soapraha_census_enabled: bool = False
    soapraha_land_records_enabled: bool = False
    # SOA Trebon (DigiArchiv): scans are published for viewing in DigiArchiv only; the operator enables it deliberately,
    # after a live check (the plugin launcher does, with a notice to the user).
    trebon_enabled: bool = False
    trebon_global_daily_requests: int = Field(default=800, ge=1)
    # Land registers and census of DigiArchiv: each has its own switch, off until the operator has verified them live.
    trebon_land_records_enabled: bool = False
    trebon_census_enabled: bool = False
    # MZA indication sketches of the stable cadastre (fund D 9): the terms let the public view the images and forbid
    # republishing them; the operator enables them deliberately (the plugin launcher does, with a notice to the user).
    sketches_enabled: bool = False
    sketches_global_daily_requests: int = Field(default=2000, ge=1)
    # Prague City Archives (AHMP) and the National Archives' Jewish registers (NA): off until the operator decides.
    ahmp_enabled: bool = False
    ahmp_global_daily_requests: int = Field(default=1000, ge=1)
    na_enabled: bool = False
    na_global_daily_requests: int = Field(default=1000, ge=1)
    # Police registration forms of Prague (National Archives, digi.nacr.cz): plain HTTP only and robots.txt "Disallow: /";
    # the operator enables them deliberately.
    prihlasky_enabled: bool = False
    prihlasky_global_daily_requests: int = Field(default=600, ge=1)
    # These archives release a whole scan at full resolution only behind a captcha: Rubrika does not assemble one.
    full_res_page_share: float = Field(default=0.5, gt=0, le=1)
    book_daily_images: int = Field(default=150, ge=1)
    max_crawl_delay: float = Field(default=60.0, ge=1, le=600)
    allowed_hosts: list[str] = Field(default_factory=integrated_hosts)
    user_agent: str = f"rubrika/{__version__} (+https://github.com/j-necas/rubrika)"
    request_interval: float = Field(default=1.0, ge=0)
    request_timeout: float = Field(default=20.0, ge=1, le=120)
    tool_timeout: float = Field(default=180.0, ge=1, le=220)
    metadata_ttl: int = Field(default=604800, ge=1)
    # Research is the user's own, on their own disk: it never expires unless a retention in days is set.
    retention_days: int | None = Field(default=None, ge=1)
    # Real MZA handwriting scans compress poorly; a 1600px JPEG detail needs ~1 MB of base64.
    payload_max_chars: int = Field(default=1500000, ge=8000, le=2000000)
    preview_side: int = Field(default=1000, ge=256, le=1600)
    crop_side: int = Field(default=1600, ge=256, le=2400)
    max_source_pixels: int = Field(default=80000000, ge=1000000)
    # At crop_side=1600 a level spans at most 3200px per axis, i.e. up to 14x14 tiles of 256px.
    max_tiles_per_call: int = Field(default=200, ge=1, le=256)
    # Not a quota: after this many archive images in one research the tools pause until the user agrees to go on
    # (continue_research). Guards against a runaway agent; the archives themselves are protected by the daily caps above.
    checkpoint_images: int = Field(default=100, ge=1)
    # Manual mode (import_scan): besides the user's Downloads and <data_dir>/import, these folders may be read (plain file names only).
    import_dirs: list[Path] = Field(default_factory=list)
    import_max_bytes: int = Field(default=60000000, ge=1000)
    max_cache_bytes: int = Field(default=1000000000, ge=1000000)
