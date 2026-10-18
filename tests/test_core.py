import os, sys, tempfile, unittest, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_sample import make
from cc_quota_doctor.core import analyze, total
from cc_quota_doctor.cli import main, build_report

class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.home = os.path.join(cls.tmp, "home")
        cls.root = os.path.join(cls.home, ".claude", "projects"); make(cls.root, cls.home)
    def test_dedupe_and_totals(self):
        s = analyze(self.root)["sessions"]["aaaaaaaa-0000-4000-8000-000000000001"]["stats"]
        self.assertEqual(s["cache_write"], 45000 + 30000 + 76000)  # duplicate r1 counted once
        self.assertEqual(s["sub_cache_read"], 60000 + 80000)       # sidechain + subagents/ file
        self.assertEqual(s["turns"], 3); self.assertEqual(s["sub_turns"], 2)
    def test_idle_and_compaction(self):
        s = analyze(self.root)["sessions"]["aaaaaaaa-0000-4000-8000-000000000001"]
        self.assertEqual(s["cache_misses_idle"], 1); self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["first_ctx"], 45010)
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
