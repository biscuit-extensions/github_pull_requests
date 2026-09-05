from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.github_pull_requests import setup
from src.github_pull_requests.extension import GitHubPullRequests


class TestGitHubPullRequestsExtension:
    def test_extension_setup(self, app_instance) -> None:
        setup(app_instance.api)
        ext = GitHubPullRequests(app_instance.api)
        parsed = ext.parse_pr_url("https://github.com/biscuit-editor/biscuit/pull/123")
        assert parsed == {"owner": "biscuit-editor", "repo": "biscuit", "pr_number": 123}
        assert ext.build_checkout_command(123) == "gh pr checkout 123"
        summary = ext.format_pr_summary(123, "Fix bug", "dev")
        assert summary == "#123: Fix bug by @dev [OPEN]"
