"""Rétroaction experte : corrections de verdict et règles expertes.

Deux formes de retour, conservées dans un fichier YAML lisible et versionnable
(jamais dans le répertoire des données) :

* **Correction** — l'expert fixe le verdict d'un écart précis. Elle porte une
  empreinte des valeurs (attendue, cible) : si les données changent, la
  correction est déclarée *périmée* et n'est plus appliquée.
* **Règle experte** — généralise une décision à tous les écarts concernés.
  Elle s'exprime dans un **mini-langage fermé** de quatre opérations, qui ne
  peuvent que reconnaître un écart comme acceptable (ANOMALIE → JUSTIFIE) :

  ``accept_alternative_source``  la cible provient d'une autre colonne
  ``accept_value_mapping``       table de recodage valeur attendue → valeur cible
  ``accept_hypothesis``          écarts expliqués par une hypothèse vérifiée
  ``accept_subcategory``         écarts d'une sous-catégorie donnée

Avant d'être acceptée, une règle est exécutée à blanc : l'aperçu montre
exactement quels verdicts changeraient.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from corroborai.analysis.config import KNOWN_HYPOTHESES
from corroborai.io.normalize import NormalizationError, normalize
from corroborai.models import Confidence, DecisionSource, Finding, Verdict

OPERATIONS: dict[str, dict[str, Any]] = {
    "accept_alternative_source": {
        "description": "La valeur cible provient d'une autre colonne : un écart est accepté si la cible "
                       "est égale à cette colonne.",
        "params": {"column": "nom de colonne candidate (voir la liste fournie pour le champ)"},
    },
    "accept_value_mapping": {
        "description": "Table de recodage : un écart est accepté si (valeur attendue → valeur cible) "
                       "figure dans la table.",
        "params": {"mapping": "objet {valeur attendue: valeur cible}"},
    },
    "accept_hypothesis": {
        "description": "Les écarts du champ expliqués par cette hypothèse vérifiée sont acceptés.",
        "params": {"hypothesis": "identifiant d'hypothèse (H-…)"},
    },
    "accept_subcategory": {
        "description": "Les écarts du champ de cette sous-catégorie sont acceptés.",
        "params": {"subcategory": "sous-catégorie exacte"},
    },
}
CORRECTION_RULE_ID = "EXPERT-CORRECTION"


class FeedbackError(ValueError):
    """Rétroaction invalide."""


# --------------------------------------------------------------------------- modèle

@dataclass
class ExpertRule:
    id: str
    operation: str
    field: str
    params: dict[str, Any]
    reason: str
    author: str = ""
    created: str = ""
    provenance: str = "FORMULAIRE"      # FORMULAIRE | SUGGESTION | IA:<fournisseur>
    original_text: str = ""
    active: bool = True

    def describe(self) -> str:
        p = self.params
        return {
            "accept_alternative_source": f"« {self.field} » peut provenir de {p.get('column')}",
            "accept_value_mapping": f"« {self.field} » : recodage de {len(p.get('mapping') or {})} valeur(s) accepté",
            "accept_hypothesis": f"« {self.field} » : écarts expliqués par {p.get('hypothesis')} acceptés",
            "accept_subcategory": f"« {self.field} » : écarts « {p.get('subcategory')} » acceptés",
        }.get(self.operation, self.operation)


@dataclass
class Correction:
    finding_id: str
    verdict: str
    reason: str
    fingerprint: dict[str, str]
    author: str = ""
    created: str = ""


@dataclass
class FeedbackStore:
    rules: list[ExpertRule] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    path: Path | None = None
    exists: bool = False      # le fichier existait-il au chargement ?

    @property
    def empty(self) -> bool:
        return not self.rules and not self.corrections

    # ------------------------------------------------------------------ persistance

    @classmethod
    def load(cls, path: str | Path, fields: set[str] | None = None) -> FeedbackStore:
        path = Path(path)
        if not path.exists():
            return cls(path=path, exists=False)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if raw.get("version") != 1:
            raise FeedbackError(f"{path.name} : version non supportée")
        store = cls(path=path, exists=True)
        for r in raw.get("rules") or []:
            store.rules.append(ExpertRule(**{k: r[k] for k in r if k in ExpertRule.__dataclass_fields__}))
        for c in raw.get("corrections") or []:
            store.corrections.append(Correction(**{k: c[k] for k in c if k in Correction.__dataclass_fields__}))
        errors = store.validate(fields)
        if errors:
            raise FeedbackError(f"{path.name} invalide :\n  - " + "\n  - ".join(errors))
        return store

    def save(self, path: str | Path | None = None) -> Path:
        path = Path(path or self.path or "feedback/retroaction.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": 1,
               "rules": [asdict(r) for r in self.rules],
               "corrections": [asdict(c) for c in self.corrections]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(path)  # écriture atomique
        self.path, self.exists = path, True
        return path

    # ------------------------------------------------------------------ validation

    def validate(self, fields: set[str] | None = None) -> list[str]:
        errors = []
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            errors.append("identifiants de règles dupliqués")
        for r in self.rules:
            errors.extend(f"{r.id} : {e}" for e in validate_rule(r, fields))
        fids = [c.finding_id for c in self.corrections]
        if len(fids) != len(set(fids)):
            errors.append("plusieurs corrections pour le même écart")
        for c in self.corrections:
            if c.verdict not in {v.value for v in Verdict}:
                errors.append(f"correction {c.finding_id} : verdict inconnu « {c.verdict} »")
            if not c.reason.strip():
                errors.append(f"correction {c.finding_id} : justification obligatoire")
        return errors

    # ------------------------------------------------------------------ édition

    def next_rule_id(self) -> str:
        n = max((int(r.id.rsplit("-", 1)[-1]) for r in self.rules if r.id.rsplit("-", 1)[-1].isdigit()), default=0)
        return f"R-EXPERT-{n + 1:03d}"

    def add_rule(self, rule: ExpertRule, fields: set[str] | None = None) -> ExpertRule:
        if not rule.id:
            rule.id = self.next_rule_id()
        rule.created = rule.created or _now()
        errors = validate_rule(rule, fields)
        if any(r.id == rule.id for r in self.rules):
            errors.append(f"identifiant déjà utilisé : {rule.id}")
        if errors:
            raise FeedbackError("; ".join(errors))
        self.rules.append(rule)
        return rule

    def remove_rule(self, rule_id: str) -> None:
        self.rules = [r for r in self.rules if r.id != rule_id]

    def add_correction(self, finding: Finding, verdict: Verdict | str, reason: str, author: str = "") -> Correction:
        verdict = Verdict(verdict)
        if not reason.strip():
            raise FeedbackError("une correction doit être justifiée")
        self.corrections = [c for c in self.corrections if c.finding_id != finding.finding_id]
        corr = Correction(finding.finding_id, verdict.value, reason.strip(), fingerprint(finding), author, _now())
        self.corrections.append(corr)
        return corr

    def remove_correction(self, finding_id: str) -> None:
        self.corrections = [c for c in self.corrections if c.finding_id != finding_id]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fingerprint(f: Finding) -> dict[str, str]:
    """Valeurs sur lesquelles l'expert s'est prononcé (détection de péremption)."""
    def s(v: Any) -> str:
        return "" if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))
    return {"expected": s(f.expected), "target_raw": s(f.target_raw), "verdict_machine": f.verdict.value}


def validate_rule(rule: ExpertRule, fields: set[str] | None = None) -> list[str]:
    errors = []
    if rule.operation not in OPERATIONS:
        return [f"opération inconnue « {rule.operation} » (possibles : {', '.join(OPERATIONS)})"]
    if fields is not None and rule.field not in fields:
        errors.append(f"champ inconnu « {rule.field} »")
    if not rule.reason or not str(rule.reason).strip():
        errors.append("justification obligatoire")
    p = rule.params or {}
    expected_keys = set(OPERATIONS[rule.operation]["params"])
    if set(p) != expected_keys:
        errors.append(f"paramètres attendus : {sorted(expected_keys)}, reçus : {sorted(p)}")
        return errors
    if rule.operation == "accept_alternative_source" and not (isinstance(p["column"], str) and p["column"].strip()):
        errors.append("column doit être un nom de colonne")
    if rule.operation == "accept_value_mapping":
        m = p["mapping"]
        if not isinstance(m, dict) or not m or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
            errors.append("mapping doit être un objet non vide {texte: texte}")
    if rule.operation == "accept_hypothesis" and p["hypothesis"] not in KNOWN_HYPOTHESES:
        errors.append(f"hypothèse inconnue « {p['hypothesis']} »")
    if rule.operation == "accept_subcategory" and not (isinstance(p["subcategory"], str) and p["subcategory"].strip()):
        errors.append("subcategory doit être un texte non vide")
    return errors


# --------------------------------------------------------------------------- application

@dataclass
class RuleContext:
    """Accès aux valeurs candidates d'un écart et aux types de champs."""

    candidate_values: Any = None                      # Callable[[Finding], dict[str, Any]] | None
    field_kinds: dict[str, Any] = field(default_factory=dict)

    def candidate_columns(self, findings: list[Finding], target_field: str,
                          preferred: list[str] | None = None) -> list[str]:
        """Colonnes candidates compatibles avec le type du champ (au moins une valeur interprétable)."""
        if self.candidate_values is None:
            return list(preferred or [])
        kind = self.field_kinds.get(target_field, "text")
        usable: set[str] = set()
        for f in findings:
            if f.target_field != target_field:
                continue
            for col, val in (self.candidate_values(f) or {}).items():
                try:
                    if normalize(val, kind) is not None:
                        usable.add(col)
                except NormalizationError:
                    continue
        first = [c for c in (preferred or []) if c in usable]
        return first + sorted(usable - set(first))


