#!/usr/bin/env python3
"""
Test suite for dismiss_approvals.py

Comprehensive tests covering diff analysis, GitHub API interactions, and utility functions.
Separated into unit tests with mocks and integration tests with real API calls.
"""

import os
import pytest
from unittest.mock import patch, Mock

from dismiss_approvals import (
    check_for_code_changes,
    GitHubClient,
    get_normalized_hash,
    parse_repository,
    parse_exempt_actors,
    get_commit_parents,
    run_gh_api_command,
)


@pytest.fixture
def mock_env_repo():
    """Fixture providing test repository environment"""
    return {"INPUT_REPOSITORY": "test-owner/test-repo"}


@pytest.fixture
def sample_commit_data():
    """Fixture providing sample commit test data"""
    return {
        "identical_shas": "abc123def456",
        "before_sha": "before123456",
        "after_sha": "after789012",
        "merge_sha": "merge345678",
    }


@pytest.fixture
def github_client():
    """Fixture providing a mocked GitHubClient"""
    return GitHubClient(
        token="test_token",
        owner="test_owner",
        repo="test_repo",
        pr_number=123,
        dismissal_message="Test dismissal",
    )


class TestUtilityFunctions:
    """Test utility functions independently"""

    def test_get_normalized_hash_empty_string(self):
        """Test hash generation for empty content"""
        result = get_normalized_hash("")
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # SHA256 of empty string
        assert result == expected

    def test_get_normalized_hash_with_content(self):
        """Test hash generation for actual diff content"""
        diff_content = "+    console.log('test')\n-    console.log('old')"
        result = get_normalized_hash(diff_content)
        # Hash should be consistent
        assert len(result) == 64  # SHA256 hex length
        assert result == get_normalized_hash(diff_content)  # Deterministic

    def test_parse_repository_valid(self):
        """Test repository parsing with valid format"""
        owner, repo = parse_repository("owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repository_invalid(self):
        """Test repository parsing with invalid format"""
        result = parse_repository("invalid-format")
        assert result is None

    def test_parse_exempt_actors_empty(self):
        """Test parsing empty exempt actors string"""
        result = parse_exempt_actors("")
        assert result == set()

    def test_parse_exempt_actors_single(self):
        """Test parsing single exempt actor"""
        result = parse_exempt_actors("user1")
        assert result == {"user1"}

    def test_parse_exempt_actors_multiple(self):
        """Test parsing multiple exempt actors"""
        result = parse_exempt_actors("user1,user2,user3")
        assert result == {"user1", "user2", "user3"}

    def test_parse_exempt_actors_with_whitespace(self):
        """Test parsing exempt actors with whitespace"""
        result = parse_exempt_actors(" user1 , user2 , user3 ")
        assert result == {"user1", "user2", "user3"}


class TestGitHubClientMocked:
    """Test GitHubClient with mocked API calls"""

    @patch("dismiss_approvals.httpx.Client")
    def test_github_client_initialization(self, mock_client_class):
        """Test GitHubClient initialization"""
        client = GitHubClient(
            token="test_token",
            owner="test_owner",
            repo="test_repo",
            pr_number=123,
            dismissal_message="Test message",
        )

        assert client.token == "test_token"
        assert client.owner == "test_owner"
        assert client.repo == "test_repo"
        assert client.pr_number == 123
        assert client.dismissal_message == "Test message"

    def test_get_approved_reviews_success(self):
        """Test successful retrieval of approved reviews"""
        client = GitHubClient(
            token="test_token",
            owner="test_owner",
            repo="test_repo",
            pr_number=123,
            dismissal_message="Test message",
        )

        # Mock the _api_request method directly
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": 1, "state": "APPROVED", "user": {"login": "reviewer1"}},
            {"id": 2, "state": "APPROVED", "user": {"login": "reviewer2"}},
        ]

        with patch.object(client, "_api_request", return_value=mock_response):
            reviewers = client.get_approved_reviews()

        assert len(reviewers) == 2
        assert reviewers[1] == "reviewer1"
        assert reviewers[2] == "reviewer2"

    def test_get_approved_reviews_filters_non_approved(self):
        """Test that only APPROVED reviews are returned, filtering out other states"""
        client = GitHubClient(
            token="test_token",
            owner="test_owner",
            repo="test_repo",
            pr_number=123,
            dismissal_message="Test message",
        )

        # Mock the _api_request method with mixed review states
        mock_response = Mock()
        mock_response.json.return_value = [
            {"id": 1, "state": "APPROVED", "user": {"login": "approved_user"}},
            {"id": 2, "state": "COMMENTED", "user": {"login": "commented_user"}},
            {"id": 3, "state": "CHANGES_REQUESTED", "user": {"login": "blocked_user"}},
            {"id": 4, "state": "APPROVED", "user": {"login": "another_approved"}},
        ]

        with patch.object(client, "_api_request", return_value=mock_response):
            reviewers = client.get_approved_reviews()

        # Should only contain the APPROVED reviews
        assert len(reviewers) == 2
        assert set(reviewers.keys()) == {1, 4}
        assert reviewers[1] == "approved_user"
        assert reviewers[4] == "another_approved"


