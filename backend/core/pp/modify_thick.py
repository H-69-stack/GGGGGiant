"""
壁厚修改（按工艺方案重算"修改后壁厚"曲线）。
完整移植自老项目 ``bin/pp_bin/modify_thick.py`` 与 ``modify_thick_pt.py``。

扩口段壁厚：T1×D1/Dn；无 Dn 时用 D0。
Taper 段壁厚：有 D2 用 D3/D2，无 D2 时用 D0。
勾选 Taper 作过渡后，总表 TLM 优先写方案 L3。
完整逻辑见 docs/WALL_THICKNESS_MODIFY_LOGIC.md。
"""
import numpy as np
import pandas as pd


def _find_duplicates(x2):
    """寻找连续段长度 > 5 的壁厚常数值（按老项目算法）。"""
    if len(x2) == 0:
        return []
    result = []
    current_num = x2[0]
    current_count = 1
    for i in range(1, len(x2)):
        if x2[i] == current_num:
            current_count += 1
        else:
            if current_count > 5:
                result.append(current_num)
            current_num = x2[i]
            current_count = 1
    if current_count > 5:
        result.append(current_num)
    return result


def _find_segments_numeric(thick_num, x1, x2):
    """老项目 ``find_segments`` 的纯数值版本（TL_sec 为数值列表）。"""
    TL_sec = []
    TL_sec_index = []
    TL_seg = []
    TLM_seg = []

    segments = []
    processed_segments = set()

    j = 0
    while j < len(x1):
        current_value = x2[j]
        if current_value in thick_num:
            start_j = j
            while j < len(x1) and x2[j] == current_value:
                j += 1
            end_j = j - 1
            current_segment = x1[start_j:end_j + 1]
            if len(current_segment) >= 5:
                segment_key = (current_value, start_j, end_j)
                if segment_key not in processed_segments:
                    processed_segments.add(segment_key)
                    segments.append({
                        'value': current_value,
                        'start_x1': current_segment[0],
                        'end_x1': current_segment[-1],
                        'start_j': start_j,
                        'end_j': end_j,
                        'length': current_segment[-1] - current_segment[0],
                    })
        else:
            j += 1

    segments.sort(key=lambda s: s['start_j'])

    for seg in segments:
        TL_sec.append(float(seg['start_x1']))
        TL_sec_index.append(seg['start_j'])
        TL_sec.append(float(seg['end_x1']))
        TL_sec_index.append(seg['end_j'])
        TL_seg.append(seg['length'])

    for k in range(3, len(TL_sec), 2):
        TLM_seg.append(TL_sec[k - 1] - TL_sec[k - 2])

    return TL_sec, TL_sec_index, TL_seg, TLM_seg


def _find_nearest_smaller(nums, target):
    left, right = 0, len(nums) - 1
    while left <= right:
        mid = left + (right - left) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return left - 1 if left > 0 else -1


def _safe_get(lst, idx, default=0):
    return lst[idx] if 0 <= idx < len(lst) else default


def _kuo_diameter(Dn, D0):
    """扩口段缩放用径：有 Dn（>0）用 Dn，否则 D0。"""
    try:
        dn = float(Dn) if Dn not in (None, "") else 0.0
    except (TypeError, ValueError):
        dn = 0.0
    if dn > 0:
        return dn
    try:
        return float(D0 or 0)
    except (TypeError, ValueError):
        return 0.0


def _taper_diameter(D2, D0):
    """Taper 段缩放用径：有 D2（>0）用 D2，否则 D0。"""
    return _kuo_diameter(D2, D0)


