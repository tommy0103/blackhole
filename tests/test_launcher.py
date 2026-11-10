"""CLI regression tests using fake engines; no Docker, VM, or API calls."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin" / "blackhole"
STUB = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as log:
    log.write(json.dumps([tool, args, os.environ.get('DOCKER_HOST')]) + '\n')
if tool == 'uname':
    print(os.environ.get('FAKE_OS', 'Linux'))
elif tool == 'column':
    sys.stdout.write(sys.stdin.read())
elif tool == 'wslpath':
    print(args[-1])
elif tool == 'powershell.exe':
    if os.environ.get('CLIPBOARD_EMPTY'): sys.exit(1)
    if os.environ.get('CLIPBOARD_RACE'):
        Path(os.environ['PROJECT'], 'capture.png').symlink_to(os.environ['CLIPBOARD_RACE'], target_is_directory=True)
    import base64
    print(base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode())
elif tool == 'colima':
    if args == ['list', '-j']:
        print('{"name":"default","status":"Running","memory":8589934592}')
elif tool == 'docker':
    if any(a.startswith('label!=') for a in args): sys.exit('unsupported container label filter')
    if args[0] == 'info':
        if os.environ.get('DAEMON_DOWN'): sys.exit(1)
        if args[-1] == '{{json .SecurityOptions}}':
            if os.environ.get('SECURITY_INFO_FAIL'): sys.exit(1)
            print(os.environ.get('ENGINE_SECURITY', '[]'))
        else:
            print(os.environ.get('ENGINE_OS', 'linux'))
    elif args[:2] == ['volume', 'ls']:
        if os.environ.get('DAEMON_DOWN'): sys.exit(1)
        print(os.environ.get('VOLUMES', ''))
    elif args[:2] == ['image', 'inspect']:
        if os.environ.get('NO_IMAGE'): sys.exit(1)
        if '--format' in args: print('sha256:fixture')
    elif args[0] == 'ps':
        if 'label=dev.blackhole.proxy=1' in args:
            print(os.environ.get('RELAY_IDS' if '-aq' in args else 'RUNNING_RELAYS', ''))
        elif any('.ID' in a for a in args): print(os.environ.get('SESSION_ROWS', ''))
    elif args[0] == 'stop' and os.environ.get('STOP_FAIL'): sys.exit(1)
    elif args[0] == 'run':
        if '--entrypoint' in args:
            entry = args[args.index('--entrypoint') + 1]
            if entry == 'claude': print('fixture-version')
            elif entry == 'blackhole-registry':
                profile = next(a.split('=', 1)[1] for a in args if a.startswith('BLACKHOLE_PROFILE=')) if '-e' in args else 'default'
                if args[-1] == 'lookup': sys.exit(1)
                if 'lookup' in args:
                    print('abc-123\tdefault\t' + os.environ['PROJECT'] + '\t2026-11-10\t/home/node/transcript.jsonl')
                elif args[-1] == 'projects' and os.environ.get('REGISTRY_ROWS'):
                    print(profile + '\t' + os.environ['PROJECT'] + '\t2026-11-10\t1')
                elif args[-1] == 'sessions' and os.environ.get('REGISTRY_ROWS'):
                    print(profile + '\tabc-123\t' + os.environ['PROJECT'] + '\t2026-11-10\t2026-11-10\tended')
            elif entry == 'cat': print('{"type":"fixture"}')
'''


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blackhole-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home"
        self.project = self.home / 'project 空间,"quoted"'
        self.project.mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "calls.jsonl"
        for tool in ("docker", "colima", "uname", "column", "powershell.exe", "wslpath", "flock"):
            stub = self.bin / tool
            stub.write_text(STUB, encoding="utf-8")
            stub.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("BLACKHOLE_", "DOCKER_", "ANTHROPIC_", "CLAUDE_"))}
        self.env.update(HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        CALL_LOG=str(self.log), PROJECT=str(self.project),
                        BLACKHOLE_STATE_DIR=str(self.base / "state"), NO_COLOR="1")

    def run_cli(self, *args, cwd=None, ok=True, **env):
        result = subprocess.run(["bash", str(LAUNCHER), *args], cwd=cwd or self.project,
                                env={**self.env, **env}, text=True, capture_output=True, timeout=10)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def calls(self, tool="docker"):
        if not self.log.exists(): return []
        return [row for row in map(json.loads, self.log.read_text().splitlines()) if row[0] == tool]

    def launched(self):
        return [args for _, args, _ in self.calls() if args[0] == "run" and '--label' in args]

    def test_linux_auto_uses_existing_engine_and_exact_project_mount(self):
        self.run_cli("-p", "hello world", DOCKER_HOST="unix:///tmp/chosen.sock")
        self.assertEqual(self.calls("colima"), [])
        self.assertTrue(all(host == "unix:///tmp/chosen.sock" for _, _, host in self.calls()))
        args, = self.launched()
        mount = next(csv.reader(io.StringIO(args[args.index('--mount') + 1])))
        self.assertEqual(mount, ['type=bind', 'src=' + str(self.project.resolve()), 'dst=/workspace'])
        self.assertIn('--security-opt', args)
        self.assertIn('no-new-privileges', args)
        self.assertIn('--pids-limit', args)
        self.assertEqual(args[-3:], ['claude', '-p', 'hello world'])
        self.assertNotIn('--privileged', args)
        self.assertNotIn('/var/run/docker.sock:/var/run/docker.sock', args)
        self.assertFalse((self.base / 'state' / 'clipboard-watch').exists())

    def test_macos_auto_keeps_colima_socket_and_mount_probe(self):
        self.run_cli("-p", "test", FAKE_OS="Darwin", BLACKHOLE_CLIPBOARD="0")
        self.assertTrue(self.calls("colima"))
        expected = 'unix://' + str(self.home) + '/.colima/default/docker.sock'
        self.assertTrue(all(host == expected for _, _, host in self.calls()))
        self.assertTrue(any('ssh' in args for _, args, _ in self.calls('colima')))

    def test_explicit_backend_and_context_are_respected(self):
        self.run_cli("shell", FAKE_OS="Darwin", BLACKHOLE_BACKEND="docker", BLACKHOLE_CLIPBOARD="0", DOCKER_CONTEXT="chosen")
        self.assertEqual(self.calls("colima"), [])
        self.assertEqual(self.launched()[0][-3:], ['bash', '--rcfile', '/usr/local/etc/blackhole-bashrc'])

    def test_invalid_backend_and_native_windows_get_actionable_errors(self):
        self.assertIn('BLACKHOLE_BACKEND', self.run_cli('help', ok=False, BLACKHOLE_BACKEND='bad').stderr)
        self.assertIn('WSL2', self.run_cli('help', ok=False, FAKE_OS='MINGW64_NT').stderr)
        self.assertEqual(self.calls(), [])

    def test_daemon_or_engine_errors_never_start_a_container(self):
        self.assertIn('socket permissions', self.run_cli('shell', ok=False, DAEMON_DOWN='1').stderr)
        self.assertIn('Linux Docker engine', self.run_cli('shell', ok=False, ENGINE_OS='windows').stderr)
        self.assertEqual(self.launched(), [])

    def test_rootless_sessions_fail_before_builds_networks_or_containers(self):
        for args in [('shell',), ('run',), ('-p', 'hello')]:
            result = self.run_cli(*args, ok=False, ENGINE_SECURITY='["name=rootless"]',
                                  NO_IMAGE='1', BLACKHOLE_PROXY='http://host:7890')
            self.assertIn('rootless Docker is not supported', result.stderr)
        self.assertFalse(any(args[0] in ('build', 'create', 'run', 'network')
                             for _, args, _ in self.calls()))

    def test_security_inspection_failure_does_not_start_a_session(self):
        self.run_cli('shell', ok=False, SECURITY_INFO_FAIL='1')
        self.assertEqual(self.launched(), [])

    def test_rootless_status_remains_available_without_launching_a_session(self):
        self.assertIn('shared engine', self.run_cli('status', ENGINE_SECURITY='["name=rootless"]').stdout)
        self.assertEqual(self.launched(), [])

    def test_broad_mounts_are_rejected_with_an_explicit_opt_in(self):
        directories = [self.home, Path('/')]
        if Path('/home').is_dir() and Path('/home').resolve() == Path('/home'):
            directories.append(Path('/home'))
        for directory in directories:
            self.run_cli('shell', cwd=directory, ok=False)
        self.assertEqual(self.launched(), [])
        self.run_cli('shell', cwd=self.home, BLACKHOLE_ALLOW_BROAD_MOUNT='1')

    def test_only_allowlisted_environment_is_forwarded(self):
        self.run_cli('-p', 'test', ANTHROPIC_API_KEY='test-fixture', PRIVATE_SECRET='do-not-forward')
        args = self.launched()[0]
        forwarded = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '-e']
        self.assertIn('ANTHROPIC_API_KEY', forwarded)
        self.assertFalse(any('PRIVATE_SECRET' in value for value in forwarded))
        self.assertFalse(any('--env-file' in value for value in args))

    def test_profiles_bind_and_inherit_without_creating_a_vm(self):
        self.run_cli('bind', 'work')
        child = self.project / 'child'
        child.mkdir()
        self.run_cli('-p', 'test', cwd=child)
        self.assertIn('blackhole-claude-home-work:/home/node', self.launched()[0])
        result = self.run_cli('profiles', VOLUMES='blackhole-claude-home-other\nunrelated')
        self.assertIn('work', result.stdout)
        self.assertIn('other', result.stdout)
        self.assertNotIn('unrelated', result.stdout)
        self.assertEqual(self.calls('colima'), [])

    def test_lists_work_without_optional_column_utility(self):
        startup = self.base / 'without-column.bash'
        startup.write_text('''command() {
    if [[ "$*" == "-v column" ]]; then return 1; fi
    builtin command "$@"
}
''')
        self.run_cli('bind', 'work')
        for command in ('profiles', 'bindings', 'projects', 'sessions'):
            result = self.run_cli(command, BASH_ENV=str(startup), REGISTRY_ROWS='1')
            self.assertIn('\t', result.stdout)
        self.assertEqual(self.calls('column'), [])

    def test_generic_profile_override_precedes_legacy_and_binding(self):
        self.run_cli('bind', 'bound')
        self.run_cli('-p', 'test', BLACKHOLE_PROFILE='generic', BLACKHOLE_COLIMA_PROFILE='legacy')
        self.assertIn('blackhole-claude-home-generic:/home/node', self.launched()[0])
        self.run_cli('-p', 'test', BLACKHOLE_COLIMA_PROFILE='legacy')
        self.assertIn('blackhole-claude-home-legacy:/home/node', self.launched()[1])
        self.run_cli('shell', ok=False, BLACKHOLE_PROFILE='../bad')

    def test_network_and_clipboard_commands_fail_before_any_side_effect(self):
        for args in [('net-watch', 'start'), ('__net-watch',), ('__net-guard',),
                     ('clipboard', 'start'), ('__clipboard-watch',)]:
            self.run_cli(*args, ok=False)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.calls('colima'), [])
        self.assertFalse((self.base / 'state').exists())

    def test_proxy_mode_uses_internal_network_and_bounded_unprivileged_relay(self):
        self.run_cli('-p', 'test', BLACKHOLE_PROXY='http://windows-host:7890')
        calls = [args for _, args, _ in self.calls()]
        network = next(args for args in calls if args[:2] == ['network', 'create'])
        self.assertIn('--internal', network)
        relay = next(args for args in calls if args[0] == 'create')
        self.assertIn('node', relay)
        self.assertIn('64m', relay)
        self.assertIn('0.25', relay)
        self.assertIn('ALL', relay)
        self.assertNotIn('-v', relay)
        args = self.launched()[0]
        self.assertEqual(args[args.index('--network') + 1], network[-1])
        self.assertIn('HTTPS_PROXY=http://blackhole-proxy:3128', args)
        self.assertEqual(args[args.index('--dns') + 1], '127.0.0.1')
        self.assertNotIn('--dns', relay)
        self.assertTrue(any(a[:2] == ['rm', '-f'] for a in calls))
        self.assertTrue(any(a[:2] == ['network', 'rm'] for a in calls))
        self.run_cli('-p', 'new terminal')
        self.assertIn('dev.blackhole.guard=proxy', self.launched()[1])

    def test_proxy_validation_never_falls_back_to_direct(self):
        for proxy in ['https://host:7890', 'http://user:password@host:7890', 'http://host:0', 'http://host:99999']:
            self.run_cli('shell', ok=False, BLACKHOLE_PROXY=proxy)
        self.assertEqual(self.launched(), [])

    def test_proxy_net_off_latches_future_sessions_and_on_releases_latch(self):
        self.run_cli('shell', BLACKHOLE_PROXY='http://windows-host:7890')
        self.run_cli('net', 'off')
        self.assertIn('offline', self.run_cli('net', 'status').stdout)
        self.log.unlink()
        self.run_cli('shell')
        self.assertFalse(any(args[0] == 'start' for _, args, _ in self.calls()))
        self.assertIn('--network', self.launched()[0])
        self.run_cli('net', 'on')
        self.assertIn('proxy-only', self.run_cli('net').stdout)

    def test_net_control_does_not_claim_to_protect_unconfigured_profiles(self):
        self.assertIn('unguarded', self.run_cli('net', 'status').stdout)
        self.assertIn('BLACKHOLE_PROXY', self.run_cli('net', 'off', ok=False).stderr)
        self.assertIn('unknown profile', self.run_cli('net', 'off', '--profile', 'typo', ok=False).stderr)

    def test_net_all_processes_configured_profiles_after_unconfigured_ones(self):
        self.run_cli('shell', BLACKHOLE_PROFILE='work', BLACKHOLE_PROXY='http://host:7890')
        self.run_cli('net', 'off', '--all', ok=False)
        self.assertTrue((self.base / 'state/docker-proxy/work.proxy.blocked').exists())

    def test_network_status_reports_observed_unguarded_or_failed_stop(self):
        self.run_cli('shell', BLACKHOLE_PROXY='http://host:7890')
        self.run_cli('net', 'off', ok=False, RELAY_IDS='fixture', STOP_FAIL='1')
        self.assertIn('BLOCK-FAILED', self.run_cli('net', 'status', RUNNING_RELAYS='fixture').stdout)
        self.assertIn('unguarded-sessions', self.run_cli('net', 'status', SESSION_ROWS='fixture|').stdout)
        result = self.run_cli('net', 'on', ok=False, SESSION_ROWS='fixture|')
        self.assertIn('unguarded sessions', result.stderr)
        self.assertTrue((self.base / 'state/docker-proxy/default.proxy.blocked').exists())

    def test_windows_clipboard_is_on_demand_and_saves_inside_workspace(self):
        result = self.run_cli('clipboard', 'paste', 'images/screenshot.png')
        self.assertEqual(result.stdout.strip(), '/workspace/images/screenshot.png')
        self.assertTrue((self.project / 'images/screenshot.png').read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.calls('powershell.exe')), 1)
        self.assertFalse(list((self.project / 'images').glob('.blackhole-clipboard.*')))

    def test_clipboard_failure_traversal_symlink_and_overwrite_are_safe(self):
        self.run_cli('clipboard', 'paste', 'missing.png', ok=False, CLIPBOARD_EMPTY='1')
        self.assertFalse((self.project / 'missing.png').exists())
        self.assertFalse(list(self.project.glob('.blackhole-clipboard.*')))
        for destination in ['../escape.png', '/tmp/escape.png']:
            self.run_cli('clipboard', 'paste', destination, ok=False)
        (self.project / 'outside').symlink_to(self.base, target_is_directory=True)
        self.run_cli('clipboard', 'paste', 'outside/new/sub/escape.png', ok=False)
        self.assertFalse((self.base / 'new').exists())
        dest = self.project / 'existing.png'
        dest.write_bytes(b'original')
        self.run_cli('clipboard', 'paste', 'existing.png', ok=False)
        self.assertEqual(dest.read_bytes(), b'original')
        self.assertFalse((self.base / 'escape.png').exists())

    def test_clipboard_destination_symlink_created_during_capture_is_not_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        self.run_cli('clipboard', 'paste', 'capture.png', ok=False, CLIPBOARD_RACE=str(outside))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((self.project / 'capture.png').is_symlink())
        self.assertFalse(list(self.project.glob('.blackhole-clipboard.*')))

    def test_status_probes_are_read_only_without_network_or_home(self):
        self.assertIn('shared engine', self.run_cli('status').stdout)
        args = [args for _, args, _ in self.calls() if args[0] == 'run']
        self.assertEqual(len(args), 1)
        self.assertIn('--read-only', args[0])
        self.assertEqual(args[0][args[0].index('--network') + 1], 'none')
        self.assertNotIn('-v', args[0])

    def test_registry_helpers_are_read_only_and_profile_filtered(self):
        self.run_cli('sessions', VOLUMES='blackhole-claude-home-work', REGISTRY_ROWS='1')
        for _, args, _ in self.calls():
            if args[0] == 'run':
                self.assertEqual(args[args.index('--network') + 1], 'none')
                self.assertTrue(args[args.index('-v') + 1].endswith(':/home/node:ro'))
            if args[0] == 'ps':
                self.assertTrue(any(a.startswith('label=dev.blackhole.profile=') for a in args))

    def test_profile_summary_uses_two_helpers_and_handles_empty_registry(self):
        result = self.run_cli('profiles')
        self.assertIn('yes\t0\t0\t-', result.stdout)
        self.assertEqual(sum(args[0] == 'run' for _, args, _ in self.calls()), 2)

    def test_registry_failure_is_not_reported_as_no_sessions(self):
        for command in ['profiles', 'projects', 'sessions']:
            self.assertIn('cannot list profiles', self.run_cli(command, ok=False, DAEMON_DOWN='1').stderr)

    def test_resume_reexec_uses_selected_profile_over_host_override(self):
        self.run_cli('resume', 'abc', BLACKHOLE_PROFILE='work')
        self.assertIn('blackhole-claude-home-default:/home/node', self.launched()[0])
        self.assertEqual(self.launched()[0][-2:], ['--resume', 'abc-123'])

    def test_export_is_read_only_and_writes_complete_transcript(self):
        dest = self.base / 'exports' / 'session.jsonl'
        self.run_cli('export', 'abc', '-o', str(dest))
        self.assertEqual(json.loads(dest.read_text()), {'type': 'fixture'})
        self.assertFalse(list(dest.parent.glob('.blackhole-export.*')))

    def test_setup_and_project_image_build_reuse_engine(self):
        config = self.project / '.blackhole'
        config.mkdir()
        (config / 'Dockerfile').write_text('FROM blackhole-claude:latest\n')
        self.run_cli('setup', '--claude-version', '1.2.3')
        builds = [args for _, args, _ in self.calls() if args[0] == 'build']
        self.assertEqual(len(builds), 2)
        self.assertIn('CLAUDE_CODE_VERSION=1.2.3', builds[0])
        self.assertEqual(builds[1][-1], str(config))
        self.assertEqual(self.calls('colima'), [])

    def test_fuse_remains_explicit_and_reduces_isolation_only_when_enabled(self):
        self.run_cli('shell', BLACKHOLE_FUSE='1')
        args = self.launched()[0]
        self.assertIn('/dev/fuse', args)
        self.assertIn('SYS_ADMIN', args)
        self.assertNotIn('no-new-privileges', args)

    def test_install_links_and_symlink_argument_passthrough(self):
        prefix = self.home / '.local' / 'bin'
        self.run_cli('install', '--prefix', str(prefix))
        link = prefix / 'claude'
        self.assertEqual(link.resolve(), LAUNCHER)
        result = subprocess.run([str(link), '-p', 'hello'], cwd=self.project,
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched()[0][-2:], ['-p', 'hello'])

    def test_shell_syntax_and_lf_checkout(self):
        for path in [LAUNCHER, *ROOT.glob('*.sh'), *ROOT.glob('lib/*.sh')]:
            self.assertNotIn(b'\r', path.read_bytes(), str(path))
            subprocess.run(['bash', '-n', str(path)], check=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
