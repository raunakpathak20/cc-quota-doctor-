import os, sys, json, tempfile, unittest, io, contextlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_sample import make
from cc_quota_doctor.cli import main, build_report
from cc_quota_doctor.share import build_share, check_no_leaks, ShareLeak, letter_label

class Share(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(); cls.home = os.path.join(cls.tmp, "home")
        cls.root = os.path.join(cls.home, ".claude", "projects"); make(cls.root, cls.home)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cls.rc = main(["--projects-dir", cls.root, "--home", cls.home, "--share"])
        cls.raw = buf.getvalue(); cls.d = json.loads(cls.raw)
    def test_valid_and_versioned(self):
        self.assertEqual(self.rc, 0)
        self.assertEqual(self.d["format"], "cc-quota-doctor-share/1")
        self.assertEqual(self.d["tool_version"], "0.3.0")
        self.raw.encode("ascii")
    def test_no_private_strings(self):
        for bad in [self.tmp, self.home, "webapp", "tiny", "-home-u-code", "aaaaaaaa", "bbbbbbbb",
                    "fix the bug", "cat big.log", "style guide", "Use pytest", "xxxxxxxx", "/home", "/Users", ":\\\\"]:
            self.assertNotIn(bad, self.raw, bad)
    def test_projects_anonymised(self):
        labs = [p["label"] for p in self.d["projects"]]
        self.assertEqual(labs, ["Project A", "Project B"])
        self.assertEqual(self.d["projects"][0]["sessions"], 1)  # webapp = biggest = A
        self.assertEqual(self.d["top_sessions"][0]["label"], "S1")
        self.assertEqual(self.d["top_sessions"][0]["project"], "Project A")
    def test_numbers_match_full_report(self):
        self.assertEqual(self.d["idle_rebuilds"]["over_60min"], {"count": 1, "cache_write_tokens": 50000})
        self.assertEqual(self.d["idle_rebuilds"]["over_5min"]["count"], 2)
        self.assertEqual(self.d["model_switches"]["count"], 1)
        self.assertEqual(self.d["compactions"], 1)
        md = {m["label"]: m["est_tokens"] for m in self.d["claude_md"]}
        self.assertGreater(md["Project A CLAUDE.md"], 1500)
        self.assertTrue(any("Slim Project A CLAUDE.md" in f for f in self.d["fixes"]))
        self.assertEqual(self.d["tool_outputs"]["largest"][0], {"tool": "Bash", "est_tokens": 30000})
    def test_out_file(self):
        p = os.path.join(self.tmp, "share.json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--projects-dir", self.root, "--home", self.home, "--share", "--out", p]), 0)
        with open(p, encoding="utf-8") as f: self.assertEqual(json.load(f), self.d)
    def test_leak_check_trips(self):
        res, projs, _, _ = build_report(self.root, self.home)
        out = build_share(res, projs, self.home)
        out["fixes"].append("see " + next(iter(projs)))
        with self.assertRaises(ShareLeak): check_no_leaks(out, res, projs, self.home)
        out["fixes"][-1] = "folder webapp is big"
        with self.assertRaises(ShareLeak): check_no_leaks(out, res, projs, self.home)
    def test_labels(self):
        self.assertEqual([letter_label(i) for i in (0, 25, 26, 27)], ["A", "Z", "AA", "AB"])
    def test_mcp_tool_names_hidden(self):
        from cc_quota_doctor.share import _tool, _model
        self.assertEqual(_tool("mcp__acme-internal__query"), "mcp"); self.assertEqual(_tool("Read"), "Read")
        self.assertEqual(_tool("CustomSecretTool"), "other")
        self.assertEqual(_model("arn:aws:bedrock:us-east-1:123456789012:x"), "other")

if __name__ == "__main__": unittest.main()
