# CorroborAI

**Corroboration intelligente des données entre un système RH et un système de gestion du temps.**

CorroborAI compare les extractions du **Système A – RH** (source) et du
**Système B – Temps** (cible), applique les règles métier du mapping, et classe
chaque champ de chaque affectation comme **conforme**, **écart justifié**,
**anomalie** ou **indéterminé**, avec une justification traçable jusqu'aux
données et à la règle qui ont conduit au verdict. Le rapport final met en tête
les seules anomalies à investiguer, triées par priorité, avec leur cause probable.

```
$ corroborai run --data-dir data --out out
Corroboration terminée en 0.09 s — 551 verdicts, 22 affectations appariées sur 23
  ANOMALIE       62
  INDETERMINE     0
  JUSTIFIE      128
  CONFORME      361
Fichiers sources après traitement : inchangés (empreintes identiques)
Rapport : out/rapport_corroboration.xlsx
CSV     : out/verdicts.csv
```

## Fonctionnalités

- **Règles métier déterministes et traçables** — les 25 champs du mapping sont
  codifiés dans `config/rules.yaml`, chacun relié à sa ligne de `Mapping.xlsx` ;
  les ambiguïtés du texte des règles sont tranchées par des interprétations
  documentées, et chaque champ est réévalué sous les interprétations alternatives.
- **Analyse des écarts** — un moteur d'hypothèses (raisonnement abductif
  symbolique) explique les anomalies : permutation entre deux employés, valeur
  d'un autre enregistrement de l'historique, autre colonne source confirmée sur
  toute la population, recodage systématique, écart systémique, type
  d'affectation non transmis. Il propose des **règles candidates** à l'expert.
- **Priorisation** — score de priorité transparent (criticité du champ ×
  confiance × hypothèses) ; les erreurs isolées et certaines passent devant les
  artefacts systémiques à traiter une seule fois.
- **IA générative encadrée (optionnelle)** — synthèses rédigées pour l'équipe
  fonctionnelle, piste pour les anomalies inexpliquées, traduction d'une
  consigne experte en règle. Le LLM n'a aucun outil, ne peut pas modifier un
  verdict, et toute sortie est validée ; fonctionne avec Gemini, un modèle local
  (Ollama, LM Studio…) ou sans LLM.
- **Protection des données** — pseudonymisation et politique de flux évaluée
  avant tout envoi ; l'option « local » garde toutes les données sur le poste.
- **Rétroaction experte** — l'expert corrige un verdict ou crée une règle (avec
  aperçu d'impact) ; les corrections répétées génèrent des règles suggérées.
- **Interface web** — chargement des fichiers, lancement, investigation écart
  par écart avec les lignes d'origine ayant servi à la décision, rétroaction,
  export.
- **Fichiers sources intouchés** — lecture seule, avec preuve par empreinte
  SHA-256 avant et après traitement.

## Démarrage rapide

**Prérequis :** Python 3.10 ou plus récent.

```bash
git clone <dépôt> corroborai && cd corroborai
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -e ".[app]"                                # ou : pip install -r requirements.txt
```

Déposer les fichiers d'extraction dans `data/` **sous leurs noms d'origine**,
au format Excel ou CSV :

| Fichier | Contenu | Requis |
|---|---|:-:|
| `Employe_Source_Anonymise_VF.xlsx` | extraction Système A – RH | oui |
| `Employe_Destination_Anonymise_VF.xlsx` | extraction Système B – Temps | oui |
| `détail_du_poste.xlsx` | historique du détail du poste | oui |
| `Motif de la situation d'emploi.xlsx` | motifs des situations d'emploi | oui |
| `Mapping.xlsx` | mapping, pour vérifier la traçabilité des règles | non |
| `manifest.json` | empreintes des fichiers distribués | non |

Puis :

```bash
corroborai check --data-dir data          # vérifie fichiers, colonnes et règles
corroborai run --data-dir data --out out  # produit out/rapport_corroboration.xlsx et out/verdicts.csv
corroborai app                            # ou lance l'interface web
```

