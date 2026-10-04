"""Notebook de démonstration : structure valide, exécuté sans erreur, synchronisé avec son générateur."""

import ast
import importlib.util
import json
import unittest

from tests._helpers import REPO

NB = REPO / "notebooks" / "demo.ipynb"


class TestNotebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nb = json.loads(NB.read_text(encoding="utf-8"))

    def test_structure(self):
        self.assertEqual(self.nb["nbformat"], 4)
        for cell in self.nb["cells"]:
            self.assertIn(cell["cell_type"], ("markdown", "code"))
            self.assertTrue(cell["id"])

    def test_code_cells_executed_without_error(self):
        code = [c for c in self.nb["cells"] if c["cell_type"] == "code"]
        self.assertEqual([c["execution_count"] for c in code], list(range(1, len(code) + 1)))
        for c in code:
            ast.parse("".join(c["source"]))
            self.assertFalse(any(o["output_type"] == "error" for o in c["outputs"]))

    def test_demonstrates_the_three_required_cases(self):
        text = json.dumps(self.nb, ensure_ascii=False)
        for marker in ("2173396:P:69289:assignmentStartDate", "2911996:P:12548:statusReasonCode",
                       "2762457:P:30106:contractTypeCode"):
            self.assertIn(marker, text)
        for verdict in ("CONFORME", "JUSTIFIE", "ANOMALIE"):
            self.assertIn(verdict, text)

    def test_in_sync_with_builder(self):
        spec = importlib.util.spec_from_file_location("build_demo", REPO / "notebooks" / "build_demo.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        expected = ["".join(builder.source_lines(text)) for _, text in builder.CELLS]
        actual = ["".join(c["source"]) for c in self.nb["cells"]]
        self.assertEqual(actual, expected, "notebook périmé : relancer « python notebooks/build_demo.py »")


if __name__ == "__main__":
    unittest.main()
