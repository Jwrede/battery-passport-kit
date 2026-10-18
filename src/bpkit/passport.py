"""Build, validate and inspect battery passport documents."""

from __future__ import annotations

import copy
import csv
import datetime as dt
import itertools
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from bpkit.schema import (
    CONDITIONAL_ELEMENTS,
    VERSION,
    Leaf,
    category_value,
    item_schema,
    leaves,
    load_schema,
    open_required_objects,
)

ARRAY_SEPARATOR = "|"
_INDEX = re.compile(r"^(\w+)\[(\d+)\]$")
_DURATION = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$")

# ---------------------------------------------------------------------------
# Paths


def _split(path: str) -> list[str | int]:
    out: list[str | int] = []
    for part in path.split("."):
        m = _INDEX.match(part)
        if m:
            out += [m[1], int(m[2])]
        else:
            out.append(part)
    return out


def get_path(doc, path: str):
    """Value at a dotted path, or None when any step is missing."""
    node = doc
    for key in _split(path):
        if isinstance(key, int):
            if not isinstance(node, list) or key >= len(node):
                return None
        elif not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def set_path(doc: dict, path: str, value) -> None:
    keys = _split(path)
    node = doc
    for key, nxt in itertools.pairwise(keys):
        empty = [] if isinstance(nxt, int) else {}
        if isinstance(key, int):
            while len(node) <= key:
                node.append(copy.copy(empty))
            if node[key] is None:
                node[key] = empty
            node = node[key]
        else:
            node = node.setdefault(key, empty)
    last = keys[-1]
    if isinstance(last, int):
        while len(node) <= last:
            node.append(None)
    node[last] = value


# ---------------------------------------------------------------------------
# Validation

_format_checker = FormatChecker()


@_format_checker.checks("date", raises=ValueError)
def _check_date(value) -> bool:
    return not isinstance(value, str) or bool(dt.date.fromisoformat(value))


@_format_checker.checks("date-time", raises=ValueError)
def _check_datetime(value) -> bool:
    if not isinstance(value, str):
        return True
    if "T" not in value and "t" not in value:
        raise ValueError("missing time part")
    return bool(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))


@_format_checker.checks("duration")
def _check_duration(value) -> bool:
    return not isinstance(value, str) or bool(_DURATION.match(value))


@_format_checker.checks("uri")
def _check_uri(value) -> bool:
    return not isinstance(value, str) or bool(urlparse(value).scheme)


@dataclass
class Problem:
    path: str
    message: str


def validate(passport: dict, category: str, stage: str) -> list[Problem]:
    validator = Draft202012Validator(load_schema(category, stage), format_checker=_format_checker)
    problems = []
    for err in sorted(validator.iter_errors(passport), key=lambda e: list(map(str, e.absolute_path))):
        path = ""
        for p in err.absolute_path:
            path += f"[{p}]" if isinstance(p, int) else (f".{p}" if path else str(p))
        problems.append(Problem(path or "(root)", err.message))
    return problems


# ---------------------------------------------------------------------------
# Completeness


@dataclass
class GroupReport:
    required_total: int = 0
    required_missing: list[str] = field(default_factory=list)
    optional_total: int = 0
    optional_filled: int = 0


@dataclass
class Report:
    groups: dict[str, GroupReport]
    conditional_missing: list[str]
    problems: list[Problem]

    @property
    def required_total(self) -> int:
        return sum(g.required_total for g in self.groups.values())

    @property
    def required_missing(self) -> int:
        return sum(len(g.required_missing) for g in self.groups.values())


def _filled(value) -> bool:
    return value not in (None, "", [], {})


def completeness(passport: dict, category: str, stage: str) -> Report:
    schema = load_schema(category, stage)
    groups: dict[str, GroupReport] = {}
    conditional = []
    for leaf in leaves(schema):
        g = groups.setdefault(leaf.group, GroupReport())
        present = _filled(get_path(passport, leaf.path))
        if leaf.required:
            g.required_total += 1
            if not present:
                g.required_missing.append(leaf.path)
        else:
            g.optional_total += 1
            g.optional_filled += present
            if not present:
                parts = leaf.path.split(".")
                for i, part in enumerate(parts):
                    if part.split("[")[0] in CONDITIONAL_ELEMENTS:
                        element = ".".join(parts[: i + 1])
                        if element not in conditional and not _filled(get_path(passport, element)):
                            conditional.append(element)
                        break
    for obj, fields in open_required_objects(schema).items():
        g = groups.setdefault(Leaf(obj, {}, True, ()).group, GroupReport())
        g.required_total += 1
        if not any(_filled(get_path(passport, f)) for f in fields):
            names = ", ".join(f.rsplit(".", 1)[-1] for f in fields)
            g.required_missing.append(f"{obj} (one of: {names})")
    return Report(groups, conditional, validate(passport, category, stage))


# ---------------------------------------------------------------------------
# Access views


def view(passport: dict, category: str, role: str) -> dict:
    """Copy of the passport with only the elements `role` may read.

    Roles come from the x-AccessRights annotations of the full schema, for example
    "public", "authorities", "repairer" or "notified_bodies".
    """
    out: dict = {}
    for leaf in leaves(load_schema(category, "full")):
        if role not in leaf.read_roles:
            continue
        if "[0]" in leaf.path:
            prefix, rest = leaf.path.split("[0]", 1)
            items = get_path(passport, prefix) or []
            for i in range(len(items)):
                value = get_path(passport, f"{prefix}[{i}]{rest}")
                if value is not None:
                    set_path(out, f"{prefix}[{i}]{rest}", value)
        else:
            value = get_path(passport, leaf.path)
            if value is not None:
                set_path(out, leaf.path, value)
    return out


