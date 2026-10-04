"""Tests du mapping codifié.

* Validation interne : chaque test par mutation injecte UNE erreur dans une
  copie de rules.yaml et exige qu'elle soit détectée (prouve que le validateur
  n'est pas trivialement permissif).
* Validation contre les données : couverture complète de Mapping.xlsx,
  références de lignes exactes, jointures vérifiées empiriquement.
"""

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from corroborai.io.loaders import load_bundle
from corroborai.io.normalize import FieldKind, norm_code
from corroborai.rules_config import (
    DEFAULT_RULES,
    KNOWN_RULE_TYPES,
    MappingRef,
    RulesConfigError,
    load_rules,
    validate_against_data,
)
from tests._helpers import DATA_DIR, requires_data

RAW = yaml.safe_load(DEFAULT_RULES.read_text(encoding="utf-8"))


def load_mutated(mutate):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        yaml.safe_dump(raw, fh, allow_unicode=True)
        path = Path(fh.name)
    try:
        return load_rules(path)
    finally:
        path.unlink()


def field_spec(raw, target):
    return next(f for f in raw["fields"] if f["target"] == target)


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.cfg = load_rules()

    def test_basic(self):
        self.assertEqual(self.cfg.version, 1)
        self.assertEqual(len(self.cfg.targets), len(set(self.cfg.targets)))
        self.assertIn("contactEmail", self.cfg.targets)

    def test_every_field_is_traceable(self):
        for f in self.cfg.fields:
            with self.subTest(f=f.target):
                self.assertIn(f.rule, KNOWN_RULE_TYPES)
                self.assertTrue(1 <= f.criticality <= 5)
                self.assertTrue(f.criticality_reason)
                self.assertTrue(f.rule_id.startswith("R-"))
                self.assertIsInstance(f.kind, FieldKind)

    def test_every_interpretation_is_documented_and_used(self):
        used = {f.interpretation for f in self.cfg.fields if f.interpretation}
        for iid, i in self.cfg.interpretations.items():
            with self.subTest(i=iid):
                self.assertTrue(i.summary and i.rationale)
                self.assertNotIn(i.choice, i.alternatives)
        # Les interprétations non rattachées à un champ concernent les cas de repli
        unused = set(self.cfg.interpretations) - used
        self.assertLessEqual(unused, {"INT-SITUATION-UNLISTED", "INT-MOTIF-MISSING", "INT-EMPTY"})

    def test_mapping_ref_parse(self):
        self.assertEqual(MappingRef.parse("Règles situation d'emploi!3"),
                         MappingRef("Règles situation d'emploi", 3))
        for bad in ("Mapping", "Mapping!", "!3", "Mapping!x", None):
            with self.subTest(bad=bad), self.assertRaises(RulesConfigError):
                MappingRef.parse(bad)


class TestRuleTables(unittest.TestCase):
    def setUp(self):
        self.cfg = load_rules()

    def test_contract_types(self):
        cases = [
            ({"CatégorieEmploi": "V", "EstPermanent": "Oui", "EstTempsPlein": "Oui"}, "JWN"),
            ({"CatégorieEmploi": "V", "EstPermanent": "Oui", "EstTempsPlein": "Non"}, "XFLR"),
            ({"CatégorieEmploi": "O", "EstPermanent": "Non", "EstTempsPlein": "Non"}, "WHX"),
            ({"CatégorieEmploi": "T", "EstPermanent": "Non", "EstTempsPlein": "Oui"}, "KELH"),
            ({"CatégorieEmploi": "Q"}, "TRSY"),
        ]
        for row, code in cases:
            with self.subTest(row=row):
                self.assertEqual(self.cfg.contract_type_for(row).code, code)

    def test_contract_types_unmatched(self):
        for row in ({"CatégorieEmploi": "V", "EstPermanent": "Non", "EstTempsPlein": "Oui"},
                    {"CatégorieEmploi": "X"},
                    {"CatégorieEmploi": "V", "EstPermanent": "peut-être", "EstTempsPlein": "Oui"},
                    {}):
            with self.subTest(row=row):
                self.assertIsNone(self.cfg.contract_type_for(row))

    def test_contract_result_independent_of_order(self):
        rows = [{"CatégorieEmploi": c, "EstPermanent": p, "EstTempsPlein": t}
                for c in "VTOMRJZQX" for p in ("Oui", "Non") for t in ("Oui", "Non")]
        forward = [self.cfg.contract_type_for(r) for r in rows]
        rev = load_mutated(lambda raw: raw["contract_types"].reverse())
        backward = [rev.contract_type_for(r) for r in rows]
        self.assertEqual([c and c.code for c in forward], [c and c.code for c in backward])

    def test_situations(self):
        self.assertEqual(self.cfg.situation_for("00").label, "Actif")
        self.assertEqual(self.cfg.situation_for("1").label, "Actif")
        self.assertEqual(self.cfg.situation_for("02").label, "Absence complète")
        self.assertEqual(self.cfg.situation_for(7).label, "Absence complète")
        for code in ("4", "5", "8", None, "", "abc"):
            with self.subTest(code=code):
                self.assertIsNone(self.cfg.situation_for(code))

    def test_assignment_types(self):
        self.assertEqual(self.cfg.assignment_type_for(True, False), "P")
        self.assertEqual(self.cfg.assignment_type_for(False, True), "A")
        self.assertEqual(self.cfg.assignment_type_for(False, False), "S")
        self.assertIsNone(self.cfg.assignment_type_for(True, True))
        self.assertIsNone(self.cfg.assignment_type_for(None, False))


