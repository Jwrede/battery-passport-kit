# battery-passport-kit

From 18 February 2027 the EU Battery Regulation (EU) 2023/1542 requires a digital battery
passport for EV batteries, LMT batteries (e-bikes, scooters) and industrial batteries above
2 kWh. Each passport is a structured record of 85 to 120 mandatory data elements, depending on category and stage, from
the manufacturer address to capacity fade and the hazardous substances in the electrolyte.

The BatteryPass-Ready consortium publishes JSON schemas for these records. `bpkit` uses those
schemas for the practical steps:

- **template**: a CSV template with one column per data element, marked required or optional,
  to fill in a spreadsheet or export from an ERP/PLM system
- **from-csv**: one passport JSON per row. Units and other fixed values are filled in, and
  each passport is validated.
- **validate**: check passports against the schema, including dates, URIs, ISO 8601
  durations and code lists
- **report**: list which required elements are missing in which data element group. It also
  flags empty elements that the regulation makes mandatory only for some batteries.
- **view**: the part of a passport a given role may read (`public`, `authorities`,
  `repairer`, ...), based on the access annotations in the schema
- **qr**: the QR code that links the physical battery to its passport

It covers all five battery categories. Each category has two stages: `feb27` (requirements from
18 February 2027) and `full` (all requirements, including carbon footprint and supply chain due
diligence, which apply later).

## Install

```
pipx install git+https://github.com/Jwrede/battery-passport-kit
```

Python 3.10+. Dependencies: `jsonschema`, `segno`.

## Example

The repository contains `examples/ev-feb27.csv` with two fictional EV batteries. In the second
row the battery mass and the cycle life test standard are left empty on purpose.

```
$ bpkit from-csv examples/ev-feb27.csv --category ev -o passports
passports/EXC77-000001.json: valid
passports/EXC77-000002.json: 2 problem(s), see `bpkit report passports/EXC77-000002.json`

$ bpkit report passports/EXC77-000002.json
passports/EXC77-000002.json  category ev, stage feb27, data model 2.0.1
Required elements: 100/102 filled, schema problems: 2

group                                          required   optional
dppHeader                                           7/7        0/2
batteryMaterialsAndComposition                      9/9        2/3
circularityAndResourceEfficiency                  16/16       7/14
identifiersAndProductData                         15/16        1/6
performanceAndDurability                          45/46      11/38
symbolsLabelsAndDocumentationOfConformity           8/8       5/12

Missing required elements:
  batteryPassportData.identifiersAndProductData.batteryMass.gramKilogramValue
  batteryPassportData.performanceAndDurability.cycleLifeReferenceTest

Empty elements that are mandatory for some batteries (check whether they apply):
  batteryPassportData.identifiersAndProductData.warrantyPeriod
  batteryPassportData.symbolsLabelsAndDocumentationOfConformity.symbolsForCadmiumAndLead
...

$ bpkit validate --stage full passports/EXC77-000001.json
passports/EXC77-000001.json: 4 problem(s) (ev, full)
  batteryPassportData: 'batteryCarbonFootprint' is a required property
  batteryPassportData: 'supplyChainDueDiligence' is a required property
  ...

$ bpkit view passports/EXC77-000001.json --role public > public.json
$ bpkit qr https://dpp.example-cells.eu/EXC77-000001 -o EXC77-000001.svg
```

Start a new data set with a template or a complete placeholder passport:

```
bpkit categories
bpkit template --category lmt > lmt-template.csv
bpkit example --category industrial-stationary --stage full > example.json
```

## CSV format

- The header row holds element paths as in the template, for example
  `batteryPassportData.identifiersAndProductData.batterySerialNumber`.
- A second row with `required`/`optional` is allowed, as written by `bpkit template`.
- Empty cells are skipped.
- Cells for list elements hold values separated by `|`, for example `Cobalt|Lithium|Nickel`.
- Lists of objects (hazardous substances) use an index in the path. The template contains
  `[0]`. Add columns with `[1]`, `[2]` and so on for more entries.
- Numbers use a decimal point. Dates are ISO 8601 (`2027-01-12`), and durations are ISO 8601
  too (`P8Y` for eight years).
- The output file is named after `batterySerialNumber`, or after the row number when the
  serial number is empty.

## Limits

- The schemas are the consortium's proposal and are marked as work in progress. They are not an
  official EU format, and the delegated and implementing acts are still evolving. Check results
  against the current Data Attribute Longlist at https://thebatterypass.eu.
- JSON schema cannot express which elements are mandatory only under conditions, for example
  the lead and cadmium symbols. `report` lists the empty ones as items to check and does not
  decide for you.
- `bpkit` creates and checks passport data. It does not host passports or provide the
  registry, access control or data carrier required by the regulation.
- This is not legal advice.

## Data model and licence

The bundled schemas are the BatteryPass-Ready data model v2.0.1, © 2026 BatteryPass-Ready
Consortium, maintained by acatech, licensed under CC BY 4.0
(https://git.gefeg.com/bp/BatteryPassDataModel). See `src/bpkit/schemas/NOTICE.md`.

The code in this repository is MIT licensed.
