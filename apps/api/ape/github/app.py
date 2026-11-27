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
# ".tpl" covers manifests with ${VAR} placeholders that a deploy script fills in
# (envsubst). They are valid YAML; templates that are not are skipped by the patcher.
MANIFEST_SUFFIXES = (".yaml", ".yml", ".yaml.tpl", ".yml.tpl")


class GitHubError(Exception):
    """A GitHub call failed. The message is safe to show to the user."""


class GitHubNotConfigured(GitHubError):
    pass


@dataclass(frozen=True, slots=True)
class UserInstallation:
    """An installation of the App as one GitHub user sees it."""

    id: int
    account: str  # login of the account or organisation the App is installed on
    writable_repos: list[str]


# A person rarely works with more accounts than this; it bounds the calls of one sign-in.
_MAX_INSTALLATIONS = 20


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

    def new_state(self, *, org_id: str, user_id: str, installation_id: int | None = None) -> str:
        """Signed, short-lived value that ties the GitHub round trip to one user and tenant.

        ``installation_id`` records which installation the user picked when their GitHub
        account can reach more than one.
        """
        self._require()
        now = int(time.time())
        claims: dict[str, Any] = {
            "org": org_id,
            "sub": user_id,
            "iat": now,
            "exp": now + _STATE_TTL_SECONDS,
        }
        if installation_id is not None:
            claims["inst"] = installation_id
        return jwt.encode(claims, self._state_key(), algorithm="HS256")

    def state_installation(self, state: str) -> int | None:
        """The installation picked in a state that ``check_state`` has already accepted."""
        try:
            claims = jwt.decode(state, self._state_key(), algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        value = claims.get("inst")
        return value if isinstance(value, int) and value > 0 else None

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

    def owner_install_url(self) -> str:
        """A link to hand to whoever may install apps on the customer's account, usually an
        organisation owner without an account here. It carries no state: installing gives
        this workspace nothing until somebody connects with their own GitHub sign-in."""
        self._require()
        base = self._settings.github_web_url.rstrip("/")
        return f"{base}/apps/{self._settings.github_app_slug}/installations/new"

    def authorize_url(self, state: str) -> str:
        """GitHub sign-in without installing anything, for connecting an installation that
        somebody else made."""
        self._require()
        base = self._settings.github_web_url.rstrip("/")
        client_id = self._settings.github_app_client_id
        return f"{base}/login/oauth/authorize?client_id={client_id}&state={state}"

    async def user_writable_repos(self, code: str, installation_id: int) -> list[str] | None:
        """The repositories of the installation that the person who came back from GitHub
        can push to, or None when that person cannot see the installation at all.

        The installation id in the callback URL is attacker-controllable: without this
        check anyone could attach somebody else's installation to their own workspace
        and get pull requests opened in (and manifests read from) a stranger's repository.

        Seeing an installation is not enough either. GitHub lists it for every collaborator
        of a single repository it covers, while the App's installation token can write to
        all of them. So the answer is the list of repositories this person could have
        pushed to themselves; the workspace is limited to it.
        """
        headers = await self._user_headers(code)
        if headers is None:
            return None
        if not any(i == installation_id for i, _ in await self._user_installations(headers)):
            return None
        return await self._writable_repos(headers, installation_id)

    async def user_installations(self, code: str) -> list[UserInstallation] | None:
        """Every installation of the App in which the person who came back from GitHub can
        push to at least one repository, or None when the sign-in is not valid.

        This is how an installation made by somebody else (the customer's organisation
        owner) is connected: the same rule as above decides, the person's own push access.
        The user token is used for these calls only and is never stored.
        """
        headers = await self._user_headers(code)
        if headers is None:
            return None
        found: list[UserInstallation] = []
        for installation_id, account in (await self._user_installations(headers))[
            :_MAX_INSTALLATIONS
        ]:
            writable = await self._writable_repos(headers, installation_id)
            if writable:
                found.append(UserInstallation(installation_id, account, writable))
        return found

    async def _user_headers(self, code: str) -> dict[str, str] | None:
        """Exchanges the one-time code for a user token. The token lives only in the
        returned headers, for the duration of one request."""
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
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
        }

    async def _writable_repos(
        self, headers: dict[str, str], installation_id: int
    ) -> list[str] | None:
        writable: list[str] = []
        for page in range(1, 11):
            listing = await self._http.get(
                f"{self._settings.github_api_url}/user/installations/{installation_id}/repositories",
                params={"per_page": 100, "page": page},
                headers=headers,
            )
            if listing.status_code != 200:
                return None
            batch = listing.json().get("repositories") or []
            for repo in batch:
                permissions = repo.get("permissions") or {}
                name = repo.get("full_name")
                if isinstance(name, str) and (permissions.get("push") or permissions.get("admin")):
                    writable.append(name)
            if len(batch) < 100:
                break
        return sorted(writable)

    async def _user_installations(self, headers: dict[str, str]) -> list[tuple[int, str]]:
        """(id, account login) of the installations the signed-in GitHub user can reach."""
        found: list[tuple[int, str]] = []
        for page in range(1, 11):
            listing = await self._http.get(
                f"{self._settings.github_api_url}/user/installations",
                params={"per_page": 100, "page": page},
                headers=headers,
            )
            if listing.status_code != 200:
                break
            installations = listing.json().get("installations") or []
            for installation in installations:
                account = installation.get("account")
                login = account.get("login") if isinstance(account, dict) else None
                if isinstance(installation.get("id"), int):
                    found.append((installation["id"], login if isinstance(login, str) else ""))
            if len(installations) < 100:
                break
        return found

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
            if not path.endswith(MANIFEST_SUFFIXES) or int(entry.get("size") or 0) > max_bytes:
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
