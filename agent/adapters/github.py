"""Thin GitHub REST adapter. Zero business logic.

Uses GITHUB_TOKEN + GITHUB_REPO from the environment (fine-grained PAT with
contents + pull-requests on the demo repo). During development, `gh auth
token` output works too.
"""

from __future__ import annotations

import os

import requests

API = "https://api.github.com"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def repo() -> str:
    return os.environ.get("GITHUB_REPO", "")


def open_pull_request(head: str, base: str, title: str, body: str) -> str:
    """Returns the PR html url."""
    resp = requests.post(
        f"{API}/repos/{repo()}/pulls",
        headers=_headers(),
        json={"title": title, "head": head, "base": base, "body": body},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["html_url"]


def list_open_pull_requests(head_prefix: str) -> list[dict]:
    resp = requests.get(
        f"{API}/repos/{repo()}/pulls", headers=_headers(),
        params={"state": "open", "per_page": 50}, timeout=30,
    )
    resp.raise_for_status()
    return [
        {"number": p["number"], "head": p["head"]["ref"], "url": p["html_url"]}
        for p in resp.json()
        if p["head"]["ref"].startswith(head_prefix)
    ]


def close_pull_request(number: int) -> None:
    requests.patch(
        f"{API}/repos/{repo()}/pulls/{number}",
        headers=_headers(), json={"state": "closed"}, timeout=30,
    ).raise_for_status()


def delete_branch(branch: str) -> None:
    resp = requests.delete(
        f"{API}/repos/{repo()}/git/refs/heads/{branch}", headers=_headers(), timeout=30
    )
    if resp.status_code not in (204, 404, 422):
        resp.raise_for_status()
