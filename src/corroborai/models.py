"""Modèle de données central de CorroborAI.

Tout le pipeline (règles, hypothèses, scoring, IA, rapport) produit ou consomme
des ``Finding``. Le modèle encode structurellement les principes du projet :

* un verdict est toujours rattaché à une source de décision traçable ;
* **l'IA ne peut jamais être la source d'un verdict** (``__post_init__`` le refuse) ;
* un verdict ``JUSTIFIE`` exige une règle identifiée ;
* les preuves (``Evidence``) pointent vers une table et une ligne d'origine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Classification finale d'un champ corroboré."""

    CONFORME = "CONFORME"          # identique après normalisation de format
    JUSTIFIE = "JUSTIFIE"          # différent de la source, mais égal à la valeur attendue par une règle
    ANOMALIE = "ANOMALIE"          # différent de la valeur attendue
    INDETERMINE = "INDETERMINE"    # aucune règle ne permet de conclure


class DecisionSource(str, Enum):
    """Qui a produit le verdict. ``IA`` existe pour l'étiquetage, jamais pour un verdict."""

    REGLE = "REGLE_DETERMINISTE"
    EXPERT = "EXPERT"
    IA = "IA"


VERDICT_SOURCES = frozenset({DecisionSource.REGLE, DecisionSource.EXPERT})


class Confidence(str, Enum):
    """Confiance dans la *manière* dont le verdict a été obtenu."""

    ELEVEE = "ELEVEE"      # règle déterministe sans hypothèse d'interprétation
    MOYENNE = "MOYENNE"    # règle appliquée avec une interprétation paramétrée
    FAIBLE = "FAIBLE"      # repose sur une hypothèse

    @property
    def weight(self) -> float:
        return {"ELEVEE": 1.0, "MOYENNE": 0.7, "FAIBLE": 0.4}[self.value]


class ExplanationSource(str, Enum):
    GABARIT = "GABARIT"
    LLM = "LLM"
    EXPERT = "EXPERT"


class ModelError(ValueError):
    """Violation d'un invariant du modèle de données."""


@dataclass(frozen=True)
class Evidence:
    """Élément de preuve traçable vers une donnée d'origine.

    ``row`` est le numéro de ligne Excel (en-tête = ligne 1) dans ``table``.
    """

    id: str
    table: str
    row: int | None
    values: dict[str, Any]
    note: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ModelError("Evidence.id est obligatoire")
        if not self.table:
            raise ModelError("Evidence.table est obligatoire")


@dataclass
class HypothesisResult:
    """Résultat d'une hypothèse du moteur d'hypothèses (déterministe)."""

    hypothesis_id: str
    description: str
    verified: bool
    evidence_ids: tuple[str, ...] = ()
    systemic: bool = False


@dataclass
class Finding:
    """Résultat de corroboration d'un champ cible pour une affectation."""

    person_id: str
    assignment_key: str
    target_field: str
    source_fields: tuple[str, ...]

    source_raw: Any
    target_raw: Any
    expected: Any
    target_norm: Any

    verdict: Verdict
    decision_source: DecisionSource
    rule_id: str | None
    justification: str

    rule_params: dict[str, Any] = field(default_factory=dict)
    rule_ref: str | None = None            # référence dans Mapping.xlsx
    subcategory: str | None = None
    confidence: Confidence = Confidence.ELEVEE
    evidence: list[Evidence] = field(default_factory=list)
    hypotheses: list[HypothesisResult] = field(default_factory=list)

    criticality: int | None = None
    priority: float | None = None
    explanation: str | None = None
    explanation_source: ExplanationSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, Verdict):
            self.verdict = Verdict(self.verdict)
        if not isinstance(self.decision_source, DecisionSource):
            self.decision_source = DecisionSource(self.decision_source)
        if not isinstance(self.confidence, Confidence):
            self.confidence = Confidence(self.confidence)
        if self.decision_source not in VERDICT_SOURCES:
            raise ModelError(
                f"Source de décision interdite pour un verdict : {self.decision_source.value}. "
                "L'IA peut enrichir un Finding, jamais en fixer le verdict."
            )
        if self.verdict is Verdict.JUSTIFIE and not self.rule_id:
            raise ModelError("Un verdict JUSTIFIE doit référencer une règle (rule_id).")
        if not self.justification:
            raise ModelError("Un verdict sans justification n'est pas acceptable.")
        ids = [e.id for e in self.evidence]
        if len(ids) != len(set(ids)):
            raise ModelError("Identifiants de preuve dupliqués dans un Finding.")
        for h in self.hypotheses:
            missing = set(h.evidence_ids) - set(ids)
            if missing:
                raise ModelError(
                    f"L'hypothèse {h.hypothesis_id} cite des preuves absentes : {sorted(missing)}"
                )

    @property
    def finding_id(self) -> str:
        return f"{self.person_id}:{self.assignment_key}:{self.target_field}"

    def add_evidence(self, ev: Evidence) -> None:
        if any(e.id == ev.id for e in self.evidence):
            raise ModelError(f"Preuve déjà présente : {ev.id}")
        self.evidence.append(ev)

    def to_record(self) -> dict[str, Any]:
        """Représentation plate, adaptée à un export CSV/Excel."""
        return {
            "finding_id": self.finding_id,
            "person_id": self.person_id,
            "assignment_key": self.assignment_key,
            "target_field": self.target_field,
            "source_fields": ", ".join(self.source_fields),
            "source_raw": _fmt(self.source_raw),
            "target_raw": _fmt(self.target_raw),
            "expected": _fmt(self.expected),
            "verdict": self.verdict.value,
            "subcategory": self.subcategory or "",
            "decision_source": self.decision_source.value,
            "rule_id": self.rule_id or "",
            "rule_ref": self.rule_ref or "",
            "rule_params": ", ".join(f"{k}={v}" for k, v in sorted(self.rule_params.items())),
            "confidence": self.confidence.value,
            "criticality": self.criticality,
            "priority": self.priority,
            "justification": self.justification,
            "hypotheses": " | ".join(
                f"{h.hypothesis_id}{'*' if h.systemic else ''}" for h in self.hypotheses if h.verified
            ),
            "evidence": " | ".join(f"{e.id}@{e.table}:{e.row}" for e in self.evidence),
            "explanation": self.explanation or "",
            "explanation_source": self.explanation_source.value if self.explanation_source else "",
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["finding_id"] = self.finding_id
        for k in ("verdict", "decision_source", "confidence", "explanation_source"):
            if d[k] is not None:
                d[k] = d[k].value if isinstance(d[k], Enum) else d[k]
        return d


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)
