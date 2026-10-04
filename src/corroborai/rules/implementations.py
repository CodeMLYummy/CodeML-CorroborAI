"""Implémentation des types de règles déclarés dans ``rules.yaml``.

Chaque fonction reçoit un :class:`RuleContext` et retourne un :class:`Outcome`.
Elles sont pures et déterministes ; elles respectent le choix d'interprétation
actif (``ctx.choice``), ce qui permet au moteur de réévaluer un champ sous
chaque interprétation alternative.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from corroborai.io.normalize import FieldKind, NormalizationError, norm_code, norm_date, norm_text, strip_accents
from corroborai.models import Confidence, Evidence, Verdict
from corroborai.rules import dates
from corroborai.rules.base import (
    NO_DIRECT,
    UNKNOWN,
    Outcome,
    RuleContext,
    decide,
    ev_motif,
    ev_poste,
    norm_target,
    show,
    source_norm,
)

RuleFn = Callable[[RuleContext], Outcome]


def _with_interp(ctx: RuleContext, iid: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"interpretation": f"{iid}={ctx.choice(iid)}", **(extra or {})}


# --------------------------------------------------------------------------- direct

def r_direct(ctx: RuleContext) -> Outcome:
    col = ctx.field.sources[0]
    value, err = source_norm(ctx, col)
    return decide(ctx, expected=value, source_direct=value, unknown_reason=err or "",
                  rule_desc=f"report direct de « {col} »", source_value=ctx.src.get(col))


def r_direct_accent_tolerant(ctx: RuleContext) -> Outcome:
    iid = ctx.field.interpretation or "INT-NAME-ACCENTS"
    col = ctx.field.sources[0]
    value, err = source_norm(ctx, col, FieldKind.TEXT)
    out = decide(ctx, expected=value, source_direct=value, unknown_reason=err or "",
                 rule_desc=f"report direct de « {col} »", kind=FieldKind.TEXT,
                 params=_with_interp(ctx, iid), source_value=ctx.src.get(col))
    if (out.verdict is Verdict.ANOMALIE and ctx.choice(iid) == "accent_tolerant"
            and value is not UNKNOWN and out.target_norm is not None and value is not None
            and strip_accents(out.target_norm).casefold() == strip_accents(value).casefold()):
        out.verdict = Verdict.JUSTIFIE
        out.confidence = Confidence.MOYENNE
        out.subcategory = "accents ou casse retirés"
        out.justification = (f"Source {show(value)}, cible {show(out.target_norm)} : seuls les accents "
                             f"ou la casse diffèrent ({iid} : retrait des accents prévu au mapping).")
    return out


# --------------------------------------------------------------------------- courriel

def r_email(ctx: RuleContext) -> Outcome:
    iid = ctx.field.interpretation or "INT-EMAIL"
    p = ctx.params(iid)
    first_col, last_col, id_col = ctx.field.sources
    first, last, ident = (norm_text(ctx.src.get(c)) for c in (first_col, last_col, id_col))
    params = _with_interp(ctx, iid)
    source_value = f"{first or '?'} / {last or '?'} / {ident or '?'}"
    if not (first and last and ident):
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="",
                      unknown_reason="Prénom, nom ou matricule manquant : courriel attendu incalculable.",
                      params=params, source_value=source_value)

    digits = re.sub(r"\D", "", ident)[-int(p["digits_from_id"]):]
    exp_initial = strip_accents(first[0]).lower()
    exp_name = strip_accents(last).lower().replace(" ", "")
    exp_local = f"{exp_initial}{exp_name}{digits}"
    domain = str(p["domain"]).lower()
    expected = f"{exp_local}@{domain}"
    rule_desc = (f"1re lettre du prénom + nom sans accents + {p['digits_from_id']} derniers chiffres "
                 f"du matricule + « @{domain} »")

    target, err = norm_target(ctx, FieldKind.TEXT)
    if err:
        err.params, err.source_value = params, source_value
        return err
    if target is None:
        return Outcome(Verdict.ANOMALIE, expected, None, f"Courriel absent ; attendu {show(expected)}.",
                       subcategory="courriel absent", params=params, source_value=source_value)
    if ctx.choice(iid) == "strict":
        ok = target == expected
        return Outcome(Verdict.JUSTIFIE if ok else Verdict.ANOMALIE, expected, target,
                       f"Comparaison stricte : attendu {show(expected)}, cible {show(target)}.",
                       params=params, source_value=source_value)

    local, _, dom = target.rpartition("@")
    m = re.match(str(p["env_prefix_pattern"]), local)
    prefix = m.group(0) if m else ""
    rest = local[len(prefix):]
    rest_cmp = rest.lower() if p.get("case_insensitive", True) else rest
    checks = {
        "domaine": dom.lower() == domain,
        "initiale": rest_cmp[:1] == exp_initial,
        "nom": rest_cmp[1:].startswith(exp_name),
        f"{len(digits)} derniers chiffres": rest_cmp.endswith(digits) and rest_cmp == exp_local,
    }
    parts = [f"{k} {'✓' if v else '✗'}" for k, v in checks.items()]
    if prefix:
        parts.insert(0, f"préfixe d'environnement « {prefix} »")
    found_ids = re.findall(r"\d+", rest)
    found = f" Identifiant numérique trouvé : {', '.join(found_ids)}." if found_ids else ""
    detail = f"Composantes : {', '.join(parts)}."
    params["composantes"] = "; ".join(parts)

    if all(checks.values()):
        exact = not prefix and rest == exp_local and dom == domain
        subcat = None if exact else ("préfixe d'environnement" if prefix else "casse")
        return Outcome(Verdict.JUSTIFIE, expected, target,
                       f"Courriel construit selon la règle ({rule_desc}). {detail}",
                       Confidence.ELEVEE if exact else Confidence.MOYENNE, subcat,
                       params=params, source_value=source_value)

    if not checks["domaine"]:
        subcat = "domaine incorrect"
    elif checks["initiale"] and not (checks["nom"] and checks[f"{len(digits)} derniers chiffres"]):
        subcat = "identifiant incohérent avec le matricule"
    else:
        subcat = "courriel non conforme"
    return Outcome(Verdict.ANOMALIE, expected, target,
                   f"Attendu {show(expected)} ({rule_desc}), cible {show(target)}. {detail}{found}",
                   subcategory=subcat, params=params, source_value=source_value)


# --------------------------------------------------------------------------- concaténation

def r_concat_code_label(ctx: RuleContext) -> Outcome:
    iid = ctx.field.interpretation or "INT-CONCAT-PADDING"
    code_col, label_col = ctx.field.sources
    pad, sep = int(ctx.field.params["pad_width"]), str(ctx.field.params["separator"])
    code, label = norm_code(ctx.src.get(code_col)), norm_text(ctx.src.get(label_col))
    params = _with_interp(ctx, iid)
    if code is None or label is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", params=params,
                      unknown_reason=f"« {code_col} » ou « {label_col} » vide : concaténation incalculable.",
                      source_value=f"{code} / {label}")
    padded = ctx.choice(iid) == "zero_padded" and code.isdigit()
    code_fmt = code.zfill(pad) if padded else code
    expected = f"{code_fmt}{sep}{label}"
    desc = (f"« {code_col} »{f' sur {pad} positions' if padded else ''} + « {sep} » + « {label_col} »")
    out = decide(ctx, expected=expected, source_direct=label, rule_desc=desc, kind=FieldKind.TEXT,
                 params=params, source_value=f"{ctx.src.get(code_col)} / {ctx.src.get(label_col)}")
    if out.verdict is Verdict.ANOMALIE and out.target_norm and sep in out.target_norm:
        t_code, _, t_label = out.target_norm.partition(sep)
        bad = []
        if norm_code(t_code) != code:
            bad.append(f"partie code {show(t_code)} ≠ {show(code_fmt)}")
        if t_label != label:
            bad.append(f"partie libellé {show(t_label)} ≠ {show(label)}")
        if bad:
            out.justification += " Écart sur : " + " ; ".join(bad) + "."
            out.subcategory = "code et libellé différents" if len(bad) == 2 else (
                "code différent" if "code" in bad[0] else "libellé différent")
    return out


# --------------------------------------------------------------------------- situation d'emploi

def _situation(ctx: RuleContext) -> tuple[Any, list[Evidence], list[str], Confidence, dict | None]:
    """Code de traitement des accès selon l'interprétation, avec contrôle croisé."""
    iid = "INT-SITUATION-CODE"
    cols = ctx.params(iid).get("columns", {})
    mj = ctx.cfg.joins["motifs"]
    motif = ctx.refs.motifs.get(norm_code(ctx.src.get(mj.col("situation").source)) or "")
    candidates = {
        "code_suspension_acces": norm_code(ctx.src.get(cols.get("code_suspension_acces", mj.col("acces").source))),
        "code_statut_emploi": norm_code(ctx.src.get(cols.get("code_statut_emploi", "CodeStatutEmploi"))),
        "motif_code_gestion_acces": norm_code(motif.get(mj.col("acces").table)) if motif else None,
    }
    evidence = [ev_motif(motif, "Motif de la situation d'emploi")] if motif else []
    present = {k: v for k, v in candidates.items() if v is not None}
    notes, conf = [], Confidence.ELEVEE
    if len(set(present.values())) > 1:
        notes.append("Contrôle croisé incohérent entre les codes de situation : "
                     + ", ".join(f"{k}={v}" for k, v in present.items()) + ".")
        conf = Confidence.MOYENNE
    code = candidates[ctx.choice(iid)]
    return (UNKNOWN if code is None else code), evidence, notes, conf, motif


