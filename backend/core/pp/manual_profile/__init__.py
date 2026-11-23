"""Independent manual formline generator (constant plateaus + PCHIP tapers).

This package is intentionally separate from ``core.pp.profile_qa`` smoothing.
"""
from .generator import generate_manual_profile
from .validate import ManualProfileError

__all__ = ["generate_manual_profile", "ManualProfileError"]
