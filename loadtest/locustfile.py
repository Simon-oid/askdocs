"""Load test for POST /ask, read as the SSE stream a client sees.

    locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \
           --headless --users 4 --spawn-rate 4 --run-time 60s --csv out/u4

Closed loop: each simulated user asks its next question as soon as the
last answer ends, so --users is the number of answers in flight the
service is offered. Questions are the eval set's, in a per-user shuffled
order.

Besides Locust's own row for the POST (time to response headers, which
for SSE says little), each answer reports, as request type SSE:
  ttft      until the first `token` event: what the user waits for
  answer    until `done`: the whole answer
  rejected  a 503 (more than ASKDOCS_MAX_CONCURRENT in flight), counted
            apart because it is the service working as designed, fast;
            the user then waits Retry-After before asking again
A `done` that never comes, an `error` event, or any other status is a
failure of the POST row.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from locust import HttpUser, constant, events, task

CASES = Path(__file__).resolve().parent.parent / "evals" / "cases.jsonl"
QUESTIONS = [json.loads(line)["question"] for line in CASES.read_text().splitlines()
             if line.strip()]


def report(name: str, started: float, *, exc: Exception | None = None) -> None:
    events.request.fire(request_type="SSE", name=name, response_length=0, exception=exc,
                        response_time=(time.perf_counter() - started) * 1000, context={})


def read_stream(resp, started: float) -> str | None:
    """Reports ttft and answer from the SSE body; returns why it failed, or None."""
    first = True
    event = None
    for raw in resp.iter_lines():
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("event: "):
            event = line[7:]
            if event == "token" and first:
                first = False
                report("ttft", started)
        elif line.startswith("data: ") and event in ("done", "error"):
            if event == "error":
                return f"error event: {json.loads(line[6:]).get('stage')}"
            report("answer", started)
            return None
    return "stream ended without done"


class AskUser(HttpUser):
    wait_time = constant(0)

    def on_start(self) -> None:
        self.order = random.Random(id(self)).sample(QUESTIONS, len(QUESTIONS))
        self.i = 0

    @task
    def ask(self) -> None:
        question = self.order[self.i % len(self.order)]
        self.i += 1
        started = time.perf_counter()
        with self.client.post("/ask", json={"question": question}, stream=True,
                              catch_response=True, name="POST /ask") as resp:
            if resp.status_code == 503:
                resp.success()            # by design; counted on its own row
                report("rejected", started)
                # A well-behaved client honours Retry-After; one that retried
                # at once would turn the test into a 503 benchmark.
                time.sleep(float(resp.headers.get("retry-after", 1)))
                return
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}")
                return
            why = read_stream(resp, started)
            if why:
                resp.failure(why)
