"""
SWE-bench Workspace Setup.

Clones the target repository at the specified base commit into the workspace
directory, preparing it for ContainerClaw agents to work on.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def setup_workspace(instance: dict, workspace_dir: str = "./workspace",
                    install_deps: bool = False) -> Path:
    """Prepare the workspace with the target repo at base_commit.

    Args:
        instance: SWE-bench instance dict (from instance_loader)
        workspace_dir: Path to the workspace directory
        install_deps: Whether to run `pip install -e .` in the workspace

    Returns:
        Path to the workspace directory
    """
    workspace = Path(workspace_dir).resolve()
    repo = instance["repo"]  # e.g. "django/django"
    base_commit = instance.get("base_commit", "")

    print(f"🔧 Setting up workspace for {repo} @ {base_commit[:12]}")

    # Clean existing workspace
    conchshell_backup = None
    if workspace.exists():
        print(f"🧹 Cleaning existing workspace: {workspace}")
        # Backup .conchshell state (will restore after clone)
        conchshell_dir = workspace / ".conchshell"
        if conchshell_dir.exists():
            import tempfile
            conchshell_backup = Path(tempfile.mkdtemp()) / ".conchshell"
            shutil.copytree(conchshell_dir, conchshell_backup)

        shutil.rmtree(workspace)

    # Clone the repo — use a targeted fetch strategy that avoids downloading
    # full history for large repos (e.g. django/django has 500k+ commits).
    repo_url = f"https://github.com/{repo}.git"
    print(f"📥 Cloning {repo_url}...")

    if base_commit:
        # Strategy: init → fetch only the specific commit → checkout
        # This downloads minimal data regardless of repo size.
        workspace.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=str(workspace),
                        capture_output=True, timeout=10)
        subprocess.run(["git", "remote", "add", "origin", repo_url],
                        cwd=str(workspace), capture_output=True, timeout=10)

        print(f"🔀 Fetching commit {base_commit[:12]}...")
        result = subprocess.run(
            ["git", "fetch", "--depth", "1", "origin", base_commit],
            capture_output=True, text=True, cwd=str(workspace), timeout=600,
        )

        if result.returncode == 0:
            # Fast path: direct commit fetch worked
            subprocess.run(["git", "checkout", "FETCH_HEAD"],
                            capture_output=True, cwd=str(workspace), timeout=30)
            print(f"✅ Checked out {base_commit[:12]} (targeted fetch)")
        else:
            # Fallback: some servers don't allow fetching arbitrary SHAs.
            # Do a full clone instead.
            print(f"⚠️  Targeted fetch failed, falling back to full clone...")
            shutil.rmtree(workspace)
            result = subprocess.run(
                ["git", "clone", repo_url, str(workspace)],
                capture_output=True, text=True, timeout=900,
            )
            if result.returncode != 0:
                print(f"❌ Clone failed: {result.stderr}")
                sys.exit(1)

            result = subprocess.run(
                ["git", "checkout", base_commit],
                capture_output=True, text=True, cwd=str(workspace), timeout=30,
            )
            if result.returncode != 0:
                print(f"❌ Checkout failed: {result.stderr}")
                sys.exit(1)
            print(f"✅ Checked out {base_commit[:12]} (full clone)")
    else:
        # No specific commit — just clone HEAD
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(workspace)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            print(f"❌ Clone failed: {result.stderr}")
            sys.exit(1)
        print(f"✅ Cloned HEAD to {workspace}")

    # Restore .conchshell state if it was backed up
    if conchshell_backup and conchshell_backup.exists():
        conchshell_dir = workspace / ".conchshell"
        shutil.copytree(conchshell_backup, conchshell_dir)
        print(f"♻️  Restored .conchshell state")

    # Optional: install dependencies
    if install_deps:
        print("📦 Installing dependencies (pip install -e .)...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", "."],
            capture_output=True, text=True, cwd=str(workspace), timeout=600,
        )
        if result.returncode != 0:
            print(f"⚠️  Dependency install failed (non-fatal): {result.stderr[:500]}")
        else:
            print("✅ Dependencies installed")

    # Show workspace summary
    file_count = sum(1 for _ in workspace.rglob("*") if _.is_file()
                     and ".git" not in str(_))
    print(f"📁 Workspace ready: {file_count} files in {workspace}")
    return workspace


def extract_patch(workspace_dir: str = "./workspace") -> str:
    """Extract the git diff from the workspace (all changes vs HEAD).

    Returns the unified diff as a string.
    """
    workspace = Path(workspace_dir).resolve()
    result = subprocess.run(
        ["git", "diff"],
        capture_output=True, text=True, cwd=str(workspace), timeout=30,
    )
    patch = result.stdout
    if not patch:
        # Also check for untracked files
        result = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True, text=True, cwd=str(workspace), timeout=30,
        )
        patch = result.stdout

    return patch


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SWE-bench Workspace Setup")
    parser.add_argument("--instance", required=True, help="Instance ID")
    parser.add_argument("--workspace", default="./workspace", help="Workspace path")
    parser.add_argument("--install-deps", action="store_true", help="Run pip install")
    parser.add_argument("--dataset", default="princeton-nlp/SWE-bench_Lite")
    args = parser.parse_args()

    from instance_loader import load_instance
    instance = load_instance(args.instance, args.dataset)
    setup_workspace(instance, args.workspace, args.install_deps)