> Le même nom en `.csv` est accepté (séparateur `;`, `,` ou tabulation ;
> encodage UTF-8 ou Windows-1252), ainsi que les variations d'accents ou
> d'apostrophes dans les noms. Les fichiers peuvent contenir plus de lignes que
> l'échantillon d'origine. Si un `manifest.json` est présent, les empreintes y
> sont comparées à titre informatif (`--manifeste-strict` pour l'exiger).

## Le rapport

`out/rapport_corroboration.xlsx` est conçu pour la personne qui investigue :

| Feuille | Contenu |
|---|---|
| **Synthèse** | exécution, intégrité, décomptes, synthèse globale, priorités les plus élevées |
| **À investiguer** | anomalies et indéterminés triés par priorité : valeurs source / attendue / cible, cause probable, justification, calcul de la priorité, règle, preuves |
| **Motifs** | permutations, écarts systémiques, motifs récurrents |
| **Règles candidates** | sources alternatives confirmées sur la population, à valider |
| **Synthèses IA** | synthèse globale et par employé (si `--ia`) |
| **Rétroaction experte** | règles et corrections appliquées, avec leur effet (si `--retroaction`) |
| **Écarts justifiés** · **Conformes** | verdicts par catégorie |
| **Détail** | tous les verdicts, toutes les colonnes de traçabilité |
| **Affectations** | appariement source ↔ cible |
| **Interprétations** | taux d'accord avec la cible de chaque interprétation, et justification |
| **Intégrité** | contrôle du manifeste, empreintes avant et après traitement |

`out/verdicts.csv` contient le détail complet (UTF-8, séparateur `;`).
Le [guide d'utilisation](docs/GUIDE_UTILISATEUR.md) explique comment lire un
verdict et mener une investigation.

## Comment ça fonctionne

| Niveau | Rôle | Nature |
|:-:|---|---|
| 0 | Chargement en lecture seule, normalisation (formats, vides, dates, encodage) | déterministe |
| 1 | Appariement des affectations (par employé, type, puis similarité de contenu) | déterministe |
| 2 | Règles métier : valeur attendue → verdict, sous chaque interprétation | déterministe |
| 3 | Moteur d'hypothèses, règles candidates, score de priorité | IA symbolique, déterministe |
| 4 | Synthèses, triage, traduction de consignes (optionnel) | LLM encadré |

Les verdicts sont fixés aux niveaux 2 (règles) ou par l'expert (rétroaction) ;
les niveaux 3 et 4 les expliquent et les priorisent sans jamais les modifier.
Détails : [architecture](docs/ARCHITECTURE.md), [règles](docs/REGLES.md),
[utilisation de l'IA](docs/IA.md).

## L'IA dans CorroborAI

L'IA intervient à deux niveaux distincts, chacun là où une règle statique ne
suffit pas :

- **Moteur d'hypothèses** (toujours actif) — catalogue fermé d'hypothèses
  génériques testées sur chaque anomalie, avec preuves ; reproductible et sans
  appel externe. C'est lui qui identifie les causes probables et les règles
  candidates.
- **LLM encadré** (optionnel, `--ia`) — rédaction et triage sous un harnais
  strict : dossier pseudonymisé, schéma de sortie sans champ de verdict,
  preuves et valeurs citées vérifiées dans les données, repli sur un gabarit
  déterministe, journal d'audit `out/audit_ia.jsonl`.

| Mode | Commande | Données |
|---|---|---|
| Gabarit (défaut) | `corroborai run … --ia` | aucun LLM, hors ligne |
| Gemini | `GEMINI_API_KEY=… corroborai run … --ia --fournisseur gemini` | pseudonymisées, contrôle anti-fuite avant envoi |
| Local | `corroborai run … --ia --fournisseur local` | restent sur le poste |

Les fournisseurs se configurent dans `config/llm.yaml` (tout service compatible
avec l'API OpenAI peut y être ajouté). Aucun modèle n'est entraîné : le
[document IA](docs/IA.md) explique ce choix.

## Rétroaction experte

Depuis l'interface (onglets « À investiguer » et « Rétroaction experte »),
l'expert peut :

- **corriger un verdict** — la correction porte une empreinte des valeurs
  jugées et devient « périmée » si les données changent ;
- **créer une règle** dans un mini-langage fermé de quatre opérations qui
  reconnaissent des écarts comme acceptables — depuis une règle candidate, un
  formulaire, une règle suggérée à partir de ses corrections, ou une consigne en
  langage naturel traduite par le LLM. Toute règle est **exécutée à blanc**
  avant acceptation.

La rétroaction est enregistrée dans `feedback/retroaction.yaml` (créé à la
première décision) et réappliquée par `--retroaction`. Un exemple est fourni :

```bash
corroborai run --data-dir data --out out --retroaction feedback/exemple_retroaction.yaml
```

## Référence de la ligne de commande

| Commande | Rôle |
|---|---|
| `corroborai check --data-dir DIR` | fichiers reconnus, colonnes requises, cohérence des règles avec les données |
| `corroborai run --data-dir DIR --out OUT` | corroboration complète, rapport Excel et CSV |
| `corroborai app` | interface web (Streamlit) |
| `corroborai rules [--markdown -o FICHIER]` | liste ou documente les règles codifiées |

Options de `run` :

| Option | Effet |
|---|---|
| `--interpretation ID=CHOIX` | force une interprétation alternative (répétable), ex. `INT-ASSIGN-DATES=strict_literal` |
| `--retroaction FICHIER` | applique la rétroaction experte |
| `--ia` · `--fournisseur NOM` · `--cache-ia DIR` | active la couche LLM, choisit le fournisseur, réutilise les réponses validées |
| `--manifeste-strict` | exige la conformité des fichiers à `manifest.json` |
| `--config` · `--rules` · `--llm-config` | chemins de configuration alternatifs |

## Configuration

| Fichier | Contenu |
|---|---|
| `config/datasets.yaml` | fichiers d'entrée, colonnes requises, fichiers facultatifs |
| `config/rules.yaml` | mapping codifié : règles, interprétations, criticités, appariement |
| `config/hypotheses.yaml` | catalogue d'hypothèses et seuils |
| `config/scoring.yaml` | formule et modificateurs de priorité |
| `config/llm.yaml` | fournisseurs LLM, harnais, politique de flux de données |

Chaque fichier est validé au chargement ; une incohérence est signalée avec sa
cause. Après une modification de `rules.yaml`, régénérer la documentation :
`corroborai rules --markdown -o docs/REGLES.md`.

## Tests

```bash
pip install -e ".[dev,app]"
pytest
```

Le notebook de démonstration se régénère, avec ses sorties et sans Jupyter,
par `python notebooks/build_demo.py` (fichiers du défi dans `data/`).

La suite couvre chaque règle, l'appariement, le moteur d'hypothèses, le
harnais IA (y compris face à un modèle malveillant simulé et contre des
serveurs HTTP locaux), la rétroaction, le rapport et l'interface. Elle
s'appuie sur un **jeu de données synthétique** conçu scénario par scénario
(`tests/fixtures/dataset.py`, généré en Excel et en CSV) et s'exécute donc
partout. Les tests qui figent les résultats des fichiers du défi ne
s'exécutent que si ces fichiers, reconnus par leurs empreintes, sont présents
(`CORROBORAI_DATA_DIR`, par défaut `data/`).

## Structure du projet

```
app/streamlit_app.py      interface web
config/                   règles, hypothèses, priorité, LLM, fichiers d'entrée
data/                     fichiers d'extraction (non versionnés)
docs/                     documentation
feedback/                 rétroaction experte (exemple fourni)
notebooks/                notebook de démonstration et son générateur
src/corroborai/
├── io/                   chargement Excel/CSV, intégrité, normalisation
├── models.py             modèle des verdicts (Finding, Evidence…)
├── matching.py           appariement des affectations
├── rules/                règles déterministes
├── engine.py             moteur de corroboration
├── analysis/             moteur d'hypothèses, score de priorité
├── ai/                   couche LLM encadrée (politique de flux, schémas, harnais, tâches)
├── feedback.py           rétroaction experte
├── report/               rapport Excel et CSV
├── service.py            logique de l'interface
└── cli.py                ligne de commande
tests/                    tests ; tests/fixtures/ : jeu de données synthétique
```

## Documentation

| Document | Contenu |
|---|---|
| [Démonstration](notebooks/demo.ipynb) | notebook exécuté : un cas conforme, un écart justifié, une anomalie, la rétroaction, l'IA et le rapport |
| [Guide d'utilisation](docs/GUIDE_UTILISATEUR.md) | lire un verdict, investiguer, utiliser l'interface et la rétroaction |
| [Architecture](docs/ARCHITECTURE.md) | pipeline, modèle de données, garanties, points d'extension |
| [Règles de corroboration](docs/REGLES.md) | champs, règles, interprétations, criticités, hypothèses (généré) |
| [Utilisation de l'IA](docs/IA.md) | moteur d'hypothèses, harnais LLM, protection des données |
| [Hypothèses et limites](docs/HYPOTHESES_ET_LIMITES.md) | choix d'interprétation, hypothèses sur les données, limites connues |
