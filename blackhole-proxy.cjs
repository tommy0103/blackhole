#!/usr/bin/env node
// A TCP relay to one explicit HTTP proxy. No target selection or direct fallback.
const net = require('node:net');
const upstream = new URL(process.env.BLACKHOLE_PROXY);
if (upstream.protocol !== 'http:' || upstream.host !== upstream.href.split('/')[2]) {
    throw new Error('BLACKHOLE_PROXY must be http://host:port without credentials');
}
const sockets = new Set();
const server = net.createServer({ maxConnections: 64 }, (client) => {
    const remote = net.createConnection({ host: upstream.hostname, port: Number(upstream.port || 80) });
    sockets.add(client);
    sockets.add(remote);
    const close = () => { client.destroy(); remote.destroy(); sockets.delete(client); sockets.delete(remote); };
    client.on('error', close).on('close', close);
    remote.on('error', close).on('close', close);
    remote.setTimeout(300000, close);
    client.pipe(remote).pipe(client);
});
server.maxConnections = 64;
server.listen(3128, '0.0.0.0');
process.on('SIGTERM', () => {
    for (const socket of sockets) socket.destroy();
    server.close(() => process.exit(0));
});
