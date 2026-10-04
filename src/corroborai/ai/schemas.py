"""Schémas de sortie stricts et validation des réponses du LLM.

Le LLM ne peut produire que ces structures. Garanties vérifiées :

* JSON valide, objet unique, **aucune clé hors schéma** (en particulier,
  aucun champ de verdict n'existe : l'IA ne peut structurellement pas en fixer) ;
* types, longueurs et énumérations fermées ;
* chaque preuve et chaque référence citées figurent dans le dossier fourni ;
* ancrage des textes : toute date, tout nombre d'au moins deux chiffres, tout
  jeton d'employé et toute valeur entre guillemets français cités dans un
  texte libre doivent apparaître dans le dossier (contrôle anti-hallucination).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from corroborai.ai.policy import Dossier

SYNTHESE_GLOBALE = "SYNTHESE_GLOBALE"
SYNTHESE_EMPLOYE = "SYNTHESE_EMPLOYE"
TRIAGE_ANOMALIE = "TRIAGE_ANOMALIE"
TRADUCTION_REGLE = "TRADUCTION_REGLE"
TASKS = (SYNTHESE_GLOBALE, SYNTHESE_EMPLOYE, TRIAGE_ANOMALIE, TRADUCTION_REGLE)
RULE_OPERATIONS = ("accept_alternative_source", "accept_value_mapping", "accept_hypothesis",
                   "accept_subcategory", "AUCUNE")

# Catégories de triage : catalogue d'hypothèses déterministes + causes plausibles
# qu'aucune règle ne peut vérifier (saisie, délai de synchronisation…).
TRIAGE_CATEGORIES = (
    "H-PERMUTATION", "H-HISTORY-RECORD", "H-ALT-SOURCE", "H-ALT-INTERPRETATION",
    "H-BIJECTION", "H-SYSTEMIC", "H-TYPE-ABSENT",
    "SAISIE_SOURCE", "SAISIE_CIBLE", "DELAI_SYNCHRONISATION", "REGLE_INCOMPLETE", "INCONNUE",
)
AI_CONFIDENCE = ("faible", "moyenne")   # jamais « élevée » : une piste IA reste à vérifier


@dataclass(frozen=True)
class Str:
    min_len: int
    max_len: int
    grounded: bool = True      # soumis au contrôle d'ancrage


@dataclass(frozen=True)
class Enum_:
    values: tuple[str, ...]


@dataclass(frozen=True)
class List_:
    item: Any
    min_items: int
    max_items: int
    subset_of: str | None = None   # "evidence" | "refs" : les éléments doivent exister dans le dossier


@dataclass(frozen=True)
class Obj:
    fields: dict[str, Any]


@dataclass(frozen=True)
class Dict_:
    """Objet à clés libres (paramètres d'opération), validé ensuite par le mini-langage.
    Valeurs : texte court ou objet {texte: texte} d'au plus ``max_entries`` entrées."""

    max_entries: int = 50


SCHEMAS: dict[str, Obj] = {
    SYNTHESE_GLOBALE: Obj({
        "synthese": Str(40, 900),
        "pistes": List_(Str(10, 260), 1, 5),
        "references": List_(Str(1, 20, grounded=False), 1, 20, subset_of="refs"),
    }),
    SYNTHESE_EMPLOYE: Obj({
        "synthese": Str(30, 700),
        "pistes": List_(Str(10, 260), 1, 4),
        "regroupements": List_(Obj({
            "references": List_(Str(1, 20, grounded=False), 1, 30, subset_of="refs"),
            "cause_commune": Str(10, 260),
        }), 0, 5),
        "preuves_citees": List_(Str(3, 40, grounded=False), 1, 30, subset_of="evidence"),
    }),
    TRADUCTION_REGLE: Obj({
        "operation": Enum_(RULE_OPERATIONS),
        "champ": Str(0, 60, grounded=False),
        "parametres": Dict_(),
        "reformulation": Str(10, 400),
        "confiance": Enum_(AI_CONFIDENCE),
    }),
    TRIAGE_ANOMALIE: Obj({
        "categorie": Enum_(TRIAGE_CATEGORIES),
        "justification": Str(20, 500),
        "piste": Str(10, 260),
        "preuves_citees": List_(Str(3, 40, grounded=False), 1, 20, subset_of="evidence"),
        "confiance": Enum_(AI_CONFIDENCE),
    }),
}

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"(?<![\w.-])\d{2,}(?:[.,]\d+)?(?![\w-])")
_TOKEN = re.compile(r"\b(?:NOM-|PRENOM-)?EMP-\d{2,}\b")
_QUOTED = re.compile(r"«\s*([^»]{1,80}?)\s*»")


@dataclass
class ValidationReport:
    ok: bool
    data: dict[str, Any] | None
    errors: list[str] = field(default_factory=list)


def parse_json(raw: str) -> tuple[Any, str | None]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"JSON invalide : {exc.msg} (position {exc.pos})"


