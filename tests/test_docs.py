"""Documentation : liens relatifs valides, commandes documentées existantes."""

import re
import unittest

from corroborai.cli import build_parser
from tests._helpers import REPO

DOCS = [REPO / "README.md", *sorted((REPO / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


class TestDocs(unittest.TestCase):
    def test_relative_links_resolve(self):
        for doc in DOCS:
            for target in LINK.findall(doc.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                with self.subTest(doc=doc.name, link=target):
                    self.assertTrue((doc.parent / target).exists(), f"lien cassé dans {doc.name} : {target}")

    def test_documented_commands_and_options_exist(self):
        parser = build_parser()
        sub = next(a for a in parser._actions if a.dest == "command")
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for cmd in re.findall(r"corroborai (\w+)", readme):
            self.assertIn(cmd, sub.choices, f"commande documentée inexistante : {cmd}")
        options = {o for p in sub.choices.values() for a in p._actions for o in a.option_strings}
        for opt in set(re.findall(r"(--[a-z][a-z-]+)", readme)):
            self.assertIn(opt, options, f"option documentée inexistante : {opt}")

    def test_docs_mention_every_hypothesis(self):
        from corroborai.analysis.config import KNOWN_HYPOTHESES
        text = (REPO / "docs" / "IA.md").read_text(encoding="utf-8")
        for hid in KNOWN_HYPOTHESES:
            self.assertIn(hid, text)


if __name__ == "__main__":
    unittest.main()
