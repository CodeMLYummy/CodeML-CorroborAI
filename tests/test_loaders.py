"""Chargement : formats Excel et CSV, manifeste informatif, preuve de non-modification."""

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
    read_csv_bytes,
    sha256_bytes,
    unpack_packed_csv,
)
from tests._helpers import CHALLENGE_DIR, fixture_dir, fresh_fixture, requires_challenge_data
from tests.fixtures import dataset


class TestPureHelpers(unittest.TestCase):
    def test_name_key_tolerant(self):
        self.assertEqual(name_key("Motif de la situation d'emploi.xlsx"),
                         name_key("Motif_de_la_situation_demploi.xlsx"))
        nfd = unicodedata.normalize("NFD", "détail_du_poste.xlsx")
        self.assertEqual(name_key(nfd), name_key("détail_du_poste.xlsx"))

    def test_unpack_packed_csv(self):
        out = unpack_packed_csv(pd.DataFrame({"a,b,c": ["1,2,3", "4,,6"]}))
        self.assertEqual(list(out.columns), ["a", "b", "c"])
        self.assertTrue(pd.isna(out.iloc[1]["b"]))
        df = pd.DataFrame({"a": ["1"], "b": ["2"]})
        self.assertIs(unpack_packed_csv(df), df)

    def test_csv_separator_and_encoding_detection(self):
        for sep in (";", ",", "\t"):
            for enc in ("utf-8-sig", "cp1252", "utf-8"):
                data = f"Prénom{sep}Code\nÉlodie{sep}007\n".encode(enc)
                with self.subTest(sep=sep, enc=enc):
                    df = read_csv_bytes(data)
                    self.assertEqual(list(df.columns), ["Prénom", "Code"])
                    self.assertEqual(df.iloc[0].tolist(), ["Élodie", "007"])  # zéros de tête conservés


class TestFormats(unittest.TestCase):
    def test_xlsx_and_csv_load_identically(self):
        bx, bc = load_bundle(fixture_dir("xlsx")), load_bundle(fixture_dir("csv"))
        for name in ("source", "target", "poste_detail", "motifs"):
            tx, tc = getattr(bx, name).df, getattr(bc, name).df
            with self.subTest(table=name):
                self.assertEqual(tx.shape, tc.shape)
                self.assertEqual(list(tx.columns), list(tc.columns))
        self.assertEqual(bc.source.path.suffix, ".csv")

    def test_row_locator_and_unpacked_detail(self):
        b = load_bundle(fixture_dir("xlsx"))
        for t in (b.source, b.target, b.poste_detail, b.motifs):
            self.assertEqual(t.df[ROW_COL].iloc[0], 2)
            self.assertEqual(t.df[ROW_COL].iloc[-1], len(t.df) + 1)
        self.assertEqual(b.poste_detail.df.shape[1] - 1, 11)
        self.assertIsNone(b.poste_detail.df["MatriculeGestionnaire"].iloc[0])

    def test_values_kept_raw(self):
        b = load_bundle(fixture_dir("xlsx"))
        self.assertTrue(any("Ã" in str(v) for v in b.target.df["detailedStatus"]))  # aucune correction au chargement
        self.assertIsInstance(b.source.df["CodeRaisonStatut"].iloc[0], str)

    def test_mapping_optional(self):
        b = load_bundle(fixture_dir("xlsx"))
        self.assertEqual(b.mapping, {})
        m = next(c for c in b.integrity.checks if c.logical_name == "mapping")
        self.assertEqual((m.status, m.required), (FileStatus.ABSENT, False))
        self.assertTrue(b.integrity.ok)