def r_situation_label(ctx: RuleContext) -> Outcome:
    code, ev, notes, conf, _ = _situation(ctx)
    params = _with_interp(ctx, "INT-SITUATION-CODE")
    sit = None if code is UNKNOWN else ctx.cfg.situation_for(code)
    if sit is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", evidence=ev, params=params,
                      unknown_reason=f"Code de situation {show(code)} absent de la table (INT-SITUATION-UNLISTED).",
                      source_value=code if code is not UNKNOWN else None)
    out = decide(ctx, expected=norm_text(sit.label), source_direct=NO_DIRECT, kind=FieldKind.TEXT,
                 rule_desc=f"code de traitement des accès {int(code):02d} → « {sit.label} »",
                 confidence=conf, evidence=ev, params=params, source_value=code)
    if notes:
        out.justification += " " + " ".join(notes)
    return out


def r_situation_reason_code(ctx: RuleContext) -> Outcome:
    code, ev, notes, conf, motif = _situation(ctx)
    params = _with_interp(ctx, "INT-SITUATION-CODE")
    reason_col = ctx.cfg.joins["motifs"].col("situation").source
    src_value = ctx.src.get(reason_col)
    sit = None if code is UNKNOWN else ctx.cfg.situation_for(code)
    if sit is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", evidence=ev, params=params,
                      unknown_reason=f"Code de situation {show(code)} absent de la table (INT-SITUATION-UNLISTED).",
                      source_value=src_value)
    if sit.reason_code == "none":
        expected, desc = None, f"situation « {sit.label} » : aucun motif transmis"
    elif motif is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", evidence=ev, params=params,
                      unknown_reason=(f"Situation « {sit.label} » : motif {show(src_value)} introuvable "
                                      "dans la table des motifs (INT-MOTIF-MISSING)."),
                      source_value=src_value)
    else:
        remphor = ctx.cfg.joins["motifs"].col("remphor").table
        expected = norm_code(motif.get(remphor))
        desc = f"situation « {sit.label} » : code Remphor du motif {show(norm_code(src_value))}"
    out = decide(ctx, expected=expected, source_direct=NO_DIRECT, rule_desc=desc, confidence=conf,
                 evidence=ev, params=params, source_value=src_value)
    if notes:
        out.justification += " " + " ".join(notes)
    return out


