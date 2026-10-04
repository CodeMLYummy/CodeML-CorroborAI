"""Jeu de données synthétique pour les tests, indépendant des fichiers du défi.

Chaque employé illustre un scénario précis, avec son verdict attendu. Les
fichiers portent les mêmes noms que ceux du défi et sont générés au format
Excel (comme l'extraction d'origine : dates en cellules, détail du poste en
CSV « tassé » avec dates en numéros de série) ou CSV (dates en texte ISO).

Génération manuelle pour inspection :
    python -m tests.fixtures.dataset /chemin/de/sortie [xlsx|csv]

Scénarios (matricule → cas) :
    1000001  Conforme ; historique de deux unités administratives (date de début conforme
             sous l'intersection, en anomalie sous la lecture littérale)
    1000002  Absence (code 02) : motif 807 → code Remphor 170, date de retour, encodage
             « Absence complÃ¨te » dans la cible → JUSTIFIE
    1000003  Type d'employé permuté avec 1000004 (JWN ↔ XFLR) → ANOMALIE
    1000004  (contrepartie)
    1000005  Libellé de site permuté avec 1000006 ; courriel à l'identifiant incohérent → ANOMALIE
    1000006  (contrepartie du libellé de site)
    1000007  Heures 35/7 dans la source, 40/8 dans la cible (= détail du poste) → ANOMALIE
    1000008  Affectations P et A ; l'affectation A est absente de la cible → ANOMALIE
    1000009  Une P et deux S ; les S sont dans l'ordre inverse dans la cible → CONFORME
    1000010  Date de début = date d'effet la plus récente du poste → ANOMALIE
    1000011  Code de situation 04 (absent de la table) → INDETERMINE
    1000012  Combinaison V / non permanent / temps plein non couverte → INDETERMINE ;
             prénom « Élodie » reçu « Elodie » → JUSTIFIE (accents)
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

SOURCE_FILE = "Employe_Source_Anonymise_VF.xlsx"
TARGET_FILE = "Employe_Destination_Anonymise_VF.xlsx"
POSTE_FILE = "détail_du_poste.xlsx"
MOTIFS_FILE = "Motif de la situation d'emploi.xlsx"
ENV_PREFIX = "dev-08-v2_"

# --------------------------------------------------------------------------- verdicts attendus

EXPECTED_ANOMALIES = {
    ("1000003", "contractTypeCode"), ("1000004", "contractTypeCode"),
    ("1000005", "siteName"), ("1000006", "siteName"), ("1000005", "contactEmail"),
    ("1000007", "weeklyHoursOverride"), ("1000007", "dailyHoursOverride"),
    ("1000008", "affectation"),
    ("1000010", "assignmentStartDate"),
}
EXPECTED_INDETERMINATE = {
    ("1000011", "detailedStatus"), ("1000011", "statusReasonCode"), ("1000011", "expectedReturnDate"),
    ("1000012", "contractTypeCode"),
}
EXPECTED_HYPOTHESES = {
    "H-PERMUTATION": {("1000003", "contractTypeCode"), ("1000004", "contractTypeCode"),
                      ("1000005", "siteName"), ("1000006", "siteName")},
    "H-ALT-SOURCE": {("1000007", "weeklyHoursOverride"), ("1000007", "dailyHoursOverride")},
    "H-HISTORY-RECORD": {("1000010", "assignmentStartDate")},
    "H-TYPE-ABSENT": {("1000008", "affectation")},
}
# Écarts à investiguer qu'aucune hypothèse n'explique → triage IA
EXPECTED_UNEXPLAINED = {("1000005", "contactEmail")} | EXPECTED_INDETERMINATE
EXPECTED_JUSTIFIED_EXAMPLES = {
    ("1000002", "statusReasonCode"), ("1000002", "detailedStatus"), ("1000012", "givenName"),
    ("1000001", "divisionName"), ("1000001", "contractTypeCode"),
}

# --------------------------------------------------------------------------- source

PEOPLE = {
    "1000001": ("Marc", "Tremblay"), "1000002": ("Julie", "Gagnon"), "1000003": ("Luc", "Roy"),
    "1000004": ("Anne", "Côté"), "1000005": ("Paul", "Bouchard"), "1000006": ("Sara", "Gauthier"),
    "1000007": ("Hugo", "Morin"), "1000008": ("Lise", "Lavoie"), "1000009": ("Éric", "Fortin"),
    "1000010": ("Nadia", "Pelletier"), "1000011": ("Yves", "Bélanger"), "1000012": ("Élodie", "Bérubé"),
}
SITES = {"48": "Emplacement48", "35": "Emplacement35"}


def _src(mat: str, poste: str, typ: str = "P", **kw: Any) -> dict[str, Any]:
    first, last = PEOPLE[mat]
    row = {
        "Matricule": mat, "NomFamille": last, "PrénomUsuel": first,
        "DateEmbaucheRécente": datetime(2000, 1, 15),
        "TypeAffectation": typ, "DateEntréePoste": datetime(2010, 3, 1), "DateSortiePoste": None,
        "CodePoste": poste, "IntituléPoste": f"Poste{poste}",
        "CodeEmploi": "6203", "IntituléEmploi": "Empl6203",
        "ÉchelleSalariale": "223", "LibelléÉchelleSalariale": "GrRemun223",
        "CodeImputation": "2950", "LibelléImputation": "Centre2950",
        "CodeDirection": "348", "LibelléDirection": "UnitAdmin00348",
        "CodeSite": "48", "LibelléSite": SITES["48"],
        "CatégorieEmploi": "V", "EstPermanent": "Oui", "EstTempsPlein": "Oui",
        "CodeStatutEmploi": "1", "CodeRaisonStatut": "703", "LibelléRaisonStatut": "MotifSitua003",
        "DateEffetRaison": datetime(2015, 3, 21), "DateRetourAnticipée": None,
        "CodeSuspensionAccès": "1", "IdentifiantResponsable": "9000001",
        "NomResponsable": "Chef, Gabriel", "CodeQuart": "23",
        "HeuresNormeHebdo": "40", "HeuresNormeQuotidienne": "8",
    }
    row.update(kw)
    return row


SOURCE_ROWS = [
    _src("1000001", "11001", CodeDirection="352", LibelléDirection="UnitAdmin00352"),
    _src("1000002", "11002", CodeSuspensionAccès="2", CodeStatutEmploi="2", CodeRaisonStatut="807",
         LibelléRaisonStatut="MotifSitua807", DateRetourAnticipée=datetime(2026, 12, 1)),
    _src("1000003", "11003"),
    _src("1000004", "11004", EstTempsPlein="Non"),
    _src("1000005", "11005"),
    _src("1000006", "11006", CodeSite="35", LibelléSite=SITES["35"]),
    _src("1000007", "11007", HeuresNormeHebdo="35", HeuresNormeQuotidienne="7"),
    _src("1000008", "11008"),
    _src("1000008", "11018", "A", DateEntréePoste=datetime(2025, 9, 22)),
    _src("1000009", "11009", CodeEmploi="6725", IntituléEmploi="Empl6725"),
    _src("1000009", "11019", "S", CodeEmploi="6754", IntituléEmploi="Empl6754",
         DateEntréePoste=datetime(2025, 11, 17)),
    _src("1000009", "11029", "S", CodeEmploi="6031", IntituléEmploi="Empl6031",
         DateEntréePoste=datetime(2024, 2, 12)),
    _src("1000010", "11010", DateEntréePoste=datetime(2008, 1, 1)),
    _src("1000011", "11011", CodeSuspensionAccès="4", CodeStatutEmploi="4"),
    _src("1000012", "11012", EstPermanent="Non"),
]

# Historique du détail du poste : (poste, emploi, unité, date d'effet)
POSTE_HISTORY = [
    ("11001", "6203", "320", date(2000, 1, 1)), ("11001", "6203", "352", date(2005, 1, 1)),
    ("11010", "6203", "348", date(2001, 1, 1)), ("11010", "6203", "348", date(2015, 6, 1)),
    ("11009", "6725", "348", date(1999, 5, 5)), ("11019", "6754", "348", date(2002, 12, 14)),
    ("11029", "6031", "348", date(1990, 5, 20)),
] + [(p, "6203", "348", date(2003, 6, 18)) for p in
     ("11002", "11003", "11004", "11005", "11006", "11007", "11008", "11018", "11011", "11012")]

# Comme dans le défi, les motifs des employés actifs (703) ne figurent pas dans la table.
MOTIFS_ROWS = [("807", "170", "2"), ("811", "165", "3"), ("820", "125", "6"), ("572", "174", "1")]


# --------------------------------------------------------------------------- cible (règles appliquées correctement)

CONTRACT = {("V", "Oui", "Oui"): "JWN", ("V", "Oui", "Non"): "XFLR", ("O",): "WHX"}


def _strip(text: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def correct_target(src: dict[str, Any]) -> dict[str, Any]:
    """Valeur cible que produirait une interface sans défaut (implémentation indépendante du moteur)."""
    first, last, mat = src["PrénomUsuel"], src["NomFamille"], src["Matricule"]
    absent = src["CodeSuspensionAccès"] in ("2", "3", "6", "7")
    typ = src["TypeAffectation"]
    contract = CONTRACT.get((src["CatégorieEmploi"], src["EstPermanent"], src["EstTempsPlein"]),
                            CONTRACT.get((src["CatégorieEmploi"],), "JWN"))
    return {
        "personId": mat, "givenName": first, "surname": last,
        "contactEmail": f"{ENV_PREFIX}{_strip(first[0] + last).capitalize()}{mat[-3:]}@loto-quebec.com",
        "activityStatus": "ACTIVE",
        "onboardDate": src["DateEmbaucheRécente"].strftime("%Y-%m-%dT00:00:00.000Z"),
        "statusReasonCode": {"807": "170"}.get(src["CodeRaisonStatut"]) if absent else None,
        "expectedReturnDate": src["DateRetourAnticipée"] if absent else None,
        "contractTypeCode": contract,
        "detailedStatus": "Absence complÃ¨te" if absent else "Actif",
        "siteId": src["CodeSite"], "siteCode": src["CodeSite"], "siteName": src["LibelléSite"],
        "divisionId": src["CodeDirection"], "divisionCode": src["CodeImputation"],
        "divisionName": f"{int(src['CodeDirection']):05d}-{src['LibelléDirection']}",
        "positionId": src["CodeEmploi"], "positionCode": src["CodeEmploi"],
        "positionName": f"{int(src['CodeEmploi']):04d}-{src['IntituléEmploi']}",
        "assignmentStartDate": src["DateEntréePoste"], "assignmentEndDate": None,
        "payGradeId": src["ÉchelleSalariale"],
        "isPrimaryAssignment": "true" if typ == "P" else "false",
        "isTemporaryAssignment": "true" if typ == "A" else "false",
        "termStartDate": src["DateEntréePoste"], "termEndDate": None,
        "weeklyHoursOverride": "40", "dailyHoursOverride": "8",
    }


def target_rows() -> list[dict[str, Any]]:
    by_key = {(s["Matricule"], s["CodePoste"]): correct_target(s) for s in SOURCE_ROWS}
    t = copy.deepcopy(by_key)
    # Perturbations délibérées (une par scénario)
    t[("1000003", "11003")]["contractTypeCode"] = "XFLR"
    t[("1000004", "11004")]["contractTypeCode"] = "JWN"
    t[("1000005", "11005")]["siteName"] = SITES["35"]
    t[("1000006", "11006")]["siteName"] = SITES["48"]
    t[("1000005", "11005")]["contactEmail"] = f"{ENV_PREFIX}PBouchard999@loto-quebec.com"
    t[("1000010", "11010")]["assignmentStartDate"] = datetime(2015, 6, 1)
    t[("1000011", "11011")]["detailedStatus"] = "Actif"
    t[("1000012", "11012")]["givenName"] = "Elodie"
    del t[("1000008", "11018")]                     # affectation A non transmise
    rows = list(t.values())
    # Affectations secondaires de 1000009 dans l'ordre inverse
    s_rows = [r for r in rows if r["personId"] == "1000009" and r["isPrimaryAssignment"] == "false"]
    others = [r for r in rows if r not in s_rows]
    idx = next(i for i, r in enumerate(others) if r["personId"] == "1000009") + 1
    return others[:idx] + list(reversed(s_rows)) + others[idx:]


# --------------------------------------------------------------------------- écriture

def _excel_serial(d: date) -> int:
    return (d - date(1899, 12, 30)).days


def _iso(v: Any) -> Any:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    return v


def build(dest: str | Path, fmt: str = "xlsx", manifest: str | None = None) -> Path:
    """Génère le jeu dans ``dest``.

    ``fmt`` : « xlsx » (comme l'extraction d'origine) ou « csv ».
    ``manifest`` : None (aucun), « match » (empreintes exactes) ou « mismatch »
    (empreintes fausses, ex. manifeste d'une autre version des données).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    poste_cols = ["IdentifiantPoste", "IdentifiantEmploi", "CodeDirectionAffectée", "DateEffetAffectation",
                  "CodeBudget", "IndicateurGestion", "CodePosteSecondaire", "MatriculeGestionnaire",
                  "HeuresSemaineContrat", "HeuresJourContrat", "JoursTravailléesSemaine"]
    poste = [[p, e, u, d, "2950", "0", "78871", "", "40", "8", "5"] for p, e, u, d in POSTE_HISTORY]
    motifs = pd.DataFrame(MOTIFS_ROWS, columns=["CodeCatégorieStatut", "CodeStatutSystèmeExterne",
                                                "CodeGestionAccès"])
    source, target = pd.DataFrame(SOURCE_ROWS), pd.DataFrame(target_rows())
    files: list[Path] = []
    if fmt == "xlsx":
        for df, name in ((source, SOURCE_FILE), (target, TARGET_FILE), (motifs, MOTIFS_FILE)):
            df.to_excel(dest / name, index=False)
            files.append(dest / name)
        packed = [",".join(str(_excel_serial(v)) if isinstance(v, date) else str(v) for v in row) for row in poste]
        pd.DataFrame({",".join(poste_cols): packed}).to_excel(dest / POSTE_FILE, index=False)
        files.append(dest / POSTE_FILE)
    elif fmt == "csv":
        tables = ((source, SOURCE_FILE, ";", "utf-8-sig"), (target, TARGET_FILE, ",", "utf-8"),
                  (motifs, MOTIFS_FILE, ";", "cp1252"),
                  (pd.DataFrame([[_iso(v) for v in row] for row in poste], columns=poste_cols), POSTE_FILE, ",",
                   "utf-8"))
        for df, name, sep, enc in tables:
            path = dest / Path(name).with_suffix(".csv").name
            df.map(_iso).to_csv(path, index=False, sep=sep, encoding=enc)
            files.append(path)
    else:
        raise ValueError(f"format inconnu : {fmt}")
    if manifest:
        entries = []
        for p in files:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
            entries.append({"path": p.name, "bytes": p.stat().st_size,
                            "sha256": sha if manifest == "match" else "0" * 64})
        (dest / "manifest.json").write_text(json.dumps({"files": entries}, ensure_ascii=False), encoding="utf-8")
    return dest


if __name__ == "__main__":  # pragma: no cover
    out = build(sys.argv[1] if len(sys.argv) > 1 else "fixture_data", sys.argv[2] if len(sys.argv) > 2 else "xlsx")
    print(f"Jeu synthétique écrit dans {out}")
