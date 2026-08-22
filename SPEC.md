# Async Web Crawler v1.0 specification

## 1. Purpose

Day 7 completes the existing Days 1-6 crawler as a configurable command-line
product while preserving its modular architecture. The finished product can
load explicit and sitemap-derived seeds, crawl them through the existing
request/politeness/retry pipeline, persist successful page records through the
existing storage system, expose one run-level statistics snapshot, report live
progress, and export self-contained JSON and HTML reports.

This specification defines observable v1.0 behavior. Existing Days 1-6 tests
remain part of the acceptance contract, together with the retry and storage
boundary corrections in section 3.3.

The separate traceability table in `PLAN.md` maps the original Day 7 assignment
to this specification, implementation tasks, deliberate v1.0 decisions, and
final verification evidence. It complements this normative specification; it
does not override it.

## 2. Terminology

- **HTTP attempt**: one call that reaches `HttpTransport.fetch()`. Retries and
  robots.txt/sitemap downloads can each add attempts.
- **Logical request**: one `RequestExecutor.fetch()` call, containing zero or
  more HTTP attempts. A robots.txt rejection contains zero page HTTP attempts.
- **Page task**: one unique URL accepted by `CrawlerQueue` for parsing as a
  page. Sitemap document downloads are not page tasks.
- **Scheduled page**: a page task accepted by the queue. Duplicate URLs are not
  scheduled twice.
- **Terminal page**: a scheduled page in exactly one terminal state:
  `successful`, `failed`, or `blocked`.
- **Successful page**: a page fetched successfully, parsed without a terminal
  parser exception, and marked processed by the queue. Storage success is not
  required.
- **Failed page**: a page ending in an HTTP/network/timeout/parser/unexpected
  error. A robots.txt block and storage failure are not failed pages.
- **Blocked page**: a page intentionally not fetched because robots.txt denied
  it.
- **Run**: one call to `AdvancedCrawler.crawl()`. Run-level page statistics are
  reset before each run.
- **Operational request statistics**: existing transport, politeness, retry,
  and error counters observed during the complete run, including sitemap and
  robots.txt activity.
- **Start source**: an explicit page URL or a sitemap URL used to produce page
  seeds.
- **Dynamic ETA**: an estimate based only on currently queued/active pages and
  current average page throughput. It is not a promise about undiscovered URLs.

The following invariant always holds after a consistent snapshot:

```text
total_pages == successful + failed + blocked == pages_completed
```

`pages_scheduled` can be greater than `total_pages` while a run is active.

## 3. Architecture contract

### 3.1 Existing components

The finished product extends the current components rather than replacing them:

- `AsyncCrawler`: crawl coordination and crawl-level source data;
- `CrawlerQueue`: URL uniqueness and task lifecycle;
- `URLFilter`: URL/domain/regex filtering;
- `RequestExecutor`: logical request and retries;
- `HttpTransport`: one HTTP attempt and pooled session;
- `PolitenessManager`: rate limiting, jitter, robots.txt, and crawl-delay;
- `RetryStrategy`: reusable retry policy;
- `ErrorTracker`: terminal request and parse errors;
- `CrawlReporter`: progress rendering;
- `DataStorage`/`StorageManager`/`CompositeStorage`: persistence.

No Day 7 component may maintain competing retry, politeness, storage, queue, or
independently incremented statistics state.

### 3.2 New components

The intended modules are:

- `src/sitemap_parser.py`: injected-request sitemap traversal;
- `src/crawler_stats.py`: derived run-statistics snapshot builder;
- `src/crawler_config.py`: typed strict JSON configuration;
- `src/storage_factory.py`: construction of existing storage backends;
- `src/logging_config.py`: application logging setup;
- `src/advanced_crawler.py`: composition facade;
- `src/report_exporter.py`: JSON and HTML run reports;
- `src/cli.py`: argparse entry point;
- `src/day7_demo.py`: deterministic demonstration, with socket behavior marked
  and never included in default tests;
- `src/benchmark.py`: opt-in simulated-I/O benchmark;
- root `crawler.py`: assignment-compatible import and CLI wrapper around
  `src.advanced_crawler` and `src.cli`.

Internal module boundaries can be adjusted if tests show a smaller design, but
the public `CrawlerStats` class, root compatibility wrapper, listed
responsibilities, and no-duplication rules are mandatory.

### 3.3 Days 1-6 remediation contracts

