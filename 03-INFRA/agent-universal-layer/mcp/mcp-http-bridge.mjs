#!/usr/bin/env node
/**
 * Launch a pinned mcp-remote bridge without writing bearer tokens to config.
 *
 * Antigravity on Windows can mangle spaces inside stdio arguments. The
 * generated config therefore passes only a URL and the NAME of the bearer
 * environment variable. This wrapper derives an environment-only header and
 * gives mcp-remote a no-space placeholder argument that it expands itself.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const EXACT_PACKAGE = /^mcp-remote@\d+\.\d+\.\d+$/;
const HEADER_ENV = 'NEXGEN_MCP_AUTH_HEADER';

/**
 * Add the headers MCP revision 2026-07-28 requires but mcp-remote never sends.
 *
 * The revision requires `Mcp-Method` on every Streamable HTTP POST, and
 * `Mcp-Name` whenever the body names a target, so that headers and body cannot
 * disagree. mcp-remote predates it -- 0.1.38 is its LAST published version, so
 * waiting for an upstream fix is not a plan -- and a server that enforces the
 * revision answers every request with HTTP 400. The client then never receives
 * a tool list and waits for it forever, which reads as a hang rather than as
 * the protocol error it is (measured against n8n, 2026-08-17).
 *
 * Everything else mcp-remote does -- SSE, sessions, retries, OAuth discovery --
 * is correct, so this does not reimplement the transport. It puts a loopback
 * shim in front of it that derives the missing headers from the body already
 * being sent. Bound to 127.0.0.1 on an ephemeral port: the bearer stays in the
 * headers mcp-remote already sets, never in an argument or a config file.
 */
export function deriveRevisionHeaders(body) {
  const headers = {};
  let doc;
  try {
    doc = JSON.parse(body || '{}');
  } catch {
    // Not JSON (an OAuth discovery GET, say): a body we cannot read is one we
    // must not describe in a header. Forward it untouched.
    return headers;
  }
  if (!doc || typeof doc !== 'object')
    return headers;
  if (typeof doc.method === 'string')
    headers['mcp-method'] = doc.method;
  const params = (doc.params && typeof doc.params === 'object') ? doc.params : {};
  if (typeof params.name === 'string')
    headers['mcp-name'] = params.name;
  const meta = (params._meta && typeof params._meta === 'object') ? params._meta : {};
  const revision = meta['io.modelcontextprotocol/protocolVersion'];
  if (typeof revision === 'string')
    headers['mcp-protocol-version'] = revision;
  return headers;
}

function startHeaderShim(target, onReady) {
  const upstream = new URL(target);
  const transport = upstream.protocol === 'https:' ? https : http;

  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const body = Buffer.concat(chunks);
      const headers = { ...req.headers, host: upstream.host };
      delete headers['content-length'];

      Object.assign(headers, deriveRevisionHeaders(body.toString('utf8')));
      if (body.length)
        headers['content-length'] = String(body.length);

      const proxied = transport.request(
        { protocol: upstream.protocol, hostname: upstream.hostname, port: upstream.port,
          path: req.url, method: req.method, headers },
        (answer) => {
          res.writeHead(answer.statusCode ?? 502, answer.headers);
          answer.pipe(res);   // streamed, so an SSE response is not buffered
        },
      );
      proxied.on('error', (error) => {
        if (!res.headersSent)
          res.writeHead(502, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ error: `mcp-http-bridge shim: ${error.message}` }));
      });
      if (body.length)
        proxied.write(body);
      proxied.end();
    });
  });

  server.on('error', (error) => {
    fail(`header shim could not start: ${error.message}`);
    process.exit(1);
  });
  server.listen(0, '127.0.0.1', () => {
    const { port } = server.address();
    onReady(`http://127.0.0.1:${port}${upstream.pathname}${upstream.search}`, server);
  });
}

