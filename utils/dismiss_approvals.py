#!/usr/bin/env python3
"""
GitHub Action: Smart PR Approval Dismissal
Automatically dismisses stale PR approvals when code changes are detected.
"""

import hashlib
import logging
import os
import subprocess
import sys
from typing import Any

import click
import httpx

# Constants
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "GitHub-Action-Smart-Approval-Dismissal/1.3"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
MAX_PAGES = 20
PER_PAGE = 100
API_TIMEOUT = 30.0
JQ_STATS_FILTER = (
    "(.files | map(.additions) | add // 0), (.files | map(.deletions) | add // 0)"
)
EMPTY_DIFF_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # SHA-256 of empty string

# Setup logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)
logger.propagate = False


def log_gh_command(level: int, message: str) -> None:
    """Format log messages with GitHub Actions command syntax when appropriate"""
    prefix = ""
    if level == logging.DEBUG:
        prefix = "::debug::"
    elif level == logging.WARNING:
        prefix = "::warning::"
    elif level == logging.ERROR:
        prefix = "::error::"
    # No prefix for INFO (cleaner output)
    print(f"{prefix}{message}")


def debug(msg: str) -> None:
    if logger.level <= logging.DEBUG:
        log_gh_command(logging.DEBUG, msg)


def info(msg: str) -> None:
    if logger.level <= logging.INFO:
        print(msg)  # No prefix for better readability


def warning(msg: str) -> None:
    log_gh_command(logging.WARNING, msg)


def error(msg: str) -> None:
    log_gh_command(logging.ERROR, msg)


class GitHubClient:
    """Client for GitHub API interactions related to PR reviews"""

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        pr_number: int,
        dismissal_message: str,
        api_url: str = "https://api.github.com",
    ):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.pr_number = pr_number
        self.dismissal_message = dismissal_message
        self.api_url = api_url
        self.client = self._create_client()
        debug(f"GitHubClient initialized for {owner}/{repo}#{pr_number}")

    def _create_client(self) -> httpx.Client:
        """Create and configure an httpx client with appropriate headers"""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        return httpx.Client(headers=headers, timeout=API_TIMEOUT)

    def _api_request(self, method: str, endpoint: str, **kwargs: Any) -> httpx.Response:
        """Make an API request and handle errors consistently"""
        url = f"{self.api_url}{endpoint}"
        debug(f"API Request: {method} {url}")

        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
            debug(f"API Response: {response.status_code}")
            return response
        except httpx.HTTPStatusError as e:
            error_msg = f"API Error: {method} {url} - {e}"
            error_msg += (
                f"\nStatus: {e.response.status_code}, Response: {e.response.text[:200]}"
            )
            error(error_msg)
            raise
        except httpx.HTTPError as e:
            error(f"API Error: {method} {url} - {e}")
            raise

    def get_approved_reviews(self) -> dict[int, str]:
        """Fetch all approved reviews for the PR

        Returns:
            dict: mapping review ID to reviewer login
        """
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews"
        info(f"Fetching approved reviews for PR #{self.pr_number}...")

        reviewers = {}  # Map review_id -> reviewer_login
        page = 1

        while page <= MAX_PAGES:
            try:
                response = self._api_request(
                    "GET", endpoint, params={"page": page, "per_page": PER_PAGE}
                )
                reviews = response.json()

                if not reviews:
                    break

                # Extract approved review IDs and reviewers
                for review in reviews:
                    if review.get("state") == "APPROVED":
                        review_id = review.get("id")
                        reviewer = review.get("user", {}).get("login", "Unknown")
                        if review_id:
                            reviewers[review_id] = reviewer
                            info(f"Found approval: ID {review_id} by {reviewer}")
                        else:
                            warning(f"Found approval by {reviewer} but missing ID")

                # Check if we've reached the last page
                if len(reviews) < PER_PAGE:
                    break

                page += 1
            except httpx.HTTPError:
                error("Failed to fetch reviews")
                return {}

        return reviewers

    def dismiss_review(self, review_id: int) -> bool:
        """Dismiss a specific review"""
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews/{review_id}/dismissals"
        payload = {"message": self.dismissal_message}

        info(f"Dismissing review {review_id}...")

        try:
            self._api_request("PUT", endpoint, json=payload)
            info(f"Successfully dismissed review {review_id}")

        except httpx.HTTPStatusError as e:
            # Some failures are expected (e.g., already dismissed)
            if e.response.status_code in [404, 422]:
                warning(
                    f"Review {review_id}: Already handled (Status {e.response.status_code})"
                )
            else:
                error(f"Failed to dismiss review {review_id}")
                return False
        except httpx.HTTPError:
            error(f"Failed to dismiss review {review_id}")
            return False

        return True

    def request_reviews(self, reviewers: list[str]) -> bool:
        """Request reviews from multiple users"""
        if not reviewers:
            return True

        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/requested_reviewers"
        payload = {"reviewers": reviewers}

        reviewer_list = ", ".join(reviewers)
        info(f"Requesting reviews from: {reviewer_list}")

        try:
            self._api_request("POST", endpoint, json=payload)
            info(f"Successfully requested reviews from {len(reviewers)} reviewer(s)")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # Some users might already be requested reviewers or can't be requested
                warning("Some reviewers cannot be requested (Status 422)")
            else:
                error(f"Failed to request reviews from: {reviewer_list}")
                return False
        except httpx.HTTPError:
            error(f"Failed to request reviews from: {reviewer_list}")
            return False

        return True

    def close(self) -> None:
        """Close the HTTP client"""
        self.client.close()


