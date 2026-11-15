"""Who may ask, and how often: API keys and a per-client rate limit.

Both are off unless configured, which keeps the Compose stack (published on
127.0.0.1 only) as it was. With ASKDOCS_API_KEYS set, /ask and /stats need
`Authorization: Bearer <key>`; /healthz stays open for health probes.

The rate limit is a token bucket per client: per API key when keys are set,
per client address otherwise. Behind a reverse proxy every request has the
proxy's address, so without keys the limit is shared by all users; keys are
what make it per user. X-Forwarded-For is not read: a client can forge it.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import SecretStr

# Clients tracked at once; past it the least recently seen is forgotten.
# Bounds memory against a flood of distinct addresses.
MAX_CLIENTS = 10_000


@dataclass(frozen=True)
class ApiKeys:
    keys: tuple[bytes, ...]

    @classmethod
    def parse(cls, raw: SecretStr | None) -> ApiKeys:
        """Comma-separated keys; blanks ignored."""
        if raw is None:
            return cls(())
        parts = (k.strip() for k in raw.get_secret_value().split(","))
        return cls(tuple(k.encode() for k in parts if k))

    @property
    def enabled(self) -> bool:
        return bool(self.keys)

    def identify(self, authorization: str | None) -> str | None:
        """The client's name for rate limiting, or None if refused. A key is
        named by a hash prefix, so logs and limits never hold the key."""
        if not self.enabled:
            return "anonymous"
        scheme, _, given = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not given:
            return None
        token = given.strip().encode()
        # Compare against every key, so the time taken does not tell which
        # prefix matched or how many keys there are.
        match = None
        for key in self.keys:
            if hmac.compare_digest(token, key):
                match = key
        if match is None:
            return None
        return "key:" + hashlib.sha256(match).hexdigest()[:12]


class RateLimiter:
    """Token bucket per client: `burst` requests at once, refilled at
    `per_minute`. per_minute 0 turns it off."""

    def __init__(self, per_minute: int, burst: int, *, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = MAX_CLIENTS):
        self.rate = per_minute / 60.0
        self.burst = float(max(burst, 1))
        self.clock, self.max_clients = clock, max_clients
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._buckets)

    def take(self, client: str) -> float:
        """0.0 if the request may go ahead (a token is spent), else the
        seconds until the next token."""
        if self.rate <= 0:
            return 0.0
        now = self.clock()
        tokens, last = self._buckets.pop(client, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        wait = 0.0
        if tokens >= 1.0:
            tokens -= 1.0
        else:
            wait = (1.0 - tokens) / self.rate
        self._buckets[client] = (tokens, now)
        while len(self._buckets) > self.max_clients:
            self._buckets.popitem(last=False)
        return wait
