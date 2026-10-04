"""Chargement et validation du mapping codifié (``config/rules.yaml``).

Deux niveaux de validation :

1. :func:`load_rules` — cohérence **interne** de la configuration (sans
   données) : types de règles connus, criticités, interprétations
   référencées, tables de transcodage sans chevauchement, etc. Toute erreur
   lève :class:`RulesConfigError`.

2. :func:`validate_against_data` — cohérence avec les **données** et avec
   ``Mapping.xlsx`` : colonnes existantes, références de lignes exactes,
   couverture complète du mapping. Retourne erreurs et avertissements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from corroborai.io.loaders import CONFIG_DIR, ROW_COL
from corroborai.io.normalize import FieldKind, NormalizationError, norm_bool, norm_code, norm_text

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

    from corroborai.io.loaders import DataBundle

DEFAULT_RULES = CONFIG_DIR / "rules.yaml"

KNOWN_RULE_TYPES = frozenset({
    "direct",
    "direct_accent_tolerant",
    "email",
    "concat_code_label",
    "situation_reason_code",
    "situation_return_date",
    "situation_label",
    "contract_type",
    "assignment_flag",
    "assignment_start",
    "assignment_end",
})
RULES_REQUIRING_INTERPRETATION = KNOWN_RULE_TYPES - {"direct", "assignment_flag"}
REASON_CODE_MODES = frozenset({"none", "motif_remphor"})
RETURN_DATE_MODES = frozenset({"none", "source"})
REQUIRED_JOIN_KEYS = {
    "poste_detail": {"poste", "emploi", "unite_admin", "date_effet"},
    "motifs": {"situation", "remphor", "acces"},
}
MAPPING_SHEET = "Mapping"
_REF = re.compile(r"^(?P<sheet>.+)!(?P<row>\d+)$")


class RulesConfigError(ValueError):
    """Configuration de règles invalide."""


# --------------------------------------------------------------------------- modèle

@dataclass(frozen=True)
class MappingRef:
    sheet: str
    row: int

    @classmethod
    def parse(cls, text: str) -> MappingRef:
        m = _REF.match(str(text or ""))
        if not m:
            raise RulesConfigError(f"Référence de mapping invalide : {text!r} (attendu « Feuille!ligne »)")
        return cls(m["sheet"], int(m["row"]))

    def __str__(self) -> str:
        return f"{self.sheet}!{self.row}"


@dataclass(frozen=True)
class Interpretation:
    id: str
    choice: str
    alternatives: tuple[str, ...]
    summary: str
    rationale: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldRule:
    target: str
    sources: tuple[str, ...]
    kind: FieldKind
    rule: str
    rule_id: str
    criticality: int
    criticality_reason: str
    mapping_ref: MappingRef
    related_refs: tuple[MappingRef, ...] = ()
    interpretation: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContractType:
    when: dict[str, Any]
    code: str
    label: str

    def matches(self, row: dict[str, Any]) -> bool:
        for col, expected in self.when.items():
            value = row.get(col)
            try:
                actual = norm_bool(value) if isinstance(expected, bool) else norm_code(value)
            except NormalizationError:
                return False
            if actual != expected:
                return False
        return True


@dataclass(frozen=True)
class Situation:
    codes: frozenset[int]
    label: str
    reason_code: str
    return_date: str
    mapping_ref: MappingRef


@dataclass(frozen=True)
class JoinColumn:
    key: str
    table: str
    source: str | None
    label: str
    excel_serial: bool = False


@dataclass(frozen=True)
class Join:
    name: str
    mapping_ref: MappingRef
    columns: dict[str, JoinColumn]

    def col(self, key: str) -> JoinColumn:
        return self.columns[key]


@dataclass(frozen=True)
class ExcludedRow:
    source: str | None
    target: str | None
    mapping_ref: MappingRef
    reason: str


@dataclass(frozen=True)
class SupportColumn:
    column: str
    mapping_ref: MappingRef
    role: str


@dataclass(frozen=True)
class RulesConfig:
    version: int
    fields: tuple[FieldRule, ...]
    interpretations: dict[str, Interpretation]
    contract_types: tuple[ContractType, ...]
    situations: tuple[Situation, ...]
    assignment_types: dict[str, dict[str, Any]]
    joins: dict[str, Join]
    source_label_aliases: dict[str, str | None]
    excluded: tuple[ExcludedRow, ...]
    support_columns: tuple[SupportColumn, ...]
    unmapped_target_notes: dict[str, str]
    path: Path | None = None

    def field(self, target: str) -> FieldRule:
        for f in self.fields:
            if f.target == target:
                return f
        raise KeyError(target)

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(f.target for f in self.fields)

    def interpretation_for(self, f: FieldRule) -> Interpretation | None:
        return self.interpretations.get(f.interpretation) if f.interpretation else None

    def situation_for(self, code: Any) -> Situation | None:
        c = norm_code(code)
        if c is None or not c.isdigit():
            return None
        n = int(c)
        return next((s for s in self.situations if n in s.codes), None)

    def contract_type_for(self, row: dict[str, Any]) -> ContractType | None:
        hits = [ct for ct in self.contract_types if ct.matches(row)]
        return hits[0] if hits else None

    def assignment_type_for(self, primary: bool | None, temporary: bool | None) -> str | None:
        for code, flags in self.assignment_types.items():
            if flags["isPrimaryAssignment"] is primary and flags["isTemporaryAssignment"] is temporary:
                return code
        return None


# --------------------------------------------------------------------------- chargement

def load_rules(path: str | Path | None = None) -> RulesConfig:
    path = Path(path) if path else DEFAULT_RULES
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise RulesConfigError("rules.yaml : document racine invalide")
    cfg = _build(raw, path)
    _validate_structure(cfg)
    return cfg


def _req(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise RulesConfigError(f"{ctx} : clé obligatoire manquante « {key} »")
    return d[key]


def _build(raw: dict[str, Any], path: Path) -> RulesConfig:
    interpretations = {}
    for iid, spec in (raw.get("interpretations") or {}).items():
        ctx = f"interprétation {iid}"
        interpretations[iid] = Interpretation(
            id=iid,
            choice=str(_req(spec, "choice", ctx)),
            alternatives=tuple(spec.get("alternatives") or ()),
            summary=str(_req(spec, "summary", ctx)).strip(),
            rationale=str(_req(spec, "rationale", ctx)).strip(),
            params=dict(spec.get("params") or {}),
        )

    fields = []
    for i, spec in enumerate(raw.get("fields") or []):
        ctx = f"fields[{i}] ({spec.get('target', '?')})"
        try:
            kind = FieldKind(_req(spec, "kind", ctx))
        except ValueError as exc:
            raise RulesConfigError(f"{ctx} : kind inconnu « {spec.get('kind')} »") from exc
        fields.append(FieldRule(
            target=str(_req(spec, "target", ctx)),
            sources=tuple(_req(spec, "sources", ctx)),
            kind=kind,
            rule=str(_req(spec, "rule", ctx)),
            rule_id=str(_req(spec, "rule_id", ctx)),
            criticality=_req(spec, "criticality", ctx),
            criticality_reason=str(_req(spec, "criticality_reason", ctx)).strip(),
            mapping_ref=MappingRef.parse(_req(spec, "mapping_ref", ctx)),
            related_refs=tuple(MappingRef.parse(r) for r in spec.get("related_refs") or ()),
            interpretation=spec.get("interpretation"),
            params=dict(spec.get("params") or {}),
        ))

    contract_types = tuple(
        ContractType(when=dict(_req(s, "when", f"contract_types[{i}]")),
                     code=str(_req(s, "code", f"contract_types[{i}]")),
                     label=str(s.get("label", "")))
        for i, s in enumerate(raw.get("contract_types") or [])
    )

    situations = tuple(
        Situation(codes=frozenset(int(c) for c in _req(s, "codes", f"situations[{i}]")),
                  label=str(_req(s, "label", f"situations[{i}]")),
                  reason_code=str(_req(s, "reason_code", f"situations[{i}]")),
                  return_date=str(_req(s, "return_date", f"situations[{i}]")),
                  mapping_ref=MappingRef.parse(_req(s, "mapping_ref", f"situations[{i}]")))
        for i, s in enumerate(raw.get("situations") or [])
    )

    joins = {}
    for name, spec in (raw.get("joins") or {}).items():
        cols = {
            key: JoinColumn(key=key, table=str(_req(c, "table", f"joins.{name}.{key}")),
                            source=c.get("source"), label=str(c.get("label", "")),
                            excel_serial=bool(c.get("excel_serial", False)))
            for key, c in (spec.get("columns") or {}).items()
        }
        joins[name] = Join(name, MappingRef.parse(_req(spec, "mapping_ref", f"joins.{name}")), cols)

    excluded = tuple(
        ExcludedRow(source=e.get("source"), target=e.get("target"),
                    mapping_ref=MappingRef.parse(_req(e, "mapping_ref", f"excluded[{i}]")),
                    reason=str(_req(e, "reason", f"excluded[{i}]")))
        for i, e in enumerate(raw.get("excluded") or [])
    )
    support = tuple(
        SupportColumn(column=str(_req(s, "column", f"support_columns[{i}]")),
                      mapping_ref=MappingRef.parse(_req(s, "mapping_ref", f"support_columns[{i}]")),
                      role=str(_req(s, "role", f"support_columns[{i}]")))
        for i, s in enumerate(raw.get("support_columns") or [])
    )

    return RulesConfig(
        version=raw.get("version"),
        fields=tuple(fields),
        interpretations=interpretations,
        contract_types=contract_types,
        situations=situations,
        assignment_types=dict(raw.get("assignment_types") or {}),
        joins=joins,
        source_label_aliases=dict(raw.get("source_label_aliases") or {}),
        excluded=excluded,
        support_columns=support,
        unmapped_target_notes=dict(raw.get("unmapped_target_notes") or {}),
        path=path,
    )


# --------------------------------------------------------------------------- validation interne

def _validate_structure(cfg: RulesConfig) -> None:
    errors: list[str] = []

    if cfg.version != 1:
        errors.append(f"version non supportée : {cfg.version!r}")
    if not cfg.fields:
        errors.append("aucun champ déclaré")

    seen: set[str] = set()
    for f in cfg.fields:
        ctx = f"champ {f.target}"
        if f.target in seen:
            errors.append(f"{ctx} : déclaré plusieurs fois")
        seen.add(f.target)
        if f.rule not in KNOWN_RULE_TYPES:
            errors.append(f"{ctx} : type de règle inconnu « {f.rule} »")
        if not isinstance(f.criticality, int) or isinstance(f.criticality, bool) or not 1 <= f.criticality <= 5:
            errors.append(f"{ctx} : criticité hors de 1–5 ({f.criticality!r})")
        if not f.sources:
            errors.append(f"{ctx} : aucune colonne source")
        if f.rule in RULES_REQUIRING_INTERPRETATION and not f.interpretation:
            errors.append(f"{ctx} : la règle « {f.rule} » exige une interprétation documentée")
        if f.interpretation and f.interpretation not in cfg.interpretations:
            errors.append(f"{ctx} : interprétation inconnue « {f.interpretation} »")
        if f.rule == "concat_code_label":
            pw = f.params.get("pad_width")
            if not isinstance(pw, int) or pw < 0 or not isinstance(f.params.get("separator"), str):
                errors.append(f"{ctx} : concat_code_label exige pad_width (entier ≥ 0) et separator")

    for i in cfg.interpretations.values():
        if i.choice in i.alternatives:
            errors.append(f"interprétation {i.id} : le choix figure aussi dans les alternatives")
    email = cfg.interpretations.get("INT-EMAIL")
    if email:
        for key in ("domain", "digits_from_id", "env_prefix_pattern"):
            if key not in email.params:
                errors.append(f"INT-EMAIL : paramètre manquant « {key} »")
        try:
            re.compile(str(email.params.get("env_prefix_pattern", "")))
        except re.error as exc:
            errors.append(f"INT-EMAIL : env_prefix_pattern invalide ({exc})")

    codes = [ct.code for ct in cfg.contract_types]
    if len(codes) != len(set(codes)):
        errors.append("contract_types : codes cibles dupliqués")
    for a, b in combinations(cfg.contract_types, 2):
        if _conditions_overlap(a.when, b.when):
            errors.append(f"contract_types : conditions qui se chevauchent ({a.code} / {b.code})")

    for a, b in combinations(cfg.situations, 2):
        if a.codes & b.codes:
            errors.append(f"situations : codes partagés {sorted(a.codes & b.codes)}")
    for s in cfg.situations:
        if s.reason_code not in REASON_CODE_MODES:
            errors.append(f"situation {s.label} : reason_code inconnu « {s.reason_code} »")
        if s.return_date not in RETURN_DATE_MODES:
            errors.append(f"situation {s.label} : return_date inconnu « {s.return_date} »")

    flags_seen = set()
    for code, flags in cfg.assignment_types.items():
        pair = (flags.get("isPrimaryAssignment"), flags.get("isTemporaryAssignment"))
        if not all(isinstance(v, bool) for v in pair):
            errors.append(f"assignment_types.{code} : indicateurs booléens requis")
        if pair in flags_seen:
            errors.append(f"assignment_types.{code} : combinaison d'indicateurs dupliquée")
        flags_seen.add(pair)

    for name, keys in REQUIRED_JOIN_KEYS.items():
        if name not in cfg.joins:
            errors.append(f"jointure manquante : {name}")
        elif missing := keys - set(cfg.joins[name].columns):
            errors.append(f"jointure {name} : colonnes manquantes {sorted(missing)}")

    if errors:
        raise RulesConfigError("rules.yaml invalide :\n  - " + "\n  - ".join(errors))


def _conditions_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Deux conditions se chevauchent si aucune clé commune n'a de valeurs différentes."""
    return all(a[k] == b[k] for k in a.keys() & b.keys())


