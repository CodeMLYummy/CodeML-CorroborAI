"""Socle commun des règles : contexte, résultat, décision et preuves.

Une règle calcule une **valeur attendue** à partir de la source (et des tables
de jointure) ; la décision est ensuite uniforme :

* valeur cible = valeur source (après normalisation)  → ``CONFORME``
* valeur cible ≠ source mais = valeur attendue         → ``JUSTIFIE``
* valeur cible ≠ valeur attendue                       → ``ANOMALIE``
* valeur attendue incalculable / valeur illisible      → ``INDETERMINE``
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from corroborai.io.loaders import ROW_COL, DataBundle
from corroborai.io.normalize import (
    FieldKind,
    NormalizationError,
    excel_serial_to_date,
    norm_code,
    norm_text,
    normalize,
)
from corroborai.models import Confidence, Evidence, Verdict
from corroborai.rules_config import FieldRule, RulesConfig


class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


UNKNOWN = _Sentinel("INCONNU")          # valeur attendue incalculable
NO_DIRECT = _Sentinel("SANS_EQUIVALENT")  # pas de valeur source directement comparable


# --------------------------------------------------------------------------- références

@dataclass(frozen=True)
class PosteRecord:
    effective: date
    unit: str | None
    row: int
    values: dict[str, Any]


def parse_effective_date(raw: Any, excel_serial: bool) -> date | None:
    """Date d'effet : numéro de série Excel (extraction d'origine) ou date texte (export CSV)."""
    text = "" if raw is None else str(raw).strip()
    try:
        if excel_serial and re.fullmatch(r"\d+(?:\.0+)?", text):
            return excel_serial_to_date(text)
        return normalize(raw, FieldKind.DATE)
    except NormalizationError:
        return None


@dataclass
class Refs:
    """Index des tables de jointure, construits une seule fois."""

    poste_history: dict[str, list[PosteRecord]]
    motifs: dict[str, dict[str, Any]]

    @classmethod
    def build(cls, bundle: DataBundle, cfg: RulesConfig) -> Refs:
        pj = cfg.joins["poste_detail"]
        history: dict[str, list[PosteRecord]] = defaultdict(list)
        for rec in bundle.poste_detail.df.to_dict("records"):
            poste = norm_code(rec.get(pj.col("poste").table))
            eff = parse_effective_date(rec.get(pj.col("date_effet").table), pj.col("date_effet").excel_serial)
            if poste is None or eff is None:
                continue
            history[poste].append(PosteRecord(eff, norm_code(rec.get(pj.col("unite_admin").table)),
                                              rec[ROW_COL], rec))
        for recs in history.values():
            recs.sort(key=lambda r: (r.effective, r.row))

        mj = cfg.joins["motifs"]
        motifs = {}
        for rec in bundle.motifs.df.to_dict("records"):
            key = norm_code(rec.get(mj.col("situation").table))
            if key is not None:
                motifs[key] = rec
        return cls(dict(history), motifs)


# --------------------------------------------------------------------------- contexte / résultat

@dataclass
class RuleContext:
    cfg: RulesConfig
    field: FieldRule
    src: dict[str, Any]
    tgt: dict[str, Any]
    refs: Refs
    overrides: dict[str, str] = field(default_factory=dict)

    @property
    def target_raw(self) -> Any:
        return self.tgt.get(self.field.target)

    def choice(self, interpretation_id: str) -> str:
        if interpretation_id in self.overrides:
            return self.overrides[interpretation_id]
        return self.cfg.interpretations[interpretation_id].choice

    def params(self, interpretation_id: str) -> dict[str, Any]:
        return self.cfg.interpretations[interpretation_id].params


@dataclass
class Outcome:
    verdict: Verdict
    expected: Any
    target_norm: Any
    justification: str
    confidence: Confidence = Confidence.ELEVEE
    subcategory: str | None = None
    evidence: list[Evidence] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    source_value: Any = None   # valeur source affichée dans le rapport


# --------------------------------------------------------------------------- présentation

def show(v: Any) -> str:
    if v is None:
        return "(vide)"
    if v is UNKNOWN:
        return "(incalculable)"
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "vrai" if v else "faux"
    return f"« {v} »"


def normalization_note(raw: Any, kind: FieldKind) -> str | None:
    """Signale une normalisation non triviale du texte cible (ex. réparation d'encodage)."""
    if kind not in (FieldKind.TEXT, FieldKind.TEXT_LOOSE) or raw is None:
        return None
    fixed = norm_text(raw)
    if fixed is not None and " ".join(str(raw).split()) != fixed:
        return f"Encodage de la cible réparé avant comparaison ({show(str(raw))} → {show(fixed)})."
    return None


# --------------------------------------------------------------------------- décision

def norm_target(ctx: RuleContext, kind: FieldKind | None = None) -> tuple[Any, Outcome | None]:
    """Normalise la valeur cible ; retourne un Outcome INDETERMINE si illisible."""
    kind = kind or ctx.field.kind
    try:
        return normalize(ctx.target_raw, kind), None
    except NormalizationError as exc:
        return None, Outcome(Verdict.INDETERMINE, UNKNOWN, None,
                             f"Valeur cible illisible pour le type « {kind.value} » : {exc}",
                             Confidence.FAIBLE, subcategory="valeur cible illisible")


def decide(ctx: RuleContext, *, expected: Any, source_direct: Any, rule_desc: str,
           kind: FieldKind | None = None, confidence: Confidence = Confidence.ELEVEE,
           unknown_reason: str = "", evidence: list[Evidence] | None = None,
           params: dict[str, Any] | None = None, source_value: Any = None) -> Outcome:
    kind = kind or ctx.field.kind
    target, err = norm_target(ctx, kind)
    if err:
        err.evidence = list(evidence or [])
        err.source_value = source_value
        return err
    notes = [n for n in (normalization_note(ctx.target_raw, kind),) if n]
    common = dict(evidence=list(evidence or []), params=dict(params or {}), source_value=source_value)

    if expected is UNKNOWN:
        return Outcome(Verdict.INDETERMINE, UNKNOWN, target,
                       " ".join([unknown_reason or "Valeur attendue incalculable.", *notes]),
                       Confidence.FAIBLE, subcategory="règle non applicable", **common)

    if target == expected:
        if source_direct is not NO_DIRECT and target == source_direct:
            text = f"Valeur identique à la source après normalisation de format : {show(target)}."
            return Outcome(Verdict.CONFORME, expected, target, " ".join([text, *notes]),
                           confidence, **common)
        src_txt = "" if source_direct is NO_DIRECT else f"Source {show(source_direct)} ; "
        text = f"{src_txt}valeur cible {show(target)} conforme à la règle : {rule_desc}."
        return Outcome(Verdict.JUSTIFIE, expected, target, " ".join([text, *notes]), confidence, **common)

    text = (f"Valeur attendue {show(expected)} selon la règle ({rule_desc}), "
            f"valeur cible {show(target)}.")
    return Outcome(Verdict.ANOMALIE, expected, target, " ".join([text, *notes]), confidence, **common)


def source_norm(ctx: RuleContext, column: str, kind: FieldKind | None = None) -> tuple[Any, str | None]:
    """Normalise une colonne source ; retourne (valeur, erreur)."""
    try:
        return normalize(ctx.src.get(column), kind or ctx.field.kind), None
    except NormalizationError as exc:
        return UNKNOWN, f"Valeur source « {column} » illisible : {exc}"


# --------------------------------------------------------------------------- preuves

def ev_source(ctx: RuleContext, columns: tuple[str, ...] | list[str]) -> Evidence:
    return Evidence(f"src:{ctx.src[ROW_COL]}", "source", ctx.src[ROW_COL],
                    {c: ctx.src.get(c) for c in columns})


def ev_target(ctx: RuleContext, columns: tuple[str, ...] | list[str]) -> Evidence:
    return Evidence(f"tgt:{ctx.tgt[ROW_COL]}", "target", ctx.tgt[ROW_COL],
                    {c: ctx.tgt.get(c) for c in columns})


def ev_poste(rec: PosteRecord, note: str = "") -> Evidence:
    return Evidence(f"poste:{rec.row}", "poste_detail", rec.row,
                    {"date_effet": rec.effective.isoformat(), "unite_admin": rec.unit}, note)


def ev_motif(rec: dict[str, Any], note: str = "") -> Evidence:
    return Evidence(f"motif:{rec[ROW_COL]}", "motifs", rec[ROW_COL],
                    {k: v for k, v in rec.items() if k != ROW_COL}, note)
