"""Moteur d'hypothèses : analyse abductive déterministe des écarts.

Pour chaque verdict ANOMALIE ou INDETERMINE, un catalogue FERMÉ d'hypothèses
génériques est testé. Une hypothèse vérifiée ajoute des preuves, une cause
probable et alimente le score de priorité. Elle ne modifie jamais le verdict.

Les hypothèses de niveau « champ » (systémique, bijection, source alternative)
s'appuient sur l'ensemble de la population : une source alternative n'est
retenue que si elle concorde avec la cible sur au moins
``alt_source_min_support`` des enregistrements — ce qui écarte les
coïncidences et en fait une **règle candidate** soumise à l'expert.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from corroborai.analysis.config import HypothesesConfig
from corroborai.io.loaders import ROW_COL
from corroborai.io.normalize import FieldKind, NormalizationError, norm_code, normalize
from corroborai.matching import AssignmentPair, MatchMethod
from corroborai.models import Evidence, Finding, HypothesisResult, Verdict
from corroborai.rules.base import PosteRecord, Refs, show
from corroborai.rules_config import RulesConfig

INVESTIGATE = (Verdict.ANOMALIE, Verdict.INDETERMINE)
AGREEING = (Verdict.CONFORME, Verdict.JUSTIFIE)
DATE_RULES = ("assignment_start", "assignment_end")


@dataclass(frozen=True)
class CandidateRule:
    """Règle alternative suggérée par les données, soumise à validation experte."""

    target_field: str
    column: str
    support: float          # part de la population où cible = colonne candidate
    population: int
    mapped_support: float   # part de la population conforme à la règle actuelle
    explains: tuple[str, ...]  # identifiants des anomalies expliquées

    @property
    def description(self) -> str:
        return (f"« {self.target_field} » semble alimenté par {self.column} "
                f"(concordance {self.support:.0%} sur {self.population} enregistrements, contre "
                f"{self.mapped_support:.0%} pour la règle actuelle ; explique {len(self.explains)} anomalie(s)).")


@dataclass(frozen=True)
class Pattern:
    """Motif observé sur plusieurs verdicts (vue d'ensemble pour l'investigation)."""

    hypothesis_id: str
    target_field: str
    count: int
    description: str
    finding_ids: tuple[str, ...]


@dataclass
class AnalysisResult:
    candidate_rules: list[CandidateRule] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    tested: tuple[str, ...] = ()


class HypothesisEngine:
    def __init__(self, cfg: RulesConfig, hcfg: HypothesesConfig, refs: Refs,
                 pairs: list[AssignmentPair], findings: list[Finding]) -> None:
        self.cfg, self.hcfg, self.refs = cfg, hcfg, refs
        self.pairs, self.findings = pairs, findings
        self.pair_by_key = {(p.person_id, p.key): p for p in pairs}
        self.by_field: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            self.by_field[f.target_field].append(f)
        self.result = AnalysisResult(tested=tuple(hcfg.precedence))

    # ------------------------------------------------------------------ orchestration

    def run(self) -> AnalysisResult:
        for target_field, fs in sorted(self.by_field.items()):
            anomalies = [f for f in fs if f.verdict in INVESTIGATE]
            if not anomalies:
                continue
            self._systemic(target_field, fs, anomalies)
            self._bijection(target_field, anomalies)
            self._permutation(target_field, anomalies)
            self._alt_source(target_field, fs, anomalies)
            self._history_record(target_field, anomalies)
            self._alt_interpretation(anomalies)
        self._type_absent()
        for f in self.findings:
            if f.verdict in INVESTIGATE:
                f.rule_params["hypothèses testées"] = ", ".join(self.hcfg.precedence)
                f.probable_cause = self._probable_cause(f)
        return self.result

    def _attach(self, f: Finding, hid: str, description: str, evidence: list[Evidence] = (),
                systemic: bool = False, support: float | None = None) -> None:
        for ev in evidence:
            if all(e.id != ev.id for e in f.evidence):
                f.add_evidence(ev)
        ids = tuple(ev.id for ev in evidence)
        f.hypotheses.append(HypothesisResult(hid, description, True, ids, systemic, support))

    def _probable_cause(self, f: Finding) -> str | None:
        verified = {h.hypothesis_id: h for h in f.hypotheses if h.verified}
        for hid in self.hcfg.precedence:
            if hid in verified:
                return f"{self.hcfg.hypotheses[hid].label} : {verified[hid].description}"
        return None

    def _kind(self, target_field: str) -> FieldKind:
        try:
            return self.cfg.field(target_field).kind
        except KeyError:
            return FieldKind.TEXT

    # ------------------------------------------------------------------ hypothèses de champ

    def _systemic(self, target_field: str, fs: list[Finding], anomalies: list[Finding]) -> None:
        n, k = len(fs), len(anomalies)
        share = k / n if n else 0.0
        if k < self.hcfg.systemic_min_count or share < self.hcfg.systemic_min_share:
            return
        subcats = Counter(f.subcategory or "—" for f in anomalies)
        main, main_n = subcats.most_common(1)[0]
        desc = (f"{k} enregistrements sur {n} ({share:.0%}) sont en écart pour ce champ "
                f"(type dominant : {main}, {main_n}/{k}).")
        for f in anomalies:
            self._attach(f, "H-SYSTEMIC", desc, systemic=True, support=share)
        self.result.patterns.append(Pattern("H-SYSTEMIC", target_field, k, desc,
                                            tuple(f.finding_id for f in anomalies)))

    def _bijection(self, target_field: str, anomalies: list[Finding]) -> None:
        usable = [f for f in anomalies if f.expected is not None and f.target_norm is not None]
        pairs = [(str(f.expected), str(f.target_norm)) for f in usable]
        if len(pairs) < self.hcfg.bijection_min_count:
            return
        fwd: dict[str, set[str]] = defaultdict(set)
        bwd: dict[str, set[str]] = defaultdict(set)
        for e, t in pairs:
            fwd[e].add(t)
            bwd[t].add(e)
        functional = all(len(v) == 1 for v in fwd.values()) and all(len(v) == 1 for v in bwd.values())
        # Sans valeur attendue partagée par des employés DIFFÉRENTS, une bijection est
        # triviale (chaque employé a sa propre valeur) et ne prouve aucun recodage.
        persons: dict[str, set[str]] = defaultdict(set)
        for f in usable:
            persons[str(f.expected)].add(f.person_id)
        repeated = any(len(p) > 1 for p in persons.values())
        if not (functional and repeated):
            return
        examples = ", ".join(f"{show(e)} → {show(next(iter(t)))}" for e, t in sorted(fwd.items())[:3])
        desc = (f"Correspondance un-pour-un stable sur {len(pairs)} écarts : {len(fwd)} valeurs attendues "
                f"distinctes recodées de façon constante (ex. {examples}).")
        for f in anomalies:
            if f.expected is not None and f.target_norm is not None:
                self._attach(f, "H-BIJECTION", desc, systemic=True, support=1.0)
        self.result.patterns.append(Pattern("H-BIJECTION", target_field, len(pairs), desc,
                                            tuple(f.finding_id for f in anomalies)))

    def _permutation(self, target_field: str, anomalies: list[Finding]) -> None:
        cands = [f for f in anomalies if f.verdict is Verdict.ANOMALIE
                 and f.expected is not None and f.target_norm is not None and f.expected != f.target_norm]
        cands.sort(key=lambda f: f.finding_id)
        used: set[str] = set()
        swaps = []
        for i, a in enumerate(cands):
            if a.finding_id in used:
                continue
            for b in cands[i + 1:]:
                if (b.finding_id not in used and b.person_id != a.person_id
                        and a.target_norm == b.expected and b.target_norm == a.expected):
                    used |= {a.finding_id, b.finding_id}
                    swaps.append((a, b))
                    break
        for a, b in swaps:
            for x, y in ((a, b), (b, a)):
                ev = [e for e in y.evidence if e.table in ("source", "target")]
                desc = (f"Valeur cible {show(x.target_norm)} = valeur attendue de l'employé {y.person_id} ; "
                        f"sa valeur cible {show(y.target_norm)} = la valeur attendue ici.")
                self._attach(x, "H-PERMUTATION", desc, [_retag(e, y.person_id) for e in ev])
            self.result.patterns.append(Pattern(
                "H-PERMUTATION", target_field, 2,
                f"Valeurs inversées entre les employés {a.person_id} et {b.person_id} "
                f"({show(a.expected)} ↔ {show(b.expected)}).", (a.finding_id, b.finding_id)))

    def _candidate_values(self, pair: AssignmentPair, exclude: set[str]) -> dict[str, tuple[Any, Evidence]]:
        """Valeurs des colonnes liées à une affectation, avec la preuve correspondante."""
        assert pair.source is not None
        out: dict[str, tuple[Any, Evidence]] = {}
        excluded = self.hcfg.alt_source_excluded_columns
        src_ev = Evidence(f"src:{pair.source[ROW_COL]}", "source", pair.source[ROW_COL], {})
        for col, val in pair.source.items():
            if col not in excluded and col not in exclude:
                out[f"source.{col}"] = (val, src_ev)
        pj = self.cfg.joins["poste_detail"]
        history = self.refs.poste_history.get(norm_code(pair.source.get(pj.col("poste").source)) or "")
        if history:
            last: PosteRecord = history[-1]
            ev = Evidence(f"poste:{last.row}", "poste_detail", last.row, {}, "Enregistrement le plus récent du poste")
            for col, val in last.values.items():
                if col not in excluded:
                    value = last.effective if col == pj.col("date_effet").table else val
                    out[f"détail du poste (enregistrement le plus récent).{col}"] = (value, ev)
        mj = self.cfg.joins["motifs"]
        motif = self.refs.motifs.get(norm_code(pair.source.get(mj.col("situation").source)) or "")
        if motif:
            ev = Evidence(f"motif:{motif[ROW_COL]}", "motifs", motif[ROW_COL], {})
            for col, val in motif.items():
                if col not in excluded:
                    out[f"motifs.{col}"] = (val, ev)
        return out

    def _alt_source(self, target_field: str, fs: list[Finding], anomalies: list[Finding]) -> None:
        try:
            fr = self.cfg.field(target_field)
        except KeyError:
            return
        kind = fr.kind
        rows: list[tuple[Finding, dict[str, tuple[Any, Evidence]]]] = []
        for f in fs:
            pair = self.pair_by_key.get((f.person_id, f.assignment_key))
            if pair is None or not pair.matched or f.target_norm is None:
                continue
            rows.append((f, self._candidate_values(pair, set(fr.sources))))
        n = len(rows)
        if n < self.hcfg.alt_source_min_count:
            return
        mapped_support = sum(f.verdict in AGREEING for f, _ in rows) / n
        hits: dict[str, list[Finding]] = defaultdict(list)
        for f, cands in rows:
            for col, (val, _) in cands.items():
                try:
                    if normalize(val, kind) == f.target_norm:
                        hits[col].append(f)
                except NormalizationError:
                    continue
        for col, matched in sorted(hits.items()):
            support = len(matched) / n
            explained = [f for f in matched if f.verdict in INVESTIGATE]
            if support < self.hcfg.alt_source_min_support or support <= mapped_support or not explained:
                continue
            rule = CandidateRule(target_field, col, support, n, mapped_support,
                                 tuple(f.finding_id for f in explained))
            self.result.candidate_rules.append(rule)
            cand_by_id = {f.finding_id: c for f, c in rows}
            for f in explained:
                val, ev = cand_by_id[f.finding_id][col]
                ev = Evidence(ev.id, ev.table, ev.row, {col.split(".")[-1]: _fmt(val)}, ev.note)
                desc = (f"Valeur cible {show(f.target_norm)} = {col} ; cette correspondance vaut pour "
                        f"{support:.0%} des {n} enregistrements du champ (règle actuelle : {mapped_support:.0%}).")
                self._attach(f, "H-ALT-SOURCE", desc, [ev], support=support)
            self.result.patterns.append(Pattern("H-ALT-SOURCE", target_field, len(explained),
                                                rule.description, rule.explains))

    def _history_record(self, target_field: str, anomalies: list[Finding]) -> None:
        try:
            fr = self.cfg.field(target_field)
        except KeyError:
            return
        if fr.rule not in DATE_RULES:
            return
        pj = self.cfg.joins["poste_detail"]
        labels: dict[str, list[Finding]] = defaultdict(list)
        for f in anomalies:
            pair = self.pair_by_key.get((f.person_id, f.assignment_key))
            if pair is None or pair.source is None or f.target_norm is None:
                continue
            history = self.refs.poste_history.get(norm_code(pair.source.get(pj.col("poste").source)) or "", [])
            for k, rec in enumerate(history):
                if rec.effective == f.target_norm:
                    label = ("la plus récente" if k == len(history) - 1 else
                             "la plus ancienne" if k == 0 else f"n° {k + 1} sur {len(history)}")
                    desc = (f"Date cible {show(f.target_norm)} = date d'effet {label} de l'historique du poste "
                            f"({len(history)} enregistrements) ; la règle attend {show(f.expected)}.")
                    self._attach(f, "H-HISTORY-RECORD", desc,
                                 [Evidence(f"poste:{rec.row}", "poste_detail", rec.row,
                                           {"date_effet": rec.effective.isoformat(), "unite_admin": rec.unit})])
                    labels[label].append(f)
                    break
        for label, fs in labels.items():
            if len(fs) >= 2:
                self.result.patterns.append(Pattern(
                    "H-HISTORY-RECORD", target_field, len(fs),
                    f"Motif récurrent : pour {len(fs)} anomalies, la cible contient la date d'effet {label} "
                    "du détail du poste au lieu de la date calculée par la règle.",
                    tuple(f.finding_id for f in fs)))

    def _alt_interpretation(self, anomalies: list[Finding]) -> None:
        for f in anomalies:
            for key, value in f.rule_params.items():
                if key.startswith("alternative[") and str(value).split(" ")[0] in {v.value for v in AGREEING}:
                    alt = key[len("alternative["):-1]
                    iid = str(f.rule_params.get("interpretation", "")).split("=")[0]
                    self._attach(f, "H-ALT-INTERPRETATION",
                                 f"La cible est conforme sous l'interprétation « {alt} » de {iid} ({value}).")

    def _type_absent(self) -> None:
        target_types = Counter(p.target_type for p in self.pairs if p.target is not None)
        source_types = Counter(p.source_type for p in self.pairs if p.source is not None)
        for f in self.findings:
            if f.verdict not in INVESTIGATE:
                continue
            pair = self.pair_by_key.get((f.person_id, f.assignment_key))
            if pair is None or pair.method is not MatchMethod.MISSING_TARGET or not pair.source_type:
                continue
            typ = pair.source_type
            if target_types.get(typ, 0) == 0:
                label = self.cfg.assignment_types.get(typ, {}).get("label", typ)
                desc = (f"Aucune affectation de type {typ} ({label}) dans le système cible "
                        f"(0 sur {sum(target_types.values())}), contre {source_types[typ]} dans la source.")
                self._attach(f, "H-TYPE-ABSENT", desc, systemic=True)
                self.result.patterns.append(Pattern("H-TYPE-ABSENT", f.target_field, 1, desc, (f.finding_id,)))


def _retag(ev: Evidence, person: str) -> Evidence:
    """Preuve empruntée à un autre employé (identifiant inchangé : même ligne d'origine)."""
    return Evidence(ev.id, ev.table, ev.row, ev.values, f"Contrepartie : employé {person}")


def _fmt(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v