function npmCliPath() {
  const candidates = [
    process.env.npm_execpath,
    path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js'),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) || null;
}

function withNodeOnPath(extra = {}) {
  const env = { ...process.env, ...extra };
  const key = Object.keys(env).find((name) => name.toLowerCase() === 'path') || 'PATH';
  const nodeDir = path.dirname(process.execPath);
  const entries = [nodeDir, ...(env[key] || '').split(path.delimiter).filter(Boolean)];
  const seen = new Set();
  env[key] = entries.filter((entry) => {
    const normalized = entry.trim().replace(/[\\/]+$/, '').toLowerCase();
    if (!normalized || seen.has(normalized))
      return false;
    seen.add(normalized);
    return true;
  }).join(path.delimiter);
  return env;
}

function fail(message) {
  console.error(`mcp-http-bridge: ${message}`);
  process.exitCode = 1;
}

function main() {
  if (process.argv[2] === '--self-test') {
    const packagePin = process.argv[3];
    if (!EXACT_PACKAGE.test(packagePin || ''))
      throw new Error('self-test requires an exact mcp-remote package pin');
    if (process.platform === 'win32' && !npmCliPath())
      throw new Error('npm-cli.js was not found beside node.exe');
    return;
  }

  const [, , url, tokenEnvName, packagePin] = process.argv;
  if (!/^https?:\/\//.test(url || ''))
    throw new Error('a valid HTTP(S) MCP URL is required');
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnvName || ''))
    throw new Error('a valid bearer environment-variable name is required');
  if (!EXACT_PACKAGE.test(packagePin || ''))
    throw new Error('mcp-remote must be pinned to an exact version');
  const token = process.env[tokenEnvName];
  if (!token)
    throw new Error(`required environment variable ${tokenEnvName} is missing`);

  const env = withNodeOnPath({ [HEADER_ENV]: `Bearer ${token}` });
  // mcp-remote talks to the shim, the shim talks to the real server: that is
  // the only way to add a per-request header to requests mcp-remote builds
  // itself.
  startHeaderShim(url, (shimUrl, shim) => launch(shimUrl, shim, packagePin, env));
}

function launch(url, shim, packagePin, env) {
  const args = [
    'exec', '--yes', `--package=${packagePin}`, '--', 'mcp-remote', url,
    '--header', `Authorization:\${${HEADER_ENV}}`,
  ];
  const npmCli = npmCliPath();
  const command = npmCli ? process.execPath : 'npm';
  const commandArgs = npmCli ? [npmCli, ...args] : args;
  const child = spawn(command, commandArgs, { stdio: 'inherit', env });

  const forward = (signal) => child.kill(signal);
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP'])
    process.on(signal, forward);
  child.on('error', (error) => fail(`unable to launch ${packagePin}: ${error.message}`));
  child.on('exit', (code, signal) => {
    shim.close();
    if (signal) {
      // Set a truthful exit code BEFORE re-signaling self: if the signal
      // has a default "ignore" disposition in this wrapper (SIGPIPE is
      // SIG_IGN in Node), the re-signal is a no-op and the process then
      // exits via an emptied event loop -- exitCode is what makes that
      // death report the child's fate instead of a fake 0.
      process.exitCode = 128 + (os.constants.signals[signal] ?? 0);
      // The forwarding handler above would swallow a re-signal to self.
      // Detach it first so the signal reaches the default handler and
      // actually terminates the wrapper (previously: orphaned wrapper).
      process.removeListener(signal, forward);
      process.kill(process.pid, signal);
    } else {
      process.exitCode = code ?? 1;
    }
  });
}

function invokedAsProgram() {
  if (!process.argv[1])
    return false;
  // Compare what the two sides really resolve to. Windows filesystems are
  // case-insensitive while string comparison is not, and the launcher's path
  // need not match the module's letter for letter (C:\Users vs c:\users, an 8.3
  // short path, a symlinked engine root). Getting this wrong fails silently:
  // the bridge would exit without starting, and the MCP server would look dead
  // for reasons nothing reports.
  const settle = (target) => {
    try {
      return fs.realpathSync(target);
    } catch {
      return path.resolve(target);
    }
  };
  const launched = settle(process.argv[1]);
  const self = settle(fileURLToPath(import.meta.url));
  return process.platform === 'win32'
    ? launched.toLowerCase() === self.toLowerCase()
    : launched === self;
}

// Only run when launched as a program. Imported (by a test asking what headers
// a body derives), the module must expose its logic without spawning anything.
if (invokedAsProgram()) {
  try {
    main();
  } catch (error) {
    fail(error.message);
  }
}
