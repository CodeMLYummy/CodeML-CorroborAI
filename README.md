# CorroborAI

Corroboration hybride des données entre le **Système A – RH** (source) et le
**Système B – Temps** (cible) : règles métier déterministes d'abord, IA
encadrée seulement là où elle est nécessaire.

> Documentation en cours de rédaction — ce README sera complété à chaque étape.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,app]"     # « app » ajoute Streamlit pour l'interface web
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

## Interface web

```bash
corroborai app        # ou : streamlit run app/streamlit_app.py
```

L'interface permet de charger les fichiers (répertoire ou téléversement — les
fichiers téléversés sont copiés dans un répertoire temporaire, les originaux
ne sont jamais modifiés), de choisir les interprétations et le mode IA, de
lancer la corroboration, puis :

- **Vue d'ensemble** — décomptes, intégrité, synthèse globale, priorités ;
- **À investiguer** — écarts triés par priorité ; pour chacun : valeurs source /
  attendue / cible, règle et référence Mapping.xlsx, cause probable, calcul de
  la priorité, hypothèses, et **les lignes d'origine ayant servi à la décision** ;
  correction du verdict par l'expert ;
- **Motifs et règles candidates** — avec création d'une règle en un clic ;
- **Rétroaction experte** — consigne en langage naturel traduite en règle,
  formulaire, règles suggérées à partir des corrections, gestion de la
  rétroaction enregistrée ;
- **Tous les verdicts** et **Exporter** (Excel, CSV).

## Rétroaction experte

L'expert peut **corriger un verdict** ou **créer une règle** ; tout est
enregistré dans `feedback/retroaction.yaml` (lisible, versionnable) et réappliqué
à chaque exécution (`corroborai run --retroaction feedback/retroaction.yaml`).

- Une **correction** porte une empreinte des valeurs jugées : si les données
  changent, elle est déclarée *périmée* et n'est plus appliquée.
- Une **règle experte** s'exprime dans un **mini-langage fermé** de quatre
  opérations, qui ne peuvent que reconnaître des écarts comme acceptables
  (ANOMALIE → JUSTIFIE) : `accept_alternative_source`, `accept_value_mapping`,
  `accept_hypothesis`, `accept_subcategory`. Les verdicts conformes ne sont
  jamais touchés.
- Toute règle est **exécutée à blanc avant acceptation** : l'aperçu montre
  exactement quels verdicts changeraient.
- Les corrections répétées sur des écarts semblables génèrent des **règles
  suggérées** (enrichissement de la base de règles).
- Une consigne en langage naturel peut être **traduite par le LLM** en règle du
  mini-langage, sous le même harnais : champ, colonnes, hypothèses et
  sous-catégories doivent provenir du dossier, la règle doit être valide, puis
  elle passe par l'aperçu et l'acceptation explicite. Sans LLM, le formulaire
  produit les mêmes règles.

Les verdicts modifiés portent la source de décision `EXPERT`, la règle ou la
correction appliquée et le verdict initial ; la feuille « Rétroaction experte »
du rapport liste chaque règle et correction avec son effet.

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

