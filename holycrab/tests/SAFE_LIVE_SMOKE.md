# Safe production smoke test

This internal runner verifies HolyCrab CLI against the fixed production API
without creating generation tasks or uploading files.

```bash
python3 holycrab/tests/safe_live_smoke.py \
  --account-label user1 \
  --report /tmp/holycrab-safe-live-report.json
```

The API Key is requested through a hidden prompt. Do not pass it as a command
argument, commit it, or store it in CI. The runner refuses to start when
`HOLYCRAB_API_KEY` is set.

The request guard permits:

- account, task, asset, and real-human group reads;
- image, video, and audio credit-estimation endpoints;
- GitHub release checks;
- local capability, MCP initialization, upload preview, and health checks.

It rejects generation creation, real-human authorization creation, presigned
upload requests, upload registration, rename, delete, and every unrecognized
API route. The report contains route categories and counts, not account data,
task IDs, person names, private links, signed URLs, or local file paths.

Real-human authorization and real-human asset upload remain manual tests. A
real ordinary asset upload is also excluded because the CLI cannot remove that
test asset afterward.
