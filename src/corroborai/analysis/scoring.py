"""Score de priorité des écarts à investiguer.

priorité = échelle × (criticité / 5) × poids_confiance × base_verdict × Π modificateurs,
plafonnée à l'échelle. Le détail du calcul accompagne chaque verdict.
"""

from __future__ import annotations

from corroborai.analysis.config import ScoringConfig
from corroborai.models import Finding, Verdict

SCORED = (Verdict.ANOMALIE, Verdict.INDETERMINE)


def score_finding(f: Finding, scfg: ScoringConfig) -> tuple[float | None, str | None]:
    if f.verdict not in SCORED:
        return None, None
    crit = f.criticality or 1
    conf = scfg.confidence_weights[f.confidence.value]
    base = scfg.verdict_base[f.verdict.value]
    terms = [f"criticité {crit}/5", f"confiance {f.confidence.value} ({conf:g})",
             f"{f.verdict.value} ({base:g})"]
    value = (crit / 5) * conf * base
    seen = set()
    for h in f.hypotheses:
        if h.verified and h.hypothesis_id not in seen and h.hypothesis_id in scfg.modifiers:
            seen.add(h.hypothesis_id)
            factor = scfg.modifiers[h.hypothesis_id].factor
            value *= factor
            terms.append(f"{h.hypothesis_id} ({factor:g})")
    raw = scfg.scale * value
    priority = round(min(raw, scfg.scale), 1)
    capped = " (plafonné)" if raw > scfg.scale else ""
    return priority, f"{' × '.join(terms)} = {priority:g}{capped}"


def apply_scores(findings: list[Finding], scfg: ScoringConfig) -> None:
    for f in findings:
        f.priority, f.priority_breakdown = score_finding(f, scfg)
