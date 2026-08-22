# Day 7 implementation plan

## How to use this plan

Each task below is intended for one focused Codex request/chat. Complete tasks
in dependency order. A task is complete only when its focused tests and
Definition of Done pass; merely adding files is not completion.

Statuses:

- `completed`: verified and no required work remains;
- `in progress`: the only task currently being implemented;
- `pending`: not started or blocked by dependencies.

Global constraints from `AGENTS.md` and contracts from `SPEC.md` apply to every
task. In particular, no task authorizes socket or real-network execution.

## Days 1-6 remediation

### D6-R1 — Enable automatic request retries by default

**Status:** completed

**Evidence:** `AsyncCrawler()` now configures the shared `RetryStrategy` with
`max_retries=3`; offline integration coverage verifies default timeout,
network, HTTP 429/500/503, recovery, permanent-error, explicit opt-out, and
statistics behavior. Current verification: `13 passed` in the focused retry
suite, `42 passed` across crawler/retry tests, and `641 passed, 4 deselected`
in the complete non-socket suite.

**Dependencies:** none

**Prompt:** Change the public `AsyncCrawler` default to `max_attempts=4`, which
must configure the existing shared `RetryStrategy` with `max_retries=3`. Add
public-scenario tests using `AsyncCrawler()` without an explicit attempt count.
Do not change error classification, backoff, politeness, timeout growth, or
storage retry.

**Allowed scope:**

- `src/crawler.py`
- `tests/test_crawler_retry_integration.py`
- `tests/test_crawler.py` only for constructor/default assertions

**Tests:** default max-retry relationship; timeout, network error, HTTP 429, and
HTTP 503 make at most four attempts; recovery stops immediately; HTTP 403/404
make one attempt; explicit `max_attempts=1` makes one attempt; injected sleep
keeps tests deterministic and fast; retry/error statistics remain correct.

**Non-goals:** New retry classes/loops, storage behavior, config, CLI, real
network or socket tests.

**Definition of Done:** The default public crawler automatically retries all
currently classified retryable failures up to three times, explicit opt-out is
preserved, and focused regression tests pass.

---

### D6-R2 — Accept standard dictionaries at the DataStorage boundary

**Status:** pending

**Dependencies:** none

**Prompt:** Implement SPEC 3.3.2. Add strict `CrawlRecord.from_dict()`
normalization and make public `DataStorage.save()`/`save_many()` accept either
the exact eight-field dictionary or `CrawlRecord`. Normalize only in the base
class; keep every concrete `_save()`/`_save_many()` typed as `CrawlRecord`.

**Allowed scope:**

- `src/crawl_record.py`
- `src/data_storage.py`
- `tests/test_crawl_record.py`
- `tests/test_data_storage.py`
- direct-input tests in `tests/test_json_storage.py`,
  `tests/test_csv_storage.py`, and `tests/test_sqlite_storage.py`
- `tests/test_composite_storage.py` only for boundary/fan-out compatibility

**Tests:** exact dictionary round trip, original input and nested containers are
not mutated, missing/unknown keys, wrong types, naive datetime, invalid status,
direct dict save for every backend, existing CrawlRecord fast path, mixed
`save_many()` input, full-batch validation before backend invocation, closed
lifecycle behavior, CompositeStorage normalizes once and preserves per-backend
retry isolation.

**Non-goals:** Persisted schema changes, parser-result dictionaries with missing
fetch metadata, backend-specific converters, HTTP retry changes, new storage
formats, socket tests.

**Definition of Done:** The assignment's public dictionary API works uniformly
while `CrawlRecord` remains the sole backend-facing model and all existing
storage behavior stays green.

---

### D6-R3 — Re-establish and document the Days 1-6 baseline

**Status:** pending

**Dependencies:** D6-R1, D6-R2

**Prompt:** Audit the two remediation changes against SPEC 3.3, update README
examples/default descriptions, run focused and complete non-socket regression,
and record current evidence. Do not add Day 7 functionality.

**Allowed scope:**

- `README.md`
- `AGENTS.md`, `SPEC.md`, and `PLAN.md` for status/evidence only
- focused remediation tests only if the audit finds a confirmed contract gap

