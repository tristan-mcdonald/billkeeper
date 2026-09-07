# billkeeper — Roadmap

`billkeeper` is a command-line invoice generator for freelancers and self-employed people. Each user runs it from their own terminal against their own private data repo.

PyPI: `billkeeper` · License: MIT · Homepage: <https://billkeeper.money> · Repository: <https://github.com/tristan-mcdonald/billkeeper>

## Fixed decisions

- **Language and tooling:** Python 3.12+, packaged with `uv`. CLI on **Typer**, data models on **Pydantic v2**, tests with **pytest**, lint and format with **ruff**, type checking with **mypy** (strict).
- **CI:** `.github/workflows/ci.yml` on every push and pull request: `ruff check`, `ruff format --check`, `mypy`, `pytest`, across a matrix of Python 3.12 and 3.13 on `ubuntu-latest` and `macos-latest`. Pandoc and Typst are installed in CI so PDF rendering tests run for real, never mocked.
- **Release:** `.github/workflows/release.yml` triggered by pushing a tag matching `v*`: runs the CI checks, builds sdist and wheel with `uv build`, publishes to PyPI using **trusted publishing** (OIDC, no stored API token), and creates a GitHub Release whose notes are that version's `CHANGELOG.md` section. Version has a single source: `[project] version` in `pyproject.toml`.
- **Open-source hygiene:** `LICENSE` (MIT), `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md` (Keep a Changelog), issue and PR templates, `.pre-commit-config.yaml` running ruff. No personal details of the author anywhere in the repo, templates, or defaults. `[project.urls]` carries `Homepage = "https://billkeeper.money"`, `Repository`, and `Changelog`; the README links the homepage.
- **Storage:** plain-text files in a git repository. No database. The data repo is a directory the user points the tool at (default `~/billkeeper`), separate from the source repo. The tool commits to it after every mutating command, using the `git` binary via `subprocess`.
- **Data format:** one TOML file per client (`clients/<slug>.toml`), one TOML file per invoice (`invoices/<YYYY>/<number>.toml`), plus `config.toml` (issuer details, defaults, numbering scheme) and `sequence.toml` (next invoice number).
- **First run:** `billkeeper init` is interactive. It asks for the data repo path (default `~/billkeeper`), issuer details (name, address, email, bank/payment details), default currency, and numbering format, showing sensible defaults. It creates the directory, runs `git init`, writes `config.toml`, the default templates, and empty `clients/` and `invoices/`, makes an initial commit, and prints a short "next steps" message. If the directory already has a `config.toml` it says so and leaves the data repo untouched, but still records that path in the user config — running `init` against a repo you already have is how you point billkeeper at it, which is what someone who has just cloned their data repo onto a second machine needs. Every other command, when it cannot find a data repo, prints one line telling the user to run `billkeeper init`.
- **Repo location:** stored in `~/.config/billkeeper/config.toml` (respecting `XDG_CONFIG_HOME`), overridable with `--repo` or `BILLKEEPER_REPO`.
- **Numbering:** sequential, gapless, format configurable, default `INV-{year}-{seq:04d}`. Assigned only on `issue`, never on draft creation.
- **Immutability:** once an invoice is issued its file is never edited. Corrections are a new credit note or a new invoice that references the original. Drafts may be edited freely.
- **Currencies:** every client has a default currency; every invoice has exactly one currency (ISO 4217). No conversion, ever. Amounts use `decimal.Decimal`, never `float`; rounding follows the currency's minor-unit count (2 for EUR/USD/GBP, 0 for JPY, 3 for KWD, …). Rendered totals use locale-appropriate formatting and the correct symbol or code.
- **Line items:** description, quantity, unit price, optional unit label ("hour", "day", "item"). Tax is out of scope for v1, but the model carries a `tax` field that is `None` for now as the extension point.
- **PDF pipeline:** Markdown template → **Pandoc** → **Typst** (`--pdf-engine=typst`). Templates live in the data repo under `templates/`: `invoice.md` (Jinja2, produces the Markdown body) and `invoice.typ` (the Typst template passed to Pandoc via `--template`, controlling page size, margins, fonts, colours, header/footer, logo, and table styling). The user owns both files entirely; the code never overrides styling. One clean default ships, with a commented font setting so changing the typeface is obvious. Output goes to `invoices/<YYYY>/<number>.pdf` and is committed alongside the TOML.
- **Statuses:** `draft` → `issued` → `paid`, plus `void`. Status changes are recorded with a date. `issue` triggers number assignment and PDF render.

## CLI surface (v1)

```
billkeeper init [PATH]                     # interactive: set up data repo, config, default template
billkeeper client add|list|show|edit
billkeeper new --client <slug>             # create a draft, open in $EDITOR
billkeeper edit <id>                       # drafts only
billkeeper issue <id>                      # assign number, render PDF, commit
billkeeper render <id> [--open]            # re-render a PDF (idempotent for issued invoices)
billkeeper list [--status ...] [--client ...] [--year ...]
billkeeper show <id>
billkeeper mark-paid <id> [--date]
billkeeper void <id> --reason
billkeeper report outstanding|revenue [--year] [--currency]   # totals grouped per currency, never summed across
```

## Out of scope for v1

Tax/VAT calculation, currency conversion, email sending, time tracking import, recurring invoices, multi-user, any GUI or web UI.

## Decisions made in this roadmap

These were open at the level of the brief; they are settled here so no session has to invent them.

- **Package layout:** `src/` layout, package `billkeeper`. Modules: `errors.py`, `money.py`, `models.py`, `numbering.py`, `config.py`, `storage.py`, `sequence.py`, `gitrepo.py`, `render.py`, `cli/` (one module per command group), `templates/` (packaged default template files).
- **Runtime dependencies:** `typer`, `pydantic>=2`, `jinja2`, `tomli-w` (the stdlib `tomllib` reads TOML but cannot write it), `babel` (locale-aware money formatting). Nothing else.
- **Slugs:** lowercase, Unicode NFKD-normalised and stripped to ASCII, every run of non-alphanumeric characters collapsed to a single `-`, leading and trailing `-` removed, truncated to 40 characters. On collision, append `-2`, `-3`, … .
- **Draft identity:** drafts have no invoice number, so they live at `invoices/drafts/<draft-id>.toml` where `<draft-id>` is `<client-slug>-<YYYYMMDD>` plus `-2`, `-3`, … on collision. The id is carried on the invoice as `draft_id`, exactly as a client carries its own `slug`, because a collision suffix cannot be derived from the invoice again later. On `issue` the file moves to `invoices/<YYYY>/<number>.toml` and `draft_id` is cleared: an invoice is named by its number or by its draft id, never both. Any command taking `<id>` accepts an invoice number, a draft id, or an unambiguous prefix of either.
- **Model base class:** every Pydantic model derives from `models.BillkeeperModel`, which sets `extra="forbid"` and re-raises Pydantic's own validation errors as `errors.ValidationError`. A mistyped key in a hand-edited TOML file then reaches the user as one clear line rather than a Pydantic traceback, and the CLI keeps a single exception type to catch.
- **Client snapshot:** creating a draft copies the client's details into the invoice file, so an issued invoice is self-contained and immune to later client edits.
- **Sequence file:** `sequence.toml` holds a `[next]` table mapping a scope key to the next integer. The scope key is the four-digit year when `[numbering] reset = "yearly"` (the default) and the literal `all` when `reset = "never"`.
- **Immutability, precisely:** after issue, every field except `status` and `status_history` is frozen. The storage layer re-reads the file before writing and refuses any write that changes a frozen field.
- **Fonts:** the default Typst template sets **Inter** for body text and **JetBrains Mono** for monospace. Both are SIL Open Font License 1.1, which permits use, PDF embedding, and redistribution. The family name is `Inter` (the project was renamed from "Inter UI" in 2019). Font files are not bundled in the wheel for v1; the template declares a fallback stack and the README documents installing them.

## How to use this roadmap

Each session is one focused coding session that ends with a passing test suite and a commit. Copy the fenced **Prompt** block verbatim into a fresh agent session — each is self-contained and restates the decisions it touches. Tick a session's acceptance criteria as they are verified, so the next session to pick up is the first one whose boxes are still empty.

---

## Session 1: Project skeleton

**Goal** — A working, installable, lint-clean, type-clean Python package with a single `hello`-level Typer command and a green test suite.

**Depends on** — none.

**Prompt**

````text
You are starting `billkeeper`, an open-source command-line invoice generator for freelancers, published on PyPI as `billkeeper` under the MIT license. This session sets up the project skeleton only. Do not implement any invoicing logic.

Fixed decisions that apply here:
- Python 3.12+, packaged with `uv`, `src/` layout, package name `billkeeper`.
- CLI on Typer, data models on Pydantic v2 (add the dependency now, do not use it yet).
- Tests with pytest, lint and format with ruff, type checking with mypy in strict mode.
- The version has a single source: `[project] version` in `pyproject.toml`.
- No personal details of any author anywhere in the repo — no names, emails, or addresses in `pyproject.toml`, the LICENSE copyright line, or anywhere else. Use `billkeeper contributors` as the copyright holder.
- Project homepage is https://billkeeper.money.

