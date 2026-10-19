"""Redacted "share" report: numbers only, safe to send to someone else.

Removed: file paths, project/folder names, session ids, prompts, assistant text,
tool inputs/outputs, CLAUDE.md contents, per-message timestamps, non-standard model ids.
Kept: token totals, counts, sizes, and anonymous labels (Project A/B/..., S1/S2/...).
A self-check refuses to print the report if any known path, name or id slipped through.
"""
import json, os, re
from .core import (new_stats, total, idle_summary, fixes, chars_to_tokens,
                   DEFAULT_IDLE_MIN, SHORT_TTL_MIN)

SHARE_FORMAT = "cc-quota-doctor-share/1"
BUILTIN_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "Bash", "Grep", "Glob", "LS", "WebFetch", "WebSearch",
                 "Task", "Agent", "TodoWrite", "NotebookEdit", "NotebookRead", "BashOutput", "KillShell",
                 "ExitPlanMode", "Skill", "SlashCommand", "AskUserQuestion", "ToolSearch"}
MODEL_RE = re.compile(r"^claude-[a-z0-9.\-]+$")


class ShareLeak(Exception):
    pass


def letter_label(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _tool(name):
    name = str(name or "?")
    if name in BUILTIN_TOOLS:
        return name
    return "mcp" if name.startswith("mcp__") else "other"


def _model(m):
    return m if MODEL_RE.match(m or "") else "other"


def _basename(p):
    return [x for x in re.split(r"[\\/]", str(p)) if x][-1:] or [""]


def _claude_mds(proj_labels, home):
    """CLAUDE.md sizes with anonymous labels instead of paths."""
    out = []
    user = os.path.join(os.path.expanduser(home), ".claude", "CLAUDE.md")
    if os.path.isfile(user):
        out.append({"label": "user CLAUDE.md (~/.claude)", "est_tokens": chars_to_tokens(os.path.getsize(user))})
    seen = {os.path.normcase(os.path.abspath(user))}
    for path, lab in proj_labels.items():
        if not os.path.isdir(str(path)):
            continue
        for rel, kind in (("CLAUDE.md", "CLAUDE.md"), (os.path.join(".claude", "CLAUDE.md"), ".claude/CLAUDE.md"),
                          ("CLAUDE.local.md", "CLAUDE.local.md")):
            c = os.path.join(path, rel)
            k = os.path.normcase(os.path.abspath(c))
            if os.path.isfile(c) and k not in seen:
                seen.add(k)
                out.append({"label": f"{lab} {kind}", "est_tokens": chars_to_tokens(os.path.getsize(c))})
    return out


def build_share(res, projs, home="~", top=5, idle_minutes=DEFAULT_IDLE_MIN, check=True):
    T = lambda st: total(st) + total(st, "sub_")
    order = sorted(projs.items(), key=lambda kv: -T(kv[1]["stats"]))
    labels = {name: f"Project {letter_label(i)}" for i, (name, _) in enumerate(order)}

    S = new_stats()
    for s in res["sessions"].values():
        for k, v in s["stats"].items():
            S[k] += v
    g = T(S)
    turns = max(1, S["turns"])

    projects = []
    for name, p in order:
        ss = [s for s in res["sessions"].values() if s["project"] == name]
        projects.append({
            "label": labels[name], "sessions": p["sessions"], "tokens": T(p["stats"]),
            "turns": p["stats"]["turns"] + p["stats"]["sub_turns"],
            "avg_startup_context": sum(p["first_ctx"]) // len(p["first_ctx"]) if p["first_ctx"] else 0,
            "idle_rebuilds_60min": sum(1 for s in ss for x, _ in s["gaps"] if x > DEFAULT_IDLE_MIN),
            "model_switches": sum(s["model_switches"] for s in ss),
            "compactions": sum(s["compactions"] for s in ss),
        })

    top_sessions = []
    for i, s in enumerate(sorted(res["sessions"].values(), key=lambda s: -T(s["stats"]))[:top], 1):
        dur = (s["last_ts"] - s["first_ts"]).total_seconds() / 3600 if s["first_ts"] and s["last_ts"] else 0
        top_sessions.append({
            "label": f"S{i}", "project": labels.get(s["project"], "Project ?"), "tokens": T(s["stats"]),
            "turns": s["stats"]["turns"] + s["stats"]["sub_turns"], "span_hours": round(dur, 1),
            "idle_rebuilds_60min": sum(1 for x, _ in s["gaps"] if x > DEFAULT_IDLE_MIN),
            "idle_rebuilds_5min": sum(1 for x, _ in s["gaps"] if x > SHORT_TTL_MIN),
            "model_switches": s["model_switches"], "compactions": s["compactions"],
            "first_turn_context": s["first_ctx"] or 0,
        })

    models = {}
    for s in res["sessions"].values():
        for m, n in s["models"].items():
            if m.startswith("<"):
                continue
            models[_model(m)] = models.get(_model(m), 0) + n

    tools = {}
    for t in res["tool_outputs"]:
        d = tools.setdefault(_tool(t["tool"]), {"calls": 0, "est_tokens": 0, "over_5k": 0})
        d["calls"] += 1; d["est_tokens"] += t["est_tokens"]; d["over_5k"] += t["est_tokens"] > 5000
    largest = [{"tool": _tool(t["tool"]), "est_tokens": t["est_tokens"]}
               for t in sorted(res["tool_outputs"], key=lambda t: -t["chars"])[:top]]

    mds = _claude_mds(labels, home)
    fx = fixes(res, [{"path": m["label"], "est_tokens": m["est_tokens"]} for m in mds], projs, idle_minutes)

    dates = [d for s in res["sessions"].values() for d in (s["first_ts"], s["last_ts"]) if d]
    n60, t60 = idle_summary(res, DEFAULT_IDLE_MIN); n5, t5 = idle_summary(res, SHORT_TTL_MIN)
    from . import __version__
    out = {
        "format": SHARE_FORMAT, "tool_version": __version__,
        "note": "Redacted: no paths, project names, session ids, prompts, code or tool contents. Raw tokens only; "
                "Anthropic does not publish how each token type counts against plan limits.",
        "period": {"first_day": min(dates).date().isoformat() if dates else None,
                   "last_day": max(dates).date().isoformat() if dates else None},
        "sessions": len(res["sessions"]), "transcript_files": res["files"], "projects_count": len(projs),
        "totals": {k: S[k] + S["sub_" + k] for k in ("input", "output", "cache_write", "cache_read")},
        "main": {k: S[k] for k in ("input", "output", "cache_write", "cache_read", "turns")},
        "subagents": {k: S["sub_" + k] for k in ("input", "output", "cache_write", "cache_read", "turns")},
        "subagent_share": round(T({**new_stats(), **{"sub_" + k: S["sub_" + k] for k in ("input", "output", "cache_write", "cache_read")}}) / g, 3) if g else 0,
        "avg_context_reread_per_turn": S["cache_read"] // turns,
        "idle_minutes_used_for_fixes": idle_minutes,
        "idle_rebuilds": {"over_60min": {"count": n60, "cache_write_tokens": t60},
                          "over_5min": {"count": n5, "cache_write_tokens": t5}},
        "model_switches": {"count": sum(s["model_switches"] for s in res["sessions"].values()),
                           "cache_write_tokens": sum(s["switch_tokens"] for s in res["sessions"].values())},
        "compactions": sum(s["compactions"] for s in res["sessions"].values()),
        "longest_session_turns": max((s["stats"]["turns"] + s["stats"]["sub_turns"] for s in res["sessions"].values()), default=0),
        "models_by_turns": models,
        "claude_md": mds,
        "tool_outputs": {"by_tool": tools, "largest": largest},
        "projects": projects, "top_sessions": top_sessions,
        "fixes": fx,
    }
    if check:
        check_no_leaks(out, res, projs, home)
    return out


def project_labels(projs):
    """Same anonymous labels build_share uses: Project A = most tokens."""
    T = lambda st: total(st) + total(st, "sub_")
    order = sorted(projs.items(), key=lambda kv: -T(kv[1]["stats"]))
    return {name: f"Project {letter_label(i)}" for i, (name, _) in enumerate(order)}


def _strings(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield str(k); yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)
    elif isinstance(o, str):
        yield o


def check_no_leaks(out, res, projs, home="~"):
    """Raise ShareLeak if any path, project folder name or session id appears in the report."""
    exact = {os.path.expanduser(home)}
    names = set()
    for p in list(projs) + [s["project"] for s in res["sessions"].values()]:
        exact.add(str(p)); names.add(_basename(p)[0])
    exact |= set(res["sessions"])
    exact |= {sid[:8] for sid in res["sessions"]}
    exact = {e for e in exact if e and len(e) >= 4 and e not in ("/", "~")}
    blob = "\n".join(_strings(out))
    # Words our own code prints (e.g. "cache", "session", "claude") can't reveal a folder name.
    names = {n for n in names if len(n) >= 3 and n.lower() not in _own_vocab(out)}
    for e in exact:
        if e in blob:
            raise ShareLeak("a path or session id")
    for n in names:
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", blob):
            raise ShareLeak("a project folder name")
    return True


def _own_vocab(out):
    here = os.path.dirname(os.path.abspath(__file__))
    words = set()
    for f in ("core.py", "share.py", "cli.py"):
        try:
            with open(os.path.join(here, f), encoding="utf-8") as fh:
                words |= set(re.findall(r"[a-z0-9_.\-]+", fh.read().lower()))
        except OSError:
            pass
    words |= {m.lower() for m in out.get("models_by_turns", {})}
    return words


def dumps(out):
    return json.dumps(out, indent=2, ensure_ascii=True)
