"""Utilitaires partagés par les tests.

Deux familles de données :

* **Jeu synthétique** (``tests/fixtures/dataset.py``) — toujours disponible,
  généré à la demande (Excel ou CSV) ; utilisé par les tests génériques.
* **Fichiers du défi** — emplacement donné par ``CORROBORAI_DATA_DIR``
  (défaut : <dépôt>/data). Les tests qui figent des résultats propres à ces
  fichiers (ex. les 18 anomalies de référence) ne s'exécutent que si les
  empreintes correspondent exactement aux fichiers distribués ; sur d'autres
  données (ex. un jeu de test plus volumineux), ils sont ignorés.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from corroborai.io.loaders import name_key, sha256_bytes

REPO = Path(__file__).resolve().parents[1]
CHALLENGE_DIR = Path(os.environ.get("CORROBORAI_DATA_DIR", REPO / "data"))

# Empreintes des fichiers distribués pour le défi (manifest.json d'origine)
CHALLENGE_SHA256 = {
    "employesourceanonymisevf": "4f825d17938a8170ea1ed2986b12ed240987430984b3693b993bd8f2f290f5b6",
    "employedestinationanonymisevf": "67c4d64b4a11e566bc4962da8a50562b47f394c27a3555476117b0a702b10717",
    "detailduposte": "ab142775628bc16d4239b01d94733f9f094004ff9c36db33bdc190862ab18b6c",
    "motifdelasituationdemploi": "5d143df7ee15e3532eb01469a5a442bc87d1a80edc9cbb3d90bcb246f9e0973b",
    "mapping": "6e7e618d6efbc67576056de610f91b8f47beebbbf59873ffe797f3d305a677ee",
}


def _is_challenge_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    found = {}
    for p in path.iterdir():
        if p.is_file() and not p.name.startswith("."):
            found[name_key(p.stem)] = p
    return all(k in found and sha256_bytes(found[k].read_bytes()) == sha
               for k, sha in CHALLENGE_SHA256.items())


HAS_CHALLENGE = _is_challenge_dir(CHALLENGE_DIR)
requires_challenge_data = unittest.skipUnless(
    HAS_CHALLENGE, f"fichiers du défi absents ou différents dans {CHALLENGE_DIR} (tests propres au défi ignorés)")

# --------------------------------------------------------------------------- jeu synthétique

_FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="corroborai_fixture_"))
atexit.register(shutil.rmtree, _FIXTURE_ROOT, ignore_errors=True)
_BUILT: dict[tuple[str, str | None], Path] = {}


def fixture_dir(fmt: str = "xlsx", manifest: str | None = None) -> Path:
    """Répertoire du jeu synthétique (généré une fois par session, puis réutilisé en lecture seule)."""
    from tests.fixtures import dataset

    key = (fmt, manifest)
    if key not in _BUILT:
        _BUILT[key] = dataset.build(_FIXTURE_ROOT / f"{fmt}_{manifest or 'sans'}", fmt, manifest)
    return _BUILT[key]


def fresh_fixture(dest: Path, fmt: str = "xlsx", manifest: str | None = None) -> Path:
    """Copie modifiable du jeu synthétique (pour les tests qui altèrent des fichiers)."""
    from tests.fixtures import dataset

    return dataset.build(dest, fmt, manifest)


def copy_data(dst: Path, src: Path | None = None) -> Path:
    """Copie des fichiers de données dans un répertoire temporaire (jamais d'altération des originaux)."""
    for p in (src or CHALLENGE_DIR).iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
    return dst
