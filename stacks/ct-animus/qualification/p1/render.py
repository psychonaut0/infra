#!/usr/bin/env python3
"""Render review artifacts only; no provider calls. Default app issuance denied."""
import argparse
import hashlib
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parent
POLICIES = {
    'entry': 'entry.expr', 'final': 'final.expr', 'pkce': 'pkce.expr',
    'application': 'application.expr', 'authorization-entry': 'authorization-entry.expr',
    'authorization-final': 'authorization-final.expr', 'issuance-closed': 'issuance-closed.expr',
    'bootstrap-entry': ('eligibility.expr', 'p1-mechanism-bootstrap', True),
    'bootstrap': ('eligibility.expr', 'p1-mechanism-bootstrap', False),
    'repair': ('eligibility.expr', 'p1-mechanism-repair', False),
}
PROMPTS = {
    'normal-entry': 'entry', 'normal-final': 'final',
    'authorization-entry': 'authorization-entry', 'authorization-final': 'authorization-final',
    'bootstrap-entry': 'bootstrap-entry', 'bootstrap': 'bootstrap', 'repair': 'repair',
}


def expression(name):
    source = (ROOT / 'policies' / name).read_text().rstrip()
    if '__EVENT_PROOF_HELPER__' in source:
        source = source.replace('__EVENT_PROOF_HELPER__', (ROOT / 'policies/event-proof.py.in').read_text().rstrip())
    return source


def render(issuance_experiment=False):
    result = (ROOT / 'blueprint.yaml.in').read_text()
    stage_rows = []
    for stage, policy in PROMPTS.items():
        stage_rows += ['  - model: authentik_stages_prompt.promptstage', f'    id: {stage}',
                       f'    identifiers: {{name: p1-mechanism-{stage}}}',
                       f'    attrs: {{fields: [!KeyOf prompt-continue], validation_policies: [!KeyOf {policy}-policy]}}']
    policy_rows = []
    for policy, source in POLICIES.items():
        if isinstance(source, tuple):
            filename, group, no_totp = source
            body = expression(filename).replace('__ELIGIBILITY_GROUP__', group).replace('__REQUIRE_NO_TOTP__', str(no_totp))
        else:
            body = expression(source)
        if policy == 'issuance-closed' and issuance_experiment:
            body = '# REVIEWED FRESH ISOLATED ISSUANCE EXPERIMENT ONLY; never deploy.\nreturn True'
        policy_rows += ['  - model: authentik_policies_expression.expressionpolicy', f'    id: {policy}-policy',
                        f'    identifiers: {{name: p1-mechanism-{policy}}}',
                        '    attrs:', '      execution_logging: false', '      expression: |', textwrap.indent(body, '        ')]
    sequences = {
        'normal-flow': ['normal-entry', 'identification', 'password', 'totp-validation', 'normal-final', 'login'],
        'enrollment-flow': ['identification', 'password', 'bootstrap-entry', 'totp-setup', 'bootstrap', 'static-setup', 'logout'],
        'repair-flow': ['identification', 'password', 'repair', 'static-validation', 'repair', 'totp-setup', 'repair', 'static-setup', 'logout'],
        'authorization-flow': ['authorization-entry', 'consent', 'authorization-final'],
    }
    bindings = []
    for flow, stages in sequences.items():
        for i, stage in enumerate(stages):
            bindings += ['  - model: authentik_flows.flowstagebinding',
                         f'    identifiers: {{target: !KeyOf {flow}, stage: !KeyOf {stage}, order: {i * 10}}}',
                         '    attrs: {evaluate_on_plan: true, re_evaluate_policies: false, invalid_response_action: retry}']
    return (result.replace('__PHASE__', 'REVIEWED ISOLATED ISSUANCE EXPERIMENT ONLY' if issuance_experiment else 'MECHANISM PHASE: application issuance unconditionally denied')
            .replace('__PROMPT_STAGES__', '\n'.join(stage_rows))
            .replace('__POLICIES__', '\n'.join(policy_rows))
            .replace('__CLAIMS__', textwrap.indent(expression('claims.expr'), '        '))
            .replace('__BINDINGS__', '\n'.join(bindings)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--issuance-experiment', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.issuance_experiment and (not args.output or args.check):
        parser.error('issuance experiment requires explicit separate --output; no default activation')
    expected = render(args.issuance_experiment)
    path = args.output or ROOT / 'candidate.yaml'
    if args.check:
        if not path.is_file() or path.read_text() != expected:
            raise SystemExit('FAIL: candidate differs from exact inputs')
    else:
        if args.issuance_experiment and path.resolve() == (ROOT / 'candidate.yaml').resolve():
            parser.error('cannot overwrite closed default candidate')
        path.write_text(expected)
    print('PASS: candidate sha256=' + hashlib.sha256(expected.encode()).hexdigest())
