from pathlib import Path
import tempfile
import unittest

import fitz

from backend.reference_rules import normalize_reference_rules, reference_rule_for_slot, extract_contractor_callouts
from backend.engine import _effective_symbol_config, _reference_pages, analyze_documents


class ReferenceRuleTests(unittest.TestCase):
    def page(self):
        document = fitz.open()
        page = document.new_page(width=400, height=300)
        self.addCleanup(document.close)
        return page

    def mark(self, page, center=(60, 60), label='12', line=((85, 60), (145, 90)), red=True):
        page.draw_circle(center, 12, color=(1, 0, 0), width=.8)
        page.insert_text((center[0] - 6, center[1] + 3), label, fontsize=8)
        page.draw_line(*line, color=(1, 0, 0) if red else (0, 0, 0), width=.8)

    def options(self, **values):
        return {**normalize_reference_rules({})['referenceRules'][1]['options'], **values}

    def test_detached_red_circle_leader_endpoint_is_authoritative_without_pipe_or_dot(self):
        page = self.page()
        self.mark(page, label='W12')
        found = extract_contractor_callouts(page, self.options())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].label, 'W12')
        self.assertEqual(found[0].weld_point, (145., 90.))
        self.assertIn('authoritative-endpoint', found[0].extraction_method)

    def test_black_leader_is_allowed_and_red_only_is_configurable(self):
        page = self.page()
        self.mark(page, red=False)
        self.assertEqual(len(extract_contractor_callouts(page, self.options())), 1)
        self.assertEqual(extract_contractor_callouts(page, self.options(leaderColor='red')), [])

    def test_segmented_bent_leader_follows_short_gap(self):
        page = self.page()
        self.mark(page, line=((85, 60), (120, 60)))
        page.draw_line((122, 60), (150, 80), color=(1, 0, 0), width=.8)
        self.assertEqual(extract_contractor_callouts(page, self.options())[0].weld_point, (150., 80.))

    def test_gap_and_number_pattern_are_independently_configurable(self):
        page = self.page()
        self.mark(page, label='W12')
        self.assertEqual(extract_contractor_callouts(page, self.options(maximumFrameGap=5)), [])
        self.assertEqual(extract_contractor_callouts(page, self.options(labelPattern=r'F\d+')), [])
        self.assertEqual(len(extract_contractor_callouts(page, self.options(labelPattern=r'W\d+'))), 1)

    def test_black_circle_red_rectangle_and_circle_without_leader_are_not_callouts(self):
        page = self.page()
        page.draw_circle((60, 60), 12, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(100, 48, 124, 72), color=(1, 0, 0))
        page.draw_circle((180, 60), 12, color=(1, 0, 0))
        for x in (60, 112, 180):
            page.insert_text((x - 4, 63), '12', fontsize=8)
        self.assertEqual(extract_contractor_callouts(page, self.options()), [])

    def test_integrated_frame_and_leader_path_and_page_rotation(self):
        page = self.page()
        shape = page.new_shape()
        shape.draw_circle((60, 60), 12)
        shape.draw_line((80, 60), (150, 100))
        shape.finish(color=(1, 0, 0), width=.8)
        shape.commit()
        page.insert_text((54, 63), '12', fontsize=8)
        page.set_rotation(90)
        found = extract_contractor_callouts(page, self.options())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].weld_point, (200., 150.))

    def test_multiple_contractors_have_separate_rules_and_slot_assignments(self):
        config = normalize_reference_rules({'referenceRules': [
            {'id': 'ep3d', 'kind': 'ep3d'},
            {'id': 'unit-a', 'kind': 'contractor', 'options': {'labelPattern': r'A\d+'}},
            {'id': 'unit-b', 'kind': 'contractor', 'options': {'labelPattern': r'B\d+'}},
        ], 'referenceRuleAssignments': {'0': 'unit-a', '1': 'unit-b'}})
        self.assertEqual(reference_rule_for_slot(config, 0)['id'], 'unit-a')
        self.assertEqual(reference_rule_for_slot(config, 1)['id'], 'unit-b')
        self.assertEqual(reference_rule_for_slot(config, 2)['id'], 'ep3d')

    def test_invalid_rule_config_is_rejected(self):
        for config in [
            {'referenceRules': []},
            {'referenceRuleAssignments': {'0': 'missing'}},
            {'referenceRules': [{'id': 'x', 'kind': 'contractor', 'options': {'labelPattern': '['}}]},
            {'referenceRules': [{'id': 'x', 'kind': 'contractor', 'options': {'maximumFrameGap': float('nan')}}]},
            {'referenceRules': [{'id': 'x', 'kind': 'contractor', 'options': {'minimumFrameDiameter': 100, 'maximumFrameDiameter': 10}}]},
        ]:
            with self.subTest(config=config), self.assertRaises(ValueError):
                _effective_symbol_config(config)

    def test_reference_adapter_and_engine_snapshot_keep_rule_identity(self):
        page = self.page()
        self.mark(page)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reference-003__contractor.pdf'
            page.parent.save(path)
            config = _effective_symbol_config({'referenceRuleAssignments': {'2': 'contractor'}})
            rules = config['referenceRules']
            reference = _reference_pages(path, rule=rules[1])[0]
            self.assertEqual(reference['callouts'][0].label, '12')
            self.assertEqual(reference['component_callouts'], [])
            result = analyze_documents(path, reference_pdf=path, symbol_config=config)
            hints = result['ep3dReferenceInventory']['documents'][0]['pages'][0]
            self.assertEqual(hints['referenceRule']['id'], 'contractor')
            self.assertEqual(hints['items'][0]['point'], [145., 90.])
            self.assertEqual(result['symbolConfig']['referenceRuleAssignments']['2'], 'contractor')

    def test_empty_contractor_page_is_retained_with_actionable_warning(self):
        page = self.page()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'contractor.pdf'
            page.parent.save(path)
            rule = normalize_reference_rules({})['referenceRules'][1]
            references = _reference_pages(path, rule=rule)
            self.assertEqual(len(references), 1)
            self.assertEqual(references[0]['callouts'], [])
            self.assertIn('OCR', references[0]['extractionWarnings'][0])
