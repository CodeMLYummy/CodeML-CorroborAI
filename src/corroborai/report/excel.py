"""Rapport de corroboration exportable (Excel + CSV).

Feuilles :

* **Synthèse** — métadonnées d'exécution, intégrité, décomptes (formules
  COUNTIFS sur la feuille Détail : le classeur reste cohérent s'il est filtré
  ou modifié par l'équipe fonctionnelle) ;
* **À investiguer** — anomalies et indéterminés, triés par criticité ;
* **Écarts justifiés** / **Conformes** ;
* **Détail** — tous les verdicts avec toutes les colonnes de traçabilité ;
* **Affectations** — résultat de l'appariement source ↔ cible ;
* **Interprétations** — choix retenus, justification, taux d'accord ;
* **Intégrité** — empreintes SHA-256 avant / après traitement.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from corroborai.engine import CorroborationResult
from corroborai.models import Confidence, Finding, Verdict

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
VERDICT_FILLS = {
    Verdict.ANOMALIE.value: PatternFill("solid", fgColor="F8CBAD"),
    Verdict.INDETERMINE.value: PatternFill("solid", fgColor="FFE699"),
    Verdict.JUSTIFIE.value: PatternFill("solid", fgColor="DDEBF7"),
    Verdict.CONFORME.value: PatternFill("solid", fgColor="E2EFDA"),
}
THIN = Side(style="thin", color="BFBFBF")
CONF_ORDER = {Confidence.ELEVEE: 0, Confidence.MOYENNE: 1, Confidence.FAIBLE: 2}

# (en-tête, clé de Finding.to_record(), largeur, retour à la ligne)
COLUMNS: list[tuple[str, str, int, bool]] = [
    ("Identifiant", "finding_id", 30, False),
    ("Employé", "person_id", 11, False),
    ("Affectation", "assignment_key", 13, False),
    ("Champ cible", "target_field", 20, False),
    ("Verdict", "verdict", 13, False),
    ("Sous-catégorie", "subcategory", 26, True),
    ("Criticité", "criticality", 9, False),
    ("Confiance", "confidence", 10, False),
    ("Valeur source", "source_raw", 24, True),
    ("Valeur attendue", "expected", 24, True),
    ("Valeur cible", "target_raw", 24, True),
    ("Justification", "justification", 70, True),
    ("Règle", "rule_id", 17, False),
    ("Réf. Mapping.xlsx", "rule_ref", 26, True),
    ("Paramètres", "rule_params", 40, True),
    ("Colonnes source", "source_fields", 26, True),
    ("Preuves", "evidence", 34, True),
    ("Source de décision", "decision_source", 20, False),
    ("Priorité", "priority", 9, False),
    ("Cause probable", "probable_cause", 60, True),
    ("Hypothèses vérifiées", "hypotheses", 22, True),
    ("Calcul de la priorité", "priority_breakdown", 44, True),
    ("Explication", "explanation", 50, True),
    ("Source explication", "explanation_source", 14, False),
]
_BY_KEY = {c[1]: c for c in COLUMNS}
INVESTIGATE_COLUMNS = [_BY_KEY[k] for k in (
    "priority", "person_id", "assignment_key", "target_field", "verdict", "subcategory",
    "source_raw", "expected", "target_raw", "probable_cause", "justification", "criticality",
    "confidence", "priority_breakdown", "hypotheses", "explanation", "explanation_source",
    "rule_id", "rule_ref", "evidence", "rule_params")]
VIEW_COLUMNS = [_BY_KEY[k] for k in (
    "person_id", "assignment_key", "target_field", "verdict", "subcategory", "criticality",
    "confidence", "source_raw", "expected", "target_raw", "justification", "rule_id", "rule_ref",
    "rule_params", "source_fields", "evidence")]


def _sort_key(f: Finding) -> tuple:
    return (-(f.priority if f.priority is not None else -1), -(f.criticality or 0), CONF_ORDER[f.confidence],
            f.person_id, f.assignment_key, f.target_field)


def _style_header(ws: Worksheet, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)


def _write_table(ws: Worksheet, columns: list[tuple[str, str, int, bool]],
                 records: list[dict[str, Any]], verdict_key: str | None = "verdict") -> None:
    ws.append([c[0] for c in columns])
    _style_header(ws, 1, len(columns))
    for rec in records:
        ws.append([rec.get(c[1]) for c in columns])
    for idx, (_, key, width, wrap) in enumerate(columns, start=1):
        letter = get_column_letter(idx)
        ws.column_dimensions[letter].width = width
        for cell in ws[letter][1:]:
            cell.font = Font(name=FONT, size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=wrap)
            if verdict_key and key == verdict_key and cell.value in VERDICT_FILLS:
                cell.fill = VERDICT_FILLS[cell.value]
                cell.font = Font(name=FONT, size=10, bold=True)
    ws.freeze_panes = "B2" if columns and columns[0][1] in ("finding_id", "person_id", "priority") else "A2"
    if records:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(records) + 1}"


def _kv(ws: Worksheet, row: int, label: str, value: Any, bold: bool = False) -> int:
    lab = ws.cell(row=row, column=1, value=label)
    lab.font = Font(name=FONT, bold=True)
    lab.alignment = Alignment(vertical="top")
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
    c = ws.cell(row=row, column=2, value=value)
    c.font = Font(name=FONT, bold=bold)
    c.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
    return row + 1


def _title(ws: Worksheet, row: int, text: str) -> int:
    ws.cell(row=row, column=1, value=text).font = Font(name=FONT, bold=True, size=12, color="1F3864")
    return row + 1


def _synthesis(ws: Worksheet, result: CorroborationResult, n_detail: int) -> None:
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
    ws.cell(row=1, column=1, value="Rapport de corroboration CorroborAI").font = Font(
        name=FONT, bold=True, size=16, color="1F3864")
    r = 3
    r = _title(ws, r, "Exécution")
    r = _kv(ws, r, "Date", result.started_at.strftime("%Y-%m-%d %H:%M:%S"))
    r = _kv(ws, r, "Version", result.version)
    r = _kv(ws, r, "Durée (s)", round(result.duration_s, 3))
    r = _kv(ws, r, "Configuration des règles", str(result.cfg.path.name if result.cfg.path else ""))
    ov = ", ".join(f"{k}={v}" for k, v in result.overrides.items()) or "aucune (choix par défaut)"
    r = _kv(ws, r, "Interprétations forcées", ov)
    r = _kv(ws, r, "Intégrité avant traitement", "OK" if result.integrity_before.ok else "ÉCHEC",
            bold=True)
    r = _kv(ws, r, "Intégrité après traitement",
            "OK — fichiers sources inchangés" if result.integrity_after.ok else "ÉCHEC", bold=True)
    r = _kv(ws, r, "Erreurs d'évaluation", len(result.errors))
    if result.analysis:
        r = _kv(ws, r, "Motifs détectés", len(result.analysis.patterns))
        r = _kv(ws, r, "Règles candidates", len(result.analysis.candidate_rules))
        if result.feedback is not None:
            fb = result.feedback
            n_rules = sum(1 for x in fb.rules_applied.values() if x)
            r = _kv(ws, r, "Rétroaction experte",
                    f"{len(fb.rules_applied)} règle(s) ({n_rules} appliquée(s), "
                    f"{sum(len(x) for x in fb.rules_applied.values())} écart(s)) — "
                    f"{len(fb.corrections_applied)} correction(s) appliquée(s), "
                    f"{len(fb.corrections_stale)} périmée(s)")
        if result.ai is not None:
            ai = result.ai
            statuses = ", ".join(f"{k} : {v}" for k, v in sorted(ai.statuses.items())) or "aucun"
            r = _kv(ws, r, "Couche IA — fournisseur",
                    f"{ai.provider}{f' ({ai.model})' if ai.model else ''} — données {ai.provider_class}")
            r = _kv(ws, r, "Couche IA — tâches", f"{ai.calls} ({statuses})")
            if ai.audit_path:
                r = _kv(ws, r, "Journal d'audit IA", str(ai.audit_path.name))
            glob = next((x for x in ai.summaries if x.scope == "GLOBALE"), None)
            if glob:
                r += 1
                r = _title(ws, r, f"Synthèse globale ({glob.source})")
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
                c = ws.cell(row=r, column=1, value=glob.synthese)
                c.font = Font(name=FONT, size=10)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.row_dimensions[r].height = 110
                r += 1
        top = [f for f in sorted(result.findings, key=_sort_key) if f.priority is not None][:5]
        r += 1
        r = _title(ws, r, "Priorités les plus élevées")
        for f in top:
            ws.cell(row=r, column=1, value=f"{f.priority:g} — {f.person_id} · {f.target_field}").font = Font(
                name=FONT, bold=True)
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=5)
            c = ws.cell(row=r, column=2, value=f.probable_cause or f.justification)
            c.font = Font(name=FONT, size=10)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 42
            r += 1

    r += 1
    r = _title(ws, r, "Décompte des verdicts")
    rng_v = f"'Détail'!$E$2:$E${n_detail + 1}"
    rng_f = f"'Détail'!$D$2:$D${n_detail + 1}"
    start = r
    for v in (Verdict.ANOMALIE, Verdict.INDETERMINE, Verdict.JUSTIFIE, Verdict.CONFORME):
        ws.cell(row=r, column=1, value=v.value).font = Font(name=FONT, bold=True)
        ws.cell(row=r, column=1).fill = VERDICT_FILLS[v.value]
        ws.cell(row=r, column=2, value=f'=COUNTIF({rng_v},"{v.value}")').font = Font(name=FONT)
        r += 1
    ws.cell(row=r, column=1, value="Total").font = Font(name=FONT, bold=True)
    ws.cell(row=r, column=2, value=f"=SUM(B{start}:B{r - 1})").font = Font(name=FONT, bold=True)
    r += 2

    r = _title(ws, r, "Verdicts par champ")
    headers = ["Champ", "ANOMALIE", "INDETERMINE", "JUSTIFIE", "CONFORME"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=r, column=i, value=h)
    _style_header(ws, r, len(headers))
    r += 1
    fields = sorted({f.target_field for f in result.findings},
                    key=lambda t: (-max((f.criticality or 0) for f in result.findings if f.target_field == t), t))
    for t in fields:
        ws.cell(row=r, column=1, value=t).font = Font(name=FONT)
        for i, v in enumerate(headers[1:], start=2):
            c = ws.cell(row=r, column=i, value=f'=COUNTIFS({rng_f},$A{r},{rng_v},"{v}")')
            c.font = Font(name=FONT)
        r += 1


def _pairs_records(result: CorroborationResult) -> list[dict[str, Any]]:
    out = []
    for p in result.pairs:
        out.append({
            "person_id": p.person_id,
            "key": p.key,
            "method": p.method.value,
            "score": p.score,
            "source_type": p.source_type or "",
            "target_type": p.target_type or "",
            "source_row": p.source["_row"] if p.source else None,
            "target_row": p.target["_row"] if p.target else None,
            "poste": (p.source or {}).get("CodePoste"),
            "emploi_src": (p.source or {}).get("CodeEmploi"),
            "emploi_tgt": (p.target or {}).get("positionId"),
        })
    return out


PAIR_COLUMNS = [
    ("Employé", "person_id", 11, False), ("Affectation", "key", 22, False),
    ("Méthode", "method", 18, False), ("Similarité", "score", 11, False),
    ("Type source", "source_type", 11, False), ("Type cible", "target_type", 11, False),
    ("Ligne source", "source_row", 12, False), ("Ligne cible", "target_row", 12, False),
    ("Poste", "poste", 10, False), ("Emploi (source)", "emploi_src", 14, False),
    ("Emploi (cible)", "emploi_tgt", 14, False),
]


PATTERN_COLUMNS = [
    ("Hypothèse", "hypothesis_id", 20, False), ("Libellé", "label", 34, True),
    ("Champ", "field", 22, False), ("Verdicts concernés", "count", 12, False),
    ("Description", "description", 90, True), ("Identifiants", "ids", 50, True),
]
CANDIDATE_COLUMNS = [
    ("Champ cible", "field", 22, False), ("Colonne candidate", "column", 50, True),
    ("Concordance", "support", 12, False), ("Population", "population", 11, False),
    ("Règle actuelle", "mapped_support", 13, False), ("Anomalies expliquées", "n", 12, False),
    ("Description", "description", 80, True), ("Statut", "status", 22, False),
]


FEEDBACK_COLUMNS = [
    ("Type", "kind", 12, False), ("Identifiant", "id", 30, False), ("Champ", "field", 22, False),
    ("Description", "description", 60, True), ("Justification", "reason", 50, True),
    ("Provenance", "provenance", 16, False), ("Auteur", "author", 14, False), ("Date", "created", 20, False),
    ("Écarts touchés", "count", 10, False), ("Statut", "status", 16, False),
]


def _feedback_records(result: CorroborationResult) -> list[dict[str, Any]]:
    fb = result.feedback
    if fb is None or fb.store is None:
        return []
    out = []
    for r in fb.store.rules:
        n = len(fb.rules_applied.get(r.id, []))
        out.append({"kind": "Règle", "id": r.id, "field": r.field, "description": r.describe(), "reason": r.reason,
                    "provenance": r.provenance, "author": r.author, "created": r.created, "count": n,
                    "status": "active" if r.active else "inactive"})
    for c in fb.store.corrections:
        status = ("appliquée" if c.finding_id in fb.corrections_applied else
                  "PÉRIMÉE" if c.finding_id in fb.corrections_stale else "écart introuvable")
        out.append({"kind": "Correction", "id": c.finding_id, "field": c.finding_id.rsplit(":", 1)[-1],
                    "description": f"Verdict fixé à {c.verdict}", "reason": c.reason, "provenance": "EXPERT",
                    "author": c.author, "created": c.created, "count": 1 if status == "appliquée" else 0,
                    "status": status})
    return out


AI_COLUMNS = [
    ("Portée", "scope", 10, False), ("Employé", "person", 11, False),
    ("Synthèse", "synthese", 80, True), ("Pistes", "pistes", 60, True),
    ("Regroupements / références", "details", 50, True),
    ("Source", "source", 10, False), ("Statut du harnais", "status", 20, False),
]


def _ai_records(result: CorroborationResult) -> list[dict[str, Any]]:
    ai = result.ai
    if ai is None:
        return []
    return [{"scope": s.scope, "person": s.person_id or "", "synthese": s.synthese,
             "pistes": "\n".join(f"• {p}" for p in s.pistes), "details": "\n".join(s.details),
             "source": s.source, "status": s.status} for s in ai.summaries]


def _pattern_records(result: CorroborationResult) -> list[dict[str, Any]]:
    if not result.analysis:
        return []
    labels = {}
    try:
        from corroborai.analysis import load_hypotheses
        labels = {k: v.label for k, v in load_hypotheses().hypotheses.items()}
    except Exception:  # noqa: BLE001 — libellés facultatifs dans le rapport
        pass
    return [{"hypothesis_id": p.hypothesis_id, "label": labels.get(p.hypothesis_id, ""),
             "field": p.target_field, "count": p.count, "description": p.description,
             "ids": ", ".join(p.finding_ids)} for p in result.analysis.patterns]


def _candidate_records(result: CorroborationResult) -> list[dict[str, Any]]:
    if not result.analysis:
        return []
    return [{"field": c.target_field, "column": c.column, "support": round(c.support, 4),
             "population": c.population, "mapped_support": round(c.mapped_support, 4),
             "n": len(c.explains), "description": c.description,
             "status": "À valider par l'expert"} for c in result.analysis.candidate_rules]


def _interpretations(ws: Worksheet, result: CorroborationResult) -> None:
    cols = [("Interprétation", "interpretation", 22, False), ("Champ", "field", 20, False),
            ("Choix", "choice", 22, False), ("Actif", "active", 8, False),
            ("Évalués", "evaluated", 9, False), ("En accord avec la cible", "agreeing", 12, False),
            ("Taux d'accord", "rate", 12, False)]
    recs = [{"interpretation": s.interpretation, "field": s.target_field, "choice": s.choice,
             "active": "oui" if s.active else "", "evaluated": s.evaluated, "agreeing": s.agreeing,
             "rate": round(s.rate, 4)} for s in result.interpretation_stats]
    _write_table(ws, cols, recs, verdict_key=None)
    for cell in ws["G"][1:]:
        cell.number_format = "0%"
    r = len(recs) + 3
    ws.cell(row=r, column=1, value="Justification des choix").font = Font(name=FONT, bold=True, size=12)
    r += 1
    for i in result.cfg.interpretations.values():
        ws.cell(row=r, column=1, value=i.id).font = Font(name=FONT, bold=True)
        ws.cell(row=r, column=2, value=i.choice).font = Font(name=FONT)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=7)
        c = ws.cell(row=r, column=3, value=f"{' '.join(i.summary.split())}\n\n"
                                           f"Justification : {' '.join(i.rationale.split())}")
        c.font = Font(name=FONT, size=10)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[r].height = 120
        r += 1


def write_report(result: CorroborationResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    findings = sorted(result.findings, key=_sort_key)
    records = [f.to_record() for f in findings]
    by = lambda *vs: [r for r in records if r["verdict"] in vs]  # noqa: E731

    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"
    sheets: list[tuple[str, Callable[[Worksheet], None]]] = [
        ("À investiguer", lambda w: _write_table(w, INVESTIGATE_COLUMNS, by("ANOMALIE", "INDETERMINE"))),
        ("Motifs", lambda w: _write_table(w, PATTERN_COLUMNS, _pattern_records(result), verdict_key=None)),
        ("Règles candidates", lambda w: _write_table(w, CANDIDATE_COLUMNS, _candidate_records(result),
                                                     verdict_key=None)),
        *([("Synthèses IA", lambda w: _write_table(w, AI_COLUMNS, _ai_records(result), verdict_key=None))]
          if result.ai is not None else []),
        *([("Rétroaction experte", lambda w: _write_table(w, FEEDBACK_COLUMNS, _feedback_records(result),
                                                          verdict_key=None))]
          if result.feedback is not None else []),
        ("Écarts justifiés", lambda w: _write_table(w, VIEW_COLUMNS, by("JUSTIFIE"))),
        ("Conformes", lambda w: _write_table(w, VIEW_COLUMNS, by("CONFORME"))),
        ("Détail", lambda w: _write_table(w, COLUMNS, records)),
        ("Affectations", lambda w: _write_table(w, PAIR_COLUMNS, _pairs_records(result), verdict_key=None)),
        ("Interprétations", lambda w: _interpretations(w, result)),
        ("Intégrité", lambda w: _write_table(w, [
            ("Fichier", "manifest", 40, False), ("Lu sous le nom", "fichier_lu", 38, False),
            ("Requis", "requis", 8, False), ("Statut avant", "statut", 18, False),
            ("Statut après", "statut_apres", 18, False), ("SHA-256 attendu", "sha256_attendu", 66, False),
            ("SHA-256 après traitement", "sha256_apres", 66, False)],
            _integrity_records(result), verdict_key=None)),
    ]
    for title, writer in sheets:
        writer(wb.create_sheet(title))
    for name, cols in (("Règles candidates", ("C", "E")),):
        for col in cols:
            for cell in wb[name][col][1:]:
                cell.number_format = "0%"
    _synthesis(ws, result, len(records))
    wb.save(path)
    return path


def _integrity_records(result: CorroborationResult) -> list[dict[str, Any]]:
    after = {r["manifest"]: r for r in result.integrity_after.to_records()}
    out = []
    for r in result.integrity_before.to_records():
        a = after.get(r["manifest"], {})
        out.append({**r, "requis": "oui" if r["requis"] else "non",
                    "statut_apres": a.get("statut", ""), "sha256_apres": a.get("sha256_calcule", "")})
    return out


def write_csv(result: CorroborationResult, path: str | Path) -> Path:
    """Export CSV complet (UTF-8 avec BOM pour une ouverture correcte dans Excel)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    findings = sorted(result.findings, key=_sort_key)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow([c[0] for c in COLUMNS])
        for f in findings:
            rec = f.to_record()
            writer.writerow(["" if rec.get(c[1]) is None else rec.get(c[1]) for c in COLUMNS])
    return path
