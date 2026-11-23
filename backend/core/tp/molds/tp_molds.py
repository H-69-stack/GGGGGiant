"""
TP Module - Mold classes for DC0128 machine.
Reconstructed from old program's TP_Base_Mold and mold classes.
"""
import os
import math
from datetime import date
import pandas as pd

try:
    import ezdxf
except ImportError:
    ezdxf = None

from config import STATIC_DIR
from core.common.params import format_dxf_text, format_length_value, relief_hole_values

_STANDARD_DXF_ROOT = os.path.join(STATIC_DIR, 'standard_dxf', 'tp', 'DC0128')


class BaseMoldClass:
    """Base class for TP mold parameter models."""

    def __init__(self):
        self.English_name = ''
        self.Chinese_name = ''
        self.parameters = {}
        self.change = False
        self.machine_type = ''
        self._doc = None
        self._file_name = ''

    def _get_tp_params_from_tube(self, pd_params) -> tuple:
        """Extract tube parameters from DataFrame for TP module.

        Returns: (L, D, Lx, Mx, Tx, T_D, T_L, T_LR)
        """
        params = pd_params.to_dict(orient='list')
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
                fv = float(val)
            except (ValueError, TypeError):
                continue
            if item.startswith('T') and item not in ['T_D', 'T_L', 'T_LR']:
                Tx[item] = fv
            elif item.startswith('M'):
                Mx[item] = fv
            elif item.startswith('L') and item not in ['L', 'T_L', 'T_LR', 'LL', 'LL1']:
                Lx[item] = fv

        T_D = float(params.get('T_D', 0))
        T_L = float(params.get('T_L', 0))
        T_LR = float(params.get('T_LR', 0))

        return L, D, Lx, Mx, Tx, T_D, T_L, T_LR

    def set_params(self, tube_df_params, external_params, normal_add=False):
        """Override in subclass to calculate mold parameters."""
        raise NotImplementedError

    def _modify_parameters(self, key, value):
        """Modify a single parameter."""
        if key in self.parameters:
            self.parameters[key] = value

    def get_params(self):
        """Return parameters as DataFrame with Chinese column names (参数/值)."""
        return pd.DataFrame({
            '参数': list(self.parameters.keys()),
            '值': list(self.parameters.values())
        })

    def _find_value_by_insert(self, texts, insert, value) -> bool:
        """Find TEXT at (x±5, y±5) and set its text value."""
        if not texts:
            return False
        for text_entity in texts:
            pt = text_entity.dxf.insert
            if abs(pt.x - insert[0]) <= 5 and abs(pt.y - insert[1]) <= 5:
                text_entity.dxf.text = str(value)
                return True
        return False

    def modify_dxf(self):
        """Read DXF template and substitute TEXT entity values with current parameters."""
        if ezdxf is None or not self.change:
            return

        graph_no = self.parameters.get('图号', '')
        if '-' not in graph_no:
            return
        short_code = getattr(self, 'dxf_code', None) or graph_no.split('-')[1][:-4]

        template_path = os.path.join(_STANDARD_DXF_ROOT, short_code + '.dxf')
        self._file_name = template_path

        try:
            self._doc = ezdxf.readfile(template_path)
        except IOError:
            self._doc = None
            return

        height = 18
        texts = self._doc.modelspace().query('TEXT')
        for text_entity in texts:
            label = text_entity.dxf.text
            if label not in self.parameters:
                continue
            value = format_dxf_text(label, self.parameters[label])
            if label == '图号':
                parts = str(value).split('-')
                val = parts[1] if len(parts) > 1 else parts[0]
                value = (val[:4] + val[-4:]) if len(val) > 8 else val
            self._find_value_by_insert(
                texts,
                (text_entity.dxf.insert.x, text_entity.dxf.insert.y + height),
                value
            )

    def save_dxf(self, output_path, filename):
        """Save modified DXF to output path."""
        if not self.change or self._doc is None:
            return None
        os.makedirs(output_path, exist_ok=True)
        save_path = os.path.join(output_path, str(filename) + '.dxf')
        self._doc.saveas(save_path)
        return save_path


class DC0128_AD01(BaseMoldClass):
    """DC0128 裁剪夹模1 - Cutting Clip Mold 1 (大直径，图号前缀 AD01)。"""

    # 图号前缀 AD01；DXF 模板为 AD01.dxf
    dxf_code = 'AD01'

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold1'
        self.Chinese_name = '裁剪夹模1'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        # external_params['图号'] 形如 AD010001（不含机床前缀）
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['%%CD'] = str(round(T_D, 2))
        self.parameters['件数'] = external_params.get('件数', 2)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0128_AD01_(BaseMoldClass):
    """DC0128 裁剪夹模2 - Cutting Clip Mold 2 (小直径)。

    计算仍用成品外径 D；图号前缀与夹模1相同为 AD01，
    同批计算时序号递增；DXF 模板仍用 AD01_.dxf。
    """

    dxf_code = 'AD01_'

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold2'
        self.Chinese_name = '裁剪夹模2'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['%%CD'] = str(round(D, 2))
        self.parameters['件数'] = external_params.get('件数', 2)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0128_ADIE(BaseMoldClass):
    """DC0128 抽套模 - Draw Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'DrawMold'
        self.Chinese_name = '抽套模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['T_s'] = str(round(D, 2))
        self.parameters['T_D'] = str(round(T_D, 2))
        self.parameters['T_L'] = format_length_value(T_L)
        self.parameters['T_e'] = format_length_value(T_L + 10)
        self.parameters['line'] = '正常读取'
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0128_ADIE_(BaseMoldClass):
    """DC0128 成型抽套模 - Forming Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMold'
        self.Chinese_name = '成型抽套模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['T_s'] = str(round(D, 2))
        self.parameters['T_D'] = str(round(T_D, 2))
        self.parameters['T_L'] = format_length_value(T_L)
        self.parameters['T_e'] = format_length_value(T_L + 10)
        self.parameters['line'] = '正常读取'
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


