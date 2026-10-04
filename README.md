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
corroborai check --data-dir data
```

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
src/corroborai/models.py    modèle Finding / Verdict / Evidence
src/corroborai/io/          chargement en lecture seule, intégrité, normalisation
src/corroborai/cli.py       interface en ligne de commande
tests/
```

## Avancement

- [x] Étape 1 — squelette, modèle de données, chargement + intégrité, normalisation
- [ ] Étape 2 — règles codifiées (`config/rules.yaml`)
- [ ] Étape 3 — appariement des affectations, moteur de règles, rapport Excel
- [ ] Étape 4 — détection de motifs, moteur d'hypothèses, scoring
- [ ] Étape 5 — harnais LLM (gabarit · Gemini · compatible OpenAI)
- [ ] Étape 6 — interface Streamlit, rétroaction experte
- [ ] Étape 7 — documentation, notebook de démo
