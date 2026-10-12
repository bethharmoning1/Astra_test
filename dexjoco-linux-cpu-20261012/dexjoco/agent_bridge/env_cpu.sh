#!/usr/bin/env bash
# Source before this project's Python; no system/profile changes.
export MUJOCO_GL=osmesa
export PYOPENGL_PLATFORM=osmesa
export LIBGL_ALWAYS_SOFTWARE=1
export JAX_PLATFORMS=cpu
export CUDA_VISIBLE_DEVICES=""
unset MUJOCO_EGL_DEVICE_ID
