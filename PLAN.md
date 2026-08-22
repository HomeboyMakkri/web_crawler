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

**Status:** completed

**Evidence:** `CrawlRecord.from_dict()` validates the exact eight-field schema,
and `DataStorage` now normalizes dictionary inputs once before passing only
`CrawlRecord` instances to concrete backends. Coverage verifies detached nested
containers, the existing-record fast path, complete batch validation, direct
JSON/CSV/SQLite input, lifecycle ordering, and CompositeStorage retry-isolated
fan-out. Current verification: `100 passed` in the focused storage suite and
`672 passed, 4 deselected` in the complete non-socket suite; `compileall` and
`git diff --check` also pass.

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

**Status:** completed

**Evidence:** D6-R1 and D6-R2 were audited against SPEC 3.3 without finding a
production or test defect. `README.md` now documents the four-attempt default,
the `max_attempts=1` opt-out, dictionary input at the `DataStorage` boundary,
base-class-only normalization, and complete batch validation. `AGENTS.md`,
`SPEC.md`, and `PLAN.md` match the implemented contracts. Current verification:
`compileall` passed; the focused retry suite passed with `13 passed`; the
focused storage suite passed with `100 passed`; the complete non-socket suite
passed with `672 passed, 4 deselected`; and `git diff --check` passed. No socket,
localhost, or public-network checks were run.

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

**Status:** completed

**Dependencies:** D6-R3

**Scope:** Confirm clean Git state, correct Day 4 socket marking, and a passing
Days 1-6 suite before functional Day 7 work.

**Evidence:** Commit `9ea74f8` marks the Day 4 local-server integration test with
`pytest.mark.socket`. Later mentor review found that the public
`AsyncCrawler()` default disabled request retry and that public `DataStorage`
accepted only `CrawlRecord`; D6-R1 through D6-R3 completed those corrections.
Before D7-01 began, `main` matched `origin/main` at `90327af`, the worktree was
clean, and the corrected baseline was committed and verified by D6-R3.

**Definition of Done:** D6-R3 is complete, the corrected baseline is committed
when the user requests it, socket tests are excluded by `-m "not socket"`, and
Day 7 starts from verified contracts.

---

## Original Day 7 assignment traceability

This table is maintained as each task completes. `SPEC.md` remains normative;
the table prevents an original-assignment requirement from disappearing during
architectural decomposition.

| Original requirement | Contract | PLAN tasks | Current evidence |
| --- | --- | --- | --- |
| 1. `sitemap.xml` support | SPEC 4 | D7-01, D7-08 | D7-01 parser completed and offline-verified; crawler integration pending D7-08. |
| 2. `CrawlerStats` and extended statistics | SPEC 5 | D7-02, D7-03, D7-08 | D7-02 outcome source and D7-03 derived snapshot-builder completed and offline-verified; facade exposure remains pending D7-08. |
| 3. JSON and HTML export with visualizations | SPEC 7-8 | D7-09 | Pending. Export API is deliberately async and must be awaited. |
| 4. YAML or JSON configuration | SPEC 6 | D7-04, D7-05 | D7-04 strict JSON configuration and D7-05 configured storage construction completed and offline-verified; v1.0 deliberately selects JSON, which satisfies the assignment choice. |
| 5. `argparse` CLI and required flags | SPEC 11 | D7-10 | Pending. Includes canonical module entry point and root `crawler.py` compatibility wrapper. |
| 6. Console/file logging and rotation | SPEC 10 | D7-06 | D7-06 console/file logging, UTF-8 output, rotation, and idempotent handler ownership completed and offline-verified. |
| 7. Real-time progress, speed, ETA, active work | SPEC 9 | D7-07 | D7-07 dynamic percentage, throughput, ETA, and active page/request reporting completed and offline-verified. |
| 8. `AdvancedCrawler` final integration | SPEC 7 | D7-08 | Pending; facade must compose the existing crawler rather than replace it. |
| 9. Complete usage demonstration | SPEC 7, 11, 13 | D7-11 | Pending; exact assignment flow is retained with documented JSON and awaited-export corrections. |
| 10. Performance testing and optimization | SPEC 12 | D7-12 | Pending; baseline and analysis are mandatory, optimization is conditional on evidence. |
| 11. README, examples, API and configuration docs | SPEC acceptance 16 | D7-13 | Pending. |
| 12. Optional proxy/cookies/JS/distributed features | SPEC 14 | None for v1.0 | Explicitly out of scope, as allowed by the assignment. |
| Assignment success criteria | SPEC acceptance 1-18 | D7-14 | Pending final acceptance evidence. |
| Final project requirements | SPEC 1 and acceptance 1-18 | D7-01 through D7-14 | Days 1-6 baseline complete; Day 7 implementation in progress. |

