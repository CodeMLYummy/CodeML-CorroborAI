"""Faux fournisseurs LLM pour les tests (aucun appel réseau externe)."""

from __future__ import annotations

import json
import re

from corroborai.ai.policy import DataClass, Dossier

DATA = re.compile(r"<donnees>\n(.*)\n</donnees>", re.S)


def extract_payload(user_prompt: str) -> dict:
    return json.loads(DATA.search(user_prompt).group(1))


def well_behaved_answer(user_prompt: str) -> str:
    """Simule un LLM discipliné : réponse conforme construite à partir du dossier."""
    p = extract_payload(user_prompt)
    if "decompte" in p:
        refs = [m["ref"] for m in p["motifs"]]
        return json.dumps({
            "synthese": f"Les écarts se concentrent sur quelques motifs ; {p['decompte']['ANOMALIE']} anomalies "
                        f"au total, dont plusieurs relèvent d'une cause unique à corriger une seule fois.",
            "pistes": [f"Commencer par le motif {refs[0]}." if refs else "Examiner les anomalies une à une."],
            "references": refs[:3] or ["M0"],
        }, ensure_ascii=False)
    if "ecart" in p:
        e = p["ecart"]
        return json.dumps({
            "categorie": "SAISIE_CIBLE",
            "justification": f"L'écart sur {e['champ']} est isolé et ne suit aucun motif connu.",
            "piste": "Vérifier l'historique de saisie dans le système cible.",
            "preuves_citees": e["preuves"][:2],
            "confiance": "faible",
        }, ensure_ascii=False)
    ecarts = p["ecarts"]
    return json.dumps({
        "synthese": f"{p['employe']} présente {len(ecarts)} écart(s) à investiguer, dont un sur {ecarts[0]['champ']}.",
        "pistes": [f"Vérifier {e['champ']} dans les deux systèmes." for e in ecarts[:2]],
        "regroupements": [],
        "preuves_citees": ecarts[0]["preuves"][:3],
    }, ensure_ascii=False)


def gemini_wrap(text: str) -> bytes:
    return json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode()


def openai_wrap(text: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode()


class ScriptedTransport:
    """Transport HTTP simulé : renvoie une suite de réponses et mémorise les requêtes."""

    def __init__(self, answers, wrap=gemini_wrap):
        self.answers = list(answers)
        self.wrap = wrap
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        req = json.loads(body)
        self.requests.append((url, headers, req))
        answer = self.answers.pop(0) if self.answers else self.answers_default(req)
        if isinstance(answer, Exception):
            raise answer
        return self.wrap(answer(req) if callable(answer) else answer)

    @staticmethod
    def answers_default(req):
        user = req["contents"][0]["parts"][0]["text"] if "contents" in req else req["messages"][1]["content"]
        return well_behaved_answer(user)


def user_text(req: dict) -> str:
    return req["contents"][0]["parts"][0]["text"] if "contents" in req else req["messages"][1]["content"]


def mini_dossier(task="SYNTHESE_EMPLOYE") -> Dossier:
    payload = {"employe": "EMP-01", "ecarts": [{
        "ref": "E1", "champ": "siteName", "verdict": "ANOMALIE", "priorite": 48.0, "sous_categorie": "",
        "valeur_attendue": "Emplacement48", "valeur_cible": "Emplacement35", "regle": "R-DIRECT",
        "justification": "Valeur attendue « Emplacement48 », valeur cible « Emplacement35 ».",
        "cause_probable": "Permutation avec EMP-02.", "hypotheses": ["H-PERMUTATION"],
        "preuves": ["src:14", "tgt:13"]}], "ecarts_systemiques_exclus": 0}
    from corroborai.ai.tasks import KEY_CLASSES
    d = Dossier(task, payload, dict(KEY_CLASSES))
    d.refs, d.evidence_ids = {"E1"}, {"src:14", "tgt:13"}
    return d


VALID_EMPLOYEE = {
    "synthese": "EMP-01 présente un écart sur siteName, probablement inversé avec EMP-02.",
    "pistes": ["Vérifier le libellé « Emplacement48 » dans le système cible."],
    "regroupements": [],
    "preuves_citees": ["src:14", "tgt:13"],
}
