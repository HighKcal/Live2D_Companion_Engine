"""Application paths shared by source and frozen launches."""
from pathlib import Path
import sys


def application_root() -> Path:
    """Return the portable root containing profiles, assets, models, and local."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = application_root()
