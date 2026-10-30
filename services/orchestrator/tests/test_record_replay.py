import json
import sys
from pathlib import Path

import pytest

from civicpulse.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import record_replay as rr

# replay_export() of the smoke_resolutions run (migration 17), plus an intake with a crop.
EXPORT = json.loads((Path(__file__).parent / "fixtures" / "replay_export.json").read_text())


class FakeDb:
    def __init__(self):
        self.downloaded = []

    def download(self, path):
        self.downloaded.append(path)
        return b"jpeg"


def test_scenario_has_local_photos_and_no_database_ids(tmp_path):
    db = FakeDb()
    out = rr.build(json.loads(json.dumps(EXPORT)), rr.Photos(db, tmp_path, dry_run=False))
    text = json.dumps(out)
    assert not rr.UUID.search(text)
    report = out["reports"][0]
    assert report["ref"] == "r_d2000000" and report["photo"] == "r_d2000000.jpg"
    assert report["intake"]["candidates"][0]["crop"] == "r_d2000000_c1.jpg"
    assert "public_path" not in report["intake"]["anonymization"]
    assert out["dispatches"][0]["brief"]["citations"][0] == {"id": "r_d2000000", "kind": "report", "ref": "r_d2000000"}
    assert out["resolutions"][1]["outcome"] == "resolved"
    assert (tmp_path / "r_d2000000.jpg").read_bytes() == b"jpeg"
    assert all(p.startswith("public/") for p in db.downloaded)


def test_never_downloads_a_private_photo(tmp_path):
    export = json.loads(json.dumps(EXPORT))
    export["reports"][0]["photo_public_path"] = "raw/device/nonce.jpg"
    with pytest.raises(ValueError, match="not a public copy"):
        rr.build(export, rr.Photos(FakeDb(), tmp_path, dry_run=False))
