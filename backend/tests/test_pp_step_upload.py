"""Exercise the real PP upload/status/data/preview contract with a known tube."""
import io
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class StepUploadTests(unittest.TestCase):
    def test_upload_native_measurements_and_binary_stl_preview(self):
        from brep_fixtures import tube, write_step
        import numpy as np
        import trimesh
        from flask import Flask
        import api.pp as pp
        with tempfile.TemporaryDirectory() as tmp:
            original_uploads = pp.PP_UPLOADS_DIR
            pp.PP_UPLOADS_DIR = tmp
            sid = None
            try:
                file = str(Path(tmp) / 'fixture.step')
                write_step(tube(12,10,60.65), file)
                app = Flask(__name__)
                app.register_blueprint(pp.pp_bp, url_prefix='/api/pp')
                client = app.test_client()
                options = {'start_trim_mm': 6, 'end_trim_mm': 7}
                response = client.post('/api/pp/upload', data={
                    'file': (io.BytesIO(Path(file).read_bytes()), 'circle.step'),
                    'end_treatment': json.dumps(options),
                })
                self.assertEqual(response.status_code, 200)
                sid = response.json['session_id']
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    status = client.get('/api/pp/upload-status', query_string={'id': sid}).json
                    if status.get('done') or (status.get('progress') or {}).get('done'):
                        break
                    time.sleep(.05)
                info_response = client.get('/api/pp/session-info', query_string={'id': sid})
                self.assertEqual(info_response.status_code, 200, info_response.json)
                info = info_response.json
                self.assertEqual(info['analysis_quality']['engine'], 'native_brep_v1')
                end_info = info['analysis_quality']['end_treatment']
                self.assertEqual(end_info['options']['start_trim_mm'], 6)
                self.assertEqual(end_info['options']['end_trim_mm'], 7)
                self.assertAlmostEqual(end_info['reference_length_mm'], 60.65, places=4)
                self.assertGreaterEqual(end_info['ends'][0]['trim_length_mm'], 6)
                self.assertGreaterEqual(end_info['ends'][1]['trim_length_mm'], 7)
                self.assertEqual(info['sections_full'][0]['measurement_status'], 'extrapolated')
                self.assertAlmostEqual(info['sections_full'][-1]['cumulative_length'], 60.65, places=4)
                self.assertAlmostEqual(info['volume'], np.pi * (144 - 100) * 60.65, places=3)
                self.assertLess(abs(info['thickness_stats']['median_mm'] - 2), .004)
                self.assertGreater(len(info['center_line']['points']), 50)
                sampling = client.get('/api/pp/thickness-data/' + sid).json
                self.assertGreater(len(sampling['samples']), 50)
                self.assertGreater(max(abs(s['z']) for s in sampling['samples']), 50)
                # Latest upstream requires an explicit automatic/manual profile choice.
                self.assertEqual(client.post('/api/pp/apply-to-optimization', json={'session_id': sid}).status_code, 409)
                self.assertEqual(client.post('/api/pp/apply-to-optimization', json={'session_id': sid, 'profile_choice': 'auto'}).status_code, 200)
                # PP optimization intentionally no longer blocks on review flags
                # (quality warnings live on the analysis page); the TP/RFM gate is
                # covered by tests/test_measurement_gate.py.
                for endpoint in ('apply-to-tab3',):
                    allowed = client.post('/api/pp/' + endpoint, json={'session_id': sid, 'pp_method': 'Taper+TP抽+扩口'})
                    self.assertEqual(allowed.status_code, 200)
                preview = client.get('/api/pp/stl-file/' + sid)
                self.assertEqual(preview.status_code, 200)
                triangles = struct.unpack('<I', preview.data[80:84])[0]
                self.assertGreater(triangles, 0)
                self.assertEqual(len(preview.data), 84 + 50 * triangles)
                mesh = trimesh.load(io.BytesIO(preview.data), file_type='stl')
                self.assertGreater(len(mesh.vertices), 20)
            finally:
                pp.PP_UPLOADS_DIR = original_uploads
                if sid:
                    pp._pp_sessions.pop(sid, None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
