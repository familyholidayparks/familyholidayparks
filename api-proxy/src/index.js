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

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin');

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

    let payload;
    try {
      payload = await request.json();
    } catch {
      return json({ error: { message: 'Invalid JSON body' } }, 400, origin);
    }

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
    const airtableUrl = `https://api.airtable.com/v0/${env.AIRTABLE_BASE_ID}/${env.AIRTABLE_TABLE_ID}`;

    const airtableRes = await fetch(airtableUrl, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${env.AIRTABLE_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        records: [
          {
            fields: {
              Name: name,
              Email: email,
              Instagram: instagram,
              'Date Submitted': today,
              Status: 'New',
            },
          },
        ],
      }),
    });

    const airtableText = await airtableRes.text();
    if (!airtableRes.ok) {
      return new Response(airtableText, {
        status: airtableRes.status,
        headers: {
          'Content-Type': 'application/json',
          ...corsHeaders(origin),
        },
      });
    }

    const n8nRes = await fetch(N8N_WEBHOOK_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, instagram }),
    });

    if (!n8nRes.ok) {
      return json({ error: { message: 'Failed to process submission' } }, 502, origin);
    }

    return new Response(airtableText, {
      status: airtableRes.status,
      headers: {
        'Content-Type': 'application/json',
        ...corsHeaders(origin),
      },
    });
  },
};
