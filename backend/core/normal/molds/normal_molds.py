"""
Normal Module - Mold classes for DC0124, DC0121, DC0125 machines.
"""
import math
from datetime import date
from .base_mold import BaseMoldClass
from core.common.params import format_length_value, relief_hole_values

# Size lookup table shared by ADIE and DIEO
D_TO_D2_TABLE = {
    (12, 15): 25, (15, 20): 30, (20, 25): 35, (25, 30): 50,
    (30, 38): 60, (38, 45): 70, (45, 52): 80, (52, 60): 90,
    (60, 69): 100, (69, 75): 110, (75, 85): 120, (85, 95): 130,
    (95, 105): 140, (105, 115): 150,
}


def min_positive_wall(Tx):
    """T1–T4 中大于 0 的最小壁厚。壁厚为 0 的段不参与芯轴和退料模直径。"""
    values = [Tx[key] for key in ('T1', 'T2', 'T3', 'T4') if Tx.get(key, 0) > 0]
    return min(values) if values else None


def lookup_d2(d_value):
    """Lookup d2 value from D size table."""
    d = float(d_value)
    for (low, high), d2 in D_TO_D2_TABLE.items():
        if low <= d < high:
            return d2
    return 0


class ADIE(BaseMoldClass):
    """缩管模 - Shrink Tube Mold (shared by all machines).

    Template has:
      %%CD (label) + %%Cd (value slot below) -> D + offset
      %%Cd1 (label) + <value> -> %%Cd + 8.7
      %%Cd2 (label) + <value> -> lookup_d2(%%Cd1)
    The %%Cd label name in the template is misleading — it's the value for %%CD.
    """

    def __init__(self):
        super().__init__()
        self.English_name = 'ShrinkTubeMold'
        self.Chinese_name = '缩管模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0
        d_mold = D + offset  # 缩管放大模具直径

        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        cd = round(d_mold + 5.55, 2)
        cd1 = round(cd + 8.7, 2)
        self.parameters['%%CD'] = str(round(d_mold, 2))
        self.parameters['%%Cd'] = str(cd)
        self.parameters['%%Cd1'] = str(cd1)
        self.parameters['%%Cd2'] = str(lookup_d2(cd1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['成品直径'] = '实抽' + str(D)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DIEO(BaseMoldClass):
    """成型模 - Forming Mold (shared by all machines)."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMold'
        self.Chinese_name = '成型模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        # Forming mold never adds 0.3 offset
        d_mold = D

        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        cd = round(d_mold + 5.55, 2)
        cd1 = round(cd + 8.7, 2)
        self.parameters['%%CD'] = str(round(d_mold, 2))
        self.parameters['%%Cd'] = str(cd)
        self.parameters['%%Cd1'] = str(cd1)
        self.parameters['%%Cd2'] = str(lookup_d2(cd1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['成品直径'] = '实抽' + str(D)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class ADBT(BaseMoldClass):
    """抽管芯轴 - Shrink Tube Mandrel (shared by all machines).

    Parameters follow the old ``ShrinkTubeMandrel`` layout:
      %%Cd1/2/3 = D - 2*T1/2/3；壁厚 ≤ 0 时为 /
      %%Cd4 = D - 2*T4；T4 ≤ 0 时为 /
      L1 = Lx['L1'] + 5,  L3 = Lx['L3'],  L4 = Lx['L4']
      M螺纹 from max diameter
      LT = 1060 or ceil((total+320)/5)*5
    """

    def __init__(self):
        super().__init__()
        self.English_name = 'ShrinkTubeMandrel'
        self.Chinese_name = '抽管芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0

        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']

        # 壁厚 ≤ 0 的芯轴外径留空。%%Cd4 用 T4。
        for i in range(1, 4):
            thickness = Tx.get(f'T{i}', 0)
            if thickness > 0:
                self.parameters[f'%%Cd{i}'] = str(round(D - 2 * thickness + offset, 1))
            else:
                self.parameters[f'%%Cd{i}'] = '/'
        t4 = Tx.get('T4', 0)
        if t4 > 0:
            self.parameters['%%Cd4'] = str(round(D - 2 * t4 + offset, 1))
        else:
            self.parameters['%%Cd4'] = '/'

        # 减重孔：图纸标签 %%CA、L。导出时按标签写入对应数值。
        ca, hole_l = relief_hole_values(
            self.parameters[f'%%Cd{i}'] for i in range(1, 5)
        )
        self.parameters['%%CA'] = ca
        self.parameters['L'] = hole_l

        # L1 有长度时仍加 5。L1–L4 源长度为 0 时写成 /
        l1 = Lx.get('L1', 0)
        l2 = Lx.get('L2', 0)
        l3 = Lx.get('L3', 0)
        l4 = Lx.get('L4', 0)

        def length_or_blank(value, extra=0):
            if value <= 0:
                return '/'
            return format_length_value(value + extra)

        self.parameters['L1'] = length_or_blank(l1, 5)
        self.parameters['L2'] = length_or_blank(l2)
        self.parameters['L3'] = length_or_blank(l3)
        self.parameters['L4'] = length_or_blank(l4)

        # M螺纹 from max diameter
        d4_val = D - 2 * Tx.get('T3', 0) - 0.5 + offset
        self.parameters['M螺纹'] = 'M36' if d4_val > 36 else 'M20'

        # LT
        total_len = (0 if l1 <= 0 else l1 + 5) + l2 + l3 + (0 if l4 <= 0 else l4)
        self.parameters['LT'] = '1060' if total_len + 320 < 1060 else str(
            math.ceil((total_len + 320) / 5) * 5
        )

        # 缩管模直径
        self.parameters['缩管模直径'] = str(round(D + offset, 2))

        # 抽管尺寸只写大于 0 的壁厚
        thicknesses = [Tx.get(f'T{i}', 0) for i in range(1, 5) if Tx.get(f'T{i}', 0) > 0]
        tube_str = f'%%C{round(D + offset, 2)}'
        if thicknesses:
            tube_str += '-' + '-'.join(str(value) for value in thicknesses)
        self.parameters['抽管尺寸'] = tube_str

        # T (芯轴厚度)
        self.parameters['T'] = str(int(0.75 * D)) if D <= 66 else '50'

        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


# === DC0124 Specific Molds ===

class DC0124_AD03(BaseMoldClass):
    """DC0124 裁剪夹模 - Cutting Clip Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold'
        self.Chinese_name = '裁剪夹模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0124_AD01(BaseMoldClass):
    """DC0124 抽管退料模 - Shrink Tube Unloading Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'ShrinkTubeUnloadingMold'
        self.Chinese_name = '抽管退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0
        t_min = min_positive_wall(Tx)
        if t_min is None:
            A_val = D + offset + 0.2
        else:
            A_val = D - 2 * t_min + offset + 0.2
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['A'] = str(round(A_val, 1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0124_AD02(BaseMoldClass):
    """DC0124 成型退料模 - Forming Unloading Mold."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingUnloadingMold'
        self.Chinese_name = '成型退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        if not normal_add:
            return
        t_min = min_positive_wall(Tx)
        if t_min is None:
            return
        A = float(D) - 2 * t_min - 0.1
    
        self.parameters['%%CA'] = str(round(A, 1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0124_SS01(BaseMoldClass):
    """DC0124 成型芯轴 - Forming Mandrel. Only calculated when normal_add=True."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMandrel'
        self.Chinese_name = '成型芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']

        if not normal_add:
            self.change = False
            return

        t_min = min_positive_wall(Tx)
        if t_min is None:
            self.change = False
            return
        d_cd = D - 2 * t_min - 0.3
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['M螺纹'] = 'M36' if d_cd < 36 else 'M20'
        self.parameters['%%CA'] = '/'
        self.parameters['L'] = '/'
        # LT
        if L + 320 < 1060:
            self.parameters['LT'] = '1060'
        else:
            self.parameters['LT'] = '1140'
        self.parameters['BXB'] = '35X35'
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0121_SS01(BaseMoldClass):
    """DC0121 成型芯轴 - Forming Mandrel. Only calculated when normal_add=True."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMandrel'
        self.Chinese_name = '成型芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']

        if not normal_add:
            self.change = False
            return

        t_min = min_positive_wall(Tx)
        if t_min is None:
            self.change = False
            return
        d_cd = D - 2 * t_min - 0.3
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['M螺纹'] = 'M36' if d_cd < 36 else 'M20'
        self.parameters['%%CA'] = '/'
        self.parameters['L'] = '/'
        if L + 320 < 1060:
            self.parameters['LT'] = '1060'
        else:
            self.parameters['LT'] = '1140'
        self.parameters['BXB'] = '25X25'
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0125_SS01(BaseMoldClass):
    """DC0125 成型芯轴 - Forming Mandrel. Only calculated when normal_add=True."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMandrel'
        self.Chinese_name = '成型芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']

        if not normal_add:
            self.change = False
            return

        t_min = min_positive_wall(Tx)
        if t_min is None:
            self.change = False
            return
        d_cd = D - 2 * t_min - 0.3
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['M螺纹'] = 'M36' if d_cd < 36 else 'M20'
        self.parameters['%%CA'] = '/'
        self.parameters['L'] = '/'
        if L + 320 < 1060:
            self.parameters['LT'] = '1060'
        else:
            self.parameters['LT'] = '1140'
        self.parameters['BXB'] = '25X25'
        self.parameters['L1'] = format_length_value(L + 10)
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


# === DC0121 Specific Molds ===

class DC0121_AD03(BaseMoldClass):
    """DC0121 裁剪夹模1."""

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold1'
        self.Chinese_name = '裁剪夹模1'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        self.parameters['A'] = '70'
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0121_AD04(BaseMoldClass):
    """DC0121 裁剪夹模2."""

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold2'
        self.Chinese_name = '裁剪夹模2'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        self.parameters['A'] = '70'
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0121_AD01(BaseMoldClass):
    """DC0121 抽管退料模."""

    def __init__(self):
        super().__init__()
        self.English_name = 'ShrinkTubeUnloadingMold'
        self.Chinese_name = '抽管退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0
        t_min = min_positive_wall(Tx)
        d_cd = D + offset + 0.2 if t_min is None else D - 2 * t_min + offset + 0.2
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['A'] = '35'
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0121_AD02(BaseMoldClass):
    """DC0121 成型退料模."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingUnloadingMold'
        self.Chinese_name = '成型退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(round(D, 2))
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


# === DC0121 Specific Molds ===

class DC0125_AD07(BaseMoldClass):
    """DC0125 裁剪夹模."""

    def __init__(self):
        super().__init__()
        self.English_name = 'CuttingClipMold'
        self.Chinese_name = '裁剪夹模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(D)
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0125_AD06_F(BaseMoldClass):
    """DC0125 成型退料模."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingUnloadingMold'
        self.Chinese_name = '成型退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0
        t_min = min_positive_wall(Tx)
        d_cd = D + offset + 0.2 if t_min is None else D - 2 * t_min + offset + 0.2
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['成品直径'] = str(D)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0125_AD06_S(BaseMoldClass):
    """DC0125 抽管退料模."""

    def __init__(self):
        super().__init__()
        self.English_name = 'ShrinkTubeUnloadingMold'
        self.Chinese_name = '抽管退料模'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        offset = 0.3 if normal_add else 0
        t_min = min_positive_wall(Tx)
        d_cd = D + offset + 0.2 if t_min is None else D - 2 * t_min + offset + 0.2
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['件数'] = external_params['件数']
        self.parameters['成品直径'] = str(D)
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True


class DC0125_SS01(BaseMoldClass):
    """DC0125 成型芯轴 - Forming Mandrel. Only calculated when normal_add=True."""

    def __init__(self):
        super().__init__()
        self.English_name = 'FormingMandrel'
        self.Chinese_name = '成型芯轴'

    def set_params(self, tube_df_params, external_params, normal_add=False):
        L, D, Lx, Mx, Tx = self._get_params_from_tube(tube_df_params)
        self.parameters['模具名称'] = self.Chinese_name
        self.machine_type = external_params['图号'].split('-')[0]
        self.parameters['图号'] = external_params['图号']

        if not normal_add:
            self.change = False
            return

        t_min = min_positive_wall(Tx)
        if t_min is None:
            self.change = False
            return
        d_cd = D - 2 * t_min - 0.3
        self.parameters['%%CD'] = str(round(d_cd, 1))
        self.parameters['M螺纹'] = 'M36' if d_cd < 36 else 'M20'
        self.parameters['%%CA'] = '/'
        self.parameters['L'] = '/'
        if L + 320 < 1060:
            self.parameters['LT'] = '1060'
        else:
            self.parameters['LT'] = '1140'
        self.parameters['BXB'] = '25X25'
        self.parameters['L1'] = format_length_value(L + 10)
        self.parameters['件数'] = external_params['件数']
        self.parameters['车种规格'] = external_params['车种规格']
        self.parameters['设计者'] = external_params['设计者']
        self.parameters['日期'] = str(date.today())
        self.change = True
