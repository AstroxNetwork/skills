# Security Policy

## Reporting a vulnerability

Please report security issues privately to `cs@holycrab.ai`. Include the affected command or MCP tool, impact, reproduction steps, and a request ID when available.

Do not include a real API Key, temporary upload URL, private media, or paid proof-of-concept generation. Please do not open a public issue before we have acknowledged the report.

## Credential handling

The CLI stores its API Key in the current user's local configuration with user-only file permissions. Environment variables can override that file. The CLI and local MCP redact credentials and temporary upload URLs from their output.

Users should create a dedicated HolyCrab API Key, set a reasonable credit limit, and revoke it from the HolyCrab website if the computer or local configuration is compromised.
