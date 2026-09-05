from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import tomllib

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.github_pull_requests import setup
from src.github_pull_requests.extension import (
    GitHubPullRequests,
    PRInfo,
    evaluate_ci_status,
    evaluate_review_decision,
)

SAMPLE_PR_JSON = {
    "number": 42,
    "title": "Add multi-language linting support",
    "state": "OPEN",
    "author": {"login": "alice-dev"},
    "headRefName": "feat/linting",
    "baseRefName": "main",
    "mergeable": "MERGEABLE",
    "url": "https://github.com/biscuit-editor/biscuit/pull/42",
    "body": "This pull request implements comprehensive linting support across languages.",
    "statusCheckRollup": [
        {"name": "test-suite", "status": "COMPLETED", "conclusion": "SUCCESS"},
        {"name": "lint-check", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ],
    "reviews": [
        {"author": {"login": "bob-reviewer"}, "state": "APPROVED"},
    ],
}

SAMPLE_DIFF = """\
diff --git a/src/linter.py b/src/linter.py
index abc..def 100644
--- a/src/linter.py
+++ b/src/linter.py
@@ -1,3 +1,5 @@
+import os
+import sys
 def lint():
-    pass
+    return True
diff --git a/tests/test_linter.py b/tests/test_linter.py
index 123..456 100644
--- a/tests/test_linter.py
+++ b/tests/test_linter.py
@@ -0,0 +1,4 @@
+def test_lint():
+    assert lint() is True
"""


class TestGitHubPullRequestsExtension:
    def test_extension_setup_and_registration(self, app_instance) -> None:
        setup(app_instance.api)
        assert app_instance.api is not None

    def test_manifest_schema_and_dependencies(self) -> None:
        toml_path = Path(__file__).parent.parent / "extension.toml"
        assert toml_path.exists(), "extension.toml must exist"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert data["extension"]["id"] == "github_pull_requests"
        assert data["extension"]["version"] == "0.2.0"
        assert data["extension"]["author"] == "tomlin7"
        assert "dependencies" in data
        assert any("requests" in p for p in data["dependencies"]["python"])
        assert "gh" in data["dependencies"]["tools"]
        assert "git" in data["dependencies"]["tools"]
        assert "contributes" in data
        assert len(data["contributes"]["commands"]) >= 4

    def test_parse_remote_url(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        # SSH remote
        ssh_res = ext.parse_remote_url("git@github.com:biscuit-editor/biscuit.git")
        assert ssh_res == ("biscuit-editor", "biscuit")

        # HTTPS remote
        https_res = ext.parse_remote_url("https://github.com/biscuit-editor/biscuit")
        assert https_res == ("biscuit-editor", "biscuit")

        # Invalid remote
        assert ext.parse_remote_url("git@gitlab.com:org/repo.git") is None
        assert ext.parse_remote_url("") is None

    def test_parse_pr_url_and_shorthand(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)

        # Standard HTTPS URL
        p1 = ext.parse_pr_url("https://github.com/biscuit-editor/biscuit/pull/123")
        assert p1 == {"owner": "biscuit-editor", "repo": "biscuit", "pr_number": 123}

        # Shorthand owner/repo#num
        p2 = ext.parse_pr_url("biscuit-editor/biscuit#456")
        assert p2 == {"owner": "biscuit-editor", "repo": "biscuit", "pr_number": 456}

        # Issue number only
        with patch.object(ext, "get_workspace_repo_info", return_value=("owner", "repo")):
            p3 = ext.parse_pr_url("#789")
            assert p3 == {"owner": "owner", "repo": "repo", "pr_number": 789}

        # Invalid URL
        assert ext.parse_pr_url("invalid-url-string") is None

    def test_build_checkout_command(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        assert ext.build_checkout_command(123) == "gh pr checkout 123"
        git_cmd = ext.build_checkout_command(123, use_gh=False)
        assert "git fetch origin pull/123/head:pr-123" in git_cmd
        assert "git checkout pr-123" in git_cmd

    def test_evaluate_ci_status(self) -> None:
        assert evaluate_ci_status(None) == "NONE"
        assert evaluate_ci_status([]) == "NONE"

        # Success checks
        success_checks = [
            {"conclusion": "SUCCESS"},
            {"status": "COMPLETED", "conclusion": "NEUTRAL"},
        ]
        assert evaluate_ci_status(success_checks) == "SUCCESS"

        # Pending check
        pending_checks = [
            {"conclusion": "SUCCESS"},
            {"status": "IN_PROGRESS"},
        ]
        assert evaluate_ci_status(pending_checks) == "PENDING"

        # Failure check
        failure_checks = [
            {"conclusion": "SUCCESS"},
            {"conclusion": "FAILURE"},
            {"status": "PENDING"},
        ]
        assert evaluate_ci_status(failure_checks) == "FAILURE"

    def test_evaluate_review_decision(self) -> None:
        assert evaluate_review_decision([]) == "NONE"
        assert evaluate_review_decision(None) == "NONE"

        reviews_approved = [
            {"author": {"login": "alice"}, "state": "APPROVED"},
        ]
        assert evaluate_review_decision(reviews_approved) == "APPROVED"

        reviews_changes = [
            {"author": {"login": "alice"}, "state": "APPROVED"},
            {"author": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
        ]
        assert evaluate_review_decision(reviews_changes) == "CHANGES_REQUESTED"

        # Reviewer changes vote from CHANGES_REQUESTED to APPROVED
        reviews_updated = [
            {"author": {"login": "bob"}, "state": "CHANGES_REQUESTED"},
            {"author": {"login": "bob"}, "state": "APPROVED"},
        ]
        assert evaluate_review_decision(reviews_updated) == "APPROVED"

    def test_parse_pr_json(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        info = ext.parse_pr_json(SAMPLE_PR_JSON)

        assert info.number == 42
        assert info.title == "Add multi-language linting support"
        assert info.author == "alice-dev"
        assert info.head_branch == "feat/linting"
        assert info.base_branch == "main"
        assert info.mergeable == "MERGEABLE"
        assert info.ci_status == "SUCCESS"
        assert info.review_decision == "APPROVED"

    def test_fetch_pr_details_via_cli(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        with patch.object(ext, "_run_cli", return_value=(0, json.dumps(SAMPLE_PR_JSON), "")):
            info = ext.fetch_pr_details(42)
            assert info is not None
            assert info.number == 42
            assert ext.current_pr == info
            assert 42 in ext.cached_prs

    def test_checkout_pr_success_and_fallback(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)

        # Success via gh CLI
        with patch.object(ext, "_run_cli", return_value=(0, "Switched to branch pr-42", "")):
            success, msg = ext.checkout_pr(42)
            assert success is True

        # Fallback to git
        def mock_run(cmd, cwd=None):
            if cmd[0] == "gh":
                return 1, "", "gh not logged in"
            return 0, "Switched to branch", ""

        with patch.object(ext, "_run_cli", side_effect=mock_run):
            success, msg = ext.checkout_pr(42)
            assert success is True
            assert "fallback" in msg

    def test_fetch_pr_diff_and_summary(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        with patch.object(ext, "_run_cli", return_value=(0, SAMPLE_DIFF, "")):
            diff_text = ext.fetch_pr_diff(42)
            assert "diff --git" in diff_text
            summary = ext.parse_diff_summary(diff_text)
            assert summary["total_files"] == 2
            assert "src/linter.py" in summary["files"]
            assert "tests/test_linter.py" in summary["files"]
            assert summary["additions"] > 0
            assert summary["deletions"] > 0

    def test_format_pr_summary(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        summary = ext.format_pr_summary(42, "Fix tests", "alice", "MERGED")
        assert summary == "#42: Fix tests by @alice [MERGED]"

    def test_refresh_pr_status_and_statusbar(self, app_instance) -> None:
        ext = GitHubPullRequests(app_instance.api)
        ext.install()
        mock_btn = MagicMock()
        ext.statusbar_button = mock_btn

        with patch.object(ext, "_run_cli", return_value=(0, json.dumps(SAMPLE_PR_JSON), "")):
            info = ext.refresh_pr_status(42)
            assert info is not None
            mock_btn.set_text.assert_called_with("PR #42 (SUCCESS)")
