import math
import unittest
from datetime import date, datetime
from decimal import Decimal

import pandas as pd

from corroborai.io.normalize import (
    FieldKind,
    NormalizationError,
    excel_serial_to_date,
    fix_mojibake,
    is_empty,
    norm_bool,
    norm_code,
    norm_date,
    norm_number,
    norm_text,
    norm_text_loose,
    normalize,
    strip_accents,
)


class TestEmpty(unittest.TestCase):
    def test_empty_values(self):
        for v in (None, float("nan"), pd.NA, pd.NaT, "", "   ", "nan", "NaN", "None", "NULL", "<NA>", "-"):
            with self.subTest(v=v):
                self.assertTrue(is_empty(v))

    def test_non_empty_values(self):
        for v in ("0", 0, False, "Actif", " x "):
            with self.subTest(v=v):
                self.assertFalse(is_empty(v))


class TestText(unittest.TestCase):
    def test_mojibake_repaired(self):
        self.assertEqual(fix_mojibake("Absence complÃ¨te"), "Absence complète")

    def test_mojibake_noop_on_clean_text(self):
        for t in ("Absence complète", "Actif", "Prénom"):
            self.assertEqual(fix_mojibake(t), t)

    def test_mojibake_unrepairable_kept(self):
        self.assertEqual(fix_mojibake("Ã"), "Ã")

    def test_norm_text_whitespace_and_encoding(self):
        self.assertEqual(norm_text("  Absence   complÃ¨te "), "Absence complète")
        self.assertIsNone(norm_text("  "))

    def test_nfc_nfd_equivalence(self):
        nfd = "de\u0301tail"  # « détail » en forme décomposée
        self.assertEqual(norm_text(nfd), "détail")

    def test_loose(self):
        self.assertEqual(norm_text_loose("Élodie Bérubé"), "elodie berube")
        self.assertEqual(strip_accents("Québec"), "Quebec")


class TestCodesNumbers(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(norm_code("00397"), "397")
        self.assertEqual(norm_code("703.0"), "703")
        self.assertEqual(norm_code(48), "48")
        self.assertEqual(norm_code(" JWN "), "JWN")
        self.assertEqual(norm_code("jwn"), "jwn")  # la casse d'un code reste visible
        self.assertEqual(norm_code("7.2"), "7.2")
        self.assertIsNone(norm_code(None))

    def test_numbers(self):
        self.assertEqual(norm_number("40"), norm_number("40.0"))
        self.assertEqual(norm_number("40"), Decimal(40))
        self.assertEqual(norm_number("7.2"), Decimal("7.2"))
        self.assertEqual(norm_number("7,2"), Decimal("7.2"))
        self.assertNotEqual(norm_number("36"), norm_number("40"))
        self.assertIsNone(norm_number(""))

    def test_number_errors(self):
        for v in ("abc", True, "inf"):
            with self.subTest(v=v), self.assertRaises(NormalizationError):
                norm_number(v)


class TestBool(unittest.TestCase):
    def test_true_false(self):
        for v in ("Oui", "true", "TRUE", "1", True, "Vrai"):
            self.assertIs(norm_bool(v), True)
        for v in ("Non", "false", "0", False, "Faux"):
            self.assertIs(norm_bool(v), False)
        self.assertIsNone(norm_bool(None))

    def test_unknown(self):
        with self.assertRaises(NormalizationError):
            norm_bool("peut-être")


class TestDates(unittest.TestCase):
    def test_formats_equivalent(self):
        expected = date(2003, 12, 12)
        for v in ("2003-12-12T00:00:00.000Z", "2003-12-12 00:00:00", "2003-12-12",
                  datetime(2003, 12, 12, 0, 0), pd.Timestamp("2003-12-12"), date(2003, 12, 12),
                  "2003-12-12T00:00:00+00:00"):
            with self.subTest(v=v):
                self.assertEqual(norm_date(v), expected)

    def test_empty_and_invalid(self):
        self.assertIsNone(norm_date(None))
        self.assertIsNone(norm_date("NaT"))
        with self.assertRaises(NormalizationError):
            norm_date("pas une date")

    def test_serial_not_guessed(self):
        # Un nombre n'est pas interprété implicitement comme une date
        with self.assertRaises(NormalizationError):
            norm_date("23604")

    def test_excel_serial(self):
        self.assertEqual(excel_serial_to_date("23604"), date(1964, 8, 15))
        self.assertEqual(excel_serial_to_date(44839), date(2022, 10, 5))
        self.assertIsNone(excel_serial_to_date(""))
        for bad in ("abc", "0", "-5", "99999999"):
            with self.subTest(bad=bad), self.assertRaises(NormalizationError):
                excel_serial_to_date(bad)


class TestExcelSerialOracle(unittest.TestCase):
    """Vérification croisée avec l'implémentation d'openpyxl (oracle indépendant)."""

    def test_matches_openpyxl(self):
        from openpyxl.utils.datetime import from_excel

        for serial in range(61, 60000, 997):  # 61+ : évite le bogue « 29 février 1900 » d'Excel
            with self.subTest(serial=serial):
                self.assertEqual(excel_serial_to_date(serial), from_excel(serial).date())


class TestDispatcher(unittest.TestCase):
    def test_dispatch(self):
        self.assertEqual(normalize("00397", FieldKind.CODE), "397")
        self.assertEqual(normalize("Oui", "bool"), True)
        self.assertEqual(normalize("2016-04-01 00:00:00", "date"), date(2016, 4, 1))
        with self.assertRaises(ValueError):
            normalize("x", "inconnu")


if __name__ == "__main__":
    unittest.main()