**Required verification:**

```bash
./venv/bin/python -m compileall -q src tests
./venv/bin/python -m pytest -q tests/test_crawler_retry_integration.py
./venv/bin/python -m pytest -q tests/test_crawl_record.py tests/test_data_storage.py tests/test_json_storage.py tests/test_csv_storage.py tests/test_sqlite_storage.py tests/test_composite_storage.py
./venv/bin/python -m pytest -q -m "not socket"
git diff --check
git status --short --branch
```

**Non-goals:** Socket tests without permission, Day 7 implementation, unrelated
Days 1-6 refactoring, new dependencies.

**Definition of Done:** README and all three planning documents match the
implemented contracts, focused and non-socket regression pass, current commands
and counts are recorded, and the Day 7 gate can be marked complete.

---

## Current milestone

### D7-00 — Freeze the Days 1-6 baseline

**Status:** pending (reopened after mentor review)

**Dependencies:** D6-R3

**Scope:** Confirm clean Git state, correct Day 4 socket marking, and a passing
Days 1-6 suite before functional Day 7 work.

**Evidence:** Commit `9ea74f8` marks the Day 4 local-server integration test with
`pytest.mark.socket`. Later mentor review found that the public
`AsyncCrawler()` default disabled request retry and that public `DataStorage`
accepted only `CrawlRecord`; D6-R1 through D6-R3 now track those corrections.

**Definition of Done:** D6-R3 is complete, the corrected baseline is committed
when the user requests it, socket tests are excluded by `-m "not socket"`, and
Day 7 starts from verified contracts.

---

## Implementation tasks

### D7-01 — Implement the isolated SitemapParser

**Status:** pending

**Dependencies:** D7-00

**Prompt:** Implement `SitemapParser.fetch_sitemap()` exactly as specified in
SPEC sections 4.1-4.3. Inject a typed async fetch callable returning
`FetchResult`; do not create a session, retry loop, or crawler integration yet.
Add typed sitemap exceptions and comprehensive unit tests using fakes only.

**Allowed scope:**

- `src/sitemap_parser.py`
- `src/errors.py` only for shared typed exceptions if justified
- `tests/test_sitemap_parser.py`

**Tests:** urlset, namespaced XML, empty sitemap, recursive index, depth-first
order, duplicate page URLs, repeated child sitemap, cycle, invalid URL entries,
root fetch/parse/schema errors, partial nested failure, cancellation, detached
state between calls.

**Non-goals:** AsyncCrawler integration, config, CLI, gzip, robots sitemap
discovery, any real HTTP/socket test.

**Definition of Done:** Focused tests pass; the parser owns only XML traversal
state and every download goes through the injected callable.

---

### D7-02 — Retain terminal page outcome data in AsyncCrawler

**Status:** pending

**Dependencies:** D7-01

**Prompt:** Add the smallest source-of-truth state needed to retain each page
task's final `FetchResult`/HTTP status for later run statistics. Preserve
existing `crawl()`, `fetch_and_parse()`, and `get_crawl_stats()` public behavior;
do not add sitemap orchestration or an independently incremented stats object.

**Allowed scope:**

- `src/crawler.py`
- `tests/test_crawler_outcomes.py`
- existing crawler tests only when extending backward-compatible assertions

**Tests:** successful 2xx/3xx result, final HTTP error after retries, network and
timeout outcomes, parser failure retaining the successful fetch status,
robots-blocked outcome, duplicate URL, reset between sequential runs, detached
read access, storage failure not changing the page outcome.

**Non-goals:** Sitemap integration, aggregation/formatting, AdvancedCrawler,
config, exports, socket tests.

**Definition of Done:** Every terminal page has one authoritative outcome source
for later aggregation and all existing Days 1-6 APIs remain compatible.

---

### D7-03 — Add canonical run statistics at existing sources of truth

**Status:** pending

**Dependencies:** D7-02

**Prompt:** Extend existing crawl/request state to support SPEC section 5:
terminal page status distribution, top domains, per-run reset/deltas, and a
detached canonical snapshot. Do not introduce an independently incremented
`CrawlerStats` class. Keep `AsyncCrawler.get_crawl_stats()` backward compatible.

