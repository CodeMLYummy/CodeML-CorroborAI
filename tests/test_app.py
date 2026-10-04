"""Application Streamlit : exécution de bout en bout contre un module simulé,
et (si Streamlit est installé) avec l'outil officiel AppTest."""

import importlib.util
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from corroborai.feedback import FeedbackStore
from tests._helpers import CHALLENGE_DIR, REPO, requires_challenge_data
from tests._streamlit_stub import RerunApp, StopApp, _State, make_stub

APP = REPO / "app" / "streamlit_app.py"


def run_app(values=None, clicks=None, state=None):
    st = make_stub(values, clicks, state)
    with mock.patch.dict(sys.modules, {"streamlit": st}):
        try:
            runpy.run_path(str(APP), run_name="__main__")
            outcome = "ok"
        except StopApp:
            outcome = "stop"
        except RerunApp:
            outcome = "rerun"
    return st, outcome


def texts(st, kind=None):
    return " ".join(str(a[0]) for name, a, _ in st.calls if a and (kind is None or name == kind))


@requires_challenge_data
class TestAppWithStub(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = str(Path(self.tmp.name) / "retroaction.yaml")
        self.base = {"Répertoire des données": str(CHALLENGE_DIR), "Fichier de rétroaction": self.store}

    def tearDown(self):
        self.tmp.cleanup()

    def launched(self, **extra):
        st, outcome = run_app(self.base, {"Lancer la corroboration"})
        self.assertEqual(outcome, "ok", texts(st, "error"))
        return st.session_state

    def test_initial_screen_waits_for_data(self):
        st, outcome = run_app(self.base)
        self.assertEqual(outcome, "stop")
        self.assertIn("lancez la corroboration", texts(st, "info"))

    def test_launch_renders_all_tabs(self):
        st, outcome = run_app(self.base, {"Lancer la corroboration"})
        self.assertEqual(outcome, "ok", texts(st, "error"))
        metrics = {a[0]: a[1] for name, a, _ in st.calls if name == "metric"}
        self.assertEqual(metrics["Anomalie"], 62)
        self.assertIn("Données ayant servi à la décision", texts(st, "markdown"))
        self.assertIn("Cause probable", texts(st, "markdown"))

    def test_bad_directory_shows_error(self):
        st, outcome = run_app({**self.base, "Répertoire des données": "/inexistant"}, {"Lancer la corroboration"})
        self.assertEqual(outcome, "stop")
        self.assertIn("Chargement impossible", texts(st, "error"))

    def test_correction_flow(self):
        state = self.launched()
        st, outcome = run_app({**self.base, "corr_verdict": "JUSTIFIE", "corr_reason": "Validé par l'équipe."},
                              {"Enregistrer la correction"}, state)
        self.assertEqual(outcome, "rerun", texts(st, "error"))
        store = FeedbackStore.load(self.store)
        self.assertEqual(len(store.corrections), 1)
        self.assertEqual(len(state.run.result.feedback.corrections_applied), 1)

    def test_correction_without_reason_is_refused(self):
        state = self.launched()
        st, outcome = run_app({**self.base, "corr_reason": ""}, {"Enregistrer la correction"}, state)
        self.assertEqual(outcome, "ok")
        self.assertIn("justifiée", texts(st, "error"))
        self.assertFalse(Path(self.store).exists())

    def test_candidate_rule_preview_then_accept(self):
        state = self.launched()
        st, _ = run_app(self.base, {"cand_0"}, state)
        self.assertIn("deviendraient JUSTIFIE", texts(st, "success"))
        st, outcome = run_app({**self.base, "cand0_author": "QA"}, {"cand0_accept"}, state)
        self.assertEqual(outcome, "rerun", texts(st, "error"))
        store = FeedbackStore.load(self.store)
        self.assertEqual([(r.operation, r.provenance, r.author) for r in store.rules],
                         [("accept_alternative_source", "SUGGESTION", "QA")])
        self.assertEqual(state.run.result.counts()["ANOMALIE"], 58)

    def test_form_rule_flow(self):
        state = self.launched()
        values = {**self.base, "form_field": "positionName", "form_op": "accept_hypothesis",
                  "form_hyp": "H-BIJECTION", "form_reason": "Recodage d'anonymisation."}
        st, _ = run_app(values, {"form_prev"}, state)
        self.assertIn("22 anomalie(s)", texts(st, "success"))
        st, outcome = run_app(values, {"form_accept"}, state)
        self.assertEqual(outcome, "rerun", texts(st, "error"))
        self.assertEqual(state.run.result.counts()["ANOMALIE"], 40)

    def test_translation_with_template_provider(self):
        state = self.launched()
        st, outcome = run_app({**self.base, "Consigne": "Les heures viennent du contrat du poste."},
                              {"Traduire en règle"}, state)
        self.assertEqual(outcome, "ok", texts(st, "error"))
        self.assertIn("formulaire", texts(st, "markdown"))
        self.assertIsNone(state.translation.rule)

    def test_export(self):
        state = self.launched()
        st, _ = run_app(self.base, {"Générer les fichiers"}, state)
        downloads = [a[0] for name, a, _ in st.calls if name == "download_button"]
        self.assertEqual(len(downloads), 2)

    def test_source_files_untouched(self):
        from corroborai.io.loaders import sha256_bytes
        before = {p.name: sha256_bytes(p.read_bytes()) for p in CHALLENGE_DIR.iterdir() if p.is_file()}
        state = self.launched()
        run_app({**self.base, "corr_reason": "ok"}, {"Enregistrer la correction"}, state)
        after = {p.name: sha256_bytes(p.read_bytes()) for p in CHALLENGE_DIR.iterdir() if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(CHALLENGE_DIR.glob("*.yaml")))


@requires_challenge_data
@unittest.skipUnless(importlib.util.find_spec("streamlit"), "Streamlit non installé")
class TestAppWithAppTest(unittest.TestCase):
    """Exécution avec le moteur réel de Streamlit (s'exécute là où Streamlit est installé)."""

    def test_launch(self):
        from streamlit.testing.v1 import AppTest

        with mock.patch.dict("os.environ", {"CORROBORAI_DATA_DIR": str(CHALLENGE_DIR)}):
            at = AppTest.from_file(str(APP), default_timeout=120)
            at.run()
            self.assertFalse(at.exception)
            next(b for b in at.sidebar.button if b.label == "Lancer la corroboration").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.tabs), 6)
            self.assertIn(62, [m.value if isinstance(m.value, int) else int(m.value) for m in at.metric])


if __name__ == "__main__":
    unittest.main()
