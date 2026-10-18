"""Parse Claude Code local transcripts (~/.claude/projects/**/*.jsonl). Local only, no network."""
import json, os, glob
from collections import defaultdict
from datetime import datetime, timedelta

# Prompt-cache TTL (https://code.claude.com/docs/en/prompt-caching#which-ttl-each-request-gets):
# Claude subscription, main conversation: 1 hour. API key / usage credits / cloud providers,
# subagents and compaction: 5 minutes.
DEFAULT_IDLE_MIN = 60
SHORT_TTL_MIN = 5
REBUILD_MIN_TOKENS = 5000
# Relative per-token API prices vs. base input (platform.claude.com prompt-caching docs):
# cache read 0.1x, 5-min cache write 1.25x. Used ONLY to rank fixes; plan-limit accounting is unpublished.
W_READ, W_WRITE = 0.1, 1.25

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
    """Parse transcripts. Records idle gaps (minutes, cache-write tokens) per session; thresholds are applied later."""
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
                                          "gaps": [], "model_switches": 0, "switch_tokens": 0, "last_model": None,
                                          "compactions": 0, "models": defaultdict(int)})
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
            cw = u.get("cache_creation_input_tokens", 0) or 0
            if not sub and prev and ts and cw > REBUILD_MIN_TOKENS:
                s["gaps"].append(((ts - prev).total_seconds() / 60, cw))
            m = msg.get("model")
            if not sub and m and not m.startswith("<"):
                if s["last_model"] and m != s["last_model"] and cw > REBUILD_MIN_TOKENS:
                    s["model_switches"] += 1
                    s["switch_tokens"] += cw
                s["last_model"] = m
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

def idle_summary(res, minutes):
    """Count main-conversation cache rebuilds that followed a gap longer than `minutes`."""
    n = toks = 0
    for s in res["sessions"].values():
        for gap, cw in s["gaps"]:
            if gap > minutes:
                n += 1; toks += cw
    return n, toks

def fixes(res, claude_mds, projs, idle_minutes=DEFAULT_IDLE_MIN):
    """Candidate fixes as (score, text). Score = API-price-weighted tokens, for RANKING ONLY.
    Anthropic does not publish how cache reads/writes count against plan limits."""
    S = new_stats()
    for s in res["sessions"].values():
        for k, v in s["stats"].items(): S[k] += v
    grand = total(S) + total(S, "sub_") or 1
    out = []
    turns = max(1, S["turns"])
    for m in [m for m in claude_mds if m["est_tokens"] > 1500]:
        out.append((m["est_tokens"] * turns * W_READ + m["est_tokens"] * W_WRITE * len(res["sessions"]),
                    f"Slim {m['path']} (~{m['est_tokens']:,} tokens; part of every request in every session) to under 1,500 tokens. Move situational sections into skills or docs read on demand. Edits apply after /clear or a restart."))
    n, t = idle_summary(res, idle_minutes)
    if n:
        out.append((t * W_WRITE, f"{n} cache rebuilds after >{idle_minutes} min idle re-wrote ~{t:,} tokens to the cache. After a long break, start a fresh session (or resume from a short summary you paste in) instead of continuing; /compact on an expired cache reprocesses the full history uncached."))
    avg_read = S["cache_read"] // turns
    if avg_read > 80000:
        out.append((S["cache_read"] * W_READ, f"Average context re-read per turn is ~{avg_read:,} tokens. Cache reads are cheap per token, but long sessions make every turn bigger. Start a fresh session per task and /compact at natural breaks between tasks."))
    sw = sum(s["model_switches"] for s in res["sessions"].values())
    if sw:
        st = sum(s["switch_tokens"] for s in res["sessions"].values())
        out.append((st * W_WRITE, f"{sw} mid-session model switches; each makes the next request re-read the whole history uncached (~{st:,} tokens). Pick the model at the start of a session."))
    starts = [c for p in projs.values() for c in p["first_ctx"]]
    if starts:
        avg = sum(starts) // len(starts)
        if avg > 20000:
            out.append((avg * len(starts) * W_WRITE, f"Average first-turn context is {avg:,} tokens. Run /context in a new session to see what loads (CLAUDE.md, skills, MCP tools) and trim what this project doesn't need."))
    subt = total(S, "sub_")
    if subt / grand > 0.3:
        out.append((S["sub_cache_write"] * W_WRITE + S["sub_cache_read"] * W_READ + S["sub_input"] + S["sub_output"] * 5,
                    f"Subagents account for {subt/grand:.0%} of recorded tokens. Each starts its own context; use them for broad searches with narrow prompts."))
    big = [x for x in res["tool_outputs"] if x["est_tokens"] > 5000]
    if big:
        tt = sum(x["est_tokens"] for x in big)
        out.append((tt * W_WRITE, f"{len(big)} tool outputs over 5k tokens (~{tt:,} total, estimated) entered the context. Read files with offsets/limits and pipe command output through head or grep."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out]
