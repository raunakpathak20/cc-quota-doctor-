import argparse, json, os, sys
from .core import analyze, find_claude_md, project_rollup, fixes, total, new_stats, idle_summary, DEFAULT_IDLE_MIN, SHORT_TTL_MIN
from . import __version__

def fmt(n): return f"{n:,}"

def build_report(root, home, top=5, since=None, until=None, idle_minutes=DEFAULT_IDLE_MIN):
    res = analyze(root, since, until)
    projs = project_rollup(res)
    mds = find_claude_md([p for p in projs if os.path.isdir(str(p))], home)
    return res, projs, mds, fixes(res, mds, projs, idle_minutes)

def render_text(res, projs, mds, fx, top=5, idle_minutes=DEFAULT_IDLE_MIN):
    L = []
    S = new_stats()
    for s in res["sessions"].values():
        for k, v in s["stats"].items(): S[k] += v
    g = total(S) + total(S, "sub_")
    L.append(f"cc-quota-doctor {__version__} - {res['files']} transcript files, {len(res['sessions'])} sessions")
    L.append("Raw tokens from local transcripts. Token types are priced differently (cache reads are cheap")
    L.append("per token) and Anthropic does not publish how each type counts against plan limits.\n")
    L.append("== Totals ==")
    for lab, k in [("input", "input"), ("output", "output"), ("cache write", "cache_write"), ("cache read*", "cache_read")]:
        v = S[k] + S["sub_"+k]
        L.append(f"  {lab:<12}{fmt(v):>15}  ({v/g:.0%})" if g else f"  {lab}: 0")
    L.append("  * cache reads: share of raw tokens, not share of quota")
    if g: L.append(f"  subagents   {fmt(total(S,'sub_')):>15}  ({total(S,'sub_')/g:.0%} of all tokens)")
    L.append("\n== Top projects ==")
    for name, p in sorted(projs.items(), key=lambda kv: -(total(kv[1]['stats'])+total(kv[1]['stats'],'sub_')))[:top]:
        t = total(p["stats"]) + total(p["stats"], "sub_")
        avg = sum(p["first_ctx"])//len(p["first_ctx"]) if p["first_ctx"] else 0
        L.append(f"  {fmt(t):>13}  {p['sessions']} sessions, avg startup ctx {fmt(avg)}  {name}")
    L.append("\n== Top sessions ==")
    for s in sorted(res["sessions"].values(), key=lambda s: -(total(s['stats'])+total(s['stats'],'sub_')))[:top]:
        t = total(s["stats"]) + total(s["stats"], "sub_")
        L.append(f"  {fmt(t):>13}  {s['id'][:8]}  turns={s['stats']['turns']+s['stats']['sub_turns']} idle>{idle_minutes}m={sum(1 for x, _ in s['gaps'] if x > idle_minutes)} model-switches={s['model_switches']} compactions={s['compactions']}  {s['project']}")
    L.append("\n== Cache rebuilds after idle (main conversation) ==")
    n1, t1 = idle_summary(res, DEFAULT_IDLE_MIN); n2, t2 = idle_summary(res, SHORT_TTL_MIN)
    L.append(f"  gaps > {DEFAULT_IDLE_MIN} min: {n1} rebuilds, ~{fmt(t1)} cache-write tokens  (expired on any plan)")
    L.append(f"  gaps > {SHORT_TTL_MIN} min:  {n2} rebuilds, ~{fmt(t2)} cache-write tokens  (expired only on the 5-min TTL: API key/usage credits)")
    if idle_minutes not in (DEFAULT_IDLE_MIN, SHORT_TTL_MIN):
        n3, t3 = idle_summary(res, idle_minutes)
        L.append(f"  gaps > {idle_minutes} min (--idle-minutes): {n3} rebuilds, ~{fmt(t3)} cache-write tokens")
    L.append("  Subscription main-conversation TTL is 1 hour; API-key/usage-credit users get 5 min. Use --idle-minutes 5 for API keys.")
    L.append("\n== CLAUDE.md files (loaded at startup) ==")
    L += [f"  ~{fmt(m['est_tokens']):>7} tokens  {m['path']}" for m in mds] or ["  none found"]
    L.append("\n== Largest tool outputs (estimated, ~4 chars/token) ==")
    for t in sorted(res["tool_outputs"], key=lambda t: -t["chars"])[:top]:
        L.append(f"  ~{fmt(t['est_tokens']):>7}  {t['tool']:<10} session {t['session'][:8]}")
    L.append("\n== Top 3 fixes ==")
    L += [f"  {i}. {f}" for i, f in enumerate(fx[:3], 1)] or ["  Nothing obvious. Your usage looks lean."]
    return "\n".join(L)

def main(argv=None):
    ap = argparse.ArgumentParser(prog="cc-quota-doctor", description="Diagnose what burns Claude Code quota, from local transcripts. Offline.")
    ap.add_argument("--projects-dir", default="~/.claude/projects")
    ap.add_argument("--home", default="~", help="home dir used to find ~/.claude/CLAUDE.md")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MIN,
                    help="idle gap treated as a cache expiry (default 60 = subscription TTL; use 5 for API key/usage credits)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(errors="replace")
        except Exception: pass
    if not os.path.isdir(os.path.expanduser(a.projects_dir)):
        print(f"No transcripts at {a.projects_dir}", file=sys.stderr); return 1
    res, projs, mds, fx = build_report(a.projects_dir, a.home, a.top, idle_minutes=a.idle_minutes)
    if a.json:
        print(json.dumps({"sessions": {k: {**v, "first_ts": str(v["first_ts"]), "last_ts": str(v["last_ts"])} for k, v in res["sessions"].items()},
                          "claude_md": mds, "fixes": fx, "idle_minutes": a.idle_minutes,
                          "idle_rebuilds": dict(zip(["count", "cache_write_tokens"], idle_summary(res, a.idle_minutes))),
                          "idle_rebuilds_5min": dict(zip(["count", "cache_write_tokens"], idle_summary(res, SHORT_TTL_MIN)))}, default=str, indent=2))
    else:
        print(render_text(res, projs, mds, fx, a.top, a.idle_minutes))
    return 0

if __name__ == "__main__":
    sys.exit(main())
