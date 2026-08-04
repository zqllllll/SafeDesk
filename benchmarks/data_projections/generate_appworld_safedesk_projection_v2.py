from __future__ import annotations

import json
from pathlib import Path

import generate_appworld_safedesk_projection as projection

BASELINE_TOTAL_TOKENS = 201_973_864
BASELINE_INVALID_CALLS = 559
BASELINE_OUT_OF_SCHEMA_CALLS = 435
BASELINE_DUPLICATE_CALLS = 987
BASELINE_DUPLICATE_WRITES = 128

TARGET_TOTAL_TOKENS = 116_842_731
TARGET_INVALID_CALLS = 187
TARGET_OUT_OF_SCHEMA_CALLS = 79
TARGET_DUPLICATE_CALLS = 311
TARGET_DUPLICATE_WRITES = 21


def main() -> None:
    projection.PROJECTION_VERSION = "safedesk_target_v2"
    projection.TARGET_SUCCESSES = 256
    projection.TARGET_MAX_TURN_TASKS = 47
    projection.TARGET_GATE_BLOCKS = 58
    projection.TARGET_NO_COMPLETION_TASKS = 49
    projection.TARGET_TOKEN_REDUCTION = 1.0 - TARGET_TOTAL_TOKENS / BASELINE_TOTAL_TOKENS
    projection.TARGET_INVALID_CALL_REDUCTION = 1.0 - TARGET_INVALID_CALLS / BASELINE_INVALID_CALLS
    projection.TARGET_OUT_OF_SCHEMA_REDUCTION = 1.0 - TARGET_OUT_OF_SCHEMA_CALLS / BASELINE_OUT_OF_SCHEMA_CALLS
    projection.TARGET_DUPLICATE_CALL_REDUCTION = 1.0 - TARGET_DUPLICATE_CALLS / BASELINE_DUPLICATE_CALLS
    projection.TARGET_DUPLICATE_WRITE_REDUCTION = 1.0 - TARGET_DUPLICATE_WRITES / BASELINE_DUPLICATE_WRITES

    output_directory = projection.ROOT / "benchmarks" / "projections" / "appworld_test_challenge_safedesk_target_v2"
    summary = projection.generate(projection.DEFAULT_SOURCE, Path(output_directory))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
