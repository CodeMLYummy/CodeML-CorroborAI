import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import yaml

from corroborai.cli import main
from corroborai.engine import corroborate
from corroborai.feedback import (
    CORRECTION_RULE_ID,
    ExpertRule,
    FeedbackError,
    FeedbackStore,
    preview_rule,
    suggest_rules,
    validate_rule,
)
from corroborai.io.loaders import load_bundle
from corroborai.models import DecisionSource, Verdict
from corroborai.rules_config import load_rules
from tests._helpers import DATA_DIR, requires_data

CFG = load_rules()
FIELDS = set(CFG.targets)


def rule(op, fld, params, reason="Décision de l'équipe fonctionnelle."):
    return ExpertRule("", op, fld, params, reason)


class TestRuleValidation(unittest.TestCase):
    def test_valid(self):
        for r in (rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"}),
                  rule("accept_subcategory", "contactEmail", {"subcategory": "x"}),
                  rule("accept_value_mapping", "positionName", {"mapping": {"a": "b"}}),
                  rule("accept_alternative_source", "weeklyHoursOverride", {"column": "c"})):
            with self.subTest(op=r.operation):
                self.assertEqual(validate_rule(r, FIELDS), [])

    def test_invalid(self):
        cases = {
            "opération": rule("supprimer_champ", "siteName", {}),
            "champ": rule("accept_hypothesis", "inconnu", {"hypothesis": "H-SYSTEMIC"}),
            "paramètres": rule("accept_hypothesis", "siteName", {"hyp": "H-SYSTEMIC"}),
            "hypothèse": rule("accept_hypothesis", "siteName", {"hypothesis": "H-MAGIE"}),
            "recodage vide": rule("accept_value_mapping", "siteName", {"mapping": {}}),
            "recodage non textuel": rule("accept_value_mapping", "siteName", {"mapping": {"a": 1}}),
            "justification": rule("accept_subcategory", "siteName", {"subcategory": "x"}, reason=" "),
        }
        for name, r in cases.items():
            with self.subTest(case=name):
                self.assertTrue(validate_rule(r, FIELDS))


