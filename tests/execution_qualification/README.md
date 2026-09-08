# D036 execution qualification

The current test manifest is
[2026-09-07-d038-uv-0.12.5-v1.json](2026-09-07-d038-uv-0.12.5-v1.json), produced by
[qualify_execution_failures.py](../../scripts/qualify_execution_failures.py) using
real uv 0.12.5, ty, configured verifier, EnvironmentFactory and SearchCoordinator.
The [D036 dated evidence](2026-09-06-uv-0.12.5-v1.json) is historical and is not
loaded by current tests. The historical `tests/uv_qualification/matrix-manifest.json`
is unchanged.

Each loopback HTTPS registry serves three deterministic dependency artifacts:

- Version 1 is an sdist containing Python-2 `setup.py` syntax. A dependency-free
  in-tree backend executes that file, avoiding unrelated setuptools/network
  failures. This is a controlled legacy setup failure, not a claim to replay
  setuptools' default legacy backend or Jinja2 itself.
- The resolve fixture omits static metadata, so uv executes setup during resolve.
- The install fixture includes static Metadata-Version 2.2. Resolve succeeds;
  wheel building executes the same setup file during install.
- Versions 2 and 3 are valid wheels. The baseline is 3; after rejecting 1,
  search returns 2 with a distinct, directly executed full PASS and predecessor 1.

The application is installed through a dependency-free editable backend. TLS
uses one ephemeral certificate shared by both cases in a fresh replay process,
trusted only by that process environment; this also preserves urllib's cached
SSLContext semantics on Python 3.12. Tests execute the script in a child process.
Certificate verification and PF's HTTPS-only registry admission remain enabled.
Private keys and temporary environments are not retained. Artifact SHA-256 values
are reproducible; source, request and report identities include the actual
ephemeral registry URL and therefore vary across runs.

Run from the PF repository root, outside the agent sandbox after risk review:

```sh
PATH="$PWD/.venv/bin:$PATH" UV_CACHE_DIR=/tmp/pf-uv-cache \
  .venv/bin/python scripts/qualify_execution_failures.py \
  --output /tmp/pf-execution-qualification.json
```

`tests/test_execution_qualification.py` checks the D038 manifest and independently
replays both cases. The script requires OpenSSL and the repository's uv/ty tools.
The only server is bound to `127.0.0.1`; dependency build requirements are empty.
Process argv and syntax-error diagnostics in the evidence are observations, not
inputs to PF's disposition or Failure ID.

[The diagnostic replay](2026-09-06-uv-diagnostics.json) is a separate fresh run of
the 13 fixed-profile cases in `scripts/qualify_uv.py`. Its `pf_classification`
field describes the diagnostic matcher, not the new operation disposition.
`tests/test_prepare_execution.py` replays these complete stdout/stderr envelopes
through UvAdapter, EnvironmentFactory and FailurePolicy: the two qualified UNSAT
shapes retain typed attribution; all other shapes and incomplete envelopes use
the actual resolve/install fallback. Both UNSAT codes also round-trip through
ReportStore. The hash-mismatch fixture came from `pip sync` and is replayed only
as an install observation.
