import contextlib
import io
import unittest

from corroborai.cli import main
from tests._helpers import DATA_DIR, requires_data


class TestCli(unittest.TestCase):
    @requires_data
    def test_check_ok(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--data-dir", str(DATA_DIR)])
        self.assertEqual(code, 0)
        self.assertIn("Contrôle d'intégrité", out.getvalue())
        self.assertIn("poste_detail", out.getvalue())

    def test_check_bad_dir(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["check", "--data-dir", "/chemin/inexistant"])
        self.assertEqual(code, 2)
        self.assertIn("ERREUR", err.getvalue())


if __name__ == "__main__":
    unittest.main()