def roles(category: str) -> list[str]:
    found = {r for leaf in leaves(load_schema(category, "full")) for r in leaf.read_roles}
    return sorted(found)


# ---------------------------------------------------------------------------
# CSV import and templates


def csv_columns(category: str, stage: str) -> list[Leaf]:
    """Elements a user has to supply: all except those with a single fixed value."""
    return [leaf for leaf in leaves(load_schema(category, stage)) if leaf.const is None]


def write_template(fh, category: str, stage: str) -> None:
    cols = csv_columns(category, stage)
    writer = csv.writer(fh)
    writer.writerow([c.path for c in cols])
    writer.writerow(["required" if c.required else "optional" for c in cols])


def _convert(text: str, schema: dict):
    kind = schema.get("type")
    if kind == "integer":
        return int(text)
    if kind == "number":
        return float(text)
    if kind == "boolean":
        if text.lower() in ("true", "yes", "1"):
            return True
        if text.lower() in ("false", "no", "0"):
            return False
        raise ValueError(f"not a boolean: {text!r}")
    return text


def from_row(row: dict[str, str], category: str, stage: str) -> dict:
    """Build a passport from one CSV row whose headers are element paths.

    Empty cells are skipped. Array cells hold values separated by "|". Columns for
    further array items use the same path with another index, e.g. `...[1].chemicalName`.
    Elements with a single allowed value (units, the battery category) are filled in.
    """
    schema = load_schema(category, stage)
    by_pattern = {re.sub(r"\[\d+\]", "[]", leaf.path): leaf for leaf in leaves(schema)}
    doc: dict = {}
    for header, cell in row.items():
        if header is None or cell is None or cell.strip() == "" or cell.strip() in ("required", "optional"):
            continue
        leaf = by_pattern.get(re.sub(r"\[\d+\]", "[]", header.strip()))
        if leaf is None:
            raise ValueError(f"unknown column {header!r}")
        cell = cell.strip()
        try:
            if leaf.is_array:
                items = item_schema(schema, leaf)
                value = [_convert(v.strip(), items) for v in cell.split(ARRAY_SEPARATOR) if v.strip()]
            else:
                value = _convert(cell, leaf.schema)
        except ValueError as exc:
            raise ValueError(f"column {header!r}: {exc}") from None
        set_path(doc, header.strip(), value)
    _fill_constants(doc, schema)
    return doc


def _fill_constants(doc: dict, schema: dict) -> None:
    """Fill single-value elements whose parent object exists in the document."""
    for leaf in leaves(schema):
        if leaf.const is None:
            continue
        paths = [leaf.path]
        if "[0]" in leaf.path:
            prefix, rest = leaf.path.split("[0]", 1)
            paths = [f"{prefix}[{i}]{rest}" for i in range(len(get_path(doc, prefix) or []))]
        for path in paths:
            parent = path.rsplit(".", 1)[0]
            if leaf.required or get_path(doc, parent) is not None:
                set_path(doc, path, leaf.const)


def rows_from_csv(fh) -> list[dict[str, str]]:
    rows = list(csv.DictReader(fh))
    return [r for r in rows if not all((v or "").strip() in ("", "required", "optional") for v in r.values())]


# ---------------------------------------------------------------------------
# Example documents

_EXAMPLE_STRINGS = {
    "date": "2026-11-02",
    "date-time": "2026-11-02T09:00:00Z",
    "duration": "P8Y",
    "uri": "https://example.com/id/0001",
}


def _example_value(schema: dict, name: str):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "integer":
        return int(schema.get("minimum", 0) + (1 if schema.get("maximum", 2) > schema.get("minimum", 0) else 0))
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return False
    pattern = schema.get("pattern", "")
    if "@" in pattern:
        return "info@example.com"
    if pattern.startswith("^https?"):
        return f"https://example.com/{name}"
    return _EXAMPLE_STRINGS.get(schema.get("format", ""), f"<{name}>")


def example(category: str, stage: str, include_optional: bool = False) -> dict:
    """A schema-valid passport with placeholder values for every required element."""
    schema = load_schema(category, stage)
    doc: dict = {}
    for leaf in leaves(schema):
        if not (leaf.required or include_optional):
            continue
        if leaf.is_array:
            items = item_schema(schema, leaf)
            value = [_example_value(items, leaf.name) for _ in range(max(1, leaf.schema.get("minItems", 1)))]
        else:
            value = _example_value(leaf.schema, leaf.name)
        set_path(doc, leaf.path, value)
    for fields in open_required_objects(schema).values():
        if not any(get_path(doc, f) is not None for f in fields):
            candidates = [lf for lf in leaves(schema) if lf.path in fields]
            leaf = next((lf for lf in candidates if "enum" in lf.schema), candidates[0])
            set_path(doc, leaf.path, _example_value(leaf.schema, leaf.name))
    set_path(
        doc,
        "batteryPassportData.identifiersAndProductData.batteryCategory.batteryCategoryValue",
        category_value(category),
    )
    set_path(doc, "dppHeader.dppSchemaVersion", VERSION)
    return doc
