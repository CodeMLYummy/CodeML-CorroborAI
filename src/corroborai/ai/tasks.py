"""Tâches LLM : construction des dossiers, gabarits déterministes, orchestration.

Le LLM n'est sollicité que là où il apporte une valeur qu'aucune règle
n'apporte :

* **SYNTHESE_GLOBALE** — une vue d'ensemble rédigée des motifs et règles
  candidates (1 appel) ;
* **SYNTHESE_EMPLOYE** — un diagnostic regroupé, seulement pour les employés
  ayant une anomalie non systémique de priorité suffisante ;
* **TRIAGE_ANOMALIE** — une piste (non vérifiée) pour une anomalie qu'aucune
  hypothèse déterministe n'explique.

Chaque dossier est pseudonymisé ; la réidentification n'a lieu que localement,
après validation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corroborai.ai.harness import Harness, HarnessResult, Status
from corroborai.ai.policy import DataClass, Dossier, LLMConfig, Pseudonymizer, load_llm_config
from corroborai.ai.providers import Transport, make_provider
from corroborai.ai.schemas import SYNTHESE_EMPLOYE, SYNTHESE_GLOBALE, TRIAGE_ANOMALIE
from corroborai.io.loaders import DataBundle
from corroborai.models import ExplanationSource, Finding, HypothesisResult, Verdict

INVESTIGATE = (Verdict.ANOMALIE, Verdict.INDETERMINE)
SYSTEMIC = {"H-SYSTEMIC", "H-BIJECTION"}
PERSONAL_FIELDS = {"givenName", "surname", "contactEmail", "personId"}

D = DataClass
KEY_CLASSES: dict[str, DataClass] = {
    # identifiants
    "employe": D.IDENTIFIANT_PSEUDONYMISE, "employes": D.IDENTIFIANT_PSEUDONYMISE,
    # valeurs
    "valeur_attendue": D.VALEUR_METIER, "valeur_cible": D.VALEUR_METIER,
    # textes
    "justification": D.TEXTE_REGLE, "cause_probable": D.TEXTE_REGLE, "description": D.TEXTE_REGLE,
    "libelle": D.TEXTE_REGLE,
    # preuves
    "preuves": D.PREUVE,
    # méta
    "ref": D.META, "champ": D.META, "verdict": D.META, "priorite": D.META, "sous_categorie": D.META,
    "regle": D.META, "hypotheses": D.META, "criticite": D.META, "nombre": D.META,
    "ecarts_systemiques_exclus": D.META, "verdicts": D.META, "decompte": D.META,
    "concordance": D.META, "population": D.META, "colonne": D.META, "hypothese": D.META,
    "ANOMALIE": D.META, "INDETERMINE": D.META, "JUSTIFIE": D.META, "CONFORME": D.META,
}

INSTRUCTIONS = {
    SYNTHESE_GLOBALE: (
        "Rédige une synthèse globale des écarts pour l'équipe fonctionnelle : quels motifs dominent, "
        "lesquels relèvent d'une cause unique à corriger une seule fois, et par où commencer. Propose "
        "des pistes d'action concrètes classées par importance. Cite les références des motifs (M1, M2…)."),
    SYNTHESE_EMPLOYE: (
        "Rédige un diagnostic regroupé des écarts de cet employé : regroupe les écarts qui partagent une "
        "cause commune, explique simplement ce qui semble s'être produit et propose des pistes de "
        "vérification concrètes. Cite les identifiants de preuve pertinents."),
    TRIAGE_ANOMALIE: (
        "Aucune hypothèse déterministe n'explique cet écart. Propose la catégorie de cause la plus "
        "plausible, justifie-la à partir des données uniquement, et propose une piste de vérification. "
        "Ta confiance ne peut être que « faible » ou « moyenne » : il s'agit d'une piste à vérifier."),
}


def clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- dossiers

def _value(f: Finding, v: Any, pseudo: Pseudonymizer) -> Any:
    if v is None:
        return None
    text = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return pseudo.text(text)


def _ecart(f: Finding, ref: str, pseudo: Pseudonymizer) -> dict[str, Any]:
    return {
        "ref": ref,
        "champ": f.target_field,
        "verdict": f.verdict.value,
        "priorite": f.priority,
        "sous_categorie": f.subcategory or "",
        "valeur_attendue": _value(f, f.expected, pseudo),
        "valeur_cible": _value(f, f.target_norm if f.target_norm is not None else f.target_raw, pseudo),
        "regle": f.rule_id or "",
        "justification": pseudo.text(f.justification),
        "cause_probable": pseudo.text(f.probable_cause or ""),
        "hypotheses": [h.hypothesis_id for h in f.hypotheses if h.verified],
        "preuves": [e.id for e in f.evidence],
    }


def employee_dossier(person: str, findings: list[Finding], pseudo: Pseudonymizer,
                     exclude_systemic: bool) -> Dossier:
    kept, excluded = [], 0
    for f in sorted(findings, key=lambda f: -(f.priority or 0)):
        if exclude_systemic and {h.hypothesis_id for h in f.hypotheses if h.verified} & SYSTEMIC:
            excluded += 1
            continue
        kept.append(f)
    ecarts = [_ecart(f, f"E{i}", pseudo) for i, f in enumerate(kept, start=1)]
    d = Dossier(SYNTHESE_EMPLOYE, {"employe": pseudo.person(person), "ecarts": ecarts,
                                   "ecarts_systemiques_exclus": excluded}, KEY_CLASSES)
    d.refs = {e["ref"] for e in ecarts}
    d.evidence_ids = {p for e in ecarts for p in e["preuves"]}
    return d


def triage_dossier(f: Finding, pseudo: Pseudonymizer) -> Dossier:
    ecart = _ecart(f, "E1", pseudo)
    d = Dossier(TRIAGE_ANOMALIE, {"employe": pseudo.person(f.person_id), "ecart": ecart}, KEY_CLASSES)
    d.key_classes = {**KEY_CLASSES, "ecart": D.META}
    d.refs, d.evidence_ids = {"E1"}, set(ecart["preuves"])
    return d


def global_dossier(findings: list[Finding], patterns: list[Any], candidates: list[Any],
                   pseudo: Pseudonymizer) -> Dossier:
    counts = Counter(f.verdict.value for f in findings)
    motifs = []
    for i, p in enumerate(patterns, start=1):
        persons = sorted({fid.split(":")[0] for fid in p.finding_ids})
        motifs.append({"ref": f"M{i}", "hypothese": p.hypothesis_id, "champ": p.target_field,
                       "nombre": p.count, "description": pseudo.text(p.description),
                       "employes": [pseudo.person(x) for x in persons]})
    offset = len(motifs)
    for j, c in enumerate(candidates, start=1):
        motifs.append({"ref": f"M{offset + j}", "hypothese": "REGLE_CANDIDATE", "champ": c.target_field,
                       "nombre": len(c.explains), "description": pseudo.text(c.description),
                       "employes": []})
    payload = {"decompte": {k: counts.get(k, 0) for k in ("ANOMALIE", "INDETERMINE", "JUSTIFIE", "CONFORME")},
               "motifs": motifs}
    d = Dossier(SYNTHESE_GLOBALE, payload, {**KEY_CLASSES, "motifs": D.META})
    d.refs = {m["ref"] for m in motifs}
    return d


# --------------------------------------------------------------------------- gabarits déterministes

def template_employee(d: Dossier) -> dict[str, Any]:
    p = d.payload
    ecarts = p["ecarts"]
    head = ecarts[0]
    prio = f"{head['priorite']:g}" if head["priorite"] is not None else "?"
    parts = [f"{p['employe']} : {len(ecarts)} écart(s) à investiguer, priorité maximale {prio}."]
    for e in ecarts[:2]:
        parts.append(f"{e['champ']} — {clip(e['cause_probable'] or e['justification'], 200)}")
    if p["ecarts_systemiques_exclus"]:
        parts.append(f"Écarts systémiques traités globalement : {p['ecarts_systemiques_exclus']}.")
    groups: dict[str, list[str]] = defaultdict(list)
    for e in ecarts:
        groups[e["hypotheses"][0] if e["hypotheses"] else "sans hypothèse"].append(e["ref"])
    return {
        "synthese": clip(" ".join(parts), 700),
        "pistes": [clip(f"Vérifier {e['champ']} dans les deux systèmes (règle {e['regle']}).", 260)
                   for e in ecarts[:4]],
        "regroupements": [{"references": refs, "cause_commune": clip(f"Cause commune : {hid}", 260)}
                          for hid, refs in groups.items() if len(refs) > 1][:5],
        "preuves_citees": sorted({x for e in ecarts for x in e["preuves"]})[:30],
    }


def template_triage(d: Dossier) -> dict[str, Any]:
    e = d.payload["ecart"]
    cat = "REGLE_INCOMPLETE" if e["verdict"] == Verdict.INDETERMINE.value else "INCONNUE"
    return {
        "categorie": cat,
        "justification": clip(f"Aucune hypothèse déterministe vérifiée. {e['justification']}", 500),
        "piste": clip(f"Comparer manuellement {e['champ']} dans les deux systèmes à partir des preuves citées.", 260),
        "preuves_citees": e["preuves"][:20] or ["aucune"],
        "confiance": "faible",
    }


def template_global(d: Dossier) -> dict[str, Any]:
    p = d.payload
    c = p["decompte"]
    motifs = p["motifs"]
    text = (f"{c['ANOMALIE']} anomalie(s) et {c['INDETERMINE']} cas indéterminé(s). "
            f"{len(motifs)} motif(s) ou règle(s) candidate(s) identifié(s). ")
    text += " ".join(clip(m["description"], 160) for m in motifs[:3])
    return {
        "synthese": clip(text, 900),
        "pistes": [clip(f"{m['ref']} ({m['champ']}) : {m['description']}", 260) for m in motifs[:5]]
                  or ["Aucun motif : examiner les anomalies une à une."],
        "references": [m["ref"] for m in motifs[:20]] or ["M0"],
    }


# --------------------------------------------------------------------------- orchestration

@dataclass
class AISummary:
    scope: str                 # "GLOBALE" | "EMPLOYE"
    person_id: str | None
    synthese: str
    pistes: list[str]
    details: list[str]
    source: str
    status: str


@dataclass
class AIReport:
    provider: str
    model: str | None
    provider_class: str
    summaries: list[AISummary] = field(default_factory=list)
    triaged: list[str] = field(default_factory=list)
    statuses: Counter = field(default_factory=Counter)
    audit_path: Path | None = None

    @property
    def calls(self) -> int:
        return sum(self.statuses.values())


def run_ai(result: Any, bundle: DataBundle, llm_cfg: LLMConfig | None = None, provider_name: str | None = None,
           cache_dir: str | Path | None = None, audit_path: str | Path | None = None,
           transport: Transport | None = None) -> AIReport:
    cfg = llm_cfg or load_llm_config()
    spec = cfg.provider(provider_name)
    pseudo = Pseudonymizer.from_bundle(bundle)
    harness = Harness(cfg, make_provider(spec, transport), pseudo, cache_dir, audit_path)
    report = AIReport(spec.name, spec.model, spec.effective_class.value, audit_path=Path(audit_path) if audit_path else None)

    def record(res: HarnessResult) -> None:
        report.statuses[res.status.value] += 1

    findings: list[Finding] = result.findings
    investigate = [f for f in findings if f.verdict in INVESTIGATE]

    # 1. Synthèse globale
    if investigate and result.analysis is not None:
        d = global_dossier(findings, result.analysis.patterns, result.analysis.candidate_rules, pseudo)
        res = harness.run(SYNTHESE_GLOBALE, d, INSTRUCTIONS[SYNTHESE_GLOBALE], template_global)
        record(res)
        data = pseudo.restore(res.data)
        report.summaries.append(AISummary("GLOBALE", None, data["synthese"], data["pistes"],
                                          [f"Références : {', '.join(data['references'])}"],
                                          res.source, res.status.value))

    # 2. Synthèses par employé (anomalie non systémique de priorité suffisante)
    by_person: dict[str, list[Finding]] = defaultdict(list)
    for f in investigate:
        by_person[f.person_id].append(f)
    h = cfg.harness
    for person, fs in sorted(by_person.items(), key=lambda kv: -max(f.priority or 0 for f in kv[1])):
        relevant = [f for f in fs if (f.priority or 0) >= h.synthesis_min_priority
                    and not ({x.hypothesis_id for x in f.hypotheses if x.verified} & SYSTEMIC)]
        if not relevant:
            continue
        d = employee_dossier(person, fs, pseudo, h.exclude_systemic_from_employee)
        res = harness.run(SYNTHESE_EMPLOYE, d, INSTRUCTIONS[SYNTHESE_EMPLOYE], template_employee)
        record(res)
        data = pseudo.restore(res.data)
        refs = {e["ref"]: e["champ"] for e in d.payload["ecarts"]}
        details = [f"{', '.join(refs.get(r, r) for r in g['references'])} — {g['cause_commune']}"
                   for g in data["regroupements"]]
        report.summaries.append(AISummary("EMPLOYE", person, data["synthese"], data["pistes"], details,
                                          res.source, res.status.value))
        source = ExplanationSource.LLM if res.source == "LLM" else ExplanationSource.GABARIT
        for f in fs:
            if f.explanation is None:
                f.explanation = data["synthese"]
                f.explanation_source = source

    # 3. Triage des anomalies sans hypothèse vérifiée
    for f in investigate:
        if any(h_.verified for h_ in f.hypotheses):
            continue
        d = triage_dossier(f, pseudo)
        res = harness.run(TRIAGE_ANOMALIE, d, INSTRUCTIONS[TRIAGE_ANOMALIE], template_triage)
        record(res)
        data = pseudo.restore(res.data)
        cited = tuple(x for x in data["preuves_citees"] if any(e.id == x for e in f.evidence))
        f.hypotheses.append(HypothesisResult(f"IA:{data['categorie']}",
                                             f"{data['justification']} Piste : {data['piste']}",
                                             verified=False, evidence_ids=cited))
        f.explanation = (f"Piste IA non vérifiée (confiance {data['confiance']}) — {data['categorie']} : "
                         f"{data['justification']} Piste : {data['piste']}")
        f.explanation_source = ExplanationSource.LLM if res.source == "LLM" else ExplanationSource.GABARIT
        report.triaged.append(f.finding_id)
    return report


__all__ = ["AIReport", "AISummary", "Status", "run_ai"]
