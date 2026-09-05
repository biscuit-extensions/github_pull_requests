from __future__ import annotations

import json
import re
import subprocess
import typing
from dataclasses import dataclass, field
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class PRInfo:
    """Represents a GitHub pull request model."""

    number: int
    title: str
    state: str = "OPEN"
    author: str = ""
    head_branch: str = ""
    base_branch: str = ""
    mergeable: str = "UNKNOWN"
    ci_status: str = "NONE"
    review_decision: str = "NONE"
    url: str = ""
    body: str = ""
    reviews: list[dict] = field(default_factory=list)


def evaluate_ci_status(status_rollup: list[dict] | dict | None) -> str:
    """Evaluate overall CI status rollup from individual status checks."""
    if not status_rollup:
        return "NONE"

    checks: list[dict] = []
    if isinstance(status_rollup, list):
        checks = status_rollup
    elif isinstance(status_rollup, dict):
        checks = status_rollup.get("contexts", []) or status_rollup.get("checks", [])

    if not checks:
        return "NONE"

    has_pending = False
    has_failure = False

    for check in checks:
        state = str(
            check.get("state")
            or check.get("status")
            or check.get("conclusion")
            or ""
        ).upper()

        if state in ("FAILURE", "FAILED", "ERROR", "TIMED_OUT", "ACTION_REQUIRED"):
            has_failure = True
            break
        elif state in ("PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", "WAITING"):
            has_pending = True

    if has_failure:
        return "FAILURE"
    if has_pending:
        return "PENDING"
    return "SUCCESS"


def evaluate_review_decision(reviews: list[dict] | None) -> str:
    """Determine overall review decision from reviewer list."""
    if not reviews:
        return "NONE"

    # Retain the latest state per reviewer
    latest_per_user: dict[str, str] = {}
    for r in reviews:
        user = r.get("author", {})
        login = user.get("login") if isinstance(user, dict) else str(user)
        state = str(r.get("state", "")).upper()
        if login and state:
            latest_per_user[login] = state

    states = list(latest_per_user.values())
    if "CHANGES_REQUESTED" in states:
        return "CHANGES_REQUESTED"
    if "APPROVED" in states:
        return "APPROVED"
    if "COMMENTED" in states:
        return "COMMENTED"
    return "REVIEW_REQUIRED"


