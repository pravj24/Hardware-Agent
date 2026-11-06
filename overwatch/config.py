"""Load a small backend-only .env file without evaluating its contents."""
import os
from pathlib import Path

BACKEND_VARIABLES = {'GEMINI_API_KEY', 'GEMINI_MODEL', 'WATCHER_CRUSTDATA_TOKEN'}


def load_backend_env(path=Path('.env')):
    """Process environment wins, including explicitly empty offline-test values.

    Only three supported backend variables are loaded. Values may be quoted but
    are otherwise literal; shell commands, interpolation and exports are unsupported.
    """
    try:
        with Path(path).open('r', encoding='utf-8-sig') as file:
            source = file.read(65_537)
    except FileNotFoundError:
        return
    if len(source) > 65_536:
        raise ValueError('Backend .env file exceeds size limit')
    values = {}
    for number, line in enumerate(source.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, separator, value = line.partition('=')
        if not separator:
            raise ValueError(f'Invalid backend .env assignment at line {number}')
        name, value = name.strip(), value.strip()
        if name not in BACKEND_VARIABLES:
            continue
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'Invalid backend .env quoting at line {number}')
            value = value[1:-1]
        values[name] = value
    for name, value in values.items():
        os.environ.setdefault(name, value)
