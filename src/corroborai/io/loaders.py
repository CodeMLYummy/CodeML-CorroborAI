"""Chargement des jeux de données en lecture seule.

Deux garanties distinctes :

1. **Les fichiers d'origine ne sont jamais modifiés** (exigence des consignes).
   Chaque fichier est lu une seule fois en octets ; son empreinte SHA-256 est
   calculée sur exactement les octets analysés, puis recalculée en fin de
   traitement (:meth:`DataBundle.verify_unchanged`) : c'est la preuve que le
   traitement n'a rien altéré. Aucune écriture n'est faite dans le répertoire
   de données.

2. **Les fichiers sont ceux qui ont été distribués** — vérification
   *informative* : si un ``manifest.json`` est présent, chaque empreinte y est
   comparée. Un écart (ex. un jeu de test plus volumineux portant les mêmes
   noms) est signalé, sans bloquer. Le mode ``strict_manifest`` l'exige.

Formats acceptés : Excel (``.xlsx``, ``.xlsm``) et CSV (séparateur et
encodage détectés). Un fichier déclaré ``X.xlsx`` est aussi trouvé sous le nom
``X.csv``. Les noms sont résolus de façon tolérante (accents, espaces,
apostrophes, forme Unicode NFC/NFD).

Chaque ligne chargée porte ``_row`` (numéro de ligne d'origine, en-tête = 1)
pour la traçabilité des preuves.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from corroborai.io.normalize import strip_accents

ROW_COL = "_row"
# Configuration : variable d'environnement CORROBORAI_CONFIG_DIR, sinon <dépôt>/config
CONFIG_DIR = Path(os.environ.get("CORROBORAI_CONFIG_DIR",
                                 Path(__file__).resolve().parents[3] / "config"))
DEFAULT_CONFIG = CONFIG_DIR / "datasets.yaml"
SUPPORTED_SUFFIXES = (".xlsx", ".xlsm", ".csv")
REQUIRED_TABLES = ("source", "target", "poste_detail", "motifs")


class DataLoadError(RuntimeError):
    """Un jeu de données requis est absent, illisible ou mal structuré."""


class FileStatus(str, Enum):
    CONFORME_MANIFESTE = "CONFORME_AU_MANIFESTE"
    DIFFERENT_MANIFESTE = "DIFFERENT_DU_MANIFESTE"   # informatif (ex. jeu de test plus volumineux)
    SANS_MANIFESTE = "NON_VERIFIE_SANS_MANIFESTE"
    ABSENT = "ABSENT"
    INCHANGE = "INCHANGE"                            # vérification après traitement
    MODIFIE = "MODIFIE"


PRESENT = {FileStatus.CONFORME_MANIFESTE, FileStatus.DIFFERENT_MANIFESTE, FileStatus.SANS_MANIFESTE,
           FileStatus.INCHANGE}


@dataclass(frozen=True)
class FileCheck:
    logical_name: str
    path: Path | None
    sha256: str | None
    manifest_sha256: str | None
    status: FileStatus
    required: bool
    note: str = ""

    @property
    def present(self) -> bool:
        return self.status in PRESENT


@dataclass
class IntegrityReport:
    checks: list[FileCheck] = field(default_factory=list)
    strict: bool = False
    manifest_found: bool = False
    notes: list[str] = field(default_factory=list)

    def _failed(self, c: FileCheck) -> bool:
        if not c.required:
            return False
        if not c.present:
            return True
        return self.strict and c.status in (FileStatus.DIFFERENT_MANIFESTE, FileStatus.SANS_MANIFESTE)

    @property
    def ok(self) -> bool:
        return not any(self._failed(c) for c in self.checks)

    def failures(self) -> list[FileCheck]:
        return [c for c in self.checks if self._failed(c)]

    def warnings(self) -> list[FileCheck]:
        return [c for c in self.checks if not self._failed(c)
                and c.status in (FileStatus.DIFFERENT_MANIFESTE, FileStatus.SANS_MANIFESTE, FileStatus.ABSENT)]

    def to_records(self) -> list[dict[str, Any]]:
        return [{
            "fichier_logique": c.logical_name,
            "fichier_lu": c.path.name if c.path else "",
            "requis": c.required,
            "statut": c.status.value,
            "sha256": c.sha256 or "",
            "sha256_manifeste": c.manifest_sha256 or "",
            "note": c.note,
        } for c in self.checks]


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
    read_hashes: dict[str, tuple[Path, str]] = field(default_factory=dict)

    def verify_unchanged(self) -> IntegrityReport:
        """Recalcule l'empreinte de chaque fichier lu et la compare à celle
        calculée à la lecture : preuve que le traitement n'a rien modifié."""
        report = IntegrityReport()
        for logical, (path, sha) in self.read_hashes.items():
            if not path.is_file():
                report.checks.append(FileCheck(logical, path, None, sha, FileStatus.ABSENT, True,
                                               "fichier disparu pendant le traitement"))
                continue
            now = sha256_bytes(path.read_bytes())
            status = FileStatus.INCHANGE if now == sha else FileStatus.MODIFIE
            report.checks.append(FileCheck(logical, path, now, sha, status, True))
        return report


# --------------------------------------------------------------------------- utilitaires

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def name_key(name: str) -> str:
    """Clé de comparaison tolérante pour les noms de fichiers."""
    return re.sub(r"[^a-z0-9]", "", strip_accents(name).casefold())


def _stem_key(name: str) -> str:
    return name_key(Path(name).stem)


