"""Settings and tool names follow the project's naming rules."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_settings_use_the_rubrika_prefix():
    from rubrika.config import Settings
    assert Settings.model_config['env_prefix']=='RUBRIKA_'


def test_archive_prefixes_mark_only_single_archive_tools():
    import ast
    from rubrika.archives import ARCHIVES
    tree=ast.parse((ROOT/'plugin'/'server'/'src'/'rubrika'/'server.py').read_text(encoding='utf-8'))
    tools={n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and any(
        isinstance(d,ast.Call) and getattr(d.func,'attr','')=='tool' for d in n.decorator_list)}
    SINGLE_ARCHIVE_TOOLS=set()   # a tool serving exactly one archive carries its code as prefix; none today
    assert {t for t in tools if t.split('_',1)[0] in ARCHIVES}==SINGLE_ARCHIVE_TOOLS
    assert tools=={'start_research','revise_research','get_research_status','finish_research','view_scan','zoom_scan',
                   'show_saved_evidence','record_finding','list_findings','export_family_tree',
                   'list_archives','find_place','list_sources','open_source','import_scan','continue_research'}
