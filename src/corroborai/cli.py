"""Interface en ligne de commande de CorroborAI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corroborai import __version__
from corroborai.io.loaders import DataLoadError, load_bundle


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
    return 0 if bundle.integrity.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corroborai", description=__doc__)
    p.add_argument("--version", action="version", version=f"corroborai {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Vérifie l'intégrité et la structure des données d'entrée")
    check.add_argument("--data-dir", type=Path, default=Path("data"))
    check.add_argument("--config", type=Path, default=None, help="Chemin de datasets.yaml")
    check.add_argument("--no-strict", action="store_true",
                       help="Continuer malgré un échec d'intégrité (déconseillé)")
    check.set_defaults(func=_cmd_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
