import datetime as dt

from gta import analytics
from gta.store import Store


class FakeReports:
    def __init__(self, calls):
        self.calls = calls

    def query(self, **kw):
        self.calls.append(kw)
        class R:
            def execute(_self):
                return {"columnHeaders": [{"name": "video"}, {"name": "day"}, {"name": "views"},
                                          {"name": "likes"}, {"name": "comments"}, {"name": "subscribersGained"},
                                          {"name": "averageViewDuration"}, {"name": "averageViewPercentage"}],
                        "rows": [["vid1", "2026-11-18", 120, 5, 1, 2, 18.5, 61.0]]}
        return R()


class FakeAnalytics:
    def __init__(self):
        self.calls = []

    def reports(self):
        return FakeReports(self.calls)


def test_fetch_and_store_metrics(tmp_path):
    svc = FakeAnalytics()
    rows = analytics.fetch_video_metrics(svc, ["vid1"], days=7, end=dt.date(2026, 11, 18))
    assert rows[0]["video"] == "vid1" and rows[0]["views"] == 120
    assert svc.calls[0]["filters"] == "video==vid1"
    store = Store(str(tmp_path / "a.sqlite"))
    assert analytics.store_metrics(store, rows) == 1
    store.execute("INSERT INTO shorts (source_key, template, status, youtube_id, creator_id) VALUES ('k','countdown_v1','published','vid1','x')")
    perf = analytics.performance_by_template(store)
    assert perf[0]["template"] == "countdown_v1" and perf[0]["views"] == 120


def test_no_ids_no_calls():
    svc = FakeAnalytics()
    assert analytics.fetch_video_metrics(svc, []) == [] and svc.calls == []
