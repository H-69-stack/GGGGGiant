"""
Normal Shrink Tube Calculator - Main business logic.
Handles mold parameter calculation for Normal tube shrinking.
"""
import os
import json
import shutil
import zipfile
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment

from core.common.db import MoldDB
from core.common.file_utils import remove_readonly, ensure_dir, export_zip_path, safe_export_stem
from core.common.params import df_to_dict
from core.normal.tube_model import TubeModelClass
from core.normal.molds.normal_molds import (
    ADIE, DIEO, ADBT,
    DC0124_AD03, DC0124_AD01, DC0124_AD02, DC0124_SS01,
    DC0121_AD03, DC0121_AD04, DC0121_AD01, DC0121_AD02, DC0121_SS01,
    DC0125_AD07, DC0125_AD06_F, DC0125_AD06_S, DC0125_SS01,
)

from config import STATIC_DIR, EXPORTS_DIR

# Machine mold configurations
MOLDS = {
    'DC0124': ['裁剪夹模', '缩管模', '抽管芯轴', '抽管退料模'],
    'DC0124 forming': ['裁剪夹模', '成型模', '成型芯轴', '成型退料模', '缩管模', '抽管芯轴', '抽管退料模'],
    'DC0121': ['裁剪夹模1', '裁剪夹模2', '缩管模', '抽管芯轴', '抽管退料模'],
    'DC0121 forming': ['裁剪夹模1', '裁剪夹模2', '成型模', '成型芯轴', '成型退料模', '缩管模', '抽管芯轴', '抽管退料模'],
    'DC0125': ['裁剪夹模', '缩管模', '抽管芯轴', '抽管退料模'],
    'DC0125 forming': ['裁剪夹模', '成型模', '成型芯轴', '成型退料模', '缩管模', '抽管芯轴', '抽管退料模'],
}

# Drawing number mapping per machine type
DRAWING_NUMBERS = {
    'DC0124': {
        '裁剪夹模': 'AD03', '成型芯轴': 'SS01', '成型模': 'DIEO',
        '成型退料模': 'AD02', '缩管模': 'ADIE', '抽管芯轴': 'ADBT', '抽管退料模': 'AD01',
    },
    'DC0121': {
        '裁剪夹模1': 'AD03', '裁剪夹模2': 'AD04', '成型芯轴': 'SS01', '成型模': 'DIEO',
        '成型退料模': 'AD02', '缩管模': 'ADIE', '抽管芯轴': 'ADBT', '抽管退料模': 'AD01',
    },
    'DC0125': {
        '裁剪夹模': 'AD07', '成型芯轴': 'SS01', '成型模': 'DIEO',
        '成型退料模': 'AD06_F', '缩管模': 'ADIE', '抽管芯轴': 'ADBT', '抽管退料模': 'AD06_S',
    },
}

# Parameter description JSON paths
PARAM_DESC_PATH = os.path.join(STATIC_DIR, 'param_configs', 'normal', 'Parameter_Description.json')
PARAM_CALC_PATH = os.path.join(STATIC_DIR, 'param_configs', 'normal', 'Parameter_Calculate_Method.json')


