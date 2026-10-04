"""Chargement des jeux de données en lecture seule, avec contrôle d'intégrité.

Garanties :

* chaque fichier est lu **une seule fois en octets** ; l'empreinte SHA-256 est
  calculée sur exactement les octets qui sont ensuite analysés ;
* aucune écriture n'est jamais faite dans le répertoire de données ;
* l'empreinte est comparée à ``manifest.json`` ; un fichier requis altéré
  interrompt le traitement (mode strict) ;
* les noms de fichiers sont résolus de façon tolérante (accents, espaces,
  apostrophes, forme Unicode NFC/NFD) car les copies distribuées peuvent avoir
  été renommées — l'empreinte reste l'autorité ;
* chaque ligne chargée porte ``_row`` (numéro de ligne Excel d'origine) pour
  la traçabilité des preuves.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import os

import pandas as pd
import yaml

from corroborai.io.normalize import strip_accents

ROW_COL = "_row"
# Configuration : variable d'environnement CORROBORAI_CONFIG_DIR, sinon <dépôt>/config
CONFIG_DIR = Path(os.environ.get("CORROBORAI_CONFIG_DIR",
                                 Path(__file__).resolve().parents[3] / "config"))
DEFAULT_CONFIG = CONFIG_DIR / "datasets.yaml"


class DataLoadError(RuntimeError):
    """Un jeu de données requis est absent, altéré ou mal structuré."""


class FileStatus(str, Enum):
    OK = "OK"
    OK_RENAMED = "OK_RENOMME"          # contenu identique, nom différent
    HASH_MISMATCH = "EMPREINTE_DIFFERENTE"
    MISSING = "ABSENT"
    NOT_IN_MANIFEST = "HORS_MANIFEST"


@dataclass(frozen=True)
class FileCheck:
    logical_name: str | None
    manifest_path: str | None
    resolved_path: Path | None
    expected_sha256: str | None
    actual_sha256: str | None
    status: FileStatus
    required: bool

    @property
    def ok(self) -> bool:
        return self.status in (FileStatus.OK, FileStatus.OK_RENAMED)


@dataclass
class IntegrityReport:
    checks: list[FileCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    def failures(self) -> list[FileCheck]:
        return [c for c in self.checks if c.required and not c.ok]

    def warnings(self) -> list[FileCheck]:
        return [c for c in self.checks if not c.required and not c.ok]

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "fichier_logique": c.logical_name or "",
                "manifest": c.manifest_path or "",
                "fichier_lu": c.resolved_path.name if c.resolved_path else "",
                "sha256_attendu": c.expected_sha256 or "",
                "sha256_calcule": c.actual_sha256 or "",
                "statut": c.status.value,
                "requis": c.required,
            }
            for c in self.checks
        ]


@dataclass(frozen=True)
class LoadedTable:
    name: str
    path: Path
    sha256: str
    df: pd.DataFrame


@dataclass
class DataBundle:
    source: LoadedTable
    target: LoadedTable
    poste_detail: LoadedTable
    motifs: LoadedTable
    mapping: dict[str, pd.DataFrame]
    mapping_sha256: str
    integrity: IntegrityReport
    data_dir: Path
    config_path: Path

    def verify_unchanged(self) -> IntegrityReport:
        """Recalcule les empreintes ; à appeler en fin de traitement pour
        démontrer que les fichiers d'origine n'ont pas été modifiés."""
        return verify_manifest(self.data_dir, _load_registry(self.config_path))


# --------------------------------------------------------------------------- utilitaires

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def name_key(name: str) -> str:
    """Clé de comparaison tolérante pour les noms de fichiers."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(name).casefold())