def _build_segments2(thick_num2, TL_seg2, TLM_seg2):
    return {
        'T1': round(_safe_get(thick_num2, 0), 1),
        'T2': round(_safe_get(thick_num2, 1), 1),
        'T3': round(_safe_get(thick_num2, 2), 1),
        'T4': round(_safe_get(thick_num2, 3), 1),
        'TL1': _safe_get(TL_seg2, 0),
        'TL2': _safe_get(TL_seg2, 1),
        'TL3': _safe_get(TL_seg2, 2),
        'TL4': _safe_get(TL_seg2, 3),
        'TLM1': _safe_get(TLM_seg2, 0),
        'TLM2': _safe_get(TLM_seg2, 1),
        'TLM3': _safe_get(TLM_seg2, 2),
    }


def _fill_taper_transition_tlm(segments2, x1, TL_sec, L3=0):
    """勾选「Taper 段壁厚作过渡」后补写过渡段长度。

    平台检测只把常值平台之间的空隙记为 TLM；线性 Taper 构不成第二平台，
    末段过渡会被丢掉。若 TLM 仍全空，且最后平台未接到管尾（或整管线性），
    把 L3（否则剩余长度）写入 TLM1。已有 TLM 不覆盖。
    """
    if not segments2 or x1 is None:
        return segments2
    n = len(x1)
    if n == 0:
        return segments2
    already = any(float(segments2.get(k) or 0) > 0 for k in ('TLM1', 'TLM2', 'TLM3'))
    if already:
        return segments2
    last_s = float(x1[-1])
    first_s = float(x1[0])
    last_platform_end = float(TL_sec[-1]) if TL_sec else first_s
    remaining = last_s - last_platform_end
    if remaining <= 0.5:
        if TL_sec:
            return segments2
        remaining = last_s - first_s
    try:
        l3 = float(L3 or 0)
    except (TypeError, ValueError):
        l3 = 0.0
    tlm = l3 if l3 > 0 else remaining
    if tlm <= 0:
        return segments2
    segments2['TLM1'] = round(float(tlm), 2)
    return segments2


def _segments2_after_modify(thick_num2, TL_seg2, TLM_seg2, x1, TL_sec2, *, Taper_thick, L3=0):
    segments2 = _build_segments2(thick_num2, TL_seg2, TLM_seg2)
    if Taper_thick:
        _fill_taper_transition_tlm(segments2, x1, TL_sec2, L3)
    return segments2


