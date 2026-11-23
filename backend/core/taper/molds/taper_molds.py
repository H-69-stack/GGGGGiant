"""
Taper Module - Mold classes for EC0120/EC0121 machines.
Reconstructed from old program's TaperMold, TaperPositionMold, TaperClampMold, etc.
"""
import os
from datetime import date

try:
    import ezdxf
    import numpy as np
except ImportError:
    ezdxf = None
    np = None

from config import STATIC_DIR
from core.common.params import format_dxf_text, format_length_value

_TAPER_DXF_ROOT = os.path.join(STATIC_DIR, 'standard_dxf', 'taper')


# ─── DXF helpers (shared by all mold classes) ──────────────────────────────

def _find_value_by_insert(texts, insert, value):
    """Find TEXT at (x±5, y±5) and set its text value."""
    if not texts:
        return False
    for te in texts:
        pt = te.dxf.insert
        if abs(pt.x - insert[0]) <= 5 and abs(pt.y - insert[1]) <= 5:
            te.dxf.text = str(value)
            return True
    return False


def _base_modify_dxf(self):
    """Read DXF template and substitute TEXT entity values."""
    if ezdxf is None or not self.change:
        return
    graph_no = self.parameters.get('图号', '')
    if '-' not in graph_no:
        return
    short_code = graph_no.split('-')[1][:-4]
    template_path = os.path.join(_TAPER_DXF_ROOT, self.machine_type, short_code + '.dxf')
    try:
        self._doc = ezdxf.readfile(template_path)
    except IOError:
        self._doc = None
        return
    HEIGHT = 18
    texts = self._doc.modelspace().query('TEXT')
    for te in texts:
        label = te.dxf.text
        if label not in self.parameters:
            continue
        val = format_dxf_text(label, self.parameters[label])
        if label == '图号':
            parts = str(val).split('-')
            val = parts[1] if len(parts) > 1 else parts[0]
            val = (val[:4] + val[-4:]) if len(val) > 8 else val
        _find_value_by_insert(
            texts,
            (te.dxf.insert.x, te.dxf.insert.y + HEIGHT),
            val
        )


def _base_save_dxf(self, output_path, filename):
    """Save the modified DXF."""
    if not self.change or self._doc is None:
        return None
    os.makedirs(output_path, exist_ok=True)
    save_path = os.path.join(output_path, str(filename) + '.dxf')
    self._doc.saveas(save_path)
    return save_path


def _get_taper_params(self, tube_df_params) -> tuple:
    """Extract (BL, D, TL, m_D, B_D) from tube DataFrame.

    DataFrame has columns: 参数, 值
    """
    params = tube_df_params.to_dict(orient='list')
    params_dict = dict(zip(params['参数'], params['值']))

    def f(key, default=0.0):
        try:
            return float(params_dict.get(key, default))
        except (ValueError, TypeError):
            return default

    BL = f('BL')
    D = f('D')
    TL = f('TL')
    m_D = f('m_D')
    B_D = f('B_D')
    return BL, D, TL, m_D, B_D


def _line_data_from_json(raw_data):
    """Convert line_data from JSON (list of [x, y]) into np.array for old code.

    Old code expects a DataFrame-like structure where column 0 = length, column 1 = diameter.
    JSON from API can be [{X, Y}, ...], [[x, y], ...], or [x1, y1, x2, y2, ...].
    """
    if not raw_data:
        return None
    if np is None:
        return raw_data
    first = raw_data[0]
    if isinstance(first, dict):
        arr = np.array([
            [
                item.get('X', item.get('x', item.get(0, 0))),
                item.get('Y', item.get('y', item.get(1, 0))),
            ]
            for item in raw_data
        ], dtype=float)
    elif isinstance(first, (list, tuple)):
        arr = np.array(raw_data, dtype=float)
    else:
        arr = np.array(list(zip(raw_data[::2], raw_data[1::2])), dtype=float)
    return arr


# ─── Mold Classes ──────────────────────────────────────────────────────────

