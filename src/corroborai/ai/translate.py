"""Traduction d'une consigne experte en langage naturel vers le mini-langage de règles.

Le LLM **propose** ; il ne décide pas. Sa sortie doit :

* respecter le schéma strict ``TRADUCTION_REGLE`` ;
* désigner un champ et des paramètres **issus du dossier** (colonnes,
  hypothèses et sous-catégories réellement observées pour ce champ) ;
* former une règle valide du mini-langage (``feedback.validate_rule``).

Toute erreur déclenche la nouvelle tentative du harnais, puis le repli
« AUCUNE » (l'expert utilise alors le formulaire). Une règle proposée n'est
jamais appliquée directement : elle passe par l'aperçu d'impact, puis par
l'acceptation explicite de l'expert.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corroborai.ai.harness import Harness
from corroborai.ai.policy import DataClass, Dossier, LLMConfig, Pseudonymizer, load_llm_config
from corroborai.ai.providers import Transport, make_provider
from corroborai.ai.schemas import TRADUCTION_REGLE
from corroborai.ai.tasks import KEY_CLASSES
from corroborai.feedback import OPERATIONS, ExpertRule, validate_rule
from corroborai.io.loaders import DataBundle
from corroborai.models import Verdict

MAX_COLUMNS = 15
D = DataClass
TRANSLATION_KEYS = {
    **KEY_CLASSES,
    "texte_expert": D.TEXTE_REGLE, "champs": D.META, "type": D.META, "anomalies": D.META,
    "sous_categories": D.META, "colonnes": D.META, "operations": D.META, "operation": D.META,
    "parametres": D.META,
}

INSTRUCTIONS = (
    "Traduis la consigne de l'expert en UNE règle du mini-langage décrit dans « operations ». "
    "Choisis le champ et les paramètres uniquement parmi ceux fournis dans « champs » (colonnes, "
    "hypothèses, sous-catégories). Si la consigne est ambiguë, ne correspond à aucune opération ou "
    "demande autre chose qu'accepter des écarts, réponds avec l'opération « AUCUNE » et explique "
    "pourquoi dans « reformulation ». La reformulation doit permettre à l'expert de vérifier, en une "
    "phrase, que la règle correspond bien à son intention."
)


@dataclass
class Translation:
    rule: ExpertRule | None
    reformulation: str
    confidence: str
    source: str
    status: str
    errors: list[str] = field(default_factory=list)


def translation_dossier(text: str, result: Any, pseudo: Pseudonymizer) -> tuple[Dossier, dict[str, dict[str, list[str]]]]:
    anomalies = [f for f in result.findings if f.verdict is Verdict.ANOMALIE]
    by_field: dict[str, list[Any]] = defaultdict(list)
    for f in anomalies:
        by_field[f.target_field].append(f)
    preferred = defaultdict(list)
    if result.analysis is not None:
        for c in result.analysis.candidate_rules:
            preferred[c.target_field].append(c.column)
    kinds = result.rule_context.field_kinds if result.rule_context else {}

    allowed: dict[str, dict[str, list[str]]] = {}
    champs = []
    for fld, fs in sorted(by_field.items()):
        cols = (result.rule_context.candidate_columns(result.findings, fld, preferred[fld])[:MAX_COLUMNS]
                if result.rule_context else [])
        subs = sorted({f.subcategory for f in fs if f.subcategory})
        hyps = sorted({h.hypothesis_id for f in fs for h in f.hypotheses if h.verified})
        allowed[fld] = {"colonnes": cols, "sous_categories": subs, "hypotheses": hyps}
        kind = kinds.get(fld)
        champs.append({"champ": fld, "type": getattr(kind, "value", str(kind or "")), "anomalies": len(fs),
                       "sous_categories": subs, "hypotheses": hyps, "colonnes": cols})
    operations = [{"operation": op, "description": spec["description"],
                   "parametres": [f"{k} : {v}" for k, v in spec["params"].items()]}
                  for op, spec in OPERATIONS.items()]
    payload = {"texte_expert": pseudo.text(text), "champs": champs, "operations": operations}
    d = Dossier(TRADUCTION_REGLE, payload, dict(TRANSLATION_KEYS))
    return d, allowed


def _extra_validator(allowed: dict[str, dict[str, list[str]]]):
    def check(data: dict[str, Any], dossier: Dossier) -> list[str]:
        op = data["operation"]
        if op == "AUCUNE":
            return []
        fld = data["champ"]
        if fld not in allowed:
            return [f"champ « {fld} » absent des champs fournis (choisir parmi : {', '.join(allowed)})"]
        rule = ExpertRule("R-PROPOSEE", op, fld, data["parametres"], data["reformulation"])
        errors = validate_rule(rule, set(allowed))
        if errors:
            return errors
        a, p = allowed[fld], data["parametres"]
        if op == "accept_alternative_source" and p["column"] not in a["colonnes"]:
            errors.append(f"colonne « {p['column']} » absente des colonnes fournies pour {fld}")
        if op == "accept_hypothesis" and p["hypothesis"] not in a["hypotheses"]:
            errors.append(f"hypothèse « {p['hypothesis']} » non observée pour {fld} (fournies : {a['hypotheses']})")
        if op == "accept_subcategory" and p["subcategory"] not in a["sous_categories"]:
            errors.append(f"sous-catégorie « {p['subcategory']} » non observée pour {fld}")
        if op == "accept_value_mapping":
            haystack = dossier.serialize()
            for k, v in p["mapping"].items():
                if k not in haystack or v not in haystack:
                    errors.append(f"recodage « {k} → {v} » non présent dans la consigne ni dans les données (non ancré)")
        return errors
    return check


def _template(_: Dossier) -> dict[str, Any]:
    return {"operation": "AUCUNE", "champ": "", "parametres": {},
            "reformulation": "Traduction automatique indisponible : utilisez le formulaire de règle.",
            "confiance": "faible"}


def translate_feedback(text: str, result: Any, bundle: DataBundle, llm_cfg: LLMConfig | None = None,
                       provider_name: str | None = None, transport: Transport | None = None,
                       audit_path: str | Path | None = None, cache_dir: str | Path | None = None) -> Translation:
    if not text or not text.strip():
        return Translation(None, "Consigne vide.", "faible", "GABARIT", "VIDE")
    cfg = llm_cfg or load_llm_config()
    spec = cfg.provider(provider_name)
    pseudo = Pseudonymizer.from_bundle(bundle)
    dossier, allowed = translation_dossier(text, result, pseudo)
    harness = Harness(cfg, make_provider(spec, transport), pseudo, cache_dir, audit_path)
    res = harness.run(TRADUCTION_REGLE, dossier, INSTRUCTIONS, _template, _extra_validator(allowed))
    data = pseudo.restore(res.data)
    errors = [e for a in res.attempts for e in a.get("erreurs", [])] + \
             [a["erreur_fournisseur"] for a in res.attempts if "erreur_fournisseur" in a]
    if data["operation"] == "AUCUNE":
        return Translation(None, data["reformulation"], data["confiance"], res.source, res.status.value, errors)
    rule = ExpertRule("", data["operation"], data["champ"], data["parametres"], data["reformulation"],
                      provenance=f"IA:{spec.name}", original_text=text.strip())
    return Translation(rule, data["reformulation"], data["confiance"], res.source, res.status.value, errors)


__all__ = ["Translation", "translate_feedback", "translation_dossier"]
