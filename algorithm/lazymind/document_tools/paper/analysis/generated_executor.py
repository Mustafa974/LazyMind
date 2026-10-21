"""Bounded transports for generated code; never use the trusted process worker."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from ..plotting.code import ALLOWED_IMPORTS, FORBIDDEN, FORBIDDEN_ATTRIBUTES
from ..execution import container

# These are the modules bundled by paper-plot.Dockerfile, plus the Python standard library.
# An import allowlist must not advertise optional packages absent from the execution image.
ANALYSIS_IMPORTS = ALLOWED_IMPORTS | {'copy', 'collections', 'itertools'}
ANALYSIS_FORBIDDEN_ATTRIBUTES = FORBIDDEN_ATTRIBUTES - {'random', 'default_rng'}


def generation_contract():
    """Expose the actual early validator rules before the first generation."""
    return dict(allowed_import_roots=sorted(ANALYSIS_IMPORTS), forbidden_names=sorted(FORBIDDEN),
                unbundled_optional_packages=['statsmodels', 'pymc', 'arviz'],
                ols_runtime='Use numpy.linalg.lstsq / pinv and scipy.stats; statsmodels is not bundled.',
                forbidden_attributes=sorted(ANALYSIS_FORBIDDEN_ATTRIBUTES),
                private_attributes_allowed=False, decorators_allowed=False,
                required_functions=dict(prepare=['tables', 'plan'], analyze=['prepared', 'plan', 'rng']),
                top_level='imports, function definitions and literal constants only',
                randomness='use supplied rng; no filesystem/network/environment access')


class AnalysisEnvironmentError(RuntimeError):
    pass


def validate_code(code):
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > 64 * 1024:
        raise ValueError('统计代码须为不超过 64 KiB 的非空文本')
    tree = ast.parse(code)
    for name, args in [('prepare', ['tables', 'plan']), ('analyze', ['prepared', 'plan', 'rng'])]:
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
        if len(functions) != 1 or [a.arg for a in functions[0].args.args] != args:
            raise ValueError(f'统计程序必须定义 {name}({", ".join(args)})')
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            continue
        if isinstance(node, ast.Assign):
            ast.literal_eval(node.value)
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        raise ValueError('统计程序顶层只允许导入、函数和常量')
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split('.')[0] not in ANALYSIS_IMPORTS for a in node.names):
            raise ValueError('统计代码包含未允许的导入')
        if isinstance(node, ast.ImportFrom) and (
            node.level or (node.module or '').split('.')[0] not in ANALYSIS_IMPORTS
        ):
            raise ValueError('统计代码包含未允许的导入')
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN or node.id.startswith('__')):
            raise ValueError('统计代码包含禁止的动态执行或 I/O: ' + node.id)
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr in ANALYSIS_FORBIDDEN_ATTRIBUTES):
            raise ValueError('统计代码包含禁止的 I/O 属性: ' + node.attr)
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError('统计代码不能使用装饰器')


def configuration():
    backend = os.environ.get('LAZYMIND_PAPER_GENERATED_ANALYSIS_BACKEND', 'docker')
    if backend not in {'docker', 'container'}:
        raise AnalysisEnvironmentError('生成统计代码支持 container / docker，不允许宿主进程回退')
    if backend == 'container':
        try:
            container.validate_environment()
        except container.ContainerEnvironmentError as exc:
            raise AnalysisEnvironmentError(str(exc)) from exc
    image = os.environ.get(
        'LAZYMIND_PAPER_GENERATED_ANALYSIS_IMAGE', os.environ.get('LAZYMIND_PAPER_PLOT_IMAGE', '')
    ).strip()
    if backend == 'docker' and (not image or image.startswith('-') or any(c.isspace() for c in image)):
        raise AnalysisEnvironmentError('请配置 LAZYMIND_PAPER_GENERATED_ANALYSIS_IMAGE，运行时不会拉取镜像')
    try:
        timeout = int(os.environ.get('LAZYMIND_PAPER_GENERATED_ANALYSIS_TIMEOUT', '180'))
        if not 1 <= timeout <= 3600:
            raise ValueError
    except ValueError as exc:
        raise AnalysisEnvironmentError('统计执行超时须为 1..3600 秒') from exc
    return image, timeout


def command_for(image, name):
    worker = Path(__file__).with_name('generated_worker.py').resolve()
    return ['docker', 'run', '--name', name, '--rm', '--pull=never', '-i', '--network', 'none',
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '128', '--memory', '1g', '--memory-swap', '1g', '--cpus', '2',
            '--user', '65534:65534', '--ulimit', 'fsize=16777216:16777216', '--log-driver', 'none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m', '--workdir', '/tmp',
            '-e', 'HOME=/tmp', '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'OMP_NUM_THREADS=1',
            '-v', f'{worker}:/analysis_worker.py:ro', image, 'python', '-I', '/analysis_worker.py']


def execute(payload):
    image, timeout = configuration()
    validate_code(payload['code'])
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('统计执行输入超过 32 MiB')
    if os.environ.get('LAZYMIND_PAPER_GENERATED_ANALYSIS_BACKEND', 'docker') == 'container':
        try:
            result = container.execute(payload, Path(__file__).with_name('generated_worker.py'),
                                       timeout, label='生成统计代码')
        except container.ContainerEnvironmentError as exc:
            raise AnalysisEnvironmentError(str(exc)) from exc
        if set(result) != {'results', 'versions', 'seed'} or result['seed'] != payload['seed']:
            raise RuntimeError('统计执行记录不完整')
        return result
    name = 'lazymind-generated-analysis-' + uuid.uuid4().hex
    env = {key: value for key, value in os.environ.items() if key in {
        'PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY',
        'DOCKER_CERT_PATH', 'SYSTEMROOT', 'WINDIR', 'LANG'}}
    process = None
    with tempfile.TemporaryDirectory(prefix='lazymind-generated-analysis-') as directory:
        paths = [Path(directory) / n for n in ('input.json', 'stdout', 'stderr')]
        paths[0].write_bytes(raw)
        try:
            with paths[0].open('rb') as source, paths[1].open('wb') as out, paths[2].open('wb') as err:
                process = subprocess.Popen(command_for(image, name), stdin=source, stdout=out, stderr=err, env=env)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'生成统计代码执行超时（{timeout} 秒）')
                    if any(p.stat().st_size > 16 * 1024 * 1024 for p in paths[1:]):
                        raise RuntimeError('生成统计程序输出超过 16 MiB')
                    time.sleep(0.05)
            if any(p.stat().st_size > 16 * 1024 * 1024 for p in paths[1:]):
                raise RuntimeError('生成统计程序输出超过 16 MiB')
            stderr = paths[2].read_text(errors='replace')[-3000:]
            if process.returncode in (125, 126, 127):
                raise AnalysisEnvironmentError('统计容器未启动: ' + stderr)
            try:
                result = json.loads(paths[1].read_text())
            except ValueError as exc:
                raise RuntimeError('统计程序未返回有效 JSON: ' + stderr) from exc
            if not isinstance(result, dict) or process.returncode or 'error' in result:
                raise RuntimeError('统计执行失败: ' + str(result.get('error', stderr) if isinstance(result, dict) else stderr))
            if set(result) != {'results', 'versions', 'seed'} or result['seed'] != payload['seed']:
                raise RuntimeError('统计执行记录不完整')
            return result
        except OSError as exc:
            raise AnalysisEnvironmentError('Docker 统计执行器不可用') from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                try:
                    cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, env=env, timeout=10)
                    detail = cleanup.stderr.decode(errors='replace')
                    if cleanup.returncode and 'No such container' not in detail:
                        raise AnalysisEnvironmentError(f'无法确认统计容器 {name} 已清理: {detail[-1000:]}')
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise AnalysisEnvironmentError(f'统计容器 {name} 清理失败') from exc
