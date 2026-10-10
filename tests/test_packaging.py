"""The repository is a Claude Code plugin marketplace; scripts/build_desktop.py packs the same server for Claude Desktop."""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT=Path(__file__).resolve().parents[1]
PLUGIN=ROOT/'plugin'
SERVER=PLUGIN/'server'
LAUNCH={'command':'uv','args':['run','--directory','${CLAUDE_PLUGIN_ROOT}/server','main.py']}


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def build():
    return load(ROOT/'scripts'/'build_desktop.py','build_desktop')

def launcher():
    return load(SERVER/'main.py','rubrika_main')

def plugin_manifest():
    return json.loads((PLUGIN/'.claude-plugin'/'plugin.json').read_text(encoding='utf-8'))

def tracked(prefix):
    out=subprocess.run(['git','ls-files','--',prefix],cwd=ROOT,capture_output=True,text=True,check=True).stdout
    return out.splitlines()


def test_marketplace_offers_the_plugin_directory():
    market=json.loads((ROOT/'.claude-plugin'/'marketplace.json').read_text(encoding='utf-8'))
    assert market['name']=='rubrika' and market['owner']['name']
    [entry]=market['plugins']
    assert entry['name']==plugin_manifest()['name']=='rubrika'
    assert entry['source']=='./plugin' and (ROOT/entry['source']/'.claude-plugin'/'plugin.json').is_file()


def test_plugin_starts_its_own_server_with_uv():
    manifest=plugin_manifest()
    assert manifest['mcpServers']=={'rubrika':LAUNCH}
    assert (SERVER/'main.py').is_file() and (SERVER/'pyproject.toml').is_file() and (SERVER/'uv.lock').is_file()
    assert 'přebíráte podmínky užití' in manifest['description']


def test_plugin_directory_is_self_contained():
    # Claude Code copies only the plugin directory into its cache; nothing may point outside it.
    files=tracked('plugin')
    assert files and not any(os.path.islink(ROOT/f) for f in files)
    assert not any('..' in arg for arg in plugin_manifest()['mcpServers']['rubrika']['args'])
    # Cowork refuses a top-level bin/; a plugin-level .mcp.json would shadow plugin.json's server.
    assert not any(f.startswith('plugin/bin/') for f in files) and 'plugin/.mcp.json' not in files
    assert not any(re.search(r'(^|/)(\.env|tests?/|.*\.sqlite)',f.removeprefix('plugin/')) for f in files)


def test_versions_agree():
    from rubrika.models import Observation
    version=re.search(r'__version__\s*=\s*"([^"]+)"',(SERVER/'src'/'rubrika'/'__init__.py').read_text(encoding='utf-8'))[1]
    project=tomllib.loads((SERVER/'pyproject.toml').read_text(encoding='utf-8'))['project']
    assert project['version']==plugin_manifest()['version']==build().version()==version
    assert Observation(kind='next_step').skill_version==version


def test_server_requires_the_mcp_sdk_without_extras():
    # uv run installs only [project].dependencies; the server cannot start without the SDK.
    project=tomllib.loads((SERVER/'pyproject.toml').read_text(encoding='utf-8'))
    assert 'mcp==2.2.0' in project['project']['dependencies']
    assert project['project']['requires-python']=='>=3.12,<3.15' and project['tool']['uv']['package'] is False
    assert (SERVER/'.python-version').read_text(encoding='utf-8')=='3.12\n'


def test_mcpb_manifest_and_contents(tmp_path):
    b=build();mcpb,_=b.build(tmp_path)
    with ZipFile(mcpb) as z:
        names=set(z.namelist())
        m=json.loads(z.read('manifest.json'))
        assert z.read('pyproject.toml')==(SERVER/'pyproject.toml').read_bytes()
        assert z.read('main.py')==(SERVER/'main.py').read_bytes()
    assert m['manifest_version']=='0.4' and m['name']=='rubrika'
    assert m['display_name']==plugin_manifest()['displayName']=='Rubrika'
    assert m['version']==b.version() and m['server']=={'type':'uv','entry_point':'main.py',
        'mcp_config':{'command':'uv','args':['run','--directory','${__dirname}','main.py']}}
    assert 'user_config' not in m and 'přebíráte podmínky užití' in m['description']
    assert {t['name'] for t in m['tools']}=={t['name'] for t in b.server_tools()}
    assert {'main.py','pyproject.toml','uv.lock','.python-version','README.md','LICENSE',
            'src/rubrika/server.py','src/rubrika/census.py','src/rubrika/certs/geant-tls-rsa-1.pem'}<=names
    assert not any(n.split('/')[-1].startswith('demo') for n in names)
    assert not any(part in ('tests','.venv','__pycache__') for n in names for part in n.split('/'))


def test_manifest_tools_follow_server_code():
    tools=build().server_tools()
    assert all(t['description'] for t in tools) and len({t['name'] for t in tools})==len(tools)>=15


def test_skill_zip_for_desktop_users(tmp_path):
    _,skill=build().build(tmp_path)
    with ZipFile(skill) as z:
        names=set(z.namelist())
    assert 'ancestor-research/SKILL.md' in names and any(n.startswith('ancestor-research/references/') for n in names)
    assert not any(part.startswith('.') for n in names for part in n.split('/'))


