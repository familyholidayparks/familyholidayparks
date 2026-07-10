# Implementation Plan

## Phase 1 — Scoring foundation

- Create `scoring/schema.json`
- Create `scoring/rubric.md`
- Create `scoring/prompt.md`
- Refactor `score_parks.py` to load the shared files
- Validate category ranges
- Calculate totals in code
- Add tests for valid, invalid and incomplete outputs

## Phase 2 — Review data model

- Define the submission schema
- Define family-member objects
- Define consent and privacy fields
- Define verification metadata
- Define moderation status fields
- Define score and evidence fields
- Confirm Airtable field mapping

## Phase 3 — Secure submission pipeline

- Add a review route to the Cloudflare Worker
- Validate and normalise payloads
- Add anti-spam controls
- Keep n8n URL server-side
- Forward to a test n8n workflow
- Confirm Airtable record creation

## Phase 4 — Conversational form

- Replace direct category scoring questions
- Add stay and family composition flow
- Add natural questions mapped to all seven criteria
- Add validation
- Add consent
- Add photos and verification options
- Submit only to the Worker endpoint

## Phase 5 — Individual review scoring

- Build a review-specific scoring prompt using the same schema and rubric
- Store evidence by category
- Calculate total in code
- Add confidence and warnings
- Route low-confidence reviews to moderation

## Phase 6 — Family Holiday Reports

- Define report-card data structure
- Build photo card
- Build stick-figure card
- Build editorial card
- Test mobile layouts
- Test manual image-generation prompt
- Add privacy-safe sharing

## Approval gates

Do not proceed from one phase to the next until:

- the schema is approved
- the data model is approved
- external n8n/Airtable dependencies are confirmed
- tests pass
- diffs are reviewed