class EC0120_B000:
    """EC0120 Taper模两瓣 - Two-piece Taper Mold.

    Key params:
      %%CD = D
      L = TL + tp_length
      %%CA = D + 10*(D-m_D)/L   (台肩处直径)
      %%CB = m_D                 (小头直径)
      L_m = 400 if TL<=300 else 480
      line1~line15 = diameter values from profile
      L_s = abs(line[0]-line[1])
    """

    def __init__(self, tp_length=0, line_data=None):
        self.English_name = 'TaperMold2'
        self.Chinese_name = 'Taper模两瓣'
        self.parameters = {}
        self.change = False
        self.machine_type = 'EC0120'
        self.tp_length = tp_length
        self._line_data = _line_data_from_json(line_data)
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        L_val = TL + self.tp_length
        self.parameters['L'] = format_length_value(L_val)
        self.parameters['%%CA'] = str(round(D + 10 * (D - m_D) / L_val, 1))
        self.parameters['%%CB'] = str(m_D)
        self.parameters['L_m'] = 400 if TL <= 300 else 480
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def _compute_line_params(self):
        """Compute line1~line15 and L_s from profile data."""
        if np is None or self._line_data is None:
            return
        line_arr = self._line_data
        if line_arr.size == 0:
            return
        target_d = float(self.parameters.get('%%CA', 0))
        left_index = 0
        # Find index where column 1 (diameter) ≈ target_d
        for i in range(len(line_arr)):
            if abs(float(line_arr[i][1]) - target_d) <= 0.1:
                left_index = i
                break
        if left_index == 0 and len(line_arr) > 0:
            diffs = np.abs(line_arr[:, 1].astype(float) - target_d)
            left_index = int(np.argmin(diffs))
        sampled = np.linspace(left_index, len(line_arr) - 1, 15, dtype=int)
        max_x = float(np.max(line_arr[:, 0].astype(float)))
        # line_i = max_x - np_line[sampled[i]][0]
        line_vals = [max_x - float(line_arr[sampled[i]][0]) for i in range(15)]
        self.parameters['L_s'] = format_length_value(abs(line_vals[0] - line_vals[1]))
        for i in range(15):
            self.parameters[f'line{i + 1}'] = str(round(float(line_arr[sampled[i]][1]), 3))

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)
        if self._doc is None:
            return
        self._compute_line_params()
        HEIGHT = 18
        texts = self._doc.modelspace().query('TEXT')
        for te in texts:
            label = te.dxf.text
            if label in self.parameters and (label.startswith('line') or label in ('L_s',)):
                _find_value_by_insert(
                    texts,
                    (te.dxf.insert.x, te.dxf.insert.y + HEIGHT),
                    format_dxf_text(label, self.parameters[label])
                )

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class EC0121_B000:
    """EC0121 Taper模四瓣 - Four-piece Taper Mold."""

    def __init__(self, tp_length=0, line_data=None):
        self.English_name = 'TaperMold4'
        self.Chinese_name = 'Taper模四瓣'
        self.parameters = {}
        self.change = False
        self.machine_type = 'EC0121'
        self.tp_length = tp_length
        self._line_data = _line_data_from_json(line_data)
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        L_val = TL + self.tp_length
        self.parameters['L'] = format_length_value(L_val)
        self.parameters['%%CA'] = str(round(D + 10 * (D - m_D) / L_val, 1))
        self.parameters['%%CB'] = str(m_D)
        self.parameters['L_m'] = 500 if TL <= 400 else 800
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def _compute_line_params(self):
        if np is None or self._line_data is None:
            return
        line_arr = self._line_data
        if line_arr.size == 0:
            return
        target_d = float(self.parameters.get('%%CA', 0))
        left_index = 0
        for i in range(len(line_arr)):
            if abs(float(line_arr[i][1]) - target_d) <= 0.1:
                left_index = i
                break
        if left_index == 0 and len(line_arr) > 0:
            diffs = np.abs(line_arr[:, 1].astype(float) - target_d)
            left_index = int(np.argmin(diffs))
        sampled = np.linspace(left_index, len(line_arr) - 1, 15, dtype=int)
        max_x = float(np.max(line_arr[:, 0].astype(float)))
        line_vals = [max_x - float(line_arr[sampled[i]][0]) for i in range(15)]
        self.parameters['L_s'] = format_length_value(abs(line_vals[0] - line_vals[1]))
        for i in range(15):
            self.parameters[f'line{i + 1}'] = str(round(float(line_arr[sampled[i]][1]), 3))

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)
        if self._doc is None:
            return
        self._compute_line_params()
        HEIGHT = 18
        texts = self._doc.modelspace().query('TEXT')
        for te in texts:
            label = te.dxf.text
            if label in self.parameters and (label.startswith('line') or label in ('L_s',)):
                _find_value_by_insert(
                    texts,
                    (te.dxf.insert.x, te.dxf.insert.y + HEIGHT),
                    format_dxf_text(label, self.parameters[label])
                )

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class C000:
    """定位杆 - Position Rod."""

    def __init__(self):
        self.English_name = 'PositionRod'
        self.Chinese_name = '定位杆'
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        T_max = 0
        try:
            params = tube_df_params.to_dict(orient='list')
            params_dict = dict(zip(params['参数'], params['值']))
            Tx = {k: float(v) for k, v in params_dict.items()
                  if k.startswith('T') and k not in ('TL',)}
            if Tx:
                T_max = max(Tx.values())
        except Exception:
            pass
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['A'] = str(round(D - 2 * T_max - 0.2, 2))
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class J000:
    """夹模 - Clamp Mold."""

    def __init__(self, machine_type='EC0120'):
        self.English_name = 'ClampMold'
        self.Chinese_name = '夹模'
        self.parameters = {}
        self.change = False
        self.machine_type = machine_type
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.parameters['图号'] = external_params['图号']
        self.parameters['A'] = str(D)
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class E000:
    """后定位模 - Back Position Mold."""

    def __init__(self):
        self.English_name = 'BackPositionMold'
        self.Chinese_name = '后定位模'
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['B'] = str(D)
        self.parameters['D'] = str(D)
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class I000:
    """内定位模 - Inner Position Mold."""

    def __init__(self):
        self.English_name = 'InnerPositionMold'
        self.Chinese_name = '内定位模'
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        T_max = 0
        try:
            params = tube_df_params.to_dict(orient='list')
            params_dict = dict(zip(params['参数'], params['值']))
            Tx = {k: float(v) for k, v in params_dict.items()
                  if k.startswith('T') and k not in ('TL',)}
            if Tx:
                T_max = max(Tx.values())
        except Exception:
            pass
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['B'] = str(round(D - 2 * T_max, 2))
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)


class H000:
    """拉杆模 - Pull Rod Mold."""

    def __init__(self):
        self.English_name = 'PullRodMold'
        self.Chinese_name = '拉杆模'
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None

    def set_params(self, tube_df_params, external_params, normal_add=False):
        BL, D, TL, m_D, B_D = _get_taper_params(self, tube_df_params)
        T_max = 0
        try:
            params = tube_df_params.to_dict(orient='list')
            params_dict = dict(zip(params['参数'], params['值']))
            Tx = {k: float(v) for k, v in params_dict.items()
                  if k.startswith('T') and k not in ('TL',)}
            if Tx:
                T_max = max(Tx.values())
        except Exception:
            pass
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['B'] = str(round(D - 2 * T_max - 1, 2))
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True

    def get_params(self):
        import pandas as pd
        return pd.DataFrame({'参数': list(self.parameters.keys()),
                             '值': list(self.parameters.values())})

    def _modify_parameters(self, key, value):
        if key in self.parameters:
            self.parameters[key] = value

    def modify_dxf(self):
        _base_modify_dxf(self)

    def save_dxf(self, output_path, filename):
        _base_save_dxf(self, output_path, filename)