Les feuilles **Motifs** (permutations, écarts systémiques, motifs récurrents) et
**Règles candidates** (sources alternatives confirmées sur toute la population,
à valider par l'expert) donnent une vue d'ensemble pour l'investigation.

Chaque verdict est produit par une règle déterministe (`REGLE_DETERMINISTE`).
Un champ dont le verdict changerait sous une interprétation alternative voit
sa confiance abaissée à `MOYENNE` et le verdict alternatif est tracé.

## Analyse des écarts (niveau 3, déterministe)

Une différence entre les deux systèmes peut s'expliquer sans être justifiée par
le mapping. Le **moteur d'hypothèses** (`config/hypotheses.yaml`) teste, pour
chaque anomalie, un catalogue fermé d'explications génériques : permutation
entre deux employés, valeur d'un autre enregistrement de l'historique du poste,
autre colonne source confirmée sur toute la population, interprétation
alternative, recodage systématique, écart systémique, type d'affectation absent.
Il s'agit d'un raisonnement abductif symbolique : reproductible, sans
apprentissage ni appel externe, et qui **ne modifie jamais un verdict** (un
test vérifie que les verdicts sont identiques avec et sans analyse).

Le **score de priorité** (`config/scoring.yaml`) combine la criticité du champ,
la confiance du verdict et des modificateurs liés aux hypothèses vérifiées ;
son calcul est publié avec chaque verdict. Les erreurs isolées et certaines
(permutation, affectation absente) passent devant les artefacts systémiques
(anonymisation des courriels et des libellés d'emploi), à traiter une seule fois.

## Couche LLM encadrée (optionnelle)

```bash
corroborai run --data-dir data --out out --ia                         # gabarit déterministe (défaut, hors ligne)
GEMINI_API_KEY=... corroborai run --data-dir data --out out --ia --fournisseur gemini
corroborai run --data-dir data --out out --ia --fournisseur local     # Ollama / LM Studio / llama.cpp / vLLM
```

Le LLM n'intervient **que** là où aucune règle ne peut le remplacer :
une **synthèse globale** des motifs pour l'équipe fonctionnelle, une
**synthèse par employé** (seulement si une anomalie non systémique atteint le
seuil de priorité), et le **triage** d'une anomalie qu'aucune hypothèse
déterministe n'explique (piste explicitement marquée « non vérifiée »).

Le harnais (`src/corroborai/ai/`) garantit que :

- le modèle **n'a aucun outil** : un appel = un dossier → un JSON ;
- la sortie suit un **schéma strict** sans aucun champ de verdict — l'IA ne peut
  structurellement pas modifier un verdict ni une priorité (testé avec un modèle
  malveillant simulé) ;
- chaque preuve et référence citée existe dans le dossier, et toute date, tout
  nombre, tout jeton d'employé ou toute valeur citée dans un texte y figure
  (contrôle anti-hallucination) ;
- en cas d'échec : une nouvelle tentative avec la liste des erreurs, puis
  **repli sur un gabarit déterministe** ; le rapport indique toujours la source
  de chaque explication (`LLM` ou `GABARIT`) et le statut du harnais ;
- le contenu des données est encadré et neutralisé contre l'injection de prompt ;
- les réponses sont mises en cache (réexécution reproductible, sans clé) et
  **revalidées à chaque lecture** ;
- chaque appel est consigné dans `out/audit_ia.jsonl` (charge pseudonymisée,
  réponse brute, erreurs, décision de flux) ; la clé d'API n'y figure jamais.

### Protection des données : politique de flux

Inspirée du contrôle de flux d'information (voir
[OpenAPPA](https://github.com/archestra-ai/OpenAPPA) pour le cas des agents
munis d'outils), une **politique de flux déclarative** (`config/llm.yaml`)
est évaluée par une fonction pure **avant chaque envoi** :

- les identifiants personnels (matricules, noms, courriels, identifiants
  numériques longs) sont remplacés par des jetons opaques (`EMP-03`) ; la
  réidentification n'a lieu que localement, après validation ;
- chaque élément du dossier porte une classe de donnée ; un élément **non
  classifié bloque l'envoi** (fermé par défaut) ;
- vers un fournisseur **externe**, la charge sérialisée est balayée : tout
  identifiant brut connu ou motif d'identifiant bloque l'appel (bascule sur le
  gabarit) ;
- un fournisseur déclaré « local » dont l'adresse n'est pas une adresse de
  bouclage est automatiquement traité comme externe.

L'option `--fournisseur local` permet ainsi de garder toutes les données sur le
poste pour des données réellement sensibles.

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

Les tests d'intégration sont ignorés si les données sont absentes. L'interface
est testée de bout en bout contre un module Streamlit simulé, et avec l'outil
officiel `AppTest` lorsque Streamlit est installé. Les tests
d'altération travaillent sur une copie temporaire, jamais sur les originaux.

## Structure

```
config/datasets.yaml        registre des fichiers d'entrée et colonnes requises
config/rules.yaml           mapping codifié (règles, interprétations, criticités)
config/hypotheses.yaml      catalogue d'hypothèses et seuils
config/scoring.yaml         formule et modificateurs de priorité
config/llm.yaml             fournisseurs LLM, harnais, politique de flux
feedback/retroaction.yaml   rétroaction experte enregistrée (créé par l'interface)
docs/REGLES.md              documentation des règles (générée)
src/corroborai/models.py    modèle Finding / Verdict / Evidence
src/corroborai/io/          chargement en lecture seule, intégrité, normalisation
src/corroborai/rules_config.py   chargement et validation de rules.yaml
src/corroborai/rules_doc.py      génération de docs/REGLES.md
src/corroborai/matching.py       appariement des affectations
src/corroborai/rules/            règles déterministes (dates, situation, courriel…)
src/corroborai/engine.py         moteur de corroboration
src/corroborai/analysis/         moteur d'hypothèses et score de priorité
src/corroborai/ai/               couche LLM encadrée (politique, schémas, harnais, tâches)
src/corroborai/feedback.py       rétroaction experte (corrections, mini-langage de règles)
src/corroborai/service.py        couche de service de l'interface
src/corroborai/report/           rapport Excel + CSV
app/streamlit_app.py             interface web
src/corroborai/cli.py       interface en ligne de commande
tests/
```

## Avancement

- [x] Étape 1 — squelette, modèle de données, chargement + intégrité, normalisation
- [x] Étape 2 — règles codifiées (`config/rules.yaml`)
- [x] Étape 3 — appariement des affectations, moteur de règles, rapport Excel
- [x] Étape 4 — détection de motifs, moteur d'hypothèses, scoring
- [x] Étape 5 — harnais LLM (gabarit · Gemini · compatible OpenAI)
- [x] Étape 6 — interface Streamlit, rétroaction experte
- [ ] Étape 7 — documentation, notebook de démo
