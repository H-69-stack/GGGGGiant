"""
TP Shrink Tube Calculator - Main business logic for TP tube module.
"""
import csv
import os
import json
import shutil
import zipfile
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment

from core.common.db import MoldDB
from core.common.file_utils import ensure_dir, export_zip_path, safe_export_stem
from core.common.params import df_to_dict
from core.tp.molds.tp_molds import (
    DC0128_AD01, DC0128_AD01_, DC0128_ADIE, DC0128_ADIE_,
    DC0128_ADBT, DC0128_ADBT_, DC0128_AD04,
)

from config import STATIC_DIR, EXPORTS_DIR

# Mold configurations based on conditions
TP_MOLDS = {
    'apply_taper_true': ['裁剪夹模1', '裁剪夹模2', '抽管退料模', '抽管芯轴', '抽套模'],
    'apply_taper_false_special_thinkness_true': [
        '裁剪夹模1', '裁剪夹模2', '抽管退料模', '抽管芯轴', '抽套模', '成型抽套模', '成型抽管芯轴'
    ],
    'apply_taper_false_special_thinkness_false': ['裁剪夹模1', '裁剪夹模2', '抽管退料模', '抽管芯轴', '抽套模'],
}

DRAWING_NUMBERS = {
    # 裁剪夹模1/2 共用 AD01 前缀，同批计算内按序号递增（AD010001、AD010002…）
    '裁剪夹模1': 'AD01', '裁剪夹模2': 'AD01',
    '抽套模': 'ADIE', '成型抽套模': 'ADIE_',
    '抽管芯轴': 'ADBT', '成型抽管芯轴': 'ADBT_',
    '抽管退料模': 'AD04',
}

PARAM_DESC_PATH = os.path.join(STATIC_DIR, 'param_configs', 'tp', 'Parameter_Description.json')
PARAM_CALC_PATH = os.path.join(STATIC_DIR, 'param_configs', 'tp', 'Parameter_Calculate_Method.json')