def _ground(text: str, haystack: str, path: str, errors: list[str]) -> None:
    for pattern, label in ((_DATE, "date"), (_TOKEN, "employé"), (_NUMBER, "nombre")):
        for m in pattern.findall(text):
            if m not in haystack:
                errors.append(f"{path} : {label} « {m} » absent(e) du dossier (non ancré)")
    for q in _QUOTED.findall(text):
        if q.strip() and q.strip() not in haystack:
            errors.append(f"{path} : valeur citée « {q.strip()} » absente du dossier (non ancrée)")


def _check(value: Any, schema: Any, path: str, dossier: Dossier, haystack: str, errors: list[str]) -> None:
    if isinstance(schema, Obj):
        if not isinstance(value, dict):
            errors.append(f"{path or 'racine'} : objet attendu")
            return
        extra = set(value) - set(schema.fields)
        missing = set(schema.fields) - set(value)
        if extra:
            errors.append(f"{path or 'racine'} : clés interdites {sorted(extra)}")
        if missing:
            errors.append(f"{path or 'racine'} : clés manquantes {sorted(missing)}")
        for k, sub in schema.fields.items():
            if k in value:
                _check(value[k], sub, f"{path}.{k}" if path else k, dossier, haystack, errors)
    elif isinstance(schema, List_):
        if not isinstance(value, list):
            errors.append(f"{path} : liste attendue")
            return
        if not schema.min_items <= len(value) <= schema.max_items:
            errors.append(f"{path} : {len(value)} élément(s), attendu entre {schema.min_items} et {schema.max_items}")
        for i, item in enumerate(value):
            _check(item, schema.item, f"{path}[{i}]", dossier, haystack, errors)
        if schema.subset_of:
            allowed = dossier.evidence_ids if schema.subset_of == "evidence" else dossier.refs
            unknown = [v for v in value if isinstance(v, str) and v not in allowed]
            if unknown:
                errors.append(f"{path} : éléments inconnus du dossier {unknown[:5]} (hallucination)")
            if len(set(map(str, value))) != len(value):
                errors.append(f"{path} : éléments dupliqués")
    elif isinstance(schema, Str):
        if not isinstance(value, str):
            errors.append(f"{path} : texte attendu")
            return
        if not schema.min_len <= len(value.strip()) <= schema.max_len:
            errors.append(f"{path} : longueur {len(value.strip())}, attendu entre {schema.min_len} et {schema.max_len}")
        if schema.grounded:
            _ground(value, haystack, path, errors)
    elif isinstance(schema, Enum_):
        if value not in schema.values:
            errors.append(f"{path} : valeur « {value} » hors de l'énumération autorisée")
    elif isinstance(schema, Dict_):
        if not isinstance(value, dict):
            errors.append(f"{path} : objet attendu")
            return
        def short(x: Any) -> bool:
            return isinstance(x, str) and len(x) <= 200
        for k, v in value.items():
            if not short(k):
                errors.append(f"{path} : clé invalide")
            elif isinstance(v, dict):
                if len(v) > schema.max_entries or not all(short(a) and short(b) for a, b in v.items()):
                    errors.append(f"{path}.{k} : objet {{texte: texte}} d'au plus {schema.max_entries} entrées attendu")
            elif not short(v):
                errors.append(f"{path}.{k} : texte court attendu")


def validate(task: str, raw: str, dossier: Dossier) -> ValidationReport:
    if task not in SCHEMAS:
        return ValidationReport(False, None, [f"tâche inconnue : {task}"])
    data, err = parse_json(raw)
    if err:
        return ValidationReport(False, None, [err])
    errors: list[str] = []
    haystack = json.dumps(dossier.payload, ensure_ascii=False, default=str)
    _check(data, SCHEMAS[task], "", dossier, haystack, errors)
    return ValidationReport(not errors, data if not errors else None, errors)


def schema_description(task: str) -> str:
    """Description lisible du schéma, injectée dans le prompt."""
    def describe(schema: Any) -> Any:
        if isinstance(schema, Obj):
            return {k: describe(v) for k, v in schema.fields.items()}
        if isinstance(schema, List_):
            note = {"evidence": " (identifiants de preuve fournis uniquement)",
                    "refs": " (références fournies uniquement)"}.get(schema.subset_of or "", "")
            return [f"{schema.min_items} à {schema.max_items} éléments{note}", describe(schema.item)]
        if isinstance(schema, Str):
            return f"texte de {schema.min_len} à {schema.max_len} caractères"
        if isinstance(schema, Enum_):
            return "une valeur parmi : " + ", ".join(schema.values)
        if isinstance(schema, Dict_):
            return "objet de paramètres propre à l'opération (voir « operations » dans les données)"
        return str(schema)
    return json.dumps(describe(SCHEMAS[task]), ensure_ascii=False, indent=1)
