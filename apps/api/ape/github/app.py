"""GitHub App identity and the handful of REST calls the product needs.

Permissions the App asks for: Contents (read and write), Pull requests (read and
write), Metadata (read). It can only touch repositories the customer installed it on.
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from ape.core.config import Settings

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"
_STATE_TTL_SECONDS = 15 * 60


class GitHubError(Exception):
    """A GitHub call failed. The message is safe to show to the user."""


class GitHubNotConfigured(GitHubError):
    pass


@dataclass(frozen=True, slots=True)
class Repo:
    full_name: str
    default_branch: str
    private: bool


class GitHubApp:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._tokens: dict[int, tuple[str, float]] = {}

    @property
    def configured(self) -> bool:
        return self._settings.github_configured

    def _require(self) -> None:
        if not self.configured:
            raise GitHubNotConfigured("the GitHub App is not configured on this installation")

    # --- install flow --------------------------------------------------------------------

    def _state_key(self) -> bytes:
        return hashlib.sha256(
            b"ape-github-state:" + self._settings.github_app_client_secret.encode()
        ).digest()

    def new_state(self, *, org_id: str, user_id: str) -> str:
        """Signed, short-lived value that ties the GitHub round trip to one user and tenant."""
        self._require()
        now = int(time.time())
        return jwt.encode(
            {"org": org_id, "sub": user_id, "iat": now, "exp": now + _STATE_TTL_SECONDS},
            self._state_key(),
            algorithm="HS256",
        )

    def check_state(self, state: str, *, org_id: str, user_id: str) -> bool:
        try:
            claims = jwt.decode(
                state, self._state_key(), algorithms=["HS256"], options={"require": ["exp"]}
            )
        except jwt.PyJWTError:
            return False
        return hmac.compare_digest(str(claims.get("org")), org_id) and hmac.compare_digest(
            str(claims.get("sub")), user_id
        )

    def install_url(self, state: str) -> str:
        self._require()
        base = self._settings.github_web_url.rstrip("/")
        return f"{base}/apps/{self._settings.github_app_slug}/installations/new?state={state}"

    async def user_can_access_installation(self, code: str, installation_id: int) -> bool:
        """Proves that the person who came back from GitHub really controls the installation.

        The installation id in the callback URL is attacker-controllable: without this
        check anyone could attach somebody else's installation to their own workspace
        and get pull requests opened in (and manifests read from) a stranger's repository.
        """
        self._require()
        response = await self._http.post(
            f"{self._settings.github_web_url.rstrip('/')}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self._settings.github_app_client_id,
                "client_secret": self._settings.github_app_client_secret,
                "code": code,
            },
        )
        token = response.json().get("access_token") if response.status_code == 200 else None
        if not isinstance(token, str) or not token:
            return False
        page = 1
        while page <= 10:
            listing = await self._http.get(
                f"{self._settings.github_api_url}/user/installations",
                params={"per_page": 100, "page": page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": _ACCEPT,
                    "X-GitHub-Api-Version": _API_VERSION,
                },
            )
            if listing.status_code != 200:
                return False
            installations = listing.json().get("installations") or []
            if any(i.get("id") == installation_id for i in installations):
                return True
            if len(installations) < 100:
                return False
            page += 1
        return False

    # --- installation access ---------------------------------------------------------------

    def _app_jwt(self) -> str:
        now = int(time.time())
        key = self._settings.github_app_private_key.replace("\\n", "\n")
        return jwt.encode(
            {"iat": now - 30, "exp": now + 9 * 60, "iss": self._settings.github_app_id},
            key,
            algorithm="RS256",
        )

    async def _installation_token(self, installation_id: int) -> str:
        self._require()
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
        response = await self._http.post(
            f"{self._settings.github_api_url}/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": _ACCEPT,
                "X-GitHub-Api-Version": _API_VERSION,
            },
        )
        if response.status_code != 201:
            raise GitHubError(
                "GitHub did not grant access to this installation; reconnect the GitHub App"
            )
        token = str(response.json()["token"])
        self._tokens[installation_id] = (token, time.time() + 50 * 60)
        return token

    async def _request(
        self, installation_id: int, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        token = await self._installation_token(installation_id)
        return await self._http.request(
            method,
            f"{self._settings.github_api_url}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": _ACCEPT,
                "X-GitHub-Api-Version": _API_VERSION,
            },
            **kwargs,
        )

    async def _json(
        self, installation_id: int, method: str, path: str, *, expect: int, what: str, **kwargs: Any
    ) -> Any:
        response = await self._request(installation_id, method, path, **kwargs)
        if response.status_code != expect:
            # GitHub's body can repeat request content; only the status is reported.
            raise GitHubError(f"GitHub refused to {what} (HTTP {response.status_code})")
        return response.json()

    async def list_repos(self, installation_id: int) -> list[Repo]:
        repos: list[Repo] = []
        for page in range(1, 11):
            data = await self._json(
                installation_id,
                "GET",
                "/installation/repositories",
                expect=200,
                what="list repositories",
                params={"per_page": 100, "page": page},
            )
            batch = data.get("repositories") or []
            repos.extend(
                Repo(r["full_name"], r["default_branch"], bool(r.get("private"))) for r in batch
            )
            if len(batch) < 100:
                break
        return sorted(repos, key=lambda r: r.full_name.lower())

    async def read_manifests(
        self,
        installation_id: int,
        repo: str,
        directory: str,
        *,
        max_files: int = 300,
        max_bytes: int = 1024 * 1024,
    ) -> tuple[str, str, dict[str, str]]:
        """Returns (default branch, its head commit sha, {path: text}) for YAML files under ``directory``."""
        info = await self._json(
            installation_id, "GET", f"/repos/{repo}", expect=200, what="read the repository"
        )
        branch = str(info["default_branch"])
        ref = await self._json(
            installation_id,
            "GET",
            f"/repos/{repo}/git/ref/heads/{branch}",
            expect=200,
            what="read the default branch",
        )
        head = str(ref["object"]["sha"])
        tree = await self._json(
            installation_id,
            "GET",
            f"/repos/{repo}/git/trees/{head}",
            expect=200,
            what="list files",
            params={"recursive": "1"},
        )
        prefix = directory.strip("/")
        prefix = f"{prefix}/" if prefix else ""
        files: dict[str, str] = {}
        for entry in tree.get("tree") or []:
            path = str(entry.get("path", ""))
            if entry.get("type") != "blob" or not path.startswith(prefix):
                continue
            if not path.endswith((".yaml", ".yml")) or int(entry.get("size") or 0) > max_bytes:
                continue
            if len(files) >= max_files:
                break
            blob = await self._json(
                installation_id,
                "GET",
                f"/repos/{repo}/git/blobs/{entry['sha']}",
                expect=200,
                what="read a file",
            )
            try:
                files[path] = base64.b64decode(blob["content"]).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
        return branch, head, files

    async def open_pull_request(
        self,
        installation_id: int,
        repo: str,
        *,
        base_branch: str,
        base_sha: str,
        branch: str,
        files: dict[str, str],
        commit_message: str,
        title: str,
        body: str,
    ) -> str:
        """One commit with all changed files on a new branch, then the pull request. Returns its URL."""
        base_commit = await self._json(
            installation_id,
            "GET",
            f"/repos/{repo}/git/commits/{base_sha}",
            expect=200,
            what="read the base commit",
        )
        tree = await self._json(
            installation_id,
            "POST",
            f"/repos/{repo}/git/trees",
            expect=201,
            what="create the file tree",
            json={
                "base_tree": base_commit["tree"]["sha"],
                "tree": [
                    {"path": p, "mode": "100644", "type": "blob", "content": c}
                    for p, c in sorted(files.items())
                ],
            },
        )
        commit = await self._json(
            installation_id,
            "POST",
            f"/repos/{repo}/git/commits",
            expect=201,
            what="create the commit",
            json={"message": commit_message, "tree": tree["sha"], "parents": [base_sha]},
        )
        await self._json(
            installation_id,
            "POST",
            f"/repos/{repo}/git/refs",
            expect=201,
            what="create the branch",
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
        pull = await self._json(
            installation_id,
            "POST",
            f"/repos/{repo}/pulls",
            expect=201,
            what="open the pull request",
            json={
                "title": title,
                "head": branch,
                "base": base_branch,
                "body": body,
                "maintainer_can_modify": True,
            },
        )
        return str(pull["html_url"])
