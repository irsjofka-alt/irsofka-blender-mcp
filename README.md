# IRSofka Blender MCP

A stdio [Model Context Protocol](https://modelcontextprotocol.io) server for Blender, written
clean-room from scratch for the Irsofka AI Workstation. Licensed **Business Source License 1.1**
for the entire work, with Change License **Apache License, Version 2.0**.

No third-party code is vendored or copied: the protocol was implemented from the receiver side
(connecting to Blender's TCP socket add-on, default `127.0.0.1:9876`).

```bash
$ python3 server.py --surface-policy  # what is registered right now, and what is pending consent
$ python3 server.py --selftest        # 16 checks, no Blender running, no network out
```

## Architecture and Safety Policies

1. **Protocol from the receiver side**: speaks JSON-over-socket with Blender's listening add-on
   on `127.0.0.1:9876` (or `IRSOFKA_BLENDER_PORT`). If Blender is not running, requests return
   structured `error_type: "BRIDGE_OFFLINE"` with connection hints, never crashing.
2. **Telemetry disabled**: `DISABLE_TELEMETRY=1` is enforced before startup.
3. **Safe mode active**: `BLENDER_MCP_SAFE_MODE=1` denies dangerous system execution calls before
   sending code to Blender.
4. **Outbound network gating**: tools reaching outside the machine (`generate_3d`, `search_assets`,
   `import_asset`) are **not registered** unless `IRSOFKA_BLENDER_ALLOW_NETWORK=1` is set.
5. **Station VRAM gate**: `vram.decide()` guards heavy operations. On unmetered systems without
   a workstation gate, it answers `UNKNOWN` and refuses rather than issuing an unmeasured permit.
   `IRSOFKA_BLENDER_GATE=off` is the explicit override producing `BYPASSED`.

## Tools

| group | tools |
|---|---|
| local scene & execution (ours) | `get_scene_info`, `execute_blender_code`, `get_object_info`, `list_scene_items`, `get_viewport_screenshot`, `export_scene` |
| network assets (ours, gated) | `generate_3d`, `search_assets`, `import_asset` (require `IRSOFKA_BLENDER_ALLOW_NETWORK=1`) |
| station layer (ours) | `irsofka_gpu_gate`, `irsofka_surface_policy` |

## License

Business Source License 1.1, `LICENSE` in this directory. Licensor **PT IRSofka Data Teknologi**,
Change Date **four years after publication**, Change License **Apache License, Version 2.0**.
See `NOTICE` for brand attribution.
