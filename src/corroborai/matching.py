"""Appariement des affectations source ↔ cible.

L'appariement ne dépend jamais de l'ordre des lignes :

1. par employé (Matricule ↔ personId) ;
2. par type d'affectation (TypeAffectation ↔ indicateurs primaire/temporaire) ;
3. à type égal et plusieurs candidats : affectation optimale qui maximise la
   similarité totale (recherche exhaustive — les effectifs par employé sont
   petits ; repli glouton au-delà de ``EXHAUSTIVE_LIMIT``) ;
4. restes d'un même employé : appariement entre types différents si la
   similarité atteint le seuil configuré (l'écart de type sera signalé par la
   règle R-ASSIGN-TYPE), sinon affectation absente.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from itertools import permutations
from typing import Any

from corroborai.io.loaders import ROW_COL, DataBundle
from corroborai.io.normalize import NormalizationError, norm_bool, normalize
from corroborai.rules_config import RulesConfig

EXHAUSTIVE_LIMIT = 7
SOURCE_KEY = "Matricule"
TARGET_KEY = "personId"
SOURCE_TYPE = "TypeAffectation"


class MatchMethod(str, Enum):
    UNIQUE = "TYPE_UNIQUE"              # un seul candidat de ce type
    SIMILARITY = "SIMILARITE"           # plusieurs candidats, affectation optimale
    CROSS_TYPE = "TYPE_DIFFERENT"       # apparié malgré un type différent
    MISSING_TARGET = "ABSENTE_CIBLE"
    MISSING_SOURCE = "ABSENTE_SOURCE"


@dataclass(frozen=True)
class AssignmentPair:
    person_id: str
    source: dict[str, Any] | None
    target: dict[str, Any] | None
    method: MatchMethod
    score: float | None
    source_type: str | None
    target_type: str | None

    @property
    def key(self) -> str:
        """Identifiant lisible et stable de l'affectation."""
        if self.source is not None:
            return f"{self.source_type or '?'}:{self.source.get('CodePoste') or self.source[ROW_COL]}"
        return f"{self.target_type or '?'}:cible-ligne{self.target[ROW_COL]}"  # type: ignore[index]

    @property
    def matched(self) -> bool:
        return self.source is not None and self.target is not None


def target_type(cfg: RulesConfig, row: dict[str, Any]) -> str | None:
    try:
        return cfg.assignment_type_for(norm_bool(row.get("isPrimaryAssignment")),
                                       norm_bool(row.get("isTemporaryAssignment")))
    except NormalizationError:
        return None


def similarity(cfg: RulesConfig, src: dict[str, Any], tgt: dict[str, Any]) -> float:
    """Part des champs de similarité égaux après normalisation (vides exclus)."""
    hits = total = 0
    for sf in cfg.matching.similarity_fields:
        try:
            a, b = normalize(src.get(sf.source), sf.kind), normalize(tgt.get(sf.target), sf.kind)
        except NormalizationError:
            total += 1
            continue
        if a is None and b is None:
            continue
        total += 1
        hits += a == b
    return hits / total if total else 0.0


def _optimal(cfg: RulesConfig, sources: list[dict], targets: list[dict]) -> list[tuple[int, int, float]]:
    """Retourne les couples (i_source, j_cible, score) maximisant la similarité totale.

    Départage déterministe : à score égal, l'ordre lexicographique des
    affectations l'emporte (reproductible d'une exécution à l'autre).
    """
    scores = [[similarity(cfg, s, t) for t in targets] for s in sources]
    n, m = len(sources), len(targets)
    if n == 0 or m == 0:
        return []
    if max(n, m) <= EXHAUSTIVE_LIMIT:
        best: tuple[float, tuple] | None = None
        if n <= m:
            for perm in permutations(range(m), n):
                total = sum(scores[i][perm[i]] for i in range(n))
                if best is None or total > best[0] + 1e-12:
                    best = (total, tuple((i, perm[i]) for i in range(n)))
        else:
            for perm in permutations(range(n), m):
                total = sum(scores[perm[j]][j] for j in range(m))
                if best is None or total > best[0] + 1e-12:
                    best = (total, tuple(sorted((perm[j], j) for j in range(m))))
        assert best is not None
        return [(i, j, scores[i][j]) for i, j in best[1]]
    # Repli glouton (effectifs inhabituels)
    cells = sorted(((scores[i][j], -i, -j) for i in range(n) for j in range(m)), reverse=True)
    used_i, used_j, out = set(), set(), []
    for sc, ni, nj in cells:
        i, j = -ni, -nj
        if i not in used_i and j not in used_j:
            used_i.add(i)
            used_j.add(j)
            out.append((i, j, sc))
    return sorted(out)


def match_assignments(bundle: DataBundle, cfg: RulesConfig) -> list[AssignmentPair]:
    src_by_person: dict[str, list[dict]] = defaultdict(list)
    tgt_by_person: dict[str, list[dict]] = defaultdict(list)
    for rec in bundle.source.df.to_dict("records"):
        src_by_person[str(rec[SOURCE_KEY]).strip()].append(rec)
    for rec in bundle.target.df.to_dict("records"):
        tgt_by_person[str(rec[TARGET_KEY]).strip()].append(rec)

    pairs: list[AssignmentPair] = []
    for person in sorted(set(src_by_person) | set(tgt_by_person)):
        pairs.extend(_match_person(cfg, person, src_by_person.get(person, []),
                                   tgt_by_person.get(person, [])))
    return pairs


def _match_person(cfg: RulesConfig, person: str, sources: list[dict],
                  targets: list[dict]) -> list[AssignmentPair]:
    out: list[AssignmentPair] = []
    s_type = {id(s): (str(s.get(SOURCE_TYPE) or "").strip().upper() or None) for s in sources}
    t_type = {id(t): target_type(cfg, t) for t in targets}
    left_s, left_t = list(sources), list(targets)

    # Étapes 2–3 : même type
    for typ in sorted({s_type[id(s)] for s in sources if s_type[id(s)]}):
        ss = [s for s in left_s if s_type[id(s)] == typ]
        tt = [t for t in left_t if t_type[id(t)] == typ]
        method = MatchMethod.UNIQUE if len(ss) == 1 and len(tt) == 1 else MatchMethod.SIMILARITY
        for i, j, score in _optimal(cfg, ss, tt):
            out.append(AssignmentPair(person, ss[i], tt[j], method, round(score, 4), typ, typ))
            left_s.remove(ss[i])
            left_t.remove(tt[j])

    # Étape 4 : types différents, au-dessus du seuil
    for i, j, score in _optimal(cfg, left_s, left_t):
        if score >= cfg.matching.cross_type_min_score:
            s, t = left_s[i], left_t[j]
            out.append(AssignmentPair(person, s, t, MatchMethod.CROSS_TYPE, round(score, 4),
                                      s_type[id(s)], t_type[id(t)]))
    matched_s = {id(p.source) for p in out}
    matched_t = {id(p.target) for p in out}
    for s in sources:
        if id(s) not in matched_s:
            out.append(AssignmentPair(person, s, None, MatchMethod.MISSING_TARGET, None, s_type[id(s)], None))
    for t in targets:
        if id(t) not in matched_t:
            out.append(AssignmentPair(person, None, t, MatchMethod.MISSING_SOURCE, None, None, t_type[id(t)]))
    return sorted(out, key=lambda p: (p.source[ROW_COL] if p.source else 10**9,
                                      p.target[ROW_COL] if p.target else 10**9))
