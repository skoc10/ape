"""GitHub connection of a workspace and the "open pull request" action (task 4.4)."""

import re
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ape.core.config import Settings
from ape.core.db import Database
from ape.core.deps import CurrentPrincipal
from ape.core.tenancy import Principal
from ape.github.app import GitHubApp, GitHubError, GitHubNotConfigured
from ape.github.pull_request import plan_pull_request
from ape.repos import audit_runs, clusters, findings, org_settings

router = APIRouter(prefix="/v1", tags=["github"])
log = structlog.get_logger(__name__)

MANAGER_ROLES = {"owner", "admin"}
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_PATH = re.compile(r"^[A-Za-z0-9_./-]{0,200}$")


def _db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def _github(request: Request) -> GitHubApp:
    app: GitHubApp = request.app.state.github
    return app


Db = Annotated[Database, Depends(_db)]
GitHub = Annotated[GitHubApp, Depends(_github)]


def _require_manager(principal: Principal) -> None:
    if principal.role not in MANAGER_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only owners and admins can manage the GitHub connection"
        )


def _upstream(exc: GitHubError) -> HTTPException:
    if isinstance(exc, GitHubNotConfigured):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


class GitHubStatus(BaseModel):
    configured: bool  # the GitHub App credentials exist on this installation
    connected: bool  # this workspace has an installation
    repo: str | None
    manifest_path: str | None
    install_url: str | None
    can_manage: bool


def _status(github: GitHubApp, row: dict[str, Any], principal: Principal) -> GitHubStatus:
    can_manage = principal.role in MANAGER_ROLES
    install_url = None
    if github.configured and can_manage:
        install_url = github.install_url(
            github.new_state(org_id=principal.org_id, user_id=principal.user_id)
        )
    return GitHubStatus(
        configured=github.configured,
        connected=row["github_installation_id"] is not None,
        repo=row["github_repo"],
        manifest_path=row["manifest_path"],
        install_url=install_url,
        can_manage=can_manage,
    )


@router.get("/settings/github")
async def github_status(principal: CurrentPrincipal, db: Db, github: GitHub) -> GitHubStatus:
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.get(tc)
    return _status(github, row, principal)


class InstallationIn(BaseModel):
    installation_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=200)
    state: str = Field(min_length=1, max_length=2000)


@router.post("/settings/github/installation")
async def connect_installation(
    body: InstallationIn, principal: CurrentPrincipal, db: Db, github: GitHub
) -> GitHubStatus:
    """Called when the user returns from installing the GitHub App."""
    _require_manager(principal)
    if not github.configured:
        raise _upstream(
            GitHubNotConfigured("the GitHub App is not configured on this installation")
        )
    if not github.check_state(body.state, org_id=principal.org_id, user_id=principal.user_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the GitHub sign-in expired; start again")
    try:
        writable = await github.user_writable_repos(body.code, body.installation_id)
    except GitHubError as exc:
        raise _upstream(exc) from None
    if not writable:
        # Do not attach an installation the signed-in GitHub user does not control, or one
        # in which that user cannot push to any repository.
        log.warning("github_installation_rejected")
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "this GitHub installation does not belong to you"
        )
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.set_github_installation(
            tc, installation_id=body.installation_id, allowed_repos=writable
        )
    log.info("github_connected")
    return _status(github, row, principal)


@router.delete("/settings/github")
async def disconnect(principal: CurrentPrincipal, db: Db, github: GitHub) -> GitHubStatus:
    _require_manager(principal)
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.set_github_installation(
            tc, installation_id=None, allowed_repos=None
        )
    return _status(github, row, principal)


class RepoOut(BaseModel):
    full_name: str
    default_branch: str
    private: bool


def _allowed(row: dict[str, Any]) -> set[str]:
    """Repositories the person who connected the installation could push to themselves.

    The installation token can write to every repository of the installation; the
    workspace may only use these.
    """
    return set(row["github_allowed_repos"] or [])


async def _installation(db: Database, principal: Principal) -> dict[str, Any]:
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.get(tc)
    if row["github_installation_id"] is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "connect the GitHub App first")
    return row