class GitHubPullRequests(Extension):
    """Review and manage GitHub pull requests and issues within Biscuit."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.current_pr: PRInfo | None = None
        self.cached_prs: dict[int, PRInfo] = {}
        self.statusbar_button = None

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("GitHub Pull Requests extension loaded.")

    def _run_cli(self, cmd: list[str], cwd: str | None = None) -> tuple[int, str, str]:
        """Execute a CLI tool safely and return (returncode, stdout, stderr)."""
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                check=False,
            )
            return res.returncode, res.stdout, res.stderr
        except FileNotFoundError:
            return -1, "", f"Command '{cmd[0]}' not found on PATH"
        except Exception as exc:
            return -1, "", str(exc)

    def parse_remote_url(self, remote_url: str) -> tuple[str, str] | None:
        """Extract owner and repo name from GitHub HTTPS or SSH remote URL."""
        if not remote_url:
            return None

        # SSH pattern: git@github.com:owner/repo.git
        ssh_match = re.match(r"git@github\.com:([^/]+)/([^/\s]+?)(?:\.git)?$", remote_url.strip())
        if ssh_match:
            return ssh_match.group(1), ssh_match.group(2)

        # HTTPS pattern: https://github.com/owner/repo.git
        https_match = re.match(r"https?://github\.com/([^/]+)/([^/\s]+?)(?:\.git)?$", remote_url.strip())
        if https_match:
            return https_match.group(1), https_match.group(2)

        return None

    def get_workspace_repo_info(self, cwd: str | None = None) -> tuple[str, str] | None:
        """Query origin remote URL from active Git repository."""
        code, stdout, _ = self._run_cli(["git", "remote", "get-url", "origin"], cwd=cwd)
        if code == 0 and stdout.strip():
            return self.parse_remote_url(stdout.strip())
        return None

    def parse_pr_url(self, url: str) -> dict | None:
        """Parse GitHub PR URL or shorthand into owner, repo, and pr_number."""
        if not url:
            return None

        clean_url = url.strip()

        # 1. Full URL: https://github.com/owner/repo/pull/123
        match = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", clean_url)
        if match:
            return {
                "owner": match.group(1),
                "repo": match.group(2),
                "pr_number": int(match.group(3)),
            }

        # 2. Shorthand: owner/repo#123
        shorthand_match = re.match(r"([^/\s]+)/([^#\s]+)#(\d+)", clean_url)
        if shorthand_match:
            return {
                "owner": shorthand_match.group(1),
                "repo": shorthand_match.group(2),
                "pr_number": int(shorthand_match.group(3)),
            }

        # 3. Issue/PR number only: #123 or 123
        num_match = re.match(r"^#?(\d+)$", clean_url)
        if num_match:
            repo_info = self.get_workspace_repo_info()
            owner = repo_info[0] if repo_info else "unknown"
            repo = repo_info[1] if repo_info else "unknown"
            return {
                "owner": owner,
                "repo": repo,
                "pr_number": int(num_match.group(1)),
            }

        return None

    def parse_pr_json(self, data: dict) -> PRInfo:
        """Parse raw JSON output from gh pr view into a PRInfo instance."""
        number = data.get("number", 0)
        title = data.get("title", "")
        state = str(data.get("state", "OPEN")).upper()

        author_val = data.get("author", "")
        author = author_val.get("login", "") if isinstance(author_val, dict) else str(author_val)

        head_ref = data.get("headRefName", "")
        base_ref = data.get("baseRefName", "")
        mergeable = str(data.get("mergeable", "UNKNOWN")).upper()
        url = data.get("url", "")
        body = data.get("body", "")

        status_rollup = data.get("statusCheckRollup", [])
        ci_status = evaluate_ci_status(status_rollup)

        reviews = data.get("reviews", [])
        review_decision = evaluate_review_decision(reviews)

        return PRInfo(
            number=number,
            title=title,
            state=state,
            author=author,
            head_branch=head_ref,
            base_branch=base_ref,
            mergeable=mergeable,
            ci_status=ci_status,
            review_decision=review_decision,
            url=url,
            body=body,
            reviews=reviews if isinstance(reviews, list) else [],
        )

    def fetch_pr_details(self, pr_id: int | str, cwd: str | None = None) -> PRInfo | None:
        """Fetch PR details using gh CLI runner with JSON output format."""
        fields = [
            "number",
            "title",
            "state",
            "author",
            "headRefName",
            "baseRefName",
            "mergeable",
            "reviews",
            "statusCheckRollup",
            "body",
            "url",
        ]
        cmd = ["gh", "pr", "view", str(pr_id), f"--json={','.join(fields)}"]
        code, stdout, stderr = self._run_cli(cmd, cwd=cwd)

        if code == 0 and stdout.strip():
            try:
                data = json.loads(stdout)
                info = self.parse_pr_json(data)
                self.current_pr = info
                self.cached_prs[info.number] = info
                return info
            except Exception as exc:
                if hasattr(self.api, "logger") and self.api.logger:
                    self.api.logger.warning(f"Error parsing gh pr view JSON: {exc}")

        # Fallback to requests if gh CLI is missing or failed
        parsed = self.parse_pr_url(str(pr_id))
        if parsed and parsed.get("owner") != "unknown":
            info = self._fetch_pr_via_rest(parsed["owner"], parsed["repo"], parsed["pr_number"])
            if info:
                self.current_pr = info
                self.cached_prs[info.number] = info
                return info

        return None

    def _fetch_pr_via_rest(self, owner: str, repo: str, pr_number: int) -> PRInfo | None:
        """Query GitHub REST API as a fallback when gh CLI is unavailable."""
        try:
            import requests

            url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                head = data.get("head", {})
                base = data.get("base", {})
                author = data.get("user", {}).get("login", "")
                return PRInfo(
                    number=data.get("number", pr_number),
                    title=data.get("title", ""),
                    state=str(data.get("state", "open")).upper(),
                    author=author,
                    head_branch=head.get("ref", ""),
                    base_branch=base.get("ref", ""),
                    url=data.get("html_url", ""),
                    body=data.get("body", ""),
                )
        except Exception:
            pass
        return None

    def build_checkout_command(self, pr_number: int, use_gh: bool = True) -> str:
        """Construct the CLI command string to check out a PR locally."""
        if use_gh:
            return f"gh pr checkout {pr_number}"
        return f"git fetch origin pull/{pr_number}/head:pr-{pr_number} && git checkout pr-{pr_number}"

    def checkout_pr(self, pr_number: int, cwd: str | None = None) -> tuple[bool, str]:
        """Check out a pull request branch using gh CLI or git fetch fallback."""
        code, stdout, stderr = self._run_cli(["gh", "pr", "checkout", str(pr_number)], cwd=cwd)
        if code == 0:
            msg = f"Successfully checked out PR #{pr_number}"
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(msg)
            return True, stdout or msg

        # Fallback to git fetch
        fetch_cmd = ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"]
        f_code, f_stdout, f_stderr = self._run_cli(fetch_cmd, cwd=cwd)
        if f_code == 0:
            c_code, c_stdout, c_stderr = self._run_cli(
                ["git", "checkout", f"pr-{pr_number}"], cwd=cwd
            )
            if c_code == 0:
                msg = f"Checked out PR #{pr_number} via git fallback"
                if hasattr(self.api, "notifications") and self.api.notifications:
                    self.api.notifications.info(msg)
                return True, msg

        error_msg = f"Failed to checkout PR #{pr_number}: {stderr.strip()}"
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.error(error_msg)
        return False, error_msg

    def fetch_pr_diff(self, pr_number: int, cwd: str | None = None) -> str:
        """Fetch the unified diff of a pull request using gh pr diff."""
        code, stdout, stderr = self._run_cli(["gh", "pr", "diff", str(pr_number)], cwd=cwd)
        if code == 0:
            return stdout
        return ""

    def parse_diff_summary(self, diff_text: str) -> dict[str, typing.Any]:
        """Extract modified file paths, added line count, and deleted line count from diff."""
        files: list[str] = []
        additions = 0
        deletions = 0

        for line in diff_text.splitlines():
            if line.startswith("+++ b/"):
                files.append(line[6:])
            elif line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        return {
            "files": files,
            "additions": additions,
            "deletions": deletions,
            "total_files": len(files),
        }

    def format_pr_summary(
        self, pr_number: int, title: str, author: str, status: str = "OPEN"
    ) -> str:
        """Format a single-line summary of a pull request."""
        return f"#{pr_number}: {title} by @{author} [{status}]"

    def refresh_pr_status(self, pr_number: int | None = None) -> PRInfo | None:
        """Refresh status of currently active or specified PR."""
        target_num = pr_number or (self.current_pr.number if self.current_pr else None)
        if not target_num:
            return None

        info = self.fetch_pr_details(target_num)
        if info and self.statusbar_button and hasattr(self.statusbar_button, "set_text"):
            self.statusbar_button.set_text(f"PR #{info.number} ({info.ci_status})")
        return info

    def install(self) -> None:
        """Register GitHub Pull Requests commands and contributions."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command(
                "github_pull_requests.view_pr", self.fetch_pr_details
            )
            self.api.commands.register_command(
                "github_pull_requests.checkout_pr", self.checkout_pr
            )
            self.api.commands.register_command(
                "github_pull_requests.refresh_status", self.refresh_pr_status
            )
            self.api.commands.register_command(
                "github_pull_requests.view_diff", self.fetch_pr_diff
            )

        if hasattr(self.api, "statusbar") and hasattr(self.api.statusbar, "add_button"):
            try:
                self.statusbar_button = self.api.statusbar.add_button(
                    text="PR: None",
                    icon="git-pull-request",
                    callback=self.refresh_pr_status,
                    description="GitHub Pull Requests status",
                )
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("GitHub Pull Requests extension installed.")
