# Security policy

## Credentials

Do not commit API keys, access tokens, private keys, provider credentials, or a populated `.env` file.

Copy `.env.example` to `.env` for local development. The `.env` file and common credential file formats are excluded by `.gitignore`. Examples and tests must use clearly invalid placeholder credentials.

If a credential is committed accidentally, revoke or rotate it first, then remove it from the complete Git history. Deleting it only from the latest commit is not sufficient.

## Reporting a vulnerability

Please open a private GitHub security advisory rather than a public issue when a report contains an exploitable vulnerability or sensitive information.
