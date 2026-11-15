"""Local pacing proxy for running the eval against a hosted free tier.

    HOSTED_API_KEY=... uvicorn evals.hosted_proxy:app --port 8099
    ASKDOCS_LLM_PROVIDER=openai ASKDOCS_LLM_BASE_URL=http://127.0.0.1:8099/v1 \
    ASKDOCS_LLM_API_KEY=via-proxy ASKDOCS_LLM_MODEL=openai/gpt-oss-120b \
    python evals/run.py --phase answer --out evals/results-gptoss120b

Free tiers cap tokens per minute; askdocs' client retries a 429 only a few
times. The proxy spaces requests (PROXY_INTERVAL_S, default 30 s), adds
`reasoning_effort` (PROXY_REASONING_EFFORT, default low) and injects the
real key, so the service code is the one under test, unchanged."""
import asyncio
import json
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

UPSTREAM = os.environ.get("HOSTED_BASE_URL", "https://api.groq.com/openai/v1")
KEY = os.environ["HOSTED_API_KEY"]
INTERVAL_S = float(os.environ.get("PROXY_INTERVAL_S", "30"))
EFFORT = os.environ.get("PROXY_REASONING_EFFORT", "low")
app = FastAPI()
lock, last = asyncio.Lock(), [0.0]
client = httpx.AsyncClient(timeout=180)

@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = json.loads(await req.body())
    body["reasoning_effort"] = EFFORT
    async with lock:
        wait = last[0] + INTERVAL_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last[0] = time.monotonic()
    up = client.build_request("POST", f"{UPSTREAM}/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {KEY}"})
    r = await client.send(up, stream=True)
    if r.status_code != 200:
        data = await r.aread()
        await r.aclose()
        print("upstream", r.status_code, data[:200], flush=True)
        return Response(data, status_code=r.status_code, headers={
            k: v for k, v in r.headers.items() if k.lower() == "retry-after"})
    async def gen():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()
    return StreamingResponse(gen(), media_type=r.headers.get("content-type", "text/event-stream"))
