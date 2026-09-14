#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024–2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: BSD-2-Clause
# Start the CUDA MPS control daemon so vLLM and Riva can share the Jetson GPU
# with a deterministic SM split instead of time-slicing.
#
# Usage:
#     bash scripts/start-mps.sh
#
# Both vLLM (nvidia-llm-vllm) and Riva (nemotron-speech) are MPS clients:
# each reads CUDA_MPS_ACTIVE_THREAD_PERCENTAGE from its docker-compose env
# (VLLM_MPS_THREAD_PCT / RIVA_MPS_THREAD_PCT in `.env`). The production
# split is symmetric 50/50.
#
# After this runs:
#     docker compose --profile generic-assistant/jetson-thor up -d
#
# Idempotent — safe to run multiple times.

set -euo pipefail

PIPE_DIR="${CUDA_MPS_PIPE_DIRECTORY:-/tmp/nvidia-mps}"
LOG_DIR="${CUDA_MPS_LOG_DIRECTORY:-/tmp/nvidia-log}"

if ! command -v nvidia-cuda-mps-control >/dev/null 2>&1; then
    echo "ERROR: nvidia-cuda-mps-control not found on PATH." >&2
    echo "       MPS is not available on this host; aborting." >&2
    exit 1
fi

# The docker containers (vLLM / Riva) run as root. MPS enforces UID match
# between server and client, so the daemon must also be root. Re-exec under
# sudo if needed.
if [[ "$(id -u)" -ne 0 ]]; then
    echo "MPS daemon must run as root for containers (which are UID 0) to connect."
    echo "Re-executing under sudo..."
    exec sudo -E bash "$0" "$@"
fi

# If stale dirs exist from a previous non-root run, take ownership.
if [[ -e "$PIPE_DIR" ]] && [[ "$(stat -c %u "$PIPE_DIR")" -ne 0 ]]; then
    echo "Found stale $PIPE_DIR owned by UID $(stat -c %u "$PIPE_DIR"); re-claiming as root."
    chown -R 0:0 "$PIPE_DIR" "$LOG_DIR" 2>/dev/null || true
fi

mkdir -p "$PIPE_DIR" "$LOG_DIR"
chmod 755 "$PIPE_DIR" "$LOG_DIR"

export CUDA_MPS_PIPE_DIRECTORY="$PIPE_DIR"
export CUDA_MPS_LOG_DIRECTORY="$LOG_DIR"

if [[ -S "$PIPE_DIR/control" ]] && pgrep -x nvidia-cuda-mps-control >/dev/null; then
    echo "MPS daemon already running (control socket: $PIPE_DIR/control)."
else
    echo "Starting MPS daemon..."
    nvidia-cuda-mps-control -d
    sleep 1
fi

DEFAULT_PCT="$(echo get_default_active_thread_percentage \
    | nvidia-cuda-mps-control 2>/dev/null | tr -d '[:space:]' || true)"
echo "MPS ready."
echo "  pipe dir : $PIPE_DIR"
echo "  log dir  : $LOG_DIR"
echo "  default active thread %: ${DEFAULT_PCT:-unknown}"
echo ""
echo "Next steps:"
echo "  1. Start (or restart) the stack:"
echo "       docker compose --profile generic-assistant/jetson-thor up -d"
echo "  2. Verify both containers are attached as MPS clients:"
echo "       echo get_device_client_list | nvidia-cuda-mps-control"
echo ""
echo "To roll back: bash scripts/stop-mps.sh"