---

## Implementation tasks

### D7-01 — Implement the isolated SitemapParser

**Status:** completed

**Evidence:** `src/sitemap_parser.py` injects a typed async `FetchResult`
callable and owns only per-call XML traversal state. Typed fetch, parse, and
schema errors retain the source sitemap URL; nested failures are warning-only,
while cancellation propagates. Fake-only coverage verifies urlsets, namespaces,
empty documents, recursive depth-first ordering, page and sitemap de-duplication,
cycles, invalid locations, root and nested failures, cancellation, and state
reset. Current verification: the focused suite passed with `26 passed`;
`compileall` and `git diff --check` passed; the complete non-socket suite passed
outside the sandbox with `698 passed, 4 deselected`. The first sandboxed full
suite run was interrupted after it stopped making progress in the known async
filesystem/SQLite area. No socket, localhost, or public-network checks were run.

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

**Status:** completed

**Evidence:** `AsyncCrawler` now retains one per-run `FetchResult | None` entry
for each page task without adding counters or changing queue ownership. The
detached `get_page_outcomes()` snapshot exposes only terminal tasks; successful
HTTP results survive later parser or storage failures, robots blocks remain
distinct, and `None` represents a terminal task with no fetch outcome. State is
cleared before every sequential crawl. Offline coverage verifies final 2xx/3xx,
retry-exhausted HTTP errors, network and timeout outcomes, parser failure,
robots blocking, duplicate URLs, active-task exclusion, detached snapshots,
sequential reset, storage isolation, and failures before a fetch result exists.
Current verification: the focused suite passed with `13 passed`; `compileall`
and `git diff --check` passed; the complete non-socket suite passed outside the
sandbox with `711 passed, 4 deselected`. No socket, localhost, or public-network
checks were run.

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

### D7-03 — Extract the derived CrawlerStats snapshot-builder

**Status:** completed

**Reopened:** Original-assignment traceability review found that the required
public `CrawlerStats` class and its direct tests were missing.

**Evidence:** `src/crawler_stats.py` now provides the public, stateless
`CrawlerStats` snapshot-builder. Canonical assembly, final status distribution,
top-domain aggregation, cumulative request deltas, and nested storage deltas
are pure calculations over supplied snapshots; the class has no event-recording
API or mutable counters. `AsyncCrawler` retains lifecycle, timing, page
outcomes, baselines, and component access, while `get_stats()` delegates
snapshot construction to `CrawlerStats`. Direct tests cover empty/repeated
builds, detachment, rates, status/domain ordering, request deltas and permanent
errors, input immutability, single/composite storage deltas, and invalid storage
counters. Existing integration tests continue to cover live/sequential runs,
retry and parser outcomes, robots blocks, and storage isolation. Current
verification: the focused D7-02/D7-03 suites passed with `35 passed`; the wider
crawler/request/storage suite passed with `134 passed`; `compileall` and
`git diff --check` passed; the full non-socket suite passed outside the sandbox
with `733 passed, 4 deselected`. The first sandboxed full-suite attempt was
interrupted after it stopped progressing in the known async filesystem/SQLite
area. No socket, localhost, or public-network checks were run.

**Dependencies:** D7-02

**Prompt:** Implement public `CrawlerStats` in `src/crawler_stats.py` as the
derived snapshot-builder required by SPEC section 5. Move pure canonical
snapshot assembly, status-code distribution, top-domain aggregation, and
request/storage delta calculations out of `AsyncCrawler`. The crawler retains
run lifecycle state, timing/baselines, page outcomes, and all authoritative
event sources; `get_stats()` becomes thin delegation. `CrawlerStats` must not
offer increment/record-event methods or maintain competing mutable counters.
Keep `AsyncCrawler.get_crawl_stats()` backward compatible.

**Allowed scope:**

