"""Day 5 demonstration using deterministic local failure endpoints."""

import asyncio
import json
import logging
import math
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from aiohttp import web

from .crawler import AsyncCrawler
from .result_storage import save_error_report


OUTPUT_PATH = Path("day5_error_report.json")


class DemoServerState(TypedDict):
    rate_limited_requests: int
    unavailable_requests: int
    missing_requests: int
    timeout_requests: int


def create_demo_app(
    *,
    slow_delay: float = 0.2,
) -> tuple[web.Application, DemoServerState]:
    """Create local endpoints for retryable, permanent and timeout failures."""
    if (
        isinstance(slow_delay, bool)
        or not isinstance(slow_delay, (int, float))
        or not math.isfinite(slow_delay)
        or slow_delay < 0
    ):
        raise ValueError("slow_delay must be a non-negative finite number")

    state: DemoServerState = {
        "rate_limited_requests": 0,
        "unavailable_requests": 0,
        "missing_requests": 0,
        "timeout_requests": 0,
    }

    async def index(_: web.Request) -> web.Response:
        return web.Response(
            text="""
            <html><head><title>Day 5 demo</title></head><body>
              <h1>Error and retry demo</h1>
              <a href="/rate-limited">Recovering 429</a>
              <a href="/unavailable">Persistent 503</a>
              <a href="/missing">Permanent 404</a>
              <a href="/slow">Timeout</a>
            </body></html>
            """,
            content_type="text/html",
        )

    async def rate_limited(_: web.Request) -> web.Response:
        state["rate_limited_requests"] += 1
        if state["rate_limited_requests"] == 1:
            return web.Response(text="Too many requests", status=429)
        return web.Response(
            text=(
                "<html><title>Recovered 429</title>"
                "<body>Retry succeeded</body></html>"
            ),
            content_type="text/html",
        )

    async def unavailable(_: web.Request) -> web.Response:
        state["unavailable_requests"] += 1
        return web.Response(text="Service unavailable", status=503)

    async def missing(_: web.Request) -> web.Response:
        state["missing_requests"] += 1
        return web.Response(text="Not found", status=404)

    async def slow(_: web.Request) -> web.Response:
        state["timeout_requests"] += 1
        await asyncio.sleep(float(slow_delay))
        return web.Response(text="Slow response", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/", index, name="index")
    app.router.add_get("/rate-limited", rate_limited, name="rate_limited")
    app.router.add_get("/unavailable", unavailable, name="unavailable")
    app.router.add_get("/missing", missing, name="missing")
    app.router.add_get("/slow", slow, name="slow")
    return app, state


async def run_demo(
    *,
    output: Callable[[str], None] = print,
    report_path: str | Path = OUTPUT_PATH,
    slow_delay: float = 0.2,
    total_timeout: float = 0.02,
    retry_base_delay: float = 0.001,
    progress_interval: float = 0.01,
) -> dict[str, Any]:
    """Run the complete local Day 5 pipeline and save its error report."""
    if not callable(output):
        raise ValueError("output must be callable")

    app, server_state = create_demo_app(slow_delay=slow_delay)
    runner = web.AppRunner(app)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = int(listener.getsockname()[1])
    base_url = f"http://127.0.0.1:{port}"

    try:
        await runner.setup()
        site = web.SockSite(runner, listener)
        await site.start()
        output(f"Локальный демонстрационный сайт Day 5: {base_url}")

        async with AsyncCrawler(
            max_concurrent=4,
            limit_per_host=4,
            max_depth=1,
            connect_timeout=total_timeout,
            read_timeout=total_timeout,
            total_timeout=total_timeout,
            timeout_multiplier=2.0,
            max_timeout=total_timeout * 4,
            max_attempts=3,
            retry_base_delay=retry_base_delay,
            retry_max_delay=1.0,
        ) as crawler:
            results = await crawler.crawl(
                [base_url],
                max_pages=5,
                same_domain_only=True,
                show_progress=True,
                progress_interval=progress_interval,
                progress_output=output,
            )
            crawl_stats = crawler.get_crawl_stats()
            request_stats = crawler.get_request_stats()
            error_stats = crawler.get_error_stats()
            saved_report = await save_error_report(
                report_path,
                errors=crawler.final_errors,
                statistics=error_stats,
            )
            summary: dict[str, Any] = {
                "base_url": base_url,
                "processed_urls": sorted(results),
                "failed_urls": dict(crawler.failed_urls),
                "final_errors": {
                    url: dict(error)
                    for url, error in crawler.final_errors.items()
                },
                "crawl_stats": crawl_stats,
                "request_stats": request_stats,
                "error_stats": error_stats,
                "server_state": dict(server_state),
                "error_report_path": str(saved_report),
            }
    finally:
        await runner.cleanup()
        if listener.fileno() != -1:
            listener.close()

    output("")
    output("Итоговая статистика Day 5:")
    output(json.dumps(summary, ensure_ascii=False, indent=2))
    output(f"Отчёт об ошибках: {Path(report_path).resolve()}")
    return summary


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    await run_demo()


if __name__ == "__main__":
    asyncio.run(main())
