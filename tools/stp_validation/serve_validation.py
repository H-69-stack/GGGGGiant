"""Loopback-only UI/API validation host with an isolated database.

Uses the application's unmodified PP blueprint. It loads persisted outputs of
run_cases for UI inspection; /upload still runs the real analysis. No production
authentication or database is touched by this test host.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    runtime = (args.report / "ui_runtime").resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ['GTAPP_DATA_DIR'] = str(runtime)
    import config
    config.PP_TUBE_DB = str(runtime / ("validation_%s.sqlite" % os.getpid()))
    from flask import Flask
    from api.pp import pp_bp
    from core.pp.thickness_db import save_full_analysis, list_database_tubes
    from core.pp.if_bended import is_tube_bended, calculate_bend_angle
    import numpy as np
    app = Flask('stp_validation')
    app.register_blueprint(pp_bp, url_prefix='/api/pp')
    imported = set()

    @app.before_request
    def sync_results():
        from flask import request
        if request.path != '/api/pp/tubes':
            return
        for f in sorted((args.report / 'measured').glob('*.json')):
            if f.stem in imported:
                continue
            r = json.loads(f.read_text(encoding='utf-8'))
            if 'error' in r:
                continue
            pts = np.array(r['center_line_points'])
            wt = [{'中心线长度(mm)': s['cumulative_length'], '外径周长(mm)': s['perimeter'],
                   '壁厚(mm)': s.get('wall_thickness'), **dict(zip(('x', 'y', 'z'), s['position']))} for s in r['sections_full']]
            payload = dict(r, center_line={'points': pts.tolist(), 'total_length': r['center_line_length']},
                           centerline_length=r['center_line_length'], thickness_stats=r['wall_thickness_stats'],
                           mesh_source='generated', is_bended=bool(is_tube_bended(pts, angle_threshold=2)),
                           bend_angle=float(calculate_bend_angle(pts)), wall_thickness_data=wt,
                           centerline_perimeter_data=wt, segments={}, L4=0)
            payload['mesh_data'] = json.loads((args.report / 'assets' / (f.stem + '_mesh.json')).read_text())
            row, error = save_full_analysis(f.stem + '.stp', f.stem + '.stp',
                str((args.fixtures / (f.stem + '.stp')).resolve()), 'stp', r['validation']['sha256'],
                payload, config.PP_TUBE_DB)
            if error:
                raise RuntimeError(error)
            imported.add(f.stem)
    print('Local validation PP API: http://127.0.0.1:5057', flush=True)
    app.run(host='127.0.0.1', port=5057, threaded=True, use_reloader=False)


if __name__ == '__main__':
    main()