class TestManifest(unittest.TestCase):
    def statuses(self, b):
        return {c.logical_name: c.status for c in b.integrity.checks if c.required}

    def test_without_manifest(self):
        b = load_bundle(fixture_dir("xlsx"))
        self.assertFalse(b.integrity.manifest_found)
        self.assertEqual(set(self.statuses(b).values()), {FileStatus.SANS_MANIFESTE})
        self.assertTrue(b.integrity.ok)

    def test_matching_manifest(self):
        b = load_bundle(fixture_dir("xlsx", "match"))
        self.assertEqual(set(self.statuses(b).values()), {FileStatus.CONFORME_MANIFESTE})

    def test_different_files_same_names_are_accepted(self):
        """Ex. jeu de test plus volumineux portant les mêmes noms que l'original."""
        b = load_bundle(fixture_dir("xlsx", "mismatch"))
        self.assertEqual(set(self.statuses(b).values()), {FileStatus.DIFFERENT_MANIFESTE})
        self.assertTrue(b.integrity.ok)
        self.assertEqual(len(b.integrity.warnings()), 5)  # 4 fichiers différents + mapping absent

    def test_more_rows_than_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fresh_fixture(Path(tmp), "xlsx", "match")
            src = d / dataset.SOURCE_FILE
            df = pd.read_excel(src, dtype=str)
            pd.concat([df, df.tail(1).assign(Matricule="1000099")]).to_excel(src, index=False)
            b = load_bundle(d)
            self.assertEqual(len(b.source.df), len(df) + 1)
            self.assertEqual(self.statuses(b)["source"], FileStatus.DIFFERENT_MANIFESTE)

    def test_strict_mode(self):
        load_bundle(fixture_dir("xlsx", "match"), strict_manifest=True)
        for manifest in ("mismatch", None):
            with self.subTest(manifest=manifest), self.assertRaises(DataLoadError):
                load_bundle(fixture_dir("xlsx", manifest), strict_manifest=True)

    def test_malformed_manifest_is_ignored_with_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fresh_fixture(Path(tmp))
            (d / "manifest.json").write_text("{pas du json", encoding="utf-8")
            b = load_bundle(d)
            self.assertFalse(b.integrity.manifest_found)
            self.assertTrue(b.integrity.notes)


class TestReadOnlyProof(unittest.TestCase):
    def test_files_unchanged_after_load(self):
        d = fixture_dir("csv")
        before = {p.name: (sha256_bytes(p.read_bytes()), p.stat().st_mtime_ns) for p in d.iterdir()}
        b = load_bundle(d)
        after_report = b.verify_unchanged()
        self.assertTrue(after_report.ok)
        self.assertEqual({c.status for c in after_report.checks}, {FileStatus.INCHANGE})
        self.assertEqual(before, {p.name: (sha256_bytes(p.read_bytes()), p.stat().st_mtime_ns) for p in d.iterdir()})

    def test_modification_during_processing_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = fresh_fixture(Path(tmp))
            b = load_bundle(d)
            (d / dataset.TARGET_FILE).write_bytes(b"altere")
            report = b.verify_unchanged()
            self.assertFalse(report.ok)
            self.assertIn(FileStatus.MODIFIE, {c.status for c in report.checks})
            (d / dataset.SOURCE_FILE).unlink()
            self.assertIn(FileStatus.ABSENT, {c.status for c in b.verify_unchanged().checks})


class TestFailures(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = fresh_fixture(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_required_file(self):
        (self.dir / dataset.POSTE_FILE).unlink()
        with self.assertRaises(DataLoadError) as ctx:
            load_bundle(self.dir)
        self.assertIn("poste_detail", str(ctx.exception))

    def test_missing_required_column(self):
        p = self.dir / dataset.SOURCE_FILE
        pd.read_excel(p, dtype=str).drop(columns=["CodePoste"]).to_excel(p, index=False)
        with self.assertRaises(DataLoadError) as ctx:
            load_bundle(self.dir)
        self.assertIn("CodePoste", str(ctx.exception))

    def test_unreadable_file(self):
        (self.dir / dataset.TARGET_FILE).write_bytes(b"ceci n'est pas un classeur")
        with self.assertRaises(DataLoadError) as ctx:
            load_bundle(self.dir)
        self.assertIn("illisible", str(ctx.exception))

    def test_tolerant_names_and_extension(self):
        p = self.dir / dataset.POSTE_FILE
        os.rename(p, self.dir / unicodedata.normalize("NFD", p.name))
        os.rename(self.dir / dataset.MOTIFS_FILE, self.dir / "Motif_de_la_situation_demploi.xlsx")
        (self.dir / ".gitkeep").write_text("")
        b = load_bundle(self.dir)
        self.assertEqual(len(b.motifs.df), len(dataset.MOTIFS_ROWS))
        note = next(c.note for c in b.integrity.checks if c.logical_name == "motifs")
        self.assertIn("Motif_de_la_situation_demploi.xlsx", note)

    def test_missing_dir(self):
        with self.assertRaises(DataLoadError):
            load_bundle(self.dir / "inexistant")


@requires_challenge_data
class TestChallengeData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(CHALLENGE_DIR)

    def test_shapes(self):
        self.assertEqual((len(self.bundle.source.df), len(self.bundle.target.df),
                          len(self.bundle.poste_detail.df), len(self.bundle.motifs.df)), (23, 22, 114, 92))
        self.assertEqual(len(self.bundle.mapping), 4)

    def test_manifest_if_present(self):
        if self.bundle.integrity.manifest_found:
            statuses = {c.status for c in self.bundle.integrity.checks if c.required}
            self.assertEqual(statuses, {FileStatus.CONFORME_MANIFESTE})


if __name__ == "__main__":
    unittest.main()
