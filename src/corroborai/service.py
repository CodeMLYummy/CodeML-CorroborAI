"""Couche de service de l'interface : toute la logique testable, sans Streamlit.

L'application web (``app/streamlit_app.py``) n'est qu'une couche de
présentation au-dessus de ces fonctions.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from corroborai.engine import CorroborationResult, corroborate
from corroborai.feedback import FeedbackStore
from corroborai.io.loaders import ROW_COL, DataBundle, load_bundle
from corroborai.models import Evidence, Finding, Verdict
from corroborai.report import write_csv, write_report
from corroborai.rules_config import RulesConfig, load_rules, validate_against_data

DEFAULT_FEEDBACK = Path("feedback/retroaction.yaml")
ALLOWED_UPLOAD = re.compile(r"^[\w\-. ()'éèêàçÉ]+\.(xlsx|xlsm|csv|json|pdf|pptx)$", re.IGNORECASE)


class ServiceError(RuntimeError):
    """Erreur présentable à l'utilisateur."""


@dataclass
class PipelineRun:
    bundle: DataBundle
    cfg: RulesConfig
    result: CorroborationResult
    store: FeedbackStore | None
    params: dict[str, Any]


def stage_uploads(files: list[tuple[str, bytes]], dest: str | Path | None = None) -> Path:
    """Copie des fichiers téléversés dans un répertoire de travail temporaire.

    Les originaux ne sont jamais touchés. Les fichiers cachés (``.gitkeep``,
    ``.DS_Store``, ``._*`` de macOS) sont ignorés sans être écrits ; tout autre
    nom doit être un nom de fichier simple (sans chemin) avec une extension
    attendue, sinon le téléversement est refusé.
    """
    if not files:
        raise ServiceError("Aucun fichier téléversé.")
    dest = Path(dest) if dest else Path(tempfile.mkdtemp(prefix="corroborai_"))
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files:
        base = Path(name).name
        if base != name:
            raise ServiceError(f"Nom de fichier refusé : {name!r}")
        if base.startswith("."):
            continue
        if not ALLOWED_UPLOAD.match(base):
            raise ServiceError(f"Nom de fichier refusé : {name!r}")
        (dest / base).write_bytes(data)
    return dest


def run_pipeline(data_dir: str | Path, overrides: dict[str, str] | None = None,
                 feedback_path: str | Path | None = None, ia_provider: str | None = None,
                 llm_config: Any = None, out_dir: str | Path | None = None,
                 transport: Any = None, strict_manifest: bool = False) -> PipelineRun:
    """Chargement, validation, corroboration, analyse, rétroaction et (option) couche IA."""
    try:
        bundle = load_bundle(data_dir, strict_manifest=strict_manifest)
    except Exception as exc:  # noqa: BLE001 — message utilisateur
        raise ServiceError(f"Chargement impossible : {exc}") from exc
    cfg = load_rules()
    check = validate_against_data(cfg, bundle)
    if not check.ok:
        raise ServiceError("Configuration incohérente avec les données : " + "; ".join(check.errors[:5]))
    store = FeedbackStore.load(feedback_path, set(cfg.targets)) if feedback_path else None
    result = corroborate(bundle, cfg, overrides or {}, feedback=store)
    if ia_provider:
        from corroborai.ai.tasks import run_ai

        audit = Path(out_dir) / "audit_ia.jsonl" if out_dir else None
        result.ai = run_ai(result, bundle, llm_config, ia_provider, audit_path=audit, transport=transport)
    params = {"data_dir": str(data_dir), "overrides": dict(overrides or {}),
              "feedback_path": str(feedback_path) if feedback_path else None, "ia_provider": ia_provider}
    return PipelineRun(bundle, cfg, result, store, params)


def rerun(run: PipelineRun, **changes: Any) -> PipelineRun:
    params = {**run.params, **changes}
    return run_pipeline(params["data_dir"], params["overrides"], params["feedback_path"], params["ia_provider"])


# --------------------------------------------------------------------------- tableaux

TABLE_COLUMNS = {
    "priority": "Priorité", "person_id": "Employé", "assignment_key": "Affectation",
    "target_field": "Champ", "verdict": "Verdict", "subcategory": "Sous-catégorie",
    "expected": "Valeur attendue", "target_raw": "Valeur cible", "probable_cause": "Cause probable",
    "decision_source": "Décision", "rule_id": "Règle", "finding_id": "Identifiant",
}


def findings_frame(findings: list[Finding], verdicts: list[str] | None = None,
                   fields: list[str] | None = None, persons: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for f in findings:
        if verdicts and f.verdict.value not in verdicts:
            continue
        if fields and f.target_field not in fields:
            continue
        if persons and f.person_id not in persons:
            continue
        rec = f.to_record()
        rows.append({label: rec.get(key) for key, label in TABLE_COLUMNS.items()})
    df = pd.DataFrame(rows, columns=list(TABLE_COLUMNS.values()))
    if not df.empty:
        df = df.sort_values(["Priorité", "Employé", "Champ"], ascending=[False, True, True],
                            na_position="last", kind="stable").reset_index(drop=True)
    return df


def investigate(findings: list[Finding]) -> list[Finding]:
    return sorted((f for f in findings if f.verdict in (Verdict.ANOMALIE, Verdict.INDETERMINE)),
                  key=lambda f: (-(f.priority or 0), f.person_id, f.target_field))


def finding_label(f: Finding) -> str:
    prio = f"{f.priority:g}" if f.priority is not None else "—"
    return f"[{prio}] {f.person_id} · {f.target_field} · {f.verdict.value}"


def evidence_rows(bundle: DataBundle, finding: Finding) -> list[tuple[Evidence, dict[str, Any]]]:
    """Ligne d'origine complète de chaque preuve : les données ayant servi à la décision."""
    tables = {"source": bundle.source.df, "target": bundle.target.df,
              "poste_detail": bundle.poste_detail.df, "motifs": bundle.motifs.df}
    out = []
    for ev in finding.evidence:
        df = tables.get(ev.table)
        row: dict[str, Any] = {}
        if df is not None and ev.row is not None:
            hit = df[df[ROW_COL] == ev.row]
            if not hit.empty:
                row = {k: v for k, v in hit.iloc[0].to_dict().items() if v is not None}
        out.append((ev, row))
    return out


TABLE_LABELS = {"source": "Système A – RH (source)", "target": "Système B – Temps (cible)",
                "poste_detail": "Détail du poste", "motifs": "Motifs de situation"}


def parse_mapping_text(text: str) -> dict[str, str]:
    """« valeur attendue => valeur cible », une paire par ligne."""
    mapping = {}
    for i, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        if "=>" not in line:
            raise ServiceError(f"Ligne {i} : format attendu « valeur attendue => valeur cible »")
        a, b = (x.strip() for x in line.split("=>", 1))
        if not a or not b:
            raise ServiceError(f"Ligne {i} : valeur vide")
        mapping[a] = b
    if not mapping:
        raise ServiceError("Table de recodage vide.")
    return mapping


def export_files(run: PipelineRun, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="corroborai_out_"))
    return (write_report(run.result, out / "rapport_corroboration.xlsx"),
            write_csv(run.result, out / "verdicts.csv"))
