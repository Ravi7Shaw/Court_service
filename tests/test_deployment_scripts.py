"""Exercise shell orchestration with synthetic CLIs; never contact AWS."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / 'project'
    shutil.copytree(ROOT / 'deploy', root / 'deploy', ignore=shutil.ignore_patterns('.terraform', '*.tfstate*'))
    home = tmp_path / 'home'
    (home / '.ssh').mkdir(parents=True)
    (home / '.ssh/id_ed25519').write_text('synthetic key')
    tools = tmp_path / 'tools'
    tools.mkdir()
    journal = tmp_path / 'journal.jsonl'
    mock = tools / 'mock'
    mock.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['JOURNAL'],'a') as f: f.write(json.dumps([name,args])+'\\n')
if name=='curl': print('203.0.113.7')
if name=='terraform':
    if args[0]=='console':
        print(json.dumps(json.dumps({'domain':os.environ.get('DOMAIN_NAME',''), 'certificate':os.environ.get('ACM_CERTIFICATE_ARN',''), 'costs':os.environ.get('COSTS','true')=='true', 'http_demo':os.environ.get('ALLOW_HTTP_DEMO','true')=='true'})))
    if args[0]=='plan':
        if os.environ.get('FAIL_PLAN'): sys.exit(1)
        target=next(x[5:] for x in args if x.startswith('-out='))
        pathlib.Path(target).write_text('synthetic reviewed plan')
    if args[0]=='apply':
        assert pathlib.Path(args[-1]).read_text()=='synthetic reviewed plan'
    if args[0]=='output':
        keys=['primary_public_ip','secondary_public_ip','primary_private_ip','secondary_private_ip','admin_password','db_password','redis_password','jwt_secret','grafana_admin_password','grafana_url','prometheus_url']
        result={k:{'value':'synthetic-'+k} for k in keys}
        result['allow_http_demo']={'value':os.environ.get('ALLOW_HTTP_DEMO','true')=='true'}
        result['application_url']={'value':os.environ.get('OUTPUT_URL','https://courts.example.com')}
        print(json.dumps(result))
if name=='ansible-playbook' and os.environ.get('FAIL_PLAYBOOK'): sys.exit(1)
''')
    mock.chmod(0o755)
    for name in ['terraform', 'ansible', 'ansible-playbook', 'ansible-galaxy', 'aws', 'curl', 'rsync']:
        (tools / name).symlink_to(mock)
    env = {**os.environ, 'HOME': str(home), 'PATH': str(tools)+os.pathsep+os.environ['PATH'],
           'JOURNAL': str(journal), 'DOMAIN_NAME': 'courts.example.com',
           'ACM_CERTIFICATE_ARN': 'arn:aws:acm:ap-southeast-2:000000000000:certificate/synthetic'}
    for key in ['FAIL_PLAN', 'FAIL_PLAYBOOK', 'COSTS', 'SSH_KEY', 'ALLOW_HTTP_DEMO', 'OUTPUT_URL']:
        env.pop(key, None)

    def run(script='deploy.sh', answer='APPLY\n', **changes):
        result = subprocess.run(['bash', str(root/'deploy'/script)], input=answer, text=True,
                                capture_output=True, env={**env, **changes})
        calls = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
        return result, calls
    return run


def terraform_calls(calls, command):
    return [args for name, args in calls if name == 'terraform' and args[0] == command]


@pytest.mark.parametrize('changes', [{'DOMAIN_NAME': ''}, {'ACM_CERTIFICATE_ARN': ''}, {'COSTS': 'false'}])
def test_missing_required_configuration_never_plans_or_applies(deployment, changes):
    result, calls = deployment(**changes)
    assert result.returncode != 0
    assert not terraform_calls(calls, 'plan')
    assert not terraform_calls(calls, 'apply')
    assert 'HTTPS configuration missing' in result.stderr or 'Review AWS costs' in result.stderr


def test_deploy_applies_reviewed_plan_and_checks_each_compose_file(deployment):
    result, calls = deployment()
    assert result.returncode == 0, result.stderr
    plan = terraform_calls(calls, 'plan')[0]
    path = next(arg[5:] for arg in plan if arg.startswith('-out='))
    assert terraform_calls(calls, 'apply') == [['apply', '-input=false', path]]
    assert not Path(path).exists()
    assert '-var=domain_name=courts.example.com' in plan
    checks = [args for name, args in calls if name == 'ansible' and '-m' in args and 'command' in args]
    assert len(checks) == 2
    assert 'compose-primary.yml' in checks[0][-1]
    assert 'compose-secondary.yml' in checks[1][-1]


@pytest.mark.parametrize('script,answer', [('deploy.sh', 'NO\n'), ('destroy.sh', 'NO\n'), ('destroy.sh', 'PLAN\nNO\n')])
def test_cancellation_never_applies(deployment, script, answer):
    _, calls = deployment(script, answer)
    assert not terraform_calls(calls, 'apply')
    for plan in terraform_calls(calls, 'plan'):
        assert not Path(next(arg[5:] for arg in plan if arg.startswith('-out='))).exists()


def test_destroy_only_applies_the_reviewed_destroy_plan(deployment):
    result, calls = deployment('destroy.sh', 'PLAN\nDESTROY\n')
    assert result.returncode == 0, result.stderr
    plan = terraform_calls(calls, 'plan')[0]
    assert '-destroy' in plan
    path = next(arg[5:] for arg in plan if arg.startswith('-out='))
    assert terraform_calls(calls, 'show') == [['show', path]]
    assert terraform_calls(calls, 'apply') == [['apply', '-input=false', path]]
    assert not Path(path).parent.exists()


@pytest.mark.parametrize('script,answer', [('deploy.sh', 'APPLY\n'), ('destroy.sh', 'PLAN\nDESTROY\n')])
def test_failed_plan_never_applies(deployment, script, answer):
    result, calls = deployment(script, answer, FAIL_PLAN='1')
    assert result.returncode != 0
    assert not terraform_calls(calls, 'apply')


def test_playbook_failure_does_not_report_deployment_completed(deployment):
    result, _ = deployment(FAIL_PLAYBOOK='1')
    assert result.returncode != 0
    assert 'DEPLOYMENT PLAYBOOK COMPLETED' not in result.stdout


def test_default_demo_needs_no_domain_or_certificate(deployment):
    result, calls = deployment(DOMAIN_NAME='', ACM_CERTIFICATE_ARN='', OUTPUT_URL='http://synthetic.elb.amazonaws.com')
    assert result.returncode == 0, result.stderr
    assert 'HTTP DEMO' in result.stdout
    assert '-var=allow_http_demo=true' in terraform_calls(calls, 'plan')[0]


def test_strict_mode_still_requires_https(deployment):
    result, calls = deployment(DOMAIN_NAME='', ACM_CERTIFICATE_ARN='', ALLOW_HTTP_DEMO='false')
    assert result.returncode != 0
    assert not terraform_calls(calls, 'apply')


def test_strict_mode_rejects_unexpected_http_output(deployment):
    result, _ = deployment(ALLOW_HTTP_DEMO='false', OUTPUT_URL='http://synthetic.elb.amazonaws.com')
    assert result.returncode != 0
    assert 'Required Terraform outputs missing' in result.stderr
