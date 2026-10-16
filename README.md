# IRSofka Blender MCP

A [Model Context Protocol](https://modelcontextprotocol.io) server for a Blender you run yourself,
built for the IRSofka AI Workstation. It is **a takeover, not a rewrite**: the base is
[BlenderMCP](https://github.com/ahujasid/mcp-for-blender) by Siddharth Ahuja (MIT), vendored verbatim,
and this repository adds the parts a workstation needs — a licence that says what it covers, an
outbound surface that waits for consent, telemetry off before anything imports, and one gate
vocabulary shared with the rest of the station.

```
engine (MCP client) ── stdio JSON-RPC ──> server.py ── HTTP/newline JSON ──> BlenderMCP add-on inside Blender
                                            │                                        127.0.0.1:9876
                                            └── irsofka_gpu_gate / irsofka_surface_policy (ours)
```

- **License**: Business Source License 1.1 for the **station layer** (this repo's own files), Licensor
  PT IRSofka Data Teknologi, Change License the Apache License, Version 2.0 after four years per version. `upstream/` is **not**
  covered by that claim: it stays MIT and its notices are retained — see `NOTICE` and `LICENSE`'s
  `Licensed Work` parameter.
- **Provenance**: `UPSTREAM` records the exact base (`7a0373ec9199183cb460068c4f96aed9c579fb4f`,
  version 2.1.9), how the copy was verified (`diff -rq` = 0 differences), and what we changed.
  It used to claim a reference-only clean room. That claim was withdrawn on 2026-10-16 when the base
  was actually taken over. The withdrawal is stated in the file that now carries the correct claim,
  and the working history that produced both stays on the machine that built them — this repository
  does not cite a commit it did not publish.
- **Requires**: Python ≥ 3.10, `mcp>=1.26,<2`, `httpx>=0.27`. Blender 4.x/5.x; developed against
  Blender 5.2.2 LTS.

## Install

Run it from a checkout with `mcp>=1.26,<2` and `httpx` available. `pip install .` is **not** the
supported path and the metadata says why: our layer is two root modules, and installing files named
`server` and `vram` into site-packages claims two of the most collision-prone names in Python for a
server that is meant to be run where it was cloned. The vendored base is packaged as `blender_mcp`
with a namespaced path, and `pyproject.toml` exists to declare dependencies and that package, not to
promise an installed command.

```bash
python3 server.py --selftest        # 17 checks, no Blender running, no network out
python3 server.py --surface-policy  # what is registered right now, and what is waiting for consent
```

Register it with an engine that reads `mcpServers`, pointing at `server.py` in wherever
you placed the checkout (`<checkout>` below is a placeholder, not a path on anybody's machine):

```json
{
  "mcpServers": {
    "irsofka-blender-mcp": {
      "command": "python3",
      "args": ["<checkout>/irsofka-blender-mcp/server.py"]
    }
  }
}
```

Inside Blender, install the **upstream add-on** (the one we vendor, not a copy of an old
clean-room script): `Edit ▸ Preferences ▸ Add-ons ▸ Install…` →
`upstream/addon.py`, then start the server it talks to from Blender's side. `blender --background
--python …` is not how this base works: the add-on expects the UI process.

## Configuration — the four switches that matter

| variable | default here | what it does | why it is not optional |
|---|---|---|---|
| `DISABLE_TELEMETRY` | `1`, set by `server.py` **before** import | suppresses the base's telemetry collector | the base reads it from the environment; setting it after import is setting it too late, which is why the launcher owns it |
| `BLENDER_MCP_SAFE_MODE` | `1`, same | deny-by-default validation of `execute_blender_code` | an MCP tool that runs arbitrary Python inside your editor is the exact shape of accident you cannot undo; the base's safe mode is a real allow/deny table, so we turn it on rather than inventing a second one |
| `IRSOFKA_BLENDER_ALLOW_NETWORK` | unset (refuse) | registers the tools that reach outside the machine | `generate_3d` (a paid third-party service), `search_assets` and `import_asset` (PolyHaven) are **not registered** until this is set to `1`/`true`/`yes`/`on`. Consent is per run, not compiled in |
| `IRSOFKA_BLENDER_GATE` | unset (gate in charge) | `off` is the only value this reads: it lets a machine with no workstation submit without a VRAM measurement | the refusal it replaces is `UNKNOWN`, which is what "no meter here" actually means. `off` is a decision, so it has to be typed, and it is written into every verdict it produces as `BYPASSED` — a submission made under it can never be mistaken later for one a gate cleared |

The first two names were read from the vendored source (`telemetry.py`, `safe_mode.py`), not from
its README, because a variable the code ignores is worse than no variable. The last two are ours.

## Surface

14 tools inherited from the base — `get_scene_info`, `execute_blender_code`, `get_addon_status`,
`look`, `open_viewport`, `viewport_latest`, `viewport_capture`, `viewport_pick`, `search_mentions`,
`record_trajectory_feedback`, `disable_telemetry`, and the three network tools gated above — plus two
of ours:

| tool | what it answers |
|---|---|
| `irsofka_gpu_gate(intent)` | the station's VRAM verdict via `vram.decide()`: `ALLOW` / `NOT_GPU_WORK` / `BYPASSED` / `UNKNOWN` / `DENY`, with the numbers. A machine with no station has no meter, so it answers `UNKNOWN` — refusing is the honest answer when nothing was measured. `IRSOFKA_BLENDER_GATE=off` is the opt-in that turns that into `BYPASSED`, stamped into every answer it touches |
| `irsofka_surface_policy()` | which tools are live, which are waiting for `IRSOFKA_BLENDER_ALLOW_NETWORK`, and what `DISABLE_TELEMETRY` / `BLENDER_MCP_SAFE_MODE` are set to |

## Testing

```bash
python3 server.py --selftest    # 17 checks: vendor present, base imports, 14 tools visible,
                                 # network tools removed by default, station tools added,
                                 # consent path, safe mode really enabled in the base,
                                 # the gate answers UNKNOWN with no meter present, the opt-in
                                 # path is stamped BYPASSED, no foreign repository's names in
                                 # our files, UPSTREAM status says takeover with a sha, licences
                                 # in place — and the count written here equals the count run
```

What the selftest does **not** claim: it never talks to a running Blender and never calls a
third-party service, so it is safe to run at any hour. A live round trip (spawn, move, read back,
delete) is a separate piece of evidence and is reported as evidence when it happens — not assumed
because the import worked.

## What is still open

- Installing the add-on and a live round trip inside Blender on this workstation has not been
  re-run since the takeover; the numbers above are from the code and the suite, not from a session.
- The base ships `upstream/TERMS_AND_CONDITIONS.md` and mentions its author's paid service. That is
  their document and their business; it travels with the verbatim copy and is not an IRSofka term.
- No `origin` is configured for this repository. Publication is a deliberate step, not a default.