The contracts in this section correct the public defaults/boundaries discovered
by mentor review. They must be implemented and regression-tested before Day 7.

#### 3.3.1 Default request retry

The default constructors are aligned as follows:

```python
AsyncCrawler(max_attempts=4)
RetryStrategy(max_retries=3)
```

`max_attempts` counts the initial HTTP attempt and every retry;
`max_retries == max_attempts - 1`.

- `AsyncCrawler()` performs at most four attempts for a retryable logical
  request: one initial attempt and up to three retries.
- Timeout, network errors, HTTP 429, and HTTP 5xx use the existing retry policy.
- Permanent HTTP errors such as 403 and 404 stop after the first attempt.
- A successful attempt stops the retry sequence immediately.
- `AsyncCrawler(max_attempts=1)` remains the explicit opt-out and performs no
  retry.
- Existing backoff, timeout growth, politeness-before-every-attempt, error
  classification, cancellation, and statistics contracts do not change.
- Storage retries remain separate and do not inherit this request default.

#### 3.3.2 Public DataStorage input

`CrawlRecord` remains the only normalized model passed to concrete storage
backends. The public base-class boundary accepts either an already-normalized
record or the standard dictionary representation:

```python
RecordInput = CrawlRecord | dict[str, object]

async def save(self, data: RecordInput) -> None: ...
async def save_many(self, data: Iterable[RecordInput]) -> None: ...
```

The standard dictionary contains exactly the fields returned by
`CrawlRecord.to_dict()`:

```text
url: str
title: str
text: str
links: list[str]
metadata: dict[str, str]
crawled_at: timezone-aware datetime
status_code: int in 200..399
content_type: non-empty str
```

Contracts:

- `CrawlRecord.from_dict()` validates the exact key set and delegates field
  validation/copying to the `CrawlRecord` constructor.
- Missing or unknown keys, wrong field types, a naive datetime, an invalid
  status, and an empty required string raise `ValueError` identifying the
  invalid field or key set.
- Any top-level input that is neither `dict` nor `CrawlRecord` raises
  `ValueError` before backend I/O.
- Normalization does not mutate the input dictionary, its `links`, or its
  `metadata`.
- Passing an existing `CrawlRecord` does not perform a pointless
  `record -> dict -> record` conversion.
- `DataStorage.save()` checks lifecycle, normalizes once, then invokes
  `_save(record)`.
- `DataStorage.save_many()` checks lifecycle and normalizes all inputs before
  invoking `_save_many(records)`, so invalid input is rejected before that
  batch reaches a backend.
- Concrete `_save()` and `_save_many()` methods remain typed exclusively in
  terms of `CrawlRecord`; JSON, CSV, and SQLite implementations do not each add
  their own dictionary conversion.
- Direct `JSONStorage.save(page_dict)`, `CSVStorage.save(page_dict)`, and
  `SQLiteStorage.save(page_dict)` calls therefore share identical validation.
- `StorageManager` and `CompositeStorage` continue to pass normalized
  `CrawlRecord` objects internally. HTTP retry, storage retry, and fan-out
  isolation do not change.
- The eight-field persisted schema itself does not change.

## 4. Sitemap contract

### 4.1 Public API

`SitemapParser` exposes:

```python
async def fetch_sitemap(self, sitemap_url: str) -> list[str]
```

Its fetch dependency is injected and, in the integrated product, is exactly the
existing crawler `RequestExecutor.fetch`. It must not own an
`aiohttp.ClientSession`, transport, retry loop, or politeness manager.

### 4.2 Accepted documents

- `sitemap_url` must be an absolute HTTP or HTTPS URL.
- The fetched body is parsed as XML regardless of a missing or inaccurate HTTP
  content type.
- XML namespaces are supported by comparing element local names.
- Root `<urlset>` produces page URLs from child `<url><loc>` elements.
- Root `<sitemapindex>` recursively visits child `<sitemap><loc>` documents.
- Any other root element, malformed XML, or failed root fetch raises a typed
  sitemap error with the source URL in its context.
- Empty valid `<urlset>` and `<sitemapindex>` documents return an empty list.

### 4.3 Ordering, duplicates, and errors

- `<loc>` whitespace is stripped.
- Only absolute HTTP(S) `<loc>` values are accepted.
- Invalid page `<loc>` entries are skipped and logged at `WARNING`.
- Results preserve first-seen, depth-first document order.
- A page URL appears at most once in the returned list.
- A sitemap document is fetched at most once per public `fetch_sitemap()` call;
  repeated references and cycles are ignored after first discovery.
