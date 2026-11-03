"""Per-user storage, with compatibility for existing source-checkout projects."""
import os
import sys
from pathlib import Path


def user_home():
    override = os.environ.get('AUTOCAPTION_HOME')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'Katuv'
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/Katuv'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'katuv'


def project_dir():
    override = os.environ.get('AUTOCAPTION_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    legacy = Path(__file__).resolve().parents[1]/'.autocaption-web'
    return legacy if legacy.is_dir() else user_home()/'projects'


def tools_dir():
    return user_home()/'tools'