def test_desktop_build_is_deterministic(tmp_path):
    a=build().build(tmp_path/'a');b=build().build(tmp_path/'b')
    assert [p.read_bytes() for p in a]==[p.read_bytes() for p in b]


def test_tree_files_skips_dotfiles_and_symlinks(tmp_path):
    base=tmp_path/'skills';(base/'ancestor-research').mkdir(parents=True)
    (base/'ancestor-research'/'SKILL.md').write_text('x');(base/'ancestor-research'/'.DS_Store').write_text('x');(base/'.env').write_text('SECRET=1')
    (base/'ancestor-research'/'link.md').symlink_to(base/'ancestor-research'/'SKILL.md')
    assert [p.relative_to(base).as_posix() for p in build()._tree_files(base)]==['ancestor-research/SKILL.md']


def test_launcher_enables_everything_without_overriding(tmp_path):
    env={'RUBRIKA_ARON_ENABLED':'false'}
    data=launcher().configure_environment(env,tmp_path)
    assert env['RUBRIKA_LIVE_ENABLED']=='true' and env['RUBRIKA_CENSUS_ENABLED']=='true' and env['RUBRIKA_VCA_ENABLED']=='true'
    assert env['RUBRIKA_ARON_ENABLED']=='false' and 'RUBRIKA_RETENTION_DAYS' not in env
    assert data==tmp_path/'Rubrika' and env['RUBRIKA_DATA_DIR']==str(tmp_path/'Rubrika')


@pytest.mark.skipif(sys.platform=='win32',reason='Windows has no POSIX mode bits; the home folder ACL keeps it private')
def test_launcher_data_dir_is_private(tmp_path):
    data=launcher().prepare_data_dir(tmp_path/'Rubrika')
    assert data.is_dir() and (data.stat().st_mode & 0o777)==0o700


@pytest.mark.parametrize('artifact',['plugin','mcpb'])
def test_server_answers_over_stdio(tmp_path,artifact):
    # A foreign .env in the working dir; a home with a space and diacritics.
    if artifact=='mcpb':
        mcpb,_=build().build(tmp_path/'dist');root=tmp_path/'bundle';ZipFile(mcpb).extractall(root)
    else:
        root=SERVER
    home=tmp_path/'Jiřina Nováková';home.mkdir()
    work=tmp_path/'cwd';work.mkdir();(work/'.env').write_text('RUBRIKA_LIVE_ENABLED=false\nRUBRIKA_DATA_DIR=/nonexistent\n')
    env={k:v for k,v in os.environ.items() if not k.startswith('RUBRIKA_')}
    env.update({'HOME':str(home),'USERPROFILE':str(home)})
    proc=subprocess.Popen([sys.executable,str(root/'main.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env,cwd=work)
    def send(message):
        proc.stdin.write(json.dumps(message)+'\n');proc.stdin.flush()
    try:
        # A real client waits for each reply; closing stdin early ends the session before queued requests run.
        send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},
              'clientInfo':{'name':'packaging-test','version':'0'}}})
        assert 'result' in json.loads(proc.stdout.readline())
        send({'jsonrpc':'2.0','method':'notifications/initialized'})
        send({'jsonrpc':'2.0','id':2,'method':'tools/list'})
        tools={t['name'] for t in json.loads(proc.stdout.readline())['result']['tools']}
    finally:
        # communicate() closes stdin and drains both pipes, so a server blocked on a full pipe still exits.
        _,stderr=proc.communicate(timeout=30)
    assert {'start_research','find_place','list_sources','open_source','list_archives'}<=tools, stderr[-2000:]
    assert (home/'Rubrika').is_dir()


# ChatGPT desktop app and Codex read the portable Agent Plugins format from the same plugin directory.
AGENT_PLUGIN='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
AGENT_MCP='https://agent-plugins.org/schemas/1.0.0/mcp.schema.json'


def test_openai_plugin_manifest_matches_the_claude_one():
    manifest=json.loads((PLUGIN/'plugin.json').read_text(encoding='utf-8'))
    assert manifest['$schema']==AGENT_PLUGIN and manifest['name']==plugin_manifest()['name']=='rubrika'
    assert manifest['version']==plugin_manifest()['version'] and manifest['description']==plugin_manifest()['description']
    # The Agent Plugins schema allows only these top-level keys.
    assert set(manifest)<={'$schema','name','version','description','author','homepage','repository','license','keywords','extensions'}
    assert manifest['extensions']['com.openai']['interface']['displayName']==plugin_manifest()['displayName']


def test_openai_plugin_starts_the_same_server():
    config=json.loads((PLUGIN/'mcp.json').read_text(encoding='utf-8'))
    assert config=={'$schema':AGENT_MCP,'mcpServers':{'rubrika':{
        'type':'stdio','command':'uv','args':['run','main.py'],'cwd':'${PLUGIN_ROOT}/server'}}}


def test_openai_marketplace_offers_the_plugin_directory():
    market=json.loads((ROOT/'.agents'/'plugins'/'marketplace.json').read_text(encoding='utf-8'))
    assert market['name']=='rubrika'
    [entry]=market['plugins']
    assert entry['name']=='rubrika' and entry['source']=={'source':'local','path':'./plugin'}
    assert (ROOT/entry['source']['path']/'plugin.json').is_file()