**Allowed scope:**

- `src/crawler.py`
- existing stats-owning components only where the source event belongs
- `tests/test_advanced_stats.py`
- existing crawl/request stats tests

**Tests:** zero state, success/failure/blocked invariant, redirects, retries
count only final page status, parser failure keeps the fetch status, no-status
failures, robots exclusion from status distribution, domain normalization/tie
ordering/top ten, detached snapshots, sequential run reset,
cumulative-component deltas, storage failures not page failures.

**Non-goals:** reporter formatting, report files, config, CLI.

**Definition of Done:** All canonical fields can be derived without duplicate
event ownership and Days 1-6 stats APIs remain green.

---

### D7-04 — Implement strict typed JSON configuration

**Status:** pending

**Dependencies:** D7-00

**Prompt:** Implement the immutable/typed configuration model and JSON loader
from SPEC section 6, including defaults, strict unknown-key rejection, nested
validation, backend declarations, and base-directory-aware path resolution. Do
not construct crawler or storage resources yet.

**Allowed scope:**

- `src/crawler_config.py`
- `tests/test_crawler_config.py`
- `requirements.txt` only if the task is explicitly re-approved; JSON needs no
  new dependency

**Tests:** complete defaults including `max_attempts=4`, partial overrides, all
value boundaries, booleans versus integers, non-finite values, invalid regex,
unknown keys at every level,
invalid JSON/root type, missing file, UTF-8, temporarily empty sources, final
effective-source validation, relative versus absolute paths, detached
serialization.

**Non-goals:** YAML, argparse, storage construction, HTTP/session creation.

**Definition of Done:** Loading config performs only deterministic local parsing
and every invalid field produces an actionable configuration error.

---

### D7-05 — Build configured storage from existing backends

**Status:** pending

**Dependencies:** D7-04

**Prompt:** Implement a small storage factory for SPEC 6.3. It must return no
storage for an empty list, one existing backend directly, or
`CompositeStorage` for multiple backends. Create parent directories but do not
duplicate writing, buffering, retry, stats, flush, or close behavior.

**Allowed scope:**

- `src/storage_factory.py`
- `src/crawler_config.py` only for narrow config typing fixes
- `tests/test_storage_factory.py`

**Tests:** empty/single/multiple backends, all type-specific options, resolved
paths, parent creation, duplicate backend types with distinct paths, invalid
input rejected by config, exact concrete instances, no resources opened during
factory creation where existing backends are lazy.

**Non-goals:** new storage formats, report export, retry changes, real SQLite
operations.

**Definition of Done:** The factory is composition-only and existing storage
tests remain unchanged.

---

### D7-06 — Add application logging configuration

**Status:** pending

**Dependencies:** D7-04

**Prompt:** Implement console and optional rotating file logging per SPEC
section 10. Keep root/application handler setup outside library imports and make
repeated setup idempotent for application-owned handlers.

**Allowed scope:**

- `src/logging_config.py`
- `tests/test_logging_config.py`
- existing demo logging only if a compatibility defect is proven

**Tests:** supported levels/case normalization, invalid level, console-only,
UTF-8 file handler, parent creation, configured rotation values, actual rotation
using temporary files, no duplicate handlers, preservation of unrelated
handlers, setup failure propagation.

**Non-goals:** structured JSON logs, third-party logging, CLI wiring, network
logging.

**Definition of Done:** Logging is configured exactly once per application setup
and module imports have no logging side effects.

---

### D7-07 — Extend CrawlReporter with percentage and ETA

**Status:** pending

**Dependencies:** D7-03

**Prompt:** Extend the existing `CrawlReporter` formatter with the dynamic
percentage, active work, throughput, and ETA contracts in SPEC section 9.
Preserve injection and output-error isolation.

**Allowed scope:**

- `src/crawl_reporter.py`
- `tests/test_crawl_reporter.py`
- `tests/test_crawl_stats.py` only for integration expectations

**Tests:** zero state, normal ETA, zero-throughput `--`, final 100%, active tasks
and requests, growing scheduled count semantics, safe missing request fields,
output callback failure, cancellation.

