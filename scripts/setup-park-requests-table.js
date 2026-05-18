/**
 * Creates "Park Requests" in Airtable and updates api-proxy/wrangler.toml.
 * Run from repo root: node scripts/setup-park-requests-table.js
 */

const fs = require('fs');
const path = require('path');

const TABLE_NAME = 'Park Requests';
const WRANGLER_TOML = path.join(__dirname, '..', 'api-proxy', 'wrangler.toml');

function loadEnv() {
  const envPath = path.join(__dirname, '..', '.env');
  if (!fs.existsSync(envPath)) {
    throw new Error('Missing .env file. Add AIRTABLE_TOKEN and AIRTABLE_BASE_ID.');
  }

  const env = {};
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    env[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return env;
}

async function airtableRequest(token, url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }

  if (!res.ok) {
    const detail = typeof body === 'object' ? JSON.stringify(body) : body;
    throw new Error(`Airtable API ${res.status}: ${detail}`);
  }

  return body;
}

function updateWranglerParkRequestsTableId(tableId) {
  let toml = fs.readFileSync(WRANGLER_TOML, 'utf8');
  if (toml.includes('AIRTABLE_PARK_REQUESTS_TABLE_ID')) {
    toml = toml.replace(
      /AIRTABLE_PARK_REQUESTS_TABLE_ID = ".*"/,
      `AIRTABLE_PARK_REQUESTS_TABLE_ID = "${tableId}"`
    );
  } else {
    toml = toml.replace(
      /AIRTABLE_TABLE_ID = ".*"/,
      (match) => `${match}\nAIRTABLE_PARK_REQUESTS_TABLE_ID = "${tableId}"`
    );
  }
  fs.writeFileSync(WRANGLER_TOML, toml, 'utf8');
}

async function main() {
  const env = loadEnv();
  const token = env.AIRTABLE_TOKEN;
  const baseId = env.AIRTABLE_BASE_ID;

  if (!token) throw new Error('AIRTABLE_TOKEN is not set in .env');
  if (!baseId) throw new Error('AIRTABLE_BASE_ID is not set in .env');

  const meta = await airtableRequest(
    token,
    `https://api.airtable.com/v0/meta/bases/${baseId}/tables`
  );

  let table = (meta.tables || []).find((t) => t.name === TABLE_NAME);

  if (table) {
    console.log(`Table "${TABLE_NAME}" already exists (id: ${table.id}).`);
  } else {
    console.log(`Creating table "${TABLE_NAME}"...`);
    table = await airtableRequest(
      token,
      `https://api.airtable.com/v0/meta/bases/${baseId}/tables`,
      {
        method: 'POST',
        body: JSON.stringify({
          name: TABLE_NAME,
          fields: [
            { name: 'Park Name', type: 'singleLineText' },
            { name: 'Email', type: 'email' },
          ],
        }),
      }
    );
    console.log(`Created table (id: ${table.id}).`);
  }

  updateWranglerParkRequestsTableId(table.id);
  console.log(`Updated ${WRANGLER_TOML}`);
}

main().catch((err) => {
  console.error(err.message || err);
  process.exit(1);
});