class TestCodeChangeDetectionUnit:
    """Unit tests for code change detection with comprehensive mocking"""

    @patch("dismiss_approvals.get_diff_content")
    def test_identical_commits(self, mock_get_diff, mock_env_repo, sample_commit_data):
        """Test when before and after commits are identical"""
        sha = sample_commit_data["identical_shas"]
        mock_get_diff.return_value = ""

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes(sha, sha)

        assert result is False
        mock_get_diff.assert_called_once()

    @patch("dismiss_approvals.get_diff_content")
    @patch("dismiss_approvals.get_commit_parents")
    @patch("dismiss_approvals.get_effective_commit_content")
    def test_same_effective_content_different_shas(
        self,
        mock_effective_content,
        mock_get_parents,
        mock_get_diff,
        mock_env_repo,
        sample_commit_data,
    ):
        """Test when commits have different SHAs but same effective content"""
        mock_get_diff.return_value = "some diff content"
        mock_get_parents.return_value = ["parent1"]  # Non-merge commit (single parent)
        mock_effective_content.side_effect = [
            "+same change\n-same removal",
            "+same change\n-same removal",
        ]

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes(
                sample_commit_data["before_sha"],
                sample_commit_data["after_sha"],
            )

        assert result is False

    @patch("dismiss_approvals.check_cumulative_content_changes")
    @patch("dismiss_approvals.get_diff_content")
    @patch("dismiss_approvals.get_commit_parents")
    @patch("dismiss_approvals.get_effective_commit_content")
    def test_different_effective_content(
        self,
        mock_effective_content,
        mock_get_parents,
        mock_get_diff,
        mock_cumulative,
        mock_env_repo,
        sample_commit_data,
    ):
        """Test when commits have different effective content"""
        mock_get_diff.return_value = "+some change\n-some removal"
        mock_get_parents.return_value = ["parent1"]
        mock_effective_content.side_effect = [
            "+added line A\n-removed line",
            "+added line B\n-removed different line",
        ]
        mock_cumulative.return_value = None

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes(
                sample_commit_data["before_sha"],
                sample_commit_data["after_sha"],
            )

        assert result is True

    @patch("dismiss_approvals.get_effective_commit_content")
    @patch("dismiss_approvals.get_diff_content")
    @patch("dismiss_approvals.get_commit_parents")
    @patch("dismiss_approvals.handle_clean_merge_case")
    def test_merge_commit_handling(
        self,
        mock_handle_merge,
        mock_get_parents,
        mock_get_diff,
        mock_effective_content,
        mock_env_repo,
        sample_commit_data,
    ):
        """Test handling of merge commits"""
        mock_get_diff.return_value = "+added line\n-removed line"
        mock_get_parents.return_value = ["parent1", "parent2"]
        mock_handle_merge.return_value = False
        mock_effective_content.return_value = None

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes(
                sample_commit_data["before_sha"],
                sample_commit_data["merge_sha"],
            )

        assert result is False
        mock_handle_merge.assert_called_once()

    @patch("dismiss_approvals.get_diff_content")
    def test_api_failure_fallback(
        self, mock_get_diff, mock_env_repo, sample_commit_data
    ):
        """Test fallback behavior when GitHub API calls fail"""
        mock_get_diff.return_value = None

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes(
                sample_commit_data["before_sha"],
                sample_commit_data["after_sha"],
            )

        assert result is True  # Fail safe - assume changes

    def test_missing_repository_env(self, sample_commit_data):
        """Test behavior when INPUT_REPOSITORY environment variable is missing"""
        with patch.dict(os.environ, {}, clear=True):
            result = check_for_code_changes(
                sample_commit_data["before_sha"],
                sample_commit_data["after_sha"],
            )

        assert result is True  # Fail safe - assume changes

    @patch("dismiss_approvals.get_effective_commit_content")
    @patch("dismiss_approvals.get_diff_content")
    @patch("dismiss_approvals.get_commit_parents")
    @patch("dismiss_approvals.handle_clean_merge_case")
    def test_merge_commit_with_manual_changes_triggers_changes(
        self, mock_handle_merge, mock_get_parents, mock_get_diff, mock_effective_content, mock_env_repo
    ):
        """Test that merge commits with manual changes during conflict resolution are detected"""
        mock_get_diff.return_value = "+added line\n-removed line"
        mock_get_parents.return_value = ["parent1", "parent2"]
        mock_handle_merge.return_value = True
        mock_effective_content.return_value = None

        with patch.dict(os.environ, mock_env_repo):
            result = check_for_code_changes("before", "after")

        assert result is True


