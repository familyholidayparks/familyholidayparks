# AGENTS.md — Family Holiday Parks

## Mission

Family Holiday Parks helps Australian families find and compare the best family-friendly holiday parks.

Primary goal:

> Build Australia's largest and most trusted family holiday park review and comparison platform.

The platform should help families book with confidence through structured park data, real family reviews, transparent scoring, useful comparisons and practical destination information.

## Core product principles

- Mobile-first
- Clear before clever
- Family-friendly, not childish
- Trustworthy and transparent
- Useful, concise and easy to scan
- Australian English
- No exaggerated marketing claims
- Protect reviewer privacy
- Prefer one source of truth over duplicated logic
- Prefer source files and generators over manually editing generated output

## Current technical context

- Website: familyholidayparks.com.au
- Static site generation: Python
- Generated pages consume location score data from `locations/{state}/{slug}/scores.json`
- Hosting/deployment: GitHub and Cloudflare Pages
- Review form: static HTML with browser JavaScript
- Review form currently lives at `public/leave-a-review.html`
- A byte-identical duplicate currently exists as `leave-a-review (1).html`
- Review submissions currently post directly from the browser to an n8n webhook placeholder
- n8n and the review Airtable schema are external to the repository
- API proxy Worker currently handles leads and park requests, not review submissions
- AI coding tools: Claude Code and OpenAI Codex
- Editor: Cursor

## Official scoring model

All park and review scoring must use the same seven-category weighted model.

| Category | Weight |
|---|---:|
| Entertainment | 20 |
| Nature | 20 |
| Value | 15 |
| Cleanliness | 15 |
| Site Size | 10 |
| Sentiment | 10 |
| Location | 10 |
| **Total** | **100** |

Do not introduce a second scoring model unless explicitly approved.

## Current scoring implementation

The current park scoring logic lives mainly in `score_parks.py` inside `SCORING_PROMPT`.

Current behaviour:

- Claude receives batches of scraped review data.
- It returns seven category scores and a total score.
- Large review sets are split into batches.
- Batch scores are aggregated using review-count weighting.
- `total_score` is currently trusted from the model output and validated only as being between 0 and 100.
- The code does not currently recalculate the total deterministically from the seven category scores.

This must be improved so the total is always calculated in code from validated category scores.

## Required scoring architecture

The project should move to one source of truth:

```text
scoring/
  schema.json
  rubric.md
  prompt.md
```

Responsibilities:

- `schema.json`: category keys, labels, weights, allowed ranges and validation rules
- `rubric.md`: clear scoring guidance and examples for each category
- `prompt.md`: model instructions that reference the same category definitions

The code should:

1. Load the shared scoring schema.
2. Validate every category score.
3. Calculate the total score deterministically.
4. Reject or flag malformed model output.
5. Preserve raw model output for debugging where appropriate.
6. Avoid duplicating weights in Python, HTML or generated copy.

## Review system direction

The public review form should collect rich information conversationally.

Families should not be expected to understand the scoring system or manually calculate category scores.

The form should collect:

### Stay details

- Park
- Location
- Stay month/date
- Length of stay
- Accommodation type
- Site or cabin type
- Approximate price paid where appropriate

### Family details

- Family name
- Number of travellers
- For each traveller:
  - name or optional first name
  - relationship/description
  - age
  - gender using an inclusive option set

Example:

```json
{
  "description": "Dad",
  "age": 37,
  "gender": "Male"
}
```

### Experience details

Questions should naturally collect evidence for:

- Entertainment
- Nature
- Value
- Cleanliness
- Site Size
- Sentiment
- Location

The form should use a mix of:

- quick structured selections
- conversational free-text answers
- optional photos
- stay-again and recommendation questions
- verification evidence where offered

## Review submission architecture

Preferred flow:

```text
Browser
  ↓
Cloudflare Worker
  ↓
Validation and anti-spam checks
  ↓
n8n
  ↓
Airtable
  ↓
Scoring workflow
  ↓
Moderation
  ↓
Publication
```

Do not expose the n8n webhook directly in browser code.

The Worker should:

- validate required fields
- normalise payload shape
- reject malformed submissions
- apply rate limiting or anti-spam controls
- forward only approved fields
- keep secrets server-side
- return clear success/error responses

## Family Holiday Report

The review system should support personalised, shareable report cards.

Initial variants:

1. Photo card
2. Stick-figure family card
3. Editorial / AI-summary card

All variants should contain:

- Family Holiday Report heading
- Family name
- Park name and location
- Family composition
- Family Score out of 100
- Short summary
- One strong pull quote
- Stay date
- Family Holiday Parks branding

The score shown must use the official seven-category weighted model.

### Stick-figure direction

Start manually.

Desired style:

- minimal line drawing
- friendly and playful
- mostly white background
- restrained colour palette
- simple proportions
- recognisable family composition
- enough variation to feel personal
- not photorealistic
- not overly corporate

Do not automate image generation until the style and prompt are manually tested and approved.

## Privacy

Never expose publicly:

- reviewer email addresses
- phone numbers
- booking confirmations
- raw verification files
- private family details not approved for publication
- internal scoring prompts
- API keys or credentials

Verification evidence may mark a review as verified but must remain private.

## Generated files

Before editing HTML, determine whether it is generated or a source file.

Prefer editing:

- generators
- source templates
- schemas
- data
- configuration

Do not manually patch generated pages unless explicitly necessary.

## Git safety

Claude Code and Codex may both work on this repository.

Rules:

- Do not let both agents edit the same files at the same time.
- Run `git status` before beginning.
- Do not discard uncommitted work.
- Review diffs before committing.
- Commit stable checkpoints.
- Do not force-reset, clean or overwrite without explicit approval.
- Do not deploy or push to production without explicit approval.

## Required workflow for large changes

Before writing code:

1. Read this file.
2. Read the relevant documents in `docs/`.
3. Inspect the repository.
4. Run `git status`.
5. Identify source files and generated files.
6. Summarise the current data flow.
7. Propose a short implementation plan.
8. List unknown external dependencies.
9. Make no production changes until the plan is approved.

## Current implementation order

1. Create the shared scoring source of truth.
2. Refactor `score_parks.py` to use it.
3. Recalculate totals deterministically.
4. Add validation tests.
5. Define the review data model.
6. Design the conversational review questions.
7. Add a Worker review-submission endpoint.
8. Define Airtable and n8n mappings.
9. Rebuild the review form.
10. Implement scoring of individual family submissions.
11. Add moderation and publication states.
12. Build the Family Holiday Report data model.
13. Build the three report-card variants.
14. Test the stick-figure prompt manually.
15. Automate only after the manual process is reliable.

## Known unknowns

These must be confirmed before implementation:

- exact production n8n workflow
- exact review Airtable table and field mapping
- deployment-time replacement of the webhook placeholder
- production branch and deploy command
- image upload flow
- spam-prevention approach
- moderation workflow
- whether reviewer consent fields already exist externally
