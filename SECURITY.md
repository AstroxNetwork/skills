# Security Policy

## Reporting a vulnerability

Please report security issues privately to `cs@holycrab.ai`. Include the affected command or MCP tool, impact, reproduction steps, and a request ID when available.

Do not include a real API Key, temporary upload URL, private media, or paid proof-of-concept generation. Please do not open a public issue before we have acknowledged the report.

## Credential handling

The CLI stores its API Key in the current user's local configuration with user-only file permissions. Environment variables can override that file. The CLI and local MCP redact credentials and temporary upload URLs from their output.

Users should create a dedicated HolyCrab API Key, set a reasonable credit limit, and revoke it from the HolyCrab website if the computer or local configuration is compromised.

## Real-person verification

The user-requested authorization start result intentionally includes a temporary verification link and QR image. Treat both as private session credentials, including in Agent conversation history. They must not be posted publicly or passed to third-party QR services. The CLI generates QR codes locally with bundled Segno 1.6.6 (BSD-3-Clause); no runtime package installation is needed.

Only the person completes verification in the official browser flow. The CLI does not collect biometric data or submit verification results. Independent provider tokens and upstream account/group/asset IDs are excluded from new public tool outputs.

QR files use user-only permissions under the configured HolyCrab directory's `real-human` cache. Terminal authorization queries remove them; later tool use cleans expired entries. Cleanup is not a background service: remove an abandoned QR manually if the CLI will not run again. The link may remain in the user's conversation history even after the local file is removed. Authorization does not grant permission for billable generation.
