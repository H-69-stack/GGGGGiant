"""
Taper Calculator - Main business logic for Taper module.
"""
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
from core.taper.molds.taper_molds import (
    EC0120_B000, EC0121_B000, C000, J000, E000, I000, H000
)

from config import EXPORTS_DIR

TAPER_MOLDS = {
    "EC0120": ["Taper模两瓣", "定位杆", "夹模", "后定位模", "内定位模", "拉杆模"],
    "EC0121": ["Taper模四瓣", "定位杆", "夹模"]
}

DRAWING_NUMBERS = {
    'Taper模两瓣': 'B000', 'Taper模四瓣': 'B000',
    '定位杆': 'C000', '夹模': 'J000',
    '后定位模': 'E000', '内定位模': 'I000', '拉杆模': 'H000',
}

PARAM_DESC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'param_configs', 'taper', 'Parameter_Description.json')
PARAM_CALC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'static', 'param_configs', 'taper', 'Parameter_Calculate_Method.json')


class TaperCalculator:
    """Taper mold parameter calculator."""

    def __init__(self):
        self.tube_df_params = None
        self.mold_objects = {}
        self.db = MoldDB('taper')
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

    def _get_next_graph_number(self, mold_name, machine_type):
        """Generate the next graph number."""
        base_number = DRAWING_NUMBERS.get(mold_name, 'X000')
        current_max = self.db.query_max_graph_number(machine_type, base_number)
        next_num = current_max + 1
        return f"{machine_type}-{base_number}{str(next_num).zfill(4)}"

    def calculate(self, user_name, machine_type, mold_list, tp_length=0, line_data=None):
        """
        Calculate Taper mold parameters.

        Args:
            user_name: Designer name
            machine_type: EC0120 or EC0121
            mold_list: List of mold names
            tp_length: TP length for taper mold
            line_data: Profile line data
        """
        if self.tube_df_params is None:
            return None

        self.mold_objects = {}
        tube_params = self.get_tube_params()

        ext_params = {
            '车种规格': tube_params.get('车种规格', '/'),
            '设计者': user_name,
            '图号': '',
        }

        for mold_name in mold_list:
            ext_params['图号'] = self._get_next_graph_number(mold_name, machine_type)

            if machine_type == 'EC0120':
                if mold_name == 'Taper模两瓣':
                    mold = EC0120_B000(tp_length, line_data)
                elif mold_name == '定位杆':
                    mold = C000()
                elif mold_name == '夹模':
                    mold = J000(machine_type)
                elif mold_name == '后定位模':
                    mold = E000()
                elif mold_name == '内定位模':
                    mold = I000()
                elif mold_name == '拉杆模':
                    mold = H000()
                else:
                    continue
            else:  # EC0121
                if mold_name == 'Taper模四瓣':
                    mold = EC0121_B000(tp_length, line_data)
                elif mold_name == '定位杆':
                    mold = C000()
                elif mold_name == '夹模':
                    mold = J000(machine_type)
                else:
                    continue

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
        """Get mold parameters for display."""
        result = {}
        for mold_name, mold in self.mold_objects.items():
            result[mold_name] = mold.get_params().to_dict(orient='records')
        return result

    def save_all(self):
        """Save all to database.

        Extracts machine from graph number prefix and table name from suffix.
        """
        for mold_name, mold in self.mold_objects.items():
            if hasattr(mold, '_compute_line_params'):
                mold._compute_line_params()
            params = mold.get_params()
            if params is None or params.empty:
                continue
            params_dict = {}
            for _, row in params.iterrows():
                params_dict[row.iloc[0]] = row.iloc[1]
            graph_number = params_dict.get('图号', '')
            if not graph_number:
                continue
            # 'EC0120-B0000001' -> machine='EC0120', table='B000'
            parts = graph_number.split('-')
            machine = parts[0] if parts else 'EC0120'
            table_name = parts[1][:-4] if len(parts) > 1 else graph_number[:-4]
            self.db.insert_data(machine, table_name, params_dict)

    def export_zip(self):
        """Export DXF files and Excel summary as ZIP.

        Mirrors the old ``output_zip_from_cache`` workflow.
        """
        tube_params = self.get_tube_params()
        vehicle = safe_export_stem(tube_params.get('车种规格'), 'taper_output')
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

        wb = load_workbook(excel_path)
        for ws in wb.worksheets:
            ws.column_dimensions['A'].width = 20
            ws.column_dimensions['B'].width = 30
            for row in ws.iter_rows():
                for cell in row:
                    cell.alignment = Alignment(horizontal='center', vertical='center')
        wb.save(excel_path)
        wb.close()

        # ── 3. Bundle into ZIP ────────────────────────────────────────────────
        zip_path = export_zip_path(vehicle, '_taper')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    fp = os.path.join(root, file)
                    zf.write(fp, os.path.relpath(fp, output_dir))
        return zip_path
