from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bpkit import __version__
from bpkit.passport import completeness, example, from_row, roles, rows_from_csv, validate, view, write_template
from bpkit.schema import CATEGORIES, STAGES, VERSION, category_value, detect_category, leaves, load_schema


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _category(args, passport: dict | None = None) -> str:
    if args.category:
        return args.category
    found = detect_category(passport or {})
    if found is None:
        sys.exit("error: cannot detect the battery category from batteryCategoryValue; pass --category")
    return found


def cmd_categories(args) -> int:
    print(f"BatteryPass-Ready data model {VERSION}\n")
    print(f"{'category':<24} {'batteryCategoryValue':<36} {'feb27':>6} {'full':>6}")
    for key in CATEGORIES:
        counts = [len(leaves(load_schema(key, stage))) for stage in ("feb27", "full")]
        print(f"{key:<24} {category_value(key):<36} {counts[0]:>6} {counts[1]:>6}")
    print("\nfeb27 = requirements from 18 February 2027, full = all requirements (data elements per schema)")
    return 0


def cmd_example(args) -> int:
    json.dump(example(args.category, args.stage, args.all), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def cmd_template(args) -> int:
    write_template(sys.stdout, args.category, args.stage)
    return 0


def cmd_from_csv(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(args.csv, encoding="utf-8-sig", newline="") as fh:
        rows = rows_from_csv(fh)
    failed = 0
    for i, row in enumerate(rows, start=1):
        try:
            doc = from_row(row, args.category, args.stage)
        except ValueError as exc:
            print(f"row {i}: {exc}", file=sys.stderr)
            failed += 1
            continue
        serial = row.get("batteryPassportData.identifiersAndProductData.batterySerialNumber", "").strip()
        target = out / f"{serial or f'row{i}'}.json"
        target.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        problems = len(validate(doc, args.category, args.stage))
        status = "valid" if problems == 0 else f"{problems} problem(s), see `bpkit report {target}`"
        print(f"{target}: {status}")
    return 1 if failed else 0


def cmd_validate(args) -> int:
    worst = 0
    for path in args.files:
        doc = _load(path)
        category = _category(args, doc)
        problems = validate(doc, category, args.stage)
        print(f"{path}: {'valid' if not problems else f'{len(problems)} problem(s)'} ({category}, {args.stage})")
        for p in problems:
            print(f"  {p.path}: {p.message}")
        worst = max(worst, 1 if problems else 0)
    return worst


def cmd_report(args) -> int:
    doc = _load(args.file)
    category = _category(args, doc)
    rep = completeness(doc, category, args.stage)
    done = rep.required_total - rep.required_missing
    print(f"{args.file}  category {category}, stage {args.stage}, data model {VERSION}")
    print(f"Required elements: {done}/{rep.required_total} filled, schema problems: {len(rep.problems)}\n")
    print(f"{'group':<44} {'required':>10} {'optional':>10}")
    for name, g in rep.groups.items():
        req = f"{g.required_total - len(g.required_missing)}/{g.required_total}"
        opt = f"{g.optional_filled}/{g.optional_total}"
        print(f"{name:<44} {req:>10} {opt:>10}")
    missing = [m for g in rep.groups.values() for m in g.required_missing]
    if missing:
        print("\nMissing required elements:")
        for m in missing:
            print(f"  {m}")
    if rep.conditional_missing:
        print("\nEmpty elements that are mandatory for some batteries (check whether they apply):")
        for m in rep.conditional_missing:
            print(f"  {m}")
    if rep.problems:
        print("\nSchema problems:")
        for p in rep.problems:
            print(f"  {p.path}: {p.message}")
    return 0 if not missing and not rep.problems else 1


def cmd_view(args) -> int:
    doc = _load(args.file)
    category = _category(args, doc)
    if args.role not in roles(category):
        sys.exit(f"error: unknown role {args.role!r}; roles in this schema: {', '.join(roles(category))}")
    json.dump(view(doc, category, args.role), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


def cmd_qr(args) -> int:
    import segno

    qr = segno.make(args.url, error="m")
    qr.save(args.out, scale=args.scale, border=4)
    print(f"{args.out}: QR code for {args.url} (version {qr.version}, error correction M)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bpkit",
        description="Create, validate and inspect EU battery passports (Regulation (EU) 2023/1542) "
        f"with the BatteryPass-Ready data model {VERSION}.",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__} (data model {VERSION})")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, category_required: bool = True, stage: bool = True):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=func)
        if category_required is not None:
            p.add_argument("--category", "-c", choices=CATEGORIES, required=category_required)
        if stage:
            p.add_argument("--stage", "-s", choices=STAGES, default="feb27", help="feb27 (default) or full")
        return p

    add("categories", cmd_categories, "list battery categories and schema sizes", None, False)
    p = add("example", cmd_example, "print a schema-valid example passport with placeholder values")
    p.add_argument("--all", action="store_true", help="also fill optional elements")
    add("template", cmd_template, "print a CSV template with one column per data element")
    p = add("from-csv", cmd_from_csv, "build one passport JSON per CSV row and validate it")
    p.add_argument("csv", type=Path)
    p.add_argument("--out", "-o", default="passports", help="output directory (default ./passports)")
    p = add("validate", cmd_validate, "validate passport files against the schema", False)
    p.add_argument("files", nargs="+", type=Path)
    p = add("report", cmd_report, "show which required elements are missing, per data element group", False)
    p.add_argument("file", type=Path)
    p = add("view", cmd_view, "print the part of a passport that a role may read", False, False)
    p.add_argument("file", type=Path)
    p.add_argument("--role", default="public", help="e.g. public, authorities, repairer (default public)")
    p = add("qr", cmd_qr, "write a QR code that links to the passport", None, False)
    p.add_argument("url")
    p.add_argument("--out", "-o", default="passport-qr.svg", help="output file, .svg or .png (default passport-qr.svg)")
    p.add_argument("--scale", type=int, default=8)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