Create:
- `pyproject.toml` using hatchling as the build backend, `requires-python = ">=3.12"`, version `0.1.0`, description, MIT license, keywords, classifiers, dependencies `typer` and `pydantic>=2`, and `[project.urls]` with `Homepage = "https://billkeeper.money"`, `Repository`, and `Changelog` (point Repository and Changelog at `https://github.com/tristan-mcdonald/billkeeper`). Add the console script entry point `billkeeper = "billkeeper.cli:main"`. Add a `[dependency-groups] dev` group with `pytest`, `mypy`, `ruff`, and `pre-commit`.
- Ruff configuration in `pyproject.toml`: line length 100, target py312, and a rule selection covering at least `E`, `F`, `I`, `UP`, `B`, `SIM`, `RUF`.
- Mypy configuration in `pyproject.toml`: `strict = true`, `python_version = "3.12"`, files `src` and `tests`.
- Pytest configuration in `pyproject.toml`: `testpaths = ["tests"]`, and treat warnings as errors.
- `src/billkeeper/__init__.py` exposing `__version__` read with `importlib.metadata.version("billkeeper")`.
- `src/billkeeper/cli/__init__.py` defining `app = typer.Typer(no_args_is_help=True, add_completion=False, help="Create, render, and track invoices.")`, a `--version` option on the app callback that prints the version and exits, one command `hello` that prints `billkeeper <version>`, and `def main() -> None` that calls `app()`.
- `src/billkeeper/py.typed`, included in the wheel.
- `LICENSE` — the MIT license text, copyright `billkeeper contributors`.
- `README.md` — a stub: name, one-sentence description, a link to https://billkeeper.money, and a "status: in development" line.
- `.pre-commit-config.yaml` running `ruff check --fix` and `ruff format` via the official `ruff-pre-commit` hooks, plus `check-toml`, `end-of-file-fixer`, and `trailing-whitespace` from `pre-commit-hooks`.
- `.gitignore` covering Python, build artefacts, `.venv`, and editor files.

Tests, in `tests/test_cli_smoke.py`, using `typer.testing.CliRunner`:
- invoking `hello` exits 0 and prints a version string;
- `--version` exits 0 and prints a version matching `\d+\.\d+\.\d+`;
- invoking with no arguments exits with a non-zero code and shows help.

Verify locally that `uv sync`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and `uv run pytest` all succeed, and that `uv run billkeeper hello` works. Initialise the git repository if it is not one already.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `uv sync` succeeds and writes a lockfile.
- [x] `uv run billkeeper hello` prints `billkeeper 0.1.0`.
- [x] `uv run billkeeper --version` prints `0.1.0`.
- [x] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and `uv run pytest` all exit 0.
- [x] `pyproject.toml`, `LICENSE`, `README.md`, `.pre-commit-config.yaml`, `.gitignore`, `src/billkeeper/__init__.py`, `src/billkeeper/cli/__init__.py`, `src/billkeeper/py.typed`, and `tests/test_cli_smoke.py` exist.
- [x] No author name, email, or address appears anywhere in the repo.

---

## Session 2: Continuous integration

**Goal** — Every push and pull request runs ruff, mypy, and pytest on the full matrix, with Pandoc and Typst installed so later PDF tests run for real.

**Depends on** — 1.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI packaged with `uv`. Add continuous integration. Do not change application code.

Fixed decisions that apply here:
- `.github/workflows/ci.yml` runs on every push and every pull request.
- It runs `ruff check`, `ruff format --check`, `mypy`, and `pytest`.
- The matrix is Python 3.12 and 3.13 across `ubuntu-latest` and `macos-latest`.
- Pandoc and Typst are installed in CI so that PDF rendering tests run for real rather than mocked. Install them now even though nothing renders yet.