- Failure or invalid XML in the root sitemap is fatal.
- Failure or invalid XML in a nested sitemap is logged at `WARNING`; remaining
  siblings are processed and the successfully collected partial result is
  returned.
- Cancellation is propagated.
- Parser traversal state is local to one public call; a later call can fetch the
  same sitemap again.

Compressed sitemap files and sitemap auto-discovery from robots.txt are outside
v1.0.

### 4.4 Integration with crawling

- `AdvancedCrawler` resolves explicit and sitemap-derived seeds before calling
  the existing `AsyncCrawler.crawl()`; `AsyncCrawler` does not gain a second
  sitemap/request pipeline.
- Explicit start URLs are collected first, preserving configuration/CLI order.
- Sitemap URLs are processed in their configured order; each sitemap's result
  preserves parser order.
- Page seeds are de-duplicated by the existing queue.
- Sitemap-derived page seeds enter at depth `0`.
- The combined page seeds define the allowed domains used by
  `same_domain_only=True`.
- Every seed passes the existing `URLFilter`; exclude patterns take priority.
- `max_pages` applies to page tasks, not sitemap documents.
- Sitemap downloads are excluded from page totals but included in operational
  request statistics.
- If no explicit or sitemap-derived page URL remains, `crawl()` completes
  normally with an empty result and zero page totals.

## 5. Run statistics

### 5.1 Ownership

`CrawlerStats` exists as a derived snapshot-builder, but does not own
independently incremented counters. `AsyncCrawler` and its existing components
remain the sources of truth. Day 7 may retain terminal `FetchResult` data
needed to derive status/domain summaries, but must not count the same event
independently in two owners. `CrawlerStats` performs pure aggregation over the
current source snapshots; it exposes no event-recording or increment API.
Its public `build_snapshot(...)` operation accepts current/baseline component
snapshots and returns a new canonical dictionary without mutating its inputs.

`AsyncCrawler.get_crawl_stats()` remains backward compatible. The facade adds
`AdvancedCrawler.get_stats()` as the canonical detached, JSON-friendly run
snapshot. `AsyncCrawler.get_stats()` may thinly delegate canonical snapshot
assembly to `CrawlerStats` while retaining lifecycle state and source data.

### 5.2 Canonical snapshot

`get_stats()` returns these top-level fields:

```text
total_pages: int
pages_completed: int
successful: int
failed: int
blocked: int
pages_scheduled: int
pages_queued: int
active_tasks: int
active_requests: int
max_depth_reached: int
total_text_length: int
total_links: int
total_images: int
elapsed_seconds: float
pages_per_second: float
status_codes: dict[str, int]
top_domains: list[{"domain": str, "pages": int}]
request_stats: dict[str, JSON value]
storage_stats: dict[str, JSON value] | null
```

Contracts:

- Numeric counters are non-negative.
- `total_pages` and `pages_completed` are equal and count terminal page tasks.
- `pages_per_second` is `total_pages / elapsed_seconds`, or `0.0` when elapsed
  time is zero.
- `status_codes` counts the final page response status when one exists. It does
  not count retry attempts, robots.txt responses, or sitemap document responses.
  Keys are decimal strings, sorted numerically in exported output.
- A parser failure after a successful fetch retains and counts that final HTTP
  status. A network failure, timeout, or robots block adds no status-code entry.
- `top_domains` counts terminal page tasks by normalized lowercase hostname,
  excludes ports, and is sorted by descending page count then ascending domain.
  It contains at most ten entries.
- `request_stats` extends the existing `get_request_stats()` snapshot and covers
  the full facade run, including sitemap, robots.txt, retry, and page attempts.
- `storage_stats` is `null` without configured storage. For one backend it is
  that `StorageManager` snapshot; for composite storage it is keyed per backend.
- Returned dictionaries/lists are detached. Caller mutation cannot alter crawler
  state.
- Before the first run, counters are zero, mappings/lists are empty, and
  `storage_stats` reflects configured storage without saves.
- Starting another run resets run-level page/status/domain/timing state. The
  facade reports per-run deltas from cumulative component counters where the
  underlying component is intentionally reusable.

## 6. Configuration

### 6.1 Format and loading

