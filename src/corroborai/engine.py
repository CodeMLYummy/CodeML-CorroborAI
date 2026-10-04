"""Moteur de corroboration (niveaux 0 à 2, entièrement déterministe).

Pour chaque affectation appariée et chaque champ du mapping codifié :

1. la règle calcule la valeur attendue et le verdict ;
2. si le champ dépend d'une interprétation à alternatives, la règle est
   réévaluée sous chaque alternative : un verdict qui en dépend voit sa
   confiance abaissée à MOYENNE, et le verdict alternatif est tracé ;
3. un ``Finding`` est produit avec ses preuves (lignes source, cible et
   tables de jointure).

Les affectations sans correspondance produisent un ``Finding`` d'appariement.
Le moteur vérifie l'intégrité des fichiers avant et après le traitement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from corroborai import __version__
from corroborai.analysis import AnalysisResult, HypothesisEngine, apply_scores, load_hypotheses, load_scoring
from corroborai.analysis.config import HypothesesConfig, ScoringConfig
from corroborai.io.loaders import ROW_COL, DataBundle, IntegrityReport
from corroborai.matching import AssignmentPair, MatchMethod, match_assignments
from corroborai.models import Confidence, DecisionSource, Evidence, Finding, Verdict
from corroborai.rules import REGISTRY
from corroborai.rules.base import UNKNOWN, Outcome, Refs, RuleContext, ev_source, ev_target
from corroborai.rules_config import KNOWN_RULE_TYPES, FieldRule, RulesConfig

AGREEING = (Verdict.CONFORME, Verdict.JUSTIFIE)
ASSIGNMENT_FIELD = "affectation"

missing_rules = KNOWN_RULE_TYPES - set(REGISTRY)
assert not missing_rules, f"Types de règles non implémentés : {sorted(missing_rules)}"


@dataclass
class InterpretationStat:
    interpretation: str
    target_field: str
    choice: str
    active: bool
    evaluated: int = 0
    agreeing: int = 0

    @property
    def rate(self) -> float:
        return self.agreeing / self.evaluated if self.evaluated else 0.0


@dataclass
class CorroborationResult:
    findings: list[Finding]
    pairs: list[AssignmentPair]
    integrity_before: IntegrityReport
    integrity_after: IntegrityReport
    cfg: RulesConfig
    overrides: dict[str, str]
    interpretation_stats: list[InterpretationStat]
    started_at: datetime
    duration_s: float
    version: str = __version__
    errors: list[str] = field(default_factory=list)
    analysis: AnalysisResult | None = None
    ai: Any = None        # AIReport, renseigné par corroborai.ai.tasks.run_ai
    feedback: Any = None  # FeedbackReport (rétroaction experte appliquée)
    rule_context: Any = None  # feedback.RuleContext (aperçu de règles expertes)

    def by_verdict(self) -> dict[Verdict, list[Finding]]:
        out: dict[Verdict, list[Finding]] = {v: [] for v in Verdict}
        for f in self.findings:
            out[f.verdict].append(f)
        return out

    def counts(self) -> dict[str, int]:
        return {v.value: len(fs) for v, fs in self.by_verdict().items()}

    def find(self, person_id: str, target_field: str) -> list[Finding]:
        return [f for f in self.findings if f.person_id == person_id and f.target_field == target_field]


def _validate_overrides(cfg: RulesConfig, overrides: dict[str, str]) -> None:
    for iid, choice in overrides.items():
        interp = cfg.interpretations.get(iid)
        if interp is None:
            raise ValueError(f"Interprétation inconnue : {iid}")
        if choice not in (interp.choice, *interp.alternatives):
            raise ValueError(f"{iid} : choix « {choice} » invalide "
                             f"(possibles : {', '.join((interp.choice, *interp.alternatives))})")


def _merge_evidence(items: list[Evidence]) -> list[Evidence]:
    merged: dict[str, Evidence] = {}
    for ev in items:
        if ev.id in merged:
            prev = merged[ev.id]
            merged[ev.id] = Evidence(ev.id, prev.table, prev.row, {**prev.values, **ev.values},
                                     prev.note or ev.note)
        else:
            merged[ev.id] = ev
    return list(merged.values())


def _evaluate(fr: FieldRule, ctx: RuleContext, strict: bool) -> Outcome:
    try:
        return REGISTRY[fr.rule](ctx)
    except Exception as exc:  # noqa: BLE001 — un champ en erreur ne doit pas bloquer le rapport
        if strict:
            raise
        return Outcome(Verdict.INDETERMINE, UNKNOWN, None,
                       f"Erreur interne lors de l'évaluation de la règle {fr.rule_id} : {exc!r}",
                       Confidence.FAIBLE, subcategory="erreur d'évaluation")


def _field_finding(cfg: RulesConfig, fr: FieldRule, pair: AssignmentPair, refs: Refs,
                   overrides: dict[str, str], strict: bool,
                   stats: dict[tuple[str, str, str], InterpretationStat]) -> Finding:
    assert pair.source is not None and pair.target is not None
    ctx = RuleContext(cfg, fr, pair.source, pair.target, refs, overrides)
    out = _evaluate(fr, ctx, strict)
    params = dict(out.params)
    confidence = out.confidence

    interp = cfg.interpretation_for(fr)
    if interp and interp.alternatives:
        active = overrides.get(interp.id, interp.choice)
        verdicts = {active: out.verdict}
        for alt in (interp.choice, *interp.alternatives):
            if alt == active:
                continue
            alt_out = _evaluate(fr, RuleContext(cfg, fr, pair.source, pair.target, refs,
                                                {**overrides, interp.id: alt}), strict)
            verdicts[alt] = alt_out.verdict
            params[f"alternative[{alt}]"] = f"{alt_out.verdict.value} (attendu {alt_out.expected})"
        if len(set(verdicts.values())) > 1 and confidence is Confidence.ELEVEE:
            confidence = Confidence.MOYENNE
        for choice, verdict in verdicts.items():
            st = stats.setdefault((interp.id, fr.target, choice),
                                  InterpretationStat(interp.id, fr.target, choice, choice == active))
            if verdict is not Verdict.INDETERMINE:
                st.evaluated += 1
                st.agreeing += verdict in AGREEING

    evidence = _merge_evidence([ev_source(ctx, fr.sources), ev_target(ctx, [fr.target]), *out.evidence])
    if pair.method is MatchMethod.CROSS_TYPE:
        params["appariement"] = f"types différents ({pair.source_type} ↔ {pair.target_type})"

    return Finding(
        person_id=pair.person_id,
        assignment_key=pair.key,
        target_field=fr.target,
        source_fields=fr.sources,
        source_raw=out.source_value if out.source_value is not None else pair.source.get(fr.sources[0]),
        target_raw=pair.target.get(fr.target),
        expected=None if out.expected is UNKNOWN else out.expected,
        target_norm=out.target_norm,
        verdict=out.verdict,
        decision_source=DecisionSource.REGLE,
        rule_id=fr.rule_id,
        justification=out.justification,
        rule_params=params,
        rule_ref=", ".join([str(fr.mapping_ref), *map(str, fr.related_refs)]),
        subcategory=out.subcategory,
        confidence=confidence,
        evidence=evidence,
        criticality=fr.criticality,
    )


def _summary(row: dict[str, Any], cols: tuple[str, ...]) -> str:
    return ", ".join(f"{c}={row.get(c)}" for c in cols if row.get(c) not in (None, ""))


def _missing_finding(cfg: RulesConfig, pair: AssignmentPair) -> Finding:
    if pair.method is MatchMethod.MISSING_TARGET:
        assert pair.source is not None
        policy = cfg.matching.missing_in_target
        row, table, side = pair.source, "source", "src"
        desc = _summary(row, ("TypeAffectation", "CodePoste", "CodeEmploi", "CodeDirection", "DateEntréePoste"))
        text = (f"Affectation présente dans le système RH mais introuvable dans le système Temps "
                f"({desc}). Aucune affectation cible restante de cet employé n'atteint le seuil de "
                f"similarité ({cfg.matching.cross_type_min_score:.0%}).")
        subcat, src_raw, tgt_raw = "affectation absente de la cible", desc, None
    else:
        assert pair.target is not None
        policy = cfg.matching.missing_in_source
        row, table, side = pair.target, "target", "tgt"
        desc = _summary(row, ("positionId", "divisionId", "assignmentStartDate",
                              "isPrimaryAssignment", "isTemporaryAssignment"))
        text = f"Affectation présente dans le système Temps mais inconnue du système RH ({desc})."
        subcat, src_raw, tgt_raw = "affectation absente de la source", None, desc
    return Finding(
        person_id=pair.person_id, assignment_key=pair.key, target_field=ASSIGNMENT_FIELD,
        source_fields=("Matricule", "TypeAffectation"), source_raw=src_raw, target_raw=tgt_raw,
        expected=None, target_norm=None, verdict=Verdict.ANOMALIE,
        decision_source=DecisionSource.REGLE, rule_id=policy.rule_id, justification=text,
        rule_params={"methode": pair.method.value}, rule_ref="config/rules.yaml#assignment_matching",
        subcategory=subcat, criticality=policy.criticality,
        evidence=[Evidence(f"{side}:{row[ROW_COL]}", table, row[ROW_COL],
                           {k: v for k, v in row.items() if k != ROW_COL and v is not None})],
    )


def corroborate(bundle: DataBundle, cfg: RulesConfig, overrides: dict[str, str] | None = None,
                strict: bool = False, analyze: bool = True,
                hypotheses: HypothesesConfig | None = None,
                scoring: ScoringConfig | None = None,
                feedback: Any = None) -> CorroborationResult:
    """Exécute la corroboration complète.

    ``strict`` propage les erreurs internes (tests). ``analyze`` exécute le
    moteur d'hypothèses et le score de priorité (niveau 3 déterministe).
    ``feedback`` (FeedbackStore) applique les règles expertes et corrections
    après l'analyse et avant le calcul des priorités.
    """
    overrides = dict(overrides or {})
    _validate_overrides(cfg, overrides)
    started, t0 = datetime.now(), time.perf_counter()

    refs = Refs.build(bundle, cfg)
    pairs = match_assignments(bundle, cfg)
    stats: dict[tuple[str, str, str], InterpretationStat] = {}
    findings: list[Finding] = []
    for pair in pairs:
        if pair.matched:
            findings.extend(_field_finding(cfg, fr, pair, refs, overrides, strict, stats) for fr in cfg.fields)
        else:
            findings.append(_missing_finding(cfg, pair))

    from corroborai.feedback import RuleContext, apply_feedback  # import local : évite un cycle

    hcfg = hypotheses or load_hypotheses()
    hengine = HypothesisEngine(cfg, hcfg, refs, pairs, findings)
    analysis = hengine.run() if analyze else None
    rule_ctx = RuleContext(hengine.candidate_values_for, {f.target: f.kind for f in cfg.fields})
    feedback_report = apply_feedback(findings, feedback, rule_ctx) if feedback is not None else None
    if analyze:
        apply_scores(findings, scoring or load_scoring(hypotheses=hcfg))

    errors = [f.finding_id for f in findings if f.subcategory == "erreur d'évaluation"]
    return CorroborationResult(
        findings=findings,
        pairs=pairs,
        integrity_before=bundle.integrity,
        integrity_after=bundle.verify_unchanged(),
        cfg=cfg,
        overrides=overrides,
        interpretation_stats=sorted(stats.values(),
                                    key=lambda s: (s.interpretation, s.target_field, not s.active, s.choice)),
        started_at=started,
        duration_s=time.perf_counter() - t0,
        errors=errors,
        analysis=analysis,
        feedback=feedback_report,
        rule_context=rule_ctx,
    )
