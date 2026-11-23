"""TP draw die: validated closed meridian revolved as one hollow CAD solid."""
import os
import numpy as np
from config import DATA_DIR
from core.common.solid_geometry import revolve_profile, mesh_from_shape, step_bytes
from core.rfm.processors import perimeter_series

_SMALL_END_OUTER_R = 47.5
_LARGE_END_OUTER_R = 62.5


def _build_mold_polygon(T_L, T_d, T_D, profile_z, profile_r):
    # Input station zero is the large end. In the machine drawing the small
    # end is at Z=9, so reverse and translate ONCE, then walk the exterior.
    z = list((T_L+9-np.asarray(profile_z))[::-1])
    r = list(np.asarray(profile_r)[::-1])
    z += [T_L+27, T_L+27, T_L+19, T_L+19, 0, 0, 5]
    r += [T_D/2, 62.5, 62.5, 47.5, 47.5, T_d/2, T_d/2]
    return np.array(z), np.array(r)


def select_tp_segment(rows, large_station, small_station, large_diameter, small_diameter):
    """Clip by axial interval, interpolate exact ends, normalize large→small."""
    ordered, s, p = perimeter_series(rows)
    offset = float(ordered[0]['中心线长度(mm)'])
    start, end = float(large_station)-offset, float(small_station)-offset
    if abs(end-start) < .01 or min(start, end) < -1e-6 or max(start, end) > s[-1]+1e-6:
        raise ValueError('TP 段端点超出型线范围或段长为零')
    inside = s[(s > min(start, end)) & (s < max(start, end))]
    stations = np.r_[start, inside if end > start else inside[::-1], end]
    perimeters = np.interp(stations, s, p)
    perimeters[0], perimeters[-1] = float(large_diameter)*np.pi, float(small_diameter)*np.pi
    return [{'中心线长度(mm)': abs(float(z-start)), '外径周长(mm)': float(v)} for z, v in zip(stations, perimeters)]


class TPMoldProcessor:
    def __init__(self):
        self.profile_data = []
        self.T_L = self.T_D = self.T_d = None
        self.mesh = self.shape = None
        self._rings_3d = None
        self.resample_step_mm = 1.

    def set_profile_data(self, profile_data):
        self.profile_data = list(profile_data)

    def set_tp_params(self, T_L, T_D, T_d):
        self.T_L, self.T_D, self.T_d = map(float, (T_L, T_D, T_d))

    def _extract_profile(self):
        rows, z, per = perimeter_series(self.profile_data)
        if not np.all(np.isfinite([self.T_L, self.T_D, self.T_d])) or not 0 < self.T_d <= self.T_D < 95:
            raise ValueError('固定 TP 模体外径为 95/125 mm；型腔直径必须满足 0 < 小径 ≤ 大径 < 95 mm')
        if abs(z[-1]-self.T_L) > .001:
            raise ValueError('型线长度与 TP 段长度不一致，请先裁取 TP 段')
        if abs(per[-1]/np.pi-self.T_D) < abs(per[0]/np.pi-self.T_D):
            z, per = self.T_L-z[::-1], per[::-1]
        if abs(per[0]/np.pi-self.T_D) > .05 or abs(per[-1]/np.pi-self.T_d) > .05:
            raise ValueError('型线端部直径与 TP 大小径不一致')
        if np.max(per/np.pi) >= 95 or np.min(per) <= 0:
            raise ValueError('型线超出固定 TP 模体尺寸')
        return z, per/(2*np.pi)

    def generate_model(self):
        z, r = self._extract_profile()
        self.profile_zr = _build_mold_polygon(self.T_L, self.T_d, self.T_D, z, r)
        self.shape = revolve_profile(*self.profile_zr)
        self.mesh = mesh_from_shape(self.shape)
        theta = np.arange(96)*2*np.pi/96
        self._rings_3d = [np.column_stack((ri*np.cos(theta), ri*np.sin(theta), np.full(96, zi)))
                          for zi, ri in zip(*self.profile_zr)]
        return self.mesh

    def get_step_bytes(self):
        if self.shape is None:
            raise ValueError('请先生成模型')
        return step_bytes(self.shape)

    def get_stl_bytes(self):
        if self.mesh is None:
            raise ValueError('请先生成模型')
        return self.mesh.export(file_type='stl')

    def export_step(self, output_path=None):
        path = output_path or os.path.join(DATA_DIR, 'tp_mold', 'TPDrawMold.step')
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'wb') as stream:
            stream.write(self.get_step_bytes())
        return path

    def export_stl(self, output_path=None):
        path = output_path or os.path.join(DATA_DIR, 'tp_mold', 'TPDrawMold.stl')
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'wb') as stream:
            stream.write(self.get_stl_bytes())
        return path
