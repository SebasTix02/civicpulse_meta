import httpx

from civicpulse.db import Db
from civicpulse.pipeline.sandbox import cleanup


class FakeDb:
    def __init__(self, gone, files):
        self.gone, self.files, self.removed = gone, files, []

    def rpc(self, fn, args):
        assert fn == "sandbox_cleanup"
        return self.gone

    def list_files(self, bucket, prefix):
        return [n for b, n in self.files if b == bucket and n.startswith(prefix + "/")]

    def remove(self, bucket, names):
        self.removed += [(bucket, n) for n in names]


def test_removes_every_file_of_the_expired_sessions_and_keeps_samples():
    files = [("sandbox", "s1/dev/n.jpg"), ("public", "sandbox/s1/r1/v1.jpg"), ("public", "sandbox/s1/r1/v1_c1.jpg"),
             ("public", "sandbox/resolutions/z1/after.jpg"), ("public", "samples/p/before.jpg"),
             ("public", "sandbox/s2/r9/v1.jpg")]
    db = FakeDb({"sessions": ["s1"], "resolutions": ["z1"]}, files)
    assert cleanup(db) == {"sessions": 1, "resolutions": 1, "files": 4}
    assert ("public", "samples/p/before.jpg") not in db.removed and ("public", "sandbox/s2/r9/v1.jpg") not in db.removed


def test_nothing_expired():
    db = FakeDb({"sessions": [], "resolutions": []}, [])
    assert cleanup(db) == {"sessions": 0, "resolutions": 0, "files": 0}


def test_list_files_walks_folders():
    def handler(req: httpx.Request):
        if req.method == "POST":
            prefix = __import__("json").loads(req.content)["prefix"]
            listing = {"sandbox/s1": [{"name": "r1", "id": None}], "sandbox/s1/r1": [{"name": "v1.jpg", "id": "x"}]}
            return httpx.Response(200, json=listing.get(prefix, []))
        assert __import__("json").loads(req.content) == {"prefixes": ["sandbox/s1/r1/v1.jpg"]}
        return httpx.Response(200, json=[])

    db = Db("https://x.test", "sb_secret_x", httpx.Client(transport=httpx.MockTransport(handler)))
    names = db.list_files("public", "sandbox/s1")
    assert names == ["sandbox/s1/r1/v1.jpg"]
    db.remove("public", names)