**Non-goals:** external progress bars, terminal cursor control, config, CLI.

**Definition of Done:** Reporter remains the single human-readable progress
formatter and existing progress calls require no duplicate reporter.

---

### D7-08 — Implement the AdvancedCrawler composition facade

**Status:** pending

**Dependencies:** D7-01, D7-02, D7-03, D7-04, D7-05, D7-07

**Prompt:** Implement `AdvancedCrawler` and `from_config()` according to SPEC
section 7. Resolve sitemap seeds through `SitemapParser` using the underlying
crawler's `RequestExecutor`, then delegate the combined seeds to the existing
AsyncCrawler. Compose configured storage, stats, and lifecycle. Do not configure
global logging and do not implement exporters or CLI in this task.

**Allowed scope:**

- `src/advanced_crawler.py`
- narrow integration changes in the completed Day 7 modules
- `tests/test_advanced_crawler.py`

**Tests:** config-to-component mapping, same shared request executor/session,
explicit-before-sitemap ordering, sitemap ordering/deduplication, depth-zero
seeds, filters and `max_pages`, sitemap requests excluded from page totals but
included in request stats, empty/root-failed/partially-failed sitemap behavior,
no-source handling at the correct layer, result return, stats
before/during/after, overlap rejection, sequential runs, close idempotence,
operations after close, context manager, cleanup on failure and cancellation,
storage stats shape, no global logging mutation.

**Non-goals:** file reports, argparse, demo, benchmark.

**Definition of Done:** The facade delegates page work to one AsyncCrawler and
has no competing queue, retry, politeness, transport, reporter, or storage
manager.

---

### D7-09 — Implement atomic JSON and HTML report exports

**Status:** pending

**Dependencies:** D7-08

**Prompt:** Implement the canonical report payload and asynchronous JSON/HTML
exports from SPEC section 8, then expose them through `AdvancedCrawler`. Use
atomic replacement, Unicode-safe output, HTML escaping, inline CSS
visualizations, and no external resources.

**Allowed scope:**

- `src/report_exporter.py`
- `src/advanced_crawler.py`
- `tests/test_report_exporter.py`
- `tests/test_advanced_crawler.py` for facade delegation

**Tests:** canonical schema, detached payload, export-before-run rejection,
Unicode, final newline, parent creation, overwrite, write/serialization failure
preserves old destination, temp cleanup, HTML escaping, empty sections,
status/domain bars and tables, JSON/HTML statistics equivalence.

**Non-goals:** changing CrawlRecord storage, PDF, external chart libraries,
synchronous file writes.

**Definition of Done:** Both formats represent the same completed-run snapshot
and cannot leave a partial destination.

---

### D7-10 — Implement CLI and precedence rules

**Status:** pending

**Dependencies:** D7-06, D7-08, D7-09

**Prompt:** Implement `python -m src.cli` with all SPEC section 11 flags,
config/default merging, logging setup, crawl/export orchestration, concise
stderr errors, exit codes, and unconditional cleanup. Test `main(argv)` through
injected/mocked facade dependencies only.

**Allowed scope:**

- `src/cli.py`
- narrow config merge helpers in `src/crawler_config.py`
- `tests/test_cli.py`

**Tests:** help, required source after merge, every flag, replacement of URL
lists, CLI/config/default precedence, boolean optional flags, invalid values,
JSON and HTML output selection, final console summary, exit 0/1/2, export
failure, cleanup on every path, debug traceback behavior, no session on config
error.

**Non-goals:** root `crawler.py`, interactive prompts, shell completion, socket
or subprocess network tests.

**Definition of Done:** CLI behavior is fully testable in-process and all
runtime resources close on success or failure.

---

### D7-11 — Add a complete offline integration demo

**Status:** pending

**Dependencies:** D7-10

**Prompt:** Add a Day 7 demonstration covering config, sitemap index, crawling,
robots/rate/retry, composite storage, stats, logging, and both reports. Separate
pure app/payload validation tests from the local-server end-to-end test. Mark
every socket-binding test with `pytest.mark.socket`.

**Allowed scope:**

