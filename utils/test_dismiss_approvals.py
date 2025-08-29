#!/usr/bin/env python3
"""
Test suite for dismiss_approvals.py

Tests the smart diff analysis logic with real commit SHAs to verify
correct detection of code changes vs. clean merges.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

# Import the module under test
from dismiss_approvals import check_for_code_changes


class TestRealRepositoryCalls:
    """Test cases that make real API calls to actual repositories with real commit SHAs"""

    def test_obr_secretaries_no_changes_clean_merge_case_1(self):
        """Test case 1: Clean merge with no logical changes in obr-secretaries (should return False)"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obr-secretaries'}):
            result = check_for_code_changes(
                "master",
                "c401964a74a888d1d9717a5784b7b370fef206db",
                "cb6e9eedcf78a2cc3b20765d8226eaa7b234602f",
            )
            assert result is False, "Expected no changes for clean merge case 1"

    def test_obr_secretaries_no_changes_clean_merge_case_2(self):
        """Test case 2: Clean merge with no logical changes in obr-secretaries (should return False)"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obr-secretaries'}):
            result = check_for_code_changes(
                "master",
                "f1c0e1ba95267ce30ceb49024437c1ca948fb2ba",
                "d4208e056969c41af53cb60d0f6f87f5c97e5226",
            )
            assert result is False, "Expected no changes for clean merge case 2"

    def test_obr_secretaries_changes_detected_case_1(self):
        """Test case 3: Actual code changes detected in obr-secretaries (should return True)"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obr-secretaries'}):
            result = check_for_code_changes(
                "master",
                "2545fae103dc7a0eb8e47b9dc084a18ce0809325",
                "989ce3d547671cdace7039eec40a1787d8d377cc",
            )
            assert result is True, "Expected changes detected for case 1"

    def test_obr_secretaries_changes_detected_case_2(self):
        """Test case 4: Actual code changes detected in obr-secretaries (should return True)"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obr-secretaries'}):
            result = check_for_code_changes(
                "master",
                "1e2e30388f38beae4fc3596bbe4d5790b9bf2049",
                "f1c0e1ba95267ce30ceb49024437c1ca948fb2ba",
            )
            assert result is True, "Expected changes detected for case 2"

    def test_obr_secretaries_no_changes_transitive_merge(self):
        """Test case 5: Transitive clean merge with no logical changes in obr-secretaries (should return False)"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obr-secretaries'}):
            result = check_for_code_changes(
                "master",
                "1ebadaa10c37f9e59dca4b10196367b61509f66a",
                "07c07c68775da288d6c243161142e3e5de39e54d",
            )
            assert result is False, "Expected no changes for transitive clean merge"

    def test_obrv2_real_commits(self):
        """Test with real commits from agilentia/obrv2 repository"""
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'agilentia/obrv2'}):
            result = check_for_code_changes(
                "main", 
                "ab3b2880f21c5c3713031ea8b8bd987f2434f84d",
                "fa049d58ec4fa545facafebabe9b95c25d238ebe"
            )

            assert result is False, "No changes, TODO: verify?"


class TestCodeChangeDetectionWithMocks:
    """Test cases using mocks to isolate the logic from GitHub API calls"""

    @patch('dismiss_approvals.get_diff_content')
    @patch('dismiss_approvals.get_commit_parents')
    def test_identical_commits(self, mock_get_parents, mock_get_diff):
        """Test when before and after commits are identical"""
        # Mock empty diff (identical commits)
        mock_get_diff.return_value = ""
        
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'test-owner/test-repo'}):
            result = check_for_code_changes("main", "abc123", "abc123")
        
        assert result is False, "Identical commits should return False"
        mock_get_diff.assert_called_once()

    @patch('dismiss_approvals.get_diff_content')
    @patch('dismiss_approvals.get_commit_parents')
    @patch('dismiss_approvals.get_effective_commit_content')
    def test_same_effective_content(self, mock_effective_content, mock_get_parents, mock_get_diff):
        """Test when commits have different SHAs but same effective content"""
        # Mock non-empty direct diff but same effective content
        mock_get_diff.return_value = "some diff content"
        mock_get_parents.side_effect = [["parent1"], ["parent2"]]  # Non-merge commits
        mock_effective_content.side_effect = ["same content", "same content"]
        
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'test-owner/test-repo'}):
            result = check_for_code_changes("main", "before_sha", "after_sha")
        
        assert result is False, "Same effective content should return False"

    @patch('dismiss_approvals.get_diff_content')
    def test_api_failure_fallback(self, mock_get_diff):
        """Test fallback behavior when GitHub API calls fail"""
        # Mock API failure
        mock_get_diff.return_value = None
        
        with patch.dict(os.environ, {'INPUT_REPOSITORY': 'test-owner/test-repo'}):
            result = check_for_code_changes("main", "before_sha", "after_sha")
        
        assert result is True, "API failure should default to True (assume changes)"

    def test_missing_repository_env(self):
        """Test behavior when INPUT_REPOSITORY environment variable is missing"""
        # Ensure INPUT_REPOSITORY is not set
        with patch.dict(os.environ, {}, clear=True):
            result = check_for_code_changes("main", "before_sha", "after_sha")
        
        assert result is True, "Missing repository should default to True (assume changes)"



if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v"])
