"""Deployment management for VISP - version control, repository updates, status checking."""

import subprocess
from pathlib import Path
from typing import Optional

from .git_repo import GitRepository
from .versions import ComponentConfig

# Try to import tabulate for nice table formatting
try:
    from tabulate import tabulate

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

    def tabulate(data, headers=None, tablefmt=None):
        """Simple fallback when tabulate is not available"""
        if not data:
            return ""
        # Simple text output
        if headers == "keys" and data and isinstance(data[0], dict):
            headers = list(data[0].keys())
        result = []
        if headers:
            result.append(" | ".join(str(h) for h in headers))
            result.append("-" * (sum(len(str(h)) for h in headers) + 3 * len(headers)))
        for row in data:
            if isinstance(row, dict):
                result.append(" | ".join(str(row.get(h, "")) for h in headers))
            else:
                result.append(" | ".join(str(v) for v in row))
        return "\n".join(result)


class DeployManager:
    """Manages deployment operations for VISP components."""

    def __init__(self, basedir: Optional[str] = None, runner=None):
        """
        Initialize deploy manager.

        Args:
            basedir: Base directory for deployment (defaults to current directory)
            runner: Runner instance for executing commands (optional)
        """
        self.basedir = Path(basedir) if basedir else Path.cwd()
        self.external_dir = self.basedir / "external"
        self.config = ComponentConfig(str(self.basedir / "versions.json"))
        self.runner = runner

    def _get_image_label(self, image_name: str, label: str) -> Optional[str]:
        """
        Get a label from a container image.

        Args:
            image_name: Full image name (e.g., visp-webclient:latest)
            label: Label name to retrieve (e.g., git.commit)

        Returns:
            Label value or None if not found
        """
        if not self.runner:
            return None

        rc, stdout, _ = self.runner.run_quiet(
            ["podman", "inspect", image_name, "--format", f'{{{{index .Labels "{label}"}}}}']
        )

        if rc == 0 and stdout.strip() and stdout.strip() != "<no value>":
            return stdout.strip()
        return None

    def _check_image_exists(self, image_name: str) -> bool:
        """Check if an image exists locally."""
        if not self.runner:
            return False

        rc, _, _ = self.runner.run_quiet(["podman", "image", "exists", image_name])
        return rc == 0

    @staticmethod
    def _get_node_build_status(component: str, current_commit: str, output_dir: Path) -> dict:
        """Check build status for a Node.js component using its .build-marker file."""
        from .build import NODE_BUILD_MARKER

        marker_path = output_dir / NODE_BUILD_MARKER
        if not output_dir.exists() or not marker_path.exists():
            return {
                "status": "❌ NOT BUILT",
                "image_commit": "N/A",
                "needs_rebuild": True,
                "recommendation": f"Run: ./visp-podman.py build {component}",
            }
        try:
            import json

            marker = json.loads(marker_path.read_text())
        except Exception:  # noqa: BLE001
            return {
                "status": "⚠️ UNKNOWN",
                "image_commit": "N/A (bad marker)",
                "needs_rebuild": None,
                "recommendation": f"Rebuild recommended: ./visp-podman.py build {component}",
            }

        build_commit = marker.get("git_commit", "")
        build_dirty = marker.get("git_dirty", False)
        dirty_suffix = " ⚠️ DIRTY BUILD" if build_dirty else ""

        if build_commit == current_commit:
            return {
                "status": f"✅ UP TO DATE{dirty_suffix}",
                "image_commit": build_commit[:8],
                "needs_rebuild": build_dirty,
                "recommendation": "Build output matches source code"
                if not build_dirty
                else "Built from a dirty tree — rebuild from clean state recommended",
            }
        return {
            "status": f"⚠️ STALE{dirty_suffix}",
            "image_commit": build_commit[:8] if build_commit else "N/A",
            "needs_rebuild": True,
            "recommendation": f"Built from {build_commit[:8]}, source at {current_commit[:8]} - rebuild needed",
        }

    def _get_build_status(self, component: str, current_commit: str) -> dict:
        """
        Check build status for a component.

        Args:
            component: Component name (e.g., webclient, session-manager)
            current_commit: Current git commit of the source

        Returns:
            Dict with build_status, image_commit, and recommendation
        """
        # Components built as Node.js projects (output to disk, no container image).
        # These use a .build-marker file in the output directory.
        node_build_output = {
            "webclient": "external/webclient/dist",
            "container-agent": "external/container-agent/dist",
        }

        marker_dir = node_build_output.get(component)
        if marker_dir:
            return self._get_node_build_status(component, current_commit, self.basedir / marker_dir)

        # Map component names to image names
        component_to_image = {
            "session-manager": "visp-session-manager:latest",
            "wsrng-server": "visp-wsrng-server:latest",
            "emu-webapp-server": "visp-emu-webapp-server:latest",
            "EMU-webApp": "visp-emu-webapp:latest",
            "container-agent": "visp-operations-session:latest",  # bundled in session images
            "WhisperVault": "visp-whisperx:latest",
        }

        # Components that use a named label instead of the default git.commit label
        # (when multiple repos are baked into one image)
        component_label_override: dict[str, str] = {}

        image_name = component_to_image.get(component)
        if not image_name:
            return {
                "status": "N/A",
                "image_commit": "N/A",
                "needs_rebuild": False,
                "recommendation": "Not a buildable image component",
            }

        # Check if image exists
        if not self._check_image_exists(image_name):
            return {
                "status": "❌ NOT BUILT",
                "image_commit": "N/A",
                "needs_rebuild": True,
                "recommendation": f"Run: ./visp-podman.py build {component}",
            }

        # Try to get git commit label from image
        # Some components may use a named label when multiple repos are baked into one image
        commit_label = component_label_override.get(component, "git.commit")
        dirty_label = commit_label.replace("git.commit", "git.dirty")
        image_commit = self._get_image_label(image_name, commit_label)
        image_dirty = self._get_image_label(image_name, dirty_label) == "true"
        dirty_suffix = " ⚠️ DIRTY BUILD" if image_dirty else ""

        if not image_commit:
            return {
                "status": "⚠️ UNKNOWN",
                "image_commit": "N/A (no label)",
                "needs_rebuild": None,  # Can't determine
                "recommendation": "Image exists but has no git commit label (rebuild recommended)",
            }

        # Compare commits
        if image_commit == current_commit:
            return {
                "status": f"✅ UP TO DATE{dirty_suffix}",
                "image_commit": image_commit[:8],
                "needs_rebuild": image_dirty,
                "recommendation": "Image matches source code"
                if not image_dirty
                else "Image built from a dirty tree — rebuild from clean state recommended",
            }
        else:
            return {
                "status": f"⚠️ STALE{dirty_suffix}",
                "image_commit": image_commit[:8],
                "needs_rebuild": True,
                "recommendation": f"Image built from {image_commit[:8]}, source at {current_commit[:8]} - rebuild needed",
            }

    def check_status(self, fetch: bool = True) -> None:
        """
        Check status of all repositories and report uncommitted changes.

        Args:
            fetch: Whether to fetch from remotes before checking status
        """
        print("🔍 Checking repository status...")

        if fetch:
            print("📡 Fetching latest remote information...")

        # 1. Check Deployment Repository (the one we're in)
        print("\n📦 Checking main deployment repository...")
        deployment_repo = GitRepository(str(self.basedir))

        try:
            if fetch:
                deployment_repo.fetch(quiet=True)

            current_branch = deployment_repo.get_current_branch() or "main"
            has_changes = deployment_repo.is_dirty()

            # Calculate ahead/behind using the class method
            # ahead = local commits not on remote (HEAD..origin = behind; origin..HEAD = ahead)
            ahead_count = deployment_repo.count_commits_between(f"origin/{current_branch}", "HEAD")
            behind_count = deployment_repo.count_commits_between("HEAD", f"origin/{current_branch}")

            deployment_repo_status = {
                "Repository": "visible-speech-deployment (THIS REPO)",
                "Branch": current_branch,
                "Has Changes": "⚠️  YES" if has_changes else "✅ NO",
                "Behind Remote": f"⬇️ {behind_count}" if behind_count > 0 else "✅ 0",
                "Ahead Remote": f"🚀 {ahead_count}" if ahead_count > 0 else "✅ 0",
            }

            if behind_count > 0:
                print(f"⚠️  WARNING: Deployment repo is {behind_count} commit(s) " "behind remote!")
                print(f"   Run 'git pull origin {current_branch}' to update " "the deployment scripts")

        except Exception as e:
            deployment_repo_status = {
                "Repository": "visible-speech-deployment (THIS REPO)",
                "Branch": "ERROR",
                "Has Changes": "❌ ERROR",
                "Behind Remote": f"Error: {str(e)}",
                "Ahead Remote": "N/A",
            }

        # 2. Check External Component Repositories
        status_results = []
        repos_with_changes = []
        repos_ahead = []
        repos_behind = []

        for repo_name, comp_data in self.config.get_components():
            repo_path = self.external_dir / repo_name
            repo = GitRepository(str(repo_path))

            # Get version info from config
            version = comp_data.get("version", "latest")
            locked_version = comp_data.get("locked_version", "N/A")
            is_locked = self.config.is_locked(repo_name)

            # Format lock status
            lock_status = "🔒 LOCKED" if is_locked else "🔓 UNLOCKED"
            lock_details = f"at {version[:8]}" if is_locked else "tracking latest"

            # Check if repo exists
            if not repo.exists():
                status_results.append(
                    {
                        "Repository": repo_name,
                        "Lock Status": f"{lock_status} ({lock_details})",
                        "Current Commit": "N/A",
                        "Locked Version": (locked_version[:8] if locked_version != "N/A" else "N/A"),
                        "Status": "❌ MISSING",
                        "Sync Status": "Repository not cloned",
                    }
                )
                continue

            if not repo.is_git_repo():
                status_results.append(
                    {
                        "Repository": repo_name,
                        "Lock Status": f"{lock_status} ({lock_details})",
                        "Current Commit": "N/A",
                        "Locked Version": (locked_version[:8] if locked_version != "N/A" else "N/A"),
                        "Status": "❌ NOT GIT",
                        "Sync Status": "Not a git repository",
                    }
                )
                continue

            try:
                # Use GitRepository methods
                if fetch:
                    try:
                        repo.fetch(quiet=True)
                    except subprocess.CalledProcessError:
                        pass  # Fetch failed, continue with cached data

                # Get current commit using the class
                current_commit = (repo.get_current_commit() or "N/A")[:8]
                current_commit_full = repo.get_current_commit() or "N/A"

                # Check for uncommitted changes using the class
                has_changes = repo.is_dirty()

                # Check build status if runner is available
                build_info = {"status": "N/A", "recommendation": ""}
                if self.runner and current_commit_full != "N/A":
                    build_info = self._get_build_status(repo_name, current_commit_full)

                # Check sync status with remote
                sync_status = "✅ SYNCED"
                sync_details = []

                try:
                    current_branch = repo.get_current_branch() or "main"

                    # Check if remote exists
                    remote_url = repo.get_remote_url()

                    if remote_url:
                        # Check if remote branch exists
                        if repo.has_remote_branch(current_branch):
                            # Calculate ahead/behind using class methods
                            # ahead = local commits not on remote (origin..HEAD = ahead; HEAD..origin = behind)
                            ahead_count = repo.count_commits_between(f"origin/{current_branch}", "HEAD")
                            behind_count = repo.count_commits_between("HEAD", f"origin/{current_branch}")

                            if ahead_count > 0:
                                repos_ahead.append(repo_name)
                                sync_details.append(f"🚀 {ahead_count} ahead")
                                sync_status = "🚀 AHEAD"

                            if behind_count > 0:
                                repos_behind.append(repo_name)
                                sync_details.append(f"⬇️ {behind_count} behind")
                                sync_status = "⬇️ BEHIND" if sync_status == "✅ SYNCED" else "🔄 DIVERGED"
                        else:
                            sync_details.append("Remote branch not found")
                            sync_status = "❓ NO REMOTE BRANCH"
                    else:
                        sync_details.append("No remote configured")
                        sync_status = "🏠 LOCAL ONLY"

                except Exception:
                    sync_details.append("Error checking remote")
                    sync_status = "❌ ERROR"

                # Determine overall status
                if has_changes:
                    repos_with_changes.append(repo_name)
                    overall_status = "⚠️  HAS CHANGES"
                else:
                    overall_status = "✅ CLEAN"

                # Combine sync details
                sync_desc = ", ".join(sync_details) if sync_details else "Up to date"

                status_results.append(
                    {
                        "Repository": repo_name,
                        "Lock Status": f"{lock_status} ({lock_details})",
                        "Current Commit": current_commit,
                        "Locked Version": (locked_version[:8] if locked_version != "N/A" else "N/A"),
                        "Status": overall_status,
                        "Build Status": build_info["status"],
                        "Sync Status": f"{sync_status} - {sync_desc}",
                    }
                )

            except Exception as e:
                status_results.append(
                    {
                        "Repository": repo_name,
                        "Lock Status": f"{lock_status} ({lock_details})",
                        "Current Commit": "ERROR",
                        "Locked Version": (locked_version[:8] if locked_version != "N/A" else "N/A"),
                        "Status": "❌ ERROR",
                        "Sync Status": f"Error: {str(e)}",
                    }
                )

        # Check Node.js build output files exist on disk
        # (these are bind-mounted at runtime and must be present regardless of image labels)
        node_build_warnings = []
        try:
            import importlib.util
            import sys

            # Load NODE_BUILD_CONFIGS from visp-podman.py via importlib
            sys.path.insert(0, str(self.basedir))
            spec = importlib.util.spec_from_file_location("visp_podman", str(self.basedir / "visp-podman.py"))
            if spec and spec.loader:
                vp = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(vp)  # type: ignore[union-attr]
                node_configs = getattr(vp, "NODE_BUILD_CONFIGS", {})
                for build_name, node_cfg in node_configs.items():
                    output_dir = self.basedir / node_cfg["output"]
                    verify = node_cfg.get("verify_file", "")
                    verify_path = output_dir / verify if verify else output_dir
                    if not verify_path.exists():
                        node_build_warnings.append(
                            f"  ❌ {build_name}: build output missing "
                            f"({verify_path.relative_to(self.basedir)})\n"
                            f"     Run: ./visp-podman.py build {build_name}"
                        )
        except Exception:  # noqa: BLE001
            pass  # Non-fatal; skip the check if import fails

        # Print results
        print("\n" + "=" * 100)
        print("REPOSITORY STATUS CHECK")
        print("=" * 100)

        # Show deployment repo status first
        if deployment_repo_status:
            print("\n🔧 DEPLOYMENT REPOSITORY (visible-speech-deployment)")
            print("-" * 100)
            print(tabulate([deployment_repo_status], headers="keys", tablefmt="grid"))
            print()

        print("📚 EXTERNAL COMPONENT REPOSITORIES")
        print("-" * 100)
        print(tabulate(status_results, headers="keys", tablefmt="grid"))

        # Check ALL container images from BUILD_CONFIGS
        # This covers: images with source_repo (apache, operations-session),
        # images with depends_on (rstudio, jupyter), and standalone images (octra).
        # Images whose source is an external repo (session-manager, wsrng-server, etc.)
        # are already tracked in the external repos table above — we skip those here
        # to avoid duplication.
        image_status_rows = []
        external_repo_names = {name for name, _ in self.config.get_components()}
        try:
            import importlib.util
            import sys

            sys.path.insert(0, str(self.basedir))
            spec = importlib.util.spec_from_file_location("visp_podman", str(self.basedir / "visp-podman.py"))
            if spec and spec.loader:
                vp = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(vp)  # type: ignore[union-attr]
                build_configs = getattr(vp, "BUILD_CONFIGS", {})

                for build_name, cfg in build_configs.items():
                    if not self.runner:
                        continue

                    # Skip images that are directly tracked as external repos
                    # (their build context IS the external repo)
                    context = cfg.get("context", "")
                    is_external = any(
                        context.rstrip("/").endswith(f"/{repo_name}") for repo_name in external_repo_names
                    )
                    if is_external and not cfg.get("source_repo") and not cfg.get("depends_on"):
                        continue

                    image_name = f"{cfg['image']}:latest"
                    source_repo = cfg.get("source_repo")
                    depends_on = cfg.get("depends_on")
                    image_exists = self._check_image_exists(image_name)
                    image_ts = self._get_image_label(image_name, "build.timestamp") if image_exists else None
                    built_str = f"Built {image_ts[:19]}" if image_ts else ""

                    # Determine what this image depends on (for display)
                    if source_repo:
                        parent = Path(source_repo).name
                    elif depends_on:
                        parent = depends_on
                    else:
                        parent = "—"

                    if not image_exists:
                        image_status_rows.append(
                            {
                                "Image": build_name,
                                "Tracks": parent,
                                "Status": "❌ NOT BUILT",
                                "Detail": f"Run: ./visp-podman.py build {build_name}",
                            }
                        )
                        continue

                    # Check source_repo match (e.g. apache tracks webclient, operations-session tracks container-agent)
                    if source_repo:
                        source_path = self.basedir / source_repo
                        image_commit = self._get_image_label(image_name, "git.commit")
                        try:
                            repo = GitRepository(str(source_path))
                            source_commit = repo.get_current_commit()
                        except Exception:  # noqa: BLE001
                            source_commit = None

                        # Also check if the deployment repo (Dockerfiles, configs) changed
                        deploy_label = self._get_image_label(image_name, "git.commit.deploy")
                        try:
                            deploy_repo = GitRepository(str(self.basedir))
                            deploy_commit = deploy_repo.get_current_commit()
                        except Exception:  # noqa: BLE001
                            deploy_commit = None

                        source_stale = source_commit and image_commit and image_commit != source_commit
                        deploy_stale = deploy_commit and deploy_label and deploy_label != deploy_commit

                        if not image_commit:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ NO LABEL",
                                    "Detail": f"{built_str} — rebuild recommended" if built_str else "No labels",
                                }
                            )
                        elif source_stale and deploy_stale:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ STALE",
                                    "Detail": f"Source ({image_commit[:8]}→{source_commit[:8]}) "
                                    f"+ Dockerfile ({deploy_label[:8]}→{deploy_commit[:8]})",
                                }
                            )
                        elif source_stale:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ STALE",
                                    "Detail": f"Source: image has {image_commit[:8]}, now at {source_commit[:8]}",
                                }
                            )
                        elif deploy_stale:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ STALE",
                                    "Detail": f"Dockerfile/config changed ({deploy_label[:8]}→{deploy_commit[:8]})",
                                }
                            )
                        else:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "✅ UP TO DATE",
                                    "Detail": built_str or f"Commit {image_commit[:8]}",
                                }
                            )

                    # Check depends_on (e.g. rstudio → operations-session)
                    elif depends_on:
                        parent_cfg = build_configs.get(depends_on, {})
                        parent_image = f"{parent_cfg['image']}:latest"
                        child_ts = image_ts
                        parent_ts = self._get_image_label(parent_image, "build.timestamp")

                        if not child_ts:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ NO TIMESTAMP",
                                    "Detail": "Rebuild recommended",
                                }
                            )
                        elif parent_ts and child_ts < parent_ts:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "⚠️ STALE",
                                    "Detail": f"Built {child_ts[:19]}, parent rebuilt {parent_ts[:19]}",
                                }
                            )
                        else:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": parent,
                                    "Status": "✅ UP TO DATE",
                                    "Detail": built_str,
                                }
                            )

                    # Standalone images (no source_repo, no depends_on) — e.g. octra
                    # These have Dockerfiles in the deployment repo, so git.commit
                    # tracks the deployment repo HEAD at build time.
                    else:
                        image_commit = self._get_image_label(image_name, "git.commit")
                        try:
                            deploy_repo = GitRepository(str(self.basedir))
                            deploy_commit = deploy_repo.get_current_commit()
                        except Exception:  # noqa: BLE001
                            deploy_commit = None

                        if image_commit and deploy_commit and image_commit == deploy_commit:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": "deployment repo",
                                    "Status": "✅ UP TO DATE",
                                    "Detail": built_str or f"Commit {image_commit[:8]}",
                                }
                            )
                        elif image_commit and deploy_commit and image_commit != deploy_commit:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": "deployment repo",
                                    "Status": "⚠️ STALE",
                                    "Detail": f"Image has {image_commit[:8]}, repo at {deploy_commit[:8]}",
                                }
                            )
                        else:
                            image_status_rows.append(
                                {
                                    "Image": build_name,
                                    "Tracks": "deployment repo",
                                    "Status": "✅ BUILT",
                                    "Detail": built_str or "No build label",
                                }
                            )

        except Exception:  # noqa: BLE001
            pass  # Non-fatal

        if image_status_rows:
            print("\n🐳 CONTAINER IMAGES (non-repo builds)")
            print("-" * 100)
            print(tabulate(image_status_rows, headers="keys", tablefmt="grid"))

        # Show third-party (pulled) images — these are pinned in quadlet files
        third_party_rows = []
        try:
            # Find Image= lines in quadlets that reference registries (not localhost/)
            import glob

            mode = "dev"  # TODO: detect current mode
            quadlet_dir = self.basedir / "quadlets" / mode
            for qfile in sorted(glob.glob(str(quadlet_dir / "*.container"))):
                with open(qfile) as f:
                    for line in f:
                        if line.startswith("Image=") and "localhost/" not in line:
                            image_ref = line.strip().split("=", 1)[1]
                            svc_name = Path(qfile).stem
                            # Check if image is pulled locally
                            if self.runner:
                                exists = self._check_image_exists(image_ref)
                                status = "✅ Pulled" if exists else "❌ Not pulled"
                            else:
                                status = "?"
                            third_party_rows.append({"Service": svc_name, "Image": image_ref, "Status": status})
        except Exception:  # noqa: BLE001
            pass

        if third_party_rows:
            print("\n📦 THIRD-PARTY IMAGES (from registries)")
            print("-" * 100)
            print(tabulate(third_party_rows, headers="keys", tablefmt="grid"))

        print("=" * 100)

        # Summary
        summary_lines = []

        # Check build status
        needs_rebuild = [r for r in status_results if r.get("Build Status", "").startswith("⚠️ STALE")]
        not_built = [r for r in status_results if r.get("Build Status", "").startswith("❌ NOT BUILT")]

        if not_built:
            names = [r["Repository"] for r in not_built]
            summary_lines.append(f"❌ Components not built: {', '.join(names)}")
            summary_lines.append("   Run: ./visp-podman.py build all")

        if needs_rebuild:
            names = [r["Repository"] for r in needs_rebuild]
            summary_lines.append(f"⚠️  Components need rebuild (source changed): {', '.join(names)}")
            summary_lines.append("   Run: ./visp-podman.py build <component>")

        if repos_with_changes:
            summary_lines.append(f"⚠️  Repositories with uncommitted changes: {', '.join(repos_with_changes)}")
            summary_lines.append(f"   Total: {len(repos_with_changes)} repo(s) have local changes")

        if repos_ahead:
            summary_lines.append(f"🚀 Repositories ahead of remote: {', '.join(repos_ahead)}")
            summary_lines.append(f"   Total: {len(repos_ahead)} repo(s) need to push")

        if repos_behind:
            summary_lines.append(f"⬇️  Repositories behind remote: {', '.join(repos_behind)}")
            summary_lines.append(f"   Total: {len(repos_behind)} repo(s) need to pull")

        if node_build_warnings:
            summary_lines.append("\n❌ Node.js build outputs missing (bind-mounts will fail):")
            for w in node_build_warnings:
                summary_lines.append(w)

        # Add container image warnings to summary
        stale_images = [d for d in image_status_rows if d["Status"].startswith("⚠️ STALE")]
        not_built_images = [d for d in image_status_rows if d["Status"].startswith("❌ NOT BUILT")]
        if stale_images:
            names = [d["Image"] for d in stale_images]
            summary_lines.append(f"⚠️  Container images need rebuild: {', '.join(names)}")
        if not_built_images:
            names = [d["Image"] for d in not_built_images]
            summary_lines.append(f"❌ Container images not built: {', '.join(names)}")

        if (
            not repos_with_changes
            and not repos_ahead
            and not repos_behind
            and not node_build_warnings
            and not needs_rebuild
            and not not_built
            and not stale_images
            and not not_built_images
        ):
            summary_lines.append("✅ All repositories are clean and synced!")
        else:
            if repos_with_changes or repos_ahead or repos_behind:
                summary_lines.append("   Use 'git status' in each repo for details")

        for line in summary_lines:
            print(line)
        print("=" * 100)

    def lock_components(self, components: list[str], lock_all: bool = False) -> bool:
        """
        Lock components to their current commit versions.

        Args:
            components: List of component names to lock
            lock_all: If True, lock all components

        Returns:
            True if successful, False otherwise
        """
        if lock_all:
            components = [name for name, _ in self.config.get_components()]
        elif not components:
            print("❌ Error: No components specified")
            print("Usage: visp-podman.py deploy lock <component> [<component> ...]")
            print("   or: visp-podman.py deploy lock --all")
            return False

        print(f"🔒 Locking {len(components)} component(s)...\n")

        locked_count = 0
        for component in components:
            comp_data = self.config.get_component(component)
            if not comp_data:
                print(f"⚠️  {component}: Not found in versions.json, skipping")
                continue

            repo_path = self.external_dir / component
            repo = GitRepository(str(repo_path))

            if not repo.exists():
                print(f"⚠️  {component}: Repository not cloned at {repo_path}, skipping")
                continue

            try:
                # Get current commit using GitRepository
                commit_info = repo.get_commit_info("HEAD")
                if not commit_info:
                    print(f"✗ {component}: Failed to get current commit")
                    continue

                # Extract date for display
                commit_date = commit_info["date"][:10] if commit_info["date"] else "N/A"

                # Lock using ComponentConfig method
                self.config.lock(component, commit_info["sha"])

                print(f"✓ {component}: Locked to {commit_info['sha_short']}")
                print(f"  Date: {commit_date}")
                print(f"  Commit: {commit_info['subject'][:60]}")
                print()

                locked_count += 1

            except Exception as e:
                print(f"✗ {component}: Failed to lock - {e}")
                continue

        # Save updated config using ComponentConfig
        if locked_count > 0:
            try:
                self.config.save()
                print(f"\n✅ Successfully locked {locked_count} component(s)")
                print("   Changes saved to versions.json")
                print("   Don't forget to commit versions.json to track these locked versions")
                return True
            except Exception as e:
                print(f"\n❌ Failed to save versions.json: {e}")
                return False
        else:
            print("\n⚠️  No components were locked")
            return False

    def unlock_components(self, components: list[str], unlock_all: bool = False) -> bool:
        """
        Unlock components to track latest.

        Args:
            components: List of component names to unlock
            unlock_all: If True, unlock all components

        Returns:
            True if successful, False otherwise
        """
        if unlock_all:
            components = [name for name, _ in self.config.get_components()]
        elif not components:
            print("❌ Error: No components specified")
            print("Usage: visp-podman.py deploy unlock <component> [<component> ...]")
            print("   or: visp-podman.py deploy unlock --all")
            return False

        print(f"🔓 Unlocking {len(components)} component(s)...\n")

        unlocked_count = 0
        for component in components:
            if not self.config.get_component(component):
                print(f"⚠️  {component}: Not found in versions.json, skipping")
                continue

            if not self.config.is_locked(component):
                print(f"ℹ️  {component}: Already unlocked (tracking latest)")
                continue

            locked_version = self.config.get_locked_version(component)

            # Unlock using ComponentConfig method
            self.config.unlock(component)

            print(f"✓ {component}: Unlocked (now tracking latest)")
            if locked_version and locked_version != "N/A":
                print(f"  Locked version preserved for rollback: {locked_version[:8]}")
            print()

            unlocked_count += 1

        # Save updated config using ComponentConfig
        if unlocked_count > 0:
            try:
                self.config.save()
                print(f"\n✅ Successfully unlocked {unlocked_count} component(s)")
                print("   Changes saved to versions.json")
                print("   Run 'visp-podman.py deploy update' to pull latest changes")
                return True
            except Exception as e:
                print(f"\n❌ Failed to save versions.json: {e}")
                return False
        else:
            print("\n⚠️  No components were unlocked")
            return False

    def rollback_components(self, components: list[str], rollback_all: bool = False) -> bool:
        """
        Rollback components to their locked versions.

        Args:
            components: List of component names to rollback
            rollback_all: If True, rollback all components

        Returns:
            True if successful, False otherwise
        """
        if rollback_all:
            components = [name for name, _ in self.config.get_components()]
        elif not components:
            print("❌ Error: No components specified")
            print("Usage: visp-podman.py deploy rollback <component> [<component> ...]")
            print("   or: visp-podman.py deploy rollback --all")
            return False

        print(f"⏮️  Rolling back {len(components)} component(s)...\n")

        rollback_count = 0
        for component in components:
            if not self.config.get_component(component):
                print(f"⚠️  {component}: Not found in versions.json, skipping")
                continue

            locked_version = self.config.get_locked_version(component)
            if not locked_version or locked_version == "N/A":
                print(f"⚠️  {component}: No locked version available for rollback")
                continue

            # Rollback using ComponentConfig method
            success = self.config.rollback(component)

            if success:
                print(f"✓ {component}: Rolled back to {locked_version[:8]}")
                rollback_count += 1
            else:
                print(f"✗ {component}: Failed to rollback")

        # Save updated config
        if rollback_count > 0:
            try:
                self.config.save()
                print(f"\n✅ Successfully rolled back {rollback_count} component(s)")
                print("   Changes saved to versions.json")
                print("   Run 'visp-podman.py deploy update' to checkout rolled back versions")
                return True
            except Exception as e:
                print(f"\n❌ Failed to save versions.json: {e}")
                return False
        else:
            print("\n⚠️  No components were rolled back")
            return False

    def update_components(self, force: bool = False) -> bool:
        """
        Update external repositories to their configured versions.

        Args:
            force: If True, force update even with uncommitted changes

        Returns:
            True if successful, False otherwise
        """
        print("🔄 Updating external repositories...\n")

        updated_count = 0
        skipped_count = 0
        error_count = 0

        for repo_name, comp_data in self.config.get_components():
            repo_path = self.external_dir / repo_name
            repo = GitRepository(str(repo_path), comp_data.get("url"))

            version = comp_data.get("version", "latest")
            is_locked = self.config.is_locked(repo_name)

            print(f"\n{'='*60}")
            print(f"Updating {repo_name}...")
            print(f"{'='*60}")

            # Clone if doesn't exist
            if not repo.exists():
                if repo.url:
                    print(f"Repository {repo_name} not found, cloning...")
                    try:
                        needs_submodules = comp_data.get("submodules", False)
                        repo.clone(recurse_submodules=needs_submodules)
                        print(f"✅ Cloned {repo_name}")
                    except subprocess.CalledProcessError as e:
                        print(f"❌ Failed to clone {repo_name}: {e}")
                        error_count += 1
                        continue
                else:
                    print(f"⚠️  No URL configured for {repo_name}, skipping")
                    skipped_count += 1
                    continue

            try:
                # Fetch latest
                print("Fetching latest from remote...")
                repo.fetch(quiet=True)

                # Check if locked
                if is_locked:
                    print(f"🔒 {repo_name} is LOCKED to version {version[:8]}")
                    print("   Skipping update (component is locked)")
                    skipped_count += 1
                    continue

                # Check for uncommitted changes
                if repo.is_dirty():
                    if force:
                        print("⚠️  WARNING: Has uncommitted changes, forcing update anyway")
                    else:
                        print("⚠️  Has uncommitted changes, skipping")
                        print("   Use --force to override")
                        skipped_count += 1
                        continue

                # Pull latest
                print("Pulling latest changes...")
                repo.pull()

                # Update submodules if needed
                if comp_data.get("submodules", False):
                    print("Updating submodules...")
                    repo.submodule_update()

                print(f"✅ Updated {repo_name}")
                updated_count += 1

            except Exception as e:
                print(f"❌ Error updating {repo_name}: {e}")
                error_count += 1

        # Summary
        print("\n" + "=" * 60)
        print(f"Update complete: {updated_count} updated, {skipped_count} skipped, {error_count} errors")
        print("=" * 60)

        return error_count == 0