def rule_matches(rule: ExpertRule, f: Finding, ctx: RuleContext) -> bool:
    if not rule.active or f.target_field != rule.field or f.verdict is not Verdict.ANOMALIE:
        return False
    p = rule.params
    if rule.operation == "accept_hypothesis":
        return any(h.verified and h.hypothesis_id == p["hypothesis"] for h in f.hypotheses)
    if rule.operation == "accept_subcategory":
        return (f.subcategory or "") == p["subcategory"]
    if rule.operation == "accept_value_mapping":
        return p["mapping"].get(_s(f.expected)) == _s(f.target_norm)
    if rule.operation == "accept_alternative_source":
        if ctx.candidate_values is None or f.target_norm is None:
            return False
        values = ctx.candidate_values(f) or {}
        if p["column"] not in values:
            return False
        kind = ctx.field_kinds.get(f.target_field, "text")
        try:
            return normalize(values[p["column"]], kind) == f.target_norm
        except NormalizationError:
            return False
    return False


def _s(v: Any) -> str:
    return "" if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


def store_status(store: FeedbackStore) -> str | None:
    """Explication lisible quand aucune rétroaction n'est appliquée (sinon None)."""
    if not store.exists:
        return (f"fichier de rétroaction introuvable ({store.path}) : aucune rétroaction appliquée. "
                "Il est créé par l'interface (corroborai app) à la première correction ou règle acceptée ; "
                "un exemple est fourni dans feedback/exemple_retroaction.yaml.")
    if store.empty:
        return f"fichier de rétroaction vide ({store.path}) : aucune règle ni correction enregistrée."
    return None


