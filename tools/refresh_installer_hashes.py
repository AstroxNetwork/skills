#!/usr/bin/env python3
"""Generate candidate installers from one reviewed runtime allowlist."""
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
ROOT = Path(__file__).resolve().parents[1]


def block(text, name, body):
    start, end = '# BEGIN GENERATED '+name, '# END GENERATED '+name
    if start not in text or end not in text: raise ValueError('Missing generated block: '+name)
    return re.sub(re.escape(start)+r'.*?'+re.escape(end), lambda _:start+'\n'+body.rstrip()+'\n'+end, text, flags=re.S)


def generated():
    manifest=json.loads((ROOT/'runtime-files.json').read_text())
    seen=set()
    for row in manifest['files']:
        for field in ('source','stage','target'):
            path=PurePosixPath(row[field])
            launcher=field=='target' and row['kind']=='launcher' and row[field]=='../../bin/holycrab'
            if path.is_absolute() or ('..' in path.parts and not launcher) or not re.fullmatch(r'[A-Za-z0-9_./-]+',row[field]):
                raise ValueError('Unsafe runtime path: '+row[field])
        key=(row['kind'],row['target'])
        if key in seen: raise ValueError('Duplicate runtime destination')
        seen.add(key)
        row['sha256']=hashlib.sha256((ROOT/row['source']).read_bytes()).hexdigest()
    expected={p.relative_to(ROOT).as_posix() for p in (ROOT/'holycrab/scripts/holycrab_runtime').glob('*.py')}
    recorded={row['source'] for row in manifest['files'] if row['target'].startswith('holycrab_runtime/')}
    if expected != recorded: raise ValueError('Runtime inventory must include every package module exactly once')
    data=(json.dumps(manifest,indent=2)+'\n').encode()
    digest=hashlib.sha256(data).hexdigest()
    shell=(ROOT/'install.sh').read_text();ps=(ROOT/'install.ps1').read_text()
    for row in manifest['files']:
        variable=row.get('legacyVariable')
        if variable:
            shell,count=re.subn(rf'(?m)^SHA256_{variable}=[0-9a-f]{{64}}$',f'SHA256_{variable}={row["sha256"]}',shell)
            if count!=1: raise ValueError('Missing bootstrap hash: '+variable)
            if row['kind']!='launcher':
                pattern=rf'(Name = "{re.escape(row["stage"])}"; Sha256 = ")[0-9a-f]{{64}}(\")'
                ps,count=re.subn(pattern,lambda m:m[1]+row['sha256']+m[2],ps)
                if count!=1: raise ValueError('Missing PowerShell hash: '+variable)
    modules=[row for row in manifest['files'] if not row.get('legacyVariable')]
    shell=block(shell,'RUNTIME DOWNLOADS','\n'.join([
        f'fetch "runtime-files.json" "$TEMP_DIR/runtime-files.json" "{digest}"',*[f'mkdir -p "$TEMP_DIR/{directory}"' for directory in sorted({str(PurePosixPath(r['stage']).parent) for r in modules})],
        *[f'fetch "{r["source"]}" "$TEMP_DIR/{r["stage"]}" "{r["sha256"]}"' for r in modules]]))
    shell=block(shell,'RUNTIME INSTALL','\n'.join([
        *[f'mkdir -p "$LIB_DIR/{directory}"' for directory in sorted({str(PurePosixPath(r['target']).parent) for r in modules})],'install -m 644 "$TEMP_DIR/runtime-files.json" "$LIB_DIR/runtime-files.json"',
        *[f'install -m 644 "$TEMP_DIR/{r["stage"]}" "$LIB_DIR/{r["target"]}"' for r in modules]]))
    ps=block(ps,'RUNTIME DOWNLOADS','\n'.join([
        f'    @{{ Relative = "runtime-files.json"; Name = "runtime-files.json"; Sha256 = "{digest}" }},',
        *[f'    @{{ Relative = "{r["source"]}"; Name = "{r["stage"]}"; Sha256 = "{r["sha256"]}" }}'+(',' if i+1<len(modules) else '') for i,r in enumerate(modules)]]))
    ps=block(ps,'RUNTIME INSTALL','\n'.join([
        *[f'    New-Item -ItemType Directory -Force -Path (Join-Path $LibDir "{directory}") | Out-Null' for directory in sorted({str(PurePosixPath(r['target']).parent) for r in modules})],
        '    Copy-Item -LiteralPath $Downloaded["runtime-files.json"] -Destination (Join-Path $LibDir "runtime-files.json") -Force',
        *[f'    Copy-Item -LiteralPath $Downloaded["{r["stage"]}"] -Destination (Join-Path $LibDir "{r["target"]}") -Force' for r in modules]]))
    return {'runtime-files.json':data,'install.sh':shell.encode(),'install.ps1':ps.encode()}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    for name,data in generated().items():
        path=ROOT/name
        if args.check:
            if path.read_bytes()!=data: raise SystemExit('Generated installer drift: '+name)
        else: path.write_bytes(data)
    print('Runtime inventory and both candidate installers match source files.')

if __name__=='__main__':main()
