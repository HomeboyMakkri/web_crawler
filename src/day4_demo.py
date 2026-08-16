"""Day 4 demonstration using a deterministic local polite-crawling site."""

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from typing import Any, TypedDict

from aiohttp import web

from .crawler import AsyncCrawler


USER_AGENT = "Day4DemoBot/1.0"


class DemoServerState(TypedDict):
    unstable_requests: int
    private_requests: int


def create_demo_app(
    *,
    robots_crawl_delay: int | None = 1,
) -> tuple[web.Application, DemoServerState]:
    """Create a tiny site exposing robots blocking and a transient HTTP error."""
    if robots_crawl_delay is not None and (
        isinstance(robots_crawl_delay, bool)
        or not isinstance(robots_crawl_delay, int)
        or robots_crawl_delay < 0
    ):
        raise ValueError("robots_crawl_delay must be a non-negative integer or None")

    state: DemoServerState = {
        "unstable_requests": 0,
        "private_requests": 0,
    }

    async def robots(_: web.Request) -> web.Response:
        rules = [
            "User-agent: Day4DemoBot",
            "Disallow: /private",
            "Allow: /",
        ]
        if robots_crawl_delay is not None:
            rules.append(f"Crawl-delay: {robots_crawl_delay}")
        return web.Response(text="\n".join(rules), content_type="text/plain")

    async def index(_: web.Request) -> web.Response:
        return web.Response(
            text="""
            <html><head><title>Day 4 demo</title></head><body>
              <h1>Polite crawler demo</h1>
              <a href="/public">Public page</a>
              <a href="/private">Blocked page</a>
              <a href="/unstable">Retry page</a>
            </body></html>
            """,
            content_type="text/html",
        )

    async def public(_: web.Request) -> web.Response:
        return web.Response(
            text="<html><title>Public</title><body>Allowed content</body></html>",
            content_type="text/html",
        )

    async def private(_: web.Request) -> web.Response:
        state["private_requests"] += 1
        return web.Response(
            text="This endpoint must be blocked by robots.txt",
            content_type="text/plain",
        )

    async def unstable(_: web.Request) -> web.Response:
        state["unstable_requests"] += 1
        if state["unstable_requests"] == 1:
            return web.Response(text="Temporary failure", status=503)
        return web.Response(
            text="<html><title>Recovered</title><body>Retry succeeded</body></html>",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/", index)
    app.router.add_get("/public", public)
    app.router.add_get("/private", private)
    app.router.add_get("/unstable", unstable)
    return app, state


async def run_demo(
    *,
    output: Callable[[str], None] = print,
    robots_crawl_delay: int | None = 1,
    requests_per_second: float = 4.0,
    min_delay: float = 0.1,
    jitter: float = 0.1,
    retry_base_delay: float = 0.25,
    progress_interval: float = 0.25,
) -> dict[str, Any]:
    """Run the complete Day 4 pipeline and return its structured summary."""
    if not callable(output):
        raise ValueError("output must be callable")

    app, server_state = create_demo_app(
        robots_crawl_delay=robots_crawl_delay,
    )
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
        output(f"Локальный демонстрационный сайт: {base_url}")

        async with AsyncCrawler(
            max_concurrent=3,
            limit_per_host=2,
            max_depth=1,
            requests_per_second=requests_per_second,
            respect_robots=True,
            min_delay=min_delay,
            jitter=jitter,
            user_agent=USER_AGENT,
            max_attempts=2,
            retry_base_delay=retry_base_delay,
            retry_max_delay=2.0,
        ) as crawler:
            results = await crawler.crawl(
                [base_url],
                max_pages=10,
                same_domain_only=True,
                show_progress=True,
                progress_interval=progress_interval,
                progress_output=output,
            )
            summary: dict[str, Any] = {
                "base_url": base_url,
                "processed_urls": sorted(results),
                "blocked_urls": dict(crawler.blocked_urls),
                "failed_urls": dict(crawler.failed_urls),
                "crawl_stats": crawler.get_crawl_stats(),
                "request_stats": crawler.get_request_stats(),
                "server_state": dict(server_state),
            }
    finally:
        await runner.cleanup()
        if listener.fileno() != -1:
            listener.close()

    output("")
    output("Итоговая статистика Day 4:")
    output(json.dumps(summary, ensure_ascii=False, indent=2))
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
