"""Tests unitaires de chaque type de règle sur des cas synthétiques."""

import unittest
from datetime import date

from corroborai.models import Confidence, Verdict
from corroborai.rules import REGISTRY
from corroborai.rules.base import PosteRecord, Refs, RuleContext
from corroborai.rules_config import KNOWN_RULE_TYPES, load_rules

CFG = load_rules()
MOTIF_807 = {"_row": 33, "CodeCatégorieStatut": "807", "CodeStatutSystèmeExterne": "170", "CodeGestionAccès": "2"}
REFS = Refs(
    poste_history={"1": [PosteRecord(date(2001, 1, 1), "348", 10, {})],
                   "2": [PosteRecord(date(1990, 1, 1), "320", 11, {}),
                         PosteRecord(date(2010, 1, 1), "352", 12, {})]},
    motifs={"807": MOTIF_807},
)
BASE_SRC = {"_row": 2, "Matricule": "1234567", "PrénomUsuel": "Éric", "NomFamille": "Bérubé",
            "CodeSuspensionAccès": "1", "CodeStatutEmploi": "1", "CodeRaisonStatut": "703",
            "DateRetourAnticipée": None, "CatégorieEmploi": "V", "EstPermanent": "Oui",
            "EstTempsPlein": "Oui", "TypeAffectation": "P", "CodeDirection": "348",
            "LibelléDirection": "UnitAdmin00348", "CodeEmploi": "6203", "IntituléEmploi": "Empl6203",
            "DateEntréePoste": "2009-03-30 00:00:00", "DateSortiePoste": None, "CodePoste": "1",
            "DateEmbaucheRécente": "2003-12-12 00:00:00", "HeuresNormeHebdo": "40"}


def run(target_field, tgt_value, overrides=None, **src_overrides):
    fr = CFG.field(target_field)
    ctx = RuleContext(CFG, fr, {**BASE_SRC, **src_overrides}, {"_row": 5, target_field: tgt_value},
                      REFS, overrides or {})
    return REGISTRY[fr.rule](ctx)


class TestRegistry(unittest.TestCase):
    def test_all_rule_types_implemented(self):
        self.assertEqual(set(REGISTRY), set(KNOWN_RULE_TYPES))


class TestDirect(unittest.TestCase):
    def test_conforme_after_format_normalization(self):
        out = run("onboardDate", "2003-12-12T00:00:00.000Z")
        self.assertIs(out.verdict, Verdict.CONFORME)

    def test_numbers(self):
        self.assertIs(run("weeklyHoursOverride", "40.0").verdict, Verdict.CONFORME)
        out = run("weeklyHoursOverride", "35")
        self.assertIs(out.verdict, Verdict.ANOMALIE)
        self.assertIn("« 40 »", out.justification)

    def test_empty_both_and_one(self):
        self.assertIs(run("weeklyHoursOverride", None, HeuresNormeHebdo=None).verdict, Verdict.CONFORME)
        self.assertIs(run("weeklyHoursOverride", "40", HeuresNormeHebdo=None).verdict, Verdict.ANOMALIE)

    def test_unreadable_target(self):
        out = run("onboardDate", "pas une date")
        self.assertIs(out.verdict, Verdict.INDETERMINE)
        self.assertIs(out.confidence, Confidence.FAIBLE)


class TestNames(unittest.TestCase):
    def test_exact(self):
        self.assertIs(run("givenName", "Éric").verdict, Verdict.CONFORME)

    def test_accents_justified(self):
        out = run("surname", "Berube")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertIs(out.confidence, Confidence.MOYENNE)

    def test_strict_interpretation(self):
        self.assertIs(run("surname", "Berube", {"INT-NAME-ACCENTS": "strict"}).verdict, Verdict.ANOMALIE)

    def test_real_difference(self):
        self.assertIs(run("surname", "Tremblay").verdict, Verdict.ANOMALIE)


class TestEmail(unittest.TestCase):
    def test_exact(self):
        out = run("contactEmail", "eberube567@loto-quebec.com")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertIs(out.confidence, Confidence.ELEVEE)
        self.assertEqual(out.expected, "eberube567@loto-quebec.com")

    def test_env_prefix_and_case(self):
        out = run("contactEmail", "dev-08-v2_EBerube567@loto-quebec.com")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertEqual(out.subcategory, "préfixe d'environnement")
        self.assertIs(out.confidence, Confidence.MOYENNE)

    def test_case_only(self):
        out = run("contactEmail", "EBerube567@loto-quebec.com")
        self.assertEqual((out.verdict, out.subcategory), (Verdict.JUSTIFIE, "casse"))

    def test_identifier_mismatch(self):
        out = run("contactEmail", "dev-08-v2_EBerube10370370@loto-quebec.com", NomFamille="Bérubé")
        self.assertIs(out.verdict, Verdict.ANOMALIE)
        self.assertEqual(out.subcategory, "identifiant incohérent avec le matricule")
        self.assertIn("10370370", out.justification)

    def test_wrong_domain(self):
        out = run("contactEmail", "eberube567@exemple.com")
        self.assertEqual((out.verdict, out.subcategory), (Verdict.ANOMALIE, "domaine incorrect"))

    def test_missing(self):
        self.assertEqual(run("contactEmail", None).subcategory, "courriel absent")

    def test_strict(self):
        out = run("contactEmail", "dev-08-v2_eberube567@loto-quebec.com", {"INT-EMAIL": "strict"})
        self.assertIs(out.verdict, Verdict.ANOMALIE)

    def test_missing_source(self):
        self.assertIs(run("contactEmail", "x@loto-quebec.com", PrénomUsuel=None).verdict, Verdict.INDETERMINE)


