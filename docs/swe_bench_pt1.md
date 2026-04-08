# SWE-bench Verified — Benchmarking Guide

> How to benchmark ContainerClaw against SWE-bench Verified using the
> two-phase pipeline. For the architectural deep-dive and gap analysis,
> see [swe_bench_pt2.md](./swe_bench_pt2.md).

---

## System Overview

The pipeline is split into two independent phases connected by a single file — `predictions.jsonl`.

```
Phase 1 (Agent Harness)             Phase 2 (Official Evaluation)
┌──────────────────────┐            ┌──────────────────────────┐
│  run.py              │            │  evaluate.py             │
│                      │            │                          │
│  For each instance:  │            │  Calls the unmodified    │
│   1. Clone repo      │  ──────►  │  swebench.harness        │
│   2. Boot agent      │ predictions│  .run_evaluation()       │
│   3. Extract patch   │   .jsonl   │                          │
│   4. Save prediction │            │  Per-instance Docker     │
│   5. Teardown        │            │  containers with exact   │
│                      │            │  conda envs, test cmds   │
└──────────────────────┘            └──────────────────────────┘
```

**Key invariant**: No custom code touches the grading decision. The only route to a "resolved" verdict is through the official `swebench.harness.run_evaluation`.

### File Layout

```
scripts/swe_bench/
├── run.py                      # Phase 1 — agent harness CLI
├── evaluate.py                 # Phase 2 — official eval wrapper
├── prediction_writer.py        # JSONL checkpoint + combine
├── trace_archiver.py           # Agent conversation archival
├── create_gold_predictions.py  # Sanity test (gold patches)
├── instance_loader.py          # HuggingFace dataset loader
├── workspace_setup.py          # Git clone + patch extraction
└── requirements.txt            # Python dependencies
```

### Output Structure

Each run produces a self-contained directory under `runs/<run_id>/`:

```
runs/containerclaw-v1/
├── manifest.json                # Git SHA, config hash, model, timestamps
├── predictions/                 # Per-instance checkpoints (resumable)
│   ├── django__django-11133.json
│   ├── scikit-learn__scikit-learn-13779.json
│   └── ...
├── predictions.jsonl            # Combined (sent to Phase 2)
└── traces/                      # Agent conversation logs
    ├── django__django-11133/
    │   ├── conversation.json    # Full ConchShell transcript
    │   ├── agent_patch.diff     # The patch the agent produced
    │   └── metadata.json        # Turns, wall clock, agents used
    └── ...
```

---

## Prerequisites

```bash
# 1. Install dependencies (use the project venv)
cd scripts/swe_bench
pip install -r requirements.txt

# 2. Verify Docker can run x86_64 images (Rosetta on Apple Silicon)
docker run --rm --platform linux/amd64 python:3.9-slim python -c "print('ok')"
```

> **Apple Silicon users**: Make sure Rosetta is installed (`softwareupdate --install-rosetta`)
> and Docker Desktop has "Use Rosetta for x86_64/amd64 emulation" enabled in
> Settings → General. Also allocate at least **16 GB RAM** and **120 GB disk** in
> Settings → Resources — the per-instance Docker images are large.

> **No need to pull base images manually.** The official SWE-bench harness builds all
> Docker images locally (base → env → instance) when `--namespace none` is used.
> Our `evaluate.py` defaults to this mode.

---

## Step 0: Validate Your Environment (Gold-Patch Sanity)

Before running ContainerClaw, confirm the evaluation pipeline itself works by testing with known-correct (gold) patches. These should resolve at 100%.

```bash
cd scripts/swe_bench

# Generate gold predictions for 5 instances across diverse repos
python create_gold_predictions.py --sample 5 --output gold_predictions.jsonl

# Run official evaluation — expect 100% resolution
python evaluate.py \
    --predictions gold_predictions.jsonl \
    --run-id gold_sanity \
    --max-workers 1
```

**Expected**: All 5 instances resolve. If any fail, your Docker environment has issues that must be fixed before proceeding.

You can also validate just the predictions format without running Docker:

```bash
python evaluate.py --predictions gold_predictions.jsonl --validate-only
```

---

## Step 1: Single Instance

Run ContainerClaw on one instance to verify the full agent → prediction pipeline works end-to-end.

