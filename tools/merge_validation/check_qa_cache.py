"""Time QA-only on this run's measured real part, with full contours retained."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'reports/merge_validation_20260921'
os.environ['GTAPP_DEV_TOOLS'] = '1'
os.environ['GTAPP_DATA_DIR'] = str(OUT/'runtime/cache_probe')
sys.path.insert(0, str(ROOT/'backend'))
from flask import Flask
from api import pp_dev, pp
from core.pp import dev_cache


def main():
    raw = json.loads((OUT/'measured/24TK_1324-2.json').read_text(encoding='utf8'))
    fixture = Path(dev_cache.resolve_fixture_name('24TK_1324-2.stp'))
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == raw['validation']['sha256']
    result, _ = pp_dev._compact_analysis_results(raw, fixture.name, 'A', 25, time.perf_counter())
    sid = 'real_cache_probe'
    sess = pp_dev._put_session(sid, str(fixture), fixture.name, 'A', 25, result)
    payload = dev_cache.persist_from_session(sid, sess, increment_stp=True, increment_qa=True)
    geometry = Path(dev_cache.CACHE_DIR)/payload['geometry_file']
    original_bytes = geometry.read_bytes()
    app = Flask(__name__)
    app.register_blueprint(pp_dev.pp_dev_bp, url_prefix='/api/pp/dev')
    def unexpected_parse(*args, **kwargs):
        raise AssertionError('QA-only must never re-parse the STEP')
    pp_dev.analyze_tube = unexpected_parse
    values = []
    for restored in (False, True):
        if restored:
            pp._pp_sessions.pop(sid)
        started = time.perf_counter()
        response = app.test_client().post('/api/pp/dev/rerun-qa', json={})
        elapsed = time.perf_counter()-started
        assert response.status_code == 200, response.json
        assert response.json['stp_read'] is False
        assert geometry.read_bytes() == original_bytes
        assert len(pp._pp_sessions[sid]['results']['sections_full']) == len(raw['sections_full'])
        values.append({'restore_from_disk': restored, 'http_seconds': elapsed,
                       'qa_ms': response.json['elapsed_ms'], 'stp_read': response.json['stp_read']})
        print(values[-1], flush=True)
    report = {'case': '24TK_1324-2', 'basis': 'this run SHA-256-verified measured JSON',
              'native_sections_retained': len(raw['sections_full']), 'geometry_bytes': geometry.stat().st_size,
              'geometry_unchanged': True, 'runs': values}
    (OUT/'qa_cache_probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
