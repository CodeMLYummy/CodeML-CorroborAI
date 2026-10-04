import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from corroborai.analysis.config import (
    DEFAULT_HYPOTHESES,
    DEFAULT_SCORING,
    KNOWN_HYPOTHESES,
    AnalysisConfigError,
    load_hypotheses,
    load_scoring,
)

RAW_H = yaml.safe_load(DEFAULT_HYPOTHESES.read_text(encoding="utf-8"))
RAW_S = yaml.safe_load(DEFAULT_SCORING.read_text(encoding="utf-8"))


def _tmp(raw):
    fh = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    yaml.safe_dump(raw, fh, allow_unicode=True)
    fh.close()
    return Path(fh.name)


def load_h(mutate):
    raw = copy.deepcopy(RAW_H)
    mutate(raw)
    p = _tmp(raw)
    try:
        return load_hypotheses(p)
    finally:
        p.unlink()


def load_s(mutate):
    raw = copy.deepcopy(RAW_S)
    mutate(raw)
    p = _tmp(raw)
    try:
        return load_scoring(p)
    finally:
        p.unlink()


class TestDefaults(unittest.TestCase):
    def test_load(self):
        h = load_hypotheses()
        s = load_scoring(hypotheses=h)
        self.assertEqual(set(h.hypotheses), KNOWN_HYPOTHESES)
        self.assertEqual(set(s.modifiers), KNOWN_HYPOTHESES)
        for m in s.modifiers.values():
            self.assertTrue(m.reason)


class TestMutations(unittest.TestCase):
    H = {
        "hypothèse non implémentée": lambda r: r["hypotheses"].update({"H-MAGIE": {"label": "x", "description": "y"}}),
        "hypothèse non documentée": lambda r: r["hypotheses"].pop("H-SYSTEMIC"),
        "description manquante": lambda r: r["hypotheses"]["H-SYSTEMIC"].pop("description"),
        "précédence incomplète": lambda r: r["precedence"].pop(),
        "précédence dupliquée": lambda r: r["precedence"].append("H-SYSTEMIC"),
        "seuil hors bornes": lambda r: r["thresholds"].update(systemic_min_share=1.5),
        "seuil non entier": lambda r: r["thresholds"].update(bijection_min_count=2.5),
        "seuil absent": lambda r: r["thresholds"].pop("alt_source_min_support"),
        "version": lambda r: r.update(version=3),
    }
    S = {
        "modificateur manquant": lambda r: r["modifiers"].pop("H-SYSTEMIC"),
        "modificateur inconnu": lambda r: r["modifiers"].update({"H-X": {"factor": 1, "reason": "x"}}),
        "facteur nul": lambda r: r["modifiers"]["H-SYSTEMIC"].update(factor=0),
        "justification manquante": lambda r: r["modifiers"]["H-SYSTEMIC"].pop("reason"),
        "base verdict incomplète": lambda r: r["verdict_base"].pop("INDETERMINE"),
        "base verdict non scorable": lambda r: r["verdict_base"].update(CONFORME=1),
        "confiance non décroissante": lambda r: r["confidence_weights"].update(FAIBLE=1.0),
        "confiance incomplète": lambda r: r["confidence_weights"].pop("MOYENNE"),
        "échelle": lambda r: r.update(scale=0),
    }

    def test_hypotheses_mutations(self):
        for name, m in self.H.items():
            with self.subTest(mutation=name), self.assertRaises(AnalysisConfigError):
                load_h(m)

    def test_scoring_mutations(self):
        for name, m in self.S.items():
            with self.subTest(mutation=name), self.assertRaises(AnalysisConfigError):
                load_s(m)


if __name__ == "__main__":
    unittest.main()