import math


class DC0128_ADBT(BaseMoldClass):
    """DC0128 抽管芯轴 - Shrink Tube Mandrel."""

    def __init__(self, interference_length=0):
        super().__init__()
        self.English_name = 'ShrinkTubeMandrel'
        self.Chinese_name = '抽管芯轴'
        self.interference_length = interference_length

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        t1 = Tx.get('T1', 0)
        t2 = Tx.get('T2', 0)
        t3 = Tx.get('T3', 0)
        l1 = Lx.get('L1', 0)
        l2 = Lx.get('L2', 0)
        l3 = Lx.get('L3', 0)
        m1 = Mx.get('M1', 0)
        m2 = Mx.get('M2', 0)

        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['L1'] = format_length_value(l1 + 5)
        self.parameters['M1'] = str(round(m1, 2))
        self.parameters['L2'] = format_length_value(l2)
        self.parameters['M2'] = str(round(m2, 2))
        self.parameters['L3'] = format_length_value(l3)
        self.parameters['IL'] = format_length_value(self.interference_length)
        self.parameters['L4'] = format_length_value(T_L)
        self.parameters['%%Cd1'] = str(round(D - 2 * t1, 2))
        self.parameters['%%Cd2'] = str(round(D - 2 * t2, 2))
        self.parameters['%%Cd3'] = str(round(D - 2 * t3, 2))
        self.parameters['%%Cd4'] = str(round(D - 2 * t3 - 0.5, 2))
        ca, hole_l = relief_hole_values(
            self.parameters[f'%%Cd{i}'] for i in range(1, 5)
        )
        self.parameters['%%CA'] = ca
        self.parameters['L'] = hole_l
        d4 = D - 2 * t3 - 0.5
        self.parameters['M'] = '36' if d4 > 36 else '20'
        total_len = (l1 + 5) + l2 + l3 + T_L
        self.parameters['LT'] = '1060' if total_len + 320 < 1060 else str(math.ceil((total_len + 320) / 5) * 5)
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0128_ADBT_(BaseMoldClass):
    """DC0128 成型抽管芯轴 - Forming Mandrel."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMandrel'
        self.Chinese_name = '成型抽管芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        t1 = Tx.get('T1', 0)
        t2 = Tx.get('T2', 0)
        t3 = Tx.get('T3', 0)
        l1 = Lx.get('L1', 0)
        l2 = Lx.get('L2', 0)
        l3 = Lx.get('L3', 0)
        m1 = Mx.get('M1', 0)
        m2 = Mx.get('M2', 0)

        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['L1'] = format_length_value(l1 + 5)
        self.parameters['M1'] = str(round(m1, 2))
        self.parameters['L2'] = format_length_value(l2)
        self.parameters['M2'] = str(round(m2, 2))
        self.parameters['L3'] = format_length_value(l3)
        self.parameters['IL'] = '0'
        self.parameters['L4'] = format_length_value(T_L)
        self.parameters['%%Cd1'] = str(round(D - 2 * t1 + 1, 2))
        self.parameters['%%Cd2'] = str(round(D - 2 * t2 + 1, 2))
        self.parameters['%%Cd3'] = str(round(D - 2 * t3 + 1, 2))
        self.parameters['%%Cd4'] = str(round(D - 2 * t3 - 0.5 + 1, 2))
        d4 = D - 2 * t3 - 0.5 + 1
        self.parameters['M'] = '36' if d4 > 36 else '20'
        total_len = (l1 + 5) + l2 + l3 + T_L
        self.parameters['LT'] = '1060' if total_len + 320 < 1060 else str(math.ceil((total_len + 320) / 5) * 5)
        self.parameters['件数'] = external_params.get('件数', 1)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0128_AD04(BaseMoldClass):
    """DC0128 抽管退料模 - Unloading Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'UnloadingMold'
        self.Chinese_name = '抽管退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx, T_D, T_L, T_LR = self._get_tp_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = 'DC0128'
        self.parameters['图号'] = 'DC0128-' + external_params['图号']
        self.parameters['A'] = str(round(T_D, 2))
        self.parameters['件数'] = external_params.get('件数', 2)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True
