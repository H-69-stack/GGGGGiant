"""Measurement-review gate: tiny deviations warn through, real defects block."""
import unittest

from api.rfm import check_measurement_quality, measurement_review_summary


def results(count, normal=None, wall=None, rejected=None, acknowledged=False, sections=None):
    quality = {'review_station_count': count}
    if normal is not None:
        quality['max_normal_deviation_deg'] = normal
    if rejected is not None:
        quality['rejected_sections'] = rejected
    if acknowledged:
        quality['review_acknowledged'] = True
    return {
        'analysis_quality': quality,
        'sections_full': sections or [],
    }


def flagged(normal, wall_min=1.4, wall=1.5, residual=0.03):
    return [{
        'measurement_status': 'needs_review',
        'normal_deviation_deg': normal,
        'wall_thickness_min': wall_min,
        'wall_thickness': wall,
        'centroid_residual_mm': residual,
    }]


class MeasurementGateTests(unittest.TestCase):
    def test_single_small_deviation_auto_passes(self):
        info = check_measurement_quality(results(1, 2.1609, sections=flagged(2.1609)))
        self.assertTrue(info.get('auto_passed'))

    def test_five_degree_station_auto_passes(self):
        info = check_measurement_quality(results(1, 4.8, sections=flagged(4.8)))
        self.assertTrue(info.get('auto_passed'))

    def test_over_five_degrees_blocks(self):
        with self.assertRaises(ValueError):
            check_measurement_quality(results(1, 5.2, sections=flagged(5.2)))

    def test_several_or_large_deviations_block(self):
        with self.assertRaises(ValueError) as ctx:
            check_measurement_quality(results(5, 8.0, sections=flagged(8.0)))
        self.assertIn('待复核', str(ctx.exception))
        self.assertEqual(ctx.exception.details['review_station_count'], 5)

    def test_operator_acknowledgement_passes(self):
        info = check_measurement_quality(
            results(1, 2.16, acknowledged=True, sections=flagged(2.16)))
        self.assertTrue(info['acknowledged'])

    def test_rejected_sections_still_block_after_acknowledgement(self):
        with self.assertRaises(ValueError):
            check_measurement_quality(
                results(0, rejected=[{'reason': 'open_contour'}], acknowledged=True))

    def test_thin_wall_never_auto_passes(self):
        with self.assertRaises(ValueError):
            check_measurement_quality(
                results(1, 2.1, sections=flagged(2.1, wall_min=0.02)))

    def test_summary_without_flags(self):
        info = measurement_review_summary(results(0))
        self.assertFalse(info['blocked'])


if __name__ == '__main__':
    unittest.main()
