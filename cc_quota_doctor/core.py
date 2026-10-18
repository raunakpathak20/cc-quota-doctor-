"""Parse Claude Code local transcripts (~/.claude/projects/**/*.jsonl). Local only, no network."""
import json, os, glob
from collections import defaultdict
from datetime import datetime, timedelta

CACHE_TTL_MIN = 5  # default prompt-cache TTL (minutes)

def chars_to_tokens(n):  # rough heuristic: ~4 chars/token
    return n // 4

def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def iter_records(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

def _content_len(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(_content_len(x.get("text", x.get("content", ""))) if isinstance(x, dict) else len(str(x)) for x in c)
    return len(str(c or ""))

def new_stats():
    return {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "turns": 0,
            "sub_input": 0, "sub_output": 0, "sub_cache_write": 0, "sub_cache_read": 0, "sub_turns": 0}

def total(s, prefix=""):
    return s[prefix+"input"] + s[prefix+"output"] + s[prefix+"cache_write"] + s[prefix+"cache_read"]

def analyze(root, since=None, until=None):
    root = os.path.expanduser(root)
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    sessions = {}
    tool_outputs = []
    tool_names = {}
    seen_msg = set()
    for path in files:
        rel = os.path.relpath(path, root)
        proj_dir = rel.split(os.sep)[0]
        is_sub_file = (os.sep + "subagents" + os.sep) in path or os.path.basename(path).startswith("agent-")
        last_ts_by_session = {}
        for r in iter_records(path):
            sid = r.get("sessionId") or os.path.splitext(os.path.basename(path))[0]
            ts = parse_ts(r.get("timestamp", "")) if r.get("timestamp") else None
            if ts and since and ts < since: continue
            if ts and until and ts >= until: continue
            s = sessions.setdefault(sid, {"id": sid, "project": r.get("cwd") or proj_dir, "stats": new_stats(),
                                          "first_ts": None, "last_ts": None, "first_ctx": None,
                                          "cache_misses_idle": 0, "compactions": 0, "models": defaultdict(int)})
            if r.get("cwd"): s["project"] = r["cwd"]
            if ts:
                s["first_ts"] = min(filter(None, [s["first_ts"], ts]))
                s["last_ts"] = max(filter(None, [s["last_ts"], ts]))
            if r.get("isCompactSummary") or (r.get("type") == "system" and r.get("subtype") == "compact_boundary"):
                s["compactions"] += 1
            msg = r.get("message") or {}
            typ = r.get("type")
            content = msg.get("content")
            if typ == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tool_names[b.get("id")] = b.get("name", "?")
            if typ == "user" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        n = _content_len(b.get("content"))
                        tool_outputs.append({"session": sid, "project": s["project"], "tool": tool_names.get(b.get("tool_use_id"), "?"),
                                             "chars": n, "est_tokens": chars_to_tokens(n)})
            u = msg.get("usage")
            if typ != "assistant" or not u:
                continue
            key = (msg.get("id"), r.get("requestId"))  # streamed messages repeat usage per content block
            if key[0] and key in seen_msg:
                continue
            seen_msg.add(key)
            sub = bool(r.get("isSidechain")) or is_sub_file
            p = "sub_" if sub else ""
            st = s["stats"]
            st[p+"input"] += u.get("input_tokens", 0) or 0
            st[p+"output"] += u.get("output_tokens", 0) or 0
            st[p+"cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
            st[p+"cache_read"] += u.get("cache_read_input_tokens", 0) or 0
            st[p+"turns"] += 1
            if msg.get("model"): s["models"][msg["model"]] += 1
            ctx = (u.get("input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0) + (u.get("cache_read_input_tokens", 0) or 0)
            if not sub and s["first_ctx"] is None:
                s["first_ctx"] = ctx
            lk = (sid, sub)
            prev = last_ts_by_session.get(lk)
            if prev and ts and ts - prev > timedelta(minutes=CACHE_TTL_MIN) and (u.get("cache_creation_input_tokens", 0) or 0) > 5000:
                s["cache_misses_idle"] += 1
                s.setdefault("idle_rewrite_tokens", 0)
                s["idle_rewrite_tokens"] += u.get("cache_creation_input_tokens", 0)
            if ts: last_ts_by_session[lk] = ts
    return {"sessions": sessions, "tool_outputs": tool_outputs, "files": len(files)}

def find_claude_md(paths, home="~"):
    out = []
    cands = [os.path.join(os.path.expanduser(home), ".claude", "CLAUDE.md")]
    for p in paths:
        cands += [os.path.join(p, "CLAUDE.md"), os.path.join(p, ".claude", "CLAUDE.md"), os.path.join(p, "CLAUDE.local.md")]
    for c in dict.fromkeys(cands):
        if os.path.isfile(c):
            n = os.path.getsize(c)
            out.append({"path": c, "bytes": n, "est_tokens": chars_to_tokens(n)})
    return out

def project_rollup(res):
    projs = defaultdict(lambda: {"stats": new_stats(), "sessions": 0, "first_ctx": []})
    for s in res["sessions"].values():
        p = projs[s["project"]]; p["sessions"] += 1
        for k, v in s["stats"].items(): p["stats"][k] += v
        if s["first_ctx"]: p["first_ctx"].append(s["first_ctx"])
    return projs

def fixes(res, claude_mds, projs):
    """Return candidate fixes as (impact_score, text). Impact = estimated tokens involved."""
    S = new_stats()
    for s in res["sessions"].values():
        for k, v in s["stats"].items(): S[k] += v
    grand = total(S) + total(S, "sub_") or 1
    out = []
    big_md = [m for m in claude_mds if m["est_tokens"] > 2000]
    for m in big_md:
        out.append((m["est_tokens"] * max(1, S["turns"]), f"Slim {m['path']} (~{m['est_tokens']:,} tokens, re-sent every turn). Move rarely-used sections into skills or docs loaded on demand; aim for < 1,500 tokens."))
    starts = [c for p in projs.values() for c in p["first_ctx"]]
    if starts:
        avg = sum(starts) // len(starts)
        if avg > 20000:
            out.append((avg * len(starts), f"Average first-turn context is {avg:,} tokens. Run /context in a new session and disable unused MCP servers, skills and plugins for projects that don't need them."))
    idle = sum(s.get("idle_rewrite_tokens", 0) for s in res["sessions"].values())
    if idle:
        n = sum(s["cache_misses_idle"] for s in res["sessions"].values())
        out.append((idle, f"{n} cache rebuilds after >{CACHE_TTL_MIN} min idle cost ~{idle:,} cache-write tokens. After a long break, /clear or /compact before continuing instead of resuming a huge context."))
    subt = total(S, "sub_")
    if subt / grand > 0.3:
        out.append((subt, f"Subagents used {subt/grand:.0%} of tokens. Each subagent starts its own context; reserve them for broad searches and give them narrow prompts."))
    big_tools = [t for t in res["tool_outputs"] if t["est_tokens"] > 5000]
    if big_tools:
        tt = sum(t["est_tokens"] for t in big_tools)
        out.append((tt * 3, f"{len(big_tools)} tool outputs over 5k tokens (~{tt:,} total) stayed in context. Limit file reads with offsets, pipe command output through head/grep, and avoid dumping logs."))
    if S["turns"] and S["cache_read"] / max(1, S["turns"]) > 80000:
        out.append((S["cache_read"], f"Average cache read per turn is {S['cache_read']//S['turns']:,} tokens: sessions run long. Start fresh sessions per task and /compact earlier."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out]
