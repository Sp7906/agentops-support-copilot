from evals.run_eval import run


def test_scenario_quality_gate_passes():
    res = run()
    assert not res["failures"], (res["failures"], [r for r in res["results"] if not r["route_ok"]])
