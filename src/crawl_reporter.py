"""Live, human-readable reporting for crawl progress."""

import asyncio
import logging
from collections.abc import Callable
from numbers import Real


logger = logging.getLogger(__name__)

CrawlStats = dict[str, int | float]


class CrawlReporter:
    """Periodically emit snapshots supplied by an ``AsyncCrawler``."""

    def __init__(
        self,
        stats_provider: Callable[[], CrawlStats],
        *,
        interval: float = 1.0,
        output: Callable[[str], None] | None = None,
    ) -> None:
        if not callable(stats_provider):
            raise ValueError("stats_provider must be callable")
        if isinstance(interval, bool) or not isinstance(interval, Real) or interval <= 0:
            raise ValueError("interval must be a positive number")
        if output is not None and not callable(output):
            raise ValueError("output must be callable")

        self._stats_provider = stats_provider
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
            message = self.format_stats(self._stats_provider(), final=final)
            self._output(message)
        except Exception:
            logger.exception("Could not report crawl progress")

    @staticmethod
    def format_stats(stats: CrawlStats, *, final: bool = False) -> str:
        """Convert a statistics snapshot to one compact console line."""
        prefix = "Итог" if final else "Прогресс"
        return (
            f"{prefix}: обработано {stats['pages_completed']}/"
            f"{stats['pages_scheduled']} | "
            f"успешно {stats['pages_successful']} | "
            f"в очереди {stats['pages_queued']} | "
            f"активно {stats['pages_active']} | "
            f"ошибок {stats['pages_failed']} | "
            f"скорость {stats['pages_per_second']:.2f} стр/с"
        )
