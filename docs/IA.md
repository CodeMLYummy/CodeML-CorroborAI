# Utilisation de l'IA

CorroborAI utilise l'IA à deux niveaux, chacun là où une règle statique ne
suffit pas, et jamais pour fixer un verdict :

| | Moteur d'hypothèses | LLM encadré |
|---|---|---|
| Nature | IA symbolique (raisonnement abductif) | IA générative |
| Rôle | expliquer les anomalies, proposer des règles, prioriser | rédiger, trier les cas inexpliqués, traduire des consignes |
| Activation | toujours | optionnelle (`--ia`) |
| Reproductibilité | totale | réponses validées et mises en cache ; repli déterministe |
| Données | locales | pseudonymisées ; contrôle de flux avant envoi |
| Peut modifier un verdict | non | non |

## 1. Le moteur d'hypothèses

Une différence entre les deux systèmes peut avoir une cause identifiable sans
être justifiée par le mapping. Les règles disent *si* un écart est acceptable ;
le moteur d'hypothèses cherche *pourquoi* il existe.

Pour chaque anomalie ou indéterminé, il teste un **catalogue fermé**
d'hypothèses génériques — aucune n'est écrite pour un champ particulier. Une
hypothèse vérifiée ajoute des preuves, une cause probable et un modificateur
de priorité.

| Hypothèse | Ce qu'elle détecte | Exemple sur les données du défi |
|---|---|---|
| `H-PERMUTATION` | la valeur cible de A est la valeur attendue de B, et réciproquement | types d'employé inversés entre 2762457 et 4625374 |
| `H-HISTORY-RECORD` | une date cible égale à un autre enregistrement de l'historique du poste | 3 dates de début égales à la date d'effet la plus récente |
| `H-ALT-SOURCE` | la cible égale une autre colonne, sur toute la population | heures cibles = heures du contrat du poste (100 % contre 82 %) |
| `H-ALT-INTERPRETATION` | conforme sous une interprétation alternative documentée | — |
| `H-BIJECTION` | recodage un-pour-un stable entre valeur attendue et cible | codes d'emploi de `positionName` (6203 → 4367 partout) |
| `H-SYSTEMIC` | même type d'écart sur la grande majorité des enregistrements | identifiants de courriel (22 sur 22) |
| `H-TYPE-ABSENT` | un type d'affectation n'existe pour aucun employé dans la cible | aucune affectation temporaire dans le système Temps |

**Garde-fous contre les fausses explications.** Les hypothèses de niveau champ
exigent un appui sur la population : une source alternative doit concorder
avec la cible sur au moins 90 % des enregistrements, mieux que la règle
actuelle ; une bijection exige qu'une même valeur attendue soit partagée par
des employés différents (sinon elle est triviale). Une concordance fortuite
sur un seul enregistrement est rejetée.

**Règles candidates.** Une source alternative confirmée sur la population est
proposée à l'expert comme règle, avec sa concordance et les anomalies qu'elle
expliquerait. L'expert l'accepte ou non ; elle n'est jamais appliquée seule.

**Priorisation.** Le score combine la criticité du champ, la confiance du
verdict et les hypothèses vérifiées : une permutation (erreur certaine, dont
la contrepartie est identifiée) monte ; un écart systémique (cause unique à
corriger une fois) descend. Le calcul est publié avec chaque verdict.

## 2. Pourquoi aucun modèle entraîné

Le choix est délibéré :

- **Pas de données d'apprentissage.** L'échantillon compte 20 employés et
  aucun verdict étiqueté ; un modèle supervisé ne ferait que mémoriser
  l'échantillon.
- **Explicabilité.** Les consignes exigent que chaque verdict soit relié à sa
  règle et à ses données ; un modèle statistique ne le permet pas sans
  approximation.
- **L'apprentissage passe par l'expert.** La boucle de rétroaction remplit le
  rôle d'un apprentissage contrôlé : les corrections répétées sur des écarts
  semblables génèrent des règles suggérées, que l'expert valide après un
  aperçu de leur effet. La base de règles s'enrichit sans perdre la
  traçabilité.

## 3. Le LLM encadré

### Tâches

Le LLM n'est sollicité que pour quatre tâches, chacune là où il apporte ce
qu'aucune règle n'apporte :

| Tâche | Quand | Sortie |
|---|---|---|
| `SYNTHESE_GLOBALE` | une fois par exécution | vue d'ensemble des motifs et pistes d'action |
| `SYNTHESE_EMPLOYE` | employés ayant une anomalie non systémique de priorité ≥ 30 | diagnostic regroupé et pistes de vérification |
| `TRIAGE_ANOMALIE` | anomalies qu'aucune hypothèse n'explique | catégorie de cause plausible, piste — marquée « non vérifiée » |
| `TRADUCTION_REGLE` | à la demande de l'expert | règle du mini-langage, à prévisualiser et accepter |

### Le harnais

Chaque appel passe par `ai/harness.py` :

1. **Décision de flux** — fonction pure évaluée avant tout envoi (section 4).
2. **Cache** — une réponse déjà validée est réutilisée, mais revalidée à
   chaque lecture : un cache altéré est rejeté.
