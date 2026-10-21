"""Execute plots inside Chat, or in an explicitly configured standalone container."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from ..execution import container


class PlotEnvironmentError(RuntimeError):
    """Deployment errors cannot be fixed by rewriting plotting code."""


def configuration() -> tuple[str, int]:
    backend = os.environ.get('LAZYMIND_PAPER_PLOT_BACKEND', 'docker')
    if backend not in {'docker', 'container'}:
        raise PlotEnvironmentError('模型绘图代码支持 container / docker；不会回退到宿主进程')
    if backend == 'container':
        try:
            container.validate_environment()
        except container.ContainerEnvironmentError as exc:
            raise PlotEnvironmentError(str(exc)) from exc
    image = os.environ.get('LAZYMIND_PAPER_PLOT_IMAGE', '').strip()
    if backend == 'docker' and (not image or image.startswith('-') or any(c.isspace() for c in image)):
        raise PlotEnvironmentError('请构建绘图镜像并配置 LAZYMIND_PAPER_PLOT_IMAGE，见 F4 开发文档')
    try:
        timeout = int(os.environ.get('LAZYMIND_PAPER_PLOT_TIMEOUT', '60'))
        if not 1 <= timeout <= 300:
            raise ValueError
    except ValueError as exc:
        raise PlotEnvironmentError('绘图超时必须是 1..300 秒') from exc
    return image, timeout


def command_for(image: str, name: str) -> list[str]:
    worker = Path(__file__).with_name('worker.py').resolve()
    return ['docker', 'run', '--name', name, '--rm', '--pull=never', '-i',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--pids-limit', '64', '--memory', '768m',
            '--memory-swap', '768m', '--cpus', '1', '--user', '65534:65534',
            '--ulimit', 'fsize=16777216:16777216', '--log-driver', 'none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=96m', '--workdir', '/tmp',
            '-e', 'MPLCONFIGDIR=/tmp/matplotlib', '-e', 'HOME=/tmp',
            '-e', 'OPENBLAS_NUM_THREADS=1', '-e', 'OMP_NUM_THREADS=1',
            '-v', f'{worker}:/plot_worker.py:ro', image, 'python', '-I', '/plot_worker.py']


def execute_plot(payload: dict) -> dict:
    image, timeout = configuration()
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 40 * 1024 * 1024:
        raise ValueError('绘图输入超过 40 MiB')
    if os.environ.get('LAZYMIND_PAPER_PLOT_BACKEND', 'docker') == 'container':
        try:
            return container.execute(payload, Path(__file__).with_name('worker.py'), timeout, label='绘图')
        except container.ContainerEnvironmentError as exc:
            raise PlotEnvironmentError(str(exc)) from exc
    name = 'lazymind-plot-' + uuid.uuid4().hex
    # Retain Docker connection settings, but do not forward host environment or credentials into the container.
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_TLS_VERIFY',
                      'DOCKER_CERT_PATH', 'SYSTEMROOT', 'WINDIR', 'LANG'}}
    process = None
    with tempfile.TemporaryDirectory(prefix='lazymind-plot-') as directory:
        paths = [Path(directory) / name for name in ('input.json', 'stdout', 'stderr')]
        paths[0].write_bytes(raw)
        try:
            with paths[0].open('rb') as source, paths[1].open('wb') as out, paths[2].open('wb') as err:
                process = subprocess.Popen(command_for(image, name), stdin=source, stdout=out, stderr=err, env=env)
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'绘图执行超过 {timeout} 秒，请简化代码')
                    if any(path.stat().st_size > 16 * 1024 * 1024 for path in paths[1:]):
                        raise RuntimeError('绘图输出超过 16 MiB')
                    time.sleep(0.05)
            if any(path.stat().st_size > 16 * 1024 * 1024 for path in paths[1:]):
                raise RuntimeError('绘图输出超过 16 MiB')
            stderr = paths[2].read_text(errors='replace')[-3000:]
            if process.returncode in (125, 126, 127):
                raise PlotEnvironmentError('绘图容器未能启动: ' + stderr)
            stdout = paths[1].read_text(errors='replace')
            try:
                result = json.loads(stdout)
            except ValueError as exc:
                raise RuntimeError('绘图程序没有返回有效结果: ' + stderr) from exc
            if not isinstance(result, dict) or process.returncode or 'error' in result:
                message = result.get('error', stderr) if isinstance(result, dict) else stderr
                raise RuntimeError('绘图执行失败: ' + str(message)[-6000:])
            return result
        except OSError as exc:
            raise PlotEnvironmentError('Docker 绘图执行器不可用: ' + str(exc)) from exc
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            # Always remove by our own name, including daemon errors or CLI termination.
            if process is not None:
                try:
                    cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, env=env, timeout=10)
                    detail = cleanup.stderr.decode(errors='replace')
                    if cleanup.returncode and 'No such container' not in detail:
                        raise PlotEnvironmentError(f'无法确认绘图容器 {name} 已清理: {detail[-1000:]}')
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise PlotEnvironmentError(f'绘图容器 {name} 清理失败，请检查 Docker 服务') from exc
