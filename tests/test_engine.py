"""Tests de bout en bout du moteur sur les données du défi.

``EXPECTED_ANOMALIES`` est l'ensemble de référence, établi par l'analyse
exploratoire et vérifié manuellement sur les données brutes. Toute dérive du
moteur (régression ou changement de règle) le fait échouer.
"""

import unittest
from unittest import mock

from corroborai.engine import ASSIGNMENT_FIELD, corroborate
from corroborai.io.loaders import load_bundle
from corroborai.models import Confidence, DecisionSource, Verdict
from corroborai.rules_config import load_rules
from tests._helpers import CHALLENGE_DIR, requires_challenge_data

ALL_PERSONS_PRIMARY = 22  # affectations appariées

EXPECTED_ANOMALIES = {
    # Libellé de site inversé entre deux employés (codes de site conformes)
    ("3241002", "siteName"), ("6035643", "siteName"),
    # Type d'employé : deux paires de valeurs inversées
    ("2762457", "contractTypeCode"), ("4625374", "contractTypeCode"),
    ("3712987", "contractTypeCode"), ("7254364", "contractTypeCode"),
    # Heures : la cible contient 40/8 (valeurs du détail du poste)
    ("2911996", "weeklyHoursOverride"), ("2911996", "dailyHoursOverride"),
    ("3712987", "weeklyHoursOverride"), ("3712987", "dailyHoursOverride"),
    ("4402456", "weeklyHoursOverride"), ("4402456", "dailyHoursOverride"),
    ("7683990", "weeklyHoursOverride"), ("7683990", "dailyHoursOverride"),
    # Début d'affectation = dernière date d'effet du détail du poste
    ("3241002", "assignmentStartDate"), ("4402456", "assignmentStartDate"),
    ("9989151", "assignmentStartDate"),
    # Affectation temporaire absente de la cible
    ("1545850", ASSIGNMENT_FIELD),
}
SYSTEMIC_ANOMALY_FIELDS = {"contactEmail", "positionName"}  # 22/22 : artefacts d'anonymisation


@requires_challenge_data
class TestEngineRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(CHALLENGE_DIR)
        cls.cfg = load_rules()
        cls.result = corroborate(cls.bundle, cls.cfg, strict=True)

    def test_volume(self):
        self.assertEqual(len(self.result.findings), ALL_PERSONS_PRIMARY * len(self.cfg.fields) + 1)
        self.assertEqual(self.result.errors, [])
        self.assertEqual(self.result.counts()["INDETERMINE"], 0)

    def test_integrity_preserved(self):
        self.assertTrue(self.result.integrity_before.ok)
        self.assertTrue(self.result.integrity_after.ok)

    def test_every_verdict_is_deterministic_and_justified(self):
        for f in self.result.findings:
            with self.subTest(f=f.finding_id):
                self.assertIs(f.decision_source, DecisionSource.REGLE)
                self.assertTrue(f.justification)
                self.assertTrue(f.rule_id)
                self.assertTrue(f.evidence)
                self.assertIsNotNone(f.criticality)

    def test_golden_anomaly_set(self):
        got = {(f.person_id, f.target_field) for f in self.result.findings
               if f.verdict is Verdict.ANOMALIE and f.target_field not in SYSTEMIC_ANOMALY_FIELDS}
        self.assertEqual(got, EXPECTED_ANOMALIES)

    def test_systemic_fields(self):
        for field, subcat in (("contactEmail", "identifiant incohérent avec le matricule"),
                              ("positionName", "code et libellé différents")):
            fs = [f for f in self.result.findings if f.target_field == field]
            with self.subTest(field=field):
                self.assertEqual(len(fs), ALL_PERSONS_PRIMARY)
                self.assertTrue(all(f.verdict is Verdict.ANOMALIE and f.subcategory == subcat for f in fs))

    def test_justified_cases(self):
        for f in self.result.findings:
            if f.target_field in ("divisionName", "detailedStatus", "statusReasonCode",
                                  "isPrimaryAssignment", "isTemporaryAssignment"):
                self.assertIs(f.verdict, Verdict.JUSTIFIE, f.finding_id)
        absences = {f.person_id: f for f in self.result.findings
                    if f.target_field == "statusReasonCode" and f.expected == "170"}
        self.assertEqual(set(absences), {"2911996", "7603160"})
        for f in absences.values():
            self.assertTrue(any(e.table == "motifs" for e in f.evidence))

    def test_unit_change_case_2173396(self):
        (f,) = self.result.find("2173396", "assignmentStartDate")
        self.assertIs(f.verdict, Verdict.CONFORME)
        self.assertIs(f.confidence, Confidence.MOYENNE)  # le verdict dépend de l'interprétation
        self.assertIn("ANOMALIE", f.rule_params["alternative[strict_literal]"])
        self.assertEqual(len([e for e in f.evidence if e.table == "poste_detail"]), 4)

    def test_evidence_points_to_real_rows(self):
        tables = {"source": self.bundle.source.df, "target": self.bundle.target.df,
                  "poste_detail": self.bundle.poste_detail.df, "motifs": self.bundle.motifs.df}
        for f in self.result.findings:
            for e in f.evidence:
                with self.subTest(f=f.finding_id, e=e.id):
                    self.assertIn(e.row, set(tables[e.table]["_row"]))

    def test_interpretation_agreement(self):
        rates = {(s.interpretation, s.target_field, s.choice): s.rate for s in self.result.interpretation_stats}
        self.assertGreater(rates[("INT-ASSIGN-DATES", "assignmentStartDate", "interval_intersection")], 0.8)
        self.assertEqual(rates[("INT-ASSIGN-DATES", "assignmentStartDate", "strict_literal")], 0.0)
        self.assertEqual(rates[("INT-CONCAT-PADDING", "divisionName", "zero_padded")], 1.0)

    def test_deterministic(self):
        again = corroborate(self.bundle, self.cfg, strict=True)
        self.assertEqual([f.to_record() for f in self.result.findings],
                         [f.to_record() for f in again.findings])

    def test_override(self):
        res = corroborate(self.bundle, self.cfg, {"INT-ASSIGN-DATES": "strict_literal"}, strict=True)
        starts = [f for f in res.findings if f.target_field == "assignmentStartDate"]
        self.assertTrue(all(f.verdict is Verdict.ANOMALIE for f in starts))
        self.assertEqual(res.overrides, {"INT-ASSIGN-DATES": "strict_literal"})

    def test_invalid_override(self):
        for ov in ({"INT-X": "a"}, {"INT-ASSIGN-DATES": "devinette"}):
            with self.subTest(ov=ov), self.assertRaises(ValueError):
                corroborate(self.bundle, self.cfg, ov)

    def test_rule_error_does_not_crash_report(self):
        def boom(ctx):
            raise RuntimeError("panne simulée")
        with mock.patch.dict("corroborai.rules.REGISTRY", {"direct": boom}):
            res = corroborate(self.bundle, self.cfg)
        broken = [f for f in res.findings if f.subcategory == "erreur d'évaluation"]
        self.assertTrue(broken)
        self.assertTrue(all(f.verdict is Verdict.INDETERMINE for f in broken))
        self.assertEqual(len(res.errors), len(broken))
        with mock.patch.dict("corroborai.rules.REGISTRY", {"direct": boom}), self.assertRaises(RuntimeError):
            corroborate(self.bundle, self.cfg, strict=True)


if __name__ == "__main__":
    unittest.main()
