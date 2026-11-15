import pytest
from pydantic import SecretStr

from askdocs.access import ApiKeys, RateLimiter
from askdocs.config import Settings

from .test_app import make_client  # noqa: F401  (fixture)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class TestApiKeys:
    def test_no_keys_means_open(self):
        keys = ApiKeys.parse(None)
        assert not keys.enabled and keys.identify(None) == "anonymous"

    def test_bearer_key_is_accepted_and_named_without_revealing_it(self):
        keys = ApiKeys.parse(SecretStr("alpha-secret, beta-secret"))
        assert keys.enabled
        who = keys.identify("Bearer beta-secret")
        assert who is not None and who.startswith("key:") and "beta-secret" not in who
        assert keys.identify("Bearer alpha-secret") != who

    @pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer wrong", "Basic alpha-secret",
                                        "alpha-secret"])
    def test_anything_else_is_refused(self, header):
        assert ApiKeys.parse(SecretStr("alpha-secret")).identify(header) is None

    def test_blank_entries_are_ignored(self):
        assert not ApiKeys.parse(SecretStr(" , ")).enabled


class TestRateLimiter:
    def test_burst_then_refill(self):
        clock = Clock()
        rl = RateLimiter(per_minute=60, burst=2, clock=clock)
        assert rl.take("a") == 0.0 and rl.take("a") == 0.0
        wait = rl.take("a")
        assert wait == pytest.approx(1.0)               # one token per second
        clock.t += 1.0
        assert rl.take("a") == 0.0

    def test_clients_are_separate(self):
        rl = RateLimiter(per_minute=60, burst=1, clock=Clock())
        assert rl.take("a") == 0.0 and rl.take("b") == 0.0
        assert rl.take("a") > 0

    def test_tracked_clients_are_bounded(self):
        clock = Clock()
        rl = RateLimiter(per_minute=60, burst=1, clock=clock, max_clients=3)
        for c in "abcd":
            rl.take(c)
        assert len(rl) == 3                             # the least recent was dropped
        assert rl.take("a") == 0.0                      # and starts again with a full bucket

    def test_off_when_rate_is_zero(self):
        rl = RateLimiter(per_minute=0, burst=1, clock=Clock())
        assert all(rl.take("a") == 0.0 for _ in range(100))


async def test_ask_needs_a_key_when_keys_are_set(make_client):  # noqa: F811
    app, client = await make_client(api_keys=SecretStr("k1"))
    async with app.router.lifespan_context(app), client:
        r = await client.post("/ask?stream=false", json={"question": "q"})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert (await client.get("/stats")).status_code == 401
        assert (await client.get("/healthz")).status_code == 200   # probes stay open
        ok = await client.post("/ask?stream=false", json={"question": "q"},
                               headers={"Authorization": "Bearer k1"})
        assert ok.status_code == 200
        stats = await client.get("/stats", headers={"Authorization": "Bearer k1"})
        assert stats.status_code == 200


async def test_rate_limit_answers_429_with_retry_after(make_client):  # noqa: F811
    app, client = await make_client(rate_per_minute=60, rate_burst=1)
    async with app.router.lifespan_context(app), client:
        first = await client.post("/ask?stream=false", json={"question": "q"})
        second = await client.post("/ask?stream=false", json={"question": "q"})
    assert first.status_code == 200
    assert second.status_code == 429 and int(second.headers["retry-after"]) >= 1


def test_settings_validate_the_rate_limit():
    with pytest.raises(ValueError):
        Settings(rate_per_minute=-1)
