# Qazy Examples

These example projects are small, self-contained browser apps that Qazy can run against.

Each example includes:

- a lightweight static app under `app/`
- a `qazy.config.json`
- one or more scenarios under `user-scenarios/`

## Student Portal

```bash
cd examples/student-portal
qazy user-scenarios/login
qazy -p "log in as the student and open the profile menu" --start-page /index.html --no-use-cookie
```

## Task Board

```bash
cd examples/task-board
qazy user-scenarios/task-flow
qazy -p "add a task, complete it, and verify the completed filter" --start-page /index.html --no-use-cookie
```

## Better Auth Portal

```bash
cd examples/better-auth-portal
qazy user-scenarios/login
```

The student portal and task board use the shared static server at `examples/_shared/serve_static.py`. Better Auth portal runs its own minimal Python server that implements `POST /api/auth/sign-in/email` and sets a `better-auth.session_token` cookie, so Qazy's built-in Better Auth flow actually signs in before the runtime takes over. None of these examples require Node or another frontend toolchain.
