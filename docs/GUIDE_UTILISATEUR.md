# Guide d'utilisation

Ce guide s'adresse à la personne qui investigue les écarts : comment lire un
verdict, par où commencer, et comment enrichir les règles par la rétroaction.

## Lancer une corroboration

**Interface web** — `corroborai app`, puis dans le panneau de gauche :
indiquer le répertoire des données (ou téléverser les fichiers), cliquer
« Lancer la corroboration ». Les options facultatives (interprétations,
fichier de rétroaction, couche IA) sont dans le même panneau.

**Ligne de commande** — `corroborai run --data-dir data --out out`, puis
ouvrir `out/rapport_corroboration.xlsx`.

## Les quatre verdicts

| Verdict | Signification | Action |
|---|---|---|
| **CONFORME** | la valeur cible est celle de la source (à un format près) | aucune |
| **JUSTIFIE** | la valeur cible diffère de la source, mais c'est exactement ce que la règle métier prévoit (transcodage, concaténation, jointure…) | aucune |
| **ANOMALIE** | la valeur cible diffère de ce que la règle prévoit | **à investiguer** |
| **INDETERMINE** | la règle ne permet pas de conclure (code absent de la table, combinaison non prévue, valeur illisible) | à examiner : souvent une règle à compléter |

## Par où commencer

1. **Synthèse** — les décomptes, l'état des fichiers et les priorités les
   plus élevées donnent la vue d'ensemble.
2. **Motifs** — avant d'examiner les écarts un par un, repérer ceux qui
   relèvent d'une **cause unique** : un écart systémique (ex. 22 courriels sur
   22) ou un recodage stable se corrige une seule fois, pour tout le champ.
3. **À investiguer** — les écarts sont triés par priorité décroissante. Les
   erreurs isolées et certaines (permutation entre deux employés, affectation
   absente) sont en tête ; les artefacts systémiques en fin de liste.
4. **Règles candidates** — si une colonne explique les écarts d'un champ sur
   toute la population, il s'agit probablement d'un écart de mapping plutôt
   que d'erreurs de données : à valider avec l'équipe fonctionnelle.

## Lire un écart

Exemple réel, tiré des données du défi :

| Colonne | Valeur |
|---|---|
| Priorité | 70 |
| Employé · Affectation · Champ | 4402456 · P:26586 · `weeklyHoursOverride` |
| Valeur source · attendue · cible | 36 · 36 · 40 |
| Justification | Valeur attendue « 36 » selon la règle (report direct de « HeuresNormeHebdo »), valeur cible « 40 ». |
| Cause probable | Valeur provenant d'une autre colonne : valeur cible « 40 » = détail du poste (enregistrement le plus récent).HeuresSemaineContrat ; cette correspondance vaut pour 100 % des 22 enregistrements du champ (règle actuelle : 82 %). |
| Calcul de la priorité | criticité 5/5 × confiance ELEVEE (1) × ANOMALIE (1) × H-ALT-SOURCE (0.7) = 70 |
| Règle · Réf. Mapping | `R-DIRECT` · `Mapping!51` |
| Preuves | `src:17@source:17` · `tgt:16@target:16` · `poste:80@poste_detail:80` |

Comment le lire :

- **Justification** — ce que la règle attendait et ce qui a été reçu.
- **Cause probable** — l'explication la plus spécifique trouvée par le moteur
  d'hypothèses. Ici : le système Temps semble prendre les heures du contrat du
  poste plutôt que celles de l'employé. C'est une piste, pas un verdict.
- **Preuves** — chaque preuve indique une table et un numéro de ligne
  d'origine (`poste:80@poste_detail:80` = ligne 80 du fichier du détail du
  poste). Dans l'interface, elles s'affichent en entier sous « Données ayant
  servi à la décision ».
- **Réf. Mapping** — la ligne de `Mapping.xlsx` d'où vient la règle.
- **Confiance** — `ELEVEE` : règle appliquée sans hypothèse d'interprétation ;
  `MOYENNE` : le verdict changerait sous une interprétation alternative (voir
  la colonne « Paramètres ») ; `FAIBLE` : valeur illisible ou règle non
  applicable.
- **Explication** — si la couche IA est active, un texte rédigé ; sa source
  (`LLM` ou `GABARIT`) est indiquée. Une piste IA sur une anomalie inexpliquée
  est toujours marquée « non vérifiée ».

