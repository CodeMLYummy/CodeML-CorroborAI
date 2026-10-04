"""Chargement et validation de ``hypotheses.yaml`` et ``scoring.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from corroborai.io.loaders import CONFIG_DIR
from corroborai.models import Confidence, Verdict

DEFAULT_HYPOTHESES = CONFIG_DIR / "hypotheses.yaml"
DEFAULT_SCORING = CONFIG_DIR / "scoring.yaml"

KNOWN_HYPOTHESES = frozenset({
    "H-PERMUTATION", "H-HISTORY-RECORD", "H-ALT-SOURCE", "H-ALT-INTERPRETATION",
    "H-BIJECTION", "H-SYSTEMIC", "H-TYPE-ABSENT",
})
SCORED_VERDICTS = frozenset({Verdict.ANOMALIE.value, Verdict.INDETERMINE.value})


class AnalysisConfigError(ValueError):
    """Configuration d'analyse invalide."""


@dataclass(frozen=True)
class HypothesisSpec:
    id: str
    label: str
    description: str


@dataclass(frozen=True)
class HypothesesConfig:
    hypotheses: dict[str, HypothesisSpec]
    precedence: tuple[str, ...]
    systemic_min_share: float
    systemic_min_count: int
    bijection_min_count: int
    alt_source_min_support: float
    alt_source_min_count: int
    alt_source_excluded_columns: frozenset[str]
    path: Path | None = None


@dataclass(frozen=True)
class Modifier:
    factor: float
    reason: str


@dataclass(frozen=True)
class ScoringConfig:
    scale: float
    verdict_base: dict[str, float]
    confidence_weights: dict[str, float]
    modifiers: dict[str, Modifier]
    path: Path | None = None


def _read(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise AnalysisConfigError(f"{path.name} : document invalide ou version non supportée")
    return raw


def _num(value: Any, name: str, lo: float, hi: float, errors: list[str], integer: bool = False) -> Any:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and lo <= value <= hi
    if integer:
        ok = ok and isinstance(value, int)
    if not ok:
        errors.append(f"{name} invalide : {value!r} (attendu {'entier ' if integer else ''}dans [{lo}, {hi}])")
    return value


def load_hypotheses(path: str | Path | None = None) -> HypothesesConfig:
    path = Path(path) if path else DEFAULT_HYPOTHESES
    raw = _read(path)
    errors: list[str] = []
    specs = {}
    for hid, spec in (raw.get("hypotheses") or {}).items():
        spec = spec or {}
        if hid not in KNOWN_HYPOTHESES:
            errors.append(f"hypothèse inconnue (non implémentée) : {hid}")
        if not spec.get("label") or not spec.get("description"):
            errors.append(f"{hid} : label et description obligatoires")
        specs[hid] = HypothesisSpec(hid, str(spec.get("label", "")), " ".join(str(spec.get("description", "")).split()))
    missing = KNOWN_HYPOTHESES - set(specs)
    if missing:
        errors.append(f"hypothèses implémentées mais non documentées : {sorted(missing)}")
    precedence = tuple(raw.get("precedence") or ())
    if sorted(precedence) != sorted(specs) or len(set(precedence)) != len(precedence):
        errors.append("precedence doit lister chaque hypothèse exactement une fois")
    t = raw.get("thresholds") or {}
    cfg = HypothesesConfig(
        hypotheses=specs,
        precedence=precedence,
        systemic_min_share=_num(t.get("systemic_min_share"), "systemic_min_share", 0.5, 1, errors),
        systemic_min_count=_num(t.get("systemic_min_count"), "systemic_min_count", 2, 10**6, errors, True),
        bijection_min_count=_num(t.get("bijection_min_count"), "bijection_min_count", 2, 10**6, errors, True),
        alt_source_min_support=_num(t.get("alt_source_min_support"), "alt_source_min_support", 0.5, 1, errors),
        alt_source_min_count=_num(t.get("alt_source_min_count"), "alt_source_min_count", 2, 10**6, errors, True),
        alt_source_excluded_columns=frozenset(raw.get("alt_source_excluded_columns") or ()),
        path=path,
    )
    if errors:
        raise AnalysisConfigError("hypotheses.yaml invalide :\n  - " + "\n  - ".join(errors))
    return cfg


def load_scoring(path: str | Path | None = None, hypotheses: HypothesesConfig | None = None) -> ScoringConfig:
    path = Path(path) if path else DEFAULT_SCORING
    raw = _read(path)
    errors: list[str] = []
    vb = dict(raw.get("verdict_base") or {})
    if set(vb) != SCORED_VERDICTS:
        errors.append(f"verdict_base doit couvrir exactement {sorted(SCORED_VERDICTS)}")
    for k, v in vb.items():
        _num(v, f"verdict_base.{k}", 0.01, 1, errors)
    cw = dict(raw.get("confidence_weights") or {})
    if set(cw) != {c.value for c in Confidence}:
        errors.append("confidence_weights doit couvrir chaque niveau de confiance")
    for k, v in cw.items():
        _num(v, f"confidence_weights.{k}", 0.01, 1, errors)
    if cw and not (cw.get("ELEVEE", 0) >= cw.get("MOYENNE", 0) >= cw.get("FAIBLE", 0)):
        errors.append("confidence_weights doit être décroissant (ELEVEE ≥ MOYENNE ≥ FAIBLE)")
    mods = {}
    for hid, spec in (raw.get("modifiers") or {}).items():
        spec = spec or {}
        if hid not in KNOWN_HYPOTHESES:
            errors.append(f"modificateur pour une hypothèse inconnue : {hid}")
        _num(spec.get("factor"), f"modifiers.{hid}.factor", 0.01, 3, errors)
        if not spec.get("reason"):
            errors.append(f"modifiers.{hid} : justification (reason) obligatoire")
        mods[hid] = Modifier(float(spec.get("factor") or 0), str(spec.get("reason", "")))
    expected_ids = set(hypotheses.hypotheses) if hypotheses else KNOWN_HYPOTHESES
    if set(mods) != expected_ids:
        errors.append(f"modifiers doit couvrir exactement les hypothèses : {sorted(expected_ids ^ set(mods))}")
    scale = _num(raw.get("scale"), "scale", 1, 10**6, errors)
    if errors:
        raise AnalysisConfigError("scoring.yaml invalide :\n  - " + "\n  - ".join(errors))
    return ScoringConfig(float(scale), {k: float(v) for k, v in vb.items()},
                         {k: float(v) for k, v in cw.items()}, mods, path)