class TestStructuralMutations(unittest.TestCase):
    """Chaque mutation doit être rejetée par load_rules."""

    MUTATIONS = {
        "règle inconnue": lambda r: field_spec(r, "givenName").update(rule="magie"),
        "criticité hors bornes": lambda r: field_spec(r, "givenName").update(criticality=6),
        "criticité booléenne": lambda r: field_spec(r, "givenName").update(criticality=True),
        "kind inconnu": lambda r: field_spec(r, "givenName").update(kind="flottant"),
        "champ dupliqué": lambda r: r["fields"].append(copy.deepcopy(field_spec(r, "surname"))),
        "interprétation absente": lambda r: field_spec(r, "contactEmail").pop("interpretation"),
        "interprétation inconnue": lambda r: field_spec(r, "contactEmail").update(interpretation="INT-X"),
        "référence invalide": lambda r: field_spec(r, "givenName").update(mapping_ref="Mapping"),
        "justif. criticité manquante": lambda r: field_spec(r, "givenName").pop("criticality_reason"),
        "concat sans pad": lambda r: field_spec(r, "divisionName")["params"].pop("pad_width"),
        "transcodage chevauchant": lambda r: r["contract_types"].append(
            {"when": {"CatégorieEmploi": "V"}, "code": "ZZZ"}),
        "code transcodage dupliqué": lambda r: r["contract_types"].append(
            {"when": {"CatégorieEmploi": "K"}, "code": "JWN"}),
        "situations chevauchantes": lambda r: r["situations"][1]["codes"].append(1),
        "mode reason_code inconnu": lambda r: r["situations"][1].update(reason_code="devinette"),
        "affectation dupliquée": lambda r: r["assignment_types"].update(
            X={"isPrimaryAssignment": True, "isTemporaryAssignment": False}),
        "jointure incomplète": lambda r: r["joins"]["motifs"]["columns"].pop("remphor"),
        "choix dans alternatives": lambda r: r["interpretations"]["INT-ASSIGN-DATES"].update(
            alternatives=["interval_intersection"]),
        "regex préfixe invalide": lambda r: r["interpretations"]["INT-EMAIL"]["params"].update(
            env_prefix_pattern="(["),
        "version": lambda r: r.update(version=2),
        "seuil d'appariement": lambda r: r["assignment_matching"].update(cross_type_min_score=1.5),
        "similarité vide": lambda r: r["assignment_matching"].update(similarity_fields=[]),
        "similarité kind": lambda r: r["assignment_matching"]["similarity_fields"][0].update(kind="x"),
        "criticité absence": lambda r: r["assignment_matching"]["missing_in_target"].update(criticality=0),
        "section appariement": lambda r: r.pop("assignment_matching"),
    }

    def test_mutations_rejected(self):
        for name, mutate in self.MUTATIONS.items():
            with self.subTest(mutation=name), self.assertRaises(RulesConfigError):
                load_mutated(mutate)


