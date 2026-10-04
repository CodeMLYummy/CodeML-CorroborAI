# Règles de corroboration

> Document généré automatiquement depuis `config/rules.yaml` (`corroborai rules --markdown`). Ne pas modifier à la main.

## Champs corroborés

| Champ cible | Colonnes source | Règle | Interprétation | Criticité | Réf. Mapping.xlsx |
|---|---|---|---|:-:|---|
| `contractTypeCode` | CatégorieEmploi, EstPermanent, EstTempsPlein | `R-CONTRACT` (contract_type) | INT-CONTRACT-UNMATCHED | 5 | Mapping!24, Mapping!26, Mapping!39, Mapping!40 |
| `dailyHoursOverride` | HeuresNormeQuotidienne | `R-DIRECT` (direct) | — | 5 | Mapping!52 |
| `detailedStatus` | CodeSuspensionAccès | `R-SITUATION-STATUS` (situation_label) | INT-SITUATION-CODE | 5 | Mapping!23, Règles situation d'emploi!2, Règles situation d'emploi!3 |
| `personId` | Matricule | `R-DIRECT` (direct) | — | 5 | Mapping!8 |
| `weeklyHoursOverride` | HeuresNormeHebdo | `R-DIRECT` (direct) | — | 5 | Mapping!51 |
| `assignmentEndDate` | DateSortiePoste, CodePoste, CodeDirection | `R-ASSIGN-END` (assignment_end) | INT-ASSIGN-DATES | 4 | Mapping!53, Jointure - Détail du poste!2 |
| `assignmentStartDate` | DateEntréePoste, CodePoste, CodeDirection | `R-ASSIGN-START` (assignment_start) | INT-ASSIGN-DATES | 4 | Mapping!46, Mapping!48, Jointure - Détail du poste!2 |
| `divisionCode` | CodeImputation | `R-DIRECT` (direct) | — | 4 | Mapping!13 |
| `divisionId` | CodeDirection | `R-DIRECT` (direct) | — | 4 | Mapping!11 |
| `expectedReturnDate` | CodeSuspensionAccès, DateRetourAnticipée | `R-SITUATION-CADP` (situation_return_date) | INT-SITUATION-CODE | 4 | Mapping!22, Règles situation d'emploi!3 |
| `isPrimaryAssignment` | TypeAffectation | `R-ASSIGN-TYPE` (assignment_flag) | — | 4 | Mapping!41, Mapping!45 |
| `isTemporaryAssignment` | TypeAffectation | `R-ASSIGN-TYPE` (assignment_flag) | — | 4 | Mapping!41, Mapping!43, Mapping!45 |
| `onboardDate` | DateEmbaucheRécente | `R-DIRECT` (direct) | — | 4 | Mapping!7 |
| `payGradeId` | ÉchelleSalariale | `R-DIRECT` (direct) | — | 4 | Mapping!49 |
| `positionCode` | CodeEmploi | `R-DIRECT` (direct) | — | 4 | Mapping!16 |
| `positionId` | CodeEmploi | `R-DIRECT` (direct) | — | 4 | Mapping!14 |
| `siteCode` | CodeSite | `R-DIRECT` (direct) | — | 4 | Mapping!10 |
| `statusReasonCode` | CodeSuspensionAccès, CodeRaisonStatut, CodeStatutEmploi | `R-SITUATION-CAD` (situation_reason_code) | INT-SITUATION-CODE | 4 | Mapping!17, Mapping!19, Règles situation d'emploi!3, Jointure - Motif des situations!2 |
| `contactEmail` | PrénomUsuel, NomFamille, Matricule | `R-EMAIL` (email) | INT-EMAIL | 3 | Mapping!4, Mapping!6 |
| `termEndDate` | DateSortiePoste, CodePoste, CodeDirection | `R-ASSIGN-END` (assignment_end) | INT-ASSIGN-DATES | 3 | Mapping!56, Mapping!58 |
| `divisionName` | CodeDirection, LibelléDirection | `R-CONCAT` (concat_code_label) | INT-CONCAT-PADDING | 2 | Mapping!12 |
| `givenName` | PrénomUsuel | `R-NAME` (direct_accent_tolerant) | INT-NAME-ACCENTS | 2 | Mapping!2 |
| `positionName` | CodeEmploi, IntituléEmploi | `R-CONCAT` (concat_code_label) | INT-CONCAT-PADDING | 2 | Mapping!15 |
| `siteName` | LibelléSite | `R-DIRECT` (direct) | — | 2 | Mapping!9 |
| `surname` | NomFamille | `R-NAME` (direct_accent_tolerant) | INT-NAME-ACCENTS | 2 | Mapping!3 |

