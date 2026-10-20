"""CLI behaviour."""

import json

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def test_diff_json_from_stdin():
    res = runner.invoke(app, ["diff", "-", "--json"], input="+    print(password)\n")
    assert res.exit_code == 0, res.output
    body = json.loads(res.output)
    assert any(f["title"] == "Secret logged" for f in body["findings"])


def test_diff_file_table_and_fail_on_changes(tmp_path):
    patch = tmp_path / "x.patch"
    patch.write_text("--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+run = eval(cmd)\n")
    res = runner.invoke(app, ["diff", str(patch), "--fail-on-changes"])
    assert res.exit_code == 2
    assert "Dynamic code execution" in res.output


def test_invalid_focus_is_a_usage_error():
    res = runner.invoke(app, ["diff", "-", "--focus", "nope"], input="+x\n")
    assert res.exit_code == 2
    assert "focus" in res.output.lower()


def test_missing_file_is_a_usage_error(tmp_path):
    res = runner.invoke(app, ["diff", str(tmp_path / "missing.patch")])
    assert res.exit_code == 2


def test_pr_requires_token():
    res = runner.invoke(app, ["pr", "acme", "widgets", "1"])
    assert res.exit_code == 1
    assert "GITHUB_TOKEN" in res.output


def test_history_lists_saved_reviews():
    runner.invoke(app, ["diff", "-"], input="+x = 1\n")
    res = runner.invoke(app, ["history"])
    assert res.exit_code == 0
    assert "Recent reviews" in res.output
