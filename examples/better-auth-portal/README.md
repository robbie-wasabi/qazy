# Better Auth Portal Example

A minimal server-backed example that exercises Qazy's built-in Better Auth
credentials-cookie login end to end.

The app (`app/server.py`) is a single-file Python HTTP server that:

- serves a login page at `/` and a protected dashboard at `/dashboard`
- implements `POST /api/auth/sign-in/email` with the JSON request and
  `Set-Cookie: better-auth.session_token=…` response shape that Qazy expects
- gates the dashboard behind that cookie

`qazy.config.json` sets `authProvider: better-auth` and `useCookie: true` in
`scenarioDefaults`, so Qazy signs in via HTTP before handing the browser to
the runtime.

## Run

```bash
cd examples/better-auth-portal
qazy user-scenarios/login
```

Credentials (baked into the example): `student@example.com` / `tester123`.
