# Deployment and Operations

## Current known state

- Site is generated with Python.
- Deployment uses GitHub and Cloudflare Pages.
- `build_all.py` is part of the build process.
- The exact production branch and deployment command must be confirmed.
- The real review n8n webhook is not present in the repository.

## Before deployment

1. Confirm the correct branch.
2. Run `git status`.
3. Review `git diff`.
4. Run the relevant build scripts.
5. Check generated output.
6. Confirm no secrets are committed.
7. Confirm no private review data is included.
8. Test the review form in a non-production environment.
9. Confirm Worker environment variables.
10. Confirm n8n receives the expected payload.
11. Confirm Airtable creates the expected record.
12. Confirm failure states show a useful message.

## Rollout recommendation

Use stages:

1. Documentation and schema
2. Local validation
3. Worker endpoint in test mode
4. n8n test workflow
5. Airtable test table
6. Internal family submission
7. Small private beta
8. Public release

## Rollback

Before public release:

- Keep the old form available as a fallback.
- Tag or commit the last stable version.
- Document how to disable the new Worker route.
- Ensure failed submissions are logged safely.
