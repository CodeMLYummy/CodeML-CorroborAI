"""Construit et exécute notebooks/demo.ipynb (format nbformat 4) sans Jupyter.

Usage (à la racine du dépôt, fichiers du défi dans data/) :
    python notebooks/build_demo.py

Chaque cellule de code est exécutée dans un espace de noms partagé ; la sortie
standard et la valeur de la dernière expression (HTML pour les DataFrames)
sont intégrées au notebook, comme le ferait un noyau.
"""

import ast
import contextlib
import io
import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "notebooks" / "demo.ipynb"

CELLS: list[tuple[str, str]] = [
("md", """# CorroborAI — démonstration

Ce notebook déroule le scénario de démonstration demandé par les consignes, sur les fichiers du défi :

1. **un cas conforme**, **un écart justifié automatiquement** et **une vraie anomalie**, chacun avec sa justification et les données qui l'ont produit ;
2. la vue de l'investigateur : écarts triés par priorité, motifs et règles candidates ;
3. la **rétroaction experte** : une règle prévisualisée puis appliquée ;
4. la **couche IA** encadrée et la protection des données ;
5. le **rapport** exportable.

**Prérequis :** `pip install -e .` à la racine du dépôt, et les fichiers du défi dans `data/` (ou le chemin indiqué par `CORROBORAI_DATA_DIR`)."""),
("code", """import os
from pathlib import Path

import pandas as pd

from corroborai.engine import corroborate
from corroborai.io.loaders import load_bundle
from corroborai.rules_config import load_rules
from corroborai.service import evidence_rows, findings_frame, investigate

pd.set_option("display.max_colwidth", 140)

# Répertoire des données : CORROBORAI_DATA_DIR, sinon data/ à la racine du dépôt
racine = Path.cwd() if (Path.cwd() / "config").is_dir() else Path.cwd().parent
DATA = Path(os.environ.get("CORROBORAI_DATA_DIR", racine / "data"))
print("Données :", DATA)"""),
("md", """## 1. Chargement en lecture seule

Chaque fichier est lu une seule fois en octets ; son empreinte SHA-256 est calculée sur exactement les octets analysés. Si un `manifest.json` est présent, les empreintes y sont comparées à titre informatif."""),
("code", """bundle = load_bundle(DATA)

pd.DataFrame(bundle.integrity.to_records())[["fichier_logique", "fichier_lu", "requis", "statut", "note"]]"""),
("code", """pd.DataFrame(
    [(t.name, t.path.name, len(t.df)) for t in (bundle.source, bundle.target, bundle.poste_detail, bundle.motifs)],
    columns=["table", "fichier", "lignes"],
)"""),
("md", """## 2. Corroboration

Appariement des affectations, application des 25 règles du mapping codifié, réévaluation sous les interprétations alternatives, moteur d'hypothèses et score de priorité. Les verdicts sont fixés par les règles ; l'analyse les explique sans les modifier."""),
("code", """cfg = load_rules()
result = corroborate(bundle, cfg)

print(f"{len(result.findings)} verdicts — {sum(p.matched for p in result.pairs)} affectations appariées sur {len(result.pairs)}")
print("Fichiers sources après traitement :", "inchangés" if result.integrity_after.ok else "MODIFIÉS")
pd.Series(result.counts(), name="verdicts").to_frame()"""),
("md", """Une petite fonction pour afficher la fiche complète d'un écart : valeurs comparées, verdict, justification, règle et référence du mapping, cause probable, priorité, puis **les lignes d'origine ayant servi à la décision**."""),
("code", """FICHE = ["verdict", "subcategory", "source_raw", "expected", "target_raw", "justification", "rule_id", "rule_ref",
         "confidence", "rule_params", "probable_cause", "priority_breakdown"]


def fiche(finding_id):
    f = next(x for x in result.findings if x.finding_id == finding_id)
    rec = f.to_record()
    display(pd.DataFrame({"valeur": {k: rec[k] for k in FICHE if rec[k] not in ("", None)}}))
    for ev, row in evidence_rows(bundle, f):
        valeurs = {c: row[c] for c in ev.values if c in row} or ev.values
        print(f"— preuve {ev.id} : table {ev.table}, ligne {ev.row}" + (f" ({ev.note})" if ev.note else ""))
        print("  ", valeurs)
    return f"""),
("md", """## 3. Trois verdicts

### Cas 1 — conforme : date de début d'affectation de l'employé 2173396

Le poste 69289 a changé trois fois d'unité administrative (325 → 320 → 352). La règle retient l'intersection des intervalles : début = MAX(date d'entrée, début de l'unité courante). Elle retrouve exactement la date de la cible. La lecture littérale de la règle (« date la plus ancienne ») aurait donné une autre date : le verdict en dépend, donc la confiance est `MOYENNE` et le verdict alternatif est tracé."""),
("code", """_ = fiche("2173396:P:69289:assignmentStartDate")"""),
("md", """### Cas 2 — écart justifié automatiquement : motif d'absence de l'employé 2911996

La source porte le motif `807`, la cible `170`. Une comparaison brute dirait « différent ». La règle de situation d'emploi s'applique : l'employé est en absence complète (code de traitement des accès 02), donc le code attendu est le code Remphor du motif, obtenu par jointure avec la table des motifs (ligne 33)."""),
("code", """_ = fiche("2911996:P:12548:statusReasonCode")"""),
("md", """### Cas 3 — vraie anomalie : type d'employé de l'employé 2762457

L'employé est permanent à temps plein : la règle attend `JWN`, la cible contient `WHX`. Le moteur d'hypothèses constate que l'employé 4625374 présente l'écart inverse : il s'agit probablement d'une **permutation** entre les deux enregistrements. Erreur isolée et certaine, avec sa contrepartie identifiée : priorité maximale."""),
("code", """_ = fiche("2762457:P:30106:contractTypeCode")"""),
("md", """## 4. La vue de l'investigateur

Les écarts à investiguer, triés par priorité. Les erreurs isolées (affectation absente, permutations) passent devant les artefacts systémiques (courriels et libellés d'emploi), qui se corrigent une seule fois."""),
("code", """inv = findings_frame(investigate(result.findings))
inv[["Priorité", "Employé", "Champ", "Verdict", "Cause probable"]].head(20)"""),
("md", """### Motifs et règles candidates"""),
("code", """pd.DataFrame([(p.hypothesis_id, p.target_field, p.count, p.description) for p in result.analysis.patterns],
             columns=["hypothèse", "champ", "verdicts", "description"])"""),
("code", """pd.DataFrame([(c.target_field, c.column, f"{c.support:.0%}", f"{c.mapped_support:.0%}", len(c.explains))
              for c in result.analysis.candidate_rules],
             columns=["champ", "colonne candidate", "concordance", "règle actuelle", "anomalies expliquées"])"""),
("md", """### Interprétations : taux d'accord avec la cible

Pour la date de début d'affectation, l'intersection d'intervalles s'accorde avec la cible sur 86 % des enregistrements ; la lecture littérale, sur aucun."""),
("code", """pd.DataFrame([(s.interpretation, s.target_field, s.choice, "oui" if s.active else "", f"{s.rate:.0%}")
              for s in result.interpretation_stats if s.target_field in ("assignmentStartDate", "divisionName")],
             columns=["interprétation", "champ", "choix", "retenu", "accord avec la cible"])"""),
("md", """## 5. Rétroaction experte

Une règle candidate devient une **règle experte** seulement après un aperçu exécuté à blanc, qui montre exactement quels verdicts changeraient, puis l'acceptation de l'expert."""),
("code", """from corroborai.feedback import ExpertRule, FeedbackStore, preview_rule

candidate = next(c for c in result.analysis.candidate_rules if c.target_field == "weeklyHoursOverride")
regle = ExpertRule("", "accept_alternative_source", candidate.target_field, {"column": candidate.column},
                   "Les heures du système Temps proviennent du contrat du poste.")
apercu = preview_rule(regle, result.findings, result.rule_context, set(cfg.targets))
print(apercu.summary())
findings_frame(apercu.matched)[["Employé", "Champ", "Valeur attendue", "Valeur cible"]]"""),
("md", """Le fichier d'exemple `feedback/exemple_retroaction.yaml` contient trois règles et une correction. Appliqué à la corroboration :"""),
("code", """exemple = FeedbackStore.load(racine / "feedback" / "exemple_retroaction.yaml", set(cfg.targets))
avec_retroaction = corroborate(bundle, cfg, feedback=exemple)

print("Règles appliquées :", {k: len(v) for k, v in avec_retroaction.feedback.rules_applied.items()})
print("Corrections appliquées :", avec_retroaction.feedback.corrections_applied)
pd.DataFrame({"sans rétroaction": result.counts(), "avec rétroaction": avec_retroaction.counts()})"""),
("md", """## 6. Couche IA encadrée

Par défaut, le fournisseur est le **gabarit** : aucun LLM, résultat reproductible et hors ligne. Avec Gemini ou un modèle local, seuls les textes changent ; les verdicts et les priorités restent identiques."""),
("code", """from corroborai.ai.tasks import run_ai

rapport_ia = run_ai(result, bundle)                       # fournisseur par défaut : gabarit
# rapport_ia = run_ai(result, bundle, provider_name="gemini")   # nécessite GEMINI_API_KEY
# rapport_ia = run_ai(result, bundle, provider_name="local")    # serveur compatible OpenAI local

print("Fournisseur :", rapport_ia.provider, "| tâches :", dict(rapport_ia.statuses))
print()
print(next(s.synthese for s in rapport_ia.summaries if s.scope == "GLOBALE"))"""),
("md", """### Protection des données

Avant tout envoi à un fournisseur externe, le dossier est pseudonymisé et la politique de flux est évaluée. Voici un extrait du dossier qui serait envoyé pour l'employé 2762457 : les matricules et courriels n'y figurent pas."""),
("code", """from corroborai.ai.policy import Pseudonymizer, check_flow, load_llm_config
from corroborai.ai.tasks import employee_dossier

pseudo = Pseudonymizer.from_bundle(bundle)
ecarts = [f for f in result.findings if f.person_id == "2762457" and f.priority is not None]
dossier = employee_dossier("2762457", ecarts, pseudo, exclude_systemic=False)
llm = load_llm_config()

print("Décision de flux vers Gemini :", check_flow(dossier, llm.provider("gemini"), llm.flow, pseudo).summary())
print("Matricule présent dans la charge :", "2762457" in dossier.serialize())
print()
print(dossier.serialize()[:900], "…")"""),
("md", """## 7. Rapport exportable"""),
("code", """from corroborai.report import write_csv, write_report

sortie = racine / "out"
xlsx = write_report(result, sortie / "rapport_corroboration.xlsx")
csv = write_csv(result, sortie / "verdicts.csv")
print("Rapport :", xlsx.relative_to(racine))
print("CSV     :", csv.relative_to(racine))
print("Fichiers sources toujours inchangés :", bundle.verify_unchanged().ok)"""),
("md", """## Pour aller plus loin

- `corroborai app` — l'interface web : investigation écart par écart, corrections et règles expertes.
- [Guide d'utilisation](../docs/GUIDE_UTILISATEUR.md) · [Architecture](../docs/ARCHITECTURE.md) · [Utilisation de l'IA](../docs/IA.md) · [Règles](../docs/REGLES.md) · [Hypothèses et limites](../docs/HYPOTHESES_ET_LIMITES.md)"""),
]


