"""Live, human-readable reporting for crawl progress."""

import asyncio
import logging
from collections.abc import Callable
from numbers import Real


logger = logging.getLogger(__name__)

CrawlStats = dict[str, int | float]
RequestStats = dict[str, int | float]


class CrawlReporter:
    """Periodically emit snapshots supplied by an ``AsyncCrawler``."""

    def __init__(
        self,
        stats_provider: Callable[[], CrawlStats],
        *,
        request_stats_provider: Callable[[], RequestStats] | None = None,
        interval: float = 1.0,
        output: Callable[[str], None] | None = None,
    ) -> None:
        if not callable(stats_provider):
            raise ValueError("stats_provider must be callable")
        if request_stats_provider is not None and not callable(
            request_stats_provider
        ):
            raise ValueError("request_stats_provider must be callable")
        if isinstance(interval, bool) or not isinstance(interval, Real) or interval <= 0:
            raise ValueError("interval must be a positive number")
        if output is not None and not callable(output):
            raise ValueError("output must be callable")

        self._stats_provider = stats_provider
        self._request_stats_provider = request_stats_provider
        self._interval = float(interval)
        self._output = print if output is None else output

    async def run(self) -> None:
        """Report immediately, then repeat until the task is cancelled."""
        while True:
            self.report_once()
            await asyncio.sleep(self._interval)

    def report_once(self, *, final: bool = False) -> None:
        """Emit one snapshot without allowing an output failure to stop crawling."""
        try:
            request_stats = (
                self._request_stats_provider()
                if self._request_stats_provider is not None
                else None
            )
            message = self.format_stats(
                self._stats_provider(),
                request_stats=request_stats,
                final=final,
            )
            self._output(message)
        except Exception:
            logger.exception("Could not report crawl progress")

    @staticmethod
    def format_stats(
        stats: CrawlStats,
        *,
        request_stats: RequestStats | None = None,
        final: bool = False,
    ) -> str:
        """Convert a statistics snapshot to one compact console line."""
        prefix = "Итог" if final else "Прогресс"
        crawl_message = (
            f"{prefix}: обработано {stats['pages_completed']}/"
            f"{stats['pages_scheduled']} | "
            f"успешно {stats['pages_successful']} | "
            f"в очереди {stats['pages_queued']} | "
            f"активно {stats['pages_active']} | "
            f"ошибок {stats['pages_failed']} | "
            f"заблокировано {stats.get('pages_blocked', 0)} | "
            f"скорость {stats['pages_per_second']:.2f} стр/с"
        )
        if request_stats is None:
            return crawl_message

        return (
            f"{crawl_message} | "
            f"HTTP-запросов {request_stats.get('total_requests', 0)} "
            f"({request_stats.get('current_requests_per_second', 0.0):.2f}/с) | "
            f"ср. задержка "
            f"{request_stats.get('average_rate_limit_wait', 0.0):.3f} с | "
            f"retry {request_stats.get('scheduled_retries', 0)} | "
            f"robots.txt блокировок {request_stats.get('robots_blocked', 0)}"
        )
