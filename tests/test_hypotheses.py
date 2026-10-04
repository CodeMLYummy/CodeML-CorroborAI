"""Moteur d'hypothèses : cas synthétiques et référence sur les données du défi."""

import unittest
from types import SimpleNamespace

from corroborai.analysis import HypothesisEngine, load_hypotheses
from corroborai.engine import ASSIGNMENT_FIELD, corroborate
from corroborai.io.loaders import load_bundle
from corroborai.matching import AssignmentPair, MatchMethod
from corroborai.models import DecisionSource, Evidence, Finding, Verdict
from corroborai.rules.base import Refs
from corroborai.rules_config import load_rules
from tests._helpers import CHALLENGE_DIR, requires_challenge_data

CFG = load_rules()
HCFG = load_hypotheses()


def finding(person, field, expected, target, verdict=Verdict.ANOMALIE, key="P:1", row=2, subcat=None):
    return Finding(person_id=person, assignment_key=key, target_field=field, source_fields=("x",),
                   source_raw=expected, target_raw=target, expected=expected, target_norm=target,
                   verdict=verdict, decision_source=DecisionSource.REGLE, rule_id="R-TEST",
                   justification="test", subcategory=subcat,
                   evidence=[Evidence(f"src:{row}", "source", row, {}), Evidence(f"tgt:{row}", "target", row, {})])


def run(findings, pairs=()):
    return HypothesisEngine(CFG, HCFG, Refs({}, {}), list(pairs), findings).run()


def hids(f):
    return {h.hypothesis_id for h in f.hypotheses if h.verified}


class TestPermutation(unittest.TestCase):
    def test_mutual_swap(self):
        a = finding("1", "siteName", "A", "B", row=2)
        b = finding("2", "siteName", "B", "A", row=3)
        c = finding("3", "siteName", "C", "D", row=4)
        res = run([a, b, c])
        self.assertIn("H-PERMUTATION", hids(a))
        self.assertIn("H-PERMUTATION", hids(b))
        self.assertNotIn("H-PERMUTATION", hids(c))
        self.assertIn("src:3", [e.id for e in a.evidence])  # preuve de la contrepartie
        self.assertEqual(len([p for p in res.patterns if p.hypothesis_id == "H-PERMUTATION"]), 1)

    def test_one_way_is_not_a_permutation(self):
        a = finding("1", "siteName", "A", "B", row=2)
        b = finding("2", "siteName", "B", "C", row=3)
        run([a, b])
        self.assertNotIn("H-PERMUTATION", hids(a) | hids(b))

    def test_same_person_not_a_permutation(self):
        a = finding("1", "siteName", "A", "B", key="P:1", row=2)
        b = finding("1", "siteName", "B", "A", key="S:2", row=3)
        run([a, b])
        self.assertNotIn("H-PERMUTATION", hids(a))

    def test_each_finding_used_once(self):
        a = finding("1", "f", "A", "B", row=2)
        b = finding("2", "f", "B", "A", row=3)
        c = finding("3", "f", "B", "A", row=4)
        run([a, b, c])
        self.assertEqual(sum("H-PERMUTATION" in hids(x) for x in (a, b, c)), 2)


class TestFieldLevel(unittest.TestCase):
    def test_systemic_threshold(self):
        fs = [finding(str(i), "f", "A", "B", row=i + 2) for i in range(5)]
        fs.append(finding("9", "f", "A", "A", verdict=Verdict.CONFORME, row=20))
        run(fs)  # 5/6 = 83 % ≥ 80 %
        self.assertTrue(all("H-SYSTEMIC" in hids(f) for f in fs[:5]))
        fs = [finding(str(i), "g", "A", "B", row=i + 2) for i in range(5)]
        fs += [finding(str(10 + i), "g", "A", "A", verdict=Verdict.CONFORME, row=30 + i) for i in range(2)]
        run(fs)  # 5/7 = 71 %
        self.assertFalse(any("H-SYSTEMIC" in hids(f) for f in fs))

    def test_bijection_requires_value_shared_by_different_people(self):
        fs = [finding(str(i), "f", f"E{i % 2}", f"T{i % 2}", row=i + 2) for i in range(6)]
        run(fs)
        self.assertTrue(all("H-BIJECTION" in hids(f) for f in fs))
        trivial = [finding(str(i), "g", f"E{i}", f"T{i}", row=i + 2) for i in range(6)]
        trivial.append(finding("0", "g", "E0", "T0", key="S:9", row=40))  # répétition chez le même employé
        run(trivial)
        self.assertFalse(any("H-BIJECTION" in hids(f) for f in trivial))

    def test_bijection_rejects_non_functional_mapping(self):
        fs = [finding(str(i), "f", "E", f"T{i % 2}", row=i + 2) for i in range(6)]
        run(fs)
        self.assertFalse(any("H-BIJECTION" in hids(f) for f in fs))

    def test_alternative_interpretation(self):
        f = finding("1", "assignmentStartDate", "2009-03-30", "2001-11-16")
        f.rule_params = {"interpretation": "INT-ASSIGN-DATES=interval_intersection",
                         "alternative[strict_literal]": "CONFORME (attendu 2001-11-16)"}
        run([f])
        self.assertIn("H-ALT-INTERPRETATION", hids(f))

    def test_verdict_and_conformes_untouched(self):
        ok = finding("1", "f", "A", "A", verdict=Verdict.CONFORME)
        run([ok])
        self.assertEqual(ok.hypotheses, [])
        self.assertIsNone(ok.probable_cause)

    def test_type_absent(self):
        src = {"_row": 2, "TypeAffectation": "A", "CodePoste": "9"}
        pair = AssignmentPair("1", src, None, MatchMethod.MISSING_TARGET, None, "A", None)
        other = AssignmentPair("2", {"_row": 3}, {"_row": 2}, MatchMethod.UNIQUE, 1.0, "P", "P")
        f = finding("1", ASSIGNMENT_FIELD, None, None, key=pair.key)
        run([f], [pair, other])
        self.assertIn("H-TYPE-ABSENT", hids(f))