class TestConcat(unittest.TestCase):
    def test_padded(self):
        out = run("divisionName", "00348-UnitAdmin00348")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)

    def test_unpadded_alternative(self):
        out = run("divisionName", "00348-UnitAdmin00348", {"INT-CONCAT-PADDING": "unpadded"})
        self.assertIs(out.verdict, Verdict.ANOMALIE)

    def test_code_and_label_mismatch(self):
        out = run("positionName", "4367-Empl4367")
        self.assertIs(out.verdict, Verdict.ANOMALIE)
        self.assertEqual(out.subcategory, "code et libellé différents")

    def test_label_only_mismatch(self):
        self.assertEqual(run("positionName", "6203-Autre").subcategory, "libellé différent")

    def test_empty_source(self):
        self.assertIs(run("divisionName", "x", LibelléDirection=None).verdict, Verdict.INDETERMINE)


class TestSituation(unittest.TestCase):
    def test_active(self):
        self.assertIs(run("detailedStatus", "Actif").verdict, Verdict.JUSTIFIE)
        self.assertIs(run("statusReasonCode", None).verdict, Verdict.JUSTIFIE)
        self.assertIs(run("expectedReturnDate", None).verdict, Verdict.CONFORME)

    def test_active_with_reason_is_anomaly(self):
        self.assertIs(run("statusReasonCode", "170").verdict, Verdict.ANOMALIE)

    def test_absence_with_motif_and_mojibake(self):
        absent = dict(CodeSuspensionAccès="2", CodeStatutEmploi="2", CodeRaisonStatut="807",
                      DateRetourAnticipée="2015-06-17 00:00:00")
        out = run("detailedStatus", "Absence complÃ¨te", **absent)
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertIn("Encodage", out.justification)
        out = run("statusReasonCode", "170", **absent)
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertIn("motif:33", [e.id for e in out.evidence])
        self.assertIs(run("expectedReturnDate", "2015-06-17", **absent).verdict, Verdict.CONFORME)

    def test_absence_motif_missing(self):
        out = run("statusReasonCode", "170", CodeSuspensionAccès="2", CodeStatutEmploi="2",
                  CodeRaisonStatut="999")
        self.assertIs(out.verdict, Verdict.INDETERMINE)
        self.assertIn("INT-MOTIF-MISSING", out.justification)

    def test_unlisted_code(self):
        out = run("detailedStatus", "Cessation", CodeSuspensionAccès="4", CodeStatutEmploi="4")
        self.assertIs(out.verdict, Verdict.INDETERMINE)

    def test_cross_check_inconsistency_lowers_confidence(self):
        out = run("detailedStatus", "Actif", CodeStatutEmploi="2")
        self.assertIs(out.confidence, Confidence.MOYENNE)
        self.assertIn("incohérent", out.justification)

    def test_alternative_situation_source(self):
        out = run("detailedStatus", "Absence complète", {"INT-SITUATION-CODE": "code_statut_emploi"},
                  CodeStatutEmploi="2")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)


class TestContractAndAssignment(unittest.TestCase):
    def test_contract(self):
        self.assertIs(run("contractTypeCode", "JWN").verdict, Verdict.JUSTIFIE)
        self.assertIs(run("contractTypeCode", "XFLR").verdict, Verdict.ANOMALIE)
        self.assertIs(run("contractTypeCode", "JWN", EstPermanent="Non").verdict, Verdict.INDETERMINE)

    def test_assignment_flags(self):
        self.assertIs(run("isPrimaryAssignment", "true").verdict, Verdict.JUSTIFIE)
        self.assertIs(run("isTemporaryAssignment", "true").verdict, Verdict.ANOMALIE)
        self.assertIs(run("isTemporaryAssignment", "true", TypeAffectation="A").verdict, Verdict.JUSTIFIE)
        self.assertIs(run("isPrimaryAssignment", "true", TypeAffectation="X").verdict, Verdict.INDETERMINE)


class TestAssignmentDatesRule(unittest.TestCase):
    def test_start_conforme_and_evidence(self):
        out = run("assignmentStartDate", "2009-03-30 00:00:00")
        self.assertIs(out.verdict, Verdict.CONFORME)
        self.assertEqual([e.id for e in out.evidence], ["poste:10"])

    def test_start_strict_literal(self):
        out = run("assignmentStartDate", "2009-03-30", {"INT-ASSIGN-DATES": "strict_literal"})
        self.assertIs(out.verdict, Verdict.ANOMALIE)
        self.assertEqual(out.expected, date(2001, 1, 1))

    def test_start_justified_by_unit_change(self):
        # Poste 2 : unité 352 depuis 2010-01-01, entrée 2009-03-30 → début = 2010-01-01
        out = run("assignmentStartDate", "2010-01-01", CodePoste="2", CodeDirection="352")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)

    def test_end_from_unit_change(self):
        # Unité courante 320 suivie de 352 le 2010-01-01 → fin = 2009-12-31
        out = run("assignmentEndDate", "2009-12-31", CodePoste="2", CodeDirection="320")
        self.assertIs(out.verdict, Verdict.JUSTIFIE)
        self.assertIs(run("assignmentEndDate", None).verdict, Verdict.CONFORME)

    def test_missing_history_lowers_confidence(self):
        out = run("assignmentStartDate", "2009-03-30", CodePoste="404")
        self.assertIs(out.verdict, Verdict.CONFORME)
        self.assertIs(out.confidence, Confidence.MOYENNE)


if __name__ == "__main__":
    unittest.main()