v1.0 supports strict JSON only. `CrawlerConfig.from_json(path)` and
`AdvancedCrawler.from_config(path)` accept a filesystem path.

- The root JSON value must be an object.
- UTF-8 is used.
- Invalid JSON, missing files, invalid values, and unknown keys raise a typed
  configuration error whose message identifies the field/path.
- Unknown keys are rejected at every nesting level.
- Configuration snapshots are detached and JSON-friendly.
- JSON loading performs no network access and constructs no HTTP session.
- A parsed config may temporarily have no sources so CLI overrides can provide
  them. Validation of the final effective config requires at least one source;
  `AdvancedCrawler.from_config()` therefore rejects a source-less file.
- Every relative path read from a config file is resolved relative to that
  file's directory. Every CLI path is resolved relative to the current working
  directory.

### 6.2 Canonical shape

```json
{
  "start_urls": ["https://example.com/"],
  "sitemap_urls": [],
  "crawl": {
    "max_concurrent": 10,
    "limit_per_host": null,
    "max_pages": 100,
    "max_depth": 2,
    "same_domain_only": true,
    "filter_external_links": false,
    "include_patterns": [],
    "exclude_patterns": [],
    "connect_timeout": 5.0,
    "read_timeout": 15.0,
    "total_timeout": 30.0,
    "timeout_multiplier": 2.0,
    "max_timeout": 120.0,
    "requests_per_second": null,
    "respect_robots": false,
    "min_delay": 0.0,
    "jitter": 0.0,
    "user_agent": "AsyncCrawler/1.0",
    "max_attempts": 4,
    "retry_base_delay": 0.5,
    "retry_max_delay": 30.0
  },
  "storage": {
    "backends": []
  },
  "reporting": {
    "show_progress": false,
    "progress_interval": 1.0,
    "json_report": null,
    "html_report": null
  },
  "logging": {
    "level": "INFO",
    "file": null,
    "max_bytes": 10485760,
    "backup_count": 3
  }
}
```

Omitted sections/fields receive the shown defaults. At least one explicit start
URL or sitemap URL must be configured by the merged config/CLI input.

Validation follows existing constructor contracts: positive integers where
required, non-negative depth/delays, positive finite rates/timeouts, non-empty
strings, booleans as actual booleans, and compilable regular expressions.

### 6.3 Storage backends

Each `storage.backends` element is one strict object:

```json
{"type": "jsonl", "path": "results/pages.jsonl"}
{"type": "csv", "path": "results/pages.csv", "encoding": "utf-8"}
{"type": "sqlite", "path": "results/pages.db", "batch_size": 100}
```

- Supported types are exactly `jsonl`, `csv`, and `sqlite`.
- An empty list disables page persistence.
- One backend is used directly; multiple backends use `CompositeStorage`.
- The factory only composes existing backends. It does not add retry or writing
  logic.
- Parent directories are created by the application before opening outputs.
- Paths follow the global config/CLI resolution rule in section 6.1.

## 7. AdvancedCrawler facade and lifecycle

`AdvancedCrawler` composes one `AsyncCrawler` and owns orchestration around it.
It exposes:

```python
@classmethod
def from_config(cls, path: str | Path) -> "AdvancedCrawler": ...

async def crawl(self) -> dict[str, dict[str, object]]: ...
def get_stats(self) -> dict[str, object]: ...
async def export_to_json(self, filename: str | Path) -> Path: ...
async def export_to_html_report(self, filename: str | Path) -> Path: ...
async def close(self) -> None: ...
```

- File export methods are deliberately asynchronous to preserve the project's
  async file-I/O contract. The original assignment example omitted `await`, but
  v1.0 callers must use `await crawler.export_to_json(...)` and
  `await crawler.export_to_html_report(...)`. The same method names do not have
  synchronous compatibility variants because those would either block the
  event loop or make the API context-dependent.
- `crawl()` obtains sitemap seeds, then delegates page crawling to the existing
  `AsyncCrawler.crawl()`.
- Overlapping `crawl()` calls on one facade are rejected.
- Sequential runs are allowed and create independent run statistics/results.
- `get_stats()` can be called before, during, or after a run.
- Export before a completed run raises `RuntimeError`.
- `close()` is idempotent and closes the underlying crawler/storage.
- Operations requiring crawl resources after close raise `RuntimeError`.
- Async context-manager entry returns the facade; exit always closes it.
- Cancellation propagates after cleanup.

