# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao.store import AppError
from fagao.submissions import SubmissionDrafts
import test_membership_server as http_fixtures


FRESH = {"work_title": "合成稿", "pen_name": "甲", "subject": "编辑收", "body": "段落一\n段落二"}
BASE = "/api/submissions/"


class DraftStorageTest(unittest.TestCase):
    def test_parallel_creates_and_stale_edits_are_transactional(self):
        with tempfile.TemporaryDirectory() as temp:
            with ThreadPoolExecutor(max_workers=6) as pool:
                rows = list(pool.map(lambda n: SubmissionDrafts(temp).save(
                    dict(FRESH, body=str(n))), range(12)))
            self.assertEqual(len({row["id"] for row in rows}), 12)
            store = SubmissionDrafts(temp)
            draft = rows[0]
            edit = dict(FRESH, id=draft["id"], revision=1)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(store.save, dict(edit, subject=str(n))) for n in range(2)]
            self.assertEqual(sum(f.exception() is None for f in results), 1)
            failed = next(f.exception() for f in results if f.exception() is not None)
            self.assertEqual(failed.code, "submission_conflict")
            self.assertEqual(store.read({"id": draft["id"]})["revision"], 2)
            with self.assertRaises(AppError) as exc:
                store.delete({"id": draft["id"], "revision": 1})
            self.assertEqual(exc.exception.status, 409)
            self.assertTrue((Path(temp) / "local-submissions" / "drafts.sqlite3").is_file())


class SubmissionRoutesTest(unittest.TestCase):
    def setUp(self):
        self.http = http_fixtures.MembershipServerTest()
        self.http.setUp()
        self.addCleanup(self.http.tearDown)
        self.headers = {"X-Fagao-Request": "1", "Origin": f"http://127.0.0.1:{self.http.port}"}

    def post(self, path, data):
        return self.http.request(BASE + path, "POST", data, self.headers)

    def get(self, path):
        return self.http.request(BASE + path, headers=self.headers)

    def test_offline_lifecycle_preserves_ranking_authorization(self):
        with patch.object(self.http.member, "require", side_effect=AssertionError("membership reached")):
            status, saved, _ = self.post("save", FRESH)
            self.assertEqual(status, 200)
            row = saved["draft"]
            self.assertEqual(row["body"], FRESH["body"])
            listed = self.get("drafts")[1]["drafts"]
            self.assertEqual([x["id"] for x in listed], [row["id"]])
            self.assertNotIn("body", listed[0])
            self.assertEqual(self.get("draft?id=" + row["id"])[1]["draft"], row)
            self.assertEqual(self.post("delete", {"id": row["id"], "revision": 1})[:2],
                             (200, {"deleted": True}))
            self.assertEqual(self.get("draft?id=" + row["id"])[0], 404)
        self.assertEqual(self.http.request("/api/state")[0], 403)
        self.assertEqual(self.get("unknown")[0], 403)

    def test_private_reads_and_mutations_require_local_request(self):
        path = BASE + "drafts"
        self.assertEqual(self.http.request(path)[0], 403)
        for header in ({"Origin": "https://somewhere.invalid"}, {"Sec-Fetch-Site": "same-site"},
                       {"Host": "somewhere.invalid"}, {"Sec-Fetch-Site": "cross-site"},
                       {"Origin": f"http://localhost:{self.http.port}"}, {"X-Fagao-Request": "0"}):
            self.assertEqual(self.http.request(path, headers={**self.headers, **header})[0], 403)
            self.assertEqual(self.http.request(BASE + "save", "POST", FRESH,
                                               headers={**self.headers, **header})[0], 403)
        self.assertEqual(self.get("drafts")[1], {"drafts": []})

    def test_handoff_selects_saved_version_and_accepts_no_external_actions(self):
        row = self.post("save", FRESH)[1]["draft"]
        selection = {"id": row["id"], "revision": row["revision"], "appearance": "eye-care"}
        with patch("fagao.submission_component.open_component", return_value={"opened": True}) as launch:
            self.assertEqual(self.post("open", dict(selection, cmd="test"))[0], 400)
            self.assertEqual(self.post("open", dict(selection, revision=9))[0], 409)
            self.assertEqual(self.post("open", dict(selection, appearance=[]))[0], 400)
            launch.assert_not_called()
            self.assertEqual(self.post("open", selection)[:2], (200, {"opened": True}))
            self.assertEqual(launch.call_args.args[1:], ("eye-care", row))

    def test_invalid_data_does_not_change_store(self):
        for data in (dict(FRESH, subject="😀" * 251), dict(FRESH, recipients=[]),
                     dict(FRESH, body="\ud800"), dict(FRESH, subject="a\nb"),
                     {key: "" for key in FRESH}, dict(FRESH, revision=True)):
            self.assertEqual(self.post("save", data)[0], 400)
        self.assertEqual(self.get("drafts")[1], {"drafts": []})

    def test_external_bookmark_can_open_document_but_never_manuscripts(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                   "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.http.request("/submissions.html", headers=headers)[0], 200)
        self.assertEqual(self.http.request(BASE + "drafts", headers=headers)[0], 403)