class TPShrinkTubeCalculator:
    """TP shrink tube mold parameter calculator."""

    def __init__(self):
        self.tube_df_params = None
        self.mold_objects = {}
        self.db = MoldDB('tp')

        self.param_description = {}
        self.param_calc_method = {}
        if os.path.exists(PARAM_DESC_PATH):
            with open(PARAM_DESC_PATH, 'r', encoding='utf-8') as f:
                self.param_description = json.load(f)
        if os.path.exists(PARAM_CALC_PATH):
            with open(PARAM_CALC_PATH, 'r', encoding='utf-8') as f:
                self.param_calc_method = json.load(f)

    def load_tube_from_params(self, df_params):
        """Load tube parameters from DataFrame."""
        self.tube_df_params = df_params

    def get_tube_params(self):
        """Get tube parameters as dictionary."""
        if self.tube_df_params is None:
            return {}
        return df_to_dict(self.tube_df_params)

    def _get_next_graph_number(self, mold_name):
        """Generate the next graph number.

        同一 DRAWING 前缀在单次 calculate 内递增，避免裁剪夹模1/2 都取到库中的 max+1。
        """
        base_number = DRAWING_NUMBERS.get(mold_name, 'XXXX')
        if not hasattr(self, '_graph_counters') or self._graph_counters is None:
            self._graph_counters = {}
        if base_number not in self._graph_counters:
            self._graph_counters[base_number] = self.db.query_max_graph_number('DC0128', base_number)
        self._graph_counters[base_number] += 1
        next_num = self._graph_counters[base_number]
        return f"{base_number}{str(next_num).zfill(4)}"

    def calculate(self, user_name, apply_taper, mold_list, special_thickness, interference_length=0):
        """
        Calculate TP mold parameters.

        Args:
            user_name: Designer name
            apply_taper: Whether taper is applied
            mold_list: List of mold names to calculate
            special_thickness: Whether special thickness applies
            interference_length: Interference length for mandrel
        """
        if self.tube_df_params is None:
            return None

        self.mold_objects = {}
        self._graph_counters = {}
        tube_params = self.get_tube_params()

        ext_params = {
            '车种规格': tube_params.get('车种规格', '/'),
            '设计者': user_name,
            '图号': '',
        }

        mold_factory = {
            '裁剪夹模1': DC0128_AD01,
            '裁剪夹模2': DC0128_AD01_,
            '抽套模': DC0128_ADIE,
            '成型抽套模': DC0128_ADIE_,
            '抽管芯轴': lambda: DC0128_ADBT(interference_length),
            '成型抽管芯轴': DC0128_ADBT_,
            '抽管退料模': DC0128_AD04,
        }

        for mold_name in mold_list:
            if mold_name not in mold_factory:
                continue

            ext_params['图号'] = self._get_next_graph_number(mold_name)
            factory = mold_factory[mold_name]
            mold = factory() if callable(factory) and not isinstance(factory, type) else factory()
            mold.set_params(self.tube_df_params, ext_params)
            self.mold_objects[mold_name] = mold

        return self.mold_objects

    def modify_parameter(self, mold_name, key, value):
        """Modify a parameter for a specific mold."""
        if mold_name in self.mold_objects:
            self.mold_objects[mold_name]._modify_parameters(key, value)

    def get_all_params(self):
        """Get all calculated parameters."""
        result = {'管件参数': self.get_tube_params()}
        for mold_name, mold in self.mold_objects.items():
            params = df_to_dict(mold.get_params())
            result[params.get('图号', mold_name)] = params
        return result

    def get_molds_params_summary(self):
        """Get mold parameters summary for display."""
        result = {}
        for mold_name, mold in self.mold_objects.items():
            result[mold_name] = mold.get_params().to_dict(orient='records')
        return result

    def save_all(self):
        """Save all parameters to database.

        Extracts table name from graph number: 'DC0128-ADBT0001' -> 'ADBT'.
        """
        for mold_name, mold in self.mold_objects.items():
            params = mold.get_params()
            if params is None or params.empty:
                continue
            params_dict = {}
            for _, row in params.iterrows():
                params_dict[row.iloc[0]] = row.iloc[1]
            graph_number = params_dict.get('图号', '')
            if not graph_number:
                continue
            # 'DC0128-ADBT0001' -> 'ADBT'
            parts = graph_number.split('-')
            table_name = parts[1][:-4] if len(parts) > 1 else graph_number[:-4]
            self.db.insert_data('DC0128', table_name, params_dict)

    def export_zip(self, profile_data=None):
        """Export DXF files, Excel summary, and line_points.csv as ZIP.

        Mirrors the old ``output_zip_from_cache`` workflow.
        """
        tube_params = self.get_tube_params()
        vehicle = safe_export_stem(tube_params.get('车种规格'), 'tp_output')
        output_dir = os.path.join(EXPORTS_DIR, vehicle)

        # Clean & recreate
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
        os.makedirs(output_dir, exist_ok=True)

        # ── 1. Generate DXF files ────────────────────────────────────────────
        for mold_name, mold in self.mold_objects.items():
            mold_params = mold.get_params()
            mold_params_dict = {}
            if mold_params is not None and not mold_params.empty:
                for _, row in mold_params.iterrows():
                    mold_params_dict[row.iloc[0]] = row.iloc[1]
            graph_no = mold_params_dict.get('图号', mold_name)
            mold.modify_dxf()
            mold.save_dxf(output_dir, graph_no)

        # ── 2. Generate Excel summary ─────────────────────────────────────────
        excel_path = os.path.join(output_dir, '模具参数汇总.xlsx')
        with pd.ExcelWriter(excel_path, engine='openpyxl', mode='w') as writer:
            if self.tube_df_params is not None and not self.tube_df_params.empty:
                self.tube_df_params.to_excel(writer, sheet_name='管件参数', index=False)
            for mold_name, mold in self.mold_objects.items():
                mp = mold.get_params()
                if mp is not None and not mp.empty:
                    mp.to_excel(writer, sheet_name=mold_name, index=False)

        # Format
        wb = load_workbook(excel_path)
        for ws in wb.worksheets:
            ws.column_dimensions['A'].width = 20
            ws.column_dimensions['B'].width = 30
            for row in ws.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(horizontal='center', vertical='center')
        wb.save(excel_path)
        wb.close()

        # ── 3. Generate line_points.csv (mirrors old output_gen_3d) ───────────
        if profile_data:
            profile_df = pd.DataFrame(profile_data)
            if not profile_df.empty and 'X' in profile_df.columns and 'Y' in profile_df.columns:
                # Process points like the old program: reverse if needed
                points = profile_df[['X', 'Y']].values.tolist()
                if len(points) >= 2:
                    point0, pointn = points[0], points[-1]
                    if point0[1] > pointn[1] and point0[0] > pointn[0]:
                        T_D = point0[1] * 2
                        T_d = pointn[1] * 2
                        T_L = int(abs(point0[0] - pointn[0])) + 1
                        rev = points[::-1][:-1]
                        rev.append([T_L + 9, T_D / 2])
                        points = rev[::-1]
                csv_path = os.path.join(output_dir, 'line_points.csv')
                with open(csv_path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['X', 'Y'])
                    for pt in points:
                        writer.writerow(pt)

        # ── 4. Bundle into ZIP ────────────────────────────────────────────────
        zip_path = export_zip_path(vehicle)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    fp = os.path.join(root, file)
                    zf.write(fp, os.path.relpath(fp, output_dir))
        return zip_path
