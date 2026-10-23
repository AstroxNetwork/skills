"""Verify public stable installation and genuine v0.5.0 update, without business API calls."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from datetime import datetime, timezone

REPO = 'AstroxNetwork/skills'
VERSION = 'v0.6.0'

def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent':'holycrab-public-upgrade-check/0.6.0'})
    with urllib.request.urlopen(request,timeout=60) as response:
        if response.status != 200:
            raise RuntimeError('Public artifact download did not return 200')
        data=response.read(10*1024*1024+1)
        if len(data)>10*1024*1024:
            raise RuntimeError('Artifact exceeded size bound')
        length=response.headers.get('Content-Length')
        if length is not None and int(length)!=len(data):
            raise RuntimeError('Truncated public artifact')
    return data

def bundle(version, directory):
    metadata=json.loads(fetch(f'https://api.github.com/repos/{REPO}/releases/tags/{version}'))
    assert metadata['tag_name']==version and metadata['draft'] is False and metadata['prerelease'] is False
    directory.mkdir()
    assets={asset['name']:asset for asset in metadata['assets']}
    values={}
    for name in ('install.sh','install.ps1','SHA256SUMS'):
        expected=f'https://github.com/{REPO}/releases/download/{version}/{name}'
        assert assets[name]['browser_download_url']==expected
        data=fetch(expected)
        assert 'sha256:'+hashlib.sha256(data).hexdigest()==assets[name]['digest'], name
        (directory/name).write_bytes(data)
        values[name]=data
    checks={name:digest for digest,name in (line.split() for line in values['SHA256SUMS'].decode().splitlines())}
    assert set(checks)=={'install.sh','install.ps1'}
    for name,digest in checks.items(): assert hashlib.sha256(values[name]).hexdigest()==digest
    return {name:hashlib.sha256(data).hexdigest() for name,data in values.items()}

class Run:
    def __init__(self, args):
        self.args=args
        self.report=args.report.resolve()
        self.report.parent.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='holycrab-public-upgrade-',dir=self.report.parent))
        self.data={'status':'running','platform':platform.platform(),'python':sys.version.split()[0],
                   'version':VERSION,'checks':[],'businessRequests':0,'runnerSha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        self.environments=[]
        guard=self.root/'guard';guard.mkdir()
        (guard/'sitecustomize.py').write_text('''import sys, urllib.parse
def check(event,args):
    if event == 'urllib.Request':
        host=urllib.parse.urlsplit(args[0]).hostname or ''
        if host not in ('github.com','api.github.com','raw.githubusercontent.com','objects.githubusercontent.com','release-assets.githubusercontent.com'):
            raise RuntimeError('Release check blocked a non-GitHub request')
sys.addaudithook(check)
''')
        self.guard=guard

    def save(self):
        self.report.write_text(json.dumps(self.data,indent=2)+'\n',encoding='utf-8')

    def record(self,label):
        self.data['checks'].append({'name':label,'status':'passed'})
        self.save(); print('PASS '+label,flush=True)

    def environment(self,label):
        root=self.root/label;root.mkdir();home=root/'home';home.mkdir()
        env={k:v for k,v in os.environ.items() if not k.startswith(('HOLYCRAB_','CODEX_','CLAUDE_')) and k not in ('GH_TOKEN','GITHUB_TOKEN','PYTHONPATH','PYTHONHOME','PYTHONOPTIMIZE')}
        env.update({'HOME':str(home),'USERPROFILE':str(home),'APPDATA':str(home/'AppData/Roaming'),
                    'LOCALAPPDATA':str(home/'AppData/Local'),'XDG_CONFIG_HOME':str(home/'.config'),
                    'HOLYCRAB_INSTALL_PREFIX':str(root/'prefix'),'HOLYCRAB_CONFIG_DIR':str(root/'config'),
                    'HOLYCRAB_INSTALL_AGENTS':'none','HOLYCRAB_INSTALL_MCP':'0','HOLYCRAB_NO_UPDATE_CHECK':'1',
                    'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','PYTHONPATH':str(self.guard)})
        # The CI host is PowerShell 7. Launch legacy 5.1 with its native module
        # search path, as tests/test_windows_adversarial.ps1 already does.
        if os.name=='nt' and label=='upgrade': env.pop('PSModulePath',None)
        self.environments.append((label,env))
        return env

    def command(self,label,command,env,*,input=None,timeout=300):
        result=subprocess.run(command,env=env,input=input,text=True,encoding='utf-8',capture_output=True,timeout=timeout)
        (self.root/(label+'.log')).write_text(result.stdout+'\nSTDERR\n'+result.stderr,encoding='utf-8')
        if result.returncode: raise RuntimeError(f'{label} exited {result.returncode}; see retained log')
        return result.stdout

    def launcher(self,env):
        return Path(env['HOLYCRAB_INSTALL_PREFIX'])/'bin'/('holycrab.cmd' if os.name=='nt' else 'holycrab')

    def cli(self,label,env,*args,**kwargs):
        return self.command(label,[str(self.launcher(env)),*args],env,**kwargs)

    def install(self,label,version,env):
        directory=self.root/version
        command=(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(directory/'install.ps1')]
                 if os.name=='nt' else ['sh',str(directory/'install.sh')])
        self.command(label,command,env,timeout=600)
        assert self.cli(label+'-version',env,'--version').strip()=='holycrab '+version[1:]
        self.record(label)

    def verify_new(self,label,env):
        assert self.cli(label+'-version',env,'--version').strip()=='holycrab 0.6.0'
        assert json.loads(self.cli(label+'-doctor',env,'doctor','--json'))['ok'] is True
        spec=importlib.util.spec_from_file_location('native_checks',self.args.source/'tools/native_install_checks.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.verify_runtime(Path(env['HOLYCRAB_INSTALL_PREFIX'])/'lib/holycrab',self.args.source)
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'post-release','version':'1'}}},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'capabilities_list','arguments':{}}}]
        replies=[json.loads(row) for row in self.cli(label+'-mcp',env,'mcp','serve',input=''.join(json.dumps(m)+'\n' for m in messages)).splitlines()]
        assert len(replies)==3 and all('error' not in reply for reply in replies)
        assert replies[0]['result']['serverInfo']['version']=='0.6.0'
        names=[tool['name'] for tool in replies[1]['result']['tools']]
        assert len(names)==len(set(names))==22
        assert replies[2]['result']['isError'] is False
        caps=replies[2]['result']['structuredContent']
        assert len(caps['models'])==len({model['id'] for model in caps['models']})==16
        latest=json.loads(self.cli(label+'-latest',env,'update','--check'))
        assert latest['updateAvailable'] is False
        self.record(label+' health, immutable runtime, 22 MCP tools, 16 models, current stable version')

    def execute(self):
        latest=json.loads(fetch(f'https://api.github.com/repos/{REPO}/releases/latest'))
        assert latest['tag_name']==VERSION and latest['draft'] is False and latest['prerelease'] is False
        self.data['releaseUrl']=latest['html_url'];self.data['publishedAt']=latest['published_at']
        self.data['artifacts']={version:bundle(version,self.root/version) for version in ('v0.5.0',VERSION)}
        for name in ('install.sh','install.ps1'):
            assert (self.root/VERSION/name).read_bytes()==(self.args.source/name).read_bytes()
        self.record('Public stable Release, both installer digests and checksums')
        fresh=self.environment('fresh')
        self.install('fresh-install',VERSION,fresh);self.verify_new('fresh',fresh)
        old=self.environment('upgrade')
        self.install('actual-v050-install','v0.5.0',old)
        self.cli('isolated-key',old,'auth','set-key','--stdin','--no-verify',input='hc-isolated-release-test-not-a-real-key\n')
        config=Path(old['HOLYCRAB_CONFIG_DIR']);plans=config/'upload-plans';plans.mkdir(exist_ok=True)
        (config/'attempts.json').write_text(json.dumps({'retainedAttempt':{'state':'unknown','taskId':'known-task'}}))
        (plans/'retainedPlan.json').write_text(json.dumps({'uploadPlanId':'retainedPlan','state':'unknown','createdAt':datetime.now(timezone.utc).isoformat(),'expiresAtEpoch':time.time()+3600,'files':[],'uploaded':[]}))
        user_file=Path(old['HOLYCRAB_INSTALL_PREFIX'])/'user-notes.txt';user_file.write_text('keep user data\n')
        watched=[config/'config.json',config/'attempts.json',plans/'retainedPlan.json',user_file]
        for private_file in watched[:-1]: private_file.chmod(0o600)
        assert json.loads(self.cli('v050-baseline-doctor',old,'doctor','--json'))['ok'] is True
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
        check=json.loads(self.cli('v050-detects-v060',old,'update','--check'))
        assert check['updateAvailable'] is True and check['latestVersion']=='0.6.0'
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}, 'Baseline records changed before update'
        self.record('Actual v0.5.0 discovers v0.6.0 via public Latest')
        self.cli('actual-update',old,'update','--yes',timeout=600)
        assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
        self.record('Actual v0.5.0 update preserves credential bytes, recovery records and user files')
        self.verify_new('upgraded',old)

    def cleanup(self):
        errors=[]
        for label,env in self.environments:
            try:
                launcher=self.launcher(env)
                if not launcher.exists(): continue
                self.cli(label+'-uninstall',env,'uninstall','--yes','--purge')
                deadline=time.monotonic()+15
                library=Path(env['HOLYCRAB_INSTALL_PREFIX'])/'lib/holycrab'
                while (launcher.exists() or library.exists()) and time.monotonic()<deadline: time.sleep(.1)
                assert not launcher.exists() and not library.exists(), 'Delayed cleanup did not finish'
                if label=='upgrade': assert (Path(env['HOLYCRAB_INSTALL_PREFIX'])/'user-notes.txt').read_text()=='keep user data\n'
                self.record(label+' actual uninstall and user-file preservation')
            except BaseException as error:
                errors.append(label+': '+type(error).__name__+': '+str(error))
        if errors: raise RuntimeError('; '.join(errors))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();args.source=args.source.resolve();run=Run(args)
    if sys.flags.optimize: raise RuntimeError('Assertions must be enabled for release verification')
    try:
        run.execute()
        run.data['status']='passed'
    except BaseException as error:
        run.data.update(status='failed',error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        try: run.cleanup()
        except BaseException as error: run.data.update(status='failed',cleanupError=type(error).__name__+': '+str(error))
        run.data['finishedAt']=datetime.now(timezone.utc).isoformat();run.save()
    print(json.dumps({'status':run.data['status'],'report':str(run.report),'checks':len(run.data['checks'])}),flush=True)
    return 0 if run.data['status']=='passed' else 1

if __name__=='__main__': raise SystemExit(main())
