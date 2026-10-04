import random
import unittest
from types import SimpleNamespace

import pandas as pd

from corroborai.io.loaders import ROW_COL, load_bundle
from corroborai.matching import MatchMethod, match_assignments, similarity
from corroborai.rules_config import load_rules
from tests._helpers import DATA_DIR, requires_data

CFG = load_rules()


def src(mat, typ, poste, emploi, direction="371", entree="2020-01-01"):
    return {"Matricule": mat, "TypeAffectation": typ, "CodePoste": poste, "CodeEmploi": emploi,
            "CodeDirection": direction, "CodeImputation": "2883", "CodeSite": "48",
            "ÉchelleSalariale": "260", "DateEntréePoste": entree, "HeuresNormeHebdo": "40"}


def tgt(pid, prim, temp, emploi, direction="371", start="2020-01-01"):
    return {"personId": pid, "isPrimaryAssignment": prim, "isTemporaryAssignment": temp,
            "positionId": emploi, "divisionId": direction, "divisionCode": "2883", "siteCode": "48",
            "payGradeId": "260", "assignmentStartDate": start, "weeklyHoursOverride": "40"}


def bundle(sources, targets):
    def df(rows):
        d = pd.DataFrame(rows)
        d.insert(0, ROW_COL, range(2, len(d) + 2))
        return d
    return SimpleNamespace(source=SimpleNamespace(df=df(sources)), target=SimpleNamespace(df=df(targets)))


class TestMatchingSynthetic(unittest.TestCase):
    def test_unique_by_type(self):
        pairs = match_assignments(bundle([src("1", "P", "10", "6000")],
                                         [tgt("1", "true", "false", "6000")]), CFG)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].method, MatchMethod.UNIQUE)
        self.assertEqual(pairs[0].key, "P:10")

    def test_same_type_paired_by_content_not_order(self):
        s = [src("1", "S", "10", "6754", entree="2025-11-17"), src("1", "S", "11", "6031", entree="2024-02-12")]
        t = [tgt("1", "false", "false", "6031", start="2024-02-12"),
             tgt("1", "false", "false", "6754", start="2025-11-17")]
        for targets in (t, list(reversed(t))):
            pairs = match_assignments(bundle(s, targets), CFG)
            with self.subTest(order=[x["positionId"] for x in targets]):
                self.assertTrue(all(p.method is MatchMethod.SIMILARITY for p in pairs))
                for p in pairs:
                    self.assertEqual(p.source["CodeEmploi"], p.target["positionId"])
                    self.assertEqual(p.score, 1.0)

    def test_cross_type_above_threshold(self):
        pairs = match_assignments(bundle([src("1", "A", "10", "6000")],
                                         [tgt("1", "false", "false", "6000")]), CFG)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].method, MatchMethod.CROSS_TYPE)
        self.assertEqual((pairs[0].source_type, pairs[0].target_type), ("A", "S"))

    def test_cross_type_below_threshold_is_missing(self):
        s = [src("1", "A", "10", "6000", direction="100", entree="2020-01-01")]
        t = [{**tgt("1", "false", "false", "9999", direction="200", start="1999-01-01"),
              "divisionCode": "1", "siteCode": "2", "payGradeId": "3", "weeklyHoursOverride": "35"}]
        methods = sorted(p.method.value for p in match_assignments(bundle(s, t), CFG))
        self.assertEqual(methods, sorted([MatchMethod.MISSING_TARGET.value, MatchMethod.MISSING_SOURCE.value]))

    def test_person_only_on_one_side(self):
        pairs = match_assignments(bundle([src("1", "P", "10", "6000")],
                                         [tgt("2", "true", "false", "6000")]), CFG)
        self.assertEqual({(p.person_id, p.method) for p in pairs},
                         {("1", MatchMethod.MISSING_TARGET), ("2", MatchMethod.MISSING_SOURCE)})

    def test_similarity_ignores_double_empty(self):
        s = {**src("1", "P", "10", "6000"), "HeuresNormeHebdo": None}
        t = {**tgt("1", "true", "false", "6000"), "weeklyHoursOverride": None}
        self.assertEqual(similarity(CFG, s, t), 1.0)


@requires_data
class TestMatchingRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA_DIR)

    def test_pairs(self):
        pairs = match_assignments(self.bundle, CFG)
        self.assertEqual(len(pairs), 23)
        self.assertEqual(sum(p.matched for p in pairs), 22)
        missing = [p for p in pairs if not p.matched]
        self.assertEqual([(p.person_id, p.key, p.method) for p in missing],
                         [("1545850", "A:45985", MatchMethod.MISSING_TARGET)])

    def test_secondary_assignments_of_7683990(self):
        pairs = [p for p in match_assignments(self.bundle, CFG)
                 if p.person_id == "7683990" and p.source_type == "S"]
        self.assertEqual(len(pairs), 2)
        for p in pairs:
            self.assertEqual(p.method, MatchMethod.SIMILARITY)
            self.assertEqual(p.source["CodeEmploi"], p.target["positionId"])

    def test_deterministic_under_row_shuffle(self):
        base = {(p.person_id, p.key, p.target[ROW_COL] if p.target else None)
                for p in match_assignments(self.bundle, CFG)}
        rng = random.Random(42)
        for _ in range(5):
            shuffled = SimpleNamespace(
                source=SimpleNamespace(df=self.bundle.source.df.sample(frac=1, random_state=rng.randint(0, 10**6))),
                target=SimpleNamespace(df=self.bundle.target.df.sample(frac=1, random_state=rng.randint(0, 10**6))))
            got = {(p.person_id, p.key, p.target[ROW_COL] if p.target else None)
                   for p in match_assignments(shuffled, CFG)}
            self.assertEqual(got, base)


if __name__ == "__main__":
    unittest.main()
