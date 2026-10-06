"""Reusable test helpers."""


def get_latest_run_id(config):
    """
    Return run_id of the most recent run for the given config.

    Use when the test has just created a single run in a temp dir (so the
    latest run is the one we care about). If the code under test starts
    writing multiple runs, prefer selecting by run_name or another stable
    attribute instead.
    """
    from maida.storage import list_runs

    runs = list_runs(limit=1, config=config)
    assert runs, "expected at least one run"
    return runs[0].get("run_id") or runs[0].get("trace_id")
