"""Test the local entrypoint in an existing image; no builds, mounts, or network.

Run: python3 tests/smoke_permissions.py [IMAGE]
The single disposable container has bounded resources and never runs Claude.
"""
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
script = "cat >/tmp/entrypoint-under-test <<'BLACKHOLE_ENTRYPOINT'\n"
script += (root / 'docker-entrypoint.sh').read_text() + '\nBLACKHOLE_ENTRYPOINT\n'
script += r'''
set -euo pipefail

# Root-owned Unix permissions must produce an actionable error before config
# writes, instead of starting a session that cannot edit its project.
chmod 0755 /workspace
printf original >/workspace/existing.txt
chmod 0644 /workspace/existing.txt
if bash /tmp/entrypoint-under-test true >/tmp/output 2>&1; then
    echo 'FAIL: accepted an unwritable root-owned workspace' >&2
    exit 1
fi
grep -q 'root-owned workspace is not writable' /tmp/output
test "$(cat /workspace/existing.txt)" = original
echo 'PASS: unwritable root-owned workspace rejected'

# Windows-style mounts may expose root ownership with writable modes. Keep
# the node identity unprivileged in this case.
chmod 0777 /workspace
chmod 0666 /workspace/existing.txt
bash /tmp/entrypoint-under-test bash -c '
    test "$(id -u)" != 0
    printf windows >/workspace/existing.txt
    touch /workspace/windows-new.txt
'
test "$(cat /workspace/existing.txt)" = windows
echo 'PASS: writable root-owned workspace uses non-root node'

# Ordinary Linux ownership must work with 0755 directories and 0644 files.
chown -R 12345:12345 /workspace
chmod 0755 /workspace
chmod 0644 /workspace/existing.txt
bash /tmp/entrypoint-under-test bash -c '
    test "$(id -u)" = 12345
    test "$(id -g)" = 12345
    printf linux >/workspace/existing.txt
    touch /workspace/linux-new.txt
'
test "$(cat /workspace/existing.txt)" = linux
test "$(stat -c %u /workspace/linux-new.txt)" = 12345
test "$(stat -c %a /workspace)" = 755
test "$(stat -c %a /workspace/existing.txt)" = 644
echo 'PASS: ordinary 0755/0644 workspace remains writable with matched UID/GID'
'''

subprocess.run([
    'docker', 'run', '--rm', '-i', '--network', 'none',
    '--memory', '128m', '--cpus', '0.5', '--pids-limit', '64',
    '--security-opt', 'no-new-privileges', '--entrypoint', 'bash',
    sys.argv[1] if len(sys.argv) > 1 else 'blackhole-claude:latest', '-s',
], input=script, text=True, check=True, timeout=60)