@pytest.mark.parametrize(
    "base_ref,before_sha,after_sha,expected",
    [
        ("main", "abc123", "abc123", False),  # Identical SHAs
        ("master", "def456", "ghi789", True),  # Default case - assume changes
    ],
)
class TestParametrizedCodeChanges:
    """Parametrized tests for various code change scenarios"""

    @patch("dismiss_approvals.get_diff_content")
    @patch("dismiss_approvals.get_commit_parents", return_value=["p"])
    def test_code_change_scenarios(
        self, mock_get_parents, mock_get_diff, base_ref, before_sha, after_sha, expected
    ):
        """Test various code change detection scenarios"""
        if before_sha == after_sha:
            mock_get_diff.return_value = ""
        else:
            mock_get_diff.return_value = None  # Simulate API failure

        with patch.dict(os.environ, {"INPUT_REPOSITORY": "test/repo"}):
            result = check_for_code_changes(before_sha, after_sha)

        assert result == expected


class TestIntegrationWithRealAPIs:
    """Integration tests that make real API calls to actual repositories"""

    @pytest.mark.integration
    def test_obr_secretaries_clean_merge_cases(self):
        """Test merge/rebase cases with base changes"""
        test_cases = [
            {
                "name": "Clean rebase",
                "base_ref": "master",
                "before": "c401964a74a888d1d9717a5784b7b370fef206db",
                "after": "cb6e9eedcf78a2cc3b20765d8226eaa7b234602f",
                "expected": False,
            },
            {
                "name": "Clean merge",
                "base_ref": "master",
                "before": "f1c0e1ba95267ce30ceb49024437c1ca948fb2ba",
                "after": "d4208e056969c41af53cb60d0f6f87f5c97e5226",
                "expected": True,
            },
            {
                "name": "Transitive clean merge",
                "base_ref": "master",
                "before": "1ebadaa10c37f9e59dca4b10196367b61509f66a",
                "after": "07c07c68775da288d6c243161142e3e5de39e54d",
                "expected": False,
            },
        ]

        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obr-secretaries"}):
            for case in test_cases:
                result = check_for_code_changes(case["before"], case["after"])
                assert result == case["expected"], f"{case['name']} failed"

    @pytest.mark.integration
    def test_obr_secretaries_with_changes(self):
        """Test cases where actual code changes are detected"""
        test_cases = [
            {
                "name": "Changes detected case 1",
                "base_ref": "master",
                "before": "2545fae103dc7a0eb8e47b9dc084a18ce0809325",
                "after": "989ce3d547671cdace7039eec40a1787d8d377cc",
                "expected": True,
            },
            {
                "name": "Changes detected case 2",
                "base_ref": "master",
                "before": "1e2e30388f38beae4fc3596bbe4d5790b9bf2049",
                "after": "f1c0e1ba95267ce30ceb49024437c1ca948fb2ba",
                "expected": True,
            },
        ]

        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obr-secretaries"}):
            for case in test_cases:
                result = check_for_code_changes(case["before"], case["after"])
                assert result == case["expected"], f"{case['name']} failed"

    @pytest.mark.integration
    def test_obr_secretaries_rebase_with_conflict_resolution(self):
        """Test rebase with conflict resolution should detect changes - PR 6401"""
        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obr-secretaries"}):
            result = check_for_code_changes(
                "3baa6d39032ce7c56935793be05d295acdd7997c",
                "382e4b352c8a30c254278a63695e59a9b6e6ad4f",
            )
            assert result is True, "Should detect changes when conflict resolution"

    @pytest.mark.integration
    def test_obrv2_merge_with_conflict_resolution(self):
        """Test merge commit with conflict resolution should detect changes - PR 10920"""
        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obrv2"}):
            result = check_for_code_changes(
                "ebbb99a55105787c84c96ce558ab0efc3aea9b7d",
                "9fb58042bb009fbb3ee4742303848ca72f3ef200",
            )
            assert result is True, "Should detect changes in merge with conflict resolution"

    @pytest.mark.integration
    @pytest.mark.skip(
        reason="Complex merge-to-merge commit comparison - needs further investigation"
    )
    def test_obrv2_merge_commit_case(self):
        """Test merge commit case with agilentia/obrv2 repository"""
        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obrv2"}):
            result = check_for_code_changes(
                "2124119514487bb9f1718e88ce4ddfeecf2833cd",
                "fa049d58ec4fa545facafebabe9b95c25d238ebe",
            )
            assert result is False, (
                "Merge commit case failed: Should detect clean merge with no logical changes"
            )

    @pytest.mark.integration
    def test_obrv2_squash_and_force_push_case(self):
        """Test squash and force push case with agilentia/obrv2 repository"""
        with patch.dict(os.environ, {"INPUT_REPOSITORY": "agilentia/obrv2"}):
            result = check_for_code_changes(
                "6df654066744529813affea7649e8ba6a2d96b6e",
                "dfc3bcfa32ab67c60dc8c4acefff73acbd519e21",
            )
            assert result is False, (
                "Squash and force push case failed: Should detect equivalent content after squash and force push"
            )