## 8. Reports

### 8.1 Canonical report payload

Both report formats represent the same logical payload:

```text
schema_version: 1
generated_at: UTC ISO-8601 string
configuration: effective JSON-friendly config
statistics: canonical get_stats() snapshot
results: mapping of successful page URL to parsed result
failed_urls: mapping of URL to message
blocked_urls: mapping of URL to reason
final_errors: mapping of URL to structured terminal error
```

The payload is a detached snapshot of the most recently completed run.

### 8.2 JSON

- UTF-8, `ensure_ascii=False`, indentation of two spaces, and a final newline.
- Existing destination files are replaced.
- Missing parent directories are created.
- Serialization errors do not leave a partially written destination: write to a
  sibling temporary file and atomically replace on success.

### 8.3 HTML

- One self-contained UTF-8 HTML5 file with no CDN, external asset, JavaScript,
  or network dependency.
- All dynamic values are HTML-escaped.
- It contains a summary, timing/rate data, status-code table/visual bars, top
  domains table/visual bars, error counts, and storage statistics.
- Empty sections render a readable “no data” state.
- Existing files are atomically replaced and parent directories are created.
- The HTML payload communicates the same counts as JSON; it need not render full
  parsed page bodies.

Page storage and report export remain separate concerns. Storage writes
`CrawlRecord` during crawling; reports summarize a run afterward.

## 9. Progress reporting

`CrawlReporter` remains the only console progress formatter. It is extended with:

- dynamic percentage: `completed / scheduled * 100`, or `0%` if nothing is
  scheduled; the final completed report displays `100%`;
- current average pages/second;
- active page tasks and active HTTP requests;
- dynamic ETA: `(queued + active) / pages_per_second`;
- `ETA --` when throughput is zero or there is no meaningful estimate.

Because discovery can increase `scheduled`, the live percentage and ETA may
move backward. This behavior must be documented and tested. Output callback
errors remain logged and isolated from crawling.

## 10. Logging

- Library modules only emit records through module loggers.
- The CLI configures logging once after successful config/argument validation.
- `CrawlerConfig` and `AdvancedCrawler.from_config()` do not mutate global
  logging state; logging configuration is an application-boundary operation.
- Console logging is always enabled for CLI runs.
- Optional file logging uses `logging.handlers.RotatingFileHandler` with the
  configured `max_bytes` and `backup_count`.
- Supported levels are `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`, case
  insensitive in input and normalized in the effective config.
- Format includes timestamp, level, logger name, and message.
- UTF-8 is used for the file handler.
- Parent directories are created.
- Repeated configuration by this application does not duplicate handlers.
- Logging setup failure is a configuration/startup error; crawling does not
  silently continue without a requested log file.

## 11. CLI

The canonical module entry point is:

```bash
./venv/bin/python -m src.cli [options]
```

The assignment-compatible root entry point is also supported:

```bash
./venv/bin/python crawler.py [options]
```

Root `crawler.py` re-exports `AdvancedCrawler` so
`from crawler import AdvancedCrawler` remains valid, and its `__main__` path
delegates to `src.cli.main()` without duplicating argument parsing or crawler
orchestration.

Required assignment options:

```text
--urls URL [URL ...]
--max-pages N
--max-depth N
--output PATH
--config PATH
--respect-robots / --no-respect-robots
--rate-limit REQUESTS_PER_SECOND
```

Additional v1.0 options:

```text
--sitemaps URL [URL ...]
--html-output PATH
--show-progress / --no-show-progress
--log-file PATH
--log-level LEVEL
```

Contracts:

- Explicit CLI values override config values; config overrides defaults.
- `--urls` and `--sitemaps`, when present, replace their respective config
  lists rather than append to them.
- `--output` selects the JSON report path, not page storage.
- `--html-output` selects the HTML report path.
- Absence of both output paths is allowed for library-like CLI execution; the
  final summary is still printed.
- At least one merged explicit URL or sitemap URL is required.
- The CLI always closes resources in `finally`.
- Exit code `0`: completed crawl and requested exports succeeded.
- Exit code `2`: argparse/configuration/startup validation error.
- Exit code `1`: crawl or export runtime failure.
- Errors are concise on stderr; tracebacks are emitted only at `DEBUG` level.

## 12. Performance harness

The benchmark is opt-in and never part of the normal pytest command.

