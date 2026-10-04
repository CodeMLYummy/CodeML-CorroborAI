import unittest

from corroborai.analysis import load_hypotheses, load_scoring
from corroborai.analysis.scoring import score_finding
from corroborai.engine import corroborate
from corroborai.io.loaders import load_bundle
from corroborai.models import Confidence, HypothesisResult, Verdict
from corroborai.rules_config import load_rules
from tests._helpers import DATA_DIR, requires_data
from tests.test_hypotheses import finding

SCFG = load_scoring(hypotheses=load_hypotheses())


class TestFormula(unittest.TestCase):
    def test_base(self):
        f = finding("1", "f", "A", "B")
        f.criticality = 4
        p, why = score_finding(f, SCFG)
        self.assertEqual(p, 80.0)
        self.assertIn("criticité 4/5", why)

    def test_confidence_and_indetermine(self):
        f = finding("1", "f", "A", "B", verdict=Verdict.INDETERMINE)
        f.criticality, f.confidence = 5, Confidence.FAIBLE
        self.assertEqual(score_finding(f, SCFG)[0], round(100 * 0.4 * 0.6, 1))

    def test_modifiers_multiply_once_and_cap(self):
        f = finding("1", "f", "A", "B")
        f.criticality = 5
        f.hypotheses = [HypothesisResult("H-PERMUTATION", "x", True),
                        HypothesisResult("H-PERMUTATION", "x", True)]
        p, why = score_finding(f, SCFG)
        self.assertEqual(p, 100.0)
        self.assertIn("plafonné", why)
        self.assertEqual(why.count("H-PERMUTATION"), 1)
        f.hypotheses = [HypothesisResult("H-SYSTEMIC", "x", True), HypothesisResult("H-BIJECTION", "x", True)]
        self.assertEqual(score_finding(f, SCFG)[0], round(100 * 0.3 * 0.8, 1))

    def test_unverified_hypothesis_ignored(self):
        f = finding("1", "f", "A", "B")
        f.criticality = 5
        f.hypotheses = [HypothesisResult("H-SYSTEMIC", "x", False)]
        self.assertEqual(score_finding(f, SCFG)[0], 100.0)

    def test_not_scored_when_not_investigated(self):
        for v in (Verdict.CONFORME, Verdict.JUSTIFIE):
            self.assertEqual(score_finding(finding("1", "f", "A", "A", verdict=v), SCFG), (None, None))


@requires_data
class TestRanking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = corroborate(load_bundle(DATA_DIR), load_rules(), strict=True)
        cls.ranked = sorted((f for f in cls.result.findings if f.priority is not None),
                            key=lambda f: -f.priority)

    def test_every_investigated_finding_scored(self):
        for f in self.result.findings:
            self.assertEqual(f.priority is not None, f.verdict in (Verdict.ANOMALIE, Verdict.INDETERMINE))
            if f.priority is not None:
                self.assertTrue(f.priority_breakdown.endswith(f"{f.priority:g}") or "plafonné" in f.priority_breakdown)

    def test_isolated_errors_rank_above_systemic_artifacts(self):
        top5 = {(f.person_id, f.target_field) for f in self.ranked[:5]}
        self.assertEqual(top5, {("1545850", "affectation"), ("2762457", "contractTypeCode"),
                                ("4625374", "contractTypeCode"), ("3712987", "contractTypeCode"),
                                ("7254364", "contractTypeCode")})
        systemic = [f.priority for f in self.ranked if f.target_field in ("contactEmail", "positionName")]
        isolated = [f.priority for f in self.ranked if f.target_field not in ("contactEmail", "positionName")]
        self.assertLess(max(systemic), min(isolated))
        self.assertEqual(self.ranked[-1].target_field, "positionName")


if __name__ == "__main__":
    unittest.main()
