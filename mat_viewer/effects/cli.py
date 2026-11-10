"""``mat-vis operate`` command.

``build-supercell`` is the structural supercell. There is no
``operate supercell`` alias; display repetition stays on the viewer
``supercell`` shorthand and on ``--repeat`` for the terminal loader.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_operate_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "operate",
        help="Commit a structural operation.",
        description=(
            "Run a structural operation on a CIF. "
            "Use build-supercell for a new lattice. "
            "Display repeats are not performed by this command."
        ),
    )
    parser.add_argument(
        "operation",
        help="Operation name, for example build-supercell. 'supercell' is rejected.",
    )
    parser.add_argument("structure", nargs="?", help="Input CIF path.")
    parser.add_argument("--a", type=int)
    parser.add_argument("--b", type=int)
    parser.add_argument("--c", type=int)
    parser.add_argument("--miller", nargs=3, type=int)
    parser.add_argument("--layers", type=int)
    parser.add_argument("--vacuum", type=float)
    parser.add_argument("--params-json", help="JSON object of operation parameters.")
    parser.add_argument("--property-policy", help="JSON object of property policies.")
    parser.add_argument("--request-id")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("-o", "--output", help="Write the committed structure as CIF.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def operate_main(args: argparse.Namespace) -> None:
    from ..structure.cif_parse import load_cif
    from .persistence import export_structure
    from .session import EffectSession

    operation = str(args.operation).strip().lower().replace("_", "-")
    if operation in {"supercell", "operate-supercell"}:
        raise SystemExit("use 'mat-vis operate build-supercell'; 'operate supercell' is not a command")
    if not args.structure:
        raise SystemExit("operate requires a structure file")
    command = operation.replace("-", "_")
    params = {}
    if args.params_json:
        params.update(json.loads(args.params_json))
    if args.a is not None or args.b is not None or args.c is not None:
        params.update({"a": args.a, "b": args.b, "c": args.c})
    if args.miller is not None:
        params["miller"] = list(args.miller)
    if args.layers is not None:
        params["layers"] = args.layers
    if args.vacuum is not None:
        params["vacuum"] = args.vacuum
    policy = json.loads(args.property_policy) if args.property_policy else None
    parsed = load_cif(args.structure)
    session = EffectSession()
    ref = session.register(
        parsed.crystal,
        structure_id=Path(args.structure).stem,
        provenance={"path": str(args.structure)},
    )
    outcome = session.operate(
        command,
        params,
        structure_id=ref.id,
        expected_revision=ref.revision,
        request_id=args.request_id,
        preview=bool(args.preview),
        property_policy=policy,
    )
    payload = outcome.to_dict()
    if payload.get("status") == "committed":
        payload["n_sites"] = session.snapshot(session.current_ref(ref.id)).n_sites
        payload["source_n_sites"] = session.snapshot(ref).n_sites
    if args.output and not args.preview and payload.get("status") == "committed":
        crystal = session.checkout(session.current_ref(ref.id))
        exported = export_structure(session.snapshot(session.current_ref(ref.id)), crystal, fmt="cif")
        Path(args.output).write_text(exported["text"], encoding="utf-8")
        payload["output_path"] = str(args.output)
        payload["export_warnings"] = exported["warnings"]
    if args.json_output:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(
            json.dumps(
                {
                    "command": command,
                    "status": payload.get("status", payload.get("candidate_id")),
                    "output": payload.get("output"),
                },
                default=str,
            )
        )


__all__ = ["build_operate_parser", "operate_main"]
