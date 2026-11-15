"""Create a private, dedicated profile. Never copies personal history or skills."""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from dotenv import dotenv_values
import yaml

parser = argparse.ArgumentParser()
parser.add_argument('--release', required=True, type=Path)
parser.add_argument('--credential-source', required=True, type=Path)
parser.add_argument('--model',default='openai/gpt-4.1-mini')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
profile = root / '.data/hermes'
profile.mkdir(parents=True, exist_ok=True, mode=0o700)
if (profile / 'config.yaml').exists():
    raise SystemExit('Profile already exists; refusing to overwrite it.')
credentials = dotenv_values(args.credential_source)
key = credentials.get('OPENROUTER_API_KEY')
if not key:
    raise SystemExit('OPENROUTER_API_KEY missing in the chosen credential file.')
config = {
    'model': {'default': args.model, 'provider': 'openrouter', 'max_tokens': 1800},
    'platform_toolsets': {'api_server': []},
    'memory': {'memory_enabled': False, 'user_profile_enabled': False, 'provider': ''},
    'skills': {'external_dirs': [], 'inline_shell': False, 'creation_nudge_interval': 0},
    'curator': {'enabled': False},
    'mcp': {'disabled': True}, 'mcp_servers': {},
    'compression': {'enabled': False},
    'agent': {'max_turns': 2, 'api_max_retries': 1},
    'gateway': {'api_server': {'max_concurrent_runs': 1}},
}
api_key = secrets.token_urlsafe(40)
files = {
    profile / 'config.yaml': yaml.safe_dump(config),
    profile / '.env': f'OPENROUTER_API_KEY={key}\n',
    profile / 'SOUL.md': 'You answer documentation questions from the supplied evidence.\n',
    root / '.env': f'DATA_DIR=.data\nHERMES_URL=http://127.0.0.1:8657\nHERMES_API_KEY={api_key}\nHERMES_MODEL={args.model}\nCOOKIE_SECURE=false\n',
    profile / 'server-key': api_key,
    profile / 'release-path': str(args.release.resolve()),
}
for path, content in files.items():
    path.write_text(content)
    path.chmod(0o600)
sha = subprocess.check_output(['git', '-c', f'safe.directory={args.release}', '-C', str(args.release), 'rev-parse', 'HEAD'],text=True).strip()
provenance = {'release': str(args.release), 'git_commit': sha,
              'api_server_sha256': hashlib.sha256((args.release/'gateway/platforms/api_server.py').read_bytes()).hexdigest(),
              'model': config['model'], 'profile_config': config}
(profile/'provenance.json').write_text(json.dumps(provenance, indent=2))
print('Isolated profile created. No credentials printed.')