def _load_registry(config_path: Path | None) -> dict[str, dict[str, Any]]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read_manifest(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / "manifest.json"
    if not path.is_file():
        raise DataLoadError(f"manifest.json introuvable dans {data_dir}")
    return json.loads(path.read_bytes().decode("utf-8"))["files"]


def _resolve(data_dir: Path, wanted: str, sha: str | None) -> tuple[Path | None, bool]:
    """Retourne (chemin, renommé?). Ordre : nom exact, nom tolérant, empreinte."""
    exact = data_dir / wanted
    if exact.is_file():
        return exact, False
    candidates = [p for p in data_dir.iterdir() if p.is_file()]
    key = name_key(wanted)
    for p in candidates:
        if name_key(p.name) == key:
            return p, True
    if sha:
        for p in candidates:
            if p.suffix.lower() == Path(wanted).suffix.lower() and sha256_bytes(p.read_bytes()) == sha:
                return p, True
    return None, False


# --------------------------------------------------------------------------- intégrité

def verify_manifest(data_dir: Path, registry: dict[str, dict[str, Any]]) -> IntegrityReport:
    data_dir = Path(data_dir)
    manifest = _read_manifest(data_dir)
    by_key = {name_key(e["path"]): e for e in manifest}
    required = {name_key(spec["file"]): logical for logical, spec in registry.items()}

    report = IntegrityReport()
    for key, entry in by_key.items():
        logical = required.get(key)
        path, renamed = _resolve(data_dir, entry["path"], entry.get("sha256"))
        if path is None:
            status, actual = FileStatus.MISSING, None
        else:
            actual = sha256_bytes(path.read_bytes())
            if actual != entry.get("sha256"):
                status = FileStatus.HASH_MISMATCH
            else:
                status = FileStatus.OK_RENAMED if renamed else FileStatus.OK
        report.checks.append(
            FileCheck(logical, entry["path"], path, entry.get("sha256"), actual, status, logical is not None)
        )
    for key, logical in required.items():
        if key not in by_key:
            report.checks.append(
                FileCheck(logical, None, None, None, None, FileStatus.NOT_IN_MANIFEST, True)
            )
    return report


# --------------------------------------------------------------------------- lecture

def _read_excel_bytes(data: bytes, sheet: Any) -> pd.DataFrame | dict[str, pd.DataFrame]:
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet, dtype=str)


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Uniformise les vides en ``None`` et ajoute le numéro de ligne d'origine."""
    df = df.astype(object)
    df = df.where(df.notna(), None)
    df.columns = [str(c).strip() for c in df.columns]
    df.insert(0, ROW_COL, range(2, len(df) + 2))  # ligne 1 = en-tête
    return df.reset_index(drop=True)


def unpack_packed_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Déplie un CSV « tassé » dans une seule colonne Excel.

    Sans effet si le tableau a déjà plusieurs colonnes.
    """
    if df.shape[1] != 1 or "," not in str(df.columns[0]):
        return df
    header = str(df.columns[0])
    lines = [header] + ["" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
                        for v in df.iloc[:, 0].tolist()]
    return pd.read_csv(io.StringIO("\n".join(lines)), dtype=str, keep_default_na=False,
                       na_values=[""])


def _check_columns(name: str, df: pd.DataFrame, required: list[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataLoadError(f"Colonnes requises absentes dans « {name} » : {missing}")


def load_bundle(data_dir: str | Path, config_path: str | Path | None = None,
                strict: bool = True) -> DataBundle:
    """Charge et valide tous les jeux de données.

    En mode strict, un fichier requis absent ou dont l'empreinte diffère du
    manifest lève :class:`DataLoadError`.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataLoadError(f"Répertoire de données introuvable : {data_dir}")
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG
    registry = _load_registry(config_path)
    integrity = verify_manifest(data_dir, registry)
    if strict and not integrity.ok:
        details = "; ".join(f"{c.logical_name}: {c.status.value}" for c in integrity.failures())
        raise DataLoadError(f"Contrôle d'intégrité échoué — {details}")

    paths = {c.logical_name: c.resolved_path for c in integrity.checks if c.logical_name}
    tables: dict[str, LoadedTable] = {}
    mapping: dict[str, pd.DataFrame] = {}
    mapping_sha = ""

    for logical, spec in registry.items():
        path = paths.get(logical)
        if path is None:
            path, _ = _resolve(data_dir, spec["file"], None)
        if path is None:
            raise DataLoadError(f"Fichier introuvable pour « {logical} » : {spec['file']}")
        data = path.read_bytes()
        sha = sha256_bytes(data)
        parsed = _read_excel_bytes(data, spec.get("sheet", 0))

        if logical == "mapping":
            mapping = {str(k): _clean(v) for k, v in parsed.items()}  # type: ignore[union-attr]
            mapping_sha = sha
            continue

        assert isinstance(parsed, pd.DataFrame)
        if spec.get("packed_csv"):
            parsed = unpack_packed_csv(parsed)
        df = _clean(parsed)
        _check_columns(logical, df, spec.get("required_columns", []))
        tables[logical] = LoadedTable(logical, path, sha, df)

    for logical in ("source", "target", "poste_detail", "motifs"):
        if logical not in tables:
            raise DataLoadError(f"Jeu de données « {logical} » non déclaré dans la configuration")

    return DataBundle(
        source=tables["source"],
        target=tables["target"],
        poste_detail=tables["poste_detail"],
        motifs=tables["motifs"],
        mapping=mapping,
        mapping_sha256=mapping_sha,
        integrity=integrity,
        data_dir=data_dir,
        config_path=config_path,
    )
