"""
Base Mold Class - shared by all mold types in the Normal module.
Reconstructed from Normal_Base_Mold.py - includes DXF template modification logic.
"""
import os
import pandas as pd

try:
    import ezdxf
except ImportError:
    ezdxf = None

from config import STATIC_DIR
from core.common.params import format_dxf_text

_STANDARD_DXF_ROOT = os.path.join(STATIC_DIR, 'standard_dxf', 'normal')


class BaseMoldClass:
    """Base class for all mold parameter models."""

    def __init__(self):
        self.English_name = ''
        self.Chinese_name = ''
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None
        self._file_name = ''

    def _get_params_from_tube(self, tube_df_params):
        """Extract tube parameters from DataFrame."""
        params = tube_df_params.to_dict(orient='list')
        if 'Parameter' in params:
            params = dict(zip(params['Parameter'], params['Value']))
        elif '参数' in params:
            params = dict(zip(params['参数'], params['值']))

        D = float(params.get('D', 0))
        L = float(params.get('L', 0))

        Tx = {}
        Mx = {}
        Lx = {}
        for item, val in params.items():
            try:
                val = float(val)
            except (ValueError, TypeError):
                continue
            if item.startswith('T') and item != 'T':
                Tx[item] = val
            elif item.startswith('M'):
                Mx[item] = val
            elif item.startswith('L') and item != 'L':
                Lx[item] = val

        return L, D, Lx, Mx, Tx

    def set_params(self, tube_df_params, external_params, normal_add=False):
        """Override in subclass to calculate mold parameters."""
        raise NotImplementedError

    def _modify_parameters(self, key, value):
        """Modify a single parameter."""
        if key in self.parameters:
            self.parameters[key] = value

    def get_params(self):
        """Return parameters as DataFrame."""
        return pd.DataFrame({
            'Parameter': list(self.parameters.keys()),
            'Value': list(self.parameters.values())
        })

    # ─── DXF modification (ported from Normal_Base_Mold.py) ─────────────────

    def _find_value_by_insert(self, texts, insert, value) -> bool:
        """Find TEXT at (x±5, y±5) and set its text value. Returns True if found."""
        if not texts:
            return False
        for text in texts:
            pt = text.dxf.insert
            if (abs(pt.x - insert[0]) <= 5 and abs(pt.y - insert[1]) <= 5):
                text.dxf.text = str(value)
                return True
        return False

    def modify_dxf(self):
        """Read DXF template and substitute TEXT entity values with current parameters.

        Mirrors the old program: reads ``StandardDXF/Normal/{machine}/{short_code}.dxf``
        where short_code may get a ``_M20``/``_M36`` suffix for mandrel types.
        """
        if ezdxf is None:
            return
        if not self.change:
            return

        # Derive the template path
        graph_no = self.parameters.get('图号', '')
        if '-' not in graph_no:
            return
        short_code = graph_no.split('-')[1][:-4]  # e.g. 'DC0124-SS010001' -> 'SS01'

        # ADBT/SS01 use different templates based on thread size
        if short_code in ('SS01', 'ADBT'):
            m_val = str(self.parameters.get('M螺纹', ''))
            if 'M20' in m_val:
                short_code += '_M20'
            elif 'M36' in m_val:
                short_code += '_M36'

        template_path = os.path.join(_STANDARD_DXF_ROOT, self.machine_type, short_code + '.dxf')
        self._file_name = template_path

        try:
            self._doc = ezdxf.readfile(template_path)
        except IOError:
            self._doc = None
            return

        # Height between label TEXT and value TEXT in the template
        HEIGHT = 18

        texts = self._doc.modelspace().query('TEXT')
        for text_entity in texts:
            label = text_entity.dxf.text
            if label not in self.parameters:
                continue
            value = format_dxf_text(label, self.parameters[label])
            if label == '图号':
                # Strip machine prefix; truncate to first 4 + last 4 chars if long
                parts = str(value).split('-')
                val = parts[1] if len(parts) > 1 else parts[0]
                value = (val[:4] + val[-4:]) if len(val) > 8 else val
            # Label is at (x, y), value is at (x, y+HEIGHT)
            found = self._find_value_by_insert(texts, (text_entity.dxf.insert.x,
                                                      text_entity.dxf.insert.y + HEIGHT),
                                              value)
            if not found:
                # Silently skip — some labels in the template may not have a value slot
                pass

    def save_dxf(self, output_path, filename):
        """Save the modified DXF document to output_path."""
        if not self.change or self._doc is None:
            return None
        os.makedirs(output_path, exist_ok=True)
        save_path = os.path.join(output_path, str(filename) + '.dxf')
        self._doc.saveas(save_path)
        return save_path