def run_gh_api_command(cmd: list[str]) -> str | None:
    """Run a GitHub API command via gh CLI and return the result"""
    debug(f"Running command: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode == 0:
            return result.stdout.strip()
        else:
            error(f"GitHub API error: {result.stderr}")
    except Exception as e:
        error(f"Error running GitHub API command: {e}")


def gh_api_compare(
    repository: str, base: str, head: str, jq_filter: str = None, diff: bool = False
) -> str | None:
    """Helper to make GitHub API compare calls"""
    cmd = ["gh", "api", f"repos/{repository}/compare/{base}...{head}"]
    if diff:
        cmd.extend(["--header", "Accept: application/vnd.github.diff"])
    elif jq_filter:
        cmd.extend(["--jq", jq_filter])
    return run_gh_api_command(cmd)


def gh_api_commit(
    repository: str, commit_sha: str, jq_filter: str = None, diff: bool = False
) -> str | None:
    """Helper to make GitHub API commit calls"""
    cmd = ["gh", "api", f"repos/{repository}/commits/{commit_sha}"]
    if diff:
        cmd.extend(["--header", "Accept: application/vnd.github.diff"])
    elif jq_filter:
        cmd.extend(["--jq", jq_filter])
    return run_gh_api_command(cmd)


def get_normalized_hash(text: str | None) -> str:
    """Return a SHA-256 hash of only the actual changes, ignoring context and metadata."""
    if not text:
        text = ""  # Treat None or empty as empty string

    hasher = hashlib.sha256()
    for line in text.splitlines():
        # Only include actual change lines (+ and -), ignore context lines and metadata
        # This accounts for master branch movement that changes context but not actual changes
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            hasher.update((line + "\n").encode("utf-8"))
    return hasher.hexdigest()


def get_diff_content(repository: str, base_sha: str, commit_sha: str) -> str | None:
    """Get the diff content between base and commit using GitHub's diff media type"""
    return gh_api_compare(repository, base_sha, commit_sha, diff=True)


def extract_feature_branch_changes(
    repository: str, merge_commit: str, feature_parent: str, master_parent: str
) -> str | None:
    """Extract feature-branch contributions.
    - For a regular feature commit: diff(merge_base, feature_parent)
    - For a merge commit: diff(master_parent, merge_commit)  (net feature effect merged into master)
    """

    # Get the merge base between feature and master parents
    merge_base = gh_api_compare(
        repository, feature_parent, master_parent, jq_filter=".merge_base_commit.sha"
    )

    if not merge_base:
        return

    debug(f"Merge base between feature and master: {merge_base}")

    # If feature_parent is a merge (two parents), compare master_parent..feature_parent
    parents = get_commit_parents(repository, feature_parent)
    if len(parents) == 2:
        feature_changes = get_diff_content(repository, master_parent, feature_parent)
    else:
        # Otherwise, compare merge_base..feature_parent
        feature_changes = get_diff_content(repository, merge_base, feature_parent)

    return feature_changes


def get_individual_commit_diff(repository: str, commit_sha: str) -> str | None:
    """Get the diff for an individual commit (what that commit changed)"""
    return gh_api_commit(repository, commit_sha, diff=True)


def handle_transitive_clean_merge_case(
    repository: str, before_sha: str, after_sha: str, parent_shas: list[str]
) -> bool | None:
    """Handle the transitive clean merge case where before_sha is an ancestor of a merge parent.

    Returns:
        bool: True if changes detected, False if no changes, None if unable to determine
    """
    if len(parent_shas) != 2:
        return

    info("Checking for transitive clean merge case...")

    # Check if before_sha is an ancestor of any merge parent
    for parent_sha in parent_shas:
        # Check the relationship between before_sha and this parent
        relationship_result = gh_api_compare(
            repository,
            before_sha,
            parent_sha,
            jq_filter=".behind_by, .merge_base_commit.sha",
        )

        if not relationship_result:
            continue

        lines = relationship_result.strip().split("\n")
        if len(lines) >= 2:
            behind_by = lines[0]
            merge_base = lines[1]

            debug(
                f"Before_sha vs parent {parent_sha}: behind_by={behind_by}, merge_base={merge_base}"
            )

            # If behind_by is 0 and merge_base is before_sha, then before_sha is an ancestor
            if behind_by == "0" and merge_base == before_sha:
                info(
                    "Found transitive relationship - before commit is ancestor of merge parent"
                )

                # For transitive clean merge, verify this is a clean merge sequence by checking:
                # 1. Same files are modified
                # 2. Incremental commit count (merge commits don't add logical changes)

                # Find the other parent (master branch at time of merge)
                other_parent = (
                    parent_shas[1] if parent_shas[0] == parent_sha else parent_shas[0]
                )

                # Find the base for the original approved commit
                approved_base = gh_api_compare(
                    repository,
                    before_sha,
                    other_parent,
                    jq_filter=".merge_base_commit.sha",
                )

                if not approved_base:
                    warning(
                        "Could not find base for approved commit, assuming changes exist"
                    )
                    return True

                # Check additions/deletions by summing file changes to confirm clean merge sequence
                approved_stats = gh_api_compare(
                    repository, approved_base, before_sha, jq_filter=JQ_STATS_FILTER
                )
                merge_stats = gh_api_compare(
                    repository, other_parent, after_sha, jq_filter=JQ_STATS_FILTER
                )

                if approved_stats and merge_stats:
                    approved_lines = approved_stats.strip().split("\n")
                    merge_lines = merge_stats.strip().split("\n")

                    if len(approved_lines) >= 2 and len(merge_lines) >= 2:
                        approved_adds, approved_dels = (
                            approved_lines[0],
                            approved_lines[1],
                        )
                        merge_adds, merge_dels = merge_lines[0], merge_lines[1]

                        if approved_adds == merge_adds and approved_dels == merge_dels:
                            info(
                                f"Transitive clean merge detected - identical stats (+{approved_adds} -{approved_dels})"
                            )
                            info(
                                "No changes detected - clean transitive merge sequence preserves logical PR content"
                            )
                            return False
                        else:
                            debug(
                                f"Stats mismatch - approved: +{approved_adds} -{approved_dels}, merge: +{merge_adds} -{merge_dels}"
                            )
                            info(
                                "Changes detected - different line additions/deletions in transitive merge"
                            )
                            return True

                warning(
                    "Could not get stats for transitive merge comparison, assuming changes exist"
                )
                return True


def handle_clean_merge_case(
    repository: str, before_sha: str, after_sha: str, parent_shas: list[str], direct_diff: str = None
) -> bool | None:
    """Handle the clean merge case where after_sha is a merge commit.

    Args:
        direct_diff: Optional direct comparison diff to check for conflict resolution

    Returns:
        bool: True if changes detected, False if no changes, None if unable to determine
    """
    if len(parent_shas) != 2:
        return

    # Check if before_sha is one of the parents (direct clean merge)
    if before_sha in parent_shas:
        info("Detected clean merge pattern - comparing feature branch contributions...")

        master_parent = (
            parent_shas[1] if parent_shas[0] == before_sha else parent_shas[0]
        )

        debug(f"Feature parent: {before_sha}")
        debug(f"Master parent: {master_parent}")

        # Extract feature branch changes for both commits
        before_feature_changes = extract_feature_branch_changes(
            repository, before_sha, before_sha, master_parent
        )
        after_feature_changes = extract_feature_branch_changes(
            repository, after_sha, before_sha, master_parent
        )

        if before_feature_changes is None or after_feature_changes is None:
            warning("Could not extract feature branch changes, assuming changes exist")
            return True

        # Compare the feature branch contributions
        before_hash = get_normalized_hash(before_feature_changes)
        after_hash = get_normalized_hash(after_feature_changes)

        debug(f"Before feature changes hash: {before_hash}")
        debug(f"After feature changes hash: {after_hash}")

        if before_hash == after_hash:
            if direct_diff and get_normalized_hash(direct_diff) != EMPTY_DIFF_HASH:
                info("Merge commit has changes in direct comparison")
                info("Changes detected - merge brought in changes that need review")
                return True
            info("No changes detected - identical feature branch contributions (clean merge)")
            return False

        info("Changes detected - different feature branch contributions")
        return True

    # Handle consecutive merge commits case
    # Check if before_sha represents the same feature branch content as one of the parents
    info("Checking for consecutive merge commits case...")

    for parent_sha in parent_shas:
        # Check if before_sha and parent_sha represent the same feature branch state
        # by comparing their feature branch contributions
        other_parent = (
            parent_shas[1] if parent_shas[0] == parent_sha else parent_shas[0]
        )

        # Get feature branch changes for before_sha compared to the other parent
        before_feature_changes = extract_feature_branch_changes(
            repository, before_sha, before_sha, other_parent
        )

        # Get feature branch changes for this parent compared to the other parent
        parent_feature_changes = extract_feature_branch_changes(
            repository, parent_sha, parent_sha, other_parent
        )

        if before_feature_changes is None or parent_feature_changes is None:
            continue

        before_hash = get_normalized_hash(before_feature_changes)
        parent_hash = get_normalized_hash(parent_feature_changes)

        debug(f"Before feature changes hash: {before_hash}")
        debug(f"Parent {parent_sha} feature changes hash: {parent_hash}")

        if before_hash == parent_hash:
            # before_sha and this parent have identical feature contributions
            # Now check if the merge (after_sha) just combines these identical contributions
            after_feature_changes = extract_feature_branch_changes(
                repository, after_sha, parent_sha, other_parent
            )

            if after_feature_changes is None:
                continue

            after_hash = get_normalized_hash(after_feature_changes)
            debug(f"After feature changes hash: {after_hash}")

            if after_hash == before_hash:
                info(
                    "No changes detected - consecutive merge with identical feature contributions"
                )
                return False


def get_commit_parents(repository: str, commit_sha: str) -> list[str]:
    """Get parent SHAs for a commit"""
    parents = gh_api_commit(repository, commit_sha, ".parents[].sha")
    return parents.splitlines() if parents else []


def get_effective_commit_content(repository: str, commit_sha: str) -> str | None:
    """Get the effective content changes for a commit.
    For merge commits, extracts feature branch changes.
    For regular commits, returns individual commit diff.
    """
    parents = get_commit_parents(repository, commit_sha)

    if len(parents) == 2:
        # Merge commit - extract feature branch changes
        debug(f"Extracting feature content from merge commit {commit_sha}")
        for i, parent_sha in enumerate(parents):
            other_parent = parents[1 - i]
            feature_changes = extract_feature_branch_changes(
                repository, commit_sha, parent_sha, other_parent
            )
            if feature_changes:
                return feature_changes
        return None
    else:
        # Regular commit - return individual commit diff
        return get_individual_commit_diff(repository, commit_sha)


def check_cumulative_content_changes(
    repository: str, before_sha: str, after_sha: str
) -> bool | None:
    """
    Check if two single-parent commits represent the same cumulative changes from a common base.
    This handles squash/rebase scenarios where individual commit diffs are different
    but the net logical changes are the same.

    Returns:
        False if no logical changes detected (same cumulative effect)
        True if changes detected
        None if unable to determine (fallback to other methods)
    """
    # Find merge base between the two commits
    merge_base = gh_api_compare(
        repository, before_sha, after_sha, jq_filter=".merge_base_commit.sha"
    )

    if not merge_base:
        debug("Could not find merge base for cumulative content comparison")
        return None

    debug(f"Merge base for cumulative comparison: {merge_base}")

    # Get cumulative changes from merge base to each commit
    before_cumulative = get_diff_content(repository, merge_base, before_sha)
    after_cumulative = get_diff_content(repository, merge_base, after_sha)

    if before_cumulative is None or after_cumulative is None:
        debug("Could not get cumulative diff content")
        return None

    # Compare the normalized cumulative changes
    before_hash = get_normalized_hash(before_cumulative)
    after_hash = get_normalized_hash(after_cumulative)

    debug(f"Before cumulative changes hash: {before_hash}")
    debug(f"After cumulative changes hash: {after_hash}")

    if before_hash == after_hash:
        info(
            "No changes detected - identical cumulative changes (squash/rebase scenario)"
        )
        return False
    else:
        info("Changes detected - different cumulative changes")
        return True


def check_for_code_changes(
    before_sha: str, after_sha: str, repository: str = None
) -> bool:
    """Check if there are effective code changes between commits"""
    if repository is None:
        repository = os.environ.get("INPUT_REPOSITORY")

    info(f"Checking for effective code changes in {repository}...")

    if not repository:
        warning("Repository not provided, assuming changes exist")
        return True

    # First try direct comparison
    direct_diff = get_diff_content(repository, before_sha, after_sha)
    if direct_diff is not None and not direct_diff.strip():
        info("No changes detected - commits are identical")
        return False

    # Check if direct diff has actual code changes
    direct_has_changes = False
    if direct_diff is not None:
        direct_hash = get_normalized_hash(direct_diff)
        direct_has_changes = direct_hash != EMPTY_DIFF_HASH

        if direct_has_changes:
            debug(f"Direct comparison shows changes (hash: {direct_hash})")
        else:
            info("No changes detected - direct diff has no code changes")
            return False

    # Check if after_sha is a merge commit (clean merge case)
    after_parents = get_commit_parents(repository, after_sha)
    if len(after_parents) == 2:
        info("Checking for clean merge pattern...")
        merge_result = handle_clean_merge_case(
            repository, before_sha, after_sha, after_parents, direct_diff
        )
        if merge_result is not None:
            return merge_result

    # Check for text-equivalent commits by comparing effective content first
    info("Checking for text-equivalent commits...")

    before_content = get_effective_commit_content(repository, before_sha)
    after_content = get_effective_commit_content(repository, after_sha)

    if before_content is not None and after_content is not None:
        before_hash = get_normalized_hash(before_content)
        after_hash = get_normalized_hash(after_content)

        debug(f"Before effective content hash: {before_hash}")
        debug(f"After effective content hash: {after_hash}")

        before_parents = get_commit_parents(repository, before_sha)
        is_single_parent_rebase = len(before_parents) == 1 and len(after_parents) == 1

        if before_hash == after_hash:
            if is_single_parent_rebase and direct_has_changes:
                commits_between = gh_api_compare(
                    repository, before_sha, after_sha, jq_filter='[.commits[] | .commit.message] | join("\\n")'
                )

                if commits_between:
                    commits_text = commits_between.lower()
                    if "conflict" in commits_text or "# conflicts:" in commits_text:
                        info("Commit messages indicate conflicts were resolved")
                        info("Changes detected - conflict resolution during rebase")
                        return True

            info("No changes detected - identical effective content")
            return False

        if is_single_parent_rebase:
            info("Different effective content, checking for squash/rebase scenario...")
            squash_result = check_cumulative_content_changes(
                repository, before_sha, after_sha
            )
            if squash_result is not None:
                return squash_result

    # Fallback: Could not get effective commit content
    if before_content is None or after_content is None:
        warning("Could not get effective commit content, using direct comparison")
        if direct_diff is None:
            warning("GitHub API call failed, assuming changes exist")
            return True
        info("Changes detected - direct diff found")
        return True

    # Check for transitive clean merge case (when before_sha is ancestor of merge parent)
    if len(after_parents) == 2:
        transitive_result = handle_transitive_clean_merge_case(
            repository, before_sha, after_sha, after_parents
        )
        if transitive_result is not None:
            return transitive_result

    info("Changes detected - different effective content")
    return True


def parse_repository(repo_string: str) -> tuple[str, str] | None:
    """Parse repository string in format 'owner/repo'"""
    parts = repo_string.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    error(f"Invalid repository format: {repo_string}")


def parse_exempt_actors(actors_string: str) -> set[str]:
    """Parse comma-separated list of exempt actors"""
    if not actors_string:
        return set()
    return {
        actor.strip().lower() for actor in actors_string.split(",") if actor.strip()
    }


def print_summary(
    approved_count: int,
    dismissed_count: int,
    failed_count: int,
    dismissed_reviewers: set[str],
    rerequest_succeeded: bool,
) -> None:
    """Print operation summary"""
    info("=== Summary ===")
    info(f"Total approvals: {approved_count}")
    info(f"Successfully dismissed: {dismissed_count}")
    if failed_count:
        info(f"Failed to dismiss: {failed_count}")

    if dismissed_reviewers:
        if rerequest_succeeded:
            info(f"Re-requested reviews: {len(dismissed_reviewers)}")
        else:
            info("Failed to re-request reviews")


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option(
    "--github-token", envvar="INPUT_GITHUB_TOKEN", required=True, help="GitHub token"
)
@click.option(
    "--pr-number", envvar="INPUT_PR_NUMBER", required=True, type=int, help="PR number"
)
@click.option(
    "--dismissal-message",
    envvar="INPUT_DISMISSAL_MESSAGE",
    required=True,
    help="Message to use when dismissing reviews",
)
@click.option(
    "--exempt-actors",
    envvar="INPUT_EXEMPT_ACTORS",
    default="SherpanyDevOps",
    help="Comma-separated list of exempt actors",
)
@click.option(
    "--before-sha",
    envvar="INPUT_EVENT_BEFORE_SHA",
    required=True,
    help="SHA before push",
)
@click.option(
    "--after-sha", envvar="INPUT_EVENT_AFTER_SHA", required=True, help="SHA after push"
)
@click.option(
    "--base-ref",
    envvar="INPUT_EVENT_BASE_REF",
    required=True,
    help="Base branch reference",
)
@click.option(
    "--actor",
    envvar="INPUT_ACTOR",
    required=True,
    help="GitHub actor who triggered the workflow",
)
@click.option(
    "--repository",
    envvar="INPUT_REPOSITORY",
    required=True,
    help="Repository in format owner/repo",
)
@click.option(
    "--api-url",
    envvar="GITHUB_API_URL",
    default="https://api.github.com",
    help="GitHub API URL",
)
@click.option("--quiet", "-q", is_flag=True, help="Reduce output verbosity")
@click.option(
    "--rerequest-review/--no-rerequest-review",
    envvar="INPUT_REREQUEST_REVIEW",
    default=True,
    help="Re-request review after dismissal",
)
def main(
    github_token: str,
    pr_number: int,
    dismissal_message: str,
    exempt_actors: str,
    before_sha: str,
    after_sha: str,
    base_ref: str,
    actor: str,
    repository: str,
    api_url: str,
    quiet: bool,
    rerequest_review: bool,
) -> None:
    """GitHub Action: Dismisses stale PR approvals based on code changes."""
    # Set log level based on flags and environment
    if quiet:
        logger.setLevel(logging.WARNING)
    else:
        logger.setLevel(
            logging.DEBUG if os.getenv("RUNNER_DEBUG") == "1" else logging.INFO
        )

    # Parse repository
    repo_parts = parse_repository(repository)
    if not repo_parts:
        sys.exit(EXIT_FAILURE)
    owner, repo = repo_parts

    # Parse exempt actors
    exempt_actor_set = parse_exempt_actors(exempt_actors)

    # Display initial info
    info("=== Smart Approval Dismissal ===")
    info(f"Repository:    {repository}")
    info(f"PR Number:     {pr_number}")
    info(f"Actor:         {actor}")
    info(f"Base Branch:   {base_ref}")
    info(f"Commit Range:  {before_sha[:7]} → {after_sha[:7]}")
    if exempt_actor_set:
        info(f"Exempt Actors: {', '.join(exempt_actor_set)}")
    info("===========================")

    # Check if actor is exempt
    if actor.lower() in exempt_actor_set:
        info(f"✓ Actor '{actor}' is exempt, skipping approval dismissal")
        sys.exit(EXIT_SUCCESS)

    # Initialize GitHub client
    client = GitHubClient(
        token=github_token,
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        dismissal_message=dismissal_message,
        api_url=api_url,
    )

    try:
        # Get approved reviews
        reviewers = client.get_approved_reviews()

        if not reviewers:
            info("✓ No approvals to dismiss")
            sys.exit(EXIT_SUCCESS)

        # Check for effective code changes
        if not check_for_code_changes(before_sha, after_sha, repository):
            info("✓ No effective code changes, no need to dismiss approvals")
            sys.exit(EXIT_SUCCESS)

        # Dismiss reviews
        info(f"Found {len(reviewers)} approval(s) to dismiss")
        info(f"Using dismissal message: '{dismissal_message}'")

        succeeded = 0
        failed = 0
        dismissed_reviewers = set()

        for review_id, reviewer in reviewers.items():
            try:
                if client.dismiss_review(review_id):
                    succeeded += 1
                    dismissed_reviewers.add(reviewer)
                else:
                    failed += 1
            except Exception as e:
                error(f"Error dismissing review {review_id}: {e}")
                failed += 1

        # Re-request reviews from dismissed reviewers
        rerequest_succeeded = False
        if dismissed_reviewers and rerequest_review:
            info(
                f"Re-requesting reviews from {len(dismissed_reviewers)} reviewer(s)..."
            )
            try:
                rerequest_succeeded = client.request_reviews(list(dismissed_reviewers))
            except Exception as e:
                error(f"Error re-requesting reviews: {e}")
                rerequest_succeeded = False

        # Print summary
        print_summary(
            len(reviewers),
            succeeded,
            failed,
            dismissed_reviewers,
            rerequest_succeeded,
        )

        # Determine exit code
        has_errors = failed > 0 or (
            dismissed_reviewers and rerequest_review and not rerequest_succeeded
        )
        if has_errors:
            error("❌ Action completed with errors")
            sys.exit(EXIT_FAILURE)
        else:
            info("✓ Action completed successfully")
            sys.exit(EXIT_SUCCESS)

    except Exception as e:
        error(f"Failed to process PR reviews: {e}")
        sys.exit(EXIT_FAILURE)
    finally:
        client.close()


if __name__ == "__main__":
    main()
