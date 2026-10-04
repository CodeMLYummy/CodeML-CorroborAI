import json
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path

import pandas as pd

from corroborai.io.loaders import (
    ROW_COL,
    DataLoadError,
    FileStatus,
    load_bundle,
    name_key,
    sha256_bytes,
    unpack_packed_csv,
)
from tests._helpers import DATA_DIR, copy_data, requires_data


class TestPureHelpers(unittest.TestCase):
    def test_name_key_tolerant(self):
        self.assertEqual(name_key("Motif de la situation d'emploi.xlsx"),
                         name_key("Motif_de_la_situation_demploi.xlsx"))
        nfd = unicodedata.normalize("NFD", "détail_du_poste.xlsx")
        self.assertEqual(name_key(nfd), name_key("détail_du_poste.xlsx"))
        self.assertNotEqual(name_key("Employe_Source.xlsx"), name_key("Employe_Destination.xlsx"))

    def test_unpack_packed_csv(self):
        packed = pd.DataFrame({"a,b,c": ["1,2,3", "4,,6"]})
        out = unpack_packed_csv(packed)
        self.assertEqual(list(out.columns), ["a", "b", "c"])
        self.assertEqual(out.iloc[0].tolist(), ["1", "2", "3"])
        self.assertTrue(pd.isna(out.iloc[1]["b"]))

    def test_unpack_noop_on_regular_table(self):
        df = pd.DataFrame({"a": ["1"], "b": ["2"]})
        self.assertIs(unpack_packed_csv(df), df)


@requires_data
class TestLoadRealData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(DATA_DIR)

    def test_integrity_ok(self):
        self.assertTrue(self.bundle.integrity.ok)
        self.assertEqual(self.bundle.integrity.failures(), [])

    def test_renamed_file_resolved_by_content(self):
        motifs = next(c for c in self.bundle.integrity.checks if c.logical_name == "motifs")
        self.assertIn(motifs.status, (FileStatus.OK, FileStatus.OK_RENAMED))
        self.assertEqual(motifs.actual_sha256, motifs.expected_sha256)

    def test_optional_missing_is_warning_only(self):
        for w in self.bundle.integrity.warnings():
            self.assertFalse(w.required)

    def test_shapes(self):
        self.assertEqual(len(self.bundle.source.df), 23)
        self.assertEqual(len(self.bundle.target.df), 22)
        self.assertEqual(len(self.bundle.poste_detail.df), 114)
        self.assertEqual(len(self.bundle.motifs.df), 92)
        self.assertEqual(self.bundle.source.df["Matricule"].nunique(), 20)
        self.assertEqual(set(self.bundle.source.df["Matricule"]),
                         set(self.bundle.target.df["personId"]))

    def test_mapping_sheets(self):
        self.assertIn("Mapping", self.bundle.mapping)
        self.assertIn("Règles situation d'emploi", self.bundle.mapping)
        self.assertEqual(len(self.bundle.mapping), 4)

    def test_poste_detail_unpacked(self):
        df = self.bundle.poste_detail.df
        self.assertIn("IdentifiantPoste", df.columns)
        self.assertIn("HeuresSemaineContrat", df.columns)
        self.assertEqual(df.shape[1] - 1, 11)  # sans _row
        # Les champs vides du CSV deviennent None
        self.assertIsNone(df.loc[df["IdentifiantPoste"] == "31109", "MatriculeGestionnaire"].iloc[0])

    def test_row_locator(self):
        for t in (self.bundle.source, self.bundle.target, self.bundle.poste_detail, self.bundle.motifs):
            with self.subTest(table=t.name):
                self.assertEqual(t.df[ROW_COL].iloc[0], 2)
                self.assertEqual(t.df[ROW_COL].iloc[-1], len(t.df) + 1)

    def test_values_kept_raw_as_strings(self):
        src = self.bundle.source.df
        self.assertEqual(src["Matricule"].iloc[0], "1545850")
        self.assertIsInstance(src["CodeRaisonStatut"].iloc[0], str)
        self.assertIsNone(src["DateSortiePoste"].iloc[0])
        # Le chargeur ne corrige rien : le mojibake d'origine est conservé tel quel
        statuses = set(self.bundle.target.df["detailedStatus"])
        self.assertTrue(any("Ã" in s for s in statuses))

    def test_sources_unchanged_after_load(self):
        before = {p.name: (sha256_bytes(p.read_bytes()), p.stat().st_mtime_ns)
                  for p in DATA_DIR.iterdir() if p.is_file()}
        bundle = load_bundle(DATA_DIR)
        self.assertTrue(bundle.verify_unchanged().ok)
        after = {p.name: (sha256_bytes(p.read_bytes()), p.stat().st_mtime_ns)
                 for p in DATA_DIR.iterdir() if p.is_file()}
        self.assertEqual(before, after)


@requires_data
class TestIntegrityFailures(unittest.TestCase):
    """Toutes les altérations sont faites sur une COPIE temporaire."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = copy_data(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, prefix):
        return next(p for p in self.dir.iterdir() if p.name.startswith(prefix))

    def test_tampered_file_rejected_in_strict_mode(self):
        p = self._file("Employe_Source")
        data = bytearray(p.read_bytes())
        data[-1] ^= 0xFF
        p.write_bytes(bytes(data))
        with self.assertRaises(DataLoadError) as ctx:
            load_bundle(self.dir)
        self.assertIn("source", str(ctx.exception))

    def test_tampered_file_reported_in_lenient_mode(self):
        p = self._file("Employe_Destination")
        df = pd.read_excel(p, dtype=str)
        df.loc[0, "givenName"] = "Modifié"
        df.to_excel(p, index=False)
        bundle = load_bundle(self.dir, strict=False)
        status = {c.logical_name: c.status for c in bundle.integrity.checks}
        self.assertEqual(status["target"], FileStatus.HASH_MISMATCH)
        self.assertFalse(bundle.integrity.ok)

    def test_missing_required_file(self):
        self._file("Mapping").unlink()
        with self.assertRaises(DataLoadError):
            load_bundle(self.dir)

    def test_missing_required_column(self):
        p = self._file("Employe_Source")
        df = pd.read_excel(p, dtype=str).drop(columns=["CodePoste"])
        df.to_excel(p, index=False)
        manifest_path = self.dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for e in manifest["files"]:
            if e["path"] == p.name:
                e["sha256"] = sha256_bytes(p.read_bytes())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(DataLoadError) as ctx:
            load_bundle(self.dir)
        self.assertIn("CodePoste", str(ctx.exception))

    def test_nfd_filename_resolved(self):
        p = self._file("d")  # détail_du_poste
        nfd = unicodedata.normalize("NFD", p.name)
        if nfd == p.name:
            self.skipTest("nom déjà en NFD")
        os.rename(p, self.dir / nfd)
        bundle = load_bundle(self.dir)
        self.assertEqual(len(bundle.poste_detail.df), 114)

    def test_missing_data_dir(self):
        with self.assertRaises(DataLoadError):
            load_bundle(self.dir / "inexistant")


if __name__ == "__main__":
    unittest.main()
