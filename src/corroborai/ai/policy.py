"""Configuration LLM, pseudonymisation, dossiers classifiés et politique de flux.

Principe (contrôle de flux d'information) : chaque élément envoyé à un LLM
porte une **classe de donnée**. Avant tout envoi, une fonction pure vérifie
que chaque classe est autorisée pour la catégorie du fournisseur (« local » ou
« externe »), puis — pour un fournisseur externe — balaie la charge sérialisée
à la recherche de tout identifiant brut connu ou de tout motif d'identifiant.
Une violation bloque l'appel : le harnais bascule sur le gabarit déterministe.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from corroborai.io.loaders import CONFIG_DIR, DataBundle

DEFAULT_LLM = CONFIG_DIR / "llm.yaml"


class LLMConfigError(ValueError):
    """Configuration LLM invalide."""


class DataClass(str, Enum):
    IDENTIFIANT = "IDENTIFIANT"                            # identifiant personnel brut — jamais envoyé
    IDENTIFIANT_PSEUDONYMISE = "IDENTIFIANT_PSEUDONYMISE"  # jeton opaque (EMP-03)
    VALEUR_METIER = "VALEUR_METIER"                        # code, date, heures…
    TEXTE_REGLE = "TEXTE_REGLE"                            # justification, cause, description de règle
    PREUVE = "PREUVE"                                      # identifiant de preuve (table:ligne)
    META = "META"                                          # nom de champ, verdict, priorité…


class ProviderClass(str, Enum):
    LOCAL = "local"
    EXTERNE = "externe"


# --------------------------------------------------------------------------- configuration

@dataclass(frozen=True)
class ProviderSpec:
    name: str
    type: str
    data_class: ProviderClass
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    timeout_s: float = 60

    @property
    def effective_class(self) -> ProviderClass:
        """Un fournisseur déclaré local mais joignable hors bouclage est traité comme externe."""
        if self.data_class is ProviderClass.EXTERNE or not self.base_url:
            return self.data_class
        host = urlparse(self.base_url).hostname or ""
        if host == "localhost":
            return ProviderClass.LOCAL
        try:
            return ProviderClass.LOCAL if ipaddress.ip_address(host).is_loopback else ProviderClass.EXTERNE
        except ValueError:
            return ProviderClass.EXTERNE


@dataclass(frozen=True)
class HarnessSettings:
    max_attempts: int
    temperature: float
    max_output_tokens: int
    synthesis_min_priority: float
    exclude_systemic_from_employee: bool


@dataclass(frozen=True)
class FlowPolicy:
    allowed: dict[ProviderClass, frozenset[DataClass]]
    external_leak_scan: bool
    identifier_patterns: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class LLMConfig:
    default_provider: str
    providers: dict[str, ProviderSpec]
    harness: HarnessSettings
    flow: FlowPolicy
    path: Path | None = None

    def provider(self, name: str | None = None) -> ProviderSpec:
        name = name or self.default_provider
        if name not in self.providers:
            raise LLMConfigError(f"Fournisseur inconnu : {name} (possibles : {', '.join(self.providers)})")
        return self.providers[name]


PROVIDER_TYPES = frozenset({"template", "gemini", "openai_compat"})


def load_llm_config(path: str | Path | None = None) -> LLMConfig:
    path = Path(path) if path else DEFAULT_LLM
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    errors: list[str] = []
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise LLMConfigError("llm.yaml : document invalide ou version non supportée")

    providers = {}
    for name, spec in (raw.get("providers") or {}).items():
        spec = spec or {}
        if spec.get("type") not in PROVIDER_TYPES:
            errors.append(f"fournisseur {name} : type inconnu « {spec.get('type')} »")
            continue
        try:
            dclass = ProviderClass(spec.get("data_class"))
        except ValueError:
            errors.append(f"fournisseur {name} : data_class doit être local ou externe")
            continue
        if spec["type"] == "gemini" and dclass is not ProviderClass.EXTERNE:
            errors.append(f"fournisseur {name} : un service infonuagique doit être déclaré externe")
        if spec["type"] != "template" and not (spec.get("model") and spec.get("base_url")):
            errors.append(f"fournisseur {name} : model et base_url obligatoires")
        providers[name] = ProviderSpec(name, spec["type"], dclass, spec.get("model"), spec.get("base_url"),
                                       spec.get("api_key_env"), float(spec.get("timeout_s", 60)))
    if raw.get("default_provider") not in providers:
        errors.append(f"default_provider inconnu : {raw.get('default_provider')!r}")
    if not any(p.type == "template" for p in providers.values()):
        errors.append("un fournisseur de type template (repli déterministe) est obligatoire")

    h = raw.get("harness") or {}
    attempts = h.get("max_attempts")
    if not isinstance(attempts, int) or not 1 <= attempts <= 3:
        errors.append("harness.max_attempts doit être un entier entre 1 et 3")
    temp = h.get("temperature", 0)
    if not isinstance(temp, (int, float)) or not 0 <= temp <= 1:
        errors.append("harness.temperature doit être dans [0, 1]")

    fp = raw.get("flow_policy") or {}
    allowed: dict[ProviderClass, frozenset[DataClass]] = {}
    for cls in ProviderClass:
        try:
            allowed[cls] = frozenset(DataClass(c) for c in (fp.get("allowed") or {}).get(cls.value, []))
        except ValueError as exc:
            errors.append(f"flow_policy.allowed.{cls.value} : {exc}")
            allowed[cls] = frozenset()
    for cls, classes in allowed.items():
        if DataClass.IDENTIFIANT in classes and cls is ProviderClass.EXTERNE:
            errors.append("flow_policy : la classe IDENTIFIANT ne peut jamais être autorisée vers un fournisseur externe")
    patterns = []
    for p in fp.get("identifier_patterns") or []:
        try:
            patterns.append(re.compile(p))
        except re.error as exc:
            errors.append(f"identifier_patterns : motif invalide {p!r} ({exc})")

    if errors:
        raise LLMConfigError("llm.yaml invalide :\n  - " + "\n  - ".join(errors))
    return LLMConfig(
        default_provider=raw["default_provider"],
        providers=providers,
        harness=HarnessSettings(attempts, float(temp), int(h.get("max_output_tokens", 1024)),
                                float(h.get("synthesis_min_priority", 30)),
                                bool(h.get("exclude_systemic_from_employee", True))),
        flow=FlowPolicy(allowed, bool(fp.get("external_leak_scan", True)), tuple(patterns)),
        path=path,
    )


# --------------------------------------------------------------------------- pseudonymisation

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"\b\d{7,}\b")


class Pseudonymizer:
    """Remplace les identifiants personnels par des jetons opaques réversibles localement.

    Construit à partir des données chargées : matricules, noms, prénoms,
    courriels et responsables. Le remplacement est fait du plus long au plus
    court pour éviter les remplacements partiels (un nom anonymisé peut
    contenir un matricule).
    """

    def __init__(self, persons: list[str], extra: dict[str, list[str]] | None = None) -> None:
        self.token_of: dict[str, str] = {}
        self.real_of: dict[str, str] = {}
        for i, pid in enumerate(sorted(set(persons)), start=1):
            self._add(pid, f"EMP-{i:02d}")
        self.secrets: set[str] = set(self.token_of)
        for kind, values in (extra or {}).items():
            for v in sorted(set(values)):
                if v and len(v) >= 3 and v not in self.token_of:
                    self.secrets.add(v)

    def _add(self, real: str, token: str) -> None:
        self.token_of[real] = token
        self.real_of[token] = real

    @classmethod
    def from_bundle(cls, bundle: DataBundle) -> Pseudonymizer:
        src, tgt = bundle.source.df, bundle.target.df
        persons = [str(v) for v in list(src["Matricule"]) + list(tgt["personId"]) if v]
        extra: dict[str, list[str]] = {}
        for col in ("NomFamille", "PrénomUsuel", "IdentifiantResponsable", "NomResponsable"):
            if col in src.columns:
                extra[col] = [str(v) for v in src[col] if v]
        for col in ("givenName", "surname", "contactEmail"):
            if col in tgt.columns:
                extra[col] = [str(v) for v in tgt[col] if v]
        p = cls(persons, extra)
        # Les noms reçoivent un jeton dérivé de l'employé (lisibilité des synthèses)
        for rec in src.to_dict("records"):
            tok = p.token_of.get(str(rec.get("Matricule")))
            if tok:
                for col, label in (("NomFamille", "NOM"), ("PrénomUsuel", "PRENOM")):
                    v = rec.get(col)
                    if v and str(v) not in p.token_of:
                        p._add(str(v), f"{label}-{tok}")
        return p

    def text(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        out = _EMAIL.sub("[courriel]", value)
        for real in sorted(self.token_of, key=len, reverse=True):
            if real in out:
                out = re.sub(rf"(?<![\w-]){re.escape(real)}(?![\w-])", self.token_of[real], out)
        for secret in sorted(self.secrets - set(self.token_of), key=len, reverse=True):
            out = out.replace(secret, "[masqué]")
        return _LONG_NUMBER.sub("[identifiant]", out)

    def person(self, pid: str) -> str:
        return self.token_of.get(pid, "EMP-??")

    def restore(self, value: Any) -> Any:
        """Réidentification locale (après validation) pour l'affichage dans le rapport."""
        if isinstance(value, str):
            for tok in sorted(self.real_of, key=len, reverse=True):
                value = value.replace(tok, self.real_of[tok])
            return value
        if isinstance(value, list):
            return [self.restore(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore(v) for k, v in value.items()}
        return value


# --------------------------------------------------------------------------- dossier classifié

@dataclass
class Dossier:
    """Charge destinée au LLM.

    ``key_classes`` associe chaque clé de la charge à une classe de donnée ;
    les éléments d'une liste héritent de la classe de leur clé. Une clé sans
    classe déclarée bloque l'envoi (politique fermée par défaut).
    """

    task: str
    payload: dict[str, Any]
    key_classes: dict[str, DataClass]
    evidence_ids: set[str] = field(default_factory=set)
    refs: set[str] = field(default_factory=set)

    def leaves(self) -> list[tuple[str, str]]:
        """(chemin, clé porteuse) de chaque valeur feuille de la charge."""
        out: list[tuple[str, str]] = []

        def walk(node: Any, path: str, key: str) -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else k, k)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]", key)
            else:
                out.append((path, key))
        walk(self.payload, "", "")
        return out

    def serialize(self) -> str:
        """JSON déterministe ; « < » et « > » échappés pour empêcher toute sortie de la balise de données."""
        text = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, indent=1, default=str)
        return text.replace("<", "\\u003c").replace(">", "\\u003e")


