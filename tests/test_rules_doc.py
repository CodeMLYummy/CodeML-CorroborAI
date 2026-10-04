import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from corroborai.cli import main
from corroborai.rules_config import load_rules
from corroborai.analysis import load_hypotheses, load_scoring
from corroborai.rules_doc import documentation_markdown, rules_markdown
from tests._helpers import DATA_DIR, REPO, requires_data


class TestRulesDoc(unittest.TestCase):
    def setUp(self):
        self.cfg = load_rules()
        self.md = rules_markdown(self.cfg)

    def test_every_field_and_interpretation_documented(self):
        for f in self.cfg.fields:
            self.assertIn(f"`{f.target}`", self.md)
        for iid in self.cfg.interpretations:
            self.assertIn(f"### {iid}", self.md)

    def test_hypotheses_documented(self):
        hcfg = load_hypotheses()
        doc = documentation_markdown(self.cfg, hcfg, load_scoring(hypotheses=hcfg))
        for hid in hcfg.hypotheses:
            self.assertIn(f"`{hid}`", doc)

    def test_committed_doc_is_up_to_date(self):
        hcfg = load_hypotheses()
        full = documentation_markdown(self.cfg, hcfg, load_scoring(hypotheses=hcfg))
        committed = (REPO / "docs" / "REGLES.md").read_text(encoding="utf-8")
        self.assertEqual(committed, full,
                         "docs/REGLES.md est périmé : relancer « corroborai rules --markdown -o docs/REGLES.md »")


class TestRulesCli(unittest.TestCase):
    def test_rules_listing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["rules"]), 0)
        self.assertIn("contractTypeCode", out.getvalue())

    def test_rules_markdown_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "r.md"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["rules", "--markdown", "-o", str(path)]), 0)
            self.assertTrue(path.read_text(encoding="utf-8").startswith("# Règles"))

    def test_invalid_rules_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
            fh.write("version: 2\nfields: []\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(main(["rules", "--rules", fh.name]), 2)
        Path(fh.name).unlink()
        self.assertIn("ERREUR", err.getvalue())

    @requires_data
    def test_check_includes_rules(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(["check", "--data-dir", str(DATA_DIR)]), 0)
        self.assertIn("0 erreur(s)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
