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

# ── Smart Bootstrap for Local Dev ──
# Automatically resolve PYTHONPATH and config path when running manually from root
ROOT = Path(__file__).resolve().parent.parent.parent
if (ROOT / "agent" / "src").exists():
    sys.path.insert(0, str(ROOT / "agent" / "src"))
    sys.path.insert(0, str(ROOT))
if "CLAW_CONFIG_PATH" not in os.environ and (ROOT / "config.yaml").exists():
    os.environ["CLAW_CONFIG_PATH"] = str(ROOT / "config.yaml")

import docker
import json
import time
import config


def _retry_subprocess(cmd: list, max_retries: int = 3, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess command with retries for transient failures."""
    import time
    for attempt in range(max_retries):
        result = subprocess.run(cmd, **kwargs)
        if result.returncode == 0:
            return result
        print(f"⚠️ Attempt {attempt+1}/{max_retries} failed: {result.stderr[:200].strip()}")
        time.sleep(2)
    return result


def setup_workspace(instance: dict, workspace_dir: str = "./workspace",
                    install_deps: bool = False) -> Path:
    """Prepare the workspace. Routes to local or sidecar setup based on config."""
    mode = config.CONFIG.execution_mode

    if mode == "native":
        return setup_local_workspace(instance, workspace_dir, install_deps)
    else:
        # Crucial Fix: We MUST seed the host workspace even if we use a sidecar.
        # Otherwise, the sidecar's bind mount maps an empty host directory
        # over the sidecar's internal codebase, leaving the Agent blind.
        setup_local_workspace(instance, workspace_dir, install_deps=False)
        
        sidecar_id = setup_sidecar(instance, workspace_dir)
        print(f"🚀 Sidecar ready: {sidecar_id}")
        
        # Create /testbed → /workspace symlink inside the sidecar.
        # Many SWE-bench scripts have /testbed hardcoded. The symlink
        # redirects their I/O to the shared volume at /workspace.
        # We must also remove any existing /testbed (from the image layer)
        # before creating the symlink.
        client = docker.from_env()
        target = config.CONFIG.sidecar_config.default_target_id
        try:
            # Remove the baked-in /testbed directory (image layer)
            exec_id = client.api.exec_create(
                container=target,
                cmd=["sh", "-c", "rm -rf /testbed && ln -s /workspace /testbed"],
            )
            client.api.exec_start(exec_id=exec_id['Id'])
            print(f"🔗 Created /testbed → /workspace symlink in sidecar")
        except Exception as e:
            print(f"⚠️  Symlink creation failed (non-fatal): {e}")

        return Path(workspace_dir)

def setup_sidecar(instance: dict, workspace_dir: str = "./workspace") -> str:
    """Provisions a Docker sidecar container for the SWE-bench instance.

    Bind-mounts the host workspace into /workspace so that the agent's
    exec_create(workdir="/workspace") can find the shared volume.
    """
    client = docker.from_env()
    repo = instance["repo"].replace("/", "__")
    instance_id = instance["instance_id"]
    
    # Map to standard SWE-bench image naming pattern (Epoch Research GHCR)
    # Example: ghcr.io/epoch-research/swe-bench.eval.x86_64.astropy__astropy-12907
    image_name = f"ghcr.io/epoch-research/swe-bench.eval.x86_64.{instance_id}"
    
    container_name = config.CONFIG.sidecar_config.default_target_id
    network_name = config.CONFIG.sidecar_config.network

    # Resolve absolute host path for the bind mount
    host_workspace = str(Path(workspace_dir).resolve())

    print(f"🐳 Provisioning sidecar: {container_name} (Image: {image_name})")
    print(f"   Host workspace: {host_workspace} → /workspace")

    # 0. Ensure network exists
    _ensure_network(client, network_name)

    # 1. Clean existing container
    try:
        old = client.containers.get(container_name)
        print(f"🧹 Removing old sidecar: {container_name}")
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    # 2. Pull image with retries
    print(f"📥 Pulling image {image_name}...")
    max_pull_retries = 3
    for attempt in range(max_pull_retries):
        try:
            client.images.pull(image_name)
            print(f"✅ Successfully pulled {image_name}")
            break
        except Exception as e:
            if attempt < max_pull_retries - 1:
                print(f"⚠️ Pull attempt {attempt+1} failed: {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                print(f"❌ Pull failed after {max_pull_retries} attempts: {e}. Attempting to run from local cache...")

    # 3. Start container WITH the shared workspace bind mount.
    # This is the critical fix: without this mount, /workspace doesn't exist
    # inside the sidecar, causing OCI runtime errors when sandbox.py tries
    # to exec with workdir=/workspace.
    container = client.containers.run(
        image=image_name,
        name=container_name,
        detach=True,
        network=network_name,
        restart_policy={"Name": "always"},
        command="sleep infinity",
        volumes={host_workspace: {"bind": "/workspace", "mode": "rw"}},
    )

    return container.id

def _ensure_network(client: docker.DockerClient, network_name: str):
    """Checks if the specified network exists, creates it if not."""
    if network_name == "bridge":
        return
    try:
        client.networks.get(network_name)
    except docker.errors.NotFound:
        print(f"🌐 Creating network: {network_name}")
        client.networks.create(network_name, driver="bridge")

def setup_local_workspace(instance: dict, workspace_dir: str = "./workspace",
                          install_deps: bool = False) -> Path:
    """Existing local setup logic (renamed)."""
    workspace = Path(workspace_dir).resolve()
    repo = instance["repo"]  # e.g. "django/django"
    base_commit = instance.get("base_commit", "")

    print(f"🔧 Setting up local workspace for {repo} @ {base_commit[:12]}")

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
        _retry_subprocess(["git", "init"], cwd=str(workspace), capture_output=True, timeout=10)
        _retry_subprocess(["git", "remote", "add", "origin", repo_url], cwd=str(workspace), capture_output=True, timeout=10)

        print(f"🔀 Fetching commit {base_commit[:12]}...")
        result = _retry_subprocess(
            ["git", "fetch", "--depth", "1", "origin", base_commit],
            capture_output=True, text=True, cwd=str(workspace), timeout=600, max_retries=3
        )

        if result.returncode == 0:
            # Fast path: direct commit fetch worked
            _retry_subprocess(["git", "checkout", "FETCH_HEAD"],
                            capture_output=True, cwd=str(workspace), timeout=120)
            print(f"✅ Checked out {base_commit[:12]} (targeted fetch)")
        else:
            # Fallback: some servers don't allow fetching arbitrary SHAs.
            # Do a blobless clone instead to save massive amounts of network traffic.
            print(f"⚠️  Targeted fetch failed, falling back to full clone...")
            shutil.rmtree(workspace, ignore_errors=True)
            result = _retry_subprocess(
                ["git", "clone", "--filter=blob:none", repo_url, str(workspace)],
                capture_output=True, text=True, timeout=900, max_retries=3
            )
            if result.returncode != 0:
                print(f"❌ Clone failed: {result.stderr}")
                sys.exit(1)

            result = _retry_subprocess(
                ["git", "checkout", base_commit],
                capture_output=True, text=True, cwd=str(workspace), timeout=120,
            )
            if result.returncode != 0:
                print(f"❌ Checkout failed: {result.stderr}")
                sys.exit(1)
            print(f"✅ Checked out {base_commit[:12]} (blobless clone)")
    else:
        # No specific commit — just clone HEAD
        result = _retry_subprocess(
            ["git", "clone", "--depth", "1", "--filter=blob:none", repo_url, str(workspace)],
            capture_output=True, text=True, timeout=300, max_retries=3
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
    """Extract the COMPLETE git diff from the workspace (all changes vs HEAD).

    Stages ALL changes first (tracked modifications + new files + deletions)
    to ensure untracked files created by the agent are captured in the diff.

    Returns the unified diff as a string.
    """
    workspace = Path(workspace_dir).resolve()

    # Stage everything — this is what makes new files visible to diff
    subprocess.run(
        ["git", "add", "-A"],
        capture_output=True, cwd=str(workspace), timeout=60,
    )

    # Diff staged changes against HEAD
    result = subprocess.run(
        ["git", "diff", "--cached", "HEAD"],
        capture_output=True, text=True, cwd=str(workspace), timeout=60,
    )
    patch = result.stdout

    if patch:
        line_count = len(patch.splitlines())
        print(f"📝 Extracted patch: {line_count} lines from {workspace}")
    else:
        print(f"⚠️  No changes detected in {workspace}")

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
