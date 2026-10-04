import contextlib
import io
import unittest

from corroborai.cli import main
from tests._helpers import fixture_dir


class TestCli(unittest.TestCase):
    def test_check_ok(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--data-dir", str(fixture_dir("csv"))])
        self.assertEqual(code, 0)
        self.assertIn("aucun manifest.json", out.getvalue())
        self.assertIn("poste_detail", out.getvalue())

    def test_check_strict_manifest(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["check", "--data-dir", str(fixture_dir("xlsx", "match")),
                                   "--manifeste-strict"]), 0)
            self.assertEqual(main(["check", "--data-dir", str(fixture_dir("xlsx", "mismatch")),
                                   "--manifeste-strict"]), 2)

    def test_check_bad_dir(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["check", "--data-dir", "/chemin/inexistant"])
        self.assertEqual(code, 2)
        self.assertIn("ERREUR", err.getvalue())


if __name__ == "__main__":
    unittest.main()
