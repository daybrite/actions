"""Structured reporting contracts, including failure-only and missing-artifact jobs."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('summary', ROOT / '.github/actions/dayscript-report-summary/summary.py')
summary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/dayapp.yml').read_text())


def fixture(run_id='fixture', status='passed', samples=(100000000, 300000000, 200000000), **extra):
    return dict(schema_version=1, run_id=run_id, script='dayscript/demo.yaml', target='macos-appkit',
                status=status, started_at_unix_ms=1700000000000, duration_ms=1234,
                steps=dict(planned=4, passed=2, skipped=1, failed=1, aborted=0),
                memory=dict(metric='physical_footprint', samples=[dict(elapsed_ms=i*1000, bytes=b) for i,b in enumerate(samples)]), **extra)


class DayscriptReportTests(unittest.TestCase):
    def render(self, reports, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            for index, report in enumerate(reports):
                path = Path(root) / str(index) / 'report.json'
                path.parent.mkdir()
                path.write_text(json.dumps(report))
            return summary.render(root, **kwargs)

    def test_statistics_use_samples_and_keep_retries_and_variants_separate(self):
        first = fixture(status='failed', variant='light')
        second = fixture(run_id='retry', variant='dark', samples=(400000000,))
        text = self.render([first, second, first], build_result='failure')
        self.assertEqual(text.count('### dayscript/demo.yaml'), 1)
        self.assertEqual(text.count('| macos-appkit |'), 2)
        self.assertIn('100.00 | 300.00 | 200.00 | 200.00', text)
        self.assertIn('400.00 | 400.00 | 400.00 | 400.00', text)
        self.assertIn('1.23s', text)
        self.assertIn('2 / 1 / 1 / 0', text)

    def test_crash_checkpoint_is_interrupted_and_unaccounted_steps_are_aborted(self):
        report = fixture(status='running')
        report['steps'] = dict(planned=8, passed=2, skipped=1, failed=0, aborted=0)
        text = self.render([report], build_result='failure')
        self.assertIn('interrupted (last checkpoint)', text)
        self.assertIn('2 / 1 / 0 / 5', text)
        self.assertIn('Build jobs: **failure**', text)

    def test_unavailable_end_does_not_become_last_successful_sample(self):
        report = fixture(samples=(100000000, None))
        self.assertEqual(summary.memory_stats(report), [100000000, 100000000, 100000000, None])
        self.assertIn('100.00 | 100.00 | 100.00 | —', self.render([report]))
        self.assertEqual(summary.memory_stats(fixture(samples=())), [None]*4)

    def test_all_jobs_failed_without_artifacts_still_produce_summary(self):
        text = self.render([], build_result='failure', download_result='failure', expected={'include':[{'target':'ios-uikit','label':'iPhone fixture'}]})
        self.assertIn('No per-script reports', text)
        self.assertIn('iPhone fixture', text)
        self.assertIn('Report download: **failure**', text)

    def test_manifest_marks_never_started_script_without_fabricating_measurements(self):
        manifest = dict(schema_version=1, kind='job', context={'target':'ios-uikit'}, status='failure', scripts=['dayscript/crash.yaml'])
        text = self.render([manifest])
        self.assertIn('### dayscript/crash.yaml', text)
        self.assertIn('not run / no report', text)
        self.assertIn('| — | — | — | — |', text)

    def test_build_failure_before_script_preparation_stays_visible_beside_successes(self):
        manifest = dict(schema_version=1, kind='job', context={'target':'android-mdc'}, status='failure', scripts=[])
        text = self.render([fixture(), manifest], build_result='failure')
        self.assertIn('Jobs without script reports', text)
        self.assertIn('android-mdc | No scripts recorded; job result: failure', text)

    def test_untrusted_text_and_malformed_files_cannot_break_summary(self):
        report = fixture(error='<script>bad</script>\n[click](https://example.com)|oops')
        report['script'] = '<img src=x>\n|file|'
        invalid = fixture(run_id='invalid')
        invalid['memory']['samples'] = 'bad'
        text = self.render([report, invalid, {'schema_version':999}])
        self.assertNotIn('<script>', text)
        self.assertNotIn('[click]', text)
        self.assertIn('&lt;img', text)
        self.assertIn('Unreadable reports', text)
        self.assertIn('invalid memory samples', text)

    def test_earlier_job_attempt_does_not_hide_missing_script_on_rerun(self):
        previous = fixture(ci={'run_attempt': '1'})
        manifest = dict(schema_version=1, kind='job', context={'target':'macos-appkit'}, status='failure', run_attempt='2', scripts=['dayscript/demo.yaml'])
        text = self.render([previous, manifest])
        self.assertIn('passed (job attempt 1)', text)
        self.assertIn('not run / no report (job attempt 2)', text)

    def test_failure_manifest_is_written_with_only_report_metadata(self):
        step = next(s for s in WORKFLOW['jobs']['build']['steps'] if s.get('name') == 'Record dayscript job outcome')
        self.assertEqual(step['if'], 'always()')
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, REPORT_DIR=root, REPORT_JOB_STATUS='failure', REPORT_RUN_ATTEMPT='2',
                       REPORT_PLANNED_SCRIPTS='dayscript/one.yaml dayscript/two.yaml',
                       DAY_SCRIPT_REPORT_CONTEXT=json.dumps({'target':'android-mdc', 'profile':'pixel-fixture', 'setup':'not report metadata'}))
            subprocess.run(['bash', '-c', step['run']], env=env, check=True, capture_output=True)
            report = json.loads((Path(root)/'job.json').read_text())
            self.assertEqual(report['status'], 'failure')
            self.assertEqual(report['run_attempt'], '2')
            self.assertEqual(report['scripts'], ['dayscript/one.yaml', 'dayscript/two.yaml'])
            self.assertEqual(report['context'], {'target':'android-mdc', 'profile':'pixel-fixture'})

    def test_summary_is_default_on_and_upload_is_independent_of_success_and_switch(self):
        inputs = WORKFLOW[True]['workflow_call']['inputs']
        self.assertIs(inputs['dayscript_report_summary']['default'], True)
        build = WORKFLOW['jobs']['build']
        self.assertEqual(build['env']['DAY_SCRIPT_REPORT'], '1')
        upload = next(step for step in build['steps'] if step.get('name') == 'Upload dayscript reports')
        self.assertEqual(upload['if'], 'always()')
        self.assertIn('matrix.shots_artifact', upload['with']['name'])
        self.assertTrue(upload['with']['overwrite'])
        self.assertIn('github.run_attempt', upload['with']['name'])
        job = WORKFLOW['jobs']['dayscript-reports']
        self.assertIn('build', job['needs'])
        self.assertIn('always()', job['if'])
        self.assertIn('inputs.dayscript_report_summary', job['if'])
        action = yaml.safe_load((ROOT / '.github/actions/dayscript-report-summary/action.yml').read_text())
        self.assertIs(action['runs']['steps'][0]['continue-on-error'], True)
        self.assertEqual(action['runs']['steps'][1]['if'], 'always()')


if __name__ == '__main__':
    unittest.main()
