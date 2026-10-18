import os, sys, tempfile, unittest, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_sample import make
from cc_quota_doctor.core import analyze, total, idle_summary
from cc_quota_doctor.cli import main, build_report

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.home = os.path.join(cls.tmp, "home")
        cls.root = os.path.join(cls.home, ".claude", "projects"); make(cls.root, cls.home)
    def test_dedupe_and_totals(self):
        s = analyze(self.root)["sessions"]["aaaaaaaa-0000-4000-8000-000000000001"]["stats"]
        self.assertEqual(s["cache_write"], 45000 + 30000 + 76000 + 50000)  # duplicate r1 counted once
        self.assertEqual(s["sub_cache_read"], 60000 + 80000)       # sidechain + subagents/ file
        self.assertEqual(s["turns"], 4); self.assertEqual(s["sub_turns"], 2)
    def test_idle_and_compaction(self):
        s = analyze(self.root)["sessions"]["aaaaaaaa-0000-4000-8000-000000000001"]
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["model_switches"], 1); self.assertEqual(s["switch_tokens"], 50000)
        self.assertEqual(s["first_ctx"], 45010)
    def test_idle_thresholds(self):
        res = analyze(self.root)
        self.assertEqual(idle_summary(res, 60), (1, 50000))           # 1h subscription TTL
        self.assertEqual(idle_summary(res, 5), (2, 76000 + 50000))    # 5-min TTL (API key)
    def test_idle_minutes_flag_and_labels(self):
        out = self._run("--idle-minutes", "5")
        self.assertIn("gaps > 5 min", out); self.assertIn("gaps > 60 min", out)
        self.assertIn("2 cache rebuilds after >5 min idle", out)
        out60 = self._run()
        self.assertIn("1 cache rebuilds after >60 min idle", out60)
    def test_fix_wording_is_honest(self):
        out = self._run()
        fx = out.split("Top 3 fixes")[1]
        self.assertIn("fresh session", fx)
        self.assertNotIn("/clear or /compact before continuing", out)
        self.assertIn("not share of quota", out)
        _, _, _, allfx = build_report(self.root, self.home)
        self.assertTrue(any("model switch" in f and "uncached" in f for f in allfx))
        self.assertTrue(any("/compact on an expired cache" in f for f in allfx))
    def test_json_has_both_counts(self):
        import json
        d = json.loads(self._run("--json"))
        self.assertEqual(d["idle_rebuilds"]["count"], 1); self.assertEqual(d["idle_rebuilds_5min"]["count"], 2)
    def _run(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): main(["--projects-dir", self.root, "--home", self.home, *extra])
        return buf.getvalue()
    def test_tool_output(self):
        t = analyze(self.root)["tool_outputs"]
        self.assertEqual(t[0]["tool"], "Bash"); self.assertEqual(t[0]["est_tokens"], 30000)
    def test_fixes_and_cli(self):
        _, _, mds, fx = build_report(self.root, self.home)
        self.assertTrue(any(m["est_tokens"] > 2000 for m in mds))
        self.assertGreaterEqual(len(fx), 3)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = main(["--projects-dir", self.root, "--home", self.home])
        self.assertEqual(rc, 0); self.assertIn("Top 3 fixes", buf.getvalue())
    def test_ascii_output(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): main(["--projects-dir", self.root, "--home", self.home])
        buf.getvalue().encode("ascii")
    def test_missing_dir(self):
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(main(["--projects-dir", "/nonexistent"]), 1)

if __name__ == "__main__": unittest.main()
