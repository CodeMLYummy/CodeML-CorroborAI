"""Génération de la documentation des règles à partir de ``rules.yaml``.

La documentation est produite depuis la configuration exécutée par le moteur :
elle ne peut pas diverger de ce qui est réellement appliqué.
"""

from __future__ import annotations

from corroborai.rules_config import RulesConfig

from corroborai.analysis.config import HypothesesConfig, ScoringConfig


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def rules_markdown(cfg: RulesConfig) -> str:
    out: list[str] = [
        "# Règles de corroboration",
        "",
        "> Document généré automatiquement depuis `config/rules.yaml` "
        "(`corroborai rules --markdown`). Ne pas modifier à la main.",
        "",
        "## Champs corroborés",
        "",
        "| Champ cible | Colonnes source | Règle | Interprétation | Criticité | Réf. Mapping.xlsx |",
        "|---|---|---|---|:-:|---|",
    ]
    for f in sorted(cfg.fields, key=lambda f: (-f.criticality, f.target)):
        refs = ", ".join([str(f.mapping_ref)] + [str(r) for r in f.related_refs])
        out.append(f"| `{f.target}` | {', '.join(f.sources)} | `{f.rule_id}` ({f.rule}) | "
                   f"{f.interpretation or '—'} | {f.criticality} | {_cell(refs)} |")

    out += ["", "### Justification des criticités", ""]
    for f in sorted(cfg.fields, key=lambda f: (-f.criticality, f.target)):
        out.append(f"- **`{f.target}`** ({f.criticality}) : {_cell(f.criticality_reason)}")

    out += ["", "## Interprétations retenues", ""]
    for i in cfg.interpretations.values():
        alts = ", ".join(f"`{a}`" for a in i.alternatives) or "aucune"
        out += [f"### {i.id} — `{i.choice}`", "", _cell(i.summary), "",
                f"*Justification.* {_cell(i.rationale)}", "", f"*Alternatives :* {alts}", ""]

    out += ["## Transcodage du type d'employé", "", "| Conditions | Code cible | Libellé |", "|---|---|---|"]
    for ct in cfg.contract_types:
        cond = " et ".join(f"{k} = {'Oui' if v is True else 'Non' if v is False else v}"
                           for k, v in ct.when.items())
        out.append(f"| {cond} | `{ct.code}` | {ct.label} |")

    out += ["", "## Situation d'emploi", "",
            "| Codes de traitement des accès | detailedStatus | statusReasonCode | expectedReturnDate |",
            "|---|---|---|---|"]
    labels = {"none": "vide", "motif_remphor": "code Remphor du motif", "source": "DateRetourAnticipée"}
    for s in cfg.situations:
        codes = ", ".join(f"{c:02d}" for c in sorted(s.codes))
        out.append(f"| {codes} | {s.label} | {labels[s.reason_code]} | {labels[s.return_date]} |")

    out += ["", "## Correspondance des colonnes de jointure", "",
            "| Jointure | Libellé Mapping.xlsx | Colonne de la table | Colonne source |", "|---|---|---|---|"]
    for j in cfg.joins.values():
        for c in j.columns.values():
            out.append(f"| {j.name} | {c.label} | `{c.table}` | {f'`{c.source}`' if c.source else '—'} |")

    m = cfg.matching
    out += ["", "## Appariement des affectations", "",
            "Par employé, puis par type d'affectation ; à type égal, affectation optimale sur la "
            "similarité des champs ci-dessous (jamais sur l'ordre des lignes). Les restes d'un même "
            f"employé sont appariés entre types différents si la similarité atteint "
            f"{m.cross_type_min_score:.0%} ; sinon l'affectation est déclarée absente "
            f"(`{m.missing_in_target.rule_id}`, criticité {m.missing_in_target.criticality} si absente "
            f"de la cible, {m.missing_in_source.criticality} si absente de la source).", "",
            "| Colonne source | Champ cible | Type |", "|---|---|---|"]
    for sf in m.similarity_fields:
        out.append(f"| `{sf.source}` | `{sf.target}` | {sf.kind.value} |")

    out += ["", "## Lignes du mapping non corroborées", ""]
    for e in cfg.excluded:
        out.append(f"- `{e.source}` ({e.mapping_ref}) : {e.reason}")
    if cfg.unmapped_target_notes:
        out += ["", "Colonnes cibles hors mapping (non corroborées) :", ""]
        for col, note in cfg.unmapped_target_notes.items():
            out.append(f"- `{col}` : {note}")
    return "\n".join(out) + "\n"


def analysis_markdown(hcfg: HypothesesConfig, scfg: ScoringConfig) -> str:
    out = [
        "## Moteur d'hypothèses (analyse déterministe des écarts)",
        "",
        "Pour chaque verdict ANOMALIE ou INDETERMINE, un catalogue fermé d'hypothèses génériques "
        "est testé (`config/hypotheses.yaml`). Une hypothèse vérifiée ajoute des preuves, une cause "
        "probable et un modificateur de priorité ; elle ne modifie jamais le verdict.",
        "",
        "| Ordre | Hypothèse | Libellé | Modificateur de priorité | Justification du modificateur |",
        "|:-:|---|---|:-:|---|",
    ]
    for i, hid in enumerate(hcfg.precedence, start=1):
        h, m = hcfg.hypotheses[hid], scfg.modifiers[hid]
        out.append(f"| {i} | `{hid}` | {h.label} | ×{m.factor:g} | {_cell(m.reason)} |")
    out += ["", "### Description des hypothèses", ""]
    for hid in hcfg.precedence:
        out.append(f"- **`{hid}`** — {_cell(hcfg.hypotheses[hid].description)}")
    out += ["", "### Seuils", "",
            f"- Écart systémique : au moins {hcfg.systemic_min_count} enregistrements et "
            f"{hcfg.systemic_min_share:.0%} du champ.",
            f"- Bijection : au moins {hcfg.bijection_min_count} écarts, correspondance un-pour-un, et une "
            "valeur attendue partagée par des employés différents (sinon la bijection est triviale).",
            f"- Source alternative : concordance sur au moins {hcfg.alt_source_min_support:.0%} d'une "
            f"population d'au moins {hcfg.alt_source_min_count} enregistrements, supérieure à celle de la "
            "règle actuelle, et au moins une anomalie expliquée. La colonne devient une règle candidate.",
            "",
            "## Score de priorité", "",
            f"priorité = {scfg.scale:g} × (criticité / 5) × poids de confiance × base du verdict × "
            f"produit des modificateurs, plafonnée à {scfg.scale:g}.", "",
            "| Confiance | Poids |", "|---|:-:|"]
    for k, v in scfg.confidence_weights.items():
        out.append(f"| {k} | {v:g} |")
    out += ["", "| Verdict | Base |", "|---|:-:|"]
    for k, v in scfg.verdict_base.items():
        out.append(f"| {k} | {v:g} |")
    return "\n".join(out) + "\n"


def documentation_markdown(cfg: RulesConfig, hcfg: HypothesesConfig, scfg: ScoringConfig) -> str:
    return rules_markdown(cfg) + "\n" + analysis_markdown(hcfg, scfg)