def r_situation_return_date(ctx: RuleContext) -> Outcome:
    code, ev, notes, conf, _ = _situation(ctx)
    params = _with_interp(ctx, "INT-SITUATION-CODE")
    col = ctx.field.sources[-1]
    src_date, err = source_norm(ctx, col, FieldKind.DATE)
    sit = None if code is UNKNOWN else ctx.cfg.situation_for(code)
    if sit is None or err:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", evidence=ev, params=params,
                      unknown_reason=err or f"Code de situation {show(code)} absent de la table (INT-SITUATION-UNLISTED).",
                      source_value=ctx.src.get(col))
    if sit.return_date == "none":
        expected, desc = None, f"situation « {sit.label} » : aucune date de retour transmise"
    else:
        expected, desc = src_date, f"situation « {sit.label} » : « {col} »"
    out = decide(ctx, expected=expected, source_direct=src_date, rule_desc=desc, confidence=conf,
                 evidence=ev, params=params, source_value=ctx.src.get(col))
    if notes:
        out.justification += " " + " ".join(notes)
    return out


# --------------------------------------------------------------------------- type d'employé

def r_contract_type(ctx: RuleContext) -> Outcome:
    cols = ctx.field.sources
    source_value = " / ".join(str(ctx.src.get(c)) for c in cols)
    ct = ctx.cfg.contract_type_for(ctx.src)
    params = _with_interp(ctx, ctx.field.interpretation) if ctx.field.interpretation else {}
    if ct is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", params=params,
                      unknown_reason=(f"Combinaison {source_value} ({', '.join(cols)}) non couverte par le "
                                      "transcodage (INT-CONTRACT-UNMATCHED)."),
                      source_value=source_value)
    cond = " et ".join(f"{k}={'Oui' if v is True else 'Non' if v is False else v}" for k, v in ct.when.items())
    return decide(ctx, expected=ct.code, source_direct=NO_DIRECT, params=params,
                  rule_desc=f"{cond} → « {ct.code} » ({ct.label})", source_value=source_value)


