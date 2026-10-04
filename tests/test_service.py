import tempfile
import unittest
from pathlib import Path

from corroborai.service import (
    ServiceError,
    evidence_rows,
    export_files,
    findings_frame,
    investigate,
    parse_mapping_text,
    run_pipeline,
    stage_uploads,
)
from tests._helpers import DATA_DIR, requires_data


class TestPure(unittest.TestCase):
    def test_parse_mapping(self):
        self.assertEqual(parse_mapping_text("a => b\n\n c=>d "), {"a": "b", "c": "d"})
        for bad in ("", "a -> b", "a => ", "=> b"):
            with self.subTest(bad=bad), self.assertRaises(ServiceError):
                parse_mapping_text(bad)

    def test_stage_uploads_ignores_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage_uploads([(".gitkeep", b""), (".DS_Store", b"x"), ("._Mapping.xlsx", b"x"),
                                    ("manifest.json", b"{}")], tmp)
            self.assertEqual(sorted(p.name for p in staged.iterdir()), ["manifest.json"])

    def test_stage_uploads_rejects_unsafe_names(self):
        for name in ("../x.xlsx", "a/b.xlsx", "x.exe", "../.hidden", "dir/.gitkeep"):
            with self.subTest(name=name), self.assertRaises(ServiceError):
                stage_uploads([(name, b""), ("manifest.json", b"{}")])
        with self.assertRaises(ServiceError):
            stage_uploads([("a.xlsx", b"")])      # manifest requis
        with self.assertRaises(ServiceError):
            stage_uploads([])


@requires_data
class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipeline = run_pipeline(DATA_DIR)

    def test_run(self):
        self.assertEqual(len(self.pipeline.result.findings), 551)
        self.assertIsNone(self.pipeline.store)

    def test_staged_uploads_run(self):
        files = [(p.name, p.read_bytes()) for p in DATA_DIR.iterdir() if p.is_file()]
        with tempfile.TemporaryDirectory() as tmp:
            staged = stage_uploads(files, tmp)
            self.assertEqual(len(run_pipeline(staged).result.findings), 551)

    def test_bad_dir(self):
        with self.assertRaises(ServiceError):
            run_pipeline("/inexistant")

    def test_frame(self):
        df = findings_frame(self.pipeline.result.findings, verdicts=["ANOMALIE"])
        self.assertEqual(len(df), 62)
        self.assertEqual(list(df["Priorité"]), sorted(df["Priorité"], reverse=True))
        self.assertEqual(len(findings_frame(self.pipeline.result.findings, fields=["siteName"])), 22)
        self.assertEqual(len(findings_frame([], verdicts=["ANOMALIE"])), 0)

    def test_evidence_rows_are_original_rows(self):
        f = next(x for x in investigate(self.pipeline.result.findings) if x.target_field == "weeklyHoursOverride")
        rows = evidence_rows(self.pipeline.bundle, f)
        tables = {ev.table for ev, _ in rows}
        self.assertIn("poste_detail", tables)
        for ev, row in rows:
            self.assertEqual(row["_row"], ev.row)
        src = next(row for ev, row in rows if ev.table == "source")
        self.assertEqual(src["Matricule"], f.person_id)

    def test_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx, csv = export_files(self.pipeline, tmp)
            self.assertTrue(Path(xlsx).is_file() and Path(csv).is_file())


if __name__ == "__main__":
    unittest.main()
