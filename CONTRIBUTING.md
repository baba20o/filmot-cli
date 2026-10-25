# Contributing to Filmot CLI

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

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.
Tests use fixtures and mocked provider calls; Filmot credentials are not needed.
For manual API use, follow [Filmot's official API documentation](https://filmot.com/api-documentation),
copy `.env.example` to `.env`, and set your own `FILMOT_API_KEY`.
Keep examples as placeholders, and use explicit request budgets for live checks.

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