Create `.github/workflows/ci.yml`:
- `name: CI`, triggers `push` and `pull_request`, plus `workflow_call` so the release workflow can reuse it later.
- Top-level `permissions: contents: read`.
- A concurrency group keyed on the workflow and ref, with `cancel-in-progress: true`.
- One job `check` with `strategy.fail-fast: false` and `strategy.matrix` over `os: [ubuntu-latest, macos-latest]` and `python-version: ["3.12", "3.13"]`, running on `${{ matrix.os }}`.
- Steps: `actions/checkout@v7`; `astral-sh/setup-uv@v10.0.1` with `enable-cache: true` (an exact pin: upstream publishes floating major tags only through v7); `uv python install ${{ matrix.python-version }}`; install Pandoc with `pandoc/actions/setup@v1` and Typst with `typst-community/setup-typst@v5`; a step running `pandoc --version` and `typst --version` so the log proves both are on PATH; `uv sync --all-extras --dev --python ${{ matrix.python-version }}`; then `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and `uv run pytest -q` as four separately named steps so a failure is easy to locate.

Also add `tests/test_environment.py` with a test asserting the running Python is 3.12 or newer, and a second test that asserts `pandoc` and `typst` resolve via `shutil.which`, marked with a `requires_tools` marker and skipped when the binaries are absent — so the suite passes on a bare laptop but exercises the check in CI. Register the `requires_tools` marker in `pyproject.toml`.

Add a CI status badge to `README.md` pointing at the workflow, using the same `tristan-mcdonald/billkeeper` slug as `pyproject.toml`.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `.github/workflows/ci.yml` exists with the four-way matrix and both Pandoc and Typst install steps.
- [x] The workflow declares `workflow_call` alongside `push` and `pull_request`.
- [x] `tests/test_environment.py` exists; the tool check skips cleanly when the binaries are absent.
- [x] `README.md` shows a CI badge.
- [x] The workflow YAML parses, and local `uv run pytest` passes.

---

## Session 3: Money and currency

**Goal** — A `Money` value type on `Decimal` with correct per-currency minor units, safe arithmetic, and locale-aware formatting. No storage, no CLI.

**Depends on** — 1.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI for invoicing. This session implements the money and currency layer only. Do not touch the CLI and do not add storage.

Fixed decisions that apply here:
- Amounts use `decimal.Decimal`, never `float`. A float must never reach a monetary value.
- Every invoice has exactly one ISO 4217 currency. There is no currency conversion anywhere in this project, ever; do not add any.
- Rounding follows the currency's minor-unit count: 2 for EUR/USD/GBP, 0 for JPY, 3 for KWD, and so on.
- Rendered amounts use locale-appropriate formatting and the correct symbol or code.
- `babel` is the approved formatting dependency; add it to `[project] dependencies`.

Create `src/billkeeper/errors.py` first: a `BillkeeperError(Exception)` base carrying a human-readable message, plus `CurrencyError`, `CurrencyMismatchError(CurrencyError)`, and `ValidationError`. Every later module raises from this hierarchy.

Create `src/billkeeper/money.py`:
- `MINOR_UNITS: dict[str, int]` listing only currencies whose minor-unit count is not 2: the 0-unit set (JPY, KRW, CLP, ISK, VND, XOF, XAF, XPF, BIF, DJF, GNF, KMF, PYG, RWF, UGX, VUV) and the 3-unit set (BHD, IQD, JOD, KWD, LYD, OMR, TND). Any other syntactically valid code defaults to 2.
- `SYMBOLS: dict[str, str]` for the common cases (at least USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD, SEK, NOK, DKK, PLN, INR, BRL, ZAR); anything absent formats with its code.
- `normalize_currency(code: str) -> str` — uppercase, strip, validate against `^[A-Z]{3}$`, raising `CurrencyError` with a clear message otherwise.
- `minor_units(code: str) -> int`.
- A frozen, hashable `Money` value type with `amount: Decimal` and `currency: str`. Accept `str | int | Decimal` on construction, reject `float` explicitly with `CurrencyError`, normalise the currency, and quantize to the currency's minor units with `ROUND_HALF_UP`. Implement `__add__`, `__sub__` (raising `CurrencyMismatchError` on differing currencies), `__mul__` by `Decimal | int` (quantizing the result), `__neg__`, ordering comparisons, and `zero(currency)` / `is_zero`.
- `format_money(value: Money, locale: str = "en_US") -> str` using `babel.numbers.format_currency`, falling back to `"{code} {amount}"` if Babel raises for an unknown locale.

Write `tests/test_money.py` covering: JPY quantizes to whole units, USD/GBP to two, KWD to three; an unknown-but-valid code defaults to two; constructing from a `float` raises; `Money("0.10","USD") + Money("0.20","USD")` is exactly `0.30`; adding USD to EUR raises `CurrencyMismatchError`; multiplication by a fractional quantity rounds half-up (`Money("10.00","USD") * Decimal("0.125")` is `1.25`, and a `.005` case rounds up); `Money` is hashable and usable as a dict key; `format_money` yields a leading `£` for GBP under `en_GB`, `¥` with no decimals for JPY, and a code-prefixed string for a currency without a symbol.

Everything must type-check under mypy strict.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `src/billkeeper/errors.py` and `src/billkeeper/money.py` exist; `babel` is a declared dependency.
- [x] `tests/test_money.py` passes, including the JPY, KWD, mismatch, half-up rounding, and float-rejection cases.
- [x] No `float` appears in any monetary code path.
- [x] `uv run mypy` and `uv run ruff check .` exit 0.

---

## Session 4: Domain models

**Goal** — Pydantic v2 models for client, line item, invoice, and status, with totals, transition rules, slug generation, and invoice-number formatting. Pure domain: no files, no CLI.

**Depends on** — 3.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI for invoicing. `src/billkeeper/money.py` already provides a `Money` type over `Decimal` with per-currency minor units, and `src/billkeeper/errors.py` provides `BillkeeperError`, `ValidationError`, `CurrencyError`, and `CurrencyMismatchError`. This session adds the domain models. Do not add file I/O, storage, or CLI code.

Fixed decisions that apply here:
- Data models are Pydantic v2.
- Amounts are `decimal.Decimal`, never `float`. Every invoice has exactly one ISO 4217 currency and there is no conversion, ever.
- Line items are: description, quantity, unit price, optional unit label ("hour", "day", "item"). Tax is out of scope for v1 but the model must leave a clear extension point: a `tax` field that is `None` for now.
- Statuses are `draft` → `issued` → `paid`, plus `void`. Status changes are recorded with a date.
- An invoice number is assigned only on issue, never on draft creation; the format is configurable with the default `INV-{year}-{seq:04d}`.
- Once an invoice is issued its content is never edited; corrections are a new credit note or a new invoice that references the original. Drafts may be edited freely.
- Slugs: lowercase, NFKD-normalised and stripped to ASCII, runs of non-alphanumerics collapsed to a single `-`, leading and trailing `-` removed, truncated to 40 characters.

Create `src/billkeeper/models.py`:
- `slugify(value: str) -> str` implementing the slug rule above; raise `ValidationError` if the result is empty.
- `InvoiceStatus(StrEnum)`: `DRAFT`, `ISSUED`, `PAID`, `VOID`.
- `StatusEvent`: `status: InvoiceStatus`, `date: datetime.date`, `reason: str | None = None`.
- `Client`: `slug`, `name`, `email: str | None`, `address: str | None` (multi-line), `contact: str | None`, `currency: str` (normalised through `money.normalize_currency`), `notes: str | None`. Validate that `slug == slugify(slug)`.
- `LineItem`: `description: str` (non-empty), `quantity: Decimal`, `unit_price: Decimal`, `unit: str | None = None`, `tax: None = None` with a docstring stating it is the v1 extension point for tax and must stay `None`. Reject `float` inputs.
- `ClientSnapshot`: the client fields copied onto an invoice at draft creation so an issued invoice is self-contained and unaffected by later client edits.
- `Invoice`: `kind: Literal["invoice", "credit_note"] = "invoice"`, `number: str | None`, `draft_id: str | None` (what names a draft's file until a number does; the two are mutually exclusive), `client: ClientSnapshot`, `currency: str`, `created: date`, `issue_date: date | None`, `due_date: date | None`, `payment_terms_days: int`, `items: list[LineItem]`, `notes: str | None`, `references: str | None` (the number of the invoice a credit note or correction refers to), `status: InvoiceStatus = DRAFT`, `status_history: list[StatusEvent] = []`.
- Configure every model with `extra="forbid"` so an unknown key in a hand-edited TOML file is an error, not silence.
- Invoice methods: `line_total(item) -> Money` (quantity × unit price, quantized to the invoice currency); `subtotal -> Money` (sum of per-line totals, each rounded before summing); `total -> Money` (equal to `subtotal` in v1, kept separate so tax can slot in); `is_editable -> bool` (true only for `DRAFT`); `transition(to: InvoiceStatus, on: date, reason: str | None = None) -> None` which appends a `StatusEvent` and enforces the legal transitions: draft→issued, issued→paid, draft/issued→void, and nothing else — anything illegal raises `ValidationError` naming both statuses. Voiding requires a reason.
- `validate_issuable() -> None`: raises `ValidationError` if there are no line items, if the invoice already has a number, or if the total is negative and `kind` is not `credit_note`.

Create `src/billkeeper/numbering.py`:
- `DEFAULT_FORMAT = "INV-{year}-{seq:04d}"`.
- `format_number(fmt: str, year: int, seq: int) -> str`.
- `validate_format(fmt: str) -> None`: the format must contain `{seq`, must use only the `year` and `seq` placeholders, must produce a filesystem-safe result (no `/`, no path separators, no whitespace), and must yield distinct strings for seq 1 and 2. Raise `ValidationError` with a message that shows the offending format.

Write `tests/test_models.py` covering: slugify on accented text ("Åsa Björk Ltd" → "asa-bjork-ltd"), on punctuation-heavy names, on over-long names, and on a name that slugifies to empty (raises); every legal and at least four illegal status transitions; void without a reason raises; per-line rounding before summing (three lines of `0.005`-style values in a 2-minor-unit currency sum the way rounded lines do, not the way raw products do); a JPY invoice totals in whole units; `validate_issuable` rejects an empty invoice and a negative-total `invoice` but accepts a negative-total `credit_note`; `extra="forbid"` rejects an unknown field; `tax` defaults to `None`. Write `tests/test_numbering.py` covering the default format at seq 1 and 9999, a usable custom format such as `{year}-{seq:03d}`, and rejection of formats with no `{seq}`, with a path separator (`{year}/{seq:03d}` renders fine but is not filename-safe, as Session 7 also notes), or with an unknown placeholder.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `src/billkeeper/models.py` and `src/billkeeper/numbering.py` exist.
- [x] `tests/test_models.py` and `tests/test_numbering.py` pass.
- [x] Illegal status transitions and unknown TOML keys both raise `ValidationError`.
- [x] `LineItem.tax` exists and is `None`.
- [x] `uv run mypy` exits 0 under strict mode.

---

## Session 5: Config and repo discovery

**Goal** — Read and write the user-level config, model the data repo's `config.toml`, and resolve the repo path from flag, environment, user config, or default — with the exact "run `billkeeper init`" error when there is none.

**Depends on** — 4.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI for invoicing. Domain models live in `src/billkeeper/models.py`, money in `src/billkeeper/money.py`, errors in `src/billkeeper/errors.py`. This session adds configuration and data-repo discovery. Do not add CLI commands or invoice storage yet.

Fixed decisions that apply here:
- The data repo is a plain-text git repository the user points the tool at; the default location is `~/billkeeper`. There is no database.
- The data repo location is stored in `~/.config/billkeeper/config.toml`, respecting `XDG_CONFIG_HOME`, and can be overridden with `--repo` or the `BILLKEEPER_REPO` environment variable.
- The data repo contains `config.toml` (issuer details, defaults, numbering scheme), `sequence.toml`, `clients/`, `invoices/`, and `templates/`.
- Every command other than `init`, when it cannot find a data repo, must print one line telling the user to run `billkeeper init`.
- Numbering format is configurable, default `INV-{year}-{seq:04d}`.
- No personal details of the author may appear in any default value.

Add `RepoNotFoundError(BillkeeperError)` and `ConfigError(BillkeeperError)` to `src/billkeeper/errors.py`. `RepoNotFoundError`'s default message is exactly: `No billkeeper data repo found. Run 'billkeeper init' to create one.`

Create `src/billkeeper/config.py`:
- `user_config_dir() -> Path`: `$XDG_CONFIG_HOME/billkeeper` when `XDG_CONFIG_HOME` is set and non-empty, otherwise `~/.config/billkeeper`.
- `user_config_path() -> Path`: that directory plus `config.toml`.
- `UserConfig` (Pydantic v2, `extra="forbid"`): `repo: Path | None`. `load_user_config()` returns an empty `UserConfig` when the file is absent, and raises `ConfigError` with the file path in the message when the TOML is malformed. `save_user_config(cfg)` creates the directory and writes `repo` as a string.
- `RepoConfig` (Pydantic v2, `extra="forbid"`) modelling the data repo's `config.toml` with three sections: `issuer` (`name`, `address`, `email`, `payment_details`, all strings, `payment_details` multi-line free text for bank/payment info), `defaults` (`currency`, normalised through `money.normalize_currency`; `locale`, default `en_US`; `payment_terms_days`, default 30), and `numbering` (`format`, default `INV-{year}-{seq:04d}`, checked with `numbering.validate_format`; `reset`, `Literal["yearly", "never"]`, default `yearly`).
- `load_repo_config(repo: Path) -> RepoConfig` and `save_repo_config(repo: Path, cfg: RepoConfig) -> None`, using stdlib `tomllib` to read and `tomli-w` to write. Add `tomli-w` to `[project] dependencies`.
- `resolve_repo(explicit: Path | None = None) -> Path`: return the first of — `explicit`; `$BILLKEEPER_REPO`; the `repo` in the user config; `~/billkeeper` — that exists and contains a `config.toml`. If an explicit path or `BILLKEEPER_REPO` is given but has no `config.toml`, raise `RepoNotFoundError` naming that path. If nothing is found at all, raise `RepoNotFoundError` with the default message. Expand `~` and resolve to an absolute path in every case.

Write `tests/test_config.py` using `tmp_path` and `monkeypatch`, never touching the real home directory: `XDG_CONFIG_HOME` is honoured and the `~/.config` fallback is used when it is unset or empty; user config round-trips; a malformed user config raises `ConfigError` naming the file; `RepoConfig` round-trips through TOML with an invalid numbering format rejected and an unknown key rejected; `resolve_repo` honours the precedence order flag > env > user config > default, with each layer tested; a repo path that exists but lacks `config.toml` raises; the no-repo-anywhere case raises `RepoNotFoundError` whose message contains `billkeeper init`.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `src/billkeeper/config.py` exists; `tomli-w` is a declared dependency.
- [x] `tests/test_config.py` passes and never reads or writes the real `$HOME`.
- [x] `resolve_repo` precedence is `--repo` > `BILLKEEPER_REPO` > user config > `~/billkeeper`.
- [x] `RepoNotFoundError` message is one line and names `billkeeper init`.

---

## Session 6: TOML storage layer

**Goal** — Read and write clients and invoices as TOML in the data repo layout, with atomic writes, id resolution, and enforcement of issued-invoice immutability.

**Depends on** — 5.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI for invoicing. Models are in `src/billkeeper/models.py`, money in `money.py`, config and `resolve_repo` in `config.py`, errors in `errors.py`. This session adds the storage layer. Do not add CLI commands, git calls, or PDF rendering.

Fixed decisions that apply here:
- Storage is plain-text files in a git repository; there is no database.
- Layout: `clients/<slug>.toml`, `invoices/<YYYY>/<number>.toml`, `config.toml`, `sequence.toml`, `templates/`.
- Drafts have no number, so they live at `invoices/drafts/<draft-id>.toml`, where `<draft-id>` is `<client-slug>-<YYYYMMDD>` with `-2`, `-3`, … appended on collision. On issue the file moves to `invoices/<YYYY>/<number>.toml`.
- `Invoice` carries its own `draft_id: str | None`, mutually exclusive with `number`, so the storage layer can find a draft's file again after a collision suffix was appended. Add it to `models.py` if it is not there yet.
- Once an invoice is issued its file is never edited. Precisely: after issue, every field except `status` and `status_history` is frozen; a write that changes any other field must be refused.
- Amounts are `Decimal` and must survive a TOML round-trip exactly — TOML has no decimal type, so write every `Decimal` as a quoted string and parse it back with `Decimal`.
- Slugs follow `models.slugify`; on collision append `-2`, `-3`, … .

Add `NotFoundError(BillkeeperError)`, `AlreadyExistsError(BillkeeperError)`, `AmbiguousIdError(BillkeeperError)`, and `ImmutableInvoiceError(BillkeeperError)` to `errors.py`.

Create `src/billkeeper/storage.py` with a `Repo` class wrapping the repo root:
- Path helpers: `clients_dir`, `invoices_dir`, `drafts_dir`, `year_dir(year)`, `templates_dir`, `client_path(slug)`, `invoice_path(invoice)` (draft → `invoices/drafts/<id>.toml`; issued → `invoices/<year of issue_date>/<number>.toml`), `pdf_path(invoice)` (the invoice path with a `.pdf` suffix).
- `_atomic_write(path, text)`: write to a temporary file in the same directory, then `os.replace`, so a crash never leaves a half-written invoice.
- Clients: `write_client(client)`, `read_client(slug)` (raises `NotFoundError` naming the slug), `list_clients()` sorted by slug, `client_exists(slug)`, and `allocate_slug(name)` which slugifies and appends `-2`, `-3`, … until free.
- Invoices: `write_invoice(invoice)`, `read_invoice_at(path)`, `list_invoices()` (drafts plus every year, sorted by number then draft id), `allocate_draft_id(client_slug, on: date)`, `delete_draft(invoice)`.
- `resolve(ident: str) -> Invoice`: match an exact invoice number in any year, then an exact draft id, then a unique case-insensitive prefix of either. No match raises `NotFoundError`; more than one raises `AmbiguousIdError` listing the candidates.
- Immutability: `write_invoice` on an invoice whose status is not `draft` re-reads the file on disk first and raises `ImmutableInvoiceError` if any field other than `status` or `status_history` differs. Provide `move_draft_to_issued(invoice)` performing the draft-file removal and the numbered write as the one legitimate transition.
- Serialisation lives here, not in the models: `to_toml_dict(invoice)` / `from_toml_dict(data)`, with dates as TOML local dates, `Decimal` as strings, and stable key ordering so a file re-written unchanged has a byte-identical result. Normalise CRLF to LF on the way in, because TOML's multi-line strings do it on the way out and the file would otherwise never settle.

Write `tests/test_storage.py` using `tmp_path`, with a fixture that builds a bare repo directory tree: client round-trip preserves accented names and multi-line addresses; `allocate_slug` handles collisions; `Decimal("1234.005")` and a quantity like `Decimal("0.125")` round-trip exactly; an invoice with a JPY currency round-trips; `allocate_draft_id` produces `<slug>-<YYYYMMDD>` and then `-2` on the same day; `resolve` finds by number, by draft id, and by unique prefix, and raises `AmbiguousIdError` for a shared prefix; writing an issued invoice with a changed line item raises `ImmutableInvoiceError` while writing one with only a changed status succeeds; re-writing an unchanged invoice produces identical bytes; a TOML file with an unknown key fails to load with a clear error.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `src/billkeeper/storage.py` exists with the `Repo` class and the layout helpers above.
- [x] `tests/test_storage.py` passes, including the Decimal round-trip and immutability cases.
- [x] Writes are atomic (temp file plus `os.replace`).
- [x] `resolve` supports number, draft id, and unique prefix, and errors clearly on ambiguity.

---

## Session 7: Sequence counter and git wrapper

**Goal** — Gapless invoice-number allocation backed by `sequence.toml`, and a thin, tested wrapper around the `git` binary used to commit after every mutating command.

**Depends on** — 6.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI for invoicing. `storage.Repo` handles TOML reads and writes, `config.RepoConfig` carries the numbering settings, `numbering.format_number` renders a number. This session adds the sequence counter and the git wrapper. Do not add CLI commands.

Fixed decisions that apply here:
- Invoice numbering is sequential and gapless. The format is configurable, default `INV-{year}-{seq:04d}`. A number is assigned only on issue, never on draft creation.
- `sequence.toml` in the data repo holds the next invoice number. Its shape is a `[next]` table mapping a scope key to the next integer. The scope key is the four-digit year when `[numbering] reset = "yearly"` (the default) and the literal `all` when `reset = "never"`.
- The tool commits to the data repo after every mutating command, using the `git` binary via `subprocess`. There is no database and no Python git library.

Create `src/billkeeper/sequence.py`:
- `scope_key(cfg: RepoConfig, year: int) -> str`.
- `peek(repo, cfg, year) -> int` — the next sequence number without consuming it; missing file or missing key means 1.
- `allocate(repo, cfg, year) -> tuple[int, str]` — returns the sequence number and the formatted invoice number, and writes the incremented counter back atomically.
- `rollback(repo, cfg, year, seq) -> None` — restores the counter to `seq` if and only if it is currently `seq + 1`, so a failed issue (for example, a PDF render that fails) leaves no gap. Raise `BillkeeperError` if the counter has moved on.
- Guard against a corrupt or non-integer counter with a clear `ConfigError`.

Create `src/billkeeper/gitrepo.py`:
- `GitError(BillkeeperError)` in `errors.py`, carrying the git stderr.
- `git_available() -> bool` and a check that raises `GitError` with an install hint when `git` is not on PATH.
- `run_git(repo: Path, *args: str) -> str` — `subprocess.run` with a list argv (never `shell=True`), `cwd=repo`, captured output, `check=False`, raising `GitError` with the command and stderr on a non-zero exit. Always pass `-c commit.gpgsign=false` so a signing setup never blocks a commit, and `-c user.name=billkeeper` or `-c user.email=billkeeper@localhost` for whichever of the two git cannot already answer with here, so the tool works in a fresh container without hijacking a configured identity — filled in one key at a time, because a machine with a name configured but no address should commit under that name.
- `init_repo(path: Path) -> None` — `git init` plus an initial empty-safe state.
- `commit(repo: Path, paths: Sequence[Path], message: str) -> str | None` — stages exactly those paths, returns `None` without committing if nothing is staged, otherwise commits and returns the short SHA.
- `is_git_repo(path) -> bool`, `head_sha(repo) -> str | None`.

Write `tests/test_sequence.py`: allocation is gapless across ten calls; the yearly scope restarts at 1 in a new year while `reset = "never"` continues; a custom format like `{year}/{seq:03d}` is rejected earlier by `validate_format` so use a valid custom one such as `ACME-{seq:05d}`; `rollback` restores the counter and refuses when the counter has already moved on; a corrupt `sequence.toml` raises `ConfigError`.

Write `tests/test_gitrepo.py` against the real `git` binary in `tmp_path`, skipped if `git` is absent: `init_repo` creates a repository; `commit` of a new file returns a SHA and `git log` shows the message; a second `commit` with no changes returns `None`; committing works when no global git identity is configured (set `HOME` and `XDG_CONFIG_HOME` to `tmp_path` and `GIT_CONFIG_GLOBAL` to a nonexistent file in the test); a git failure surfaces as `GitError` containing stderr.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `src/billkeeper/sequence.py` and `src/billkeeper/gitrepo.py` exist.
- [x] `tests/test_sequence.py` and `tests/test_gitrepo.py` pass against the real `git` binary.
- [x] Numbering is gapless, with a rollback path for failed issues.
- [x] No `shell=True` anywhere; no git library dependency added.

---

## Session 8: CLI skeleton and `init`

**Goal** — A global CLI shell with uniform error handling and a working interactive `billkeeper init` that creates and commits a fresh data repo.

**Depends on** — 7.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. Available already: `models.py`, `money.py`, `numbering.py`, `config.py` (with `resolve_repo`, `RepoConfig`, `UserConfig`), `storage.Repo`, `sequence.py`, `gitrepo.py`, `errors.py`. This session builds the CLI shell and the `init` command only. Do not implement `client`, `new`, `issue`, or rendering.

Fixed decisions that apply here:
- `billkeeper init [PATH]` is interactive, using Typer prompts. It asks for the data repo path (default `~/billkeeper`), issuer details (name, address, email, bank/payment details), default currency, and numbering format, showing sensible defaults for each. It then creates the directory, runs `git init`, writes `config.toml`, the default templates, and empty `clients/` and `invoices/`, makes an initial commit, and prints a short "next steps" message.
- If the target directory already has a `config.toml`, `init` says so and leaves the data repo untouched, but still records that path in the user config. Running `init` against an existing repo is how a user adopts one they have cloned onto another machine, so it must not be a no-op.
- The data repo location is stored in `~/.config/billkeeper/config.toml` (respecting `XDG_CONFIG_HOME`) and can be overridden with `--repo` or `BILLKEEPER_REPO`.
- Every command other than `init`, when it cannot find a data repo, must print exactly one line telling the user to run `billkeeper init`, and exit non-zero.
- Storage is plain-text files in a git repository, committed after every mutating command via the `git` binary.
- Numbering default is `INV-{year}-{seq:04d}`; the default currency and locale must contain no personal details of the author.
- Templates live in the data repo under `templates/`: `invoice.md` (Jinja2, Markdown body) and `invoice.typ` (the Typst template Pandoc receives via `--template`). The user owns both files entirely.

In `src/billkeeper/cli/__init__.py`:
- Keep `app`, the `--version` flag, and `main()`. Remove the `hello` command and its test.
- Add a global `--repo PATH` option on the app callback, and store an `AppContext` dataclass on `ctx.obj` holding the explicit repo path. Provide `get_repo(ctx) -> tuple[Repo, RepoConfig]` that calls `config.resolve_repo`, loads the repo config, and is used by every command except `init`.
- Wrap command dispatch so any `BillkeeperError` is printed to stderr as one line (no traceback) and exits with code 1; anything else keeps its traceback. Implement this as a `main()` that catches `BillkeeperError` around `app()`.
- Register the `init` command from `src/billkeeper/cli/init_cmd.py`.

Create `src/billkeeper/cli/init_cmd.py` implementing `init`:
- Optional positional `PATH`. If given, use it without prompting for the path; otherwise prompt with `~/billkeeper` as the default. The global `--repo PATH` names the data repo for every other command, so `init` honours it as the target too, `$BILLKEEPER_REPO` is offered as the prompt's default when it is set, and a `--repo` that disagrees with the positional `PATH` is an error rather than a guess.
- If `<path>/config.toml` exists, write that path to the user config, print `Already initialised: <path>/config.toml` followed by `billkeeper will now use <path>.`, and exit 0. Nothing inside the data repo is touched and no commit is made — the only write is the user config.
- Prompt for issuer name, address (multi-line accepted as a single string; state in the prompt that `\n` may be used), email, and payment details; default currency (default `USD`); locale (default derived from `$LANG` if it parses, else `en_US`); numbering format (default `INV-{year}-{seq:04d}`, validated with `numbering.validate_format` and re-prompted on error).
- Create `clients/`, `invoices/`, `invoices/drafts/`, and `templates/`, each with a `.gitkeep` where empty; write `config.toml` from a `RepoConfig`; write `sequence.toml` with an empty `[next]` table; copy the packaged default templates into `templates/`.
- Run `gitrepo.init_repo` then `gitrepo.commit` with the message `Initialise billkeeper data repo`.
- Save the repo path into the user config. This happens on both paths through the command: after a fresh `init`, and on the already-initialised exit above.
- Print a short next-steps block: add a client, create a draft, issue it.

Add the packaged default templates at `src/billkeeper/templates/invoice.md` and `src/billkeeper/templates/invoice.typ`, loaded with `importlib.resources`. Keep them minimal but valid for now — a Jinja2 Markdown body with a YAML metadata header, and a Typst Pandoc template that renders `$body$` — Session 11 replaces them with the designed default. Make sure both files are included in the built wheel.

Write `tests/test_cli_init.py` with `CliRunner`, `tmp_path`, and monkeypatched `HOME`, `XDG_CONFIG_HOME`, and `GIT_CONFIG_GLOBAL` so the real home is never touched: a scripted run through the prompts creates every expected file and directory; the repo is a git repository with exactly one commit; `config.toml` reloads into a `RepoConfig` with the entered values; the user config now points at the repo; running `init` again prints `Already initialised`, leaves the commit count at one, and leaves every file in the data repo byte-identical; running `init` against an existing repo when the user config is absent, and again when it points somewhere else, leaves it pointing at that repo in both cases; an invalid numbering format is re-prompted; and a second test module `tests/test_cli_no_repo.py` asserts that a non-`init` command with no discoverable repo prints one line containing `billkeeper init` and exits 1.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [x] `billkeeper init /tmp/somewhere` creates `config.toml`, `sequence.toml`, `clients/`, `invoices/drafts/`, `templates/invoice.md`, `templates/invoice.typ`, and one git commit.
- [x] Re-running `init` on the same path says so and leaves the data repo byte-identical, with no new commit.
- [x] The repo path is written to `$XDG_CONFIG_HOME/billkeeper/config.toml`, both by a fresh `init` and by an `init` that finds an existing repo.
- [x] A command run with no data repo prints a single line naming `billkeeper init` and exits 1.
- [x] `tests/test_cli_init.py` and `tests/test_cli_no_repo.py` pass; no test touches the real `$HOME`.

---

## Session 9: `client` commands

**Goal** — `billkeeper client add|list|show|edit`, each persisting TOML and committing to the data repo.

**Depends on** — 8.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. The CLI shell, `get_repo(ctx)`, and `init` exist; `storage.Repo` reads and writes client TOML; `gitrepo.commit` commits; `models.Client` and `models.slugify` are available. This session implements the `client` command group only.

Fixed decisions that apply here:
- One TOML file per client at `clients/<slug>.toml`.
- Slugs are lowercase, NFKD-normalised to ASCII, non-alphanumeric runs collapsed to `-`, trimmed, truncated to 40 characters, with `-2`, `-3`, … appended on collision.
- Every client has a default currency (ISO 4217), which an invoice for that client inherits. There is no currency conversion anywhere.
- The tool commits to the data repo after every mutating command.
- When no data repo can be found, print one line telling the user to run `billkeeper init` and exit non-zero.

Create `src/billkeeper/editor.py`: `open_in_editor(path: Path) -> None` using `$VISUAL`, then `$EDITOR`, then `vi`, invoked with `subprocess.run` on a list argv (never `shell=True`), raising `BillkeeperError` if the editor exits non-zero.

Create `src/billkeeper/cli/client.py` with a Typer sub-app registered as `client`:
- `add`: options `--name` (prompted if omitted), `--email`, `--address`, `--contact`, `--currency` (defaults to the repo's default currency), `--notes`, and `--slug` to override slug generation. Refuses an explicit slug that already exists with `AlreadyExistsError`; auto-generated slugs get the `-2` suffix treatment. Writes the file, commits with `Add client <slug>`, and prints the slug.
- `list`: a `rich` table with slug, name, currency, and email, sorted by slug. When there are no clients, print `No clients yet. Add one with 'billkeeper client add'.` and exit 0.
- `show <slug>`: all fields, one per line; unknown slug raises `NotFoundError` naming the slug and suggesting `billkeeper client list`.
- `edit <slug>`: opens `clients/<slug>.toml` in the editor, then re-reads and validates it. If it no longer parses or fails model validation, print the error, leave the file as the user left it, and exit 1 without committing. On success, commit with `Edit client <slug>`. If the file is unchanged, print `No changes.` and do not commit.

Write `tests/test_cli_client.py` with `CliRunner` and a `repo` fixture that runs `init` non-interactively into `tmp_path` (factor that fixture into `tests/conftest.py` so later sessions reuse it; it must monkeypatch `HOME`, `XDG_CONFIG_HOME`, and `GIT_CONFIG_GLOBAL`). Cover: `client add --name "Åsa Björk Ltd"` creates `clients/asa-bjork-ltd.toml` and one new commit; a second client with the same name gets `-2`; an explicit duplicate `--slug` fails with exit code 1 and no new commit; `client list` shows both and the empty-state message on a fresh repo; `client show` prints the fields and fails clearly for an unknown slug; `client edit` with a fake `$EDITOR` script that rewrites the name commits the change, and with an editor that writes invalid TOML exits 1 without committing; an unchanged edit says `No changes.`.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `billkeeper client add|list|show|edit` all work against a real data repo.
- [ ] Each mutating command produces exactly one git commit with a descriptive message.
- [ ] Slug collisions resolve to `-2`, `-3`; explicit duplicates are refused.
- [ ] Invalid TOML from `client edit` exits 1 and commits nothing.
- [ ] `tests/conftest.py` exposes a reusable initialised-repo fixture.

---

## Session 10: `new` and `edit` drafts

**Goal** — Create a draft invoice from a client, open it in `$EDITOR`, validate it, and commit — plus `billkeeper edit <id>` for drafts only.

**Depends on** — 9.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. `storage.Repo` handles invoice TOML, id resolution, draft ids, and immutability; `models.Invoice` carries line items, statuses, and totals; `editor.open_in_editor` opens a file; `gitrepo.commit` commits. This session implements `billkeeper new` and `billkeeper edit`.

Fixed decisions that apply here:
- `billkeeper new --client <slug>` creates a draft and opens it in `$EDITOR`.
- A draft has no invoice number. Numbers are assigned only on issue. Drafts live at `invoices/drafts/<draft-id>.toml`, where `<draft-id>` is `<client-slug>-<YYYYMMDD>` with `-2`, `-3`, … on collision.
- Creating a draft copies the client's details into the invoice file, so an issued invoice is self-contained and unaffected by later client edits.
- Every invoice has exactly one currency, inherited from the client, and there is no conversion. Amounts are `Decimal` written as quoted strings in TOML.
- Line items are description, quantity, unit price, and an optional unit label ("hour", "day", "item"). `tax` exists on the model, is `None`, and is out of scope for v1.
- `billkeeper edit <id>` edits drafts only. An issued, paid, or void invoice is never edited; corrections are a new credit note or a new invoice referencing the original.
- `<id>` accepts an invoice number, a draft id, or an unambiguous prefix of either.
- The tool commits to the data repo after every mutating command.

Create `src/billkeeper/cli/draft.py` and register both commands on the root app:
- `new`: `--client <slug>` (required), `--currency` to override the client's default, `--date` for the creation date (default today), `--notes`, and `--no-edit` to skip opening the editor (used by tests and scripts). It builds an `Invoice` with the client snapshot, `status=draft`, no number, `payment_terms_days` from the repo config, and exactly one example line item (`description = "Describe the work"`, `quantity = "1"`, `unit_price = "0.00"`, `unit = "hour"`) so the file is valid and obvious to edit. It writes the file, opens it in the editor unless `--no-edit`, re-reads and validates, prints the draft id and the current total, and commits with `Create draft <draft-id>`.
- `edit <id>`: resolves the id, refuses anything not `draft` with a message naming the status, for example `INV-2026-0001 is issued and cannot be edited. Create a credit note or a new invoice referencing it.`; otherwise opens the file, re-validates, prints the new total, and commits with `Edit draft <draft-id>` — or `No changes.` without a commit if the bytes are unchanged.
- Shared behaviour for both: if the edited file fails to parse or validate, print the underlying error with the file path, leave the user's text on disk so nothing is lost, and exit 1 without committing.

Write `tests/test_cli_draft.py` using the initialised-repo fixture from `tests/conftest.py`: `new --client <slug> --no-edit` creates `invoices/drafts/<slug>-<YYYYMMDD>.toml` with the client snapshot, the client's currency, no number, and one commit; a second draft the same day gets `-2`; `new` for an unknown client exits 1 naming the slug; `new` with a `$EDITOR` script that writes three real line items reports the correct total, including a fractional quantity such as `2.5` hours; `edit` on a draft with a scripted editor commits and reports the new total; `edit` on an issued invoice (construct one directly through `storage`) exits 1 with the "cannot be edited" message and no commit; an editor that writes invalid TOML exits 1, leaves the bad file in place, and creates no commit; `edit` on a unique id prefix resolves, and on an ambiguous prefix lists the candidates.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `billkeeper new --client acme --no-edit` writes a valid draft with a client snapshot and no number.
- [ ] Draft ids collide safely with `-2` on the same day.
- [ ] `billkeeper edit <id>` works on drafts and refuses every other status with an actionable message.
- [ ] Invalid edits exit 1, preserve the user's file, and create no commit.
- [ ] `tests/test_cli_draft.py` passes.

---

## Session 11: PDF pipeline

**Goal** — Render an invoice to PDF through Jinja2 → Markdown → Pandoc → Typst, with a designed default template pair, a clear check that `pandoc` and `typst` are on `PATH`, and byte-stable output.

**Depends on** — 10.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. Models, storage, config, and the draft commands exist. This session implements PDF rendering and ships the designed default templates. Do not wire it into `issue` yet — that is the next session — but expose a clean function the CLI can call.

Fixed decisions that apply here:
- The pipeline is: Markdown template → Pandoc → Typst, invoked as `pandoc --pdf-engine=typst`.
- Templates live in the data repo under `templates/`: `invoice.md` is a Jinja2 template producing the Markdown body, and `invoice.typ` is the Typst template passed to Pandoc via `--template`, controlling page size, margins, fonts, colours, header/footer, logo, and table styling.
- The user owns both files entirely. The code never overrides styling, never injects CSS or Typst directives, and never rewrites the user's templates after `init`.
- Output goes to `invoices/<YYYY>/<number>.pdf`, alongside the TOML.
- Amounts are `Decimal`, formatted for the repo's configured locale with the correct symbol or code, rounded to the currency's minor units. There is no currency conversion.
- Typefaces for the shipped default: body text **Inter**, monospace **JetBrains Mono**. Both are SIL Open Font License 1.1, which permits use, embedding in PDFs, and redistribution. Do not bundle the font files; declare fallbacks so rendering still succeeds when they are not installed.
- `jinja2` is the approved templating dependency; add it to `[project] dependencies`.

Create `src/billkeeper/render.py`:
- `MissingToolError(BillkeeperError)` in `errors.py`.
- `check_tools() -> None`: uses `shutil.which` for `pandoc` and `typst` and raises `MissingToolError` naming every missing binary with install hints — `brew install pandoc typst` on macOS, `apt install pandoc` plus a pointer to the Typst releases page on Linux — in one short message.
- `build_context(invoice, cfg) -> dict[str, object]`: issuer, client, invoice number or `None`, dates, currency, per-line description/quantity/unit/unit price/line total as preformatted strings via `money.format_money` with the configured locale, the subtotal and total, payment details, notes, and a `draft` boolean.
- `render_markdown(repo, invoice, cfg) -> str`: Jinja2 `Environment` with `StrictUndefined`, `keep_trailing_newline=True`, and a `FileSystemLoader` rooted at the repo's `templates/`, so an undefined variable is a loud error rather than a blank in a PDF.
- `render_pdf(repo, invoice, cfg, out_path: Path) -> Path`: writes the Markdown to a temporary file, runs `pandoc <md> --from=markdown --pdf-engine=typst --template=<repo>/templates/invoice.typ --output=<out_path>` with a list argv and no shell, and raises `BillkeeperError` carrying Pandoc's stderr on failure. Set `SOURCE_DATE_EPOCH` in the subprocess environment from the invoice's issue date (or its creation date for a draft) so re-rendering the same invoice produces identical bytes.

Replace the placeholder packaged templates:
- `src/billkeeper/templates/invoice.md` — Jinja2 producing a YAML metadata block (invoice number or the string `DRAFT`, issue and due dates, issuer fields, client fields, currency, the line items as a list, subtotal, total, payment details, notes) followed by the Markdown body. Everything the Typst template needs must reach it as Pandoc metadata.
- `src/billkeeper/templates/invoice.typ` — a Pandoc Typst template rendering `$body$` inside a clean A4 layout: issuer block, client block, invoice number and dates, a line-item table with right-aligned amounts, a total row, payment details, and a footer. Put the font configuration at the very top under a comment banner explaining how to change it, as a single obvious pair of lines, for example `#let body-font = ("Inter", "Helvetica Neue", "Arial")` and `#let mono-font = ("JetBrains Mono", "DejaVu Sans Mono")`, applied with `#set text(font: body-font)` and `#show raw: set text(font: mono-font)`. Add a comment noting that Inter and JetBrains Mono are SIL OFL 1.1, that Typst resolves them from installed system fonts, and that the later entries are fallbacks used when they are absent. Show `DRAFT` prominently where the number would be when the invoice has no number.

Write `tests/test_render.py`, marked `requires_tools` and skipped when `pandoc` or `typst` is missing: rendering a two-line issued invoice writes a file starting with `%PDF-` and larger than 1 KB; rendering the same invoice twice produces byte-identical files; a draft renders with `DRAFT` in the Markdown and no invoice number; a JPY invoice renders whole-unit amounts and a GBP invoice under `en_GB` renders a `£`; an undefined variable in a hand-broken template raises rather than rendering a blank; `check_tools` raises `MissingToolError` naming both binaries when `shutil.which` is monkeypatched to return `None`, and that test is not skipped. Add a Markdown-only test that runs without the binaries.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `src/billkeeper/render.py` exists; `jinja2` is a declared dependency.
- [ ] The shipped `invoice.typ` sets Inter and JetBrains Mono with fallbacks, under a comment explaining how to change the typeface and noting the OFL 1.1 licence.
- [ ] A real `pandoc --pdf-engine=typst` run produces a valid PDF; two runs are byte-identical.
- [ ] `check_tools` names every missing binary with an install hint, and that test runs everywhere.
- [ ] `tests/test_render.py` passes locally and in CI, where Pandoc and Typst are installed.

---

## Session 12: `issue` and `render`

**Goal** — `billkeeper issue <id>` assigns the next number, renders the PDF, and commits both files atomically in effect; `billkeeper render <id> [--open]` re-renders idempotently.

**Depends on** — 11.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. `sequence.allocate`/`rollback` manage the gapless counter, `render.render_pdf` produces the PDF, `storage.Repo` handles files and immutability, `gitrepo.commit` commits. This session implements `issue` and `render`.

Fixed decisions that apply here:
- Invoice numbering is sequential and gapless, format configurable, default `INV-{year}-{seq:04d}`. A number is assigned only on issue, never on draft creation.
- `issue` triggers number assignment and PDF render, and the status becomes `issued` with the date recorded.
- Once an invoice is issued, its file is never edited: after issue, every field except `status` and `status_history` is frozen.
- Drafts live at `invoices/drafts/<draft-id>.toml`; an issued invoice lives at `invoices/<YYYY>/<number>.toml` with its PDF at `invoices/<YYYY>/<number>.pdf`, committed alongside the TOML.
- `render <id> [--open]` re-renders a PDF and is idempotent for issued invoices.
- The tool commits to the data repo after every mutating command.
- `<id>` accepts an invoice number, a draft id, or an unambiguous prefix.

Add `src/billkeeper/clock.py` with `today() -> date`, and use it everywhere a command needs the current date, so tests can freeze time by monkeypatching one function.

Create `src/billkeeper/cli/issue.py`:
- `issue <id>`: resolve the id; refuse anything that is not a draft with a message naming the current status; call `invoice.validate_issuable()`; allocate the number for the year of `clock.today()`; set `issue_date` to today and `due_date` to today plus `payment_terms_days`; transition to `issued`; move the draft file to `invoices/<YYYY>/<number>.toml`; render the PDF to `invoices/<YYYY>/<number>.pdf`; commit the TOML, the PDF, and `sequence.toml` together with the message `Issue invoice <number>`; print the number, the total, and the PDF path.
- Failure handling is the point of this session: if rendering or writing fails at any step after the number was allocated, roll the sequence counter back with `sequence.rollback`, restore the draft file at its original path, remove any partial output, and re-raise — so a failed issue leaves no gap in the numbering and no half-issued invoice on disk. Wrap this in a `try`/`except` that is explicit about what it undoes.
- `render <id> [--open]`: resolve the id, render to the invoice's PDF path, and commit with `Render <id>` only when the PDF bytes actually changed; otherwise print `PDF is up to date.` and do not commit. `--open` opens the resulting file with `typer.launch`. Rendering a draft writes to `invoices/drafts/<draft-id>.pdf`.

Write `tests/test_cli_issue.py` using the initialised-repo fixture, with `clock.today` monkeypatched to a fixed date and marked `requires_tools` where a real PDF is needed: issuing the first draft yields `INV-<year>-0001`, moves the file, writes the PDF, leaves no file under `invoices/drafts/`, and creates one commit containing all three paths; the second issue yields `-0002`; `sequence.toml` advances by exactly one each time; issuing a draft with no line items exits 1 and does not advance the counter; a monkeypatched `render_pdf` that raises leaves the draft in place, the counter unchanged, and no new commit; issuing an already-issued invoice exits 1; `render` on an issued invoice twice produces byte-identical PDFs and the second run commits nothing; `render --open` does not fail when `typer.launch` is monkeypatched.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `billkeeper issue <draft-id>` produces `invoices/<YYYY>/<number>.toml` and `.pdf` and one commit.
- [ ] A failed render rolls the counter back, restores the draft, and commits nothing.
- [ ] Numbering stays gapless across success and failure cases.
- [ ] `billkeeper render <id>` is idempotent for issued invoices and skips the commit when nothing changed.
- [ ] `tests/test_cli_issue.py` passes.

---

## Session 13: `list`, `show`, `mark-paid`, `void`

**Goal** — Read-only listing and detail views, plus the two remaining status transitions, each recorded with a date and committed.

**Depends on** — 12.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. `storage.Repo.list_invoices` and `.resolve` are available, `models.Invoice.transition` enforces the legal status changes, `money.format_money` formats amounts for a locale. This session implements `list`, `show`, `mark-paid`, and `void`.

Fixed decisions that apply here:
- Statuses are `draft` → `issued` → `paid`, plus `void`. Every status change is recorded with a date. Voiding requires a reason.
- After issue, every field except `status` and `status_history` is frozen; these two commands are the only writes allowed on an issued invoice, and a voided invoice keeps its number so the sequence stays gapless.
- Every invoice has exactly one currency and amounts are never converted or summed across currencies.
- Amounts render with locale-appropriate formatting and the correct symbol or code, using the repo's configured locale.
- The tool commits to the data repo after every mutating command.
- `<id>` accepts an invoice number, a draft id, or an unambiguous prefix.

Create `src/billkeeper/cli/query.py` for the read commands:
- `list [--status ...] [--client ...] [--year ...]`: `--status` is repeatable and validated against the status enum; `--client` filters on the client slug; `--year` filters on the issue year for issued/paid/void invoices and the creation year for drafts. Output a `rich` table with columns id (number, or draft id), client, date, status, and total — the total formatted in that invoice's own currency. Sort by date, then id. Print `No invoices match.` on an empty result and exit 0. Never print a combined total in this command.
- `show <id>`: issuer and client blocks, number or `DRAFT`, dates, currency, a line-item table with quantity, unit, unit price, and line total, then subtotal and total, notes, the PDF path if the file exists, and the status history as `<status> on <date>` with the reason where present.

Create `src/billkeeper/cli/status.py`:
- `mark-paid <id> [--date]`: `--date` accepts an ISO date and defaults to `clock.today()`. Only an `issued` invoice can be marked paid; anything else exits 1 with a message naming the current status. Transition, write, commit with `Mark <number> paid`, and print a confirmation with the amount.
- `void <id> --reason <text>`: `--reason` is required and non-empty. A `draft` or `issued` invoice may be voided; `paid` and already-`void` exit 1. Transition, write, commit with `Void <id>: <reason>`, and confirm. A voided issued invoice keeps its number and its PDF.

Write `tests/test_cli_query.py` and `tests/test_cli_status.py` on the initialised-repo fixture with a frozen clock, building a fixture set of at least five invoices across two clients, two currencies, two years, and all four statuses: `list` with no filters shows them all; each filter narrows correctly and filters combine; an invalid `--status` exits 1 listing the valid values; the empty case prints the message; totals appear in each invoice's own currency and no cross-currency total is printed; `show` renders a draft and an issued invoice, including the status history and the reason on a voided one; `mark-paid` on an issued invoice records the date, commits once, and is refused on a draft, on a void, and on an already-paid invoice; `--date 2026-02-01` is honoured and an unparseable date exits 1; `void --reason` works on a draft and on an issued invoice, keeps the number and PDF for the latter, and is refused for a paid invoice; `void` without a reason exits non-zero; the frozen-field guard still holds — none of these commands alters a line item.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `billkeeper list` and its three filters work and combine.
- [ ] `billkeeper show <id>` displays line items, totals, PDF path, and status history.
- [ ] `mark-paid` and `void` enforce the legal transitions, record dates, and commit once each.
- [ ] A voided issued invoice keeps its number and PDF.
- [ ] No command sums amounts across currencies.
- [ ] `tests/test_cli_query.py` and `tests/test_cli_status.py` pass.

---

## Session 14: `report`

**Goal** — `billkeeper report outstanding` and `billkeeper report revenue`, grouped per currency and never summed across.

**Depends on** — 13.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. Invoices are read through `storage.Repo.list_invoices`, amounts are `Money` values formatted with `money.format_money`. This session implements the `report` command group.

Fixed decisions that apply here:
- `billkeeper report outstanding|revenue [--year] [--currency]`, with totals grouped per currency and never summed across currencies. There is no currency conversion anywhere in this project.
- Amounts are `Decimal`, rounded to each currency's minor units, formatted for the repo's configured locale with the correct symbol or code.
- Statuses are `draft`, `issued`, `paid`, `void`.

Decide and implement these definitions, and state them in the command help text:
- `outstanding` counts invoices with status `issued` (that is, issued and not yet paid, excluding drafts and voids), grouped by currency, reporting count and total. `--year` filters on the issue year. Additionally flag overdue invoices — those whose `due_date` is before `clock.today()` — as a separate count and total within each currency group.
- `revenue` counts invoices with status `paid`, grouped by currency, reporting count and total. `--year` filters on the year of the payment date taken from the `paid` entry in `status_history`, not the issue date, since that is the year the money arrived.
- `--currency` restricts output to one currency, validated with `money.normalize_currency`.

Create `src/billkeeper/reports.py` with pure functions that take a list of invoices and return `dict[str, CurrencyTotals]` keyed by currency code, where `CurrencyTotals` holds count, total `Money`, and — for outstanding — overdue count and overdue total. No printing, no I/O, so the logic is testable on its own.

Create `src/billkeeper/cli/report.py` with a `report` sub-app calling those functions and printing one `rich` table per currency, each headed with the currency code, sorted by code. Print `Nothing outstanding.` or `No revenue recorded.` for an empty result and exit 0. If more than one currency appears, print a one-line footer stating that totals are reported per currency and never combined.

Write `tests/test_reports.py` against the pure functions, and `tests/test_cli_report.py` against the CLI on the initialised-repo fixture with a frozen clock. Cover: a mixed set of GBP, USD, and JPY invoices across statuses reports each currency separately, with JPY totals in whole units; drafts and voids are excluded from both reports; overdue is computed against the frozen today and a due date one day either side of it; `--year` on `outstanding` filters by issue year, and on `revenue` by payment year — include an invoice issued in one year and paid in the next, and assert it lands in the payment year; `--currency GBP` restricts the output; `--currency XXQ` exits 1; the empty cases print the right message; and assert explicitly that no output line combines two currencies into one number.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `billkeeper report outstanding` and `billkeeper report revenue` work with `--year` and `--currency`.
- [ ] Totals are grouped per currency and never summed across; the help text states the definitions.
- [ ] `revenue --year` filters on the payment year; `outstanding --year` on the issue year.
- [ ] Overdue counts and totals appear within each outstanding currency group.
- [ ] `tests/test_reports.py` and `tests/test_cli_report.py` pass.

---

## Session 15: End-to-end lifecycle test

**Goal** — One test that drives the real CLI through a complete invoice lifecycle in a temporary git repo, with real `git`, `pandoc`, and `typst`, asserting file layout, commit history, and PDF output.

**Depends on** — 14.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing. Every v1 command is implemented. This session adds an end-to-end test. Fix any bugs it exposes, but do not add features.

Fixed decisions the test must verify:
- The data repo is a git repository of plain-text files: `config.toml`, `sequence.toml`, `clients/<slug>.toml`, `invoices/<YYYY>/<number>.toml`, `invoices/drafts/<draft-id>.toml`, `templates/invoice.md`, `templates/invoice.typ`. No database.
- The tool commits to the data repo after every mutating command.
- Numbering is sequential and gapless, default `INV-{year}-{seq:04d}`, assigned only on issue.
- Once issued, an invoice file is never edited except for `status` and `status_history`.
- The PDF pipeline is Markdown → Pandoc → Typst, output at `invoices/<YYYY>/<number>.pdf`, committed alongside the TOML.
- Statuses are `draft` → `issued` → `paid`, plus `void`, each change recorded with a date.
- Every invoice has one currency; reports group per currency and never sum across.

Write `tests/test_e2e_lifecycle.py`:
- Marked `requires_tools`, skipped when `pandoc`, `typst`, or `git` is missing, and run with `HOME`, `XDG_CONFIG_HOME`, and `GIT_CONFIG_GLOBAL` pointed into `tmp_path` so the real environment is untouched, `clock.today` frozen, and `$EDITOR` set to a small Python script that overwrites the draft with fixed content.
- Drive the CLI through `typer.testing.CliRunner` only — no direct calls into `storage` or `models`. The test must read like a user session.
- The walk: `init` into `tmp_path/data` with scripted prompt input; `client add` two clients with different currencies; `new` a draft for the first client, edited via the scripted editor into three line items including a fractional quantity; `list` shows one draft; `issue` it; `show` it; `render --open` with `typer.launch` monkeypatched; `mark-paid`; `new` and `issue` a second invoice for the second client in its own currency; `new` and `void` a third; `report outstanding` and `report revenue`; `list --status paid` and `list --year`.
- Assert along the way: every expected file exists at the expected path; the numbers are `INV-<year>-0001` and `-0002` with no gap; `git log --oneline` in the data repo has one commit per mutating command and none for the read-only ones; `git status --porcelain` is empty at the end, proving nothing was left uncommitted; each PDF begins with `%PDF-` and exceeds 1 KB; the issued TOML's line items are byte-identical before and after `mark-paid`; the two reports show each currency separately with the correct totals and never a combined figure.
- Keep the assertions on observable outputs and files, not on internal call counts.

If the test exposes a defect anywhere in the codebase, fix the defect rather than weakening the assertion, and note the fix in the commit message.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `tests/test_e2e_lifecycle.py` exists and passes locally with the tools installed, and in CI.
- [ ] The test drives only the CLI, in a temporary repo, with a frozen clock and a scripted editor.
- [ ] It asserts file layout, gapless numbering, one commit per mutating command, a clean final `git status`, real PDF output, post-issue immutability, and per-currency reporting.
- [ ] Any defects it exposed are fixed rather than asserted around.

---

## Session 16: Polish

**Goal** — Documentation, contributor files, templates, and a pass over every `--help` string and error message so the tool is pleasant for a first-time user.

**Depends on** — 15.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ Typer CLI for invoicing, feature-complete for v1. This session is documentation and user-experience polish. Do not add features or change behaviour except where an error message or help string is wrong.

Fixed decisions that apply here:
- MIT licensed, developed in the open on GitHub, published on PyPI as `billkeeper`.
- Project homepage is https://billkeeper.money, set as `Homepage` in `[project.urls]` alongside `Repository` and `Changelog`, and linked from the README.
- No personal details of the author anywhere in the repo, templates, or defaults.
- Open-source hygiene files: `LICENSE`, `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, issue and PR templates, `.pre-commit-config.yaml` running ruff.
- The data repo is plain-text files in git, default `~/billkeeper`, overridable with `--repo` or `BILLKEEPER_REPO`, and its location is remembered in `~/.config/billkeeper/config.toml` (respecting `XDG_CONFIG_HOME`).
- The PDF pipeline is Markdown → Pandoc → Typst; the user owns `templates/invoice.md` and `templates/invoice.typ` entirely and the code never overrides styling. The default template uses Inter for body text and JetBrains Mono for monospace, both SIL OFL 1.1, resolved from installed system fonts with fallbacks; the fonts are not bundled.
- Tax, currency conversion, email sending, time tracking import, recurring invoices, multi-user, and any GUI or web UI are out of scope for v1.

Rewrite `README.md` with: a one-paragraph description; the homepage link; install instructions covering `uv tool install billkeeper` and `pipx install billkeeper`; the prerequisites Pandoc, Typst, and git, with install lines for macOS and Debian/Ubuntu; a short quickstart running `init`, `client add`, `new`, `issue`; the full command reference matching the actual `--help` output; a data-layout section showing the file tree; a customising-the-invoice section explaining the two templates, where to change the fonts, that Inter and JetBrains Mono are OFL 1.1 and how to install them, and what happens when they are absent; a "your data is yours" section noting it is plain TOML in a git repository you own; an explicit out-of-scope list; and the licence. Keep the CI badge.

Write `CONTRIBUTING.md`: development setup with `uv sync`, running `uv run pytest`, `uv run ruff check .`, `uv run ruff format`, `uv run mypy`, installing the pre-commit hooks, how to run the tests that need Pandoc and Typst, the commit and pull-request expectations, and a note that every change should keep CI green on all four matrix legs.

Add `.github/ISSUE_TEMPLATE/bug_report.yml`, `.github/ISSUE_TEMPLATE/feature_request.yml`, `.github/ISSUE_TEMPLATE/config.yml`, and `.github/PULL_REQUEST_TEMPLATE.md`. The bug template must ask for the billkeeper version, Python version, operating system, and Pandoc and Typst versions, and must ask the reporter not to paste real client data. No author name or email anywhere.

Review and fix every user-facing string: each command and option has a one-line help string that reads as an instruction; `billkeeper --help` groups the commands sensibly; every `BillkeeperError` message is one line, says what went wrong, names the file, slug, or id involved, and suggests the next command where one exists. Replace anything vague such as "invalid input".

Add `tests/test_help_and_errors.py`: walk the Typer app and assert every command and every option has a non-empty help string; assert `--help` exits 0 for the app and for each command and sub-app; assert a representative set of failure paths — unknown client, unknown id, ambiguous id, editing an issued invoice, no data repo, missing render tools — each print exactly one line to stderr, exit 1, and produce no traceback.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `README.md` covers install (`uv tool install` and `pipx install`), prerequisites, quickstart, command reference, data layout, template and font customisation, and out-of-scope items, and links https://billkeeper.money.
- [ ] `CONTRIBUTING.md` and all four GitHub templates exist, with no author personal details.
- [ ] Every command and option has help text; `--help` exits 0 everywhere.
- [ ] Every checked error path prints one line, exits 1, and shows no traceback.
- [ ] `tests/test_help_and_errors.py` passes.

---

## Session 17: Release

**Goal** — A tag-triggered release workflow publishing to PyPI by trusted publishing, a `0.1.0` changelog entry that becomes the GitHub Release notes, and a verified dry-run build.

**Depends on** — 16.

**Prompt**

````text
You are working in the `billkeeper` repository, a Python 3.12+ CLI packaged with `uv` and published on PyPI as `billkeeper` under the MIT license. This session sets up the release pipeline. Do not change application behaviour.

Fixed decisions that apply here:
- `.github/workflows/release.yml` is triggered by pushing a tag matching `v*`. It runs the CI checks, builds an sdist and a wheel with `uv build`, publishes to PyPI using trusted publishing (OIDC, no stored API token), and creates a GitHub Release whose notes are the `CHANGELOG.md` section for that version.
- The version has a single source: `[project] version` in `pyproject.toml`.
- `CHANGELOG.md` follows Keep a Changelog.
- `[project.urls]` carries `Homepage = "https://billkeeper.money"`, `Repository`, and `Changelog`.
- `.github/workflows/ci.yml` already exists and already supports `workflow_call`.

Create `CHANGELOG.md` in Keep a Changelog format with an `Unreleased` section and a `[0.1.0]` section dated today, describing the v1 feature set: interactive `init`, client management, drafts, gapless issue numbering, Pandoc/Typst PDF rendering with user-owned templates, statuses with dates, per-currency reporting, and plain-TOML-in-git storage. Note explicitly that tax, currency conversion, and email sending are out of scope for this release. Add link definitions at the bottom for the version and the comparison range.

Create `scripts/changelog_section.py`: a dependency-free script taking a version like `0.1.0` and printing that section's body from `CHANGELOG.md`, exiting non-zero with a clear message when the section is absent. Test it in `tests/test_changelog_section.py` with a fixture changelog: it extracts the right body, stops at the next heading, handles the newest and oldest sections, and fails on a missing version. Also assert that the version in `pyproject.toml` has a section in `CHANGELOG.md`.

Create `.github/workflows/release.yml`:
- Trigger `push` on tags `v*`; top-level `permissions: contents: read`.
- Job `checks` that reuses the CI workflow with `uses: ./.github/workflows/ci.yml`.
- Job `build`, needing `checks`: checkout, `astral-sh/setup-uv@v10.0.1`, a step that verifies the pushed tag equals `v` plus the `[project] version` in `pyproject.toml` and fails with a clear message otherwise, `uv build`, then upload `dist/` with `actions/upload-artifact@v7`.
- Job `publish`, needing `build`, with `permissions: id-token: write`, `environment: pypi`, downloading the artifact and calling `pypa/gh-action-pypi-publish@release/v1` with no username or password — trusted publishing only. Do not add a PyPI token anywhere, and do not add a fallback token path.
- Job `github-release`, needing `publish`, with `permissions: contents: write`: checkout, extract the notes with `python scripts/changelog_section.py "${GITHUB_REF_NAME#v}"`, and create the release with `gh release create` attaching the built artifacts.

Add a `Releasing` section to `CONTRIBUTING.md`: bump `[project] version`, move `Unreleased` entries into the new version section with today's date, commit, tag with `git tag v0.1.0 && git push origin v0.1.0`, and the one-time PyPI setup — create a trusted publisher for the `billkeeper` project pointing at this repository, the workflow file `release.yml`, and the `pypi` environment, and create that environment in the repository settings.

Confirm every GitHub URL in `pyproject.toml`, `README.md`, and the workflows uses the real repository slug from `git remote get-url origin` (`tristan-mcdonald/billkeeper`), and that no `OWNER` placeholder has crept back in.

Verify the build locally: run `uv build`, confirm `dist/` contains both an sdist and a wheel with version `0.1.0`, install the wheel into a throwaway environment, and confirm `billkeeper --version` and `billkeeper --help` work from it. Confirm `python scripts/changelog_section.py 0.1.0` prints the release notes. Do not push a tag.

Run the full test suite, make sure it passes, and commit with a descriptive message.
````

**Acceptance criteria**

- [ ] `.github/workflows/release.yml` exists with `checks` → `build` → `publish` → `github-release`, tag/version verification, `id-token: write`, the `pypi` environment, and no stored token.
- [ ] `CHANGELOG.md` has a `0.1.0` section in Keep a Changelog format.
- [ ] `scripts/changelog_section.py` extracts that section; `tests/test_changelog_section.py` passes.
- [ ] `uv build` produces a `0.1.0` sdist and wheel; the installed wheel's `billkeeper --version` and `--help` work.
- [ ] `CONTRIBUTING.md` documents the tagging steps and the one-time trusted-publisher setup.
- [ ] Every GitHub URL uses the real `tristan-mcdonald/billkeeper` slug; no `OWNER` placeholder remains.
