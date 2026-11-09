"""A stand-in for crag-mcp: newline-delimited JSON-RPC over stdio, enough
of MCP for the client (initialize, tools/call crag_search).

Behaviour is set by environment variables, so tests can make it fail:
  FAKE_MODE=ok        two passages for any query (default)
  FAKE_MODE=nomatch   the "no chunk cleared" sentinel
  FAKE_MODE=toolerror isError result
  FAKE_MODE=garbage   an item that is not in crag_search format
  query "slow"        answer after 1 s (a slow embedder)
  FAKE_DIE_AFTER=N    exit (as if crashed) on the N-th tools/call, once:
                      a marker file in FAKE_STATE_DIR keeps the restarted
                      server alive
"""

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_MODE", "ok")
DIE_AFTER = int(os.environ.get("FAKE_DIE_AFTER", "0"))
STATE = os.environ.get("FAKE_STATE_DIR", "")
ROOT = "/abs/build/host/data/corpus/"
NO_MATCH = ("(no chunk cleared the relevance threshold — the index is non-empty "
            "but nothing matched this query)")


def items(query: str) -> tuple[list[dict], bool]:
    if MODE == "nomatch":
        return [{"type": "text", "text": NO_MATCH}], False
    if MODE == "toolerror":
        return [{"type": "text", "text": "embedding failed: connection refused"}], True
    if MODE == "garbage":
        return [{"type": "text", "text": "not a crag item"}], False
    return [
        {"type": "text", "text": f"[cos 0.712  bm25 9.88  fusion 0.033] {ROOT}cMCP/docs/a.md\n"
                                 f"Alpha passage about {query}."},
        {"type": "text", "text": f"[cos 0.601  bm25 0.00  fusion 0.016] {ROOT}cRAG/README.md\n"
                                 "Beta passage."},
    ], False


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    calls = 0
    for line in sys.stdin:
        msg = json.loads(line)
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue                                   # notification
        if method == "initialize":
            reply(msg_id, {"protocolVersion": msg["params"]["protocolVersion"],
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake-crag-mcp", "version": "0"}})
        elif method == "tools/list":
            reply(msg_id, {"tools": [{"name": "crag_search", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"},
                                                 "k": {"type": "integer"}},
                "required": ["query"]}}]})
        elif method == "tools/call":
            calls += 1
            marker = os.path.join(STATE, "died") if STATE else ""
            if DIE_AFTER and calls == DIE_AFTER and marker and not os.path.exists(marker):
                open(marker, "w").close()
                sys.exit(3)
            query = msg["params"]["arguments"]["query"]
            if query == "slow":
                time.sleep(1.0)           # longer than the test's MCP timeout
            content, is_error = items(query)
            reply(msg_id, {"content": content, "isError": is_error})
        else:
            reply(msg_id, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
