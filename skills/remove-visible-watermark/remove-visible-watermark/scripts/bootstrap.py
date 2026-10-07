#!/usr/bin/env python3
"""Find working dependencies, or install them in an isolated local cache on request."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

SKILL_ROOT=Path(__file__).resolve().parents[1]
REQUIREMENTS=SKILL_ROOT/'requirements.txt'
PROBE='import json,sys,numpy,PIL,cv2; print(json.dumps({"python":sys.executable,"numpy":numpy.__version__,"Pillow":PIL.__version__,"OpenCV":cv2.__version__}))'


def probe(python):
    result=subprocess.run([str(python),'-c',PROBE],capture_output=True,text=True)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--install',action='store_true',help='Allow pip to install into an isolated cache if needed')
    p.add_argument('--check-only',action='store_true',help='Only report the current/cache interpreter status')
    p.add_argument('--cache-dir',type=Path)
    args=p.parse_args()
    if args.install and args.check_only:
        p.error('--install and --check-only are mutually exclusive')
    if sys.version_info<(3,10):
        print(json.dumps({'status':'error','message':'Python 3.10 or newer is required.'}));return 2
    current=probe(sys.executable)
    if current:
        print(json.dumps({'status':'ready',**current},ensure_ascii=False));return 0
    fingerprint=hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:10]
    default_cache=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData'/'Local')) if os.name=='nt' else Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))
    environment=(args.cache_dir or default_cache/'remove-visible-watermark')/f'py{sys.version_info.major}{sys.version_info.minor}-{fingerprint}'
    python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    cached=probe(python) if python.is_file() else None
    if cached:
        print(json.dumps({'status':'ready',**cached},ensure_ascii=False));return 0
    if not args.install:
        print(json.dumps({'status':'missing_dependencies','message':'Run this script with --install to create an isolated environment.','cache':str(environment)},ensure_ascii=False));return 2
    try:
        venv.EnvBuilder(with_pip=True).create(environment)
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(REQUIREMENTS)],
                       check=True,stdout=sys.stderr)
        result=probe(python)
        if not result:
            raise RuntimeError('Dependency import failed after installation.')
        print(json.dumps({'status':'ready',**result},ensure_ascii=False));return 0
    except Exception as exc:
        print(json.dumps({'status':'error','message':str(exc),'cache':str(environment)},ensure_ascii=False));return 2


if __name__=='__main__':
    sys.exit(main())
