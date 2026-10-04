"""Logique des dates d'affectation.

Les valeurs « oracle » des cas réels ont été calculées à la main à partir de
l'historique du détail du poste (voir commentaires), indépendamment du code.
"""

import unittest
from datetime import date

from corroborai.rules.base import PosteRecord
from corroborai.rules.dates import (
    INTERVAL_INTERSECTION,
    STRICT_LITERAL,
    assignment_end,
    assignment_start,
    unit_interval,
)


def hist(*items):
    return [PosteRecord(date.fromisoformat(d), u, i + 2, {}) for i, (d, u) in enumerate(items)]


class TestUnitInterval(unittest.TestCase):
    def test_no_history(self):
        ui = unit_interval([], "348")
        self.assertIsNone(ui.start)
        self.assertIsNone(ui.end)
        self.assertTrue(ui.notes)

    def test_no_change_takes_min_effdt(self):
        ui = unit_interval(hist(("2001-11-16", "348"), ("2022-10-05", "348")), "348")
        self.assertEqual(ui.start, date(2001, 11, 16))
        self.assertIsNone(ui.end)
        self.assertFalse(ui.has_change)
        self.assertEqual(ui.notes, [])

    def test_no_change_but_unit_differs_from_source(self):
        ui = unit_interval(hist(("2001-11-16", "348")), "999")
        self.assertEqual(ui.start, date(2001, 11, 16))
        self.assertTrue(ui.notes)

    def test_change_detected(self):
        # Poste 69289 (employé 2173396) : 325 → 320 → 320 → 352
        ui = unit_interval(hist(("1987-11-21", "325"), ("1991-06-18", "320"),
                                ("1993-12-14", "320"), ("1997-06-24", "352")), "352")
        self.assertEqual(ui.start, date(1997, 6, 24))
        self.assertIsNone(ui.end)
        self.assertTrue(ui.has_change)

    def test_change_then_back_uses_last_run(self):
        ui = unit_interval(hist(("2000-01-01", "A"), ("2001-01-01", "B"), ("2002-01-01", "A")), "A")
        self.assertEqual(ui.start, date(2002, 1, 1))
        self.assertIsNone(ui.end)

    def test_current_unit_followed_by_other(self):
        ui = unit_interval(hist(("2000-01-01", "A"), ("2000-01-02", "A"), ("2001-03-01", "B")), "A")
        self.assertEqual(ui.start, date(2000, 1, 1))
        self.assertEqual(ui.end, date(2001, 2, 28))  # veille du prochain détail

    def test_current_unit_absent(self):
        ui = unit_interval(hist(("2000-01-01", "A"), ("2001-01-01", "B")), "C")
        self.assertIsNone(ui.start)
        self.assertTrue(ui.notes)


class TestAssignmentDates(unittest.TestCase):
    def test_start(self):
        ui = unit_interval(hist(("2001-11-16", "348")), "348")
        self.assertEqual(assignment_start(date(2009, 3, 30), ui, INTERVAL_INTERSECTION), date(2009, 3, 30))
        self.assertEqual(assignment_start(date(2009, 3, 30), ui, STRICT_LITERAL), date(2001, 11, 16))
        self.assertEqual(assignment_start(None, ui, INTERVAL_INTERSECTION), date(2001, 11, 16))
        self.assertIsNone(assignment_start(None, unit_interval([], None), INTERVAL_INTERSECTION))
        with self.assertRaises(ValueError):
            assignment_start(date(2009, 3, 30), ui, "autre")

    def test_end(self):
        ui = unit_interval(hist(("2000-01-01", "A"), ("2001-03-01", "B")), "A")
        self.assertEqual(assignment_end(None, ui), date(2001, 2, 28))
        self.assertEqual(assignment_end(date(2000, 6, 1), ui), date(2000, 6, 1))
        self.assertEqual(assignment_end(date(2005, 1, 1), ui), date(2001, 2, 28))
        self.assertIsNone(assignment_end(None, unit_interval(hist(("2000-01-01", "A")), "A")))


class TestOracleRealCases(unittest.TestCase):
    """(historique, unité courante, entrée) → (intersection, littéral), calculés à la main."""

    CASES = {
        # 2173396 : changement vers 352 le 1997-06-24 ; entrée 2019-04-29
        "2173396": (hist(("1987-11-21", "325"), ("1991-06-18", "320"), ("1993-12-14", "320"),
                         ("1997-06-24", "352")), "352", date(2019, 4, 29),
                    date(2019, 4, 29), date(1997, 6, 24)),
        # 3241002 : aucun changement, MIN EFFDT 2001-11-16 ; entrée 2009-03-30
        "3241002": (hist(("2001-11-16", "348"), ("2009-01-01", "348"), ("2022-10-05", "348")),
                    "348", date(2009, 3, 30), date(2009, 3, 30), date(2001, 11, 16)),
        # 4402456 : aucun changement, MIN EFFDT 2002-06-10 (après l'entrée 2001-02-05)
        "4402456": (hist(("2002-06-10", "371"), ("2013-10-20", "371")), "371", date(2001, 2, 5),
                    date(2002, 6, 10), date(2001, 2, 5)),
        # 9989151 : aucun changement, MIN EFFDT 2003-06-18 ; entrée 2009-03-30
        "9989151": (hist(("2003-06-18", "348"), ("2021-03-30", "348")), "348", date(2009, 3, 30),
                    date(2009, 3, 30), date(2003, 6, 18)),
    }

    def test_oracle(self):
        for name, (h, unit, entry, inter, literal) in self.CASES.items():
            ui = unit_interval(h, unit)
            with self.subTest(case=name):
                self.assertEqual(assignment_start(entry, ui, INTERVAL_INTERSECTION), inter)
                self.assertEqual(assignment_start(entry, ui, STRICT_LITERAL), literal)


if __name__ == "__main__":
    unittest.main()