def _load_registry(config_path: Path | None) -> dict[str, dict[str, Any]]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read_manifest(data_dir: Path, notes: list[str]) -> dict[str, str] | None:
    """{clé de nom (sans extension): sha256} ; None si absent ou illisible (avec note)."""
    path = data_dir / "manifest.json"
    if not path.is_file():
        return None
    try:
        files = json.loads(path.read_bytes().decode("utf-8"))["files"]
        return {_stem_key(e["path"]): e["sha256"] for e in files if e.get("path") and e.get("sha256")}
    except (ValueError, KeyError, TypeError) as exc:
        notes.append(f"manifest.json illisible, ignoré ({exc.__class__.__name__})")
        return None


def _candidates(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED_SUFFIXES)


def _resolve(data_dir: Path, wanted: str) -> tuple[Path | None, str]:
    """Retourne (chemin, mode de résolution). Ordre : nom exact, autre extension, nom tolérant."""
    exact = data_dir / wanted
    if exact.is_file():
        return exact, ""
    files = _candidates(data_dir)
    stem = Path(wanted).stem
    for p in files:
        if p.stem == stem:
            return p, f"trouvé au format {p.suffix.lower()}"
    key = _stem_key(wanted)
    for p in files:
        if _stem_key(p.name) == key:
            return p, f"trouvé sous le nom « {p.name} »"
    return None, ""


# --------------------------------------------------------------------------- lecture

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    """CSV avec détection de l'encodage (UTF-8, CP1252) et du séparateur (; , tabulation)."""
    text = _decode(data)
    first = next((line for line in text.splitlines() if line.strip()), "")
    try:
        sep = csv.Sniffer().sniff(first, delimiters=";,\t").delimiter
    except csv.Error:
        sep = ","
    return pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, na_values=[""])


def _read_table(data: bytes, suffix: str, sheet: Any) -> pd.DataFrame | dict[str, pd.DataFrame]:
    if suffix == ".csv":
        df = read_csv_bytes(data)
        return {"Mapping": df} if sheet is None else df
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


def _check_columns(name: str, path: Path, df: pd.DataFrame, required: list[str]) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataLoadError(f"Colonnes requises absentes dans « {name} » ({path.name}) : {missing}")


def load_bundle(data_dir: str | Path, config_path: str | Path | None = None,
                strict_manifest: bool = False) -> DataBundle:
    """Charge et valide tous les jeux de données.

    Un fichier requis absent, illisible ou sans ses colonnes requises lève
    :class:`DataLoadError`. Le manifeste est informatif, sauf avec
    ``strict_manifest`` (tout fichier requis doit alors y être conforme).
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise DataLoadError(f"Répertoire de données introuvable : {data_dir}")
    config_path = Path(config_path) if config_path else DEFAULT_CONFIG
    registry = _load_registry(config_path)
    integrity = IntegrityReport(strict=strict_manifest)
    manifest = _read_manifest(data_dir, integrity.notes)
    integrity.manifest_found = manifest is not None

    tables: dict[str, LoadedTable] = {}
    mapping: dict[str, pd.DataFrame] = {}
    mapping_sha = ""
    read_hashes: dict[str, tuple[Path, str]] = {}
    missing: list[str] = []

    for logical, spec in registry.items():
        required = not spec.get("optional", False)
        path, how = _resolve(data_dir, spec["file"])
        if path is None:
            integrity.checks.append(FileCheck(logical, None, None, None, FileStatus.ABSENT, required,
                                              f"attendu : {spec['file']} (ou .csv)"))
            if required:
                missing.append(f"{logical} ({spec['file']})")
            continue
        data = path.read_bytes()
        sha = sha256_bytes(data)
        read_hashes[logical] = (path, sha)
        expected = manifest.get(_stem_key(spec["file"])) if manifest is not None else None
        if manifest is None:
            status = FileStatus.SANS_MANIFESTE
        elif expected is None:
            status, how = FileStatus.SANS_MANIFESTE, ", ".join(x for x in (how, "absent du manifeste") if x)
        else:
            status = FileStatus.CONFORME_MANIFESTE if sha == expected else FileStatus.DIFFERENT_MANIFESTE
        integrity.checks.append(FileCheck(logical, path, sha, expected, status, required, how))

        try:
            parsed = _read_table(data, path.suffix.lower(), spec.get("sheet", 0))
        except Exception as exc:  # noqa: BLE001 — message utilisateur explicite
            raise DataLoadError(f"Fichier illisible pour « {logical} » ({path.name}) : {exc}") from exc
        if logical == "mapping":
            mapping = {str(k): _clean(v) for k, v in parsed.items()}  # type: ignore[union-attr]
            mapping_sha = sha
            continue
        assert isinstance(parsed, pd.DataFrame)
        if spec.get("packed_csv"):
            parsed = unpack_packed_csv(parsed)
        df = _clean(parsed)
        _check_columns(logical, path, df, spec.get("required_columns", []))
        tables[logical] = LoadedTable(logical, path, sha, df)

    if missing:
        raise DataLoadError(f"Fichier(s) requis introuvable(s) dans {data_dir} : {', '.join(missing)}")
    for logical in REQUIRED_TABLES:
        if logical not in tables:
            raise DataLoadError(f"Jeu de données « {logical} » non déclaré dans la configuration")
    if strict_manifest and not integrity.ok:
        details = "; ".join(f"{c.logical_name}: {c.status.value}" for c in integrity.failures())
        raise DataLoadError(f"Contrôle du manifeste (mode strict) échoué — {details}")

    return DataBundle(
        source=tables["source"], target=tables["target"], poste_detail=tables["poste_detail"],
        motifs=tables["motifs"], mapping=mapping, mapping_sha256=mapping_sha, integrity=integrity,
        data_dir=data_dir, config_path=config_path, read_hashes=read_hashes,
    )
