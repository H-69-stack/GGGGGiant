import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.common.params import format_dxf_text, format_length_value


def test_length_rounds_to_integer():
    assert format_length_value(123.4) == '123'
    assert format_length_value('123.5') == '124'
    assert format_length_value(123.49) == '123'
    assert format_length_value('/') == '/'


def test_dxf_length_labels_are_integers_diameters_unchanged():
    assert format_dxf_text('L1', 612.39) == '612'
    assert format_dxf_text('T_L', '150.6') == '151'
    assert format_dxf_text('L_s', 12.2) == '12'
    assert format_dxf_text('%%CD', 38.25) == '38.25'
    assert format_dxf_text('图号', 'DC0128-ADBT0001') == 'DC0128-ADBT0001'
