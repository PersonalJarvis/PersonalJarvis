from jarvis.clis.usage_log import UsageLog


def test_list_for_trace_filters_and_orders(tmp_path):
    log = UsageLog(db_path=tmp_path / "u.db")
    rid = log.record_start(cli_name="gcloud", full_command="gcloud projects list",
                           caller="router_tool", trace_id="trace-A", started_at_ms=1000)
    log.record_finish(rid, exit_code=0, stdout="ok", stderr="", finished_at_ms=1200)
    log.record_start(cli_name="gh", full_command="gh pr list",
                     caller="router_tool", trace_id="trace-B", started_at_ms=1100)

    rows = log.list_for_trace("trace-A")
    assert len(rows) == 1
    assert rows[0].cli_name == "gcloud" and rows[0].exit_code == 0
    assert log.list_for_trace("") == []
    log.close()
