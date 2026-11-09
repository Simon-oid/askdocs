#!/usr/bin/env bash
# Builds the demo corpus and its cRAG index, reproducibly.
#
# Corpus: the Markdown documentation of cMCP and cRAG at pinned commits
# (public repos, same author; English). Questions about them have
# checkable answers, which is what the eval set needs.
#
#   scripts/build_corpus.sh            # -> data/corpus/, data/docs.db
#   ASKDOCS_DATA_DIR=/data scripts/build_corpus.sh   # elsewhere
#
# Needs: git, the `crag` CLI on PATH (or $CRAG), Ollama with the embedder.
set -euo pipefail

CMCP_REF=${CMCP_REF:-cdb8852}
CRAG_REF=${CRAG_REF:-a3d4815}
EMBED_MODEL=${CRAG_EMBED_MODEL:-mxbai-embed-large}
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434/api/embeddings}
CRAG=${CRAG:-crag}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${ASKDOCS_DATA_DIR:-$ROOT/data}     # /data in the container
CACHE=$OUT/.src

mkdir -p "$CACHE"
fetch() {   # name ref
    local dir=$CACHE/$1
    [ -d "$dir/.git" ] || git clone -q "https://github.com/Simon-oid/$1" "$dir"
    git -C "$dir" fetch -q origin
    git -C "$dir" checkout -q "$2"
}
fetch cMCP "$CMCP_REF"
fetch cRAG "$CRAG_REF"

rm -rf "$OUT/corpus"
for repo in cMCP cRAG; do
    # Tracked Markdown only, minus test fixtures (unrelated topics, Italian
    # municipal samples) and GitHub issue/PR templates.
    git -C "$CACHE/$repo" ls-files '*.md' |
    grep -v -E '^(tests/manual/corpus|tests/fixtures/|\.github/)' |
    while read -r f; do
        mkdir -p "$OUT/corpus/$repo/$(dirname "$f")"
        cp "$CACHE/$repo/$f" "$OUT/corpus/$repo/$f"
    done
done

rm -f "$OUT/docs.db" "$OUT/docs.db-wal" "$OUT/docs.db-shm"
CRAG_EMBED_MODEL=$EMBED_MODEL "$CRAG" index "$OUT/corpus" --db "$OUT/docs.db" \
    --ext md --backend ollama --url "$OLLAMA_URL"
"$CRAG" stats --db "$OUT/docs.db"
printf 'cMCP %s, cRAG %s, embedder %s\n' "$CMCP_REF" "$CRAG_REF" "$EMBED_MODEL" \
    > "$OUT/corpus.version"
