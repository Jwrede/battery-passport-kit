import io
import json
from pathlib import Path

import pytest

from bpkit.cli import main
from bpkit.passport import (
    completeness,
    example,
    from_row,
    get_path,
    rows_from_csv,
    set_path,
    validate,
    view,
    write_template,
)
from bpkit.schema import CATEGORIES, STAGES, detect_category, leaves, load_schema, open_required_objects

EXAMPLE_CSV = Path(__file__).parent.parent / "examples" / "ev-feb27.csv"


@pytest.mark.parametrize("category", CATEGORIES)
@pytest.mark.parametrize("stage", STAGES)
def test_examples_are_valid_and_complete(category, stage):
    doc = example(category, stage)
    assert validate(doc, category, stage) == []
    rep = completeness(doc, category, stage)
    assert rep.required_missing == 0
    assert detect_category(doc) == category


def test_feb27_is_subset_of_full():
    for category in CATEGORIES:
        feb = {lf.path for lf in leaves(load_schema(category, "feb27"))}
        full = {lf.path for lf in leaves(load_schema(category, "full"))}
        assert feb <= full


def test_missing_required_element_is_reported():
    doc = example("ev", "feb27")
    del doc["batteryPassportData"]["identifiersAndProductData"]["batterySerialNumber"]
    rep = completeness(doc, "ev", "feb27")
    missing = rep.groups["identifiersAndProductData"].required_missing
    assert missing == ["batteryPassportData.identifiersAndProductData.batterySerialNumber"]
    assert any("batterySerialNumber" in p.message for p in rep.problems)


def test_open_required_object_needs_one_field():
    doc = example("ev", "feb27")
    assert "batteryPassportData.batteryMaterialsAndComposition.batteryChemistry" in open_required_objects(
        load_schema("ev", "feb27")
    )
    doc["batteryPassportData"]["batteryMaterialsAndComposition"]["batteryChemistry"] = {}
    rep = completeness(doc, "ev", "feb27")
    assert any("batteryChemistry (one of:" in m for m in rep.groups["batteryMaterialsAndComposition"].required_missing)


@pytest.mark.parametrize(
    "path,value,fragment",
    [
        ("batteryPassportData.identifiersAndProductData.manufacturingDate", "2027-13-40", "manufacturingDate"),
        ("dppHeader.lastUpdated", "yesterday", "lastUpdated"),
        ("batteryPassportData.performanceAndDurability.capacityFade.percentageValue", 140, "percentageValue"),
        ("batteryPassportData.identifiersAndProductData.batteryStatus.batteryStatusValues", "used", "batteryStatus"),
    ],
)
def test_invalid_values_fail(path, value, fragment):
    doc = example("ev", "feb27")
    set_path(doc, path, value)
    problems = validate(doc, "ev", "feb27")
    assert problems and any(fragment in p.path for p in problems)


def test_conditional_elements_are_flagged():
    rep = completeness(example("ev", "feb27"), "ev", "feb27")
    assert "batteryPassportData.identifiersAndProductData.warrantyPeriod" in rep.conditional_missing


def test_public_view_hides_restricted_elements():
    doc = example("ev", "feb27")
    public = view(doc, "ev", "public")
    assert get_path(public, "batteryPassportData.identifiersAndProductData.batterySerialNumber") is not None
    assert get_path(public, "batteryPassportData.identifiersAndProductData.uniqueManufacturerIdentifier") is None
    assert get_path(public, "dppHeader.economicOperatorId") is None
    authorities = view(doc, "ev", "authorities")
    assert get_path(authorities, "dppHeader.economicOperatorId") is not None


def test_csv_roundtrip_with_arrays_and_constants():
    rows = rows_from_csv(io.StringIO(EXAMPLE_CSV.read_text(encoding="utf-8")))
    assert len(rows) == 2
    doc = from_row(rows[0], "ev", "feb27")
    assert validate(doc, "ev", "feb27") == []
    comp = doc["batteryPassportData"]["batteryMaterialsAndComposition"]
    assert comp["criticalRawMaterials"][:2] == ["Cobalt", "Lithium"]
    assert comp["hazardousSubstancesAndTheirImpact"][0]["concentration"] == 1.2
    assert doc["batteryPassportData"]["identifiersAndProductData"]["batteryCategory"] == {
        "batteryCategoryValue": "electric vehicle battery"
    }
    assert doc["dppHeader"]["granularity"]["dppGranularityValue"] == "item"
    mass = doc["batteryPassportData"]["identifiersAndProductData"]["batteryMass"]
    assert mass == {"gramKilogramUnit": "kg", "gramKilogramValue": 478.0}


def test_csv_second_array_item_and_bad_number():
    base = {"batteryPassportData.batteryMaterialsAndComposition.hazardousSubstancesAndTheirImpact[1].chemicalName": "X"}
    doc = from_row(base, "ev", "feb27")
    items = doc["batteryPassportData"]["batteryMaterialsAndComposition"]["hazardousSubstancesAndTheirImpact"]
    assert items[1]["chemicalName"] == "X"
    with pytest.raises(ValueError, match="column"):
        from_row(
            {"batteryPassportData.identifiersAndProductData.batteryMass.gramKilogramValue": "heavy"}, "ev", "feb27"
        )
    with pytest.raises(ValueError, match="unknown column"):
        from_row({"notAField": "1"}, "ev", "feb27")


def test_template_header_matches_importer():
    buf = io.StringIO()
    write_template(buf, "lmt", "feb27")
    header, kinds = buf.getvalue().splitlines()[:2]
    assert header.startswith("dppHeader.")
    assert set(kinds.split(",")) == {"required", "optional"}
    assert from_row(dict.fromkeys(header.split(","), ""), "lmt", "feb27")


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "passports"
    assert main(["from-csv", str(EXAMPLE_CSV), "-c", "ev", "-o", str(out)]) == 0
    files = sorted(out.glob("*.json"))
    assert [f.name for f in files] == ["EXC77-000001.json", "EXC77-000002.json"]
    assert main(["validate", str(files[0])]) == 0
    assert main(["report", str(files[1])]) == 1
    report = capsys.readouterr().out
    assert "cycleLifeReferenceTest" in report and "warrantyPeriod" in report
    assert main(["view", str(files[0]), "--role", "public"]) == 0
    public = json.loads(capsys.readouterr().out)
    assert "economicOperatorId" not in public["dppHeader"]
    qr = tmp_path / "qr.svg"
    assert main(["qr", "https://dpp.example-cells.eu/EXC77-000001", "-o", str(qr)]) == 0
    assert qr.read_text().startswith("<?xml")
