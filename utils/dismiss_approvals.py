#!/usr/bin/env python3
"""
GitHub Action: Smart PR Approval Dismissal
Automatically dismisses stale PR approvals when code changes are detected.
"""
import os
import sys
import subprocess
import logging
from typing import List, Set, Optional, Tuple
import httpx
import click

# Constants
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "GitHub-Action-Smart-Approval-Dismissal/1.3"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
GIT_DIFF_NO_CHANGES = 0
GIT_DIFF_HAS_CHANGES = 1

# Setup logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)
logger.propagate = False


# Configure GitHub Actions workflow commands in log messages
def log_gh_command(level, message):
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


# Custom log methods that use GitHub Actions format
def debug(msg):
    if logger.level <= logging.DEBUG:
        log_gh_command(logging.DEBUG, msg)


def info(msg):
    if logger.level <= logging.INFO:
        print(msg)  # No prefix for better readability


def warning(msg):
    log_gh_command(logging.WARNING, msg)


def error(msg):
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
        return httpx.Client(headers=headers, timeout=30.0)

    def _api_request(self, method: str, endpoint: str, **kwargs) -> httpx.Response:
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

    def get_approved_reviews(self) -> List[int]:
        """Fetch all approved reviews for the PR"""
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews"
        info(f"Fetching approved reviews for PR #{self.pr_number}...")

        approved_ids = []
        page = 1
        max_pages = 20  # Safety limit

        while page <= max_pages:
            try:
                response = self._api_request(
                    "GET", endpoint, params={"page": page, "per_page": 100}
                )
                reviews = response.json()

                if not reviews:
                    break

                # Extract approved review IDs
                for review in reviews:
                    if review.get("state") == "APPROVED":
                        review_id = review.get("id")
                        reviewer = review.get("user", {}).get("login", "Unknown")
                        if review_id:
                            approved_ids.append(review_id)
                            info(f"  Found approval: ID {review_id} by {reviewer}")
                        else:
                            warning(f"  Found approval by {reviewer} but missing ID")

                # Check if we've reached the last page
                if len(reviews) < 100:
                    break

                page += 1

            except httpx.HTTPError:
                error("Failed to fetch reviews")
                return []

        # Don't close the client here - it's needed for dismiss_review
        return list(set(approved_ids))  # Remove any duplicates

    def dismiss_review(self, review_id: int) -> bool:
        """Dismiss a specific review"""
        endpoint = f"/repos/{self.owner}/{self.repo}/pulls/{self.pr_number}/reviews/{review_id}/dismissals"
        payload = {"message": self.dismissal_message}

        info(f"Dismissing review {review_id}...")

        try:
            self._api_request("PUT", endpoint, json=payload)
            info(f"  Successfully dismissed review {review_id}")
            return True
        except httpx.HTTPStatusError as e:
            # Some failures are expected (e.g., already dismissed)
            if e.response.status_code in [404, 422]:
                warning(
                    f"  Review {review_id}: Already handled (Status {e.response.status_code})"
                )
                return True
            else:
                error(f"  Failed to dismiss review {review_id}")
                return False
        except httpx.HTTPError:
            error(f"  Failed to dismiss review {review_id}")
            return False


def check_for_code_changes(base_ref: str, before_sha: str, after_sha: str) -> bool:
    """
    Check if there are effective code changes between commits using git range-diff
    Returns True if changes exist, False otherwise
    """
    info("Checking for effective code changes...")

    # The correct format for git range-diff is: git range-diff <base> <rev1> <rev2>
    cmd = ["git", "range-diff", "--quiet", base_ref, before_sha, after_sha]

    debug(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=60
        )
        debug(f"Git exit code: {result.returncode}")
        debug(f"Git stdout: {result.stdout.strip() or '[empty]'}")
        debug(f"Git stderr: {result.stderr.strip() or '[empty]'}")

        if result.returncode == GIT_DIFF_NO_CHANGES:
            info("  No effective code changes detected")
            return False
        elif result.returncode == GIT_DIFF_HAS_CHANGES:
            info("  Code changes detected")
            return True
        else:
            error(f"  Git range-diff failed with exit code {result.returncode}")
            warning("  Assuming changes exist as a precaution")
            return True

    except Exception as e:
        error(f"  Error running git command: {e}")
        warning("  Assuming changes exist as a precaution")
        return True


def parse_repository(repo_string: str) -> Optional[Tuple[str, str]]:
    """Parse repository string in format 'owner/repo'"""
    parts = repo_string.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    error(f"Invalid repository format: {repo_string}")
    return None


def parse_exempt_actors(actors_string: str) -> Set[str]:
    """Parse comma-separated list of exempt actors"""
    if not actors_string:
        return set()
    return {
        actor.strip().lower() for actor in actors_string.split(",") if actor.strip()
    }


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
    default="",
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
    github_token,
    pr_number,
    dismissal_message,
    exempt_actors,
    before_sha,
    after_sha,
    base_ref,
    actor,
    repository,
    api_url,
    quiet,
):
    """
    GitHub Action: Dismisses stale PR approvals based on code changes.
    """
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

    # Check for effective code changes
    if not check_for_code_changes(base_ref, before_sha, after_sha):
        info("✓ No effective code changes, no need to dismiss approvals")
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

    # Get approved reviews
    try:
        approved_ids = client.get_approved_reviews()
    except Exception as e:
        error(f"Failed to get approval reviews: {e}")
        sys.exit(EXIT_FAILURE)

    if not approved_ids:
        info("✓ No approvals to dismiss")
        sys.exit(EXIT_SUCCESS)

    # Dismiss reviews
    info(f"Found {len(approved_ids)} approval(s) to dismiss")
    info(f"Using dismissal message: '{dismissal_message}'")

    succeeded = 0
    failed = 0

    for review_id in approved_ids:
        try:
            if client.dismiss_review(review_id):
                succeeded += 1
            else:
                failed += 1
        except Exception as e:
            error(f"Error dismissing review {review_id}: {e}")
            failed += 1

    # Clean up resources
    client.client.close()

    # Summary
    info("=== Summary ===")
    info(f"Total approvals: {len(approved_ids)}")
    info(f"Successfully dismissed: {succeeded}")
    if failed:
        info(f"Failed to dismiss: {failed}")

    if failed > 0:
        error("❌ Action completed with errors")
        sys.exit(EXIT_FAILURE)
    else:
        info("✓ Action completed successfully")
        sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
