"""Exercise relay forwarding and upstream failure using local sockets only."""
import os
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import threading
import time
import unittest


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request.recv(4096)
        self.request.sendall(data)


@unittest.skipUnless(shutil.which('node'), 'Node is needed for relay tests')
class ProxyTests(unittest.TestCase):
    def test_explicit_http_port_80_is_normalized_without_rejection(self):
        source = str(Path(__file__).resolve().parents[1] / 'blackhole-proxy.cjs')
        script = r'''
const vm = require('vm'), fs = require('fs'), {EventEmitter} = require('events');
const socket = () => Object.assign(new EventEmitter(), {
    destroy() {}, setTimeout() {return this;}, pipe() {return this;}
});
let target;
const net = {
    createConnection(options) { target=options; return socket(); },
    createServer(options, handler) { return {listen(){handler(socket());}}; }
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    require: () => net, URL, Set,
    process: {env: {BLACKHOLE_PROXY: 'http://proxy.invalid:80'}, on() {}}
});
if (target.port !== 80 || target.host !== 'proxy.invalid') process.exit(2);
'''
        subprocess.run(['node', '-e', script, source], check=True, timeout=5)

    def test_relay_and_closed_upstream_have_no_direct_fallback(self):
        upstream = socketserver.TCPServer(('127.0.0.1', 0), Echo)
        self.addCleanup(upstream.server_close)
        worker = threading.Thread(target=upstream.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(upstream.shutdown)
        port = upstream.server_address[1]
        relay = subprocess.Popen(['node', str(Path(__file__).resolve().parents[1] / 'blackhole-proxy.cjs')],
                                 env={**os.environ, 'BLACKHOLE_PROXY': f'http://127.0.0.1:{port}'},
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def stop():
            if relay.poll() is None:
                relay.terminate()
            relay.communicate(timeout=5)
        self.addCleanup(stop)
        deadline = time.monotonic() + 5
        while True:
            self.assertIsNone(relay.poll(), 'relay exited before listening')
            try:
                client = socket.create_connection(('127.0.0.1', 3128), timeout=1)
                break
            except ConnectionRefusedError:
                if time.monotonic() > deadline: raise
                time.sleep(0.02)
        with client:
            client.sendall(b'CONNECT example.invalid:443 HTTP/1.1\r\n\r\n')
            self.assertEqual(client.recv(4096), b'CONNECT example.invalid:443 HTTP/1.1\r\n\r\n')
        upstream.shutdown()
        upstream.server_close()
        with socket.create_connection(('127.0.0.1', 3128), timeout=1) as client:
            client.sendall(b'GET http://example.invalid/ HTTP/1.1\r\n\r\n')
            try:
                self.assertEqual(client.recv(4096), b'')
            except ConnectionResetError:
                pass
        self.assertIsNone(relay.poll(), 'upstream failure must not crash the relay')