# --------------------------------------------------------------------------- type d'affectation

def r_assignment_flag(ctx: RuleContext) -> Outcome:
    col = ctx.field.sources[0]
    typ = (norm_text(ctx.src.get(col)) or "").upper()
    flags = ctx.cfg.assignment_types.get(typ)
    if flags is None:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="",
                      unknown_reason=f"Type d'affectation {show(typ or None)} inconnu.", source_value=typ)
    expected = flags[ctx.field.target]
    return decide(ctx, expected=expected, source_direct=NO_DIRECT, source_value=typ,
                  rule_desc=f"TypeAffectation {typ} ({flags.get('label', '')}) → {ctx.field.target} = {show(expected)}")


# --------------------------------------------------------------------------- dates d'affectation

def _date_context(ctx: RuleContext) -> tuple[Any, dates.UnitInterval, list[Evidence], str | None, str]:
    date_col, poste_col, unit_col = ctx.field.sources
    raw = ctx.src.get(date_col)
    try:
        boundary = norm_date(raw)
        err = None
    except NormalizationError as exc:
        boundary, err = UNKNOWN, f"Valeur source « {date_col} » illisible : {exc}"
    poste, unit = norm_code(ctx.src.get(poste_col)), norm_code(ctx.src.get(unit_col))
    history = ctx.refs.poste_history.get(poste or "", [])
    ui = dates.unit_interval(history, unit)
    ev = [ev_poste(r, "Historique du détail du poste") for r in history]
    return boundary, ui, ev, err, unit or "?"


def _date_outcome(ctx: RuleContext, *, expected: Any, boundary: Any, ui: dates.UnitInterval,
                  ev: list[Evidence], desc: str) -> Outcome:
    conf = Confidence.ELEVEE if not ui.notes else Confidence.MOYENNE
    out = decide(ctx, expected=expected, source_direct=boundary, rule_desc=desc, kind=FieldKind.DATE,
                 confidence=conf, evidence=ev, params=_with_interp(ctx, "INT-ASSIGN-DATES"),
                 source_value=ctx.src.get(ctx.field.sources[0]))
    if ui.notes:
        out.justification += " " + " ".join(ui.notes)
    return out


def r_assignment_start(ctx: RuleContext) -> Outcome:
    entry, ui, ev, err, unit = _date_context(ctx)
    if err:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", unknown_reason=err, evidence=ev)
    interp = ctx.choice("INT-ASSIGN-DATES")
    expected = dates.assignment_start(entry, ui, interp)
    op = "MAX" if interp == dates.INTERVAL_INTERSECTION else "MIN"
    how = "changement d'unité" if ui.has_change else "aucun changement d'unité : MIN des dates d'effet"
    desc = (f"{op}(date d'entrée {show(entry)}, début de l'unité {unit} {show(ui.start)} [{how}])")
    return _date_outcome(ctx, expected=expected, boundary=entry, ui=ui, ev=ev, desc=desc)


def r_assignment_end(ctx: RuleContext) -> Outcome:
    exit_, ui, ev, err, unit = _date_context(ctx)
    if err:
        return decide(ctx, expected=UNKNOWN, source_direct=NO_DIRECT, rule_desc="", unknown_reason=err, evidence=ev)
    expected = dates.assignment_end(exit_, ui)
    end_txt = show(ui.end) if ui.end else "(aucune : pas de changement d'unité ultérieur)"
    desc = f"MIN(date de sortie {show(exit_)}, fin de l'unité {unit} {end_txt})"
    return _date_outcome(ctx, expected=expected, boundary=exit_, ui=ui, ev=ev, desc=desc)


# --------------------------------------------------------------------------- registre

REGISTRY: dict[str, RuleFn] = {
    "direct": r_direct,
    "direct_accent_tolerant": r_direct_accent_tolerant,
    "email": r_email,
    "concat_code_label": r_concat_code_label,
    "situation_label": r_situation_label,
    "situation_reason_code": r_situation_reason_code,
    "situation_return_date": r_situation_return_date,
    "contract_type": r_contract_type,
    "assignment_flag": r_assignment_flag,
    "assignment_start": r_assignment_start,
    "assignment_end": r_assignment_end,
}