- `src/crawler_stats.py`
- `src/crawler.py`
- `tests/test_crawler_stats.py`
- `tests/test_advanced_stats.py`
- existing crawler stats tests only for compatibility assertions

**Tests:** direct `CrawlerStats` construction and pure aggregation; zero state;
success/failure/blocked invariant; redirects; retries count only final page
status; parser failure keeps the fetch status; no-status failures; robots
exclusion; domain normalization/tie ordering/top ten; detached snapshots;
request and single/composite storage deltas; no mutation of supplied inputs;
AsyncCrawler delegation, live snapshots, sequential reset, and legacy API
compatibility.

**Non-goals:** reporter formatting, report files, config, CLI.

**Definition of Done:** `CrawlerStats` is a directly tested public component,
pure calculations no longer inflate `AsyncCrawler`, all canonical fields are
derived without duplicate event ownership, and Days 1-6 stats APIs remain
green.

---

### D7-04 — Implement strict typed JSON configuration

**Status:** completed

**Evidence:** `src/crawler_config.py` now provides frozen typed settings for
crawl behavior, storage backends, reporting, and logging plus
`CrawlerConfig.from_json()`/`from_dict()`. The loader accepts strict UTF-8 JSON,
rejects unknown keys at every level with field paths, applies the complete
defaults including four total request attempts, validates constructor-aligned
numeric/string/boolean/regex and cross-field boundaries, rejects non-finite JSON
numbers, normalizes logging levels, and resolves every configured file path
relative to the config file. Empty source lists remain valid during parsing;
`validate_effective_sources()` performs the final merged-source check.
Configuration objects use frozen dataclasses and tuples, while `to_dict()`
returns a detached JSON-friendly snapshot. Loading creates no output directory,
storage backend, HTTP session, or network activity. Current verification: the
focused suite passed with `162 passed`; `compileall` and `git diff --check`
passed; the full non-socket suite passed outside the sandbox with
`895 passed, 4 deselected`. No socket, localhost, or public-network checks were
run.

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

**Status:** completed

**Evidence:** `src/storage_factory.py` builds existing `JSONStorage`,
`CSVStorage`, and `SQLiteStorage` instances from validated `StorageSettings`.
An empty declaration returns no storage, one backend is returned directly, and
multiple declarations preserve order inside `CompositeStorage`. The factory
creates parent directories but opens no JSONL/CSV handle or SQLite connection;
all writing, buffering, retry, statistics, flush, and close behavior remains in
the existing storage components. Offline tests cover every backend option,
empty/single/composite shapes, duplicate backend types with distinct paths,
resolved paths, parent creation, invalid config, exact instances, and lazy
resources. Current focused D7-04/D7-05 verification passed with `171 passed`;
the combined D7-04 through D7-07 suite passed with `214 passed`; `compileall`
and `git diff --check` passed; the full non-socket suite passed outside the
sandbox with `932 passed, 4 deselected`. No socket, localhost, public-network,
or real SQLite I/O checks were run.

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

**Status:** completed

**Evidence:** `src/logging_config.py` configures the root logger only when the
application explicitly calls `configure_logging()`. It always installs one
formatted console handler and optionally one UTF-8 `RotatingFileHandler` with
validated level/rotation settings and parent creation. Reconfiguration replaces
and closes only application-owned handlers, preserves unrelated handlers, and
does not duplicate output. New handlers are constructed before the active set
is changed, so setup failure propagates as `ConfigurationError` without
destroying the previous configuration. Offline tests cover every supported
case-insensitive level, invalid settings, console-only output, formatting,
UTF-8 file output, configured and actual rotation, parent creation, repeated
setup, unrelated handlers, failure preservation, and import side effects.
Current focused verification passed with `22 passed`; the combined D7-04
through D7-07 suite passed with `214 passed`; `compileall` and
`git diff --check` passed; the full non-socket suite passed outside the sandbox
with `932 passed, 4 deselected`. No socket, localhost, or public-network checks
were run.

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

**Status:** completed