class TestGitCommandMocking:
    """Test git command interactions with proper mocking"""

    @patch("dismiss_approvals.run_gh_api_command")
    def test_get_commit_parents_single_parent(self, mock_run_cmd):
        """Test getting parents for regular commit"""
        mock_run_cmd.return_value = "parent_sha_123"

        result = get_commit_parents("test-owner/test-repo", "commit_sha")

        assert result == ["parent_sha_123"]
        mock_run_cmd.assert_called_once()

    @patch("dismiss_approvals.run_gh_api_command")
    def test_get_commit_parents_merge_commit(self, mock_run_cmd):
        """Test getting parents for merge commit"""
        mock_run_cmd.return_value = "parent1_sha\nparent2_sha"

        result = get_commit_parents("test-owner/test-repo", "merge_sha")

        assert result == ["parent1_sha", "parent2_sha"]

    @patch("dismiss_approvals.run_gh_api_command")
    def test_get_commit_parents_failure(self, mock_run_cmd):
        """Test handling of git command failure"""
        mock_run_cmd.return_value = None

        result = get_commit_parents("test-owner/test-repo", "invalid_sha")

        assert result == []


class TestRunGhApiCommand:
    """Test the git command execution wrapper"""

    @patch("dismiss_approvals.subprocess.run")
    def test_successful_command(self, mock_subprocess):
        """Test successful git command execution"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "command output"
        mock_subprocess.return_value = mock_result

        result = run_gh_api_command(["git", "status"])

        assert result == "command output"

    @patch("dismiss_approvals.subprocess.run")
    def test_failed_command(self, mock_subprocess):
        """Test failed git command execution"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_subprocess.return_value = mock_result

        result = run_gh_api_command(["git", "invalid-command"])

        assert result is None

    @patch("dismiss_approvals.subprocess.run")
    def test_command_exception(self, mock_subprocess):
        """Test command execution with exception"""
        mock_subprocess.side_effect = Exception("Command failed")

        result = run_gh_api_command(["git", "status"])

        assert result is None


class TestEdgeCases:
    """Test edge cases and error conditions"""

    @patch.dict(os.environ, {"INPUT_REPOSITORY": "test/repo"})
    @patch("dismiss_approvals.get_diff_content")
    def test_empty_diff_content(self, mock_get_diff):
        """Test handling of empty diff content"""
        mock_get_diff.return_value = ""

        result = check_for_code_changes("sha1", "sha2")

        assert result is False

    @patch.dict(os.environ, {"INPUT_REPOSITORY": "test/repo"})
    @patch("dismiss_approvals.get_diff_content")
    def test_whitespace_only_diff(self, mock_get_diff):
        """Test handling of whitespace-only changes"""
        mock_get_diff.return_value = "   \n\t\n   "

        result = check_for_code_changes("sha1", "sha2")

        assert result is False


if __name__ == "__main__":
    # Run tests if executed directly
    # Use -m pytest for better integration test handling
    pytest.main([__file__, "-v", "-m", "not integration"])
