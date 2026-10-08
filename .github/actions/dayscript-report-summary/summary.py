"""Render versioned dayscript JSON, never app logs, as a bounded GitHub summary."""
import html
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path


def cell(value):
    text = '—' if value is None or value == '' else str(value)
    # Artifact contents are untrusted display data, never Markdown/HTML or shell source.
    return html.escape(text.replace('\\', '\\\\').replace('|', '&#124;').replace('\r', ' ').replace('\n', ' '), quote=False).replace('&amp;#124;', '&#124;').replace('`', '&#96;').replace('[', '&#91;').replace(']', '&#93;').replace('*', '&#42;').replace('_', '&#95;')


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def memory_stats(report):
    memory = report.get('memory') or {}
    samples = memory.get('samples') or []
    values = [s.get('bytes') for s in samples if isinstance(s, dict) and number(s.get('bytes'))]
    # Recompute from source samples; never average per-run averages or mix platform metrics.
    end = samples[-1].get('bytes') if samples and isinstance(samples[-1], dict) else None
    return [min(values), max(values), sum(values)/len(values), end] if values else [None]*4


def mb(value):
    return f'{value/1_000_000:.2f}' if number(value) else '—'


def identity(context):
    return tuple(str(context.get(k) or '') for k in ('target', 'device_slug', 'flavor'))


def validate(report):
    for key in ('target', 'device_slug', 'variant', 'locale', 'flavor', 'status'):
        if report.get(key) is not None and not isinstance(report[key], str):
            raise ValueError(f'invalid {key}')
    for key in ('hardware', 'steps', 'memory', 'ci'):
        if report.get(key) is not None and not isinstance(report[key], dict):
            raise ValueError(f'invalid {key}')
    hardware = report.get('hardware') or {}
    for key in ('host', 'device', 'ci_profile'):
        if hardware.get(key) is not None and not isinstance(hardware[key], dict):
            raise ValueError(f'invalid hardware.{key}')
    samples = report.get('memory', {}).get('samples', [])
    if not isinstance(samples, list) or any(not isinstance(s, dict) or (s.get('bytes') is not None and not number(s['bytes'])) for s in samples):
        raise ValueError('invalid memory samples')
    for key, value in report.get('steps', {}).items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f'invalid step count {key}')


