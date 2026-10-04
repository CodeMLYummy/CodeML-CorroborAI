"""Calcul des dates d'affectation à partir de l'historique du détail du poste.

Règles (Mapping.xlsx, lignes 46–58), interprétation INT-ASSIGN-DATES :

* **Début de l'unité administrative courante** : date d'effet de
  l'enregistrement où l'unité courante devient applicable (changement par
  rapport à l'enregistrement précédent) ; sans changement dans l'historique,
  la plus ancienne date d'effet (MIN EFFDT).
* **Fin de l'unité administrative courante** : date d'effet du prochain
  enregistrement − 1 jour, uniquement s'il porte une unité différente ;
  sinon aucune fin (NULL).
* **Début d'affectation** : ``interval_intersection`` → MAX(entrée, début
  unité) ; ``strict_literal`` → MIN(entrée, début unité).
* **Fin d'affectation** : MIN(sortie, fin unité), valeurs nulles ignorées.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from corroborai.rules.base import PosteRecord

INTERVAL_INTERSECTION = "interval_intersection"
STRICT_LITERAL = "strict_literal"


@dataclass
class UnitInterval:
    start: date | None
    end: date | None
    start_record: PosteRecord | None = None
    end_record: PosteRecord | None = None
    has_change: bool = False
    notes: list[str] = field(default_factory=list)


def unit_interval(history: list[PosteRecord], current_unit: str | None) -> UnitInterval:
    """Intervalle de validité de l'unité administrative courante dans l'historique du poste."""
    if not history:
        return UnitInterval(None, None, notes=["Aucun historique de détail du poste."])
    units = [r.unit for r in history]
    has_change = any(units[i] != units[i - 1] for i in range(1, len(units)))

    if not has_change:
        res = UnitInterval(history[0].effective, None, history[0], None, False)
        if current_unit is not None and units[0] != current_unit:
            res.notes.append(f"L'historique porte l'unité {units[0]}, la source {current_unit}.")
        return res

    # Début de la DERNIÈRE séquence contiguë portant l'unité courante
    starts = [i for i, u in enumerate(units) if u == current_unit and (i == 0 or units[i - 1] != u)]
    if not starts:
        return UnitInterval(None, None, has_change=True,
                            notes=[f"L'unité courante {current_unit} n'apparaît pas dans l'historique."])
    k = starts[-1]
    res = UnitInterval(history[k].effective, None, history[k], None, True)
    nxt = next((j for j in range(k + 1, len(units)) if units[j] != current_unit), None)
    if nxt is not None:
        res.end = history[nxt].effective - timedelta(days=1)
        res.end_record = history[nxt]
    return res


def _non_null(*values: date | None) -> list[date]:
    return [v for v in values if v is not None]


def assignment_start(entry: date | None, unit: UnitInterval, interpretation: str) -> date | None:
    vals = _non_null(entry, unit.start)
    if not vals:
        return None
    if interpretation == INTERVAL_INTERSECTION:
        return max(vals)
    if interpretation == STRICT_LITERAL:
        return min(vals)
    raise ValueError(f"Interprétation inconnue : {interpretation}")


def assignment_end(exit_: date | None, unit: UnitInterval) -> date | None:
    vals = _non_null(exit_, unit.end)
    return min(vals) if vals else None
