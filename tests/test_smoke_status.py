from scripts.smoke_sponsors import exit_code


def test_smoke_exit_status_rejects_failed_or_incomplete_verification():
    assert exit_code([("PASS", "test", "ok")]) == 0
    assert exit_code([("FAIL", "test", "outage")]) == 1
    assert exit_code([("PASS", "a", "ok"), ("SKIP", "b", "no key")]) == 2
    assert exit_code([]) == 2