### Justification des criticités

- **`contractTypeCode`** (5) : Détermine les règles de temps, d'horaire et de rémunération applicables.
- **`dailyHoursOverride`** (5) : Heures normales ; impact direct sur la paie et les horaires.
- **`detailedStatus`** (5) : Un employé absent planifié, ou actif bloqué, affecte directement les horaires.
- **`personId`** (5) : Clé d'identité de l'employé.
- **`weeklyHoursOverride`** (5) : Heures normales ; impact direct sur la paie et les horaires.
- **`assignmentEndDate`** (4) : Fin de validité de l'affectation (horaires et imputation).
- **`assignmentStartDate`** (4) : Début de validité de l'affectation (horaires et imputation).
- **`divisionCode`** (4) : Centre de coûts d'imputation des heures.
- **`divisionId`** (4) : Rattachement organisationnel (approbations, rapports).
- **`expectedReturnDate`** (4) : Date de reprise de la planification de l'employé.
- **`isPrimaryAssignment`** (4) : Affectation de référence pour la planification.
- **`isTemporaryAssignment`** (4) : Affectation temporaire (bornée dans le temps).
- **`onboardDate`** (4) : Ancienneté et admissibilité (vacances, primes).
- **`payGradeId`** (4) : Groupe de rémunération (taux applicables).
- **`positionCode`** (4) : Emploi occupé (règles de temps et d'horaire associées).
- **`positionId`** (4) : Emploi occupé (règles de temps et d'horaire associées).
- **`siteCode`** (4) : Lieu de travail utilisé pour la planification des horaires.
- **`statusReasonCode`** (4) : Motif d'absence longue durée (traitement des absences).
- **`contactEmail`** (3) : Accès et notifications dans le système Temps.
- **`termEndDate`** (3) : Fin du détail du poste ; redondant avec assignmentEndDate.
- **`divisionName`** (2) : Libellé dérivé ; le code porte l'information.
- **`givenName`** (2) : Identification visuelle, sans impact sur le temps.
- **`positionName`** (2) : Libellé dérivé ; le code porte l'information.
- **`siteName`** (2) : Libellé ; le code de site porte l'information opérationnelle.
- **`surname`** (2) : Identification visuelle, sans impact sur le temps.

## Interprétations retenues

### INT-ASSIGN-DATES — `interval_intersection`

Les règles de début et de fin décrivent l'intersection de deux intervalles : [entrée, sortie du poste] ∩ [début, fin de l'unité administrative courante]. Début = MAX des deux débuts ; fin = MIN des deux fins.

*Justification.* Le texte dit « date la plus ancienne » pour le début. Appliqué littéralement, il retourne la date de création du poste (historique du poste, pas de l'employé) et met la quasi-totalité de la cible en écart. La règle de fin prend la plus ancienne des fins, ce qui n'est cohérent qu'avec une intersection. L'interprétation littérale reste disponible (strict_literal) et le rapport publie le taux d'accord des deux.

*Alternatives :* `strict_literal`

### INT-EMAIL — `decomposed`

Courriel attendu = minuscule(sans_accents(1re lettre du prénom + nom)) + 3 derniers chiffres du matricule + "@loto-quebec.com". Comparaison par composantes : préfixe, initiale, nom, chiffres, domaine.

*Justification.* « Code » est interprété comme le matricule (« Code de l'employé » ↔ Matricule dans Mapping.xlsx). La casse n'est pas significative dans une adresse courriel. Un préfixe d'environnement (ex. « dev-08-v2_ ») seul est un écart justifié ; un identifiant incohérent avec le matricule est une anomalie (confirmé par les organisatrices : artefact d'anonymisation à signaler).

*Alternatives :* `strict`

### INT-NAME-ACCENTS — `accent_tolerant`

La règle « Enlever les accents pour le nom et prénom » s'applique au courriel. Pour givenName/surname, une différence uniquement d'accents est un écart justifié (confiance moyenne) ; toute autre différence est une anomalie.

*Justification.* La position de la règle (sous le courriel) est ambiguë. Cette interprétation couvre les deux lectures sans masquer de vraie différence.

*Alternatives :* `strict`

### INT-CONCAT-PADDING — `zero_padded`

Concaténations « code-libellé » : le code est complété de zéros à gauche (5 positions pour l'unité administrative, 4 pour l'emploi).

*Justification.* Format observé uniformément dans la cible (ex. « 00397-UnitAdmin00397 »). Le texte de la règle ne précise pas le format du code.

*Alternatives :* `unpadded`

### INT-SITUATION-CODE — `code_suspension_acces`

Le « Code de traitement des accès » de la table de situation d'emploi est lu dans CodeSuspensionAccès. Les codes sont comparés numériquement (« 00 » ≡ « 0 »). CodeStatutEmploi et le CodeGestionAccès du motif sont vérifiés en contrôle croisé ; une incohérence est ajoutée comme preuve.

*Justification.* La feuille « Jointure - Motif des situations » identifie explicitement CodeSuspensionAccès comme le code de traitement des accès, qui est la clé de la feuille « Règles situation d'emploi ».

*Alternatives :* `code_statut_emploi`, `motif_code_gestion_acces`

### INT-SITUATION-UNLISTED — `indetermine`

Un code de situation absent de la table (ex. une cessation) ne permet pas de calculer de valeur attendue : verdict INDETERMINE.

*Justification.* Mapping.xlsx mentionne « cessation » mais ne fournit pas sa règle.

*Alternatives :* aucune

### INT-MOTIF-MISSING — `indetermine`

Absence (codes 02, 03, 06, 07) dont le motif est introuvable dans la table des motifs : statusReasonCode attendu inconnu, verdict INDETERMINE.

*Justification.* La table des motifs ne couvre pas tous les codes (ex. 697, 703, 651) ; ce n'est pas bloquant pour les actifs, qui n'exigent pas de motif.

*Alternatives :* aucune

### INT-CONTRACT-UNMATCHED — `indetermine`

Combinaison catégorie / permanent / temps plein non couverte par le tableau de transcodage : verdict INDETERMINE.

*Justification.* Ex. CatégorieEmploi = V avec EstPermanent = Non n'a pas de règle.

*Alternatives :* aucune

### INT-EMPTY — `empty_equals_empty`

Deux valeurs vides (après normalisation) sont conformes ; une seule valeur vide est un écart.

*Justification.* Les vides ont des représentations variées (NaN, "", NaT).

*Alternatives :* aucune

## Transcodage du type d'employé

| Conditions | Code cible | Libellé |
|---|---|---|
| CatégorieEmploi = V et EstPermanent = Oui et EstTempsPlein = Oui | `JWN` | Permanent temps plein |
| CatégorieEmploi = V et EstPermanent = Oui et EstTempsPlein = Non | `XFLR` | Permanent temps partiel |
| CatégorieEmploi = T | `KELH` | Surnuméraire avec vacances |
| CatégorieEmploi = O | `WHX` | Occasionnel - Surnuméraire |
| CatégorieEmploi = M | `CEGQ` | Occasionnel avec avantages |
| CatégorieEmploi = R | `CNZC` | Surnuméraire avec avantages |
| CatégorieEmploi = J | `RMQ` | Saisonnier |
| CatégorieEmploi = Z | `JAW` | Aspirant croupier |
| CatégorieEmploi = Q | `TRSY` | Stagiaire |

## Situation d'emploi

| Codes de traitement des accès | detailedStatus | statusReasonCode | expectedReturnDate |
|---|---|---|---|
| 00, 01 | Actif | vide | vide |
| 02, 03, 06, 07 | Absence complète | code Remphor du motif | DateRetourAnticipée |

## Correspondance des colonnes de jointure

| Jointure | Libellé Mapping.xlsx | Colonne de la table | Colonne source |
|---|---|---|---|
| poste_detail | Numéro du poste | `IdentifiantPoste` | `CodePoste` |
| poste_detail | Numéro de l'emploi | `IdentifiantEmploi` | `CodeEmploi` |
| poste_detail | Code d'unité administrative | `CodeDirectionAffectée` | `CodeDirection` |
| poste_detail | Date d'effet | `DateEffetAffectation` | — |
| motifs | Code de la situation d'emploi | `CodeCatégorieStatut` | `CodeRaisonStatut` |
| motifs | Code de la situation d'emploi de Remphor | `CodeStatutSystèmeExterne` | — |
| motifs | Code de traitement des accès | `CodeGestionAccès` | `CodeSuspensionAccès` |

## Appariement des affectations

Par employé, puis par type d'affectation ; à type égal, affectation optimale sur la similarité des champs ci-dessous (jamais sur l'ordre des lignes). Les restes d'un même employé sont appariés entre types différents si la similarité atteint 60% ; sinon l'affectation est déclarée absente (`R-MATCH`, criticité 5 si absente de la cible, 4 si absente de la source).

| Colonne source | Champ cible | Type |
|---|---|---|
| `CodeEmploi` | `positionId` | code |
| `CodeDirection` | `divisionId` | code |
| `CodeImputation` | `divisionCode` | code |
| `CodeSite` | `siteCode` | code |
| `ÉchelleSalariale` | `payGradeId` | code |
| `DateEntréePoste` | `assignmentStartDate` | date |
| `HeuresNormeHebdo` | `weeklyHoursOverride` | number |

## Lignes du mapping non corroborées

- `LibelléImputation` (Mapping!50) : Aucun champ cible (« - » dans Mapping.xlsx).
- `DateEffetRaison` (Mapping!21) : Aucun champ cible ni règle associée.

Colonnes cibles hors mapping (non corroborées) :

- `termStartDate` : Non mappé ; identique à assignmentStartDate dans l'extraction.
- `siteId` : Non mappé ; identique à siteCode dans l'extraction.
- `activityStatus` : Non mappé ; la situation est corroborée via detailedStatus.

## Moteur d'hypothèses (analyse déterministe des écarts)

Pour chaque verdict ANOMALIE ou INDETERMINE, un catalogue fermé d'hypothèses génériques est testé (`config/hypotheses.yaml`). Une hypothèse vérifiée ajoute des preuves, une cause probable et un modificateur de priorité ; elle ne modifie jamais le verdict.

| Ordre | Hypothèse | Libellé | Modificateur de priorité | Justification du modificateur |
|:-:|---|---|:-:|---|
| 1 | `H-PERMUTATION` | Permutation entre deux employés | ×1.2 | Erreur isolée et certaine, la contrepartie est identifiée. |
| 2 | `H-HISTORY-RECORD` | Valeur d'un autre enregistrement de l'historique du poste | ×1 | Erreur de calcul localisée ; le motif peut être récurrent. |
| 3 | `H-ALT-SOURCE` | Valeur provenant d'une autre colonne | ×0.7 | Écart de mapping probable plutôt qu'erreur de donnée ; à valider une fois pour le champ. |
| 4 | `H-ALT-INTERPRETATION` | Conforme sous une interprétation alternative | ×0.6 | Dépend de la lecture de la règle ; à trancher avec l'équipe fonctionnelle. |
| 5 | `H-BIJECTION` | Transformation systématique cohérente | ×0.8 | Recodage systématique ; une seule cause pour tous les enregistrements. |
| 6 | `H-SYSTEMIC` | Écart systémique | ×0.3 | Cause globale ; à traiter une fois pour le champ, pas enregistrement par enregistrement. |
| 7 | `H-TYPE-ABSENT` | Type d'affectation absent du système cible | ×1 | Type d'affectation non transmis ; impact direct sur la planification. |

### Description des hypothèses

- **`H-PERMUTATION`** — La valeur cible de l'employé A est la valeur attendue de l'employé B, et réciproquement, pour le même champ. Typique d'une inversion d'enregistrements lors du chargement.
- **`H-HISTORY-RECORD`** — La date cible correspond à la date d'effet d'un autre enregistrement de l'historique du détail du poste (ex. le plus récent) : la règle de calcul a probablement été appliquée sur le mauvais enregistrement.
- **`H-ALT-SOURCE`** — La valeur cible est égale à une autre colonne (source, détail du poste, motif) que celle prévue au mapping, et cette correspondance est confirmée sur l'ensemble de la population du champ.
- **`H-ALT-INTERPRETATION`** — La valeur cible est conforme si la règle est lue selon une interprétation alternative documentée.
- **`H-BIJECTION`** — Les écarts du champ suivent une correspondance un-pour-un stable entre valeur attendue et valeur cible sur toute la population (même valeur attendue → même valeur cible). Typique d'un recodage ou d'une anonymisation appliqués à un seul des deux systèmes.
- **`H-SYSTEMIC`** — La grande majorité des enregistrements présentent le même type d'écart pour ce champ : cause globale probable (chargement, environnement, anonymisation) plutôt qu'erreur de saisie individuelle.
- **`H-TYPE-ABSENT`** — Aucune affectation de ce type n'existe dans le système cible pour aucun employé : le type n'est probablement pas transmis par l'interface.

### Seuils

- Écart systémique : au moins 5 enregistrements et 80% du champ.
- Bijection : au moins 5 écarts, correspondance un-pour-un, et une valeur attendue partagée par des employés différents (sinon la bijection est triviale).
- Source alternative : concordance sur au moins 90% d'une population d'au moins 5 enregistrements, supérieure à celle de la règle actuelle, et au moins une anomalie expliquée. La colonne devient une règle candidate.

## Score de priorité

priorité = 100 × (criticité / 5) × poids de confiance × base du verdict × produit des modificateurs, plafonnée à 100.

| Confiance | Poids |
|---|:-:|
| ELEVEE | 1 |
| MOYENNE | 0.7 |
| FAIBLE | 0.4 |

| Verdict | Base |
|---|:-:|
| ANOMALIE | 1 |
| INDETERMINE | 0.6 |
