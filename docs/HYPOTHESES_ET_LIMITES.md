# Hypothèses et limites

## Choix d'interprétation

Le texte de certaines règles du mapping est ambigu. Chaque ambiguïté est
tranchée par une interprétation nommée, justifiée dans `config/rules.yaml` et
dans [Règles](REGLES.md), et paramétrable (`--interpretation`). Le rapport
publie, pour chaque champ, le taux d'accord avec la cible sous chaque lecture.

| Interprétation | Choix retenu | Raison principale |
|---|---|---|
| `INT-ASSIGN-DATES` | intersection d'intervalles : début = MAX(entrée, début de l'unité) ; fin = MIN(sortie, fin de l'unité) | la lecture littérale (« date la plus ancienne ») renvoie la date de création du poste et contredit la règle de fin ; accord avec la cible : 86 % contre 0 % sur les données du défi |
| `INT-EMAIL` | courriel décomposé : préfixe d'environnement et casse tolérés, identifiant vérifié | « code » = matricule ; un identifiant incohérent reste une anomalie (consigne des organisatrices) |
| `INT-NAME-ACCENTS` | une différence d'accents seule sur le nom ou le prénom est justifiée | la position de la règle « enlever les accents » est ambiguë |
| `INT-CONCAT-PADDING` | code complété de zéros (5 positions pour l'unité, 4 pour l'emploi) | format observé uniformément dans la cible |
| `INT-SITUATION-CODE` | code de traitement des accès lu dans `CodeSuspensionAccès`, contrôlé contre `CodeStatutEmploi` et le motif | désignation explicite dans la feuille de jointure |

Les interprétations de repli (`INT-SITUATION-UNLISTED`, `INT-MOTIF-MISSING`,
`INT-CONTRACT-UNMATCHED`) donnent un verdict `INDETERMINE` lorsqu'une règle ne
couvre pas un cas, plutôt que de deviner.

## Hypothèses sur les données

- **Correspondance des colonnes de jointure.** Les extractions n'utilisent pas
  les libellés de `Mapping.xlsx` ; la correspondance (ex. « Code de la situation
  d'emploi de Remphor » ↔ `CodeStatutSystèmeExterne`) est déduite des feuilles de
  jointure et vérifiée empiriquement sur les données du défi.
- **Identification des affectations.** Un employé est identifié par
  `Matricule` ↔ `personId` ; le type d'affectation par `TypeAffectation` ↔
  indicateurs primaire / temporaire.
- **Unité administrative courante.** Celle de la source (`CodeDirection`) ; si
  l'historique du poste ne la contient pas, la confiance du verdict de date est
  abaissée et la raison indiquée.
- **Formats.** Dates : cellules Excel, ISO (`2003-12-12`, avec ou sans heure,
  `…T00:00:00.000Z`), `JJ/MM/AAAA`, numéros de série Excel pour les dates
  d'effet du détail du poste. Booléens : Oui/Non, true/false, 1/0. Toute autre
  forme donne un verdict `INDETERMINE` (« valeur illisible ») plutôt qu'une
  conversion hasardeuse.
- **Champs hors mapping.** Seuls les champs du mapping sont corroborés
  (consignes) : `termStartDate`, `siteId` et `activityStatus` ne le sont pas.

## Limites connues

**Règles et mapping**

- Les règles sont codifiées à la main dans `config/rules.yaml` à partir de
  `Mapping.xlsx`, dont le texte libre ne se prête pas à une lecture
  automatique fiable. Une évolution du mapping demande une mise à jour de
  `rules.yaml` ; la validation croisée signale les références qui ne
  correspondent plus (champ absent, ligne vide, champ non couvert).
- La criticité des champs (1 à 5) et les modificateurs de priorité sont des
  choix d'expertise, justifiés champ par champ dans la configuration, mais
  subjectifs : à ajuster avec l'équipe fonctionnelle.

**Analyse des écarts**

- Les seuils du moteur d'hypothèses (écart systémique : 80 % et au moins 5
  enregistrements ; source alternative : 90 % sur au moins 5 enregistrements)
  ont été fixés avec un échantillon de 20 employés. Sur une petite population,
  une source alternative ne peut pas être confirmée ; sur une très grande, une
  concordance de 90 % peut masquer des exceptions — l'aperçu d'une règle
  candidate les montre avant acceptation.
- Une hypothèse est une explication plausible, pas une preuve de cause : la
  « cause probable » oriente l'investigation sans la remplacer.
- L'appariement explore toutes les combinaisons jusqu'à 7 affectations de
  même type par employé, puis passe à une méthode gloutonne.

**Rétroaction experte**

- Une règle experte ne peut que reconnaître des écarts comme acceptables ;
  déclarer une anomalie se fait par correction, écart par écart.
- Une correction est rattachée à l'identifiant de l'écart (employé, type
  d'affectation, poste, champ) : si le poste change, elle devient
  « introuvable » et doit être refaite.

**Couche LLM**

- Le contrôle d'ancrage est volontairement strict : une réponse correcte qui
  cite un nombre absent tel quel du dossier est rejetée, et le texte vient
  alors du gabarit. Le journal d'audit montre chaque rejet et sa cause.
- L'ancrage vérifie que les valeurs citées existent, pas que le raisonnement
  est juste : une synthèse peut mal relier des faits exacts. C'est pourquoi
  les textes sont signalés par leur source et ne pèsent ni sur les verdicts ni
  sur les priorités.
- La qualité dépend du modèle ; un petit modèle local sur processeur est lent
  et sa rédaction plus sommaire. Les noms de modèles évoluent : celui de
  `llm.yaml` peut devoir être mis à jour.

**Interface et performance**

- L'interface est testée de bout en bout contre un module Streamlit simulé et
  avec l'outil officiel `AppTest` ; le rendu visuel dépend de la version de
  Streamlit installée.
- Mesure sur 1 000 employés (données du défi répliquées, 27 550 verdicts) :
  chargement 0,5 s, corroboration et analyse 5 s, écriture du rapport Excel
  environ 20 s (le rapport contient alors près d'un million de cellules). Le
  traitement est essentiellement linéaire. La couche LLM fait un appel par
  employé concerné (anomalie non systémique de priorité suffisante) : sur un
  grand volume avec un fournisseur externe, prévoir le temps et le coût
  correspondants, ou relever `synthesis_min_priority` dans `llm.yaml`.
