"""上游同步保留二开配置，业务冲突时不提交，重复同步不产生提交。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("scenario", ["updated", "unchanged", "conflict"])
def test_upstream_merge_preserves_fork_and_stops_on_conflicts(tmp_path: Path, scenario: str) -> None:
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/sync-upstream.yml"
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["sync"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "merge")
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=checkout, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("config", "commit.gpgsign", "false")
    workflows = checkout / ".github/workflows"
    workflows.mkdir(parents=True)
    old_workflow = workflows / "test.yml"
    old_workflow.write_text("official CI\n", encoding="utf-8")
    content = checkout / "content.txt"
    content.write_text("base\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")

    git("checkout", "-b", "official")
    old_workflow.write_text("updated official CI\n", encoding="utf-8")
    (workflows / "new-official.yml").write_text("new official CI\n", encoding="utf-8")
    content.write_text("upstream\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "upstream update")
    upstream = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/upstream/main", base if scenario == "unchanged" else upstream)

    git("checkout", "main")
    old_workflow.unlink()
    (workflows / "docker-edge.yml").write_text("fork Docker\n", encoding="utf-8")
    if scenario == "conflict":
        content.write_text("fork\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "fork changes")
    before = git("rev-parse", "HEAD")
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=checkout,
        env={**os.environ, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )

    assert (workflows / "docker-edge.yml").read_text(encoding="utf-8") == "fork Docker\n"
    assert not old_workflow.exists(), result.stdout + result.stderr
    assert not (workflows / "new-official.yml").exists(), result.stdout + result.stderr
    if scenario == "updated":
        assert result.returncode == 0, result.stdout + result.stderr
        assert content.read_text(encoding="utf-8") == "upstream\n"
        assert git("rev-parse", "HEAD^1") == before
        assert git("rev-parse", "HEAD^2") == upstream
        assert output.read_text(encoding="utf-8") == f"changed=true\nsha={git('rev-parse', 'HEAD')}\n"
    else:
        assert git("rev-parse", "HEAD") == before
        if scenario == "unchanged":
            assert result.returncode == 0, result.stdout + result.stderr
            assert output.read_text(encoding="utf-8") == "changed=false\n"
        else:
            assert result.returncode != 0
            assert "conflicts require manual resolution" in result.stdout
