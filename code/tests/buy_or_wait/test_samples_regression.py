"""Guard against calibration regressions on the solved samples (uses committed LLM caches)."""

from buy_or_wait.cli import main


def test_samples_score_does_not_regress(capsys):
    main(["--samples", "--no-llm", "--quiet"])
    out = capsys.readouterr().out
    acc = {line.split()[0]: int(line.split()[-1].split("/")[0]) for line in out.splitlines()
           if "/25" in line and not line.startswith("rows")}
    assert acc["affordability_status"] >= 21
    assert acc["recommended_payment_method"] >= 22
    assert acc["payment_plan"] >= 21
    assert acc["earliest_date_for_full_payment"] >= 21
