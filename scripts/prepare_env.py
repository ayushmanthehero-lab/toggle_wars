"""Create local secrets once. Never print secret values."""
from pathlib import Path
import base64
import os
import secrets

root = Path(__file__).resolve().parents[1]
local = root / '.local'
local.mkdir(mode=0o700, exist_ok=True)
path = local / 'runtime.env'
if not path.exists():
    values = {
        'POSTGRES_PASSWORD': secrets.token_hex(24),
        'DJANGO_SECRET_KEY': secrets.token_hex(32),
        'FLAGSMITH_ADMIN_EMAIL': 'admin@ci-guard.local',
        'FLAGSMITH_ADMIN_PASSWORD': secrets.token_hex(16),
        'CI_GUARD_TOKEN': secrets.token_hex(32),
        'CI_GUARD_ADMIN_TOKEN': secrets.token_hex(32),
        'CI_GUARD_GITHUB_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    print('Created .local/runtime.env with private permissions. Secret values are not printed.')
else:
    print('Preserved existing .local/runtime.env.')
    existing = dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
    if 'CI_GUARD_GITHUB_ENCRYPTION_KEY' not in existing:
        with path.open('a') as output:
            output.write('\nCI_GUARD_GITHUB_ENCRYPTION_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + '\n')
        os.chmod(path, 0o600)
        print('Added a private GitHub credential encryption key; existing credentials were preserved.')
# Compose requires this env file even before initial Flagsmith provisioning.
flags = local / 'flagsmith.env'
if not flags.exists():
    flags.touch(mode=0o600)