class Modify_thick:
    """Taper+TP抽+扩口 / 普通抽+TP抽+Taper+扩口 / 加长Taper+TP抽+扩口 用。"""

    def __init__(self, thicknesses, D1, D0, D2, D3, L1, L2, Taper_thick, Dn=0, L3=0):
        self.thicknesses = thicknesses
        self.x1 = np.asarray(thicknesses['中心线长度(mm)'].values, dtype=float)
        self.x2 = np.asarray(thicknesses['壁厚(mm)'].values, dtype=float).copy()
        self.D1 = D1
        self.D0 = D0
        self.D2 = D2
        self.D3 = D3
        self.Dn = Dn
        self.L1 = L1
        self.L2 = L2
        self.L3 = L3
        self.Taper_thick = bool(Taper_thick)
        if self.L2 <= 5:
            self.L2 = 50

    def modify_thick(self, segments):
        x1 = self.x1
        x2 = self.x2
        D1, D0, D2, D3 = self.D1, self.D0, self.D2, self.D3
        L1, L2 = self.L1, self.L2
        D_kuo = _kuo_diameter(self.Dn, D0)
        thick_num = _find_duplicates(x2)
        if not thick_num:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])
        if D_kuo <= 0:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])
        T_kuo = thick_num[0] * D1 / D_kuo

        TL_sec, TL_sec_index, TL_seg, TLM_seg = _find_segments_numeric(thick_num, x1, x2)
        L1_index = _find_nearest_smaller(x1, L1)
        L2_index = _find_nearest_smaller(x1, L1 + L2)

        for i in range(L1_index + 1):
            x2[i] = T_kuo

        n = len(thick_num)

        if n == 4:
            if len(TL_sec) > 2 and (L1 + L2) < TL_sec[2]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[1] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[1] * D3 / D2 - thick_num[3] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)
            elif len(TL_sec) > 3 and (L1 + L2) < TL_sec[3]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[1] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[1] * D3 / D2 - thick_num[3] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, L2_index):
                    x2[i] = T_kuo - ((i - L1_index) / (L2_index - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)
            elif len(TL_sec) > 4 and L1 + L2 < TL_sec[4]:
                if self.Taper_thick:
                    for i in range(TL_sec_index[4], TL_sec_index[-1] + 1):
                        x2[i] = thick_num[2] * D3 / D2 - ((i - TL_sec_index[4]) / (TL_sec_index[-1] + 1 - TL_sec_index[4])) * (
                            thick_num[2] * D3 / D2 - thick_num[3] * D3 / D2)
                else:
                    for i in range(TL_sec_index[4], TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[3], TL_sec_index[4]):
                    x2[i] = (thick_num[1] - ((i - TL_sec_index[3]) / (TL_sec_index[4] - TL_sec_index[3])) * (
                        thick_num[1] - thick_num[2] * D3 / D2))
            elif len(TL_sec) > 5 and L1 + L2 < TL_sec[5]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[2] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[2] * D3 / D2 - thick_num[3] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[3], L2_index):
                    x2[i] = thick_num[1] - ((i - TL_sec_index[3]) / (L2_index - TL_sec_index[3])) * (
                        thick_num[1] - thick_num[2] * D3 / D2)
            elif len(TL_sec) > 6 and L1 + L2 < TL_sec[6]:
                for i in range(TL_sec_index[6], TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[5], TL_sec_index[6]):
                    x2[i] = (thick_num[2] - ((i - TL_sec_index[5]) / (TL_sec_index[6] - TL_sec_index[5])) * (
                        thick_num[2] - thick_num[3] * D3 / D2))
            elif len(TL_sec) > 7 and L1 + L2 < TL_sec[7]:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[5], L2_index):
                    x2[i] = (thick_num[2] - ((i - TL_sec_index[5]) / (L2_index - TL_sec_index[5])) * (
                        thick_num[2] - thick_num[3] * D3 / D2))

        elif n == 3:
            if len(TL_sec) > 1 and (L1 + L2) < TL_sec[1]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[0] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[2] * D3 / D2 - thick_num[0] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, L2_index):
                    x2[i] = T_kuo - ((i - L1_index) / (L2_index - L1_index)) * (
                        T_kuo - thick_num[0] * D3 / D2)
            elif len(TL_sec) > 2 and (L1 + L2) < TL_sec[2]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[1] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[2] * D3 / D2 - thick_num[1] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)
            elif len(TL_sec) > 3 and (L1 + L2) < TL_sec[3]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[1] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[2] * D3 / D2 - thick_num[1] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, L2_index):
                    x2[i] = T_kuo - ((i - L1_index) / (L2_index - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)
            elif len(TL_sec) > 4 and L1 + L2 < TL_sec[4]:
                for i in range(TL_sec_index[4], TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[3], TL_sec_index[4]):
                    x2[i] = (thick_num[1] - ((i - TL_sec_index[3]) / (TL_sec_index[4] - TL_sec_index[3])) * (
                        thick_num[1] - thick_num[2] * D3 / D2))
            elif len(TL_sec) > 5 and L1 + L2 < TL_sec[5]:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1])
                for i in range(TL_sec_index[3], L2_index):
                    x2[i] = thick_num[1] - ((i - TL_sec_index[3]) / (L2_index - TL_sec_index[3])) * (
                        thick_num[1] - thick_num[2] * D3 / D2)

        elif n == 2:
            if len(TL_sec) > 1 and (L1 + L2) < TL_sec[1]:
                if self.Taper_thick:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = thick_num[0] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                            thick_num[1] * D3 / D2 - thick_num[0] * D3 / D2)
                else:
                    for i in range(L2_index, TL_sec_index[-1] + 1):
                        x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, L2_index):
                    x2[i] = T_kuo - ((i - L1_index) / (L2_index - L1_index)) * (
                        T_kuo - thick_num[0] * D3 / D2)
            elif len(TL_sec) > 2 and (L1 + L2) < TL_sec[2]:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, TL_sec_index[2]):
                    x2[i] = T_kuo - ((i - L1_index) / (TL_sec_index[2] - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)
            elif len(TL_sec) > 3 and (L1 + L2) < TL_sec[3]:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
                for i in range(L1_index, L2_index):
                    x2[i] = T_kuo - ((i - L1_index) / (L2_index - L1_index)) * (
                        T_kuo - thick_num[1] * D3 / D2)

        elif n == 1:
            if self.Taper_thick:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = thick_num[0] * D3 / D2 - ((i - L2_index) / (TL_sec_index[-1] + 1 - L2_index)) * (
                        thick_num[0] - thick_num[0] * D3 / D2)
            else:
                for i in range(L2_index, TL_sec_index[-1] + 1):
                    x2[i] = x2[i] * D3 / D2
            for i in range(L1_index, L2_index):
                x2[i] = (T_kuo -
                         ((i - L1_index) / (L2_index - L1_index)) * (T_kuo - thick_num[0] * D3 / D2))

        x2 = np.around(x2, decimals=3)
        thick_num2 = _find_duplicates(x2)
        _TL_sec2, _TL_sec_index2, TL_seg2, TLM_seg2 = _find_segments_numeric(thick_num2, x1, x2)
        segments2 = _segments2_after_modify(
            thick_num2, TL_seg2, TLM_seg2, x1, _TL_sec2,
            Taper_thick=self.Taper_thick, L3=self.L3,
        )
        df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
        return df_output, segments2


class Modify_thick_pt:
    """普通抽+TP抽 / 普通抽+扩口 / TP抽+扩口 / 普通抽+Taper+扩口 / 普通抽+Taper 用。"""

    def __init__(self, thicknesses, D1, D0, D3, L1=0, L3=0, Taper_thick=False, Dn=0, D2=0):
        self.thicknesses = thicknesses
        self.x1 = np.asarray(thicknesses['中心线长度(mm)'].values, dtype=float)
        self.x2 = np.asarray(thicknesses['壁厚(mm)'].values, dtype=float).copy()
        self.D1 = D1
        self.D3 = D3
        self.D0 = D0
        self.Dn = Dn
        self.D2 = D2
        self.L1 = L1
        self.L3 = L3
        self.Taper_thick = bool(Taper_thick)

    def _calculate_x2_values(self, initial_index, final_index, thick, thick2, D0, D1, D3, L3, x2):
        """在 [initial_index, final_index) 线性过渡到 thick2，并保证接点连续。

        旧实现在 final_index <= initial_index 时 range 为空，扩口平区与后方原壁厚硬接产生跳变
        （普通抽+扩口常见：L1 已越过第二平台起点 TL_sec_index[2]）。
        """
        if D0 == 0:
            raise ValueError("参数D0不能为0，会导致除以0错误")
        n = len(x2)
        if n == 0:
            return x2
        i0 = max(0, int(initial_index))
        i1 = int(final_index)
        if i0 >= n:
            return x2
        # 终点无效时：在扩口段后补一段过渡，避免硬跳
        if i1 <= i0:
            i1 = min(i0 + 50, n)
        i1 = min(max(i1, i0 + 1), n)
        span = i1 - i0
        for i in range(i0, i1):
            x2[i] = thick - ((i - i0) / span) * (thick - thick2)
        # 接点落到 thick2，与后方平台/原曲线对齐
        if i1 < n:
            x2[i1] = thick2
        return x2

    @staticmethod
    def _resolve_blend_end(L1_index, TL_sec_index, n_points, fallback_span=50):
        """选取扩口段后的过渡终点：优先下一平台起点，否则 L1 后 fallback_span。"""
        for k in range(0, len(TL_sec_index), 2):
            start = int(TL_sec_index[k])
            if start > L1_index:
                return start
        return min(L1_index + fallback_span, n_points)

    @staticmethod
    def _taper_end_index(TL_sec_index, n_points):
        return (TL_sec_index[-1] + 1) if TL_sec_index else n_points

    @staticmethod
    def _taper_target_thick(thick_num, D3, D_taper):
        """Taper 末端目标壁厚：T末 × D3 / D_taper（D_taper 为 D2，无 D2 时为 D0）。"""
        if D_taper == 0:
            raise ValueError("Taper 缩放直径不能为0，会导致除以0错误")
        return float(thick_num[-1]) * D3 / D_taper

    def modify_thick_taper_only(self, segments):
        """普通抽+Taper（无扩口）：整段壁厚按 L3/Taper 处理，不保留 50mm 过渡段。"""
        x1 = self.x1
        x2 = self.x2
        x2_orig = np.asarray(x2, dtype=float).copy()
        D0, D3 = self.D0, self.D3
        D_taper = _taper_diameter(self.D2, D0)
        thick_num = _find_duplicates(x2)
        if not thick_num:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])
        if D_taper <= 0:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])

        TL_sec, TL_sec_index, TL_seg, TLM_seg = _find_segments_numeric(thick_num, x1, x2)
        start_idx = 0
        end_idx = self._taper_end_index(TL_sec_index, len(x2))

        if self.Taper_thick:
            thick = float(thick_num[0])
            thick2 = self._taper_target_thick(thick_num, D3, D_taper)
            x2 = self._calculate_x2_values(
                start_idx, end_idx, thick, thick2, D0, self.D1, D3, self.L3, x2
            )
        else:
            scale = D3 / D_taper
            for i in range(start_idx, end_idx):
                x2[i] = x2_orig[i] * scale

        x2 = np.around(x2, decimals=3)
        thick_num2 = _find_duplicates(x2)
        _TL_sec2, _TL_sec_index2, TL_seg2, TLM_seg2 = _find_segments_numeric(thick_num2, x1, x2)
        segments2 = _segments2_after_modify(
            thick_num2, TL_seg2, TLM_seg2, x1, _TL_sec2,
            Taper_thick=self.Taper_thick, L3=self.L3,
        )
        df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
        return df_output, segments2

    def modify_thick(self, segments):
        x1 = self.x1
        x2 = self.x2
        D1, D0, D3 = self.D1, self.D0, self.D3
        L1, L3 = self.L1, self.L3
        D_kuo = _kuo_diameter(self.Dn, D0)
        D_taper = _taper_diameter(self.D2, D0)
        x2_orig = np.asarray(x2, dtype=float).copy()
        thick_num = _find_duplicates(x2)
        if not thick_num:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])
        if D_kuo <= 0:
            df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
            return df_output, _build_segments2([], [], [])
        T_kuo = thick_num[0] * D1 / D_kuo

        TL_sec, TL_sec_index, TL_seg, TLM_seg = _find_segments_numeric(thick_num, x1, x2)
        L1_index = _find_nearest_smaller(x1, L1)
        if L1_index < 0:
            L1_index = 0

        for i in range(L1_index + 1):
            x2[i] = T_kuo

        n = len(thick_num)
        if n == 4:
            if L3 == 0:
                thick = T_kuo
                end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                # 目标壁厚：过渡终点处的原壁厚（第二平台或 L1 之后实际值）
                j = min(end_idx, len(x2_orig) - 1)
                thick2 = float(x2_orig[j]) if j >= 0 else thick_num[1]
                x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
            else:
                if self.Taper_thick:
                    thick, thick2 = T_kuo, thick_num[3] * D3 / D_taper
                    x2 = self._calculate_x2_values(L1_index, TL_sec_index[-1] + 1, thick, thick2, D0, D1, D3, L3, x2)
                else:
                    thick, thick2 = T_kuo, thick_num[1] * D3 / D_taper
                    if len(TL_sec_index) > 2:
                        end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                        x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
                        for i in range(end_idx, TL_sec_index[-1] + 1):
                            x2[i] = x2_orig[i] * D3 / D_taper
        elif n == 3:
            if L3 == 0:
                # 普通抽+扩口 / TP抽+扩口：扩口段 [0,L1] 缩放后，平滑接到后方原壁厚
                thick = T_kuo
                end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                j = min(end_idx, len(x2_orig) - 1)
                thick2 = float(x2_orig[j]) if j >= 0 else thick_num[1]
                x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
            else:
                if self.Taper_thick:
                    thick, thick2 = T_kuo, thick_num[2] * D3 / D_taper
                    x2 = self._calculate_x2_values(L1_index, TL_sec_index[-1] + 1, thick, thick2, D0, D1, D3, L3, x2)
                else:
                    thick, thick2 = T_kuo, thick_num[1] * D3 / D_taper
                    if len(TL_sec_index) > 2:
                        end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                        x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
                        for i in range(end_idx, TL_sec_index[-1] + 1):
                            x2[i] = x2_orig[i] * D3 / D_taper
        elif n == 2:
            if L3 == 0:
                thick = T_kuo
                end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                j = min(end_idx, len(x2_orig) - 1)
                thick2 = float(x2_orig[j]) if j >= 0 else thick_num[1]
                x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
            else:
                if self.Taper_thick:
                    thick, thick2 = T_kuo, thick_num[1] * D3 / D_taper
                    x2 = self._calculate_x2_values(L1_index, TL_sec_index[-1] + 1, thick, thick2, D0, D1, D3, L3, x2)
                else:
                    thick, thick2 = T_kuo, thick_num[1] * D3 / D_taper
                    if len(TL_sec_index) > 2:
                        end_idx = self._resolve_blend_end(L1_index, TL_sec_index, len(x2))
                        x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
                        for i in range(end_idx, TL_sec_index[-1] + 1):
                            x2[i] = x2_orig[i] * D3 / D_taper
        elif n == 1:
            if L3 == 0:
                thick, thick2 = T_kuo, thick_num[0]
                end_idx = min(L1_index + 50, len(x2))
                x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
            else:
                if self.Taper_thick:
                    thick, thick2 = T_kuo, thick_num[0] * D3 / D_taper
                    x2 = self._calculate_x2_values(L1_index, TL_sec_index[-1] + 1, thick, thick2, D0, D1, D3, L3, x2)
                else:
                    thick, thick2 = T_kuo, thick_num[0] * D3 / D_taper
                    end_idx = min(L1_index + 50, len(x2))
                    x2 = self._calculate_x2_values(L1_index, end_idx, thick, thick2, D0, D1, D3, L3, x2)
                    for i in range(end_idx, TL_sec_index[-1] + 1):
                        x2[i] = x2_orig[i] * D3 / D_taper

        x2 = np.around(x2, decimals=3)
        thick_num2 = _find_duplicates(x2)
        _TL_sec2, _TL_sec_index2, TL_seg2, TLM_seg2 = _find_segments_numeric(thick_num2, x1, x2)
        segments2 = _segments2_after_modify(
            thick_num2, TL_seg2, TLM_seg2, x1, _TL_sec2,
            Taper_thick=self.Taper_thick, L3=self.L3,
        )
        df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
        return df_output, segments2

    def no_modify_thick(self, segments):
        """不修改壁厚曲线；仍从原曲线（或已有手工分段）导出 segments2。

        曲线必须与输入一一对应（不做 round），避免「修改后壁厚」列与原壁厚不一致。
        """
        x1 = self.x1
        x2 = self.x2  # 保持原值，禁止 round / 缩放
        df_output = pd.DataFrame({'中心线长度(mm)': x1, '壁厚(mm)': x2})
        seg_in = dict(segments) if segments else {}

        # 手工 / QA 已给出 T 与 TL（含单段 T1+TL1）时直接沿用，避免再按 STP 形线检测覆盖图纸长
        n_t = sum(1 for k in ('T1', 'T2', 'T3', 'T4') if float(seg_in.get(k) or 0) > 0)
        has_structure = any(
            float(seg_in.get(k) or 0) > 0
            for k in ('TLM1', 'TLM2', 'TLM3', 'TL1', 'TL2', 'TL3', 'TL4')
        )
        if n_t >= 1 and has_structure:
            return df_output, seg_in

        # 分段识别允许对副本 round，不影响返回曲线
        x2_det = np.around(np.asarray(x2, dtype=float), decimals=3)
        thick_num2 = _find_duplicates(x2_det)
        if not thick_num2:
            return df_output, seg_in if seg_in else _build_segments2([], [], [])

        _TL_sec2, _TL_sec_index2, TL_seg2, TLM_seg2 = _find_segments_numeric(thick_num2, x1, x2_det)
        segments2 = _build_segments2(thick_num2, TL_seg2, TLM_seg2)
        for k in ('D1', 'D3'):
            if float(seg_in.get(k) or 0) > 0:
                segments2[k] = seg_in[k]
        return df_output, segments2