@requires_data
class TestAgainstData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA_DIR)
        cls.cfg = load_rules()

    def test_config_consistent_with_data(self):
        res = validate_against_data(self.cfg, self.bundle)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.warnings, [])

    def _errors_after(self, mutate):
        return validate_against_data(load_mutated(mutate), self.bundle).errors

    def test_detects_wrong_mapping_row(self):
        errs = self._errors_after(lambda r: field_spec(r, "surname").update(mapping_ref="Mapping!2"))
        self.assertTrue(any("ne mentionne pas" in e for e in errs), errs)

    def test_detects_empty_mapping_row(self):
        errs = self._errors_after(lambda r: field_spec(r, "surname").update(related_refs=["Mapping!5"]))
        self.assertTrue(any("vide ou inexistante" in e for e in errs), errs)

    def test_detects_uncovered_mapping_field(self):
        def drop(r):
            r["fields"] = [f for f in r["fields"] if f["target"] != "payGradeId"]
        errs = self._errors_after(drop)
        self.assertTrue(any("payGradeId" in e and "ni corroboré ni exclu" in e for e in errs), errs)

    def test_detects_missing_source_column(self):
        errs = self._errors_after(lambda r: field_spec(r, "givenName").update(sources=["Prenom"]))
        self.assertTrue(any("colonne source absente" in e for e in errs), errs)

    def test_detects_missing_target_column(self):
        errs = self._errors_after(lambda r: field_spec(r, "givenName").update(target="firstName"))
        self.assertTrue(any("firstName" in e for e in errs), errs)

    def test_detects_unknown_source_label(self):
        errs = self._errors_after(lambda r: r["source_label_aliases"].pop("EMPT_CD"))
        self.assertTrue(any("EMPT_CD" in e for e in errs), errs)

    def test_detects_bad_similarity_column(self):
        errs = self._errors_after(
            lambda r: r["assignment_matching"]["similarity_fields"][0].update(target="jobId"))
        self.assertTrue(any("jobId" in e for e in errs), errs)

    def test_detects_bad_join_column(self):
        errs = self._errors_after(
            lambda r: r["joins"]["motifs"]["columns"]["remphor"].update(table="CodeRemphor"))
        self.assertTrue(any("CodeRemphor" in e for e in errs), errs)

    def test_detects_wrong_support_row(self):
        errs = self._errors_after(lambda r: r["support_columns"][0].update(mapping_ref="Mapping!21"))
        self.assertTrue(any("attendu" in e for e in errs), errs)

    def test_warns_on_uncovered_contract_values(self):
        res = validate_against_data(
            load_mutated(lambda r: r["contract_types"].pop(3)), self.bundle)  # retire « O »
        self.assertTrue(res.ok)
        self.assertTrue(any("type d'employé non couvert" in w for w in res.warnings))

    # --- Validation empirique des correspondances de colonnes de jointure ------

    def test_motif_join_matches_target_for_absences(self):
        motifs = self.cfg.joins["motifs"]
        m = self.bundle.motifs.df.set_index(motifs.col("situation").table)
        tgt = self.bundle.target.df.drop_duplicates("personId").set_index("personId")
        checked = 0
        for rec in self.bundle.source.df.to_dict("records"):
            sit = self.cfg.situation_for(rec[motifs.col("acces").source])
            if sit is None or sit.reason_code != "motif_remphor":
                continue
            motif = m.loc[rec[motifs.col("situation").source]]
            self.assertEqual(norm_code(motif[motifs.col("remphor").table]),
                             norm_code(tgt.loc[rec["Matricule"], "statusReasonCode"]))
            self.assertEqual(norm_code(motif[motifs.col("acces").table]),
                             norm_code(rec[motifs.col("acces").source]))
            checked += 1
        self.assertGreater(checked, 0)

    def test_poste_detail_join_covers_all_assignments(self):
        pd_join = self.cfg.joins["poste_detail"]
        dp = self.bundle.poste_detail.df
        for rec in self.bundle.source.df.to_dict("records"):
            hist = dp[dp[pd_join.col("poste").table] == rec[pd_join.col("poste").source]]
            with self.subTest(matricule=rec["Matricule"], poste=rec["CodePoste"]):
                self.assertFalse(hist.empty)
                self.assertEqual(set(hist[pd_join.col("emploi").table]),
                                 {rec[pd_join.col("emploi").source]})


if __name__ == "__main__":
    unittest.main()
