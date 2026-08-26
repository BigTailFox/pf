# PF self-search report fixtures

`pf-self-search-inline.json` is the frozen Schema 1 size baseline specified by
D014. The fixture is not currently available in this repository; the values
below identify the exact external input that must be restored. It was produced
from the PF repository with:

```console
pf search
```

Generation environment:

- PF 0.1.0
- uv 0.12.5
- ty 0.0.56
- CPython 3.10.18
- source snapshot digest
  `ccb09c63cf0fffb66aca4220a11f04c4231507b21d9b6916497696456a6e92df`
- default registry candidate source: `https://pypi.org/simple`

The inline file is qualification input only. Production `ReportStore` must not
read it. Its fixed identity is 7,682,528 bytes with SHA-256
`29dd927eea928d63a555203f35304bea1f927f5e81963bac1b163e2e209af034`.

`pf-self-search-v2.json` must be a Schema 2 report for the same generation
inputs and product result. It is also not currently available. Once both fixed
inputs have been restored, run:

```console
python scripts/qualify_report_schema.py
python scripts/qualify_report_schema.py --check
```

The qualification script verifies both files, their shared generation facts,
the hard size and entity-count gates, and records validation/merge measurements
in `docs/qualification/package-floor-v2.json`.

Until then, `scripts/qualify_report_schema.py --check` fails strictly and the
single pytest qualification case is skipped with an explicit missing-fixture
reason. The skip is not qualification evidence.
