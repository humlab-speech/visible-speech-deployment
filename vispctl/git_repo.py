"""Git repository operations for VISP deployment."""

import os
import subprocess
from typing import Optional


class GitRepository:
    """
    Encapsulates all Git operations for a repository.
    Centralizes subprocess handling and error management.
    """

    def __init__(self, path: str, url: Optional[str] = None):
        """
        Initialize a Git repository wrapper.

        Args:
            path: Absolute or relative path to the repository
            url: Optional remote URL for the repository
        """
        self.path = os.path.abspath(path) if path else None
        self.url = url

    def exists(self) -> bool:
        """Check if the repository directory exists."""
        return self.path and os.path.exists(self.path)

    def is_git_repo(self) -> bool:
        """Check if the path is a valid git repository."""
        if not self.exists():
            return False
        return os.path.exists(os.path.join(self.path, ".git"))

    def run_git(
        self, args: list[str], check: bool = True, capture_output: bool = True
    ) -> Optional[subprocess.CompletedProcess]:
        """
        Run a git command in the repository.

        Args:
            args: List of git command arguments (without 'git')
            check: Whether to raise exception on non-zero exit
            capture_output: Whether to capture stdout/stderr

        Returns:
            CompletedProcess result if capture_output=True, else None
        """
        cmd = ["git"] + args
        if capture_output:
            result = subprocess.run(cmd, cwd=self.path, capture_output=True, text=True, check=check)
            return result
        else:
            subprocess.run(cmd, cwd=self.path, check=check)
            return None

    def clone(self, destination: Optional[str] = None, recurse_submodules: bool = False) -> None:
        """Clone the repository to destination (or self.path if not specified)."""
        if not self.url:
            raise ValueError("Cannot clone: no URL specified")
        target = destination or self.path
        cmd = ["git", "clone"]
        if recurse_submodules:
            cmd.append("--recurse-submodules")
        cmd += [self.url, target]
        subprocess.run(cmd, check=True)
        if destination:
            self.path = os.path.abspath(destination)

    def submodule_update(self) -> None:
        """Initialise and update all submodules (equivalent to git submodule update --init --recursive)."""
        self.run_git(["submodule", "update", "--init", "--recursive"])

    def fetch(self, quiet: bool = True) -> None:
        """Fetch all remotes."""
        args = ["fetch", "--all"]
        if quiet:
            args.append("--quiet")
        self.run_git(args)

    def checkout(self, ref: str, force: bool = False) -> None:
        """Checkout a specific ref (branch, tag, commit)."""
        args = ["checkout", ref]
        if force:
            args.insert(1, "-f")
        self.run_git(args)

    def pull(self, rebase: bool = False) -> None:
        """Pull from current tracking branch."""
        args = ["pull"]
        if rebase:
            args.append("--rebase")
        self.run_git(args)

    def get_current_commit(self) -> Optional[str]:
        """Get the current commit SHA (full)."""
        try:
            result = self.run_git(["rev-parse", "HEAD"])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_current_branch(self) -> Optional[str]:
        """Get the current branch name."""
        try:
            result = self.run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_commit_info(self, ref: str = "HEAD") -> Optional[dict]:
        """
        Get detailed information about a commit.

        Returns:
            dict with keys: sha, sha_short, date, subject, author
            or None if the ref doesn't exist
        """
        try:
            # Get full SHA
            sha_result = self.run_git(["rev-parse", ref])
            sha = sha_result.stdout.strip()

            # Get short SHA
            sha_short_result = self.run_git(["rev-parse", "--short", ref])
            sha_short = sha_short_result.stdout.strip()

            # Get commit details
            log_result = self.run_git(["log", "-1", "--format=%ci|%s|%an", ref])
            log_parts = log_result.stdout.strip().split("|", 2)

            return {
                "sha": sha,
                "sha_short": sha_short,
                "date": log_parts[0] if len(log_parts) > 0 else "",
                "subject": log_parts[1] if len(log_parts) > 1 else "",
                "author": log_parts[2] if len(log_parts) > 2 else "",
            }
        except subprocess.CalledProcessError:
            return None

    def count_commits_between(self, from_ref: str, to_ref: str) -> int:
        """
        Count commits between two refs.

        Returns:
            int: Number of commits from from_ref to to_ref
            Returns 0 if refs are the same or on error
        """
        try:
            result = self.run_git(["rev-list", "--count", f"{from_ref}..{to_ref}"])
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 0

    def is_dirty(self) -> bool:
        """Check if there are uncommitted changes."""
        try:
            result = self.run_git(["status", "--porcelain"], check=False)
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False

    def get_remote_url(self, remote: str = "origin") -> Optional[str]:
        """Get the URL for a remote."""
        try:
            result = self.run_git(["remote", "get-url", remote])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def has_remote_branch(self, branch: str, remote: str = "origin") -> bool:
        """Check if a remote branch exists."""
        try:
            self.run_git(["rev-parse", f"{remote}/{branch}"])
            return True
        except subprocess.CalledProcessError:
            return False
