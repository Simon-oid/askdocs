# Known limitations

Where askdocs fails or has not been tested, each with the measurement behind
it when there is one. Measured on one machine (Ryzen 7 5800X, RX 7800 XT,
Qwen2.5-3B-Instruct Q4_K_M) unless noted.

## Answer quality

- **Faithfulness is the weak point: 0.60 [0.45, 0.75].** A judge model checks
  each sentence against the passage it cites. On average 40% of an answer's
  sentences are not supported by it. The two failure modes found by reading them:
  - the model cites a neighbouring passage instead of the one the fact is in;
  - it copies a passage without citing it.
- **Questions that need several documents: 0.60 key-fact recall** (5 cases),
  against 0.92 for single-document questions.
- **The judge is itself a model.** On 25 sentences it agrees with independent
  grades at κ 0.80, with one error in each direction. The independent grades
  were written by a stronger model, not by a person.
- **The eval set is small and from one corpus:** 45 questions over the
  documentation of two projects, in English. One wrong answer moves a rate by
  two to three points, so every rate is reported with its interval.
- **Answers vary between runs at temperature 0.** Four runs of the same code
  gave key-fact recall from 0.77 to 0.87. The CI gate allows for that noise,
  so a real regression smaller than about 0.10 can pass it.
- **A contradiction in the corpus is not detected.** Two documents disagree
  on one fact, and the answer follows whichever passage ranks higher.

## Retrieval

- **The index depends on the hardware that embedded it.** The same corpus
  embedded on CPU instead of GPU gave vectors at cosine 0.99998, enough to
  swap 3 of 45 near-tied rankings. A baseline holds only for its index.
- **The index is read-only for the service, but must be mounted writable:**
  SQLite's WAL mode creates a `-shm` file even for readers.
- **No re-indexing while running.** The corpus is built once by a script.

## Capacity and latency

- **About 1.8 answers per second on one GPU**, whatever the concurrency:
  each answer needs about 0.46 s of GPU to read its 2000-token prompt.
- **Past 4 answers in flight, requests get `503`.** This is by design, but a
  client must retry.
- **CPU-only is not usable interactively:** about 14 s an answer.
- **llama.cpp's `-c` must cover all slots.** With `-c 8192` and 4 slots, 65%
  of answers failed at 4 concurrent users. The shipped configuration is
  `-c 16384 -np 4`; a different model or a larger `top_k` needs it recomputed.
- **Under a burst, traces can lose spans.** The exporter's queue drops spans
  when full. `/stats` does not.

## Security and operations

- **No authentication, no rate limit per client.** Anyone who can reach the
  port can spend the GPU. The Compose stack publishes it on `127.0.0.1` only,
  and the AWS guide opens no inbound port.
- **`/stats` and `/healthz` are open too.** They expose counts and latencies,
  no content.
- **Prompt injection is reduced, not solved.** Passages and the question sit
  inside per-request random fences, and the prompt tells the model to treat
  them as data. A model can still be talked into ignoring that. The eval set
  has no injection cases, so this is untested.
- **Source file names reach the prompt outside the fence.** They come from
  the index, so whoever controls the corpus controls them.
- **Questions are not logged or traced, by design.** Debugging a bad answer
  needs the user to report the question and the `trace_id`.

## Not done

- **The AWS deploy has never been run.** `compose.cuda.yaml` and the user-data
  script were checked for syntax only: there is no NVIDIA GPU here.
- **A hosted model (`LLM_PROVIDER=openai`) is configurable but not measured.**
  No quality, latency or cost numbers exist for it.
- **One language, one embedder, one model family.** Nothing here says how
  the numbers move with another.
