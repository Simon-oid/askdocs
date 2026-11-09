# Load test

How askdocs behaves with concurrent users, measured with Locust
([`loadtest/`](../loadtest/)) on 2026-11-09. Setup:
- one machine: RX 7800 XT, Qwen2.5-3B-Instruct Q4_K_M on llama.cpp with ROCm;
- `mxbai-embed-large` on Ollama, tracing off;
- askdocs and llama.cpp on the host, as for the eval baseline.

```sh
pip install -e '.[load]'
loadtest/run.sh             # 1 2 4 8 16 users, 60 s each, against :8000
```

**The model.** Closed loop: each simulated user asks the next eval question
as soon as its answer ends. The number of users is therefore the number of
answers offered at once.

**What each answer records.** Times are read from the SSE stream:
- **ttft**: until the first `token` event;
- **answer**: until `done`.

**503 responses** are counted on their own row, and the user then waits
`Retry-After` (1 s) before asking again, like a well-behaved client. A client
that retried at once would turn the test into a 503 benchmark.

Locust's percentiles are approximate: above 100 ms it rounds to two
significant digits.

## 1. The first run found a configuration bug

The first run used `llama-server -c 8192`, as the quick start did. It had no
failures at 1 and 2 users, but **68 failed answers out of 104 at 4 users**,
and failures at every step above that. The server log had one cause:

```
ask failed at generation: upstream error in stream:
  {'code': 500, 'message': 'Context size has been exceeded.'}
```

`-c` is the context of **all slots together**:
- Without `-np`, llama.cpp ran 4 slots on one unified KV cache of 8192 tokens.
- Four answers of about 2000 prompt tokens each, plus what they generate, do
  not fit.
- The sequential evals could never see it, and two users still fit.

The rule: `-c ≥ slots × (longest prompt + max_tokens)`. The longest prompt in
the eval set is 2729 tokens and `max_tokens` is 512, so 4 slots × 4096 =
`-c 16384 -np 4`. That is about 600 MB more KV cache for the 3B model. The
quick start and `compose.yaml` now use it. `ASKDOCS_MAX_CONCURRENT` must
equal `-np`, for the reason in section 3.

## 2. Results, limit 4 on 4 slots

| users | answers/s | ttft P50 / P95 ms | answer P50 / P95 ms | answers | rejected (503) | failed |
|---|---|---|---|---|---|---|
| 1 | 1.34 | 440 / 580 | 690 / 1700 | 79 | 0 (0%) | 0 |
| 2 | 1.47 | 480 / 940 | 1200 / 3000 | 87 | 0 (0%) | 0 |
| 4 | 1.78 | 540 / 1100 | 2100 / 4300 | 104 | 0 (0%) | 0 |
| 8 | 1.73 | 500 / 1100 | 2000 / 4900 | 102 | 236 (70%) | 0 |
| 16 | 1.81 | 550 / 1500 | 2100 / 4000 | 107 | 708 (87%) | 0 |

- **No failures** at any load.
- **Throughput stops at about 1.8 answers/s.** Going from 1 to 4 answers in
  flight adds only 33% throughput, while the answer time triples.
- **Past the limit,** time to first token stays where it was at 4 users:
  the excess is refused at once instead of queued. At 16 users, 87% of
  attempts get a 503, and the users who are served see the same latency as at
  4.

### Why concurrency buys so little: prefill

llama.cpp's own timings for the same run, as the median per answer:

| users | prefill tok/s | decode ms/token | decode tok/s per answer |
|---|---|---|---|
| 1 | 4545 | 6.7 | 150 |
| 2 | 4545 | 18.1 | 55 |
| 4 | 4167 | 41.4 | 24 |
| 8 | 4348 | 39.5 | 25 |
| 16 | 4000 | 40.7 | 25 |

What the table shows:

- **Prefill does not slow down,** but each answer needs about 2000 prompt
  tokens of it: about **0.46 s of GPU time per answer**, whatever else is
  running.
- **The GPU is mostly busy reading prompts.** At 1.78 answers/s that is
  about 0.82 s of prefill every second.
- **Decode stalls behind it.** The other answers wait while a new prompt is
  read, so decode per answer falls from 150 to 24 tok/s. With 4 in flight,
  the total is about 96 tok/s, less than one answer alone.
- **The ceiling.** If prefill were the only cost, it would be 1 / 0.46 s ≈
  2.2 answers/s. The measured 1.8 is close.

So this service is **prefill-bound**. The prompt is about 60 times the
answer (see [observability](observability.md)), so the levers are shorter
prompts (`top_k`, shorter passages), not more concurrency:
- halving the context should roughly double throughput;
- a bigger `-np` would not help.

This is a prediction, not a measurement, and it would need its own eval run:
fewer passages can also cost answer quality.

## 3. Limit 8 on 4 slots: same throughput, 5× the wait

Same test, but `ASKDOCS_MAX_CONCURRENT=8` with llama.cpp still on 4 slots:

| users | answers/s | ttft P50 / P95 ms | answer P50 / P95 ms | rejected (503) |
|---|---|---|---|---|
| 8, limit 4 | 1.73 | 500 / 1100 | 2000 / 4900 | 70% |
| 8, limit 8 | 1.83 | 2600 / 3600 | 4000 / 7600 | 0% |
| 16, limit 8 | 1.81 | 2700 / 4000 | 4300 / 7000 | 82% |

The 4 answers beyond the slots wait in llama.cpp's queue. The client cannot
see that queue: for it, they are just slow. Throughput is unchanged, because
the GPU was already full. Time to first token goes from 0.5 s to 2.6 s.

With the limit equal to the slots:
- the same throughput;
- a fifth of the wait for the users who are served;
- an honest, immediate 503 for the rest, which a load balancer can send
  elsewhere.

This is why `MAX_CONCURRENT` must equal `-np`.

## Limits of this test

- **One machine.** Locust, askdocs, Ollama and llama.cpp share the CPU, and
  Locust is light next to the model.
- **A closed loop measures capacity, not a traffic pattern.** Real users
  arrive independently (an open loop). At an arrival rate below 1.8/s the
  service would mostly run below its limit.
- **Retrieval roughly doubled under load, but stayed small.** In the
  `/stats` window it went from a P50 of 14 ms to about 28 ms, still about 1%
  of an answer. A likely cause is concurrent embedding requests queueing in
  Ollama, not verified.
- **The eval baseline was measured with `-c 8192`.** With one request at a
  time a slot then had the whole 8192 tokens. The baseline's longest prompt,
  2729 tokens plus 512, fits in 4096, so the new configuration does not
  truncate any of its prompts.
