"""Optional real Docker smoke test. No Claude login, API calls, or image builds.

Run with an already-built image: python3 tests/smoke_docker.py IMAGE
Requires Docker access and Node only inside the image. Creates bounded test
containers and removes its own resources. Uses local HTTP fixtures exclusively.
"""
import http.server
import json
import os
from pathlib import Path
import select
import shlex
import socketserver
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'blackhole-claude:latest'
PROFILE = f'smoke-{os.getpid()}'
VOLUME = f'blackhole-claude-home-{PROFILE}'
CONTAINERS = []


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=30).strip()


class Response(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'controlled-proxy-response'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class Fixture(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port=0):
    fixture = Fixture(('0.0.0.0', port), Response)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    return fixture


def until(process, marker, timeout=15):
    deadline = time.monotonic() + timeout
    captured = []
    while time.monotonic() < deadline:
        if select.select([process.stdout], [], [], 0.2)[0]:
            # Binary unbuffered I/O avoids select + TextIO read-ahead stalls.
            line = process.stdout.readline().decode()
            if not line:
                raise AssertionError('shell exited: ' + ''.join(captured))
            captured.append(line)
            if marker in line:
                return ''.join(captured)
    raise AssertionError('timed out waiting for ' + marker + ': ' + ''.join(captured))


def send(process, javascript, marker):
    process.stdin.write(('node -e ' + shlex.quote(javascript) + '\n').encode())
    process.stdin.flush()
    return until(process, marker)


fixture = serve()
fixture_port = fixture.server_address[1]
gateway = docker('network', 'inspect', 'bridge', '--format', '{{(index .IPAM.Config 0).Gateway}}')
proxy_ok = """
const http = require('http');
const req = http.request(process.env.HTTP_PROXY, {path:'http://fixture.invalid/'}, r => {
 let text=''; r.on('data', x=>text+=x); r.on('end',()=>{
  if(text!=='controlled-proxy-response') process.exit(2); console.log('PROXY_OK');
 });
}); req.on('error',()=>process.exit(3)); req.setTimeout(3000,()=>req.destroy()); req.end();
"""
proxy_down = """
const http = require('http');
const req = http.request(process.env.HTTP_PROXY, {path:'http://fixture.invalid/'}, () => process.exit(2));
req.on('error',()=>console.log('PROXY_BLOCKED')); req.setTimeout(2000,()=>req.destroy()); req.end();
"""
direct_blocked = f"""
const net=require('net');
const s=net.createConnection({{host:{json.dumps(gateway)},port:{fixture_port}}});
s.on('connect',()=>{{ console.log('DIRECT_LEAK'); s.destroy(); process.exit(2); }});
s.on('error',()=>console.log('DIRECT_BLOCKED')); s.setTimeout(2000,()=>s.destroy(new Error('blocked')));
"""

try:
    with tempfile.TemporaryDirectory(prefix='blackhole-smoke-') as directory:
        base = Path(directory)
        project = base / 'project 空间,"quoted"'
        if os.getuid() == 0:
            raise SystemExit('Run this smoke test as a normal user; use smoke_permissions.py for root-owned mounts')
        project.mkdir(mode=0o755)
        os.chmod(project, 0o755)
        expected_uid = os.getuid()
        home = base / 'home'
        home.mkdir()
        # Wrapper caps the main test container too; relay already has its own
        # limits. Preserve the real Docker CLI for every other operation.
        tools = base / 'bin'
        tools.mkdir()
        real_docker = subprocess.check_output(['which', 'docker'], text=True).strip()
        wrapper = tools / 'docker'
        wrapper.write_text('#!/usr/bin/env bash\nif [[ "$1" == run ]]; then\n'
                           ' shift\n exec ' + shlex.quote(real_docker) + ' run --memory 256m --cpus 0.5 "$@"\n'
                           'fi\nexec ' + shlex.quote(real_docker) + ' "$@"\n')
        wrapper.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('BLACKHOLE_', 'CLAUDE_', 'ANTHROPIC_'))}
        env.update(HOME=str(home), PATH=str(tools) + ':' + os.environ['PATH'],
                   BLACKHOLE_BACKEND='docker', BLACKHOLE_PROFILE=PROFILE,
                   BLACKHOLE_IMAGE=IMAGE, BLACKHOLE_STATE_DIR=str(base / 'state'),
                   BLACKHOLE_PROXY=f'http://{gateway}:{fixture_port}', NO_COLOR='1')

        def cli(*args):
            return subprocess.check_output(['bash', str(ROOT / 'bin/blackhole'), *args],
                                           cwd=project, env=env, text=True, timeout=30)

        shell = subprocess.Popen(['bash', str(ROOT / 'bin/blackhole'), 'shell'], cwd=project, env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, bufsize=0)
        CONTAINERS.append(shell)
        shell.stdin.write(b'printf "UID=%s\\n" "$(id -u)"\n')
        shell.stdin.flush()
        marker = f'UID={expected_uid}'
        assert marker in until(shell, marker)
        sessions = docker('ps', '-q', '--filter', 'label=dev.blackhole.session=1', '--filter', f'label=dev.blackhole.profile={PROFILE}').split()
        assert len(sessions) == 1
        assert json.loads(docker('inspect', sessions[0]))[0]['HostConfig']['Dns'] == ['127.0.0.1']
        send(shell, "require('fs').writeFileSync('/workspace/persist.txt','saved'); console.log('WRITE_OK')", 'WRITE_OK')
        assert (project / 'persist.txt').read_text() == 'saved'
        send(shell, proxy_ok, 'PROXY_OK')
        send(shell, direct_blocked, 'DIRECT_BLOCKED')
        print('PASS: non-root session, live Unicode/comma bind mount, proxy succeeds, direct route blocked')

        relays = docker('ps', '-q', '--filter', 'label=dev.blackhole.proxy=1', '--filter', f'label=dev.blackhole.profile={PROFILE}').split()
        assert len(relays) == 1
        info = json.loads(docker('inspect', relays[0]))[0]
        assert info['HostConfig']['Memory'] == 64 * 1024 ** 2
        assert info['HostConfig']['NanoCpus'] == 250000000
        assert info['HostConfig']['CapDrop'] == ['ALL']
        assert info['Mounts'] == []
        print('Relay actual usage:', docker('stats', '--no-stream', '--format', '{{.MemUsage}} {{.CPUPerc}}', relays[0]))

        fixture.shutdown()
        fixture.server_close()
        send(shell, proxy_down, 'PROXY_BLOCKED')
        send(shell, direct_blocked, 'DIRECT_BLOCKED')
        print('PASS: upstream loss blocks traffic without direct fallback')
        fixture = serve(fixture_port)
        cli('net', 'off')
        send(shell, proxy_down, 'PROXY_BLOCKED')
        assert 'offline' in cli('net', 'status')
        # A fresh session inherits both proxy mode and the offline latch even
        # after removing the environment override, like opening a new terminal.
        inherited = {k: v for k, v in env.items() if k != 'BLACKHOLE_PROXY'}
        offline = subprocess.run(['bash', str(ROOT / 'bin/blackhole'), 'shell'], cwd=project, env=inherited,
                                 input='node -e ' + shlex.quote(proxy_down) + '\n', text=True,
                                 capture_output=True, timeout=20)
        assert offline.returncode == 0 and 'PROXY_BLOCKED' in offline.stdout, offline.stdout + offline.stderr
        cli('net', 'on')
        # Startup readiness is bounded and local; no recurring health poll.
        deadline = time.monotonic() + 5
        while docker('inspect', '--format', '{{.State.Running}}', relays[0]) != 'true':
            assert time.monotonic() < deadline
            time.sleep(0.05)
        send(shell, proxy_ok, 'PROXY_OK')
        print('PASS: net off/on and new-session offline latch')
        shell.stdin.close()
        assert shell.wait(timeout=15) == 0
        assert docker('ps', '-aq', '--filter', 'label=dev.blackhole.proxy=1', '--filter', f'label=dev.blackhole.profile={PROFILE}') == ''
        assert not any(PROFILE in name for name in docker('network', 'ls', '--format', '{{.Name}}').splitlines())
        assert (project / 'persist.txt').read_text() == 'saved'
        print('PASS: normal-exit relay/network cleanup and project persistence')
finally:
    fixture.shutdown()
    fixture.server_close()
    for process in CONTAINERS:
        if process.poll() is None:
            process.terminate()
    # Filter by this unique test namespace; never prune shared Docker state.
    ids = docker('ps', '-aq', '--filter', f'label=dev.blackhole.profile={PROFILE}').split()
    if ids: docker('rm', '-f', *ids)
    names = docker('network', 'ls', '--format', '{{.Name}}').splitlines()
    for name in names:
        if name.startswith(f'blackhole-{PROFILE}-'): docker('network', 'rm', name)
    subprocess.run(['docker', 'volume', 'rm', VOLUME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
