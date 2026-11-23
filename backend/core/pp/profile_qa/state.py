"""QA status machine.

Illegal transitions raise ``QaStateError`` so API handlers can return 409
instead of silently applying an action in the wrong state.
"""
from __future__ import annotations

from typing import FrozenSet, Mapping

QA_PENDING = "QA_PENDING"
QA_INVALID_INPUT = "QA_INVALID_INPUT"
QA_LOCAL_ANOMALY = "QA_LOCAL_ANOMALY"
QA_ENGINEER_REVIEW_REQUIRED = "QA_ENGINEER_REVIEW_REQUIRED"
QA_SMOOTHING_APPLIED = "QA_SMOOTHING_APPLIED"
QA_RECHECK_FAILED = "QA_RECHECK_FAILED"
QA_MONOTONIC_PASS = "QA_MONOTONIC_PASS"
QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION = "QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION"
QA_MANUAL_REQUIRED = "QA_MANUAL_REQUIRED"
QA_MANUAL_DATA_INVALID = "QA_MANUAL_DATA_INVALID"
QA_MANUAL_DATA_ACCEPTED = "QA_MANUAL_DATA_ACCEPTED"
QA_PASS = "QA_PASS"

ALL_STATES: FrozenSet[str] = frozenset({
    QA_PENDING,
    QA_INVALID_INPUT,
    QA_LOCAL_ANOMALY,
    QA_ENGINEER_REVIEW_REQUIRED,
    QA_SMOOTHING_APPLIED,
    QA_RECHECK_FAILED,
    QA_MONOTONIC_PASS,
    QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
    QA_MANUAL_REQUIRED,
    QA_MANUAL_DATA_INVALID,
    QA_MANUAL_DATA_ACCEPTED,
    QA_PASS,
})

# Review-equivalent local-anomaly states (engineer must decide).
LOCAL_REVIEW_STATES = frozenset({QA_LOCAL_ANOMALY, QA_ENGINEER_REVIEW_REQUIRED})
SMOOTH_REVIEW_STATES = LOCAL_REVIEW_STATES | frozenset({QA_MANUAL_REQUIRED})

# Actions that mutate working / final profiles.
ACTION_PREVIEW_SMOOTH = "preview_smooth"
ACTION_CONFIRM_SMOOTH = "confirm_smooth"
ACTION_REJECT_SMOOTH = "reject_smooth"
ACTION_CONFIRM_STRAIGHT = "confirm_straight"
ACTION_REJECT_STRAIGHT = "reject_straight"
ACTION_FINALIZE_STRAIGHT = "finalize_straight"
ACTION_UPLOAD_XLSX = "upload_xlsx"
ACTION_SELECT_PROFILE = "select_profile"
ACTION_CONFIRM_WALL_LENGTH = "confirm_wall_length"
ACTION_SEARCH_KEY_SECTION = "search_key_section"
ACTION_SAVE_KEY_SECTION = "save_key_section"
ACTION_DELETE_KEY_SECTION = "delete_key_section"
ACTION_RERUN = "rerun"
ACTION_GET_FINAL = "get_final"

_ALLOWED: Mapping[str, FrozenSet[str]] = {
    ACTION_PREVIEW_SMOOTH: SMOOTH_REVIEW_STATES,
    ACTION_CONFIRM_SMOOTH: SMOOTH_REVIEW_STATES | frozenset({
        QA_SMOOTHING_APPLIED, QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION,
        QA_PASS, QA_RECHECK_FAILED,
    }),
    ACTION_REJECT_SMOOTH: SMOOTH_REVIEW_STATES,
    ACTION_CONFIRM_STRAIGHT: frozenset({QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, QA_PASS}),
    ACTION_REJECT_STRAIGHT: frozenset({QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, QA_PASS}),
    ACTION_FINALIZE_STRAIGHT: frozenset({QA_STRAIGHT_SEGMENTS_PENDING_CONFIRMATION, QA_PASS}),
    ACTION_UPLOAD_XLSX: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_SELECT_PROFILE: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_CONFIRM_WALL_LENGTH: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_SEARCH_KEY_SECTION: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_SAVE_KEY_SECTION: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_DELETE_KEY_SECTION: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_RERUN: ALL_STATES - frozenset({QA_PENDING}),
    ACTION_GET_FINAL: frozenset({QA_PASS}),
}


class QaStateError(Exception):
    def __init__(self, code: str, message: str, status: str, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or []

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "details": self.details,
        }


def normalize_status(status: str) -> str:
    if status == QA_ENGINEER_REVIEW_REQUIRED:
        return QA_LOCAL_ANOMALY
    return status


def can_transition(status: str, action: str) -> bool:
    allowed = _ALLOWED.get(action)
    if allowed is None:
        return False
    return status in allowed


def require_action(status: str, action: str) -> None:
    if can_transition(status, action):
        return
    raise QaStateError(
        code="QA_ILLEGAL_STATE",
        message=f"当前状态 {status} 不允许执行 {action}",
        status=status,
        details=[{"action": action, "status": status}],
    )


def is_terminal_pass(status: str) -> bool:
    return status == QA_PASS


def is_blocking_status(status: str) -> bool:
    """True when the profile must not be treated as a final qualified formline."""
    return status != QA_PASS
