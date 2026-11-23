"""Thresholds for the standalone manual-profile generator.

This package must not import STP formline smoothing. Physical diameter
limits are duplicated here so the generator does not depend on QA smoothing
config.
"""
from __future__ import annotations

MIN_CONTROL_POINTS = 2
MIN_DIAMETER_MM = 1.0
MAX_DIAMETER_MM = 2000.0
# Consecutive control y within this band belong to the same straight size.
CONSTANT_Y_TOLERANCE_MM = 0.05
DUPLICATE_X_EPS_MM = 1e-6
SAMPLE_STEP_MM = 1.0
MONOTONE_EPS_MM = 1e-9
SOURCE_NAME = "manual_xlsx"
