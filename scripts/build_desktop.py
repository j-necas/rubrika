#!/usr/bin/env python3
"""Build the Claude Desktop release files: dist/rubrika.mcpb and dist/rubrika-skill.zip.

Claude Code installs the plugin straight from this repository (.claude-plugin/marketplace.json → plugin/),
so it needs no build. Requires only Python's standard library."""
import argparse
import ast
import json
import re
from pathlib import Path
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugin'
SERVER=PLUGIN/'server'
SERVER_SRC=SERVER/'src'/'rubrika'
SKILL=PLUGIN/'skills'/'ancestor-research'
SERVER_FILES=('main.py','pyproject.toml','uv.lock','.python-version','README.md')
FIXED_TIME=(2026,1,1,0,0,0)


def plugin_manifest():
    return json.loads((PLUGIN/'.claude-plugin'/'plugin.json').read_text(encoding='utf-8'))


def version():
    text=(SERVER_SRC/'__init__.py').read_text(encoding='utf-8')
    return re.search(r'__version__\s*=\s*"([^"]+)"',text)[1]


def server_tools():
    """MCP tools as declared in server.py (decorated with @mcp.tool), so the manifest never goes stale."""
    tree=ast.parse((SERVER_SRC/'server.py').read_text(encoding='utf-8'))
    tools=[]
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and any(
                isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='tool' for d in node.decorator_list):
            tools.append({'name':node.name,'description':(ast.get_docstring(node) or '').split('\n')[0]})
    return sorted(tools,key=lambda t:t['name'])


def mcpb_manifest():
    plugin=plugin_manifest()
    return {'manifest_version':'0.4','name':'rubrika','display_name':plugin['displayName'],'version':version(),
            'description':plugin['description'],'author':plugin['author'],'license':'MIT',
            # Same launch shape as the official MCPB hello-world-uv example; the host provides uv.
            'server':{'type':'uv','entry_point':'main.py',
                      'mcp_config':{'command':'uv','args':['run','--directory','${__dirname}','main.py']}},
            'tools':server_tools()}


def _write_zip(path,entries):
    path.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(path,'w',ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info=ZipInfo(name,date_time=FIXED_TIME);info.external_attr=0o644<<16;info.compress_type=ZIP_DEFLATED
            archive.writestr(info,entries[name])
    return path


def _tree_files(base):
    """Regular files under base, sorted; never dotfiles (.DS_Store, .env) or symlinks."""
    return [p for p in sorted(Path(base).rglob('*')) if p.is_file() and not p.is_symlink()
            and not any(part.startswith('.') for part in p.relative_to(base).parts)]


def build(output_dir):
    output_dir=Path(output_dir)
    bundle={'manifest.json':(json.dumps(mcpb_manifest(),ensure_ascii=False,indent=2)+'\n').encode(),
            'LICENSE':(PLUGIN/'LICENSE').read_bytes()}
    bundle.update((name,(SERVER/name).read_bytes()) for name in SERVER_FILES)
    for p in _tree_files(SERVER_SRC):
        if p.suffix not in ('.py','.pem') or '__pycache__' in p.parts or p.name.startswith('demo'): continue
        bundle['src/rubrika/'+p.relative_to(SERVER_SRC).as_posix()]=p.read_bytes()
    # Claude Desktop installs the .mcpb without skills; the ancestor-research skill is uploaded separately (Settings → Skills).
    skill={'ancestor-research/'+p.relative_to(SKILL).as_posix():p.read_bytes() for p in _tree_files(SKILL)}
    return _write_zip(output_dir/'rubrika.mcpb',bundle),_write_zip(output_dir/'rubrika-skill.zip',skill)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default='dist')
    print('\n'.join(str(p) for p in build(parser.parse_args().output_dir)))


if __name__=='__main__':main()