## Interprétations

Certaines règles du mapping sont ambiguës (ex. « date la plus ancienne » pour
le début d'affectation). Chaque ambiguïté est tranchée par une interprétation
documentée (voir [Règles](REGLES.md)). La feuille **Interprétations** montre,
pour chaque champ, quelle part des enregistrements s'accorde avec la cible sous
chaque lecture. Pour essayer une autre lecture : dans l'interface, panneau
« Interprétations des règles » ; en ligne de commande,
`--interpretation INT-ASSIGN-DATES=strict_literal`.

## Rétroaction experte

### Corriger un verdict

Onglet **À investiguer** → sélectionner l'écart → « Corriger ce verdict » :
choisir le verdict, saisir une justification (obligatoire), enregistrer.
La correction est réappliquée à chaque exécution tant que les valeurs jugées
ne changent pas ; si elles changent, elle est signalée **périmée** et n'est
plus appliquée — une décision ne survit pas à des données qu'elle n'a pas vues.

### Créer une règle

Une règle généralise une décision à tous les écarts concernés. Elle ne peut
que reconnaître des écarts comme acceptables (ANOMALIE → JUSTIFIE) ; elle ne
touche jamais un verdict conforme.

| Opération | Exemple |
|---|---|
| `accept_alternative_source` | « les heures hebdomadaires viennent du contrat du poste » |
| `accept_value_mapping` | « 6203-Empl6203 est recodé en 4367-Empl4367 » (une paire par ligne : `attendu => cible`) |
| `accept_hypothesis` | « les écarts de positionName expliqués par un recodage systématique sont acceptés » |
| `accept_subcategory` | « les courriels à identifiant incohérent sont acceptés » |

Quatre façons de créer une règle, toutes suivies d'un **aperçu** (quels
verdicts changeraient) avant acceptation :

- **Motifs et règles candidates** → « Prévisualiser une règle experte » ;
- **Rétroaction experte → Formulaire** ;
- **Règles suggérées** — proposées automatiquement quand au moins deux de vos
  corrections acceptent des écarts semblables ;
- **Consigne en langage naturel** — décrite en français, traduite par le LLM en
  règle ; la reformulation permet de vérifier qu'elle correspond à l'intention.

### Le fichier de rétroaction

Tout est enregistré dans `feedback/retroaction.yaml`, créé à la première
décision. Le fichier est lisible et peut être versionné ou partagé ; la
section « Rétroaction enregistrée » de l'interface permet d'en retirer un
élément. En ligne de commande : `--retroaction feedback/retroaction.yaml`.
Le rapport (feuille **Rétroaction experte**) liste chaque règle et correction
avec le nombre d'écarts touchés. Les verdicts modifiés portent la décision
`EXPERT` et conservent leur verdict initial.

## Questions fréquentes

**Pourquoi un écart est-il INDETERMINE ?** La justification l'indique : code
de situation absent de la table, motif introuvable, combinaison de type
d'employé non prévue, ou valeur illisible. C'est en général le signe qu'une
règle du mapping est incomplète.

**Le rapport indique « DIFFERENT_DU_MANIFESTE ».** Les fichiers ne sont pas
ceux décrits par `manifest.json` (ex. un jeu plus volumineux). C'est
informatif : la corroboration est faite normalement. La garantie importante —
les fichiers n'ont pas été modifiés par le traitement — est vérifiée
séparément (« Fichiers sources après traitement : inchangés »).

**« Fichier de rétroaction introuvable ».** Aucune décision n'a encore été
enregistrée à ce chemin, ou le chemin est mal saisi. Un exemple est fourni :
`feedback/exemple_retroaction.yaml`.

**La synthèse IA vient du gabarit alors que Gemini est choisi.** Le statut du
harnais en donne la raison (`REPLI_FOURNISSEUR` : clé, réseau ou modèle ;
`REPLI_VALIDATION` : réponse rejetée ; `BLOQUE_POLITIQUE` : envoi refusé par la
politique de flux). Le détail est dans `out/audit_ia.jsonl`.

**Pendant une séance de corrections, l'interface est lente.** Chaque décision
relance la corroboration ; si la couche IA est active avec un fournisseur
externe, les synthèses sont aussi regénérées. Désactiver l'IA pendant les
corrections, puis la réactiver pour le rapport final.