class ShrinkTubeCalculator:
    """Normal shrink tube mold parameter calculator."""

    def __init__(self):
        self.tube_df_params = None
        self.mold_objects = {}
        self.external_params = {'图号': '/', '件数': '/', '车种规格': '/', '设计者': '/'}
        self.db = MoldDB('normal')

        # Load parameter descriptions if available
        self.param_description = {}
        self.param_calc_method = {}
        if os.path.exists(PARAM_DESC_PATH):
            with open(PARAM_DESC_PATH, 'r', encoding='utf-8') as f:
                self.param_description = json.load(f)
        if os.path.exists(PARAM_CALC_PATH):
            with open(PARAM_CALC_PATH, 'r', encoding='utf-8') as f:
                self.param_calc_method = json.load(f)

    def load_tube_from_file(self, file_path):
        """Load tube parameters from DXF file."""
        tube = TubeModelClass(file_path)
        self.tube_df_params = tube.get_params()
        params = df_to_dict(self.tube_df_params)
        self.external_params['车种规格'] = params.get('车种规格', '/')
        return self.tube_df_params

    def load_tube_from_params(self, df_params):
        """Load tube parameters from DataFrame (e.g., from process planning)."""
        self.tube_df_params = df_params
        params = df_to_dict(self.tube_df_params)
        self.external_params['车种规格'] = params.get('车种规格', '/')

    def get_tube_params(self):
        """Get tube parameters as dictionary."""
        return df_to_dict(self.tube_df_params)

    def get_tube_params_df(self):
        """Get tube parameters as DataFrame with descriptions."""
        if self.tube_df_params is None:
            return pd.DataFrame()
        params = df_to_dict(self.tube_df_params)
        rows = []
        for key, val in params.items():
            rows.append({
                '参数': key,
                '值': str(val),
                '描述': self.param_description.get('管件参数', {}).get(key, '')
            })
        return pd.DataFrame(rows)

    def _get_next_graph_number(self, mold_name, machine_type):
        """Generate the next graph number for a mold."""
        drawing_map = DRAWING_NUMBERS.get(machine_type, {})
        base_number = drawing_map.get(mold_name, 'XXXX')
        current_max = self.db.query_max_graph_number(machine_type, base_number)
        next_num = current_max + 1
        suffix = str(next_num).zfill(4)
        return f"{machine_type}-{base_number}{suffix}"

    def calculate(self, user_name, normal_add, mold_list, machine_type):
        """
        Calculate mold parameters.

        Args:
            user_name: Designer name
            normal_add: Whether to add 0.3mm offset
            mold_list: List of mold names to calculate
            machine_type: Machine type (DC0124, DC0121, DC0125)

        Returns:
            dict of mold_name -> mold object
        """
        if self.tube_df_params is None:
            return None

        self.mold_objects = {}

        mold_factory = self._get_mold_factory(machine_type)

        if machine_type == 'DC0124':
            mold_counts = {
                '裁剪夹模': 4, '成型芯轴': 1, '成型模': 1,
                '成型退料模': 1, '缩管模': 1, '抽管芯轴': 1, '抽管退料模': 1,
            }
        elif machine_type == 'DC0121':
            mold_counts = {
                '裁剪夹模1': 2, '裁剪夹模2': 2, '成型芯轴': 1, '成型模': 1,
                '成型退料模': 1, '缩管模': 1, '抽管芯轴': 1, '抽管退料模': 1,
            }
        else:  # DC0125
            mold_counts = {
                '裁剪夹模': 4, '成型芯轴': 2, '成型模': 2,
                '成型退料模': 2, '缩管模': 2, '抽管芯轴': 2, '抽管退料模': 2,
            }

        for mold_name in mold_list:
            if mold_name not in mold_factory:
                continue

            mold = mold_factory[mold_name]()
            graph_number = self._get_next_graph_number(mold_name, machine_type)
            ext_params = {
                '图号': graph_number,
                '件数': mold_counts.get(mold_name, 1),
                '车种规格': self.external_params['车种规格'],
                '设计者': user_name,
            }
            mold.set_params(self.tube_df_params, ext_params, normal_add)
            self.mold_objects[mold_name] = mold

        return self.mold_objects

    def _get_mold_factory(self, machine_type):
        """Return a dict mapping mold names to constructor callables."""
        if machine_type == 'DC0124':
            return {
                '裁剪夹模': DC0124_AD03, '成型模': DIEO, '成型芯轴': DC0124_SS01,
                '成型退料模': DC0124_AD02, '缩管模': ADIE, '抽管芯轴': ADBT, '抽管退料模': DC0124_AD01,
            }
        elif machine_type == 'DC0121':
            return {
                '裁剪夹模1': DC0121_AD03, '裁剪夹模2': DC0121_AD04, '成型模': DIEO,
                '成型芯轴': DC0121_SS01, '成型退料模': DC0121_AD02,
                '缩管模': ADIE, '抽管芯轴': ADBT, '抽管退料模': DC0121_AD01,
            }
        else:  # DC0125
            return {
                '裁剪夹模': DC0125_AD07, '成型模': DIEO, '成型芯轴': DC0125_SS01,
                '成型退料模': DC0125_AD06_F, '缩管模': ADIE, '抽管芯轴': ADBT, '抽管退料模': DC0125_AD06_S,
            }

    def modify_parameter(self, mold_name, key, value):
        """Modify a parameter for a specific mold."""
        if mold_name in self.mold_objects:
            self.mold_objects[mold_name]._modify_parameters(key, value)

    def get_all_params(self):
        """Get all calculated parameters as nested dict."""
        result = {'管件参数': df_to_dict(self.tube_df_params)}
        for mold_name, mold in self.mold_objects.items():
            params = df_to_dict(mold.get_params())
            graph_number = params.get('图号', mold_name)
            result[graph_number] = params
        return result

    def get_molds_params_summary(self):
        """Get all mold parameters for display."""
        result = {}
        for mold_name, mold in self.mold_objects.items():
            params_df = mold.get_params()
            result[mold_name] = params_df.to_dict(orient='records')
        return result

    def save_all(self):
        """Save all mold parameters to database.

        Mirrors the old program: each mold type goes into its own table
        (e.g. 'ADBT', 'ADIE', 'DC0124_AD03') under the machine DB.
        """
        machine_type = self.mold_objects[list(self.mold_objects.keys())[0]].machine_type

        # Table name for each mold: map Chinese name → table suffix
        TABLE_MAP = {
            '裁剪夹模': 'AD03',
            '成型模': 'DIEO',
            '缩管模': 'ADIE',
            '抽管芯轴': 'ADBT',
            '抽管退料模': 'AD01',
            '成型退料模': 'AD02',
            '成型芯轴': 'SS01',
        }
        # DC0125 uses different table names
        if machine_type == 'DC0125':
            TABLE_MAP = {
                '裁剪夹模': 'AD07',
                '成型退料模': 'AD06_F',
                '抽管退料模': 'AD06_S',
                '成型模': 'DIEO',
                '缩管模': 'ADIE',
                '抽管芯轴': 'ADBT',
                '成型芯轴': 'SS01',
            }
        if machine_type == 'DC0121':
            TABLE_MAP = {
                '裁剪夹模1': 'AD03', '裁剪夹模2': 'AD04',
                '成型模': 'DIEO',
                '缩管模': 'ADIE',
                '抽管芯轴': 'ADBT',
                '抽管退料模': 'AD01',
                '成型退料模': 'AD02',
                '成型芯轴': 'SS01',
            }

        for mold_name, mold in self.mold_objects.items():
            params = mold.get_params()
            if params is None or params.empty:
                continue
            # Convert DataFrame rows to flat dict
            params_dict = {}
            for _, row in params.iterrows():
                params_dict[row.iloc[0]] = row.iloc[1]

            table_name = TABLE_MAP.get(mold_name, mold_name)
            self.db.insert_data(machine_type, table_name, params_dict)

    def export_zip(self):
        """Export DXF files and Excel summary as ZIP.

        Mirrors the old ``output_zip_from_cache`` workflow:
          1. Call ``mold.modify_dxf()`` to read the template and substitute values.
          2. Call ``mold.save_dxf()`` to write each modified DXF to the output dir.
          3. Generate a formatted Excel summary.
          4. Bundle everything into a ZIP.
        """
        tube_params = self.get_tube_params()
        vehicle = safe_export_stem(tube_params.get('车种规格'), 'output')
        output_dir = os.path.join(EXPORTS_DIR, vehicle)

        # Clean & recreate output directory
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir, ignore_errors=True)
        os.makedirs(output_dir, exist_ok=True)

        # ── 1. Generate DXF files ────────────────────────────────────────────
        for mold_name, mold in self.mold_objects.items():
            mold_params = mold.get_params()
            mold_params_dict = {}
            if mold_params is not None and not mold_params.empty:
                # get_params() returns Parameter/Value columns
                for _, row in mold_params.iterrows():
                    mold_params_dict[row.iloc[0]] = row.iloc[1]

            graph_no = mold_params_dict.get('图号', mold_name)
            mold.modify_dxf()
            mold.save_dxf(output_dir, graph_no)

        # ── 2. Generate Excel summary ─────────────────────────────────────────
        excel_path = os.path.join(output_dir, '模具参数汇总.xlsx')
        with pd.ExcelWriter(excel_path, engine='openpyxl', mode='w') as writer:
            # Tube params — keep whatever columns the df has
            if self.tube_df_params is not None and not self.tube_df_params.empty:
                self.tube_df_params.to_excel(writer, sheet_name='管件参数', index=False)
            for mold_name, mold in self.mold_objects.items():
                mp = mold.get_params()
                if mp is not None and not mp.empty:
                    mp.to_excel(writer, sheet_name=mold_name, index=False)

        # Format Excel
        wb = load_workbook(excel_path)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            ws.column_dimensions['A'].width = 20
            ws.column_dimensions['B'].width = 30
            for row in ws.iter_rows(min_row=1, min_col=1, max_col=ws.max_column, max_row=ws.max_row):
                for cell in row:
                    cell.alignment = Alignment(horizontal='center', vertical='center')
        wb.save(excel_path)
        wb.close()

        # ── 3. Bundle into ZIP ────────────────────────────────────────────────
        zip_path = export_zip_path(vehicle)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(output_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_dir)
                    zf.write(file_path, arcname)

        return zip_path
