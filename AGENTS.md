# Repository instructions

## Purpose and sources of truth

This repository is an educational asynchronous web crawler. Days 1-6 are
implemented, but mentor review reopened two public contracts that must be fixed
and re-verified before functional Day 7 work starts. Day 7 then completes the
product without replacing the existing architecture.

Before changing code:

1. Read this file, `SPEC.md`, and the relevant item in `PLAN.md`.
2. Inspect `git status --short --branch` and preserve unrelated user changes.
3. Inspect the real implementation and tests named by the plan item. The
   original assignment is context, not a more accurate source than the code and
   `SPEC.md`.
4. Work on one `PLAN.md` item at a time. Do not implement dependent or later
   items opportunistically.

`SPEC.md` is the product contract. `PLAN.md` is the implementation sequence. If
they conflict, stop and surface the contradiction instead of guessing.

## Existing architecture and invariants

- `AsyncCrawler` coordinates crawling, workers, parsing, crawl state, and
  optional persistence.
- `CrawlerQueue` owns unique URL scheduling and terminal task state.
- `URLFilter` owns HTTP(S), domain, include, and exclude filtering.
- `RequestExecutor` owns one logical request and invokes request policy before
  every attempt.
- `HttpTransport` owns the shared `aiohttp.ClientSession`, connection pool,
  concurrency limits, timeouts, and one real HTTP attempt.
- `PolitenessManager` is the single owner of rate limiting, jitter, robots.txt,
  and crawl-delay behavior.
- `RetryStrategy` is the shared retry primitive. Page-request retry and storage
  retry may configure it differently, but must not reimplement backoff loops.
- `ErrorTracker` owns terminal request and parsing errors.
- `CrawlReporter` owns human-readable live progress formatting.
- `CrawlRecord` is the normalized internal persistence model for a successfully
  fetched and parsed page.
- `DataStorage`, `StorageManager`, and the existing concrete/composite storage
  classes own persistence, retry isolation, flushing, and closure.

Preserve these invariants:

- Do not create parallel retry, politeness, queue, crawler, reporting, or
  storage pipelines.
- Do not create a second independently incremented statistics system. New
  statistics must be derived from or added at the existing source of truth.
- Sitemap requests must use the crawler's existing `RequestExecutor`; they must
  not create another HTTP session.
- `AdvancedCrawler` is a facade composed around `AsyncCrawler`, not a replacement
  engine and not a subclass with duplicated lifecycle state.
- Storage errors remain isolated from page success/failure. A storage failure
  must not place a successfully crawled URL in `failed_urls`.
- `robots.txt` blocks remain distinct from failures.
- Existing public Days 1-6 behavior and tests remain backward compatible unless
  `SPEC.md` explicitly changes a contract.
- The public `AsyncCrawler()` default is four total attempts: one initial
  attempt plus three retries. `max_attempts=1` explicitly disables retries.
- `DataStorage.save()` and `save_many()` accept either the standard eight-field
  dictionary defined in `SPEC.md` or an existing `CrawlRecord`. Normalization
  happens once in `DataStorage`; every concrete `_save()`/`_save_many()` receives
  only validated `CrawlRecord` instances.

## Days 1-6 remediation gate

Complete `D6-R1`, `D6-R2`, and `D6-R3` in `PLAN.md` before starting `D7-01` or
any other functional Day 7 task.

- `D6-R1` enables the agreed default request retry contract without changing
  error classification or creating another retry loop.
- `D6-R2` adds the public dictionary adapter while preserving `CrawlRecord` as
  the only backend-facing model.
- `D6-R3` updates affected documentation, runs the complete non-socket
  regression, and re-establishes the baseline.

Do not describe Days 1-6 as a verified stable milestone until `D6-R3` is
complete. The remediation tasks are compatibility corrections, not permission
to refactor unrelated Days 1-6 code.

## Day 7 scope limits

Day 7 includes sitemap XML, run statistics, strict JSON configuration, rotating
application logging, progress percentage/ETA, the `AdvancedCrawler` facade,
JSON/HTML run reports, CLI integration, an offline demo, an opt-in benchmark,
and documentation.

The following are out of scope for v1.0:

- proxies;
- user-configurable cookies or authentication;
- JavaScript rendering and browser automation;
- distributed crawling or multiple process workers;
- YAML configuration;
- compressed sitemap files;
- external charting, progress-bar, or logging dependencies.

Do not add dependencies without explicit user approval. Prefer the standard
library and packages already listed in `requirements.txt`.

## Verification commands

Use the project interpreter from the repository root.

Fast syntax/static checks:

```bash
./venv/bin/python -m compileall -q src tests
git diff --check
```

Default regression suite, excluding all tests that bind sockets:

```bash
./venv/bin/python -m pytest -q -m "not socket"
```

Focused Day 7 tests should be run before the regression suite, for example:

```bash
./venv/bin/python -m pytest -q tests/test_sitemap_parser.py
```

The exact focused command must match the files changed by the active plan item.
Report commands and their current results precisely; do not reuse a historical
pass count as current evidence.

## Network and socket prohibition

Never run a test, demo, benchmark, or command that binds a local socket or
accesses a real network without the user's explicit permission in the current
chat. This includes localhost/`127.0.0.1` servers.

- Tests that bind sockets must have `@pytest.mark.socket`.
- Normal tests must inject/mimic fetchers, transports, clocks, sleeps, and file
  destinations.
- Do not run `src.day4_demo`, `src.day5_demo`, `src.day6_demo`, or any
  `-m socket` selection without permission.
- A performance benchmark must use deterministic in-process simulated I/O and
  must remain opt-in; it is not part of the default pytest suite.

## Implementation and test discipline

- Validate public inputs explicitly. Reject booleans where Python would
  otherwise accept them as integers.
- Reject non-finite numeric configuration values.
- Keep JSON-friendly public snapshots detached from mutable internal state.
- Preserve deterministic ordering where the specification requires it.
- Keep blocking file/network work out of async functions; use existing async
  storage utilities and `aiofiles` where appropriate.
- Configure root handlers only at the application/CLI boundary. Library modules
  use `logging.getLogger(__name__)` and must not call `basicConfig()` on import.
- Always close the crawler and storage in `finally` or through an async context
  manager. Cancellation remains control flow and must not be converted into a
  normal failure.
- Add contrast tests for success, expected failure, invalid input, empty input,
  duplicate input, lifecycle/repeated calls, and relevant boundary limits.
- Tests must not depend on timing races, Internet availability, or machine
  performance.

## Plan and handoff discipline

For an active `PLAN.md` item:

1. Stay inside its scope and allowed files unless a discovered dependency makes
   that impossible.
2. If the contract is ambiguous, stop and ask; do not silently broaden it.
3. Satisfy the item's focused tests and Definition of Done.
4. Run the non-socket regression suite when the environment supports it.
5. Update only that item's status/evidence in `PLAN.md` after verification.
6. Hand off a concise summary of files changed, behavior added, commands run,
   and anything not verified.

Do not commit, push, install dependencies, run socket tests, or access the real
network unless the user explicitly requests that action.