@requires_challenge_data
class TestRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(CHALLENGE_DIR)
        cls.result = corroborate(cls.bundle, CFG, strict=True)
        cls.by = {(f.person_id, f.target_field, f.assignment_key): f for f in cls.result.findings}

    def get(self, person, field):
        return [f for (p, t, _), f in self.by.items() if p == person and t == field]

    def test_verdicts_identical_with_and_without_analysis(self):
        plain = corroborate(self.bundle, CFG, strict=True, analyze=False)
        self.assertEqual([(f.finding_id, f.verdict, f.decision_source, f.expected) for f in plain.findings],
                         [(f.finding_id, f.verdict, f.decision_source, f.expected) for f in self.result.findings])

    def test_hypothesis_evidence_is_attached(self):
        for f in self.result.findings:
            ids = {e.id for e in f.evidence}
            for h in f.hypotheses:
                self.assertTrue(set(h.evidence_ids) <= ids, f.finding_id)

    def test_expected_hypotheses(self):
        expected = {
            "H-PERMUTATION": {("2762457", "contractTypeCode"), ("4625374", "contractTypeCode"),
                              ("3712987", "contractTypeCode"), ("7254364", "contractTypeCode"),
                              ("3241002", "siteName"), ("6035643", "siteName")},
            "H-HISTORY-RECORD": {("3241002", "assignmentStartDate"), ("4402456", "assignmentStartDate"),
                                 ("9989151", "assignmentStartDate")},
            "H-ALT-SOURCE": {(p, f) for p in ("2911996", "3712987", "4402456", "7683990")
                             for f in ("weeklyHoursOverride", "dailyHoursOverride")},
            "H-TYPE-ABSENT": {("1545850", ASSIGNMENT_FIELD)},
        }
        for hid, cases in expected.items():
            got = {(f.person_id, f.target_field) for f in self.result.findings if hid in hids(f)}
            with self.subTest(hypothesis=hid):
                self.assertEqual(got, cases)

    def test_systemic_fields(self):
        for f in self.result.findings:
            if f.target_field == "contactEmail":
                self.assertEqual(hids(f), {"H-SYSTEMIC"})       # identifiants propres à chaque employé
            elif f.target_field == "positionName":
                self.assertEqual(hids(f), {"H-SYSTEMIC", "H-BIJECTION"})

    def test_candidate_rules(self):
        rules = {(c.target_field, c.column.split(".")[-1]): c for c in self.result.analysis.candidate_rules}
        self.assertEqual(set(rules), {("weeklyHoursOverride", "HeuresSemaineContrat"),
                                      ("dailyHoursOverride", "HeuresJourContrat")})
        for c in rules.values():
            self.assertEqual(c.support, 1.0)
            self.assertEqual(len(c.explains), 4)
            self.assertLess(c.mapped_support, c.support)

    def test_recurring_history_pattern(self):
        pats = [p for p in self.result.analysis.patterns if p.hypothesis_id == "H-HISTORY-RECORD"]
        self.assertEqual(len(pats), 1)
        self.assertEqual(pats[0].count, 3)
        self.assertIn("la plus récente", pats[0].description)

    def test_every_anomaly_has_a_cause(self):
        for f in self.result.findings:
            if f.verdict is Verdict.ANOMALIE:
                self.assertTrue(f.probable_cause, f.finding_id)

    def test_alt_source_against_real_population(self):
        res = [c for c in self.result.analysis.candidate_rules if c.target_field == "weeklyHoursOverride"]
        self.assertEqual(res[0].population, 22)


class TestAltSourceSynthetic(unittest.TestCase):
    """Colonne « Fortuite » égale à la cible sur 1 enregistrement sur 6 : rejetée.
    Colonne « Contrat » égale à la cible sur 6/6 : règle candidate."""

    def setUp(self):
        self.pairs, self.findings = [], []
        for i in range(6):
            target = "35" if i == 0 else "40"
            src = {"_row": i + 2, "TypeAffectation": "P", "CodePoste": str(i), "HeuresNormeHebdo": "40",
                   "Fortuite": "35" if i == 0 else "99", "Contrat": target}
            pair = AssignmentPair(str(i), src, {"_row": i + 2}, MatchMethod.UNIQUE, 1.0, "P", "P")
            verdict = Verdict.ANOMALIE if i == 0 else Verdict.CONFORME
            from decimal import Decimal
            f = finding(str(i), "weeklyHoursOverride", Decimal(40), Decimal(target), verdict, key=pair.key, row=i + 2)
            self.pairs.append(pair)
            self.findings.append(f)

    def test_only_population_wide_match_is_retained(self):
        res = run(self.findings, self.pairs)
        self.assertEqual([c.column for c in res.candidate_rules], ["source.Contrat"])
        self.assertEqual(res.candidate_rules[0].support, 1.0)
        self.assertIn("H-ALT-SOURCE", hids(self.findings[0]))
        self.assertTrue(all(h.hypothesis_id != "H-ALT-SOURCE" for f in self.findings[1:] for h in f.hypotheses))

    def test_mapped_column_never_a_candidate(self):
        res = run(self.findings, self.pairs)
        self.assertFalse(any(c.column.endswith("HeuresNormeHebdo") for c in res.candidate_rules))

    def test_population_too_small(self):
        res = run(self.findings[:3], self.pairs[:3])
        self.assertEqual(res.candidate_rules, [])

if __name__ == "__main__":
    unittest.main()
