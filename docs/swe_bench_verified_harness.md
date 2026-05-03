# SWE-bench Verified: Sampling & Evaluation Guide

This guide explains how to create a stratified sample from the SWE-bench Verified dataset and run evaluations on specific subsets using ContainerClaw.

## 1. Creating a Stratified Sample

To ensure a balanced evaluation across different repositories, use the `stratified_swebench_verified_sample.py` script. This script samples up to `N` instances per repository.

### Prerequisites
The script expects a JSONL version of the dataset. If you have already run `run.py`, the dataset is likely cached in `scripts/swe_bench/.cache/`.

### Steps:
1. **Convert Cached JSON to JSONL**:
   ```bash
   cd scripts/swe_bench
   python3 -c "import json; items=json.load(open('.cache/princeton-nlp_SWE-bench_Verified_test.json')); [print(json.dumps(item)) for item in items]" > swebench_verified.jsonl
   ```

2. **Run the Sampling Script**:
   ```bash
   python3 stratified_swebench_verified_sample.py \
     --dataset-path swebench_verified.jsonl \
     --per-repo 3 \
     --seed 42 \
     --output notebooks/swebench_verified_stratified_sample.jsonl
   ```
   *   `--per-repo`: Max instances per repository (default: 3).
   *   `--seed`: Random seed for reproducibility (default: 42).

---

## 2. Running on a Specific Subset

Once you have a sampled JSONL file, you can instruct `run.py` to use it as the dataset source by passing the file path to the `--dataset` argument.

### Example: Running the Stratified Sample
```bash
uv run python run.py \
  --batch \
  --dataset notebooks/swebench_verified_stratified_sample.jsonl \
  --model-name containerclaw-v1 \
  --timeout 7200 \
  --run-id smoke_test_stratified \
  --skip-docker \
  --keep-alive
```

### Key Arguments for Custom Subsets:
- `--batch`: Required to process multiple instances from the file.
- `--dataset`: Path to your local `.jsonl` or `.json` file.
- `--limit`: (Optional) Further limit the number of instances from the subset.
- `--run-id`: Unique identifier for the run (results will be in `runs/<run_id>/`).

---

## 3. Official Evaluation (Phase 2)

After Phase 1 (generating predictions) is complete, run the official evaluation:

```bash
python evaluate.py \
  --predictions runs/smoke_test_stratified/predictions.jsonl \
  --run-id smoke_test_stratified
```

For more details on the evaluation methodology and the gap analysis between the host-run and Docker-run evaluations, see [SWE-bench Verified: Comprehensive E2E Benchmarking Audit](docs/swe_bench_pt2.md).
