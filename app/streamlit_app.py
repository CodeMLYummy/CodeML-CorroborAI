"""CorroborAI — interface web.

Lancement : ``corroborai app`` (ou ``streamlit run app/streamlit_app.py``).
Toute la logique est dans ``corroborai.service`` et ``corroborai.feedback`` ;
ce fichier ne fait que de la présentation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from corroborai.ai.policy import load_llm_config
from corroborai.feedback import OPERATIONS, ExpertRule, FeedbackError, FeedbackStore, preview_rule, suggest_rules
from corroborai.models import Verdict
from corroborai.service import (
    DEFAULT_FEEDBACK,
    TABLE_LABELS,
    ServiceError,
    evidence_rows,
    export_files,
    finding_label,
    findings_frame,
    investigate,
    parse_mapping_text,
    rerun,
    run_pipeline,
    stage_uploads,
)

st.set_page_config(page_title="CorroborAI", page_icon="🔎", layout="wide")
state = st.session_state


def _show_preview(preview, key: str, store_path: str) -> None:
    """Affiche l'aperçu d'une règle et propose son acceptation."""
    if not preview.ok:
        st.error(preview.summary())
        return
    (st.success if preview.matched else st.warning)(preview.summary())
    if preview.matched:
        st.dataframe(findings_frame(preview.matched), hide_index=True, use_container_width=True)
    author = st.text_input("Auteur de la décision", key=f"{key}_author")
    if st.button("Accepter et enregistrer la règle", key=f"{key}_accept", disabled=not preview.matched):
        store = FeedbackStore.load(store_path, set(state.run.cfg.targets))
        preview.rule.author = author
        try:
            store.add_rule(preview.rule, set(state.run.cfg.targets))
            store.save(store_path)
            state.run = rerun(state.run, feedback_path=store_path)
            state.pop(f"{key}_preview", None)
            st.success(f"Règle {preview.rule.id} enregistrée et appliquée.")
            st.rerun()
        except FeedbackError as exc:
            st.error(str(exc))


# --------------------------------------------------------------------------- barre latérale

with st.sidebar:
    st.title("🔎 CorroborAI")
    st.caption("Corroboration Système A – RH ↔ Système B – Temps")
    st.header("Données")
    mode = st.radio("Source des fichiers", ["Répertoire", "Téléversement"], horizontal=True)
    data_dir = None
    if mode == "Répertoire":
        data_dir = st.text_input("Répertoire des données", value=os.environ.get("CORROBORAI_DATA_DIR", "data"))
    else:
        uploads = st.file_uploader("Fichiers du défi (avec manifest.json)", accept_multiple_files=True,
                                   type=["xlsx", "json", "pdf", "pptx"])
        st.caption("Les fichiers sont copiés dans un répertoire de travail temporaire ; les originaux "
                   "ne sont jamais modifiés.")
    from corroborai.rules_config import load_rules

    rules_cfg = load_rules()
    overrides = {}
    with st.expander("Interprétations des règles"):
        for iid, interp in rules_cfg.interpretations.items():
            if interp.alternatives:
                choices = [interp.choice, *interp.alternatives]
                choice = st.selectbox(iid, choices, index=0, help=" ".join(interp.summary.split()))
                if choice != interp.choice:
                    overrides[iid] = choice
    st.header("Rétroaction experte")
    store_path = st.text_input("Fichier de rétroaction", value=str(DEFAULT_FEEDBACK))
    apply_fb = st.checkbox("Appliquer la rétroaction enregistrée", value=True)
    st.header("Couche IA")
    llm_cfg = load_llm_config()
    providers = list(llm_cfg.providers)
    provider = st.selectbox("Fournisseur", providers, index=providers.index(llm_cfg.default_provider),
                            help="« gabarit » : aucun LLM. « gemini » : GEMINI_API_KEY requis. "
                                 "« local » : serveur compatible OpenAI sur ce poste.")
    use_ai = st.checkbox("Rédiger les synthèses (LLM encadré)", value=False)
    if st.button("Lancer la corroboration", type="primary", use_container_width=True):
        try:
            if mode == "Téléversement":
                data_dir = str(stage_uploads([(f.name, f.getvalue()) for f in uploads or []]))
            with st.spinner("Corroboration en cours…"):
                state.run = run_pipeline(data_dir, overrides, store_path if apply_fb else None,
                                         provider if use_ai else None)
            state.pop("translation", None)
        except (ServiceError, FeedbackError, ValueError) as exc:
            st.error(str(exc))

if "run" not in state:
    st.info("Indiquez les données dans le panneau de gauche, puis lancez la corroboration.")
    st.stop()

run = state.run
result = run.result
counts = result.counts()

tab_overview, tab_inv, tab_patterns, tab_feedback, tab_all, tab_export = st.tabs(
    ["Vue d'ensemble", "À investiguer", "Motifs et règles candidates", "Rétroaction experte",
     "Tous les verdicts", "Exporter"])

# --------------------------------------------------------------------------- vue d'ensemble

with tab_overview:
    cols = st.columns(4)
    for col, v in zip(cols, ("ANOMALIE", "INDETERMINE", "JUSTIFIE", "CONFORME")):
        col.metric(v.capitalize(), counts[v])
    st.caption(f"{len(result.findings)} verdicts · {sum(p.matched for p in result.pairs)} affectations "
               f"appariées sur {len(result.pairs)} · intégrité des fichiers après traitement : "
               f"{'OK' if result.integrity_after.ok else 'ÉCHEC'}")
    if result.feedback is not None:
        fb = result.feedback
        st.caption(f"Rétroaction experte : {sum(len(x) for x in fb.rules_applied.values())} écart(s) par règle, "
                   f"{len(fb.corrections_applied)} correction(s), {len(fb.corrections_stale)} périmée(s).")
    if result.ai is not None:
        glob = next((s for s in result.ai.summaries if s.scope == "GLOBALE"), None)
        if glob:
            st.subheader(f"Synthèse globale ({glob.source})")
            st.write(glob.synthese)
            for p in glob.pistes:
                st.markdown(f"- {p}")
    st.subheader("Priorités les plus élevées")
    st.dataframe(findings_frame(investigate(result.findings)[:10]), hide_index=True, use_container_width=True)

# --------------------------------------------------------------------------- à investiguer

with tab_inv:
    inv = investigate(result.findings)
    if not inv:
        st.success("Aucun écart à investiguer.")
    else:
        fields = sorted({f.target_field for f in inv})
        chosen = st.multiselect("Filtrer par champ", fields, default=[])
        shown = [f for f in inv if not chosen or f.target_field in chosen]
        st.dataframe(findings_frame(shown), hide_index=True, use_container_width=True)
        labels = {finding_label(f): f for f in shown}
        sel = st.selectbox("Examiner un écart", list(labels))
        f = labels[sel]
        st.divider()
        st.subheader(f"{f.person_id} · {f.target_field}")
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**Valeur source**  \n{f.source_raw or '(vide)'}")
        c2.markdown(f"**Valeur attendue**  \n{f.expected if f.expected is not None else '(vide)'}")
        c3.markdown(f"**Valeur cible**  \n{f.target_raw if f.target_raw is not None else '(vide)'}")
        st.markdown(f"**Verdict :** {f.verdict.value}"
                    f"{f' — {f.subcategory}' if f.subcategory else ''} · décision : {f.decision_source.value}"
                    f" · confiance : {f.confidence.value} · règle : `{f.rule_id}` ({f.rule_ref})")
        st.markdown(f"**Justification.** {f.justification}")
        if f.probable_cause:
            st.markdown(f"**Cause probable.** {f.probable_cause}")
        if f.priority_breakdown:
            st.markdown(f"**Priorité {f.priority:g}.** {f.priority_breakdown}")
        if f.hypotheses:
            with st.expander("Hypothèses"):
                for h in f.hypotheses:
                    st.markdown(f"- `{h.hypothesis_id}` {'✓ vérifiée' if h.verified else '✗ non vérifiée'} — "
                                f"{h.description}")
        if f.explanation:
            st.info(f"**Explication ({f.explanation_source.value if f.explanation_source else ''}).** "
                    f"{f.explanation}")
        st.markdown("**Données ayant servi à la décision**")
        for ev, row in evidence_rows(run.bundle, f):
            with st.expander(f"{TABLE_LABELS.get(ev.table, ev.table)} — ligne {ev.row} "
                             f"{('· ' + ev.note) if ev.note else ''}"):
                st.dataframe(pd.DataFrame([row]).T.rename(columns={0: "valeur"}), use_container_width=True)
        st.markdown("**Corriger ce verdict**")
        cc1, cc2 = st.columns([1, 3])
        new_verdict = cc1.selectbox("Verdict", [v.value for v in Verdict], key="corr_verdict")
        reason = cc2.text_input("Justification (obligatoire)", key="corr_reason")
        author = st.text_input("Auteur", key="corr_author")
        if st.button("Enregistrer la correction"):
            try:
                store = FeedbackStore.load(store_path, set(run.cfg.targets))
                store.add_correction(f, new_verdict, reason, author)
                store.save(store_path)
                state.run = rerun(run, feedback_path=store_path)
                st.success("Correction enregistrée et appliquée.")
                st.rerun()
            except FeedbackError as exc:
                st.error(str(exc))

# --------------------------------------------------------------------------- motifs et règles candidates

with tab_patterns:
    analysis = result.analysis
    if analysis is None or not analysis.patterns:
        st.info("Aucun motif détecté.")
    else:
        st.subheader("Motifs")
        st.dataframe(pd.DataFrame([{"Hypothèse": p.hypothesis_id, "Champ": p.target_field, "Verdicts": p.count,
                                    "Description": p.description} for p in analysis.patterns]),
                     hide_index=True, use_container_width=True)
    if analysis is not None and analysis.candidate_rules:
        st.subheader("Règles candidates (à valider)")
        for i, c in enumerate(analysis.candidate_rules):
            with st.expander(f"{c.target_field} ← {c.column}"):
                st.write(c.description)
                if st.button("Prévisualiser une règle experte", key=f"cand_{i}"):
                    rule = ExpertRule("", "accept_alternative_source", c.target_field, {"column": c.column},
                                      f"Correspondance confirmée sur {c.support:.0%} des enregistrements.",
                                      provenance="SUGGESTION")
                    state[f"cand{i}_preview"] = preview_rule(rule, result.findings, result.rule_context,
                                                             set(run.cfg.targets))
                if f"cand{i}_preview" in state:
                    _show_preview(state[f"cand{i}_preview"], f"cand{i}", store_path)

# --------------------------------------------------------------------------- rétroaction experte

with tab_feedback:
    targets = set(run.cfg.targets)
    anomaly_fields = sorted({f.target_field for f in result.findings if f.verdict is Verdict.ANOMALIE})

    st.subheader("1. Consigne en langage naturel")
    st.caption("Le LLM propose une règle du mini-langage ; elle est validée, prévisualisée, puis soumise "
               "à votre acceptation. Elle n'est jamais appliquée automatiquement.")
    text = st.text_area("Consigne", placeholder="Ex. : les heures hebdomadaires viennent du contrat du poste, "
                                                "ces écarts sont normaux.")
    if st.button("Traduire en règle"):
        from corroborai.ai.translate import translate_feedback

        with st.spinner("Traduction…"):
            state.translation = translate_feedback(text, result, run.bundle, llm_cfg, provider)
        state.pop("nl_preview", None)
    tr = state.get("translation")
    if tr is not None:
        st.markdown(f"**Reformulation ({tr.source}, {tr.status}, confiance {tr.confidence}).** {tr.reformulation}")
        if tr.errors:
            with st.expander("Erreurs de validation rencontrées"):
                for e in tr.errors:
                    st.markdown(f"- {e}")
        if tr.rule is not None:
            st.json({"operation": tr.rule.operation, "champ": tr.rule.field, "parametres": tr.rule.params})
            tr.rule.reason = st.text_input("Justification enregistrée", value=tr.rule.reason, key="nl_reason")
            if st.button("Prévisualiser l'impact", key="nl_prev"):
                state.nl_preview = preview_rule(tr.rule, result.findings, result.rule_context, targets)
            if "nl_preview" in state:
                _show_preview(state.nl_preview, "nl", store_path)

    st.subheader("2. Formulaire")
    if not anomaly_fields:
        st.info("Aucune anomalie : aucune règle à créer.")
    else:
        fld = st.selectbox("Champ", anomaly_fields, key="form_field")
        op = st.selectbox("Opération", list(OPERATIONS), key="form_op",
                          format_func=lambda o: f"{o} — {OPERATIONS[o]['description']}")
        field_findings = [f for f in result.findings if f.target_field == fld and f.verdict is Verdict.ANOMALIE]
        params: dict = {}
        try:
            if op == "accept_alternative_source":
                pref = [c.column for c in (result.analysis.candidate_rules if result.analysis else [])
                        if c.target_field == fld]
                cols = result.rule_context.candidate_columns(result.findings, fld, pref)
                params = {"column": st.selectbox("Colonne", cols or ["(aucune)"], key="form_col")}
            elif op == "accept_hypothesis":
                hyps = sorted({h.hypothesis_id for f in field_findings for h in f.hypotheses if h.verified})
                params = {"hypothesis": st.selectbox("Hypothèse", hyps or ["(aucune)"], key="form_hyp")}
            elif op == "accept_subcategory":
                subs = sorted({f.subcategory for f in field_findings if f.subcategory})
                params = {"subcategory": st.selectbox("Sous-catégorie", subs or ["(aucune)"], key="form_sub")}
            else:
                params = {"mapping": parse_mapping_text(st.text_area(
                    "Recodage (une paire par ligne : valeur attendue => valeur cible)", key="form_map"))}
        except ServiceError as exc:
            st.caption(str(exc))
        reason = st.text_input("Justification (obligatoire)", key="form_reason")
        if st.button("Prévisualiser l'impact", key="form_prev") and params:
            state.form_preview = preview_rule(ExpertRule("", op, fld, params, reason), result.findings,
                                              result.rule_context, targets)
        if "form_preview" in state:
            _show_preview(state.form_preview, "form", store_path)

    st.subheader("3. Règles suggérées à partir de vos corrections")
    store_now = FeedbackStore.load(store_path, targets) if Path(store_path).exists() else FeedbackStore()
    sugg = suggest_rules(store_now, result.findings)
    if not sugg:
        st.caption("Aucune suggestion : il faut au moins deux corrections acceptant des écarts semblables.")
    for i, r in enumerate(sugg):
        with st.expander(r.describe()):
            st.write(r.reason)
            if st.button("Prévisualiser", key=f"sug_{i}"):
                state[f"sug{i}_preview"] = preview_rule(r, result.findings, result.rule_context, targets)
            if f"sug{i}_preview" in state:
                _show_preview(state[f"sug{i}_preview"], f"sug{i}", store_path)

    st.subheader("4. Rétroaction enregistrée")
    if store_now.rules or store_now.corrections:
        recs = [{"Type": "Règle", "Identifiant": r.id, "Description": r.describe(), "Justification": r.reason,
                 "Provenance": r.provenance} for r in store_now.rules]
        recs += [{"Type": "Correction", "Identifiant": c.finding_id, "Description": f"→ {c.verdict}",
                  "Justification": c.reason, "Provenance": "EXPERT"} for c in store_now.corrections]
        st.dataframe(pd.DataFrame(recs), hide_index=True, use_container_width=True)
        target_id = st.selectbox("Retirer", [x["Identifiant"] for x in recs], key="del_id")
        if st.button("Retirer l'élément sélectionné"):
            store_now.remove_rule(target_id)
            store_now.remove_correction(target_id)
            store_now.save(store_path)
            state.run = rerun(run, feedback_path=store_path)
            st.rerun()
    else:
        st.caption(f"Aucune rétroaction enregistrée dans {store_path}.")

# --------------------------------------------------------------------------- tous les verdicts

with tab_all:
    c1, c2 = st.columns(2)
    v_sel = c1.multiselect("Verdicts", [v.value for v in Verdict], default=[])
    f_sel = c2.multiselect("Champs", sorted({f.target_field for f in result.findings}), default=[])
    st.dataframe(findings_frame(result.findings, v_sel or None, f_sel or None),
                 hide_index=True, use_container_width=True)

# --------------------------------------------------------------------------- export

with tab_export:
    st.write("Rapport complet (Excel) et verdicts (CSV), avec justifications, règles et preuves.")
    if st.button("Générer les fichiers"):
        state.exports = export_files(run)
    if "exports" in state:
        xlsx, csv_path = state.exports
        st.download_button("Télécharger le rapport Excel", Path(xlsx).read_bytes(), file_name=Path(xlsx).name)
        st.download_button("Télécharger le CSV", Path(csv_path).read_bytes(), file_name=Path(csv_path).name)
