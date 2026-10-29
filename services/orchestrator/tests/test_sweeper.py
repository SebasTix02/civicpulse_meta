from datetime import UTC, datetime, timedelta

from civicpulse.pipeline.sweeper import sweep

NOW = datetime(2026, 10, 28, 17, 0, tzinfo=UTC)


def ago(seconds: int) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat()


class FakeDb:
    def __init__(self, reports, runs):
        self.reports, self.runs, self.queries, self.moved = reports, runs, [], []

    def select(self, table, params):
        self.queries.append((table, params))
        if table == "reports":
            return self.reports
        return [r for r in self.runs if f"eq.{r['report_id']}" == params["report_id"]]

    def compare_and_set(self, table, id_, expected, patch):
        self.moved.append((id_, expected, patch))
        return True


def test_spawns_stuck_skips_running_and_fails_after_max_attempts():
    reports = [{"id": "stuck", "status": "spec_ready", "current_photo_version": 1},
               {"id": "busy", "status": "spec_ready", "current_photo_version": 1},
               {"id": "broken", "status": "spec_ready", "current_photo_version": 1}]
    runs = [{"report_id": "stuck", "status": "error", "started_at": ago(600)},
            {"report_id": "busy", "status": "ok", "started_at": ago(30)},
            *({"report_id": "broken", "status": "error", "started_at": ago(300 + i)} for i in range(5))]
    db, spawned = FakeDb(reports, runs), []
    out = sweep(db, lambda kind, key: spawned.append((kind, key)), NOW, max_attempts=5)
    assert out == {"spawned": ["stuck"], "running": ["busy"], "failed": ["broken"]}
    assert spawned == [("intake", "stuck")]
    assert db.moved == [("broken", "spec_ready", {"status": "failed"})]
    params = db.queries[0][1]
    assert params["status"] == "in.(received,spec_ready,vision_done)" and params["updated_at"] == f"lt.{ago(120)}"


def test_resolutions_respawn_closure_until_max_attempts():
    from civicpulse.pipeline.sweeper import sweep_resolutions

    class ResDb:
        def select(self, table, params):
            if table == "resolutions":
                assert params["ai_status"] == "eq.pending" and params["created_at"] == f"lt.{ago(120)}"
                return [{"id": "lost"}, {"id": "busy"}, {"id": "broken"}]
            key = params["resolution_id"].removeprefix("eq.")
            return {"lost": [],
                    "busy": [{"status": "ok", "started_at": ago(10)}],
                    "broken": [{"status": "error", "started_at": ago(400 + i)} for i in range(5)]}[key]

    spawned = []
    out = sweep_resolutions(ResDb(), lambda kind, key: spawned.append((kind, key)), NOW)
    assert out == {"spawned": ["lost"], "running": ["busy"], "gave_up": ["broken"]}
    assert spawned == [("closure", "lost")]
