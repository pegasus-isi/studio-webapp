# Vendored: PegasusAI Bot client (JupyterLab extension)

This directory is a vendored copy of the **private** repository
[TauferLab/Src_PegasusAI_Client](https://github.com/TauferLab/Src_PegasusAI_Client)
(`@gclab/pegasus-ai-client` / py `pegasus_ai_client`), Apache-2.0.

It is vendored (not cloned at build time) because the upstream repo is private
and the Docker build has no credentials. It is a **separate** extension from
the studio's own `jupyter-pegasusai-chat` — different package and labextension
path, so both coexist.

- **Source ref:** `be7288c1ba5a7debd9937d3906f572707805fceb`
- **What's included:** only the build-relevant files (source, schema, style,
  build config, LICENSE). CI (`.github/`), figures, `ui-tests/`, and unit
  tests (`src/__tests__/`) are omitted.
- **Local modification:** `src/configPlugin.tsx` seeds the server URL + user
  from server-injected `PageConfig` (see the file header) instead of the
  upstream manual-only settings form. This is the ONLY change from upstream.

## Updating

1. Re-fetch the build-relevant files at the new ref (keep the omissions above).
2. Re-apply the `src/configPlugin.tsx` change (or diff it against the new
   upstream version).
3. Update the ref above.
4. Rebuild the image and smoke-test the cell-toolbar button.