- `src/day7_demo.py`
- a checked-in example JSON config
- `tests/test_day7_demo.py`
- `tests/test_day7_demo_socket.py`
- `.gitignore` for known generated demo artifacts only

**Tests:** pure endpoint/config/report validation without sockets; one marked
localhost pipeline test; exact cleanup of server/crawler/storage; generated
artifact integrity; repeated demo reset removes only known demo files.

**Non-goals:** Internet access, running the socket test without explicit user
permission, production benchmark claims.

**Definition of Done:** Default `-m "not socket"` verifies all pure demo logic;
the marked integration test is documented but only run with permission.

---

### D7-12 — Add the deterministic performance harness

**Status:** pending

**Dependencies:** D7-08

**Prompt:** Implement the opt-in benchmark from SPEC section 12 using equivalent
deterministic simulated latency/payloads for sync and async paths. Measure
elapsed time, throughput, and peak `tracemalloc` memory for requested scales.

**Allowed scope:**

- `src/benchmark.py`
- `tests/test_benchmark.py`

**Tests:** argument validation, deterministic counts, requested 100/500/1000
scales represented, metrics schema, inconsistent result failure, no socket/DNS
calls, lightweight small-scale unit tests only.

**Non-goals:** pytest performance thresholds, real sites, localhost server,
profiling-driven production optimization, claims of universal speedup.

**Definition of Done:** The harness is opt-in, reproducible, network-free, and
reports rather than asserts hardware-dependent performance.

---

### D7-13 — Complete README/API/configuration documentation

**Status:** pending

**Dependencies:** D7-09, D7-10, D7-11, D7-12

**Prompt:** Update README for the finished product and add any concise API/config
examples required by SPEC acceptance criterion 16. Document exact commands,
JSON keys, precedence, sitemap behavior, statistics meanings, storage versus
reports, lifecycle, logging, testing restrictions, and benchmark limitations.

**Allowed scope:**

- `README.md`
- checked-in example config
- docstrings only where public API documentation is missing

**Tests/checks:** commands and examples match implemented argparse/config APIs;
all referenced paths exist; no claim of a test count or benchmark result without
current evidence; `git diff --check`.

**Non-goals:** generated API sites, broad tutorial rewrite, features outside
SPEC.

**Definition of Done:** A new user can install, configure, run, export, test,
and close the crawler using only checked-in documentation.

---

### D7-14 — Final acceptance and architecture audit

**Status:** pending

**Dependencies:** D7-01 through D7-13

**Prompt:** Perform a read-only-first acceptance audit against every SPEC section
and criterion. Search for duplicate retry/politeness/storage/stats/session
mechanisms, unmarked socket use, blocking async I/O, unclosed resources, and
untestable requirements. Fix only confirmed acceptance defects and add focused
regressions.

**Allowed scope:** Any Day 7 file or test required for a confirmed defect;
unrelated Days 1-6 refactors are forbidden.

**Required verification:**

```bash
./venv/bin/python -m compileall -q src tests
./venv/bin/python -m pytest -q -m "not socket"
git diff --check
git status --short --branch
```

Socket tests and demos remain unexecuted unless the user grants explicit
permission in that chat.

**Definition of Done:** Every acceptance criterion has current evidence, all
non-socket tests pass, documents match behavior, optional features remain out of
scope, and any unrun socket checks are reported explicitly rather than implied
green.

## Dependency summary

```text
D6-R1 default request retry ─┐
D6-R2 storage dict boundary ─┴─> D6-R3 baseline audit ─> D7-00

D7-00
├── D7-01 SitemapParser
├── D7-02 terminal page outcomes
│   └── D7-03 run statistics
│       └── D7-07 progress/ETA
├── D7-04 JSON configuration
│   ├── D7-05 storage factory
│   └── D7-06 logging
└── D7-08 AdvancedCrawler
    depends on D7-01 through D7-05 and D7-07
    ├── D7-09 reports
    │   └── D7-10 CLI (also depends on D7-06)
    │       └── D7-11 integration demo
    └── D7-12 benchmark

D7-13 documentation depends on D7-09 through D7-12.
D7-14 acceptance audit depends on every implementation/documentation task.
```
