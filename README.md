# CorroborAI

Corroboration hybride des données entre le **Système A – RH** (source) et le
**Système B – Temps** (cible) : règles métier déterministes d'abord, IA
encadrée seulement là où elle est nécessaire.

> Documentation en cours de rédaction — ce README sera complété à chaque étape.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Données

Copier les fichiers du défi (tels que distribués, avec `manifest.json`) dans
`data/`. Ils ne sont **jamais modifiés** : chaque fichier est lu une seule fois
en octets et son SHA-256 est vérifié contre `manifest.json` avant traitement.
Les noms sont résolus de façon tolérante (accents, apostrophes, NFC/NFD) ; en
cas de renommage, c'est l'empreinte qui fait foi.

```bash
corroborai run --data-dir data --out out        # corroboration complète + rapport
corroborai run --data-dir data --out out \
    --interpretation INT-ASSIGN-DATES=strict_literal   # forcer une interprétation alternative
corroborai check --data-dir data      # intégrité des fichiers + validation des règles
corroborai rules                      # liste des champs corroborés
corroborai rules --markdown -o docs/REGLES.md   # régénère la documentation des règles
```

## Rapport

`corroborai run` produit `out/rapport_corroboration.xlsx` et `out/verdicts.csv` :

| Feuille | Contenu |
|---|---|
| Synthèse | exécution, intégrité avant/après, décomptes (formules sur « Détail ») |
| À investiguer | anomalies et indéterminés, triés par criticité puis confiance |
| Écarts justifiés / Conformes | verdicts par catégorie |
| Détail | tous les verdicts : valeurs source / attendue / cible, règle, référence Mapping.xlsx, preuves (table:ligne), paramètres |
| Affectations | appariement source ↔ cible (méthode, similarité) |
| Interprétations | taux d'accord avec la cible de chaque interprétation, par champ, et justification des choix |
| Intégrité | empreintes SHA-256 avant et après traitement |

Chaque verdict est produit par une règle déterministe (`REGLE_DETERMINISTE`).
Un champ dont le verdict changerait sous une interprétation alternative voit
sa confiance abaissée à `MOYENNE` et le verdict alternatif est tracé.

## Règles métier

Le mapping est codifié dans [`config/rules.yaml`](config/rules.yaml) : pour
chaque champ, la règle appliquée, l'interprétation retenue en cas
d'ambiguïté, la criticité justifiée et la référence exacte de la ligne de
`Mapping.xlsx`. La documentation lisible [`docs/REGLES.md`](docs/REGLES.md)
est générée depuis ce fichier (un test échoue si elle est périmée).

La configuration est validée à deux niveaux :

- **interne** : types de règles connus, criticités, interprétations
  référencées, tables de transcodage sans chevauchement ;
- **contre les données** : colonnes existantes, chaque référence pointe vers
  une ligne de `Mapping.xlsx` qui mentionne le champ, chaque champ cible du
  mapping est corroboré ou explicitement exclu, et les correspondances des
  colonnes de jointure sont vérifiées empiriquement.

## Tests

```bash
pytest                                    # ou : python -m unittest discover -s tests -t .
CORROBORAI_DATA_DIR=/autre/chemin pytest  # si les données sont ailleurs
```

Les tests d'intégration sont ignorés si les données sont absentes. Les tests
d'altération travaillent sur une copie temporaire, jamais sur les originaux.

## Structure

```
config/datasets.yaml        registre des fichiers d'entrée et colonnes requises
config/rules.yaml           mapping codifié (règles, interprétations, criticités)
docs/REGLES.md              documentation des règles (générée)
src/corroborai/models.py    modèle Finding / Verdict / Evidence
src/corroborai/io/          chargement en lecture seule, intégrité, normalisation
src/corroborai/rules_config.py   chargement et validation de rules.yaml
src/corroborai/rules_doc.py      génération de docs/REGLES.md
src/corroborai/matching.py       appariement des affectations
src/corroborai/rules/            règles déterministes (dates, situation, courriel…)
src/corroborai/engine.py         moteur de corroboration
src/corroborai/report/           rapport Excel + CSV
src/corroborai/cli.py       interface en ligne de commande
tests/
```

## Avancement

- [x] Étape 1 — squelette, modèle de données, chargement + intégrité, normalisation
- [x] Étape 2 — règles codifiées (`config/rules.yaml`)
- [x] Étape 3 — appariement des affectations, moteur de règles, rapport Excel
- [ ] Étape 4 — détection de motifs, moteur d'hypothèses, scoring
- [ ] Étape 5 — harnais LLM (gabarit · Gemini · compatible OpenAI)
- [ ] Étape 6 — interface Streamlit, rétroaction experte
- [ ] Étape 7 — documentation, notebook de démo
