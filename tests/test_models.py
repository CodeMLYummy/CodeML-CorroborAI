import unittest
from datetime import date

from corroborai.models import (
    Confidence,
    DecisionSource,
    Evidence,
    Finding,
    HypothesisResult,
    ModelError,
    Verdict,
)


def make(**overrides):
    base = dict(
        person_id="1545850",
        assignment_key="P",
        target_field="divisionName",
        source_fields=("CodeDirection", "LibelléDirection"),
        source_raw="UnitAdmin00397",
        target_raw="00397-UnitAdmin00397",
        expected="00397-UnitAdmin00397",
        target_norm="00397-UnitAdmin00397",
        verdict=Verdict.JUSTIFIE,
        decision_source=DecisionSource.REGLE,
        rule_id="R-CONCAT-DIVISION",
        justification="Concaténation code + « - » + libellé (Mapping.xlsx).",
    )
    base.update(overrides)
    return Finding(**base)


class TestFindingInvariants(unittest.TestCase):
    def test_valid_finding(self):
        f = make()
        self.assertEqual(f.finding_id, "1545850:P:divisionName")
        self.assertEqual(f.confidence, Confidence.ELEVEE)

    def test_ai_cannot_set_verdict(self):
        with self.assertRaises(ModelError) as ctx:
            make(decision_source=DecisionSource.IA)
        self.assertIn("IA", str(ctx.exception))

    def test_ai_cannot_set_verdict_via_string(self):
        with self.assertRaises(ModelError):
            make(decision_source="IA")

    def test_justified_requires_rule(self):
        with self.assertRaises(ModelError):
            make(rule_id=None)

    def test_anomaly_without_rule_allowed(self):
        f = make(verdict=Verdict.ANOMALIE, rule_id=None, justification="Valeurs différentes.")
        self.assertIs(f.verdict, Verdict.ANOMALIE)

    def test_justification_required(self):
        with self.assertRaises(ModelError):
            make(justification="")

    def test_string_coercion(self):
        f = make(verdict="ANOMALIE", decision_source="REGLE_DETERMINISTE", confidence="MOYENNE")
        self.assertIs(f.verdict, Verdict.ANOMALIE)
        self.assertIs(f.confidence, Confidence.MOYENNE)
        with self.assertRaises(ValueError):
            make(verdict="PEUT_ETRE")

    def test_expert_can_set_verdict(self):
        f = make(decision_source=DecisionSource.EXPERT)
        self.assertIs(f.decision_source, DecisionSource.EXPERT)


class TestEvidence(unittest.TestCase):
    def test_evidence_requires_id_and_table(self):
        with self.assertRaises(ModelError):
            Evidence(id="", table="source", row=2, values={})
        with self.assertRaises(ModelError):
            Evidence(id="e1", table="", row=2, values={})

    def test_duplicate_evidence_rejected(self):
        e = Evidence("e1", "source", 2, {"CodeDirection": "397"})
        with self.assertRaises(ModelError):
            make(evidence=[e, e])
        f = make(evidence=[e])
        with self.assertRaises(ModelError):
            f.add_evidence(e)

    def test_hypothesis_must_cite_existing_evidence(self):
        e = Evidence("e1", "poste_detail", 63, {"HeuresSemaineContrat": "40"})
        ok = HypothesisResult("H-JOIN-COLUMN", "Valeur du détail du poste", True, ("e1",))
        make(evidence=[e], hypotheses=[ok])
        bad = HypothesisResult("H-JOIN-COLUMN", "…", True, ("e1", "e404"))
        with self.assertRaises(ModelError):
            make(evidence=[e], hypotheses=[bad])


class TestSerialization(unittest.TestCase):
    def test_to_record_is_flat(self):
        e = Evidence("e1", "source", 2, {"x": 1})
        h = HypothesisResult("H-SYSTEMIC", "…", True, ("e1",), systemic=True)
        f = make(expected=date(2016, 4, 1), evidence=[e], hypotheses=[h],
                 rule_params={"interpretation": "interval_intersection"})
        rec = f.to_record()
        self.assertEqual(rec["expected"], "2016-04-01")
        self.assertEqual(rec["verdict"], "JUSTIFIE")
        self.assertEqual(rec["evidence"], "e1@source:2")
        self.assertEqual(rec["hypotheses"], "H-SYSTEMIC*")
        self.assertEqual(rec["rule_params"], "interpretation=interval_intersection")
        for v in rec.values():
            self.assertIsInstance(v, (str, int, float, type(None)))

    def test_to_dict(self):
        d = make().to_dict()
        self.assertEqual(d["verdict"], "JUSTIFIE")
        self.assertEqual(d["decision_source"], "REGLE_DETERMINISTE")
        self.assertEqual(d["finding_id"], "1545850:P:divisionName")


if __name__ == "__main__":
    unittest.main()