**Evidence:** The existing `CrawlReporter` remains the only human-readable live
progress formatter and now adds dynamic completed/scheduled percentage, active
page tasks, active HTTP requests, current page throughput, and ETA calculated as
`(queued + active) / pages_per_second`. Zero scheduled work reports `0.0%`;
final output reports `100.0%`; zero throughput, completed work, and final output
use `ETA --`. Discovery-driven growth of scheduled work may lower the displayed
percentage and change ETA without affecting the crawl. Existing request/retry/
politeness details and output-error isolation remain intact. Offline tests cover
zero, normal, stalled, growing, and final snapshots, safe missing request and
active-request fields, active work, output failure, repeated reporting, and
cancellation. Current focused reporter/crawl integration passed with
`21 passed`; the combined D7-04 through D7-07 suite passed with `214 passed`;
`compileall` and `git diff --check` passed; the full non-socket suite passed
outside the sandbox with `932 passed, 4 deselected`. No socket, localhost, or
public-network checks were run.

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
AsyncCrawler. Compose configured storage, the required derived `CrawlerStats`
snapshot-builder, and lifecycle. The facade and crawler must expose the same
canonical derived snapshot; neither may own competing counters. Do not
configure global logging and do not implement exporters or CLI in this task.

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
manager, and its statistics come from the one derived `CrawlerStats` path.

---

### D7-09 — Implement atomic JSON and HTML report exports

**Status:** pending

**Dependencies:** D7-08

**Prompt:** Implement the canonical report payload and asynchronous JSON/HTML
exports from SPEC section 8, then expose them through `AdvancedCrawler`. Use
atomic replacement, Unicode-safe output, HTML escaping, inline CSS
visualizations, and no external resources. Preserve the deliberate async public
API: both export methods return awaitables and every checked-in call site uses
`await`. A verbatim, explicitly non-runnable traceability quotation of the
original assignment is the only exception; do not add synchronous methods with
the same names to imitate its unawaited export call.

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

**Definition of Done:** Both awaited export methods represent the same
completed-run snapshot, cannot leave a partial destination, and are documented
without an unawaited call site.

---

### D7-10 — Implement CLI and precedence rules

**Status:** pending

**Dependencies:** D7-06, D7-08, D7-09

**Prompt:** Implement canonical `python -m src.cli` with all SPEC section 11
flags, config/default merging, logging setup, crawl/export orchestration,
concise stderr errors, exit codes, and unconditional cleanup. Add root
`crawler.py` as the assignment-compatible wrapper: re-export
`AdvancedCrawler`, and delegate its `__main__` path to `src.cli.main()` without
duplicating argparse or orchestration. Test both entry points through
injected/mocked facade dependencies only.

**Allowed scope:**

- `src/cli.py`
- `crawler.py`
- narrow config merge helpers in `src/crawler_config.py`
- `tests/test_cli.py`
- `tests/test_crawler_entrypoint.py`

**Tests:** help, required source after merge, every flag, replacement of URL
lists, CLI/config/default precedence, boolean optional flags, invalid values,
JSON and HTML output selection, final console summary, exit 0/1/2, export
failure, cleanup on every path, debug traceback behavior, no session on config
error, `from crawler import AdvancedCrawler`, root wrapper delegation, and no
import-time CLI side effects.

**Non-goals:** Interactive prompts, shell completion, duplicated CLI parsing,
socket or subprocess network tests.

**Definition of Done:** Both CLI forms share one implementation, the assignment
root import works, behavior is fully testable in-process, and all runtime
resources close on success or failure.

---

### D7-11 — Add a complete offline integration demo

**Status:** pending

**Dependencies:** D7-10

**Prompt:** Add a Day 7 demonstration covering config, sitemap index, crawling,
robots/rate/retry, composite storage, stats, logging, and both reports. Separate
pure app/payload validation tests from the local-server end-to-end test. Mark
every socket-binding test with `pytest.mark.socket`. Preserve the exact public
flow demonstrated by the original assignment. The following block is a
verbatim, non-normative source quotation, not a runnable v1.0 example:

```python
from crawler import AdvancedCrawler

async def main():
    crawler = AdvancedCrawler.from_config("config.yaml")
    await crawler.crawl()
    stats = crawler.get_stats()
    print(f"Обработано: {stats['total_pages']} страниц")
    print(f"Успешно: {stats['successful']}")
    print(f"Ошибок: {stats['failed']}")
    crawler.export_to_html_report("report.html")
    await crawler.close()

asyncio.run(main())
```