def source_lines(text: str) -> list[str]:
    lines = text.split("\n")
    return [l + "\n" for l in lines[:-1]] + [lines[-1]]


def run_cell(code: str, ns: dict) -> list[dict]:
    outputs: list[dict] = []
    tree = ast.parse(code)
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    buf = io.StringIO()

    def display(obj):
        flush()
        outputs.append(rich(obj, "display_data"))

    def flush():
        if buf.getvalue():
            outputs.append({"output_type": "stream", "name": "stdout", "text": source_lines(buf.getvalue())})
            buf.seek(0)
            buf.truncate()

    ns["display"] = display
    with contextlib.redirect_stdout(buf):
        exec(compile(tree, "<cellule>", "exec"), ns)
        value = eval(compile(ast.Expression(last.value), "<cellule>", "eval"), ns) if last else None
    flush()
    if value is not None:
        outputs.append(rich(value, "execute_result"))
    return outputs


COUNTER = {"n": 0}


def rich(obj, kind: str) -> dict:
    data = {"text/plain": source_lines(obj.to_string() if hasattr(obj, "to_string") else repr(obj))}
    if hasattr(obj, "_repr_html_"):
        data["text/html"] = source_lines(obj._repr_html_())
    out = {"output_type": kind, "data": data, "metadata": {}}
    if kind == "execute_result":
        out["execution_count"] = COUNTER["n"]
    return out


def main() -> None:
    os.chdir(REPO / "notebooks")
    ns: dict = {"__name__": "__main__"}
    cells = []
    for kind, text in CELLS:
        if kind == "md":
            cells.append({"cell_type": "markdown", "metadata": {}, "source": source_lines(text)})
            continue
        COUNTER["n"] += 1
        try:
            outputs = run_cell(text, ns)
        except Exception:
            print(f"ÉCHEC cellule {COUNTER['n']} :\n{text}\n", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)
        cells.append({"cell_type": "code", "execution_count": COUNTER["n"], "metadata": {},
                      "outputs": outputs, "source": source_lines(text)})
    nb = {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": sys.version.split()[0]}},
        "nbformat": 4, "nbformat_minor": 5}
    for i, c in enumerate(nb["cells"]):
        c["id"] = f"cell-{i:02d}"
    OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT} : {len(cells)} cellules, {COUNTER['n']} exécutées sans erreur")


if __name__ == "__main__":
    main()
