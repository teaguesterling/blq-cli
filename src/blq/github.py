"""
Minimal GitHub API client for PR comments.

This module provides a simple client for interacting with GitHub's API
to create and update PR comments. It's designed for CI workflows.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from json import dumps as json_dumps
from json import loads as json_loads
from typing import Any

# GitHub API base URL
API_BASE = "https://api.github.com"


class GitHubError(Exception):
    """Error from GitHub API."""

    def __init__(self, message: str, status_code: int | None = None, response: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class GitHubClient:
    """Minimal GitHub API client for PR comments.

    Uses urllib to avoid external dependencies (requests).
    Only implements the endpoints needed for blq ci comment.

    Example:
        client = GitHubClient(os.environ["GITHUB_TOKEN"])
        client.create_comment("owner/repo", 123, "Build passed!")
    """

    def __init__(self, token: str):
        """Initialize client with GitHub token.

        Args:
            token: GitHub personal access token or GITHUB_TOKEN
        """
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict | None = None,
    ) -> dict | list | None:
        """Make a request to the GitHub API.

        Args:
            method: HTTP method (GET, POST, PATCH)
            endpoint: API endpoint (e.g., /repos/owner/repo/issues/1/comments)
            data: Request body for POST/PATCH

        Returns:
            Parsed JSON response or None for empty responses

        Raises:
            GitHubError: If request fails
        """
        url = f"{API_BASE}{endpoint}"
        body = json_dumps(data).encode("utf-8") if data else None

        req = urllib.request.Request(url, data=body, method=method)
        for key, value in self._headers.items():
            req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                content = response.read()
                if content:
                    result = json_loads(content.decode("utf-8"))
                    return result  # type: ignore[no-any-return]
                return None
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            try:
                error_json = json_loads(error_body)
                message = error_json.get("message", str(e))
            except Exception:
                message = error_body or str(e)
            raise GitHubError(
                message, status_code=e.code, response=error_json if error_body else None
            )
        except urllib.error.URLError as e:
            raise GitHubError(f"Connection error: {e.reason}")

    def create_comment(self, repo: str, pr_number: int, body: str) -> int:
        """Create a comment on a pull request.

        Args:
            repo: Repository in "owner/repo" format
            pr_number: Pull request number
            body: Comment body (markdown)

        Returns:
            Comment ID

        Raises:
            GitHubError: If request fails
        """
        endpoint = f"/repos/{repo}/issues/{pr_number}/comments"
        response = self._request("POST", endpoint, {"body": body})
        if not isinstance(response, dict):
            raise GitHubError("Unexpected response format")
        return int(response["id"])

    def update_comment(self, repo: str, comment_id: int, body: str) -> None:
        """Update an existing comment.

        Args:
            repo: Repository in "owner/repo" format
            comment_id: Comment ID to update
            body: New comment body (markdown)

        Raises:
            GitHubError: If request fails
        """
        endpoint = f"/repos/{repo}/issues/comments/{comment_id}"
        self._request("PATCH", endpoint, {"body": body})

    def find_comment(self, repo: str, pr_number: int, marker: str) -> int | None:
        """Find a comment containing a specific marker.

        Used to find existing blq comments for updating.

        Args:
            repo: Repository in "owner/repo" format
            pr_number: Pull request number
            marker: Marker string to search for (e.g., "<!-- blq-ci-comment -->")

        Returns:
            Comment ID if found, None otherwise

        Raises:
            GitHubError: If request fails
        """
        endpoint = f"/repos/{repo}/issues/{pr_number}/comments"
        response = self._request("GET", endpoint)
        if not isinstance(response, list):
            return None

        for comment in response:
            if isinstance(comment, dict):
                body = comment.get("body", "")
                if marker in body:
                    return int(comment["id"])

        return None

    def get_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Get pull request details.

        Args:
            repo: Repository in "owner/repo" format
            pr_number: Pull request number

        Returns:
            PR data dict

        Raises:
            GitHubError: If request fails
        """
        endpoint = f"/repos/{repo}/pulls/{pr_number}"
        response = self._request("GET", endpoint)
        if not isinstance(response, dict):
            raise GitHubError("Unexpected response format")
        return response
