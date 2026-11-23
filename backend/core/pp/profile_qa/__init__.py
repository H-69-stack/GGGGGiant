"""STP formline QA package (monotonic check, smoothing, straight detection, XLSX)."""
from .config import ALGORITHM_VERSION, QA_QUANTITY
from .manual_profile_parser import build_xlsx_template_bytes, parse_manual_xlsx
from .monotonic_validator import classify_local_vs_manual, validate_monotonic_profile
from .pipeline import (
    apply_manual_xlsx,
    backfill_section_dims,
    choose_profile,
    confirm_smooth,
    confirm_straight,
    confirm_wall_and_validate_length,
    delete_key_section_record,
    finalize_straight,
    get_final_profile,
    initialize_qa_from_records,
    preview_smooth,
    qa_summary,
    reject_smooth,
    reject_straight,
    rerun_qa,
    save_key_section_record,
    search_key_sections_in_bundle,
    working_to_wall_thickness_records,
)
from .profile_validation import validate_profile_input
from .state import QaStateError
from .straight_segment_detector import detect_straight_segments
from .wall_thickness_confirm import calculate_drawing_length, validate_wall_thickness

__all__ = [
    "ALGORITHM_VERSION",
    "QA_QUANTITY",
    "QaStateError",
    "apply_manual_xlsx",
    "calculate_drawing_length",
    "choose_profile",
    "build_xlsx_template_bytes",
    "confirm_smooth",
    "confirm_straight",
    "confirm_wall_and_validate_length",
    "delete_key_section_record",
    "detect_straight_segments",
    "finalize_straight",
    "get_final_profile",
    "initialize_qa_from_records",
    "parse_manual_xlsx",
    "preview_smooth",
    "qa_summary",
    "reject_smooth",
    "reject_straight",
    "rerun_qa",
    "backfill_section_dims",
    "save_key_section_record",
    "search_key_sections_in_bundle",
    "validate_monotonic_profile",
    "validate_profile_input",
    "validate_wall_thickness",
    "working_to_wall_thickness_records",
]