3. **Appel sans outil** — un dossier JSON → une réponse JSON, température 0.
   Les données sont encadrées par `<donnees>…</donnees>` et neutralisées
   (« < » et « > » échappés) : une instruction cachée dans une valeur ne peut
   pas sortir du cadre.
4. **Validation stricte** (`ai/schemas.py`) :
   - schéma fermé, **sans aucun champ de verdict**, aucune clé supplémentaire ;
   - types, longueurs et énumérations (la confiance d'une piste IA ne peut être
     que « faible » ou « moyenne ») ;
   - chaque preuve et référence citée existe dans le dossier ;
   - **ancrage** : toute date, tout nombre d'au moins deux chiffres, tout jeton
     d'employé et toute valeur entre « » cités dans un texte figurent dans le
     dossier ;
   - pour une règle traduite : champ, colonne, hypothèse ou sous-catégorie
     issus du dossier, règle valide dans le mini-langage.
5. **Nouvelle tentative** avec la liste des erreurs, puis **repli sur un
   gabarit déterministe** : le rapport est toujours produit.
6. **Journal d'audit** `out/audit_ia.jsonl` : charge pseudonymisée, réponse
   brute, erreurs, décision de flux, statut. La clé d'API n'y figure jamais.

Le rapport indique pour chaque texte sa source (`LLM` ou `GABARIT`) et le
statut du harnais :

| Statut | Signification |
|---|---|
| `LLM_VALIDE` | réponse du modèle, validée |
| `CACHE_VALIDE` | réponse en cache, revalidée (aucun appel pendant cette exécution) |
| `GABARIT` | fournisseur « gabarit » : aucun LLM |
| `BLOQUE_POLITIQUE` | envoi refusé par la politique de flux → gabarit |
| `REPLI_VALIDATION` | réponses invalides → gabarit |
| `REPLI_FOURNISSEUR` | erreur d'appel (réseau, clé, modèle) → gabarit ; la raison est dans le journal |

## 4. Protection des données

Les consignes interdisent de transmettre des données confidentielles vers un
service externe non autorisé. CorroborAI applique un **contrôle de flux
d'information** déclaratif (`config/llm.yaml`), évalué avant chaque envoi :

- **Pseudonymisation** — matricules, noms, prénoms, courriels et identifiants
  numériques longs sont remplacés par des jetons (`EMP-03`, `[courriel]`,
  `[identifiant]`) ; la réidentification n'a lieu que localement, après
  validation. La consigne tapée par un expert est pseudonymisée de la même façon.
- **Classes de données** — chaque élément du dossier porte une classe
  (identifiant pseudonymisé, valeur métier, texte de règle, preuve, méta).
  Un élément **non classifié bloque l'envoi** : la politique est fermée par défaut.
- **Balayage anti-fuite** — vers un fournisseur externe, la charge sérialisée
  est balayée : tout identifiant brut connu ou motif d'identifiant bloque
  l'appel. Les valeurs détectées ne sont jamais journalisées.
- **Catégorie effective** — un fournisseur déclaré « local » dont l'adresse
  n'est pas une adresse de bouclage est traité comme externe.

Les deux protections (pseudonymisation et balayage) sont indépendantes : un
test montre que si la pseudonymisation des courriels est désactivée, le
balayage bloque l'envoi.

Cette approche transpose à un appel sans outil le principe des politiques de
flux pour agents (voir [OpenAPPA](https://github.com/archestra-ai/OpenAPPA)) :
une décision de flux déclarée tient à chaque exécution, là où un détecteur
probabiliste peut se tromper.

## 5. Fournisseurs

| Fournisseur | Type | Configuration |
|---|---|---|
| `gabarit` | aucun LLM (défaut) | — |
| `gemini` | API Gemini (`generateContent`), externe | `GEMINI_API_KEY` ; modèle dans `llm.yaml` |
| `local` | API compatible OpenAI (Ollama, LM Studio, llama.cpp, vLLM) | `base_url` et modèle dans `llm.yaml` |

Tout service compatible OpenAI s'ajoute dans `llm.yaml` sans code, par exemple :

```yaml
  openai:
    type: openai_compat
    data_class: externe
    model: gpt-4o-mini
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
```

**Vérifier qu'un fournisseur fonctionne :** exécuter sans `--cache-ia` et lire
la ligne « Couche IA » de la console (`LLM_VALIDE=…` attendu) ; en cas de
`REPLI_FOURNISSEUR` ou `REPLI_VALIDATION`, `out/audit_ia.jsonl` donne la raison
exacte (erreur HTTP, modèle introuvable, phrase rejetée par le validateur).

## 6. Ce que l'IA ne fait pas

- Elle ne fixe, ne modifie ni ne priorise aucun verdict : les verdicts
  viennent des règles ou de l'expert ; la priorité est calculée.
- Elle n'a accès à aucun outil, fichier ou réseau.
- Elle n'applique aucune règle : une règle proposée passe par l'aperçu et
  l'acceptation explicite de l'expert.
- Sans LLM, toutes les fonctionnalités restent disponibles ; seuls les textes
  sont produits par gabarit.
