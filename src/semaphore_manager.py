"""Global and per-domain concurrency limits for HTTP requests."""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit


class SemaphoreManager:
    """Coordinate global and per-domain request concurrency."""

    def __init__(
        self,
        global_limit: int = 10,
        per_domain_limit: int = 2,
    ) -> None:
        self._global_limit = self._validate_limit(global_limit, "global_limit")
        self._per_domain_limit = self._validate_limit(
            per_domain_limit,
            "per_domain_limit",
        )

        self._global_semaphore = asyncio.Semaphore(self._global_limit)
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}

        self._active_total = 0
        self._active_by_domain: defaultdict[str, int] = defaultdict(int)
        
        self._waiting_global = 0
        self._waiting_by_domain: defaultdict[str, int] = defaultdict(int)

    @asynccontextmanager
    async def request_slot(self, url: str) -> AsyncGenerator[None, None]:
        """Wait for domain and global capacity, then release both safely."""
        domain = self.get_domain(url)
        domain_semaphore = self._get_domain_semaphore(domain)
        domain_acquired = False
        global_acquired = False
        counted_as_active = False

        try:
            # Acquiring the narrower limit first prevents one busy domain from
            # occupying every global slot while waiting for its domain slots.
            self._waiting_by_domain[domain] += 1
            try:
                await domain_semaphore.acquire()
            finally:
                self._waiting_by_domain[domain] -= 1
                if self._waiting_by_domain[domain] == 0:
                    del self._waiting_by_domain[domain]
            domain_acquired = True

            self._waiting_global += 1
            try:
                await self._global_semaphore.acquire()
            finally:
                self._waiting_global -= 1
            global_acquired = True

            self._active_total += 1
            self._active_by_domain[domain] += 1
            counted_as_active = True
            yield
        finally:
            if counted_as_active:
                self._active_total -= 1
                self._active_by_domain[domain] -= 1
                if self._active_by_domain[domain] == 0:
                    del self._active_by_domain[domain]
            if global_acquired:
                self._global_semaphore.release()
            if domain_acquired:
                domain_semaphore.release()

    @property
    def active_total(self) -> int:
        return self._active_total

    @property
    def active_by_domain(self) -> dict[str, int]:
        return dict(self._active_by_domain)

    def get_stats(self) -> dict[str, int | dict[str, int]]:
        """Return current limits, active requests and waiting tasks."""
        return {
            "global_limit": self._global_limit,
            "per_domain_limit": self._per_domain_limit,
            "active_total": self._active_total,
            "active_by_domain": dict(self._active_by_domain),
            "waiting_global": self._waiting_global,
            "waiting_by_domain": dict(self._waiting_by_domain),
        }

    @staticmethod
    def get_domain(url: str) -> str:
        """Return a normalized hostname used as the semaphore key."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty HTTP(S) URL")

        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("url must be a valid HTTP(S) URL")
        return parsed.hostname.lower()

    def _get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        semaphore = self._domain_semaphores.get(domain)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._per_domain_limit)
            self._domain_semaphores[domain] = semaphore
        return semaphore

    @staticmethod
    def _validate_limit(value: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value 
