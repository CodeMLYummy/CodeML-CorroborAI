import json
import os
import unittest
from unittest import mock

from corroborai.ai.policy import Pseudonymizer, check_flow, load_llm_config
from corroborai.ai.translate import translate_feedback, translation_dossier
from corroborai.engine import corroborate
from corroborai.feedback import preview_rule
from corroborai.io.loaders import load_bundle
from corroborai.rules_config import load_rules
from tests._ai_helpers import ScriptedTransport, user_text
from tests._helpers import DATA_DIR, requires_data


def answer(**kw):
    base = {"operation": "accept_hypothesis", "champ": "positionName",
            "parametres": {"hypothesis": "H-BIJECTION"},
            "reformulation": "Accepter les écarts de positionName expliqués par un recodage systématique.",
            "confiance": "moyenne"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


@requires_data
class TestTranslation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA_DIR)
        cls.cfg = load_rules()
        cls.result = corroborate(cls.bundle, cls.cfg, strict=True)

    def tr(self, answers, text="Les libellés d'emploi sont recodés par l'anonymisation, c'est normal."):
        t = ScriptedTransport(answers)
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}):
            return translate_feedback(text, self.result, self.bundle, provider_name="gemini", transport=t), t

    def test_valid_translation(self):
        tr, _ = self.tr([answer()])
        self.assertEqual(tr.status, "LLM_VALIDE")
        self.assertEqual((tr.rule.operation, tr.rule.field), ("accept_hypothesis", "positionName"))
        self.assertTrue(tr.rule.provenance.startswith("IA:"))
        p = preview_rule(tr.rule, self.result.findings, self.result.rule_context, set(self.cfg.targets))
        self.assertEqual(len(p.matched), 22)

    def test_ungrounded_parameters_trigger_retry_then_fallback(self):
        bad_col = answer(operation="accept_alternative_source", champ="weeklyHoursOverride",
                         parametres={"column": "source.ColonneInventée"})
        bad_hyp = answer(parametres={"hypothesis": "H-PERMUTATION"})   # non observée pour ce champ
        tr, t = self.tr([bad_col, bad_hyp])
        self.assertIsNone(tr.rule)
        self.assertEqual(tr.status, "REPLI_VALIDATION")
        self.assertIn("ColonneInventée", user_text(t.requests[1][2]))  # erreurs renvoyées au modèle

    def test_hallucinated_mapping_rejected(self):
        tr, _ = self.tr([answer(operation="accept_value_mapping", parametres={"mapping": {"ZZZ": "YYY"}})] * 2)
        self.assertIsNone(tr.rule)

    def test_unknown_field_and_extra_keys(self):
        tr, _ = self.tr([answer(champ="salaire"), json.dumps({**json.loads(answer()), "verdict": "CONFORME"})])
        self.assertIsNone(tr.rule)

    def test_model_may_decline(self):
        tr, _ = self.tr([answer(operation="AUCUNE", champ="", parametres={},
                                reformulation="La consigne demande de supprimer des données : hors périmètre.")])
        self.assertIsNone(tr.rule)
        self.assertEqual(tr.status, "LLM_VALIDE")

    def test_template_provider(self):
        tr = translate_feedback("n'importe quoi", self.result, self.bundle)
        self.assertIsNone(tr.rule)
        self.assertEqual(tr.source, "GABARIT")

    def test_expert_text_is_pseudonymized_and_dossier_allowed(self):
        text = "Pour 4625374 et pnom4625374374@loto-quebec.com, accepter les écarts de libellé d'emploi."
        tr, t = self.tr([answer()], text)
        sent = user_text(t.requests[0][2])
        self.assertNotIn("4625374", sent)
        pseudo = Pseudonymizer.from_bundle(self.bundle)
        d, _ = translation_dossier(text, self.result, pseudo)
        cfg = load_llm_config()
        self.assertTrue(check_flow(d, cfg.provider("gemini"), cfg.flow, pseudo).allowed)

    def test_empty_text(self):
        self.assertEqual(translate_feedback("  ", self.result, self.bundle).status, "VIDE")


if __name__ == "__main__":
    unittest.main()
