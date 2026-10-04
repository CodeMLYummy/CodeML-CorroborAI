import copy
import json
import unittest

from corroborai.ai.schemas import SYNTHESE_EMPLOYE, TRIAGE_ANOMALIE, schema_description, validate
from tests._ai_helpers import VALID_EMPLOYEE, mini_dossier


def check(obj, task=SYNTHESE_EMPLOYE, raw=None):
    return validate(task, raw if raw is not None else json.dumps(obj, ensure_ascii=False), mini_dossier(task))


def mutated(**changes):
    o = copy.deepcopy(VALID_EMPLOYEE)
    o.update(changes)
    return o


class TestValidation(unittest.TestCase):
    def test_valid(self):
        rep = check(VALID_EMPLOYEE)
        self.assertTrue(rep.ok, rep.errors)

    def test_markdown_fence_accepted(self):
        self.assertTrue(check(None, raw="```json\n" + json.dumps(VALID_EMPLOYEE) + "\n```").ok)

    def test_invalid_json(self):
        rep = check(None, raw="Voici ma réponse : {")
        self.assertFalse(rep.ok)
        self.assertIn("JSON invalide", rep.errors[0])

    def test_rejections(self):
        cases = {
            "champ verdict interdit": mutated(verdict="CONFORME"),
            "clé manquante": {k: v for k, v in VALID_EMPLOYEE.items() if k != "pistes"},
            "type": mutated(pistes="une piste"),
            "trop de pistes": mutated(pistes=["Vérifier ceci en détail."] * 5),
            "texte trop court": mutated(synthese="ok"),
            "preuve inventée": mutated(preuves_citees=["src:999"]),
            "preuves dupliquées": mutated(preuves_citees=["src:14", "src:14"]),
            "référence inventée": mutated(regroupements=[{"references": ["E9"], "cause_commune": "Une cause commune"}]),
            "date non ancrée": mutated(synthese="EMP-01 : écart constaté depuis le 2019-04-29 sur le site."),
            "nombre non ancré": mutated(synthese="EMP-01 présente 4500 écarts sur le libellé du site."),
            "employé non ancré": mutated(synthese="EMP-01 et EMP-07 présentent des écarts sur le site."),
            "valeur citée non ancrée": mutated(pistes=["Remplacer par « Emplacement99 » dans la cible."]),
        }
        for name, obj in cases.items():
            with self.subTest(case=name):
                self.assertFalse(check(obj).ok)

    def test_triage_confidence_never_high(self):
        base = {"categorie": "SAISIE_CIBLE", "justification": "Écart isolé sur le libellé du site.",
                "piste": "Vérifier la saisie dans la cible.", "preuves_citees": ["src:14"], "confiance": "faible"}
        self.assertTrue(check(base, TRIAGE_ANOMALIE).ok)
        self.assertFalse(check({**base, "confiance": "élevée"}, TRIAGE_ANOMALIE).ok)
        self.assertFalse(check({**base, "categorie": "ERREUR_HUMAINE"}, TRIAGE_ANOMALIE).ok)

    def test_schema_description(self):
        self.assertIn("preuves_citees", schema_description(SYNTHESE_EMPLOYE))


if __name__ == "__main__":
    unittest.main()
