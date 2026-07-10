# Architecture

## Current known flow

### Park scoring

```text
Scraped Google reviews
  ↓
score_parks.py
  ↓
Claude scoring prompt
  ↓
Batch aggregation
  ↓
locations/{state}/{slug}/scores.json
  ↓
Generated pages and leaderboards
```

### Review form

```text
public/leave-a-review.html
  ↓
Browser JavaScript
  ↓
N8N_WEBHOOK_URL placeholder
  ↓
External n8n workflow
  ↓
External Airtable setup
```

## Target review flow

```text
Public review form
  ↓
Cloudflare Worker `/reviews`
  ↓
Validation / anti-spam / normalisation
  ↓
n8n
  ↓
Airtable
  ↓
Scoring workflow
  ↓
Moderation
  ↓
Publication and report-card generation
```

## Recommended repository additions

```text
scoring/
  schema.json
  rubric.md
  prompt.md

docs/
  vision.md
  review-system.md
  scoring-system.md
  holiday-report.md
  architecture.md
  deployment.md
```

## Technical decisions to confirm

- Whether `schema.json` is loaded directly by Python and Worker code
- Whether the Worker forwards to n8n or writes directly to Airtable
- Whether image uploads use a separate signed-upload flow
- Where raw verification documents are stored
- Whether moderation happens in Airtable
- How published reviews flow back into the static-site build
- How individual family scores affect, or do not affect, park-level aggregate scores

## Security priorities

- No public webhook secrets
- No Airtable tokens in browser code
- Server-side validation
- Payload size limits
- File type validation
- Rate limiting
- Bot protection
- Explicit field allow-list
- Logging without leaking personal information
