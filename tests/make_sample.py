"""Generate synthetic Claude Code transcripts (no real user data)."""
import json, os, sys, uuid
from datetime import datetime, timedelta, timezone

def rec(sid, cwd, ts, typ, message, **kw):
    return {"type": typ, "sessionId": sid, "cwd": cwd, "uuid": str(uuid.uuid4()),
            "timestamp": ts.isoformat().replace("+00:00", "Z"), "message": message, **kw}

def asst(i, inp, out, cw, cr, tool=None, model="claude-sonnet-4-5"):
    content = [{"type": "text", "text": "ok"}]
    if tool: content.append({"type": "tool_use", "id": tool, "name": "Bash", "input": {"command": "cat big.log"}})
    return {"id": f"msg_{i}", "role": "assistant", "model": model, "content": content,
            "usage": {"input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}

def make(root, home):
    t0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    proj_a = os.path.join(home, "code", "webapp"); os.makedirs(proj_a, exist_ok=True)
    proj_b = os.path.join(home, "code", "tiny"); os.makedirs(proj_b, exist_ok=True)
    open(os.path.join(proj_a, "CLAUDE.md"), "w").write("# Rules\n" + "Always follow the style guide in detail. " * 1200)
    open(os.path.join(proj_b, "CLAUDE.md"), "w").write("# Tiny\nUse pytest.\n")
    # Session A: bloated startup, idle gap, big tool output, subagent
    sa = "aaaaaaaa-0000-4000-8000-000000000001"
    d = os.path.join(root, "-home-u-code-webapp"); os.makedirs(d, exist_ok=True)
    L = [rec(sa, proj_a, t0, "user", {"role": "user", "content": "fix the bug"})]
    L.append(rec(sa, proj_a, t0 + timedelta(seconds=5), "assistant", asst(1, 10, 400, 45000, 0, tool="tu1"), requestId="r1"))
    L.append(rec(sa, proj_a, t0 + timedelta(seconds=5), "assistant", asst(1, 10, 400, 45000, 0), requestId="r1"))  # streamed duplicate
    L.append(rec(sa, proj_a, t0 + timedelta(seconds=20), "user", {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "x" * 120000}]}))
    L.append(rec(sa, proj_a, t0 + timedelta(seconds=30), "assistant", asst(2, 5, 300, 30000, 45000), requestId="r2"))
    L.append(rec(sa, proj_a, t0 + timedelta(minutes=40), "assistant", asst(3, 5, 200, 76000, 0), requestId="r3"))  # idle rebuild
    L.append(rec(sa, proj_a, t0 + timedelta(minutes=41), "assistant", asst(4, 5, 900, 2000, 60000), requestId="r4", isSidechain=True))
    # 3h later, different model: counts as >60-min idle rebuild AND a model switch
    L.append(rec(sa, proj_a, t0 + timedelta(hours=4), "assistant", asst(7, 5, 100, 50000, 0, model="claude-opus-4-1"), requestId="r7"))
    L.append({"type": "system", "subtype": "compact_boundary", "sessionId": sa, "timestamp": (t0 + timedelta(minutes=50)).isoformat()})
    with open(os.path.join(d, sa + ".jsonl"), "w") as f:
        f.write("\n".join(json.dumps(x) for x in L) + "\nnot json garbage\n")
    # Subagent file (newer layout)
    sd = os.path.join(d, sa, "subagents"); os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "agent-1.jsonl"), "w") as f:
        f.write(json.dumps(rec(sa, proj_a, t0 + timedelta(minutes=42), "assistant", asst(5, 20, 1500, 20000, 80000), requestId="r5")) + "\n")
    # Session B: lean
    sb = "bbbbbbbb-0000-4000-8000-000000000002"
    d2 = os.path.join(root, "-home-u-code-tiny"); os.makedirs(d2, exist_ok=True)
    with open(os.path.join(d2, sb + ".jsonl"), "w") as f:
        f.write(json.dumps(rec(sb, proj_b, t0 + timedelta(days=1), "assistant", asst(6, 50, 100, 3000, 0), requestId="r6")) + "\n")

if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "sample"
    base = os.path.abspath(base); make(os.path.join(base, "home", ".claude", "projects"), os.path.join(base, "home"))
    print("sample written to", base)