def apply_modify_thick(wall_thickness_data, solution, pp_method, Taper_thick=False, segments=None):
    """对外便捷函数：把 list-of-dict 形式的 wall_thickness_data 接进来，
    根据 pp_method 选择正确的修改函数，输出修改后的 DataFrame 和 segments2。

    返回 (modified_df, segments2_dict)。
    """
    if not wall_thickness_data:
        return pd.DataFrame(columns=['中心线长度(mm)', '壁厚(mm)']), {}

    df = pd.DataFrame(wall_thickness_data)
    if '中心线长度(mm)' not in df.columns or '壁厚(mm)' not in df.columns:
        return df, {}

    thicknesses = df[['中心线长度(mm)', '壁厚(mm)']].copy()
    segments = segments or {}

    D0 = float(solution.get('D0') or 0)
    D1 = float(solution.get('D1') or 0)
    D2 = float(solution.get('D2') or 0)
    D3 = float(solution.get('D3') or 0)
    Dn = float(solution.get('Dn') or 0)
    L1 = float(solution.get('L1') or 0)
    L2 = float(solution.get('L2') or 0)
    L3 = float(solution.get('L3') or 0)

    if D0 == 0 or D1 == 0 or D3 == 0:
        return thicknesses, segments

    method = (pp_method or '').strip()
    try:
        if method in ('Taper+TP抽+扩口', '普通抽+TP抽+Taper+扩口', '加长Taper+TP抽+扩口'):
            mt = Modify_thick(thicknesses, D1, D0, D2, D3, L1, L2, Taper_thick, Dn=Dn, L3=L3)
            return mt.modify_thick(segments)
        elif method in ('仅TP抽', '普通抽+TP抽'):
            mt = Modify_thick_pt(thicknesses, D1, D0, D3)
            return mt.no_modify_thick(segments)
        elif method in ('普通抽+扩口', 'TP抽+扩口'):
            mt = Modify_thick_pt(thicknesses, D1, D0, D3, L1, Dn=Dn)
            return mt.modify_thick(segments)
        elif method == '普通抽+Taper':
            mt = Modify_thick_pt(thicknesses, D1, D0, D3, L1=0, L3=L3, Taper_thick=Taper_thick, D2=D2)
            return mt.modify_thick_taper_only(segments)
        elif method == '普通抽+Taper+扩口':
            mt = Modify_thick_pt(thicknesses, D1, D0, D3, L1, L3, Taper_thick, Dn=Dn, D2=D2)
            return mt.modify_thick(segments)
        else:
            return thicknesses, segments
    except Exception:
        return thicknesses, segments
