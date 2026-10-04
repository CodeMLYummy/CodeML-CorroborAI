"""Bout en bout sur le jeu synthétique : chaque scénario conçu produit le verdict attendu,
identiquement aux formats Excel et CSV. S'exécute partout (aucune dépendance aux fichiers du défi)."""

import unittest

from corroborai.ai.tasks import run_ai
from corroborai.engine import corroborate
from corroborai.feedback import ExpertRule, FeedbackStore
from corroborai.io.loaders import load_bundle
from corroborai.models import Confidence, DecisionSource, Verdict
from corroborai.rules_config import load_rules, validate_against_data
from tests._helpers import fixture_dir
from tests.fixtures import dataset as ds

CFG = load_rules()


def keys(result, pred):
    return {(f.person_id, f.target_field) for f in result.findings if pred(f)}


class TestSyntheticDataset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {fmt: corroborate(load_bundle(fixture_dir(fmt)), CFG, strict=True) for fmt in ("xlsx", "csv")}
        cls.r = cls.results["xlsx"]

    def test_config_valid_on_dataset(self):
        res = validate_against_data(CFG, load_bundle(fixture_dir()))
        self.assertEqual(res.errors, [])

    def test_formats_give_identical_results(self):
        rx, rc = (self.results[f] for f in ("xlsx", "csv"))
        self.assertEqual([(f.finding_id, f.verdict, f.expected, f.priority) for f in rx.findings],
                         [(f.finding_id, f.verdict, f.expected, f.priority) for f in rc.findings])

    def test_anomalies(self):
        self.assertEqual(keys(self.r, lambda f: f.verdict is Verdict.ANOMALIE), ds.EXPECTED_ANOMALIES)

    def test_indeterminate(self):
        self.assertEqual(keys(self.r, lambda f: f.verdict is Verdict.INDETERMINE), ds.EXPECTED_INDETERMINATE)

    def test_justified_examples(self):
        verdicts = {(f.person_id, f.target_field): f.verdict for f in self.r.findings}
        for k in ds.EXPECTED_JUSTIFIED_EXAMPLES:
            self.assertIs(verdicts[k], Verdict.JUSTIFIE, k)

    def test_hypotheses(self):
        for hid, expected in ds.EXPECTED_HYPOTHESES.items():
            got = keys(self.r, lambda f, h=hid: any(x.verified and x.hypothesis_id == h for x in f.hypotheses))
            with self.subTest(hypothesis=hid):
                self.assertEqual(got, expected)

    def test_unexplained(self):
        got = keys(self.r, lambda f: f.verdict in (Verdict.ANOMALIE, Verdict.INDETERMINE)
                   and not any(h.verified for h in f.hypotheses))
        self.assertEqual(got, ds.EXPECTED_UNEXPLAINED)

    def test_scenario_details(self):
        by = {(f.person_id, f.target_field): f for f in self.r.findings}
        start = by[("1000001", "assignmentStartDate")]
        self.assertEqual((start.verdict, start.confidence), (Verdict.CONFORME, Confidence.MOYENNE))
        self.assertEqual(by[("1000002", "statusReasonCode")].expected, "170")
        self.assertIn("Encodage", by[("1000002", "detailedStatus")].justification)
        self.assertEqual(by[("1000012", "givenName")].subcategory, "accents ou casse retirés")
        secondaries = [f for f in self.r.findings if f.person_id == "1000009" and f.target_field == "positionId"]
        self.assertTrue(all(f.verdict is Verdict.CONFORME for f in secondaries) and len(secondaries) == 3)
        email = by[("1000005", "contactEmail")]
        self.assertEqual(email.subcategory, "identifiant incohérent avec le matricule")
        self.assertEqual(by[("1000003", "contactEmail")].subcategory, "préfixe d'environnement")

    def test_candidate_rules(self):
        self.assertEqual({(c.target_field, c.column.split(".")[-1]) for c in self.r.analysis.candidate_rules},
                         {("weeklyHoursOverride", "HeuresSemaineContrat"), ("dailyHoursOverride", "HeuresJourContrat")})

    def test_triage_of_unexplained(self):
        res = corroborate(load_bundle(fixture_dir()), CFG, strict=True)
        rep = run_ai(res, load_bundle(fixture_dir()))
        triaged = {tuple(fid.split(":")[i] for i in (0, 3)) for fid in rep.triaged}
        self.assertEqual(triaged, ds.EXPECTED_UNEXPLAINED)

    def test_expert_rule(self):
        col = next(c.column for c in self.r.analysis.candidate_rules if c.target_field == "weeklyHoursOverride")
        store = FeedbackStore()
        store.add_rule(ExpertRule("", "accept_alternative_source", "weeklyHoursOverride", {"column": col},
                                  "Heures du contrat du poste."), set(CFG.targets))
        res = corroborate(load_bundle(fixture_dir("csv")), CFG, strict=True, feedback=store)
        f = next(x for x in res.findings if x.person_id == "1000007" and x.target_field == "weeklyHoursOverride")
        self.assertEqual((f.verdict, f.decision_source), (Verdict.JUSTIFIE, DecisionSource.EXPERT))

    def test_integrity(self):
        self.assertTrue(self.r.integrity_after.ok)


if __name__ == "__main__":
    unittest.main()
