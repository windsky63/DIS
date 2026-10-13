import unittest
from backend.marker_geometry import marker_geometry

class MarkerGeometryTests(unittest.TestCase):
    def test_long_numbers_and_fonts_do_not_expand_round_frames(self):
        for shape in ('circle', 'diamond', 'split-circle'):
            style = {'shape': shape, 'frameSize': 32, 'fontSize': 12}
            short = marker_geometry({'number': '1', 'secondaryText': '2'}, style)
            for item in ({'number': '123456789012345', 'secondaryText': '2'}, {'number': '1', 'secondaryText': '123456789012345'}):
                long = marker_geometry(item, {**style, 'fontSize': 24})
                self.assertEqual(long[:2], (32, 32))
                if len(item['number']) > 1 or shape == 'split-circle':
                    self.assertLess(long[2], short[2])
            self.assertEqual(marker_geometry({'number': '1'}, {**style, 'frameSize': 48})[:2], (48, 48))

    def test_rectangle_retains_content_width(self):
        style = {'shape': 'rectangle', 'frameSize': 20, 'fontSize': 12}
        short = marker_geometry({'number': '1'}, style)
        long = marker_geometry({'number': '123456789012345'}, style)
        self.assertEqual(short[0], 27)
        self.assertEqual(long[1], 20)
        self.assertGreater(long[0], short[0])
