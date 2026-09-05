from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class GitHubPullRequests(Extension):
    """Review and manage GitHub pull requests and issues within Biscuit"""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("GitHub Pull Requests extension initialised.")

    def parse_pr_url(self, url: str) -> dict | None:
        """Parse GitHub PR URL into owner, repo, and pr_number."""
        import re
        match = re.match(r'https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)', url)
        if match:
            return {
                "owner": match.group(1),
                "repo": match.group(2),
                "pr_number": int(match.group(3))
            }
        return None

    def build_checkout_command(self, pr_number: int) -> str:
        return f'gh pr checkout {pr_number}'

    def format_pr_summary(self, pr_number: int, title: str, author: str, status: str = "OPEN") -> str:
        return f"#{pr_number}: {title} by @{author} [{status}]"

    def install(self) -> None:
        self.api.logger.info("GitHub Pull Requests extension installed.")
