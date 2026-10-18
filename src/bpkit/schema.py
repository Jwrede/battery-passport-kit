"""Load the bundled BatteryPass-Ready schemas and list their data elements."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources

VERSION = "2.0.1"

# Category key -> schema file stem (without stage and version).
CATEGORIES = {
    "ev": "EV",
    "lmt": "LMT",
    "industrial-without-bms": "Industrial_Without_BMS",
    "industrial-other": "Other_Industrial_Above_2kWh",
    "industrial-stationary": "Stationary_Industrial_Above_2kWh",
}

# Stage key -> file infix. "feb27" holds the requirements that apply from 18 February 2027.
STAGES = {"feb27": "_Feb27", "full": ""}

# Elements the schemas mark optional although the regulation makes them mandatory for some
# batteries (BatteryPass-Ready README, "Disclaimer").
CONDITIONAL_ELEMENTS = frozenset(
    {
        "symbolsForCadmiumAndLead",
        "warrantyPeriod",
        "nickelRecycledContentShare",
        "cobaltRecycledContentShare",
        "lithiumRecycledContentShare",
        "leadRecycledContentShare",
        "expectedLifetimeNumberOfChargeDischargeCycles",
        "remainingRoundTripEnergyEfficiency",
        "evolutionOfSelfDischargeRates",
        "presentInternalResistanceOfPack",
        "cycleLifeReferenceTest",
        "cRateOfRelevantCycleLifeTest",
    }
)


@dataclass(frozen=True)
class Leaf:
    """One fillable data element, addressed by a dotted path such as `a.b[0].c`."""

    path: str
    schema: dict
    required: bool
    read_roles: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.path.rsplit(".", 1)[-1].split("[", 1)[0]

    @property
    def group(self) -> str:
        parts = self.path.split(".")
        return parts[1] if parts[0] == "batteryPassportData" and len(parts) > 1 else parts[0]

    @property
    def const(self):
        """The only allowed value, if the element has exactly one (for example a fixed unit)."""
        enum = self.schema.get("enum")
        return enum[0] if enum and len(enum) == 1 else None

    @property
    def is_array(self) -> bool:
        return self.schema.get("type") == "array"


def schema_file(category: str, stage: str) -> str:
    if category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}; choose from {', '.join(CATEGORIES)}")
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; choose from {', '.join(STAGES)}")
    return f"{CATEGORIES[category]}{STAGES[stage]}_{VERSION}.json"


@cache
def load_schema(category: str, stage: str) -> dict:
    text = (
        resources.files("bpkit").joinpath("schemas").joinpath(schema_file(category, stage)).read_text(encoding="utf-8")
    )
    return json.loads(text)


def category_value(category: str) -> str:
    """The batteryCategoryValue the schema of a category expects."""
    return load_schema(category, "full")["$defs"]["batteryCategoryCodes"]["enum"][0]


def detect_category(passport: dict) -> str | None:
    value = (
        passport.get("batteryPassportData", {})
        .get("identifiersAndProductData", {})
        .get("batteryCategory", {})
        .get("batteryCategoryValue")
    )
    for key in CATEGORIES:
        if category_value(key) == value:
            return key
    return None


def _resolve(schema: dict, node: dict) -> dict:
    while "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        node = schema["$defs"][name]
    return node


def _roles(prop: dict, inherited: tuple[str, ...]) -> tuple[str, ...]:
    raw = prop.get("x-AccessRights")
    if not raw:
        return inherited
    return tuple(json.loads(raw).get("read", []))


def leaves(schema: dict) -> list[Leaf]:
    """All data elements of a schema in document order.

    An element is required when it and all its parents are listed in `required`.
    Arrays of objects contribute their item fields with index 0.
    """
    out: list[Leaf] = []

    def walk(node: dict, path: str, required: bool, roles: tuple[str, ...]) -> None:
        node = _resolve(schema, node)
        if node.get("type") == "object" or "properties" in node:
            req = set(node.get("required", []))
            for name, prop in node.get("properties", {}).items():
                child = f"{path}.{name}" if path else name
                walk(prop, child, required and name in req, _roles(prop, roles))
            return
        if node.get("type") == "array":
            items = _resolve(schema, node.get("items", {}))
            if items.get("type") == "object" or "properties" in items:
                walk(items, f"{path}[0]", required and node.get("minItems", 0) > 0, roles)
                return
        out.append(Leaf(path, node, required, roles))

    walk(schema, "", True, ("public",))
    return out


def open_required_objects(schema: dict) -> dict[str, list[str]]:
    """Required objects that list no required fields, mapped to their field paths.

    The schema only demands that such an object exists (for example `batteryChemistry`).
    A passport is treated as complete for it when at least one of its fields is filled.
    """
    out: dict[str, list[str]] = {}

    def walk(node: dict, path: str, required: bool) -> None:
        node = _resolve(schema, node)
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties", {})
            req = set(node.get("required", []))
            if required and path and not req and props:
                out[path] = [f"{path}.{name}" for name in props]
            for name, prop in props.items():
                walk(prop, f"{path}.{name}" if path else name, required and name in req)
        elif node.get("type") == "array":
            items = _resolve(schema, node.get("items", {}))
            if items.get("type") == "object" or "properties" in items:
                walk(items, f"{path}[0]", required and node.get("minItems", 0) > 0)

    walk(schema, "", True)
    return out


def item_schema(schema: dict, leaf: Leaf) -> dict:
    """Schema of the items of an array leaf."""
    return _resolve(schema, leaf.schema.get("items", {}))