@dataclass
class FlowDecision:
    allowed: bool
    provider_class: ProviderClass
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return "AUTORISÉ" if self.allowed else "BLOQUÉ : " + "; ".join(self.reasons)


def check_flow(dossier: Dossier, provider: ProviderSpec, policy: FlowPolicy,
               pseudo: Pseudonymizer | None) -> FlowDecision:
    """Décision de flux déterministe, évaluée avant tout envoi."""
    pclass = provider.effective_class
    decision = FlowDecision(True, pclass)
    allowed = policy.allowed[pclass]
    for path, key in dossier.leaves():
        cls = dossier.key_classes.get(key)
        if cls is None:
            decision.allowed = False
            decision.reasons.append(f"élément non classifié ({path}) : envoi refusé par défaut")
        elif cls not in allowed:
            decision.allowed = False
            decision.reasons.append(f"classe {cls.value} interdite vers un fournisseur {pclass.value} ({path})")
    if pclass is ProviderClass.EXTERNE and policy.external_leak_scan:
        text = dossier.serialize()
        if pseudo is not None:
            leaked = sorted({s for s in pseudo.secrets if s in text})
            if leaked:
                decision.allowed = False
                # Les valeurs elles-mêmes ne sont jamais journalisées
                decision.reasons.append(f"{len(leaked)} identifiant(s) brut(s) détecté(s) dans la charge")
        for pat in policy.identifier_patterns:
            if pat.search(text):
                decision.allowed = False
                decision.reasons.append(f"motif d'identifiant détecté ({pat.pattern})")
    return decision
