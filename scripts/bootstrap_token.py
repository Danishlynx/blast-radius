"""Ensure .env contains a working DataHub personal access token.

Idempotent: if DATAHUB_TOKEN is present and accepted by GMS, do nothing.
Otherwise log into the frontend with the default demo credentials
(datahub/datahub), mint a PAT via GraphQL, and write it into .env.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"

GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")
FRONTEND_URL = os.environ.get("DATAHUB_FRONTEND_URL", "http://localhost:9002")
USERNAME = os.environ.get("DATAHUB_USERNAME", "datahub")
PASSWORD = os.environ.get("DATAHUB_PASSWORD", "datahub")


def token_works(token: str) -> bool:
    resp = requests.post(
        f"{GMS_URL}/api/graphql",
        json={"query": "{ me { corpUser { username } } }"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    return resp.status_code == 200 and "errors" not in resp.json()


def mint_token() -> str:
    session = requests.Session()
    login = session.post(
        f"{FRONTEND_URL}/logIn",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=15,
    )
    login.raise_for_status()

    mutation = """
    mutation createAccessToken($input: CreateAccessTokenInput!) {
      createAccessToken(input: $input) { accessToken metadata { id name } }
    }
    """
    variables = {
        "input": {
            "type": "PERSONAL",
            "actorUrn": f"urn:li:corpuser:{USERNAME}",
            "duration": "NO_EXPIRY",
            "name": "blast-radius",
            "description": "Minted by scripts/bootstrap_token.py",
        }
    }
    # Right after first boot, GMS's policy bootstrap + policy cache refresh
    # (120s interval) can lag the health check, so the datahub user briefly
    # lacks the token privilege. Retry through that window.
    deadline = time.monotonic() + 300
    while True:
        resp = session.post(
            f"{FRONTEND_URL}/api/v2/graphql",
            json={"query": mutation, "variables": variables},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("errors"):
            return body["data"]["createAccessToken"]["accessToken"]
        unauthorized = any("Unauthorized" in e.get("message", "") for e in body["errors"])
        if not unauthorized or time.monotonic() > deadline:
            raise RuntimeError(f"createAccessToken failed: {body['errors']}")
        print("token privilege not granted yet (policy bootstrap still settling) — retrying in 20s")
        time.sleep(20)


def write_env_token(token: str) -> None:
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    if re.search(r"^DATAHUB_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^DATAHUB_TOKEN=.*$", f"DATAHUB_TOKEN={token}", text, flags=re.MULTILINE)
    else:
        text += f"\nDATAHUB_TOKEN={token}\n"
    ENV_PATH.write_text(text)


def main() -> int:
    existing = dotenv_values(ENV_PATH).get("DATAHUB_TOKEN") or os.environ.get("DATAHUB_TOKEN")
    if existing:
        try:
            if token_works(existing):
                print("DATAHUB_TOKEN already valid — nothing to do")
                return 0
        except requests.RequestException as exc:
            print(f"could not validate existing token ({exc}); minting a new one")

    token = mint_token()
    if not token_works(token):
        print("minted a token but GMS rejected it — is METADATA_SERVICE_AUTH_ENABLED=true?", file=sys.stderr)
        return 1
    write_env_token(token)
    print("minted DataHub token and wrote it to .env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