@dataclass
class FeedbackReport:
    rules_applied: dict[str, list[str]] = field(default_factory=dict)       # règle → écarts
    corrections_applied: list[str] = field(default_factory=list)
    corrections_stale: list[str] = field(default_factory=list)
    corrections_missing: list[str] = field(default_factory=list)
    store: FeedbackStore | None = None


def apply_feedback(findings: list[Finding], store: FeedbackStore, ctx: RuleContext) -> FeedbackReport:
    """Applique les règles expertes puis les corrections (la plus spécifique l'emporte)."""
    report = FeedbackReport(store=store)
    for rule in store.rules:
        hits = []
        for f in findings:
            if rule_matches(rule, f, ctx):
                initial = f.justification
                f.rule_params["verdict_initial"] = f.verdict.value
                f.rule_params["regle_initiale"] = f.rule_id or ""
                f.verdict = Verdict.JUSTIFIE
                f.decision_source = DecisionSource.EXPERT
                f.rule_id = rule.id
                f.confidence = Confidence.ELEVEE
                f.justification = (f"Règle experte {rule.id} — {rule.describe()} : {rule.reason} "
                                   f"[Verdict initial ANOMALIE : {initial}]")
                hits.append(f.finding_id)
        report.rules_applied[rule.id] = hits

    by_id = {f.finding_id: f for f in findings}
    for c in store.corrections:
        f = by_id.get(c.finding_id)
        if f is None:
            report.corrections_missing.append(c.finding_id)
            continue
        current = fingerprint(f)
        # La correction porte sur des valeurs précises : si elles ont changé, elle est périmée.
        if (current["expected"], current["target_raw"]) != (c.fingerprint.get("expected"),
                                                            c.fingerprint.get("target_raw")):
            report.corrections_stale.append(c.finding_id)
            f.rule_params["correction_perimee"] = c.reason
            continue
        initial = f.verdict.value
        f.rule_params["verdict_initial"] = f.rule_params.get("verdict_initial", initial)
        f.verdict = Verdict(c.verdict)
        f.decision_source = DecisionSource.EXPERT
        if f.verdict is Verdict.JUSTIFIE and not f.rule_id:
            f.rule_id = CORRECTION_RULE_ID
        f.justification = (f"Correction experte{f' ({c.author})' if c.author else ''} : {c.reason} "
                           f"[Verdict initial {initial} : {f.justification}]")
        report.corrections_applied.append(c.finding_id)
    return report


