"""Loopback-only merged production API host with isolated fixture databases."""
import json
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'reports/merge_validation_20260921'
os.environ['GTAPP_DATA_DIR'] = str(OUT/'runtime')
sys.path.insert(0, str(ROOT/'backend'))
import config
config.PP_TUBE_DB = str(OUT/'runtime/tubes.db')
config.PP_DESIGN_DB = str(OUT/'runtime/designs.db')
from flask import Flask, jsonify, send_from_directory
from api.pp import pp_bp
from api.pp_qa import pp_qa_bp
from api.rfm import rfm_bp
from api.tp import tp_bp
app = Flask('merge_review')
app.register_blueprint(pp_bp, url_prefix='/api/pp')
app.register_blueprint(pp_qa_bp, url_prefix='/api/pp')
app.register_blueprint(rfm_bp)
app.register_blueprint(tp_bp, url_prefix='/api/tp')


@app.get('/validation/manifest')
def manifest():
    records = [json.loads(p.read_text(encoding='utf8')) for p in sorted(OUT.glob('*.json')) if p.stem.startswith(('24TK_', 'H0A2'))]
    return jsonify(records)


@app.get('/validation/models/<name>')
def model(name):
    return send_from_directory(OUT/'models', name)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5058, use_reloader=False, threaded=True)
