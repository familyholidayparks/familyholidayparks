/**
 * Reads AIRTABLE_TOKEN from ../.env and sets it as a Wrangler secret (no prompt).
 * Run from api-proxy: node set-secret.js
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ENV_PATH = path.join(__dirname, '..', '.env');
const SECRET_NAME = 'AIRTABLE_TOKEN';

function loadEnv(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`Missing ${filePath}`);
  }
  const env = {};
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    env[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return env;
}

function resolveWranglerBin() {
  const local = path.join(
    __dirname,
    'node_modules',
    'wrangler',
    'bin',
    'wrangler.js'
  );
  if (fs.existsSync(local)) {
    return { cmd: process.execPath, args: [local] };
  }

  const localCmd = path.join(__dirname, 'node_modules', '.bin', 'wrangler.cmd');
  if (fs.existsSync(localCmd)) {
    return { cmd: localCmd, args: [] };
  }

  return { cmd: 'wrangler', args: [] };
}

function main() {
  const env = loadEnv(ENV_PATH);
  const token = env.AIRTABLE_TOKEN;

  if (!token) {
    throw new Error('AIRTABLE_TOKEN is not set in .env');
  }

  const { cmd, args: wranglerArgs } = resolveWranglerBin();
  const args = [...wranglerArgs, 'secret', 'put', SECRET_NAME];

  const result = spawnSync(cmd, args, {
    cwd: __dirname,
    input: token,
    stdio: ['pipe', 'inherit', 'inherit'],
    shell: cmd === 'wrangler',
    env: process.env,
  });

  if (result.error) {
    throw new Error(
      `Failed to run wrangler (${result.error.message}). Run: npm install`
    );
  }

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }

  console.log(`Set Wrangler secret ${SECRET_NAME} for api-proxy.`);
}

main();