@router.get("/settings/github/repos")
async def list_repos(principal: CurrentPrincipal, db: Db, github: GitHub) -> list[RepoOut]:
    _require_manager(principal)
    row = await _installation(db, principal)
    try:
        repos = await github.list_repos(int(row["github_installation_id"]))
    except GitHubError as exc:
        raise _upstream(exc) from None
    allowed = _allowed(row)
    return [
        RepoOut(full_name=r.full_name, default_branch=r.default_branch, private=r.private)
        for r in repos
        if r.full_name in allowed
    ]


class RepoIn(BaseModel):
    repo: str
    manifest_path: str = "/"


def _clean_path(path: str) -> str:
    cleaned = "/" + path.strip().strip("/")
    if not _PATH.match(cleaned) or ".." in cleaned.split("/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid manifest directory")
    return cleaned


@router.put("/settings/github/repo")
async def choose_repo(
    body: RepoIn, principal: CurrentPrincipal, db: Db, github: GitHub
) -> GitHubStatus:
    _require_manager(principal)
    if not _REPO.match(body.repo):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "repository must look like owner/name"
        )
    manifest_path = _clean_path(body.manifest_path)
    row = await _installation(db, principal)
    installation_id = int(row["github_installation_id"])
    try:
        repos = await github.list_repos(installation_id)
    except GitHubError as exc:
        raise _upstream(exc) from None
    # Only repositories the installation actually covers, and that the person who connected
    # it can push to, can be chosen.
    if body.repo not in {r.full_name for r in repos} & _allowed(row):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "the GitHub App is not installed on this repository"
        )
    async with db.tenant(principal.org_id) as tc:
        row = await org_settings.set_github_repo(tc, repo=body.repo, manifest_path=manifest_path)
    return _status(github, row, principal)


class PullRequestOut(BaseModel):
    pr_url: str
    applied: int
    manual: int


@router.post("/audits/{run_id}/pr", status_code=status.HTTP_201_CREATED)
async def open_pull_request(
    request: Request, run_id: UUID, principal: CurrentPrincipal, db: Db, github: GitHub
) -> PullRequestOut:
    """Opens the fix pull request. Only ever on an explicit request from an owner or admin."""
    _require_manager(principal)
    settings: Settings = request.app.state.settings
    async with db.tenant(principal.org_id) as tc:
        run = await audit_runs.get(tc, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "audit not found")
        rows = await findings.list_for_run(tc, run_id)
        cluster = await clusters.get(tc, run["cluster_id"])
        config = await org_settings.get(tc)
    if run["status"] != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "the audit is not completed yet")
    if run["pr_url"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "a pull request was already opened for this audit"
        )
    if (
        config["github_installation_id"] is None
        or not config["github_repo"]
        or config["github_repo"] not in _allowed(config)
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "connect GitHub and choose a repository first"
        )
    if not any(r["patch"] for r in rows):
        raise HTTPException(status.HTTP_409_CONFLICT, "this audit has no patches to propose")

    installation_id, repo = int(config["github_installation_id"]), str(config["github_repo"])
    report_url = None
    if settings.public_web_url:
        report_url = f"{settings.public_web_url.rstrip('/')}/audits/{run_id}"
    try:
        base_branch, base_sha, sources = await github.read_manifests(
            installation_id, repo, str(config["manifest_path"] or "/")
        )
        plan = plan_pull_request(
            sources,
            rows,
            cluster_name=str(cluster["name"]) if cluster else "cluster",
            summary=str((run["stats"] or {}).get("summary") or ""),
            score=run["score"],
            report_url=report_url,
        )
        if not plan.result.files:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "none of the patches matches a manifest in the selected directory",
            )
        pr_url = await github.open_pull_request(
            installation_id,
            repo,
            base_branch=base_branch,
            base_sha=base_sha,
            branch=plan.branch,
            files=plan.result.files,
            commit_message=plan.commit_message,
            title=plan.title,
            body=plan.body,
        )
    except GitHubError as exc:
        raise _upstream(exc) from None
    async with db.tenant(principal.org_id) as tc:
        await audit_runs.set_pr_url(tc, run_id, pr_url)
    log.info(
        "pull_request_opened",
        run_id=str(run_id),
        applied=len(plan.result.applied),
        manual=len(plan.result.manual),
    )
    return PullRequestOut(
        pr_url=pr_url, applied=len(plan.result.applied), manual=len(plan.result.manual)
    )
