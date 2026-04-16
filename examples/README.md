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

Both examples use the shared static server at `examples/_shared/serve_static.py`, so they do not require Node or another frontend toolchain.