# --------------------------------------------------------------------------- aperçu et suggestions

@dataclass
class RulePreview:
    rule: ExpertRule
    errors: list[str]
    matched: list[Finding]                  # anomalies qui deviendraient JUSTIFIE
    field_anomalies: int                    # anomalies du champ
    remaining: int                          # anomalies du champ restantes après la règle

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if self.errors:
            return "Règle invalide : " + "; ".join(self.errors)
        if not self.matched:
            return "Cette règle ne modifierait aucun verdict."
        return (f"{len(self.matched)} anomalie(s) sur {self.field_anomalies} pour « {self.rule.field} » "
                f"deviendraient JUSTIFIE ; {self.remaining} resteraient à investiguer.")


def preview_rule(rule: ExpertRule, findings: list[Finding], ctx: RuleContext,
                 fields: set[str] | None = None) -> RulePreview:
    """Exécution à blanc : aucune donnée n'est modifiée."""
    errors = validate_rule(rule, fields)
    anomalies = [f for f in findings if f.target_field == rule.field and f.verdict is Verdict.ANOMALIE]
    matched = [] if errors else [f for f in anomalies if rule_matches(rule, f, ctx)]
    return RulePreview(rule, errors, matched, len(anomalies), len(anomalies) - len(matched))


def suggest_rules(store: FeedbackStore, findings: list[Finding], min_count: int = 2) -> list[ExpertRule]:
    """Généralise des corrections répétées en règles candidates.

    Si l'expert a accepté (JUSTIFIE) au moins ``min_count`` écarts d'un même
    champ partageant la même hypothèse vérifiée ou la même sous-catégorie, une
    règle est proposée — elle reste soumise à l'aperçu et à l'acceptation.
    """
    by_id = {f.finding_id: f for f in findings}
    by_hyp: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_sub: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in store.corrections:
        f = by_id.get(c.finding_id)
        if f is None or c.verdict != Verdict.JUSTIFIE.value:
            continue
        for h in f.hypotheses:
            if h.verified:
                by_hyp[(f.target_field, h.hypothesis_id)].append(f.finding_id)
        if f.subcategory:
            by_sub[(f.target_field, f.subcategory)].append(f.finding_id)
    existing = {(r.operation, r.field, str(sorted(r.params.items()))) for r in store.rules}
    out = []
    for (fld, hyp), ids in sorted(by_hyp.items()):
        if len(ids) >= min_count:
            out.append(ExpertRule("", "accept_hypothesis", fld, {"hypothesis": hyp},
                                  f"Généralisation de {len(ids)} corrections expertes ({', '.join(ids[:3])}…)",
                                  provenance="SUGGESTION"))
    for (fld, sub), ids in sorted(by_sub.items()):
        if len(ids) >= min_count:
            out.append(ExpertRule("", "accept_subcategory", fld, {"subcategory": sub},
                                  f"Généralisation de {len(ids)} corrections expertes ({', '.join(ids[:3])}…)",
                                  provenance="SUGGESTION"))
    return [r for r in out if (r.operation, r.field, str(sorted(r.params.items()))) not in existing]


def clone_findings(findings: list[Finding]) -> list[Finding]:
    return copy.deepcopy(findings)
