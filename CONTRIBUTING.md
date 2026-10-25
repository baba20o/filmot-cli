# Contributing to Filmot CLI

[Quick start](README.md#quick-start) · [CLI reference](docs/CLI_REFERENCE.md) · [Configuration](docs/CONFIGURATION.md)

## Development setup

Use Python 3.9 or newer and a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/check_doc_links.py
git diff --check
```

On Windows PowerShell, activate with `.\.venv\Scripts\Activate.ps1` instead.
Tests use fixtures and mocked provider calls; Filmot credentials are not needed.
For manual API use, follow [Filmot's official API documentation](https://filmot.com/api-documentation),
copy `.env.example` to `.env`, and set your own `FILMOT_API_KEY`.
Keep examples as placeholders, and use explicit request budgets for live checks.

## Project map

This selected map covers the main implementation and documentation entry
points. Update the relevant rows when moving modules or adding a public
workflow; it is not an exhaustive file listing.

| Area | Files and responsibilities |
|---|---|
| Package and entry points | `pyproject.toml` defines dependencies, extras and the `filmot` command; `MANIFEST.in` selects source-distribution files; `requirements.txt` supports direct-Python use; `filmot/_version.py` owns the version. `main.py` and `filmot/__main__.py` enter the same CLI. |
| CLI contracts | `filmot/cli.py` registers commands; `filmot/commands/` contains command groups; `cli_support.py` and `schemas.py` define presentation and result contracts. |
| Configuration and transport | `filmot/config.py`, `environment.py` and `paths.py` load settings and locate storage. `api.py`, `api_providers.py`, `api_usage.py` and `api_contract.py` handle Filmot requests, routing, accounting and response validation. |
| Discovery and channel intelligence | `filmot/discovery.py` normalizes candidates; `discovery_search.py` and `channel_intel.py` support discovery and channel workflows exposed by `commands/discovery.py` and `commands/channel.py`. |
| Transcript acquisition | `filmot/transcript.py`, `_transcript_worker.py` and `_process.py` bound acquisition; `index_transcript.py` reads Filmot's subtitle index; `proxy_pool.py` manages proxy health; `aws_transcribe.py` provides the optional AWS fallback. |
| Metadata and saved research | `filmot/filmot_metadata.py` normalizes Filmot fields; `video_metadata.py` coordinates enrichment; `library.py` stores records and metadata lifecycle state. `passages.py`, `analysis.py`, `claims.py`, `ledger.py` and `session_context.py` support passage reading, comparison, evidence and sessions. |
| YouTube providers and channel corpus | `filmot/youtube_search.py`, `youtube_resources.py`, `youtube_comments.py` and `youtube_api_support.py` implement bounded public API reads. `channel_dl.py` resolves channels and manages uploads enumeration and corpus storage. |
| Tests and fixtures | `tests/` contains the offline regression suite, `tests/helpers/` its subprocess helpers, and `tests/fixtures/filmot_api/` sanitized recorded provider responses. |
| Maintenance and CI | `scripts/check_doc_links.py` checks local documentation targets; `scripts/record_filmot_api_fixtures.py` records explicitly requested live fixtures. `.github/workflows/ci.yml` runs Ubuntu Python 3.9/3.14 and Windows Python 3.14 checks. |

The maintained documentation entry points are:

| Document | Purpose |
|---|---|
| [README](README.md) | Product overview, installation and quick start. |
| [CLI reference](docs/CLI_REFERENCE.md) | Command syntax, workflow examples, output contracts and limits. |
| [Configuration](docs/CONFIGURATION.md) | Credentials, provider routing, storage, dependencies, AWS and proxies. |
| [Agent usage](AGENTS_README.md) and [research guide](docs/AGENTS_RESEARCH_GUIDE.md) | Agent workflows, source assessment and verification. |
| [Filmot API coverage](docs/FILMOT_API_COVERAGE.md) | Audited schema coverage, enrichment behavior and deliberate limits; separate from live-provider verification. |
| [Local passages](docs/LOCAL_PASSAGES.md) | Reading bounded time windows from saved transcripts, with timing and output limits. |
| [YouTube API behavior](docs/YOUTUBE_API.md) and [roadmap](docs/YOUTUBE_ROADMAP.md) | Shipped API boundaries and future endpoint opportunities. |
| [Next slice](docs/NEXT-SLICE.md) | Historical September implementation scope with a follow-up backlog and later audit notes; not a complete current feature inventory. |
| [Feature plan](docs/AGENT_FEATURES_PLAN.md) and [field-test log](docs/FIELD_TEST_LOG.md) | Historical plans and recorded research/test observations. |
| [Changelog](CHANGELOG.md) | Released changes and the current unreleased work. |

## Public files and local files

| Keep in Git | Keep local |
|---|---|
| `filmot/`, scripts, tests, and sanitized test fixtures | `.env` and its local variants, keys, certificates, proxy lists |
| README, guides in `docs/`, this guide, and `.env.example` | `context/`, `Insights/`, `custom_searches/`, `findings/` |
| Package metadata, license, changelog, CI configuration | `.filmot_data/`, `.filmot_cache/`, databases and their journals |
| Reviewed sample files under `tests/fixtures/` | `exports/`, generated JSON/JSONL/CSV, subtitle files and ZIP exports |
| Shared product documentation | Virtual environments, build output, test/type-check caches and editor state |

Put local exports under `exports/`, including Markdown and text exports that
cannot be ignored by extension without hiding documentation:

```bash
mkdir -p exports
filmot titles sqlite --output exports/sqlite.jsonl
filmot library context databases --output exports/context.md
```

The ignore rules deliberately allow JSON, JSONL, NDJSON, CSV, subtitle and ZIP
fixtures under `tests/fixtures/`. Sanitize fixture contents before committing;
being in that directory does not make credentials or private captures safe.
The recording helper and fixture guidance are in
[tests/fixtures/filmot_api/README.md](tests/fixtures/filmot_api/README.md).

UAF is local maintainer tooling in this checkout. `.uaf/`, root `AGENTS.md`,
`CLAUDE.md`, `checkpoint.md`, and `templates/` are ignored; contributors do not
need them to install, test, or use Filmot. `AGENTS_README.md` is the public agent
usage guide and stays tracked.

Use `git status --short` before staging. To check an ignore rule, run
`git check-ignore -v --no-index PATH`. To identify tracked files that conflict
with current ignore rules, run:

```bash
git ls-files --cached --ignored --exclude-standard
```

Ignoring a file does not untrack it or erase earlier commits. If a real credential
was committed, revoke or rotate it first, then coordinate any history cleanup
with repository collaborators. Ignore changes preserve local data; deleting
ignored files is a separate operation.

## Checking a distribution before release

Build from a clean checkout containing the intended release changes:

```bash
python -m pip install build
python -m build
python -m zipfile -l dist/filmot_cli-VERSION-py3-none-any.whl
python -m tarfile -l dist/filmot_cli-VERSION.tar.gz
```

Replace `VERSION` with the release version. Review archive contents as well as
Git status: `.gitignore` does not control Python package contents. The source
archive includes public documentation, tests and their helpers/fixtures, plus
`main.py` and `requirements.txt` for the documented direct-Python workflow.
The wheel contains the installable package and distribution metadata.

Install the built wheel in a separate environment and check `filmot --help` and
`filmot --version`. Publishing and version changes are separate release steps;
the current unreleased work is recorded in [CHANGELOG.md](CHANGELOG.md).
