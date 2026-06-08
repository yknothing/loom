#!/usr/bin/env python3
"""
rate_limiter.py — Token-bucket rate limiter for LLM API calls
"""

import asyncio
import time
from collections import deque


class RateLimiter:
    """Sliding-window rate limiter for requests and tokens per minute."""

    def __init__(self, rpm: int = 10, tpm: int = 100000):
        self.rpm = rpm
        self.tpm = tpm
        self._request_times: deque = deque()
        self._token_usage: deque = deque()  # (timestamp, token_count)
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int = 2000):
        """Wait until a request slot and token budget are available."""
        while True:
            async with self._lock:
                now = time.time()
                cutoff = now - 60

                # Prune old entries
                while self._request_times and self._request_times[0] < cutoff:
                    self._request_times.popleft()
                while self._token_usage and self._token_usage[0][0] < cutoff:
                    self._token_usage.popleft()

                # Check RPM
                if len(self._request_times) >= self.rpm:
                    wait = self._request_times[0] + 60 - now + 0.5
                    if wait > 0:
                        pass  # will sleep below
                    else:
                        # Slot available
                        self._request_times.append(now)
                        self._token_usage.append((now, estimated_tokens))
                        return
                else:
                    # Check TPM
                    total_tokens = sum(tok for _, tok in self._token_usage)
                    if total_tokens + estimated_tokens > self.tpm:
                        wait = self._token_usage[0][0] + 60 - now + 0.5
                        if wait <= 0:
                            self._request_times.append(now)
                            self._token_usage.append((now, estimated_tokens))
                            return
                    else:
                        self._request_times.append(now)
                        self._token_usage.append((now, estimated_tokens))
                        return

            # Calculate wait time
            async with self._lock:
                now = time.time()
                cutoff = now - 60
                while self._request_times and self._request_times[0] < cutoff:
                    self._request_times.popleft()
                while self._token_usage and self._token_usage[0][0] < cutoff:
                    self._token_usage.popleft()

                waits = []
                if len(self._request_times) >= self.rpm:
                    waits.append(self._request_times[0] + 60 - now + 0.5)
                total_tokens = sum(tok for _, tok in self._token_usage)
                if total_tokens + estimated_tokens > self.tpm:
                    waits.append(self._token_usage[0][0] + 60 - now + 0.5)

                wait = max(waits) if waits else 0
                if wait > 0:
                    await asyncio.sleep(wait)


    def acquire_sync(self, estimated_tokens: int = 2000):
        """Synchronous version of acquire for non-async code."""
        while True:
            now = time.time()
            cutoff = now - 60

            while self._request_times and self._request_times[0] < cutoff:
                self._request_times.popleft()
            while self._token_usage and self._token_usage[0][0] < cutoff:
                self._token_usage.popleft()

            can_proceed = True

            if len(self._request_times) >= self.rpm:
                can_proceed = False

            total_tokens = sum(tok for _, tok in self._token_usage)
            if total_tokens + estimated_tokens > self.tpm:
                can_proceed = False

            if can_proceed:
                self._request_times.append(time.time())
                self._token_usage.append((time.time(), estimated_tokens))
                return

            # Calculate wait
            waits = []
            if len(self._request_times) >= self.rpm:
                waits.append(self._request_times[0] + 60 - time.time() + 0.5)
            if total_tokens + estimated_tokens > self.tpm:
                waits.append(self._token_usage[0][0] + 60 - time.time() + 0.5)
            wait = max(waits) if waits else 1
            time.sleep(max(wait, 0.5))


class ConcurrencySemaphore:
    """Simple async semaphore for controlling concurrent requests."""

    def __init__(self, max_concurrent: int = 3):
        self._sem = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self):
        await self._sem.acquire()
        return self

    async def __aexit__(self, *args):
        self._sem.release()
