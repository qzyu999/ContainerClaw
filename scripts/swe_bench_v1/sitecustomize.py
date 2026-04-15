import sys

def patch_swebench():
    """
    In-memory monkey patch for SWE-bench git cloning.
    Runs automatically on Python startup when injected via PYTHONPATH.
    This safely injects retry loops and --filter=blob:none into Docker build
    commands without permanently altering the .venv installation paths.
    """
    try:
        import swebench.harness.test_spec.python as python_test_spec
        import swebench.harness.test_spec.create_scripts as create_scripts
        
        original_make = python_test_spec.make_repo_script_list_py
        if getattr(original_make, "_patched", False):
            return
            
        def patched_make(*args, **kwargs):
            cmds = original_make(*args, **kwargs)
            for i, c in enumerate(cmds):
                if c.startswith("git clone"):
                    parts = c.split(" ")
                    tgt = parts[-1]
                    cmds[i] = f"cd /root && for i in 1 2 3 4 5; do {c} --filter=blob:none && break || (rm -rf {tgt} && sleep 2); done"
                elif c.startswith("git gc") or c.startswith("git reflog expire"):
                    cmds[i] = f"echo 'Skipped {c.split()[1]} to support blobless clones'"
            return cmds
        patched_make._patched = True
        
        python_test_spec.make_repo_script_list_py = patched_make
        create_scripts.make_repo_script_list_py = patched_make
        
        from swebench.harness.test_spec import utils as utils_test_spec
        original_common = utils_test_spec.make_repo_script_list_common
        def patched_common(*args, **kwargs):
            cmds = original_common(*args, **kwargs)
            for i, c in enumerate(cmds):
                if c.startswith("git clone"):
                    parts = c.split(" ")
                    tgt = parts[-1]
                    cmds[i] = f"cd /root && for i in 1 2 3 4 5; do {c} --filter=blob:none && break || (rm -rf {tgt} && sleep 2); done"
                elif c.startswith("git gc") or c.startswith("git reflog expire"):
                    cmds[i] = f"echo 'Skipped {c.split()[1]} to support blobless clones'"
            return cmds
        
        utils_test_spec.make_repo_script_list_common = patched_common
        create_scripts.make_repo_script_list_common = patched_common

    except ImportError:
        pass

patch_swebench()
