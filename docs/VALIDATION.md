# Validation Report

Generated for the Live2Shop v1.0 source delivery.

## Checks completed in the artifact-generation environment

The final source tree was checked after all patches:

```text
Python AST parse:       PASS
Go parse/gofmt:         PASS
YAML parse:             PASS
JSON parse:             PASS
XML/POM parse:          PASS
Bash -n:                PASS
Cross-file invariants:  PASS
Unit/contract tests:    7 passed
```

Unit-test coverage in this compact source package includes:

- verifier reward prefers correct CommerceEvent over corrupt output;
- invalid JSON is penalized;
- SessionRecModel tensor/output shapes and normalized user embedding;
- required protobuf contracts exist;
- E-VAds test split converter guard is present;
- embedding semantic version is part of the contract;
- resumed CommerceVLM stages explicitly reactivate loaded LoRA parameters.

## What could not be executed inside this sandbox

This artifact environment does not expose:

```text
Docker daemon
Maven
protoc
full Transformers model runtime
NVIDIA 3090 GPUs
external dataset/model network downloads
```

Therefore the following were **not falsely claimed as executed here**:

- Docker image builds;
- Java/Flink Maven compilation against downloaded dependencies;
- protobuf code generation with real `protoc`;
- RTMP end-to-end live stream test;
- public dataset downloads;
- Qwen/SigLIP/teacher checkpoint downloads;
- CommerceVLM/RecModel GPU training;
- measured model/business metrics.

Those steps are reproducibly specified in:

```text
docs/RUNBOOK.md
docs/DATASETS.md
docs/TRAINING_GUIDE.md
```

Start on the target machine with:

```bash
bash scripts/preflight.sh
```

Then follow the training guide before bringing up the final `trained` stack.

## Important scientific/engineering boundary

The repository includes source code, data pipelines, training recipes, tests, and
deployment infrastructure. It intentionally does not bundle third-party datasets,
third-party model weights, or fabricated trained checkpoints/results.

Fill `docs/RESULTS_TEMPLATE.md` only with metrics from your actual runs.
