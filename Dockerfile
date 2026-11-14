# askdocs with its retriever: the Python service plus the `crag` CLI (to
# build the index) and `crag-mcp` (spawned by the service over stdio), both
# built from source at pinned commits. These pin the binaries only: the demo
# corpus has its own pins in scripts/build_corpus.sh, so moving CMCP_REF
# does not change the indexed documents or the eval baseline.
# CMCP_REF 17599fb: crag_search returns structuredContent.
#
#   docker build -t askdocs .

ARG CMCP_REF=17599fb
ARG CRAG_REF=a3d4815

# ---- C build: cRAG, then crag-mcp, which links cRAG's objects ------------
FROM debian:bookworm-slim AS crag
ARG CMCP_REF
ARG CRAG_REF
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config \
        libcurl4-openssl-dev git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone -q https://github.com/Simon-oid/cRAG && git -C cRAG checkout -q "$CRAG_REF" \
 && git clone -q https://github.com/Simon-oid/cMCP && git -C cMCP checkout -q "$CMCP_REF"
RUN make -C cRAG -j"$(nproc)" crag \
 && make -C cMCP -j"$(nproc)" crag-mcp CRAG_DIR=../cRAG \
 && install -m 755 cRAG/crag cMCP/tools/crag-mcp/crag-mcp /usr/local/bin/

# ---- runtime --------------------------------------------------------------
FROM python:3.12.12-slim-bookworm
# libcurl: both binaries call the embedder over HTTP. git: the index job
# fetches the corpus sources at their pinned commits.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcurl4 git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=crag /usr/local/bin/crag /usr/local/bin/crag-mcp /usr/local/bin/

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[otel]'
COPY scripts ./scripts

# Not root; /data holds the index (a volume in compose).
RUN useradd --create-home --uid 10001 askdocs && mkdir /data && chown askdocs /data
USER askdocs
ENV ASKDOCS_MCP_COMMAND=crag-mcp \
    ASKDOCS_MCP_DB=/data/docs.db \
    ASKDOCS_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"
CMD ["askdocs", "--host", "0.0.0.0", "--port", "8000"]
