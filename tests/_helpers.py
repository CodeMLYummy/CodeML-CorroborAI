"""Utilitaires partagés par les tests.

Les tests d'intégration utilisent les vrais fichiers du défi. Leur
emplacement est donné par CORROBORAI_DATA_DIR (défaut : <dépôt>/data).
Ils sont ignorés si les données sont absentes.
"""

from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CORROBORAI_DATA_DIR", REPO / "data"))
HAS_DATA = (DATA_DIR / "manifest.json").is_file()

requires_data = unittest.skipUnless(HAS_DATA, f"données du défi absentes de {DATA_DIR}")


def copy_data(dst: Path) -> Path:
    """Copie les fichiers de données dans un répertoire temporaire (pour les
    tests qui simulent une altération — jamais sur les originaux)."""
    for p in DATA_DIR.iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
    return dst
