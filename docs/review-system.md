# Review System

## Purpose

The review system should collect enough information to produce:

1. A useful public review
2. A reliable seven-category score
3. Structured comparison data
4. A personalised Family Holiday Report
5. Verification and moderation metadata

## Design principle

Do not ask families to behave like auditors.

Ask natural questions about their stay, then map those answers to the scoring model.

## Suggested form flow

### 1. Park and stay

- Which park did you stay at?
- When did you stay?
- How many nights?
- What did you stay in?
- What did you roughly pay?

### 2. Who travelled?

- Family name
- Number of travellers
- Repeatable traveller fields:
  - first name optional
  - description/relationship
  - age
  - gender

### 3. What did the kids do?

Potential prompts:

- What did your kids spend the most time doing?
- Which facilities or activities did they actually use?
- Was there enough for their ages?
- What was missing?

Primary category: Entertainment

### 4. What was the setting like?

Potential prompts:

- Did it feel natural, green, coastal, riverside or mostly built-up?
- Was there shade, wildlife, water or bushland nearby?
- Did the setting add to the holiday?

Primary category: Nature

### 5. How clean and well maintained was it?

Potential prompts:

- How clean were the amenities, pool, grounds and accommodation?
- Did anything feel neglected?
- Were bins, bathrooms and shared spaces maintained?

Primary category: Cleanliness

### 6. How was your site or accommodation?

Potential prompts:

- Was there enough room for your setup?
- Was access easy?
- Was it level and private enough?
- How close were neighbours?

Primary category: Site Size

### 7. Was it worth the money?

Potential prompts:

- What did you pay?
- Did the facilities and experience justify the price?
- Were there extra charges?
- How did it compare with similar parks?

Primary category: Value

### 8. How was the location?

Potential prompts:

- Was it convenient for beaches, attractions, shops or town?
- Was the park itself somewhere you wanted to spend time?
- Was road noise, distance or access an issue?

Primary category: Location

### 9. Overall feeling

Potential prompts:

- What was the best part of the stay?
- What was the biggest disappointment?
- Would you stay again?
- Would you recommend it to a family like yours?
- Write the review you wish you had read before booking.

Primary category: Sentiment

### 10. Photos, verification and consent

- Optional photos
- Optional booking evidence
- Public display name
- Publication consent
- Photo consent
- Privacy acknowledgement
- Email for private contact only

## Scoring flow

1. Store the raw answers.
2. Validate required structured fields.
3. Pass approved review content to the scoring engine.
4. Return seven category scores.
5. Validate ranges.
6. Calculate total in code.
7. Save both category scores and total.
8. Flag uncertain or incomplete scoring for moderation.
9. Never publish automatically without the chosen moderation rules.

## Recommended statuses

- Draft
- Submitted
- Needs verification
- Verified
- Needs moderation
- Approved
- Rejected
- Published

## Data retention

Raw verification evidence should be stored separately from public review content.

The public record should include only approved fields.