def render(root, expected=None, build_result='unknown', download_result='success'):
    groups = {}
    manifests = []
    invalid = []
    seen_ids = set()
    for path in sorted(Path(root).rglob('*.json')):
        try:
            if path.is_symlink() or path.stat().st_size > 20_000_000:
                raise ValueError('report is a symlink or exceeds 20 MB')
            report = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(report, dict) or report.get('schema_version') != 1:
                raise ValueError('unsupported report schema')
            if report.get('kind') == 'job':
                if not isinstance(report.get('context'), dict) or not isinstance(report.get('scripts'), list):
                    raise ValueError('invalid job manifest')
                manifests.append(report)
                continue
            if not isinstance(report.get('script'), str) or not isinstance(report.get('steps'), dict) or not isinstance(report.get('memory'), dict):
                raise ValueError('missing script, steps or memory')
            validate(report)
            run_id = report.get('run_id')
            if not isinstance(run_id, str):
                raise ValueError('missing run_id')
            if run_id in seen_ids:
                continue
            seen_ids.add(run_id)
            groups.setdefault(report['script'], []).append(report)
        except (OSError, ValueError, TypeError) as error:
            invalid.append(f'{path.name}: {error}')

    available = {identity(r) for reports in groups.values() for r in reports}
    missing = []
    for manifest in manifests:
        context = manifest.get('context') or {}
        available.add(identity(context))
        planned = manifest.get('scripts') or []
        if not isinstance(planned, list):
            planned = []
        if not planned and not any(identity(r) == identity(context) for reports in groups.values() for r in reports):
            missing.append({**context, 'report_reason': 'No scripts recorded; job result: ' + str(manifest.get('status', 'unknown'))})
        for script in planned:
            if not isinstance(script, str):
                continue
            if any(identity(r) == identity(context) and (not manifest.get('run_attempt') or (r.get('ci') or {}).get('run_attempt') == manifest['run_attempt']) for r in groups.get(script, [])):
                continue
            groups.setdefault(script, []).append({**context, 'status': 'not run / no report', 'job_result': manifest.get('status'), 'hardware': {'ci_profile': context}, 'ci': {'run_attempt': manifest.get('run_attempt')}})

    expected = expected if isinstance(expected, dict) and isinstance(expected.get('include', []), list) else {}
    for context in expected.get('include', []):
        if isinstance(context, dict) and identity(context) not in available:
            missing.append(context)

    lines = ['## DayScript reports', '', f'Build jobs: **{cell(build_result)}**. Memory is in decimal MB; min/max are sampled values and avg is the arithmetic sample mean.', '',
             'End is the last observed sample, including for interrupted runs. Separate helper processes are excluded; Wasm measures linear memory only. Retries and variants remain separate rows.', '']
    if download_result != 'success':
        lines += [f'Report download: **{cell(download_result)}**; available reports are shown below.', '']
    if not groups:
        lines += ['No per-script reports were available. The CLI may predate reporting, or jobs may have stopped before running scripts.', '']
    rows_written = 0
    for script, reports in sorted(groups.items()):
        notes = []
        lines += [f'### {cell(script)}', '', '| Target / device | Variant / locale / flavor | Started (UTC) | Result | Passed / skipped / failed / aborted | Duration | Samples | Min MB | Max MB | Avg MB | End MB | Metric | Hardware / OS |',
                  '|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
        for report in sorted(reports, key=lambda r: (str(r.get('target')), str(r.get('device_slug')), str(r.get('variant')), str(r.get('started_at_unix_ms')))):
            if rows_written >= 1000:
                break
            rows_written += 1
            state = report.get('status', 'unknown')
            if state == 'running':
                state = 'interrupted (last checkpoint)'
            if (report.get('ci') or {}).get('run_attempt'):
                state += ' (job attempt ' + str(report['ci']['run_attempt']) + ')'
            step = report.get('steps') or {}
            if report.get('status') == 'running':
                step = dict(step)
                step['aborted'] = max(0, step.get('planned', 0) - sum(step.get(k, 0) for k in ('passed', 'skipped', 'failed')))
            counts = ' / '.join(str(step.get(k, '—')) for k in ('passed', 'skipped', 'failed', 'aborted'))
            hardware = report.get('hardware') or {}
            guest, host, profile = (hardware.get(k) or {} for k in ('device', 'host', 'ci_profile'))
            model = guest.get('model') or profile.get('profile') or host.get('model') or 'unknown'
            system = guest.get('os_version') or host.get('os_version') or 'unknown OS'
            cpu = host.get('cpu') or host.get('arch') or ''
            target = str(report.get('target') or 'unknown')
            if report.get('device_slug'):
                target += ' / ' + str(report['device_slug'])
            variant = ' / '.join(str(report.get(k) or 'default') for k in ('variant', 'locale', 'flavor'))
            elapsed = report.get('duration_ms')
            duration = f'{elapsed/1000:.2f}s' if number(elapsed) else '—'
            memory = report.get('memory') or {}
            samples = memory.get('samples') or []
            count = sum(isinstance(s, dict) and number(s.get('bytes')) for s in samples)
            stats = memory_stats(report)
            started = report.get('started_at_unix_ms')
            stamp = datetime.fromtimestamp(started/1000, timezone.utc).isoformat(timespec='milliseconds') if number(started) and started < 253_402_300_000_000 else '—'
            values = [target, variant, stamp, state, counts, duration, count, *map(mb, stats), memory.get('metric'), f'{model}; {system}; {cpu}']
            lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
            error = report.get('error') or memory.get('error')
            if error:
                notes.append(f'{cell(target)}: {cell(str(error)[:500])}')
        lines.extend(['', *notes, ''])
    if rows_written >= 1000:
        lines += ['Table limited to 1,000 rows; full reports remain in the job artifacts.', '']
    if missing:
        lines += ['### Jobs without script reports', '', '| Target / profile | Build result |', '|---|---|']
        for context in missing:
            reason = context.get('report_reason') or ('No report artifact; overall build result: ' + str(build_result))
            lines.append(f"| {cell(context.get('label') or context.get('target'))} | {cell(reason)} |")
        lines.append('')
    if invalid:
        lines += ['### Unreadable reports', ''] + [f'- {cell(error)}' for error in invalid[:50]] + ['']
    return '\n'.join(lines)


def main():
    try:
        expected = json.loads(os.environ.get('EXPECTED_MATRIX') or '{}')
    except ValueError:
        expected = {}
    text = render(os.environ.get('REPORT_ROOT', '.'), expected, os.environ.get('BUILD_RESULT', 'unknown'), os.environ.get('DOWNLOAD_RESULT', 'success'))
    # GitHub limits each step summary to 1 MiB. Preserve a complete UTF-8 document under it.
    encoded = text.encode('utf-8')
    if len(encoded) > 950_000:
        text = encoded[:940_000].decode('utf-8', errors='ignore') + '\n\nSummary truncated; see JSON artifacts for complete results.\n'
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as summary:
        summary.write(text + '\n')


if __name__ == '__main__':
    main()