The checked-in runnable v1.0 example must keep that flow while applying the two
documented contract decisions: use `config.json` because v1.0 selected JSON
from the assignment's YAML-or-JSON choice, and use
`await crawler.export_to_html_report("report.html")` because exports are async.
It must import `asyncio` and close in `finally` or an async context manager.

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
the root-import example reproduces the assignment flow with the documented JSON
and async-export corrections; the marked integration test is documented but
only run with permission.

---

### D7-12 — Add the deterministic performance harness

**Status:** pending

**Dependencies:** D7-08

**Prompt:** Implement the opt-in benchmark from SPEC section 12 in three stages:
(1) establish a comparable 100/500/1000-page baseline using equivalent
deterministic simulated latency/payloads for sync and async paths; (2) analyze
elapsed time, throughput, peak `tracemalloc` memory, and profiling evidence to
identify a concrete bottleneck; (3) optimize only a confirmed bottleneck, then
rerun the same measurements. If analysis finds no justified production change,
record that conclusion instead of changing code speculatively.

**Allowed scope:**

- `src/benchmark.py`
- `tests/test_benchmark.py`
- one specifically identified Day 7 production module only after benchmark or
  profiling evidence confirms its bottleneck

**Tests:** argument validation, deterministic counts, requested 100/500/1000
scales represented, metrics schema, inconsistent result failure, no socket/DNS
calls, lightweight small-scale unit tests only, behavioral equivalence before
and after any optimization.

**Non-goals:** pytest performance thresholds, real sites, localhost server,
broad/speculative optimization, claims of universal speedup.

**Definition of Done:** The harness is opt-in, reproducible, network-free, and
reports rather than asserts hardware-dependent performance. Baseline and
analysis conclusions are recorded; a confirmed optimization includes comparable
before/after evidence and focused regression, while a no-optimization result
explicitly explains why no production change was justified.

---

### D7-13 — Complete README/API/configuration documentation

**Status:** pending

**Dependencies:** D7-09, D7-10, D7-11, D7-12

**Prompt:** Update README for the finished product and add any concise API/config
examples required by SPEC acceptance criterion 16. Document exact commands,
JSON keys, precedence, sitemap behavior, statistics meanings, storage versus
reports, `CrawlerStats` ownership, lifecycle, logging, testing restrictions,
benchmark analysis/optimization limitations, both CLI entry points, the root
`AdvancedCrawler` import, and awaited export calls. Complete the evidence column
of the original-assignment traceability table in this plan without weakening
its mapping.

**Allowed scope:**

- `README.md`
- `PLAN.md` for traceability evidence only
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
and criterion, and separately against every row in the original-assignment
traceability table in this plan. Search for duplicate retry,
politeness, storage, stats, or session mechanisms, unmarked socket use, blocking
async I/O, unclosed resources, and untestable requirements. Fix only confirmed
acceptance defects and add focused regressions.

**Allowed scope:** The traceability evidence column in `PLAN.md` plus any Day 7
file or test required for a confirmed defect; unrelated Days 1-6 refactors are
forbidden.

**Required verification:**

```bash
./venv/bin/python -m compileall -q src tests
./venv/bin/python -m pytest -q -m "not socket"
git diff --check
git status --short --branch
```

Socket tests and demos remain unexecuted unless the user grants explicit
permission in that chat.

**Definition of Done:** Every SPEC acceptance criterion and every original-
assignment table row has current evidence, all non-socket tests pass, documents
match behavior, optional features remain explicitly disposed as out of scope,
and any unrun socket checks are reported rather than implied green.

## Dependency summary

```text
D6-R1 default request retry ─┐
D6-R2 storage dict boundary ─┴─> D6-R3 baseline audit ─> D7-00

D7-00
├── D7-01 SitemapParser
│   └── D7-02 terminal page outcomes
│       └── D7-03 CrawlerStats extraction
│           └── D7-07 progress/ETA
└── D7-04 JSON configuration
    ├── D7-05 storage factory
    └── D7-06 logging
D7-08 AdvancedCrawler
    depends on D7-01 through D7-05 and D7-07
    ├── D7-09 reports
    │   └── D7-10 CLI (also depends on D7-06)
    │       └── D7-11 integration demo
    └── D7-12 benchmark

D7-13 documentation depends on D7-09 through D7-12.
D7-14 acceptance audit depends on every implementation/documentation task and
checks both SPEC.md and the original-assignment traceability table above.
```
