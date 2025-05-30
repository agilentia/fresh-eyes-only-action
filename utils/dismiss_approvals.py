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
                            info(f"  Found approval: ID {review_id} by {reviewer}")
                        else:
                            warning(f"  Found approval by {reviewer} but missing ID")

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
            info(f"  Successfully dismissed review {review_id}")

        except httpx.HTTPStatusError as e:
            # Some failures are expected (e.g., already dismissed)
            if e.response.status_code in [404, 422]:
                warning(
                    f"  Review {review_id}: Already handled (Status {e.response.status_code})"
                )
            else:
                error(f"  Failed to dismiss review {review_id}")
                return False
        except httpx.HTTPError:
            error(f"  Failed to dismiss review {review_id}")
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
            info(f"  Successfully requested reviews from {len(reviewers)} reviewer(s)")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                # Some users might already be requested reviewers or can't be requested
                warning("  Some reviewers cannot be requested (Status 422)")
            else:
                error(f"  Failed to request reviews from: {reviewer_list}")
                return False
        except httpx.HTTPError:
            error(f"  Failed to request reviews from: {reviewer_list}")
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
            error(f"  GitHub API error: {result.stderr}")
            return None
    except Exception as e:
        error(f"  Error running GitHub API command: {e}")
        return None


def get_normalized_hash(text: str | None) -> str:
    """Return a SHA-256 hash of the filtered lines, handling None and empty input."""
    if not text:
        text = ""  # Treat None or empty as empty string

    hasher = hashlib.sha256()
    for line in text.splitlines():
        # ignore meta like hunk allocation
        if not line.startswith(("@@", "+++", "---")):
            hasher.update((line + "\n").encode("utf-8"))
    return hasher.hexdigest()


def get_file_changes(repository: str, main_sha: str, sha: str) -> set[str]:
    """Get the set of changed files between main and given SHA"""
    cmd = [
        "gh",
        "api",
        f"repos/{repository}/compare/{main_sha}...{sha}",
        "--jq",
        ".files[].filename",
    ]
    files = run_gh_api_command(cmd)
    return set(files.splitlines() if files else [])


def get_diff_content(repository: str, main_sha: str, sha: str) -> str | None:
    """Get the diff content between main and given SHA"""
    cmd = [
        "gh",
        "api",
        f"repos/{repository}/compare/{main_sha}...{sha}",
        "--jq",
        ".files[].patch",
    ]
    return run_gh_api_command(cmd)


def check_for_code_changes(base_ref: str, before_sha: str, after_sha: str) -> bool:
    """Check if there are effective code changes between commits"""
    info("Checking for effective code changes...")

    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        warning("  GITHUB_REPOSITORY not set, assuming changes exist")
        return True

    info("  Using GitHub API to compare deltas with main branch")

    # Get main branch SHA
    main_sha_cmd = [
        "gh",
        "api",
        f"repos/{repository}/git/refs/heads/{base_ref}",
        "--jq",
        ".object.sha",
    ]

    main_sha = run_gh_api_command(main_sha_cmd)
    if not main_sha:
        warning("  Could not get main branch SHA, assuming changes exist")
        return True

    debug(f"  Main branch SHA: {main_sha}")

    # Get file changes for both commits
    before_files = get_file_changes(repository, main_sha, before_sha)
    after_files = get_file_changes(repository, main_sha, after_sha)

    if before_files is None or after_files is None:
        warning("  GitHub API call failed, assuming changes exist as a precaution")
        return True

    debug(f"  Files changed in before commit: {len(before_files)}")
    debug(f"  Files changed in after commit: {len(after_files)}")

    # If the sets are different, there are changes
    if before_files != after_files:
        info("  Changes detected - different files modified compared to main branch")
        added = after_files - before_files
        removed = before_files - after_files
        if added:
            debug(f"  New files in after commit: {added}")
        if removed:
            debug(f"  Files only in before commit: {removed}")
        return True

    # Check content changes if same files are affected
    before_diff = get_diff_content(repository, main_sha, before_sha)
    after_diff = get_diff_content(repository, main_sha, after_sha)

    if before_diff is None or after_diff is None:
        warning(
            "  GitHub API call failed when checking diff details, assuming changes exist"
        )
        return True

    # Compare normalized hashes
    if get_normalized_hash(before_diff) == get_normalized_hash(after_diff):
        info("  No changes detected - identical diffs compared to main branch")
        return False

    info("  Changes detected - different diff content compared to main branch")
    return True


def parse_repository(repo_string: str) -> tuple[str, str] | None:
    """Parse repository string in format 'owner/repo'"""
    parts = repo_string.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    error(f"Invalid repository format: {repo_string}")
    return None


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
    envvar="GITHUB_EVENT_BEFORE_SHA",
    required=True,
    help="SHA before push",
)
@click.option(
    "--after-sha", envvar="GITHUB_EVENT_AFTER_SHA", required=True, help="SHA after push"
)
@click.option(
    "--base-ref",
    envvar="GITHUB_EVENT_BASE_REF",
    required=True,
    help="Base branch reference",
)
@click.option(
    "--actor",
    envvar="GITHUB_ACTOR",
    required=True,
    help="GitHub actor who triggered the workflow",
)
@click.option(
    "--repository",
    envvar="GITHUB_REPOSITORY",
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
        if not check_for_code_changes(base_ref, before_sha, after_sha):
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
        if dismissed_reviewers:
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
        has_errors = failed > 0 or (dismissed_reviewers and not rerequest_succeeded)
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