# --------------------------------------------------------------------------- validation vs données

@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _mapping_columns(df: pd.DataFrame) -> tuple[str, str]:
    """Retrouve les colonnes « Système A » et « Système B » malgré les retours de ligne."""
    def find(token: str) -> str:
        for c in df.columns:
            if token in " ".join(str(c).split()):
                return c
        raise RulesConfigError(f"Colonne « {token} » introuvable dans la feuille {MAPPING_SHEET}")
    return find("Système A"), find("Système B")


def _row(bundle: DataBundle, ref: MappingRef) -> dict[str, Any] | None:
    df = bundle.mapping.get(ref.sheet)
    if df is None:
        return None
    hit = df[df[ROW_COL] == ref.row]
    if hit.empty:
        return None
    row = hit.iloc[0].to_dict()
    # pandas conserve les lignes vides intercalées : une ligne sans contenu est inexistante
    if all(norm_text(v) is None for k, v in row.items() if k != ROW_COL):
        return None
    return row


def _target_tokens(cell: Any) -> list[str]:
    text = norm_text(cell)
    if not text or text == "-":
        return []
    return [t.strip() for t in text.split(" et ") if t.strip()]


def validate_against_data(cfg: RulesConfig, bundle: DataBundle) -> ValidationResult:
    res = ValidationResult()
    src_cols = set(bundle.source.df.columns)
    tgt_cols = set(bundle.target.df.columns)
    tables = {"poste_detail": bundle.poste_detail.df, "motifs": bundle.motifs.df}

    # Colonnes des champs
    for f in cfg.fields:
        for s in f.sources:
            if s not in src_cols:
                res.errors.append(f"{f.target} : colonne source absente « {s} »")
        if f.target not in tgt_cols:
            res.errors.append(f"{f.target} : colonne cible absente")
    for ct in cfg.contract_types:
        for col in ct.when:
            if col not in src_cols:
                res.errors.append(f"contract_types {ct.code} : colonne source absente « {col} »")

    # Jointures
    for name, join in cfg.joins.items():
        table = tables.get(name)
        if table is None:
            res.errors.append(f"jointure {name} : table inconnue")
            continue
        for jc in join.columns.values():
            if jc.table not in table.columns:
                res.errors.append(f"jointure {name}.{jc.key} : colonne « {jc.table} » absente de la table")
            if jc.source and jc.source not in src_cols:
                res.errors.append(f"jointure {name}.{jc.key} : colonne source « {jc.source} » absente")
    motifs = cfg.joins.get("motifs")
    if motifs and motifs.col("situation").table in bundle.motifs.df.columns:
        if not bundle.motifs.df[motifs.col("situation").table].is_unique:
            res.errors.append("jointure motifs : la clé de situation n'est pas unique")

    # Références vers Mapping.xlsx
    refs: list[tuple[str, MappingRef]] = []
    for f in cfg.fields:
        refs.append((f.target, f.mapping_ref))
        refs.extend((f"{f.target} (lié)", r) for r in f.related_refs)
    refs.extend((f"situation {s.label}", s.mapping_ref) for s in cfg.situations)
    refs.extend((f"jointure {j.name}", j.mapping_ref) for j in cfg.joins.values())
    refs.extend((f"exclusion {e.source}", e.mapping_ref) for e in cfg.excluded)
    refs.extend((f"support {s.column}", s.mapping_ref) for s in cfg.support_columns)
    for label, ref in refs:
        if ref.sheet not in bundle.mapping:
            res.errors.append(f"{label} : feuille inconnue « {ref.sheet} »")
        elif _row(bundle, ref) is None:
            res.errors.append(f"{label} : ligne {ref} vide ou inexistante")

    sheet = bundle.mapping.get(MAPPING_SHEET)
    if sheet is None:
        res.errors.append(f"feuille « {MAPPING_SHEET} » absente de Mapping.xlsx")
        return res
    col_a, col_b = _mapping_columns(sheet)

    def resolve_label(label: str) -> tuple[bool, str | None]:
        """(connu?, colonne réelle)"""
        if label in src_cols:
            return True, label
        if label in cfg.source_label_aliases:
            return True, cfg.source_label_aliases[label]
        return False, None

    # Chaque champ pointe vers une ligne qui le mentionne
    for f in cfg.fields:
        if f.mapping_ref.sheet != MAPPING_SHEET:
            continue
        row = _row(bundle, f.mapping_ref)
        if row is not None and f.target not in _target_tokens(row.get(col_b)):
            res.errors.append(f"{f.target} : la ligne {f.mapping_ref} ne mentionne pas ce champ "
                              f"(trouvé : {norm_text(row.get(col_b))!r})")

    # Couverture : chaque champ cible du mapping est corroboré ou exclu
    mapped_targets: dict[str, int] = {}
    for _, r in sheet.iterrows():
        for t in _target_tokens(r[col_b]):
            mapped_targets.setdefault(t, int(r[ROW_COL]))
    declared = set(cfg.targets) | {e.target for e in cfg.excluded if e.target}
    for t, row_no in mapped_targets.items():
        if t not in declared:
            res.errors.append(f"champ cible « {t} » ({MAPPING_SHEET}!{row_no}) ni corroboré ni exclu")

    # Chaque libellé source du mapping est une colonne réelle ou un alias déclaré
    for _, r in sheet.iterrows():
        label = norm_text(r[col_a])
        if not label:
            continue
        known, real = resolve_label(label)
        if not known:
            res.errors.append(f"libellé source « {label} » ({MAPPING_SHEET}!{r[ROW_COL]}) inconnu")
        elif real and real not in src_cols:
            res.errors.append(f"alias « {label} » → « {real} » : colonne absente")

    # Exclusions et colonnes de support : la ligne référencée correspond
    for item, col in [(e, e.source) for e in cfg.excluded] + [(s, s.column) for s in cfg.support_columns]:
        row = _row(bundle, item.mapping_ref)
        if row is None or col is None:
            continue
        _, real = resolve_label(norm_text(row.get(col_a)) or "")
        if real != col:
            res.errors.append(f"{item.mapping_ref} : attendu « {col} », trouvé « {real} »")

    # Avertissements : valeurs des données non couvertes par les tables de règles
    sit_col = next((f.sources[0] for f in cfg.fields if f.rule == "situation_label"), None)
    if sit_col in src_cols:
        for v in sorted({norm_code(v) for v in bundle.source.df[sit_col] if norm_code(v)}):
            if cfg.situation_for(v) is None:
                res.warnings.append(f"code de situation « {v} » absent de la table : verdicts INDETERMINE")
    for rec in bundle.source.df.to_dict("records"):
        if cfg.contract_type_for(rec) is None:
            res.warnings.append(f"type d'employé non couvert (ligne source {rec[ROW_COL]}) : verdict INDETERMINE")

    return res
