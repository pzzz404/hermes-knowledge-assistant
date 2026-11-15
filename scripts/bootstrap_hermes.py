"""Install the exact upstream Hermes source and dependencies in private data."""
import argparse
import subprocess
from pathlib import Path

COMMIT='2a5dc0ef3df433a36abed9ee544ea067d807c438'
REPOSITORY='https://github.com/NousResearch/hermes-agent.git'
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--destination',type=Path,default=root/'.data/hermes-runtime')
args=p.parse_args();dest=args.destination.resolve()
def run(*argv):subprocess.run(list(argv),check=True)
if not dest.exists():
    dest.mkdir(parents=True,mode=0o700)
    run('git','init','--quiet',str(dest))
    run('git','-C',str(dest),'remote','add','origin',REPOSITORY)
    run('git','-C',str(dest),'fetch','--depth=1','origin',COMMIT)
    run('git','-C',str(dest),'checkout','--detach',COMMIT)
actual=subprocess.check_output(['git','-C',str(dest),'rev-parse','HEAD'],text=True).strip()
dirty=subprocess.check_output(['git','-C',str(dest),'status','--porcelain'],text=True).strip()
if actual!=COMMIT or dirty:
    raise SystemExit('The dedicated source does not match the clean pinned revision.')
# The pinned release declares aiohttp in its sms extra rather than core;
# the HTTP API adapter also requires it. Installing a dependency enables no tools.
run('uv','sync','--frozen','--no-dev','--extra','sms','--project',str(dest))
print('Clean dedicated upstream Hermes installed:',COMMIT)
