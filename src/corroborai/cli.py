"""Interface en ligne de commande de CorroborAI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corroborai import __version__
from corroborai.io.loaders import DataLoadError, load_bundle
from corroborai.rules_config import RulesConfigError, load_rules, validate_against_data
from corroborai.rules_doc import documentation_markdown


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        bundle = load_bundle(args.data_dir, args.config, strict=not args.no_strict)
    except DataLoadError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2

    print("Contrôle d'intégrité (manifest.json)")
    for rec in bundle.integrity.to_records():
        flag = "requis " if rec["requis"] else "option."
        lu = f" -> {rec['fichier_lu']}" if rec["fichier_lu"] and rec["fichier_lu"] != rec["manifest"] else ""
        print(f"  [{rec['statut']:<20}] {flag} {rec['manifest'] or rec['fichier_logique']}{lu}")
    print("\nTables chargées")
    for t in (bundle.source, bundle.target, bundle.poste_detail, bundle.motifs):
        print(f"  {t.name:<13} {t.df.shape[0]:>4} lignes × {t.df.shape[1] - 1:>3} colonnes  ({t.path.name})")
    print(f"  {'mapping':<13} {len(bundle.mapping):>4} feuilles: {', '.join(bundle.mapping)}")

    print("\nRègles (rules.yaml)")
    try:
        cfg = load_rules(args.rules)
    except RulesConfigError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    res = validate_against_data(cfg, bundle)
    print(f"  {len(cfg.fields)} champs, {len(cfg.interpretations)} interprétations, "
          f"{len(res.errors)} erreur(s), {len(res.warnings)} avertissement(s)")
    for e in res.errors:
        print(f"  [ERREUR] {e}")
    for w in res.warnings:
        print(f"  [AVERT.] {w}")
    return 0 if bundle.integrity.ok and res.ok else 1


def _cmd_rules(args: argparse.Namespace) -> int:
    try:
        cfg = load_rules(args.rules)
    except RulesConfigError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2
    if args.markdown:
        from corroborai.analysis import load_hypotheses, load_scoring

        hcfg = load_hypotheses()
        text = documentation_markdown(cfg, hcfg, load_scoring(hypotheses=hcfg))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            print(f"Documentation écrite : {args.output}")
        else:
            print(text)
        return 0
    for f in sorted(cfg.fields, key=lambda f: (-f.criticality, f.target)):
        print(f"  [{f.criticality}] {f.target:<22} {f.rule_id:<20} {f.interpretation or ''}")
    return 0


def _parse_overrides(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"--interpretation attend ID=choix, reçu « {item} »")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _cmd_run(args: argparse.Namespace) -> int:
    from corroborai.engine import corroborate
    from corroborai.report import write_csv, write_report

    try:
        bundle = load_bundle(args.data_dir, args.config)
        cfg = load_rules(args.rules)
        res = validate_against_data(cfg, bundle)
        if not res.ok:
            for e in res.errors:
                print(f"[ERREUR] {e}", file=sys.stderr)
            return 2
        store = None
        if args.retroaction:
            from corroborai.feedback import FeedbackStore

            store = FeedbackStore.load(args.retroaction, set(cfg.targets))
        result = corroborate(bundle, cfg, _parse_overrides(args.interpretation), feedback=store)
    except (DataLoadError, RulesConfigError, ValueError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    if args.ia:
        from corroborai.ai.policy import LLMConfigError, load_llm_config
        from corroborai.ai.tasks import run_ai

        try:
            llm_cfg = load_llm_config(args.llm_config)
            result.ai = run_ai(result, bundle, llm_cfg, args.fournisseur,
                               cache_dir=args.cache_ia, audit_path=out / "audit_ia.jsonl")
        except LLMConfigError as exc:
            print(f"ERREUR : {exc}", file=sys.stderr)
            return 2
    xlsx = write_report(result, out / "rapport_corroboration.xlsx")
    csv_path = write_csv(result, out / "verdicts.csv")
    counts = result.counts()
    print(f"Corroboration terminée en {result.duration_s:.2f} s — {len(result.findings)} verdicts, "
          f"{sum(p.matched for p in result.pairs)} affectations appariées sur {len(result.pairs)}")
    for v in ("ANOMALIE", "INDETERMINE", "JUSTIFIE", "CONFORME"):
        print(f"  {v:<12} {counts[v]:>4}")
    print(f"Intégrité des fichiers sources après traitement : "
          f"{'OK' if result.integrity_after.ok else 'ÉCHEC'}")
    if result.feedback is not None:
        fb = result.feedback
        print(f"Rétroaction experte : {sum(len(x) for x in fb.rules_applied.values())} écart(s) par règle, "
              f"{len(fb.corrections_applied)} correction(s), {len(fb.corrections_stale)} périmée(s)")
    if result.ai is not None:
        ai = result.ai
        print(f"Couche IA : {ai.provider} ({ai.provider_class}) — {ai.calls} tâche(s) : "
              + ", ".join(f"{k}={v}" for k, v in sorted(ai.statuses.items())))
    print(f"Rapport : {xlsx}\nCSV     : {csv_path}")
    return 0 if result.integrity_after.ok and not result.errors else 1


def _cmd_app(args: argparse.Namespace) -> int:
    import subprocess

    app = Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py"
    try:
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])
    except FileNotFoundError:  # pragma: no cover
        print("Streamlit n'est pas installé : pip install -e '.[app]'", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corroborai", description=__doc__)
    p.add_argument("--version", action="version", version=f"corroborai {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Vérifie l'intégrité et la structure des données d'entrée")
    check.add_argument("--data-dir", type=Path, default=Path("data"))
    check.add_argument("--config", type=Path, default=None, help="Chemin de datasets.yaml")
    check.add_argument("--rules", type=Path, default=None, help="Chemin de rules.yaml")
    check.add_argument("--no-strict", action="store_true",
                       help="Continuer malgré un échec d'intégrité (déconseillé)")
    check.set_defaults(func=_cmd_check)

    run = sub.add_parser("run", help="Exécute la corroboration et produit le rapport")
    run.add_argument("--data-dir", type=Path, default=Path("data"))
    run.add_argument("--out", type=Path, default=Path("out"), help="Répertoire de sortie")
    run.add_argument("--config", type=Path, default=None, help="Chemin de datasets.yaml")
    run.add_argument("--rules", type=Path, default=None, help="Chemin de rules.yaml")
    run.add_argument("--interpretation", action="append", metavar="ID=CHOIX",
                     help="Force une interprétation (ex. INT-ASSIGN-DATES=strict_literal)")
    run.add_argument("--retroaction", type=Path, default=None,
                     help="Fichier de rétroaction experte (règles et corrections) à appliquer")
    run.add_argument("--ia", action="store_true", help="Active la couche LLM encadrée (synthèses, triage)")
    run.add_argument("--fournisseur", default=None,
                     help="Fournisseur LLM défini dans llm.yaml (défaut : default_provider, soit « gabarit »)")
    run.add_argument("--llm-config", type=Path, default=None, help="Chemin de llm.yaml")
    run.add_argument("--cache-ia", type=Path, default=None,
                     help="Répertoire de cache des réponses LLM (revalidées à chaque lecture)")
    run.set_defaults(func=_cmd_run)

    app = sub.add_parser("app", help="Lance l'interface web (Streamlit)")
    app.set_defaults(func=_cmd_app)

    rules = sub.add_parser("rules", help="Affiche ou documente les règles codifiées")
    rules.add_argument("--rules", type=Path, default=None, help="Chemin de rules.yaml")
    rules.add_argument("--markdown", action="store_true", help="Produit la documentation Markdown")
    rules.add_argument("-o", "--output", type=Path, default=None, help="Fichier de sortie (Markdown)")
    rules.set_defaults(func=_cmd_rules)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