class TestStore(unittest.TestCase):
    def test_roundtrip_and_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fb" / "r.yaml"
            s = FeedbackStore()
            a = s.add_rule(rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"}), FIELDS)
            b = s.add_rule(rule("accept_subcategory", "contactEmail", {"subcategory": "x"}), FIELDS)
            self.assertEqual((a.id, b.id), ("R-EXPERT-001", "R-EXPERT-002"))
            s.save(path)
            self.assertFalse(path.with_suffix(".yaml.tmp").exists())
            loaded = FeedbackStore.load(path, FIELDS)
            self.assertEqual([r.id for r in loaded.rules], ["R-EXPERT-001", "R-EXPERT-002"])
            loaded.remove_rule("R-EXPERT-001")
            self.assertEqual(loaded.next_rule_id(), "R-EXPERT-003")

    def test_missing_file_is_empty(self):
        self.assertEqual(FeedbackStore.load("/inexistant/r.yaml").rules, [])

    def test_invalid_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "r.yaml"
            p.write_text(yaml.safe_dump({"version": 1, "rules": [
                {"id": "R1", "operation": "accept_hypothesis", "field": "siteName",
                 "params": {"hypothesis": "H-X"}, "reason": "x"}]}), encoding="utf-8")
            with self.assertRaises(FeedbackError):
                FeedbackStore.load(p, FIELDS)
            p.write_text(yaml.safe_dump({"version": 9}), encoding="utf-8")
            with self.assertRaises(FeedbackError):
                FeedbackStore.load(p)

    def test_duplicate_rule_rejected(self):
        s = FeedbackStore()
        r = s.add_rule(rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"}), FIELDS)
        dup = rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"})
        dup.id = r.id
        with self.assertRaises(FeedbackError):
            s.add_rule(dup, FIELDS)


@requires_data
class TestApplication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA_DIR)

    def base(self, store=None):
        return corroborate(self.bundle, CFG, strict=True, feedback=store)

    def test_preview_does_not_mutate(self):
        res = self.base()
        before = [(f.finding_id, f.verdict) for f in res.findings]
        col = next(c.column for c in res.analysis.candidate_rules if c.target_field == "weeklyHoursOverride")
        p = preview_rule(rule("accept_alternative_source", "weeklyHoursOverride", {"column": col}),
                         res.findings, res.rule_context, FIELDS)
        self.assertEqual(len(p.matched), 4)
        self.assertEqual(p.remaining, 0)
        self.assertEqual(before, [(f.finding_id, f.verdict) for f in res.findings])

    def test_preview_of_invalid_or_empty_rule(self):
        res = self.base()
        self.assertFalse(preview_rule(rule("accept_hypothesis", "x", {"hypothesis": "H-SYSTEMIC"}),
                                      res.findings, res.rule_context, FIELDS).ok)
        p = preview_rule(rule("accept_hypothesis", "siteName", {"hypothesis": "H-SYSTEMIC"}),
                         res.findings, res.rule_context, FIELDS)
        self.assertTrue(p.ok)
        self.assertEqual(p.matched, [])
        self.assertIn("aucun verdict", p.summary())

    def test_rules_applied_in_engine(self):
        store = FeedbackStore()
        res0 = self.base()
        col = next(c.column for c in res0.analysis.candidate_rules if c.target_field == "weeklyHoursOverride")
        store.add_rule(rule("accept_alternative_source", "weeklyHoursOverride", {"column": col}), FIELDS)
        store.add_rule(rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"}), FIELDS)
        res = self.base(store)
        self.assertEqual(res.counts()["ANOMALIE"], res0.counts()["ANOMALIE"] - 4 - 22)
        hit = [f for f in res.findings if f.rule_id == "R-EXPERT-001"]
        self.assertEqual(len(hit), 4)
        for f in hit:
            self.assertIs(f.verdict, Verdict.JUSTIFIE)
            self.assertIs(f.decision_source, DecisionSource.EXPERT)
            self.assertIsNone(f.priority)
            self.assertIn("Verdict initial ANOMALIE", f.justification)
            self.assertEqual(f.rule_params["verdict_initial"], "ANOMALIE")
        # Les écarts conformes ne sont jamais touchés par une règle experte
        self.assertEqual(res.counts()["CONFORME"], res0.counts()["CONFORME"])

    def test_corrections_and_staleness(self):
        res0 = self.base()
        store = FeedbackStore()
        email = next(f for f in res0.findings if f.target_field == "contactEmail")
        site = next(f for f in res0.findings if f.target_field == "siteCode")
        store.add_correction(email, "JUSTIFIE", "Artefact d'anonymisation confirmé.", "QA")
        store.add_correction(site, "ANOMALIE", "Code de site à revoir.")
        res = self.base(store)
        fe = next(f for f in res.findings if f.finding_id == email.finding_id)
        fs = next(f for f in res.findings if f.finding_id == site.finding_id)
        self.assertEqual((fe.verdict, fe.rule_id), (Verdict.JUSTIFIE, "R-EMAIL"))
        self.assertIs(fs.verdict, Verdict.ANOMALIE)
        self.assertIsNotNone(fs.priority)  # une anomalie déclarée par l'expert est priorisée
        self.assertEqual(sorted(res.feedback.corrections_applied), sorted([email.finding_id, site.finding_id]))
        # Données modifiées → correction périmée, non appliquée
        store.corrections[0].fingerprint["target_raw"] = "autre@exemple.com"
        res2 = self.base(store)
        self.assertEqual(res2.feedback.corrections_stale, [email.finding_id])
        self.assertIs(next(f for f in res2.findings if f.finding_id == email.finding_id).verdict, Verdict.ANOMALIE)

    def test_correction_rule_id_when_none(self):
        res0 = self.base()
        f = next(x for x in res0.findings if x.target_field == "affectation")
        store = FeedbackStore()
        store.add_correction(f, "JUSTIFIE", "Affectation temporaire non gérée dans Temps, accepté.")
        g = next(x for x in self.base(store).findings if x.finding_id == f.finding_id)
        self.assertEqual(g.rule_id, "R-MATCH")
        f.rule_id = None
        store2 = FeedbackStore()
        store2.add_correction(f, "JUSTIFIE", "x")
        self.assertEqual(store2.corrections[0].verdict, "JUSTIFIE")
        self.assertEqual(CORRECTION_RULE_ID, "EXPERT-CORRECTION")

    def test_correction_requires_reason(self):
        res = self.base()
        with self.assertRaises(FeedbackError):
            FeedbackStore().add_correction(res.findings[0], "ANOMALIE", "  ")

    def test_suggestions_from_repeated_corrections(self):
        res = self.base()
        store = FeedbackStore()
        emails = [f for f in res.findings if f.target_field == "contactEmail"][:2]
        for f in emails:
            store.add_correction(f, "JUSTIFIE", "Artefact d'anonymisation.")
        sugg = suggest_rules(store, res.findings)
        self.assertEqual({(r.operation, r.field) for r in sugg},
                         {("accept_hypothesis", "contactEmail"), ("accept_subcategory", "contactEmail")})
        self.assertTrue(all(r.provenance == "SUGGESTION" for r in sugg))
        store.add_rule(sugg[0], FIELDS)
        self.assertEqual(len(suggest_rules(store, res.findings)), 1)  # déjà créée → plus suggérée

    def test_cli_retroaction(self):
        store = FeedbackStore()
        store.add_rule(rule("accept_hypothesis", "positionName", {"hypothesis": "H-BIJECTION"}), FIELDS)
        with tempfile.TemporaryDirectory() as tmp:
            p = store.save(Path(tmp) / "r.yaml")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["run", "--data-dir", str(DATA_DIR), "--out", tmp, "--retroaction", str(p)])
            self.assertEqual(code, 0)
            self.assertIn("Rétroaction experte : 22", out.getvalue())


if __name__ == "__main__":
    unittest.main()
