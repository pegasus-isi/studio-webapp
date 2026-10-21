# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Report it privately through GitHub: on this repository's **Security** tab,
choose **Report a vulnerability**. That opens an advisory only the
maintainers can see, where we can discuss and fix it before anything is
public.

Helpful to include:

- what an attacker can do, and what they need first (an account on a hosted
  Studio, a local account on an Open OnDemand host, a victim who clicks a
  link, …);
- the deployment it applies to: hosted (JupyterHub/KubeSpawner), Open
  OnDemand, or local (`local-test-instance.sh`);
- steps or a request that reproduces it, against your own account or a local
  instance only;
- the commit or image tag you tested.

We aim to acknowledge a report within a few working days and to tell you
when a fix ships.

## Supported versions

Only `main` (published as `ghcr.io/pegasus-isi/studio-webapp:main`) is
supported. There are no maintained release branches; fixes land on `main`.

## Scope and security model

Studio runs as **one container per user**, and has no login of its own. It
relies on what runs in front of it:

- **Hosted (JupyterHub):** every request is checked against the hub's OAuth
  before nginx passes it on (`studio-api/routers/hub_auth.py`). All users'
  servers share one origin (`/user/<name>/`), so a page served from one
  user's server is same-origin with another's. That is a known JupyterHub
  property, fixed only by per-user subdomains, which this deployment does not
  use.
- **Open OnDemand:** a per-session token gate (`STUDIO_AUTH_TOKEN`) protects
  the front port, studio-api requires a proxy-stamped secret, and JupyterLab
  requires its own token. The Next.js upstream serves only the UI shell and
  is reachable by other local users by design (see `SPEC.md`).

Inside the container the user already has a shell, so issues that need code
execution as that same user are out of scope. Issues that let *someone else*
act as the user, read their files or credentials, or reach their container
are in scope, including through the AI chat (for example a prompt injection
that gets past the approval prompts).