```bash
# Phase 1: Agent harness
python run.py \
    --instance django__django-11133 \
    --model-name containerclaw-v1 \
    --timeout 600

# Phase 2: Official evaluation
python evaluate.py \
    --predictions runs/<run_id>/predictions.jsonl \
    --run-id <run_id>
```

**What to check**:
- Prediction checkpoint saved under `runs/<run_id>/predictions/`
- Patch is non-empty (agent actually produced changes)
- Trace archived under `runs/<run_id>/traces/`
- Official eval completes without Docker errors

### Useful flags for debugging

```bash
# Skip Docker boot (use already-running ContainerClaw)
python run.py --instance django__django-11133 --skip-docker --model-name test

# Keep Docker alive after run (for inspection)
python run.py --instance django__django-11133 --keep-alive --model-name test

# Skip workspace setup (reuse existing clone)
python run.py --instance django__django-11133 --skip-setup --model-name test
```

---

## Step 2: Small Batch (n=30)

Run a modest batch to validate checkpointing, cleanup, and stability across multiple instances and repos.

```bash
# Phase 1: Agent harness (30 instances, 10 min timeout each)
python run.py \
    --batch \
    --limit 30 \
    --model-name containerclaw-v1 \
    --timeout 600 \
    --run-id smoke_test_30

# Phase 2: Official evaluation
python evaluate.py \
    --predictions runs/smoke_test_30/predictions.jsonl \
    --run-id smoke_test_30
```

### Test checkpointing

Interrupt the batch mid-run (Ctrl+C), then resume:

```bash
# Resume — completed instances are automatically skipped
python run.py \
    --batch \
    --limit 30 \
    --model-name containerclaw-v1 \
    --timeout 600 \
    --run-id smoke_test_30
```

Verify that:
- Previously completed instances show `⏩ Skipping ... (prediction already exists)`
- No duplicate entries in the final `predictions.jsonl`
- Docker is cleaned up even on Ctrl+C (no zombie containers)

### Filter by repo

Test specific repositories to cover diverse environments:

```bash
# Django instances only
python run.py --batch --limit 10 --repo django --model-name containerclaw-v1

# Scikit-learn instances only
python run.py --batch --limit 10 --repo scikit-learn --model-name containerclaw-v1
```

---

## Step 3: Full Run (all 500 instances)

```bash
# Phase 1: Full agent harness
python run.py \
    --batch \
    --model-name containerclaw-v1 \
    --timeout 600 \
    --run-id containerclaw-v1

# Phase 2: Official evaluation (can also auto-trigger)
python run.py \
    --batch \
    --model-name containerclaw-v1 \
    --timeout 600 \
    --run-id containerclaw-v1 \
    --auto-evaluate
```

**Time estimate**: ~150 hours (6+ days) serial. Plan for interruptions — checkpointing handles this.

### Monitor progress

```bash
# Count completed predictions
ls runs/containerclaw-v1/predictions/*.json | wc -l

# Check for errors
grep -l '"error"' runs/containerclaw-v1/predictions/*.json | wc -l

# Combine and validate at any point (partial results are fine)
python prediction_writer.py \
    --combine runs/containerclaw-v1/predictions/ \
    --output runs/containerclaw-v1/predictions.jsonl
```

### After the run

```bash
# Validate final predictions
python evaluate.py \
    --predictions runs/containerclaw-v1/predictions.jsonl \
    --validate-only

# Run official evaluation
python evaluate.py \
    --predictions runs/containerclaw-v1/predictions.jsonl \
    --run-id containerclaw-v1 \
    --max-workers 1

# Results are written to:
#   logs/run_evaluation/containerclaw-v1/     (per-instance logs)
#   containerclaw-v1.containerclaw-v1.json    (official report)
```

---

## Quick Reference

| Task | Command |
|------|---------|
| Validate env | `python create_gold_predictions.py --sample 5 && python evaluate.py --predictions gold_predictions.jsonl --run-id gold` |
| Single instance | `python run.py --instance <id> --model-name <name>` |
| Small batch | `python run.py --batch --limit 30 --model-name <name> --run-id smoke` |
| Full run | `python run.py --batch --model-name <name> --run-id <id> --auto-evaluate` |
| Resume interrupted batch | Re-run the same command (same `--run-id`) |
| Validate predictions | `python evaluate.py --predictions <path> --validate-only` |
| Run evaluation only | `python evaluate.py --predictions <path> --run-id <id>` |
| Combine checkpoints | `python prediction_writer.py --combine <dir> --output predictions.jsonl` |