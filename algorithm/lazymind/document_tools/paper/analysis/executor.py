"""Execute only the bundled, trusted statistics program in a chosen environment."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 48 * 1024 * 1024


def execute(table: dict, plan: dict, *, statistics_only: bool = False) -> dict:
    backend = os.environ.get('LAZYMIND_PAPER_ANALYSIS_BACKEND', 'process')
    if backend not in ('process', 'docker'):
        raise RuntimeError('分析执行环境仅支持 process/docker')
    raw_timeout = os.environ.get('LAZYMIND_PAPER_ANALYSIS_TIMEOUT', '60')
    try:
        timeout = int(raw_timeout)
        if not 1 <= timeout <= 300:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError('分析超时必须是 1..300 秒的整数') from exc
    payload = json.dumps({'table': table, 'plan': plan, 'statistics_only': statistics_only},
                         ensure_ascii=False, allow_nan=False)
    if len(payload.encode()) > MAX_INPUT_BYTES:
        raise ValueError('分析输入超过 32 MiB，请缩小数据范围')
    worker = Path(__file__).with_name('worker.py').resolve()
    with tempfile.TemporaryDirectory(prefix='lazymind-analysis-') as directory:
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'LANG', 'LC_ALL') if key in os.environ}
        env.update(HOME=directory, TMPDIR=directory, TEMP=directory, TMP=directory,
                   MPLCONFIGDIR=directory, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        if backend == 'docker':
            image = os.environ.get('LAZYMIND_PAPER_ANALYSIS_IMAGE', '').strip()
            if not image or image.startswith('-') or any(c.isspace() for c in image):
                raise RuntimeError('Docker 分析必须配置预装依赖的 LAZYMIND_PAPER_ANALYSIS_IMAGE')
            command = ['docker', 'run', '--rm', '--pull=never', '-i', '--network', 'none', '--read-only',
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--pids-limit', '64', '--memory', '512m', '--cpus', '1',
                       '--user', '65534:65534', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
                       '-e', 'MPLCONFIGDIR=/tmp/matplotlib', '-e', 'HOME=/tmp',
                       '-v', f'{worker}:/analysis_worker.py:ro', image, 'python', '-I', '/analysis_worker.py']
        else:
            python = os.environ.get('LAZYMIND_PAPER_ANALYSIS_PYTHON', sys.executable)
            command = [python, '-I', str(worker)]
        try:
            # A timeout must stop a container too, not merely the docker CLI.
            name = None
            if backend == 'docker':
                name = 'lazymind-f4-' + Path(directory).name
                command[2:2] = ['--name', name]
            completed = subprocess.run(command, input=payload, text=True, encoding='utf-8',
                                       capture_output=True, timeout=timeout, cwd=directory, env=env)
        except subprocess.TimeoutExpired as exc:
            cleanup_note = ''
            if name is not None:
                try:
                    cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=10, env=env)
                    if cleanup.returncode:
                        cleanup_note = f'；请检查容器 {name} 是否已停止'
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_note = f'；容器 {name} 清理失败，需要检查执行服务'
            raise RuntimeError(f'分析代码执行超时（{timeout} 秒），未发布结果{cleanup_note}') from exc
        except OSError as exc:
            raise RuntimeError(f'分析执行器不可用: {exc.strerror}') from exc
        if len(completed.stdout.encode()) > MAX_OUTPUT_BYTES:
            raise RuntimeError('分析输出超过 48 MiB，未发布结果')
        try:
            result = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError('分析程序没有返回有效 JSON，未发布结果') from exc
        if not isinstance(result, dict):
            raise RuntimeError('分析程序返回的结果不是对象')
        if completed.returncode or 'error' in result:
            raise RuntimeError(str(result.get('error') or f'分析程序异常退出: {completed.returncode}'))
        return result