```bash
./venv/bin/python -m src.benchmark --pages 100 500 1000
```

- It uses injected deterministic simulated I/O; no socket, DNS, or network.
- It compares a synchronous baseline with the asynchronous crawler path using
  equivalent simulated latency and payloads.
- It records elapsed time, pages/second, peak memory via `tracemalloc`, and
  configuration for each scale.
- It validates result counts and exits non-zero on inconsistent results.
- Work proceeds as baseline measurement, evidence-based bottleneck analysis,
  and then conditional optimization of a confirmed bottleneck only. If the
  analysis finds no actionable bottleneck, the result explicitly records that
  no production optimization was justified.
- Any production optimization is narrow, preserves observable behavior, and is
  followed by comparable before/after measurements plus focused regression.
- It makes no hardware-dependent assertion that async must be faster by a fixed
  factor or memory must stay below a universal threshold.

## 13. Error and cleanup behavior

- Invalid user configuration fails before network/session creation.
- Expected page failures are represented in crawler state and do not abort
  unrelated workers.
- Root sitemap failure aborts the run before page crawling; nested sitemap
  failure is partial as defined above.
- Report export failure does not corrupt an existing report file.
- Storage failure remains isolated and visible in storage statistics/logs.
- `asyncio.CancelledError` is always propagated after required cleanup.
- Every CLI and facade path closes HTTP and storage resources exactly once in
  effect, even if close is called repeatedly.

## 14. Non-goals

v1.0 does not include:

- proxy support;
- custom cookies, authentication, or browser session import;
- JavaScript rendering, Playwright, or Selenium;
- distributed/multiprocess crawling;
- YAML;
- compressed `.gz` sitemaps;
- robots.txt sitemap auto-discovery;
- external dashboards, charting packages, or a web UI;
- real-Internet benchmark claims;
- changing the persisted `CrawlRecord` schema without a separate specification.

## 15. Acceptance criteria

The product is complete when all of the following are true:

1. `AsyncCrawler()` defaults to four total attempts/three retries for retryable
   failures; timeout, network, 429, and 5xx behavior is covered, while 403/404,
   success-short-circuit, and explicit `max_attempts=1` provide contrast tests.
2. `DataStorage.save()` and `save_many()` accept the exact standard dictionary
   or `CrawlRecord`, normalize only at the base boundary, and all concrete
   storage backends receive only `CrawlRecord`.
3. Existing Days 1-6 non-socket tests remain green after those corrections.
4. New tests are offline by default and every socket-binding test is marked
   `socket`.
5. Sitemap parsing covers urlset, index recursion, namespaces, order,
   deduplication, cycles, invalid entries, root failure, and partial nested
   failure.
6. Sitemap and page fetches share one request/transport/politeness/retry
   pipeline and one pooled session.
7. Explicit and sitemap-derived seeds obey existing filtering, depth, uniqueness,
   and `max_pages` contracts.
8. The public `CrawlerStats` derived snapshot-builder and canonical statistics
   satisfy their invariants, reset per run, distinguish blocked/failed/storage
   errors, and expose status/domain summaries without duplicate counters.
9. Strict JSON config, defaults, validation, path resolution, and CLI precedence
   are covered by tests.
10. Storage configuration constructs only existing backends and preserves
   per-backend retry isolation.
11. Progress reports percentage, speed, ETA, and active work without stopping the
   crawl when output fails.
12. Console/file logging and rotation are deterministic and do not duplicate
    handlers.
13. JSON and HTML reports are equivalent at the statistics level, Unicode-safe,
    escaped, self-contained, and atomically replaced.
14. Both CLI entry points, the root compatibility import, success/error exit
    codes, and cleanup are covered without invoking a real network or socket.
15. The opt-in 100/500/1000-page benchmark reports time and memory, records its
    bottleneck analysis, and applies optimization only when evidence justifies
    it, without hardware-dependent pass thresholds.
16. README documents installation, JSON configuration, both CLI entry points,
    the root import, awaited export API, library API, storage versus reports,
    testing restrictions, and benchmark usage.
17. `compileall`, `git diff --check`, focused Day 7 tests, and the full
    `-m "not socket"` suite pass in a suitable local environment.
18. The traceability table in `PLAN.md` maps every original Day 7 requirement,
    success criterion, and final-project requirement to its SPEC contract,
    PLAN task, deliberate disposition, and current acceptance evidence.
