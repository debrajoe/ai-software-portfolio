"""Small cross-platform checks for local private files used by provider adapters."""
import os
import stat
from pathlib import Path

def private_file(path):
    path=Path(path)
    if not path.is_file() or path.is_symlink():return False
    if os.name!='nt':return stat.S_IMODE(path.stat().st_mode)==0o600
    try:return path.resolve().is_relative_to(Path.home().resolve())
    except OSError:return False

def private_directory(path):
    path=Path(path)
    if not path.is_dir() or path.is_symlink():return False
    if os.name!='nt':return path.stat().st_uid==os.getuid() and stat.S_IMODE(path.stat().st_mode)==0o700
    try:return path.resolve().is_relative_to(Path.home().resolve())
    except OSError:return False
