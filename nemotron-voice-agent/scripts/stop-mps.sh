#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024–2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: BSD-2-Clause
# Gracefully stop the CUDA MPS control daemon.
#
# Usage:
#     bash scripts/stop-mps.sh
#
# Run this AFTER stopping the MPS clients (vLLM container and Riva container).
# Once the daemon is down, all CUDA processes fall back to the default
# time-sliced GPU scheduler — i.e. pre-MPS behavior.

set -euo pipefail

PIPE_DIR="${CUDA_MPS_PIPE_DIRECTORY:-/tmp/nvidia-mps}"
LOG_DIR="${CUDA_MPS_LOG_DIRECTORY:-/tmp/nvidia-log}"

export CUDA_MPS_PIPE_DIRECTORY="$PIPE_DIR"
export CUDA_MPS_LOG_DIRECTORY="$LOG_DIR"

if ! command -v nvidia-cuda-mps-control >/dev/null 2>&1; then
    echo "nvidia-cuda-mps-control not found; nothing to stop."
    exit 0
fi

if [[ ! -S "$PIPE_DIR/control" ]]; then
    echo "No MPS control socket at $PIPE_DIR/control; daemon not running."
    exit 0
fi

# The daemon runs as root (see start-mps.sh); stopping it requires root too.
if [[ "$(id -u)" -ne 0 ]]; then
    exec sudo -E bash "$0" "$@"
fi

echo "Stopping MPS daemon..."
echo quit | nvidia-cuda-mps-control || true

for _ in $(seq 1 10); do
    pgrep -x nvidia-cuda-mps-control >/dev/null || break
    sleep 0.5
done

if pgrep -x nvidia-cuda-mps-control >/dev/null; then
    echo "WARN: MPS daemon did not exit cleanly; sending SIGTERM." >&2
    pkill -TERM -x nvidia-cuda-mps-control || true
    for _ in $(seq 1 10); do
        pgrep -x nvidia-cuda-mps-control >/dev/null || break
        sleep 0.5
    done
fi

if pgrep -x nvidia-cuda-mps-control >/dev/null; then
    echo "ERROR: MPS daemon still running after SIGTERM; sending SIGKILL." >&2
    pkill -KILL -x nvidia-cuda-mps-control || true
    sleep 1
fi

if pgrep -x nvidia-cuda-mps-control >/dev/null; then
    echo "FATAL: Failed to stop MPS daemon." >&2
    exit 1
fi

rm -f "$PIPE_DIR/control" 2>/dev/null || true
echo "MPS daemon stopped."
