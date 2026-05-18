const ALLOWED_ORIGINS = new Set([
  'https://familyholidayparks.com.au',
  'https://www.familyholidayparks.com.au',
]);

const N8N_WEBHOOK_URL =
  'https://familyholidayparks.app.n8n.cloud/webhook/6beb95d0-3dfa-4911-9a9f-b034f8a242ea';

function isAllowedOrigin(origin) {
  return origin && ALLOWED_ORIGINS.has(origin);
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Max-Age': '86400',
  };
}

function json(body, status, origin) {
  const headers = { 'Content-Type': 'application/json' };
  if (origin && isAllowedOrigin(origin)) {
    Object.assign(headers, corsHeaders(origin));
  }
  return new Response(JSON.stringify(body), { status, headers });
}

async function parseJsonBody(request, origin) {
  try {
    return await request.json();
  } catch {
    return { error: json({ error: { message: 'Invalid JSON body' } }, 400, origin) };
  }
}

async function postToAirtable(env, tableId, fields, origin) {
  const airtableUrl = `https://api.airtable.com/v0/${env.AIRTABLE_BASE_ID}/${tableId}`;

  const airtableRes = await fetch(airtableUrl, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.AIRTABLE_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ records: [{ fields }] }),
  });

  const airtableText = await airtableRes.text();
  if (!airtableRes.ok) {
    return {
      ok: false,
      response: new Response(airtableText, {
        status: airtableRes.status,
        headers: {
          'Content-Type': 'application/json',
          ...corsHeaders(origin),
        },
      }),
    };
  }

  return { ok: true, text: airtableText, status: airtableRes.status };
}

async function handleLeads(request, env, origin) {
  const parsed = await parseJsonBody(request, origin);
  if (parsed.error) return parsed.error;
  const payload = parsed;

  const name = String(payload.name || '').trim();
  const email = String(payload.email || '').trim();
  const instagram = String(payload.instagram || '').trim();

  if (!name || !email || !instagram) {
    return json({ error: { message: 'Missing required fields' } }, 400, origin);
  }

  if (!email.includes('@')) {
    return json({ error: { message: 'Invalid email address' } }, 400, origin);
  }

  const today = new Date().toISOString().slice(0, 10);
  const result = await postToAirtable(
    env,
    env.AIRTABLE_TABLE_ID,
    {
      Name: name,
      Email: email,
      Instagram: instagram,
      'Date Submitted': today,
      Status: 'New',
    },
    origin
  );

  if (!result.ok) return result.response;

  const n8nRes = await fetch(N8N_WEBHOOK_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({ name, email, instagram }),
  });

  if (!n8nRes.ok) {
    return json({ error: { message: 'Failed to process submission' } }, 502, origin);
  }

  return new Response(result.text, {
    status: result.status,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders(origin),
    },
  });
}

async function handleParkRequest(request, env, origin) {
  if (!env.AIRTABLE_PARK_REQUESTS_TABLE_ID) {
    return json({ error: { message: 'Server not configured' } }, 500, origin);
  }

  const parsed = await parseJsonBody(request, origin);
  if (parsed.error) return parsed.error;
  const payload = parsed;

  const parkName = String(
    payload.parkName || payload.park_name || payload.park || ''
  ).trim();
  const email = String(payload.email || '').trim();

  if (!parkName || !email) {
    return json({ error: { message: 'Missing required fields' } }, 400, origin);
  }

  if (!email.includes('@')) {
    return json({ error: { message: 'Invalid email address' } }, 400, origin);
  }

  const result = await postToAirtable(
    env,
    env.AIRTABLE_PARK_REQUESTS_TABLE_ID,
    {
      'Park Name': parkName,
      Email: email,
    },
    origin
  );

  if (!result.ok) return result.response;

  return new Response(result.text, {
    status: result.status,
    headers: {
      'Content-Type': 'application/json',
      ...corsHeaders(origin),
    },
  });
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin');
    const path = new URL(request.url).pathname.replace(/\/$/, '') || '/';

    if (request.method === 'OPTIONS') {
      if (!isAllowedOrigin(origin)) {
        return new Response(null, { status: 403 });
      }
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (request.method !== 'POST') {
      return json({ error: { message: 'Method not allowed' } }, 405, origin);
    }

    if (!isAllowedOrigin(origin)) {
      return json({ error: { message: 'Forbidden' } }, 403, origin);
    }

    if (!env.AIRTABLE_TOKEN) {
      return json({ error: { message: 'Server not configured' } }, 500, origin);
    }

    if (path === '/request-park') {
      return handleParkRequest(request, env, origin);
    }

    return handleLeads(request, env, origin);
  },
};
