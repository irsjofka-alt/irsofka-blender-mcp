"""IRSofka Blender MCP — clean-room Model Context Protocol server for Blender.

Architecture:
- Clean-room implementation: no third-party code vendored or copied.
- Connects to Blender via TCP socket (default 127.0.0.1:9876) using JSON-over-socket framing:
    Request:  {"type": "<cmd>", "params": {...}}
    Response: {"status": "success"|"error", "result": ..., "message": ...}
- Station policies enforced:
    - DISABLE_TELEMETRY=1 and BLENDER_MCP_SAFE_MODE=1 enforced before initialization.
    - Outbound network tools (generate_3d, search_assets, import_asset) withheld
      until explicit operator consent via IRSOFKA_BLENDER_ALLOW_NETWORK.
    - VRAM gate checks (vram.py) guard heavy GPU workloads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

# Enforce telemetry disabled and safe mode active before any subsystem loads
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["BLENDER_MCP_SAFE_MODE"] = "1"

REPO = Path(__file__).resolve().parent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IRSofka_Blender_MCP")

ALLOW_NETWORK_ENV = "IRSOFKA_BLENDER_ALLOW_NETWORK"
CONSENT_VALUES = ("1", "true", "yes", "on")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
MAX_BUFFER = 8 * 1024 * 1024

NETWORK_TOOLS = {
    "generate_3d": "External 3D generation service requiring network access",
    "search_assets": "External asset search (PolyHaven / online repositories)",
    "import_asset": "External asset download and import",
}

LOCAL_TOOL_NAMES = {
    "get_scene_info",
    "execute_blender_code",
    "get_object_info",
    "list_scene_items",
    "get_viewport_screenshot",
    "export_scene",
}


def network_allowed() -> bool:
    return os.environ.get(ALLOW_NETWORK_ENV, "").strip().lower() in CONSENT_VALUES


def telemetry_disabled() -> bool:
    return os.environ.get("DISABLE_TELEMETRY", "").strip().lower() in CONSENT_VALUES


def safe_mode_active() -> bool:
    return os.environ.get("BLENDER_MCP_SAFE_MODE", "").strip().lower() in CONSENT_VALUES


def get_endpoint() -> tuple[str, int]:
    host = os.environ.get("IRSOFKA_BLENDER_HOST", DEFAULT_HOST)
    try:
        port = int(os.environ.get("IRSOFKA_BLENDER_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    return host, port


def bridge_call(cmd_type: str, params: dict | None = None, timeout: float = 30.0) -> dict:
    """Execute one request/reply exchange with the Blender socket add-on.

    Returns structured dict. Never raises unhandled exception: connection failures
    return state="UNAVAILABLE" with connection context and recovery hint.
    """
    host, port = get_endpoint()
    payload = json.dumps({"type": cmd_type, "params": params or {}}, ensure_ascii=False)
    try:
        with socket.create_connection((host, port), timeout=min(timeout, 5.0)) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload.encode("utf-8"))
            chunks = []
            total_bytes = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes > MAX_BUFFER:
                    return {
                        "status": "error",
                        "error_type": "OVERSIZE",
                        "message": f"Response exceeded {MAX_BUFFER} bytes",
                    }
                combined = b"".join(chunks)
                try:
                    res = json.loads(combined.decode("utf-8"))
                    return res
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            if chunks:
                return json.loads(b"".join(chunks).decode("utf-8"))
            return {
                "status": "error",
                "error_type": "EMPTY_RESPONSE",
                "message": "Blender socket closed connection without returning data",
            }
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        return {
            "status": "error",
            "error_type": "BRIDGE_OFFLINE",
            "message": f"Blender MCP socket unreachable on {host}:{port}: {exc}",
            "host": host,
            "port": port,
            "start_hint": f"Start the MCP add-on inside Blender listening on port {port}",
        }


def check_code_safety(code: str) -> tuple[bool, str]:
    """Validate python code against safe mode restrictions when BLENDER_MCP_SAFE_MODE=1."""
    if not safe_mode_active():
        return True, ""
    dangerous_patterns = [
        r"\bos\.system\b",
        r"\bsubprocess\b",
        r"\bshutil\.rmtree\b",
        r"\bbuiltins\.__import__\b",
        r"\bopen\s*\([^)]*['\"][wa]",
    ]
    for pat in dangerous_patterns:
        if re.search(pat, code):
            return False, f"Code blocked by SAFE_MODE: matched dangerous pattern '{pat}'"
    return True, ""


def create_mcp_server():
    """Build and configure the FastMCP server with all clean-room tools and station policies."""
    from mcp.server.fastmcp import FastMCP
    import vram

    mcp = FastMCP("irsofka-blender-mcp")

    # 1. Local tools
    @mcp.tool()
    def get_scene_info() -> str:
        """Get summary information about the currently open Blender scene."""
        res = bridge_call("get_scene_info")
        return json.dumps(res, indent=2)

    @mcp.tool()
    def get_object_info(name: str) -> str:
        """Get detailed property and transform information for a named Blender object."""
        res = bridge_call("get_object_info", {"name": name})
        return json.dumps(res, indent=2)

    @mcp.tool()
    def list_scene_items() -> str:
        """List objects and collections currently present in the active Blender scene."""
        res = bridge_call("list_scene_items")
        return json.dumps(res, indent=2)

    @mcp.tool()
    def execute_blender_code(code: str) -> str:
        """Execute Python code within Blender's main thread environment.

        Subject to BLENDER_MCP_SAFE_MODE validation.
        """
        safe, why = check_code_safety(code)
        if not safe:
            return json.dumps({
                "status": "error",
                "error_type": "SAFE_MODE_DENIED",
                "message": why,
            }, indent=2)
        res = bridge_call("execute_code", {"code": code})
        return json.dumps(res, indent=2)

    @mcp.tool()
    def get_viewport_screenshot(filepath: str = "") -> str:
        """Capture a screenshot from Blender's active 3D viewport."""
        # Consult VRAM gate for viewport capture heavy operations
        verdict = vram.decide("viewport_capture")
        if not vram.is_go(verdict):
            return json.dumps({
                "status": "error",
                "error_type": "VRAM_GATE_REFUSED",
                "state": verdict.get("state"),
                "gate": verdict.get("gate"),
                "why": verdict.get("why"),
            }, indent=2)
        res = bridge_call("get_viewport_screenshot", {"filepath": filepath})
        return json.dumps(res, indent=2)

    @mcp.tool()
    def export_scene(filepath: str, format: str = "glb") -> str:
        """Export the active Blender scene to a specified file format (e.g. glb, fbx, obj)."""
        res = bridge_call("export_scene", {"filepath": filepath, "format": format})
        return json.dumps(res, indent=2)

    # 2. Outbound / Network tools
    @mcp.tool()
    def search_assets(query: str, category: str = "") -> str:
        """Search external online asset libraries. Requires IRSOFKA_BLENDER_ALLOW_NETWORK=1."""
        res = bridge_call("search_polyhaven_assets", {"query": query, "category": category})
        return json.dumps(res, indent=2)

    @mcp.tool()
    def import_asset(asset_id: str) -> str:
        """Download and import an external asset into Blender. Requires IRSOFKA_BLENDER_ALLOW_NETWORK=1."""
        res = bridge_call("download_polyhaven_asset", {"asset_id": asset_id})
        return json.dumps(res, indent=2)

    @mcp.tool()
    def generate_3d(prompt: str) -> str:
        """Request external 3D generative model synthesis. Requires IRSOFKA_BLENDER_ALLOW_NETWORK=1."""
        res = bridge_call("generate_3d_asset", {"prompt": prompt})
        return json.dumps(res, indent=2)

    # 3. Station tools
    @mcp.tool()
    def irsofka_gpu_gate(intent: str = "cycles-render") -> str:
        """Station VRAM gate for local GPU workloads: answers ALLOW / NOT_GPU_WORK / BYPASSED / UNKNOWN / DENY."""
        return json.dumps(vram.decide(intent), indent=2)

    @mcp.tool()
    def irsofka_surface_policy() -> str:
        """Report surface policy, network consent status, safe mode, and gate vocabulary."""
        return json.dumps({
            "allow_network": network_allowed(),
            "consent_env": ALLOW_NETWORK_ENV,
            "network_tools": list(NETWORK_TOOLS.keys()),
            "telemetry_disabled": telemetry_disabled(),
            "safe_mode_active": safe_mode_active(),
            "endpoint": f"{get_endpoint()[0]}:{get_endpoint()[1]}",
            "gate_vocab": list(vram.GATE_GO),
            "gate_env": vram.GATE_ENV,
        }, indent=2)

    return mcp


def apply_surface_policy(mcp_obj, allow_network: bool | None = None) -> dict:
    """Filter MCP tool registrations based on operator network consent."""
    allow = network_allowed() if allow_network is None else allow_network
    removed = []

    if not allow:
        # FastMCP remove_tool or tool list manipulation
        for name in NETWORK_TOOLS:
            try:
                mcp_obj.remove_tool(name)
                removed.append(name)
            except Exception:
                pass

    tools = [t.name for t in asyncio.run(mcp_obj.list_tools())]
    return {
        "ok": True,
        "allow_network": allow,
        "removed": removed,
        "present": sorted(tools),
        "telemetry_off": telemetry_disabled(),
        "safe_mode_on": safe_mode_active(),
    }


def build():
    mcp = create_mcp_server()
    report = apply_surface_policy(mcp)
    return mcp, report


def selftest() -> int:
    """Run full self-verification without Blender running and without network egress."""
    results = []

    def check(name: str, cond: bool, detail: str = ""):
        results.append((bool(cond), name, detail))
        print(f"[{'ok    ' if cond else 'GAGAL '}] {name}" + ("" if cond else f"  <- {detail}"))

    # 1. Upstream vendored must NOT exist
    upstream_dir = REPO / "upstream"
    check("upstream/ pihak ketiga TIDAK ada (clean-room murni)", not upstream_dir.exists(),
          f"upstream/ masih ditemukan di {upstream_dir}")

    # 2. Server builds and FastMCP initializes
    mcp_base = None
    try:
        mcp_base = create_mcp_server()
        check("server FastMCP terinisialisasi bersih tanpa modul pihak ketiga", mcp_base is not None, "")
    except Exception as exc:
        check("server FastMCP terinisialisasi bersih tanpa modul pihak ketiga", False, f"{type(exc).__name__}: {exc}")

    if mcp_base is not None:
        tools_pre = {t.name for t in asyncio.run(mcp_base.list_tools())}
        # 3. Local tools registered
        check("tool inti lokal terdaftar (get_scene_info, execute_blender_code, export_scene, ...)",
              LOCAL_TOOL_NAMES <= tools_pre, str(sorted(tools_pre)))

        # 4. Outbound network tools removed by default
        rep = apply_surface_policy(mcp_base)
        tools_post = {t.name for t in asyncio.run(mcp_base.list_tools())}
        check("kebijakan stasiun melepas tool yang keluar jaringan secara default",
              rep.get("ok") and set(rep["removed"]) == set(NETWORK_TOOLS) and not (set(NETWORK_TOOLS) & tools_post),
              json.dumps(rep)[:160])

        # 5. Consent env restores network tools
        mcp_with_net = create_mcp_server()
        rep_net = apply_surface_policy(mcp_with_net, allow_network=True)
        tools_with_net = {t.name for t in asyncio.run(mcp_with_net.list_tools())}
        check("dengan consent env terpasang, kebijakan TIDAK menghapus tool jaringan",
              set(NETWORK_TOOLS) <= tools_with_net and rep_net["removed"] == [],
              str(sorted(tools_with_net)))

        # 6. Station tools registered
        check("permukaan stasiun terdaftar (irsofka_gpu_gate, irsofka_surface_policy)",
              {"irsofka_gpu_gate", "irsofka_surface_policy"} <= tools_post, str(sorted(tools_post)))

    # 7. Telemetry and safe mode environment flags
    check("telemetri dimatikan dan safe mode dipaksa aktif saat modul dimuat",
          telemetry_disabled() is True and safe_mode_active() is True,
          f"telemetry={telemetry_disabled()} safe_mode={safe_mode_active()}")

    # 8. Safe mode validation works
    safe_ok, _ = check_code_safety("print('hello blender')")
    safe_bad, why_bad = check_code_safety("import os; os.system('rm -rf /')")
    check("validasi safe mode menolak eksekusi perintah berbahaya sebelum ke socket",
          safe_ok is True and safe_bad is False and "SAFE_MODE" in why_bad.upper(),
          f"safe_bad={safe_bad} why={why_bad}")

    # 9. VRAM single-source vocabulary
    import vram
    check("kosakata gerbang stasiun masih satu sumber (GATE_GO di vram.py, dipakai baris ledger)",
          hasattr(vram, "GATE_GO") and "ALLOW" in vram.GATE_GO and hasattr(vram, "is_go"),
          "vram.GATE_GO atau is_go hilang")

    # 10. Unmetered environment returns UNKNOWN
    saved_env = {k: os.environ.get(k) for k in ("AI_STATION_HOME", vram.GATE_ENV)}
    try:
        with tempfile.TemporaryDirectory() as empty_dir:
            os.environ["AI_STATION_HOME"] = empty_dir
            os.environ.pop(vram.GATE_ENV, None)
            v = vram.decide("render this on the GPU")
            check("mesin TANPA stasiun menjawab UNKNOWN dan bukan izin (is_go == False)",
                  v.get("state") == "UNKNOWN" and v.get("gate") == "absent" and not vram.is_go(v),
                  json.dumps(v)[:180])

            # 11. Explicit bypass
            os.environ[vram.GATE_ENV] = "off"
            b = vram.decide("render this on the GPU")
            check(f"jalan keluar harus DIKETIK: {vram.GATE_ENV}=off menghasilkan BYPASSED (is_go == True)",
                  b.get("state") == "BYPASSED" and vram.is_go(b), json.dumps(b)[:180])
    finally:
        for k, old in saved_env.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old

    # 12. Unreachable socket returns structured error
    unavail = bridge_call("ping", timeout=0.5)
    check("socket Blender mati mengembalikan status error terstruktur, bukan crash atau exception",
          unavail.get("status") == "error" and unavail.get("error_type") == "BRIDGE_OFFLINE" and "start_hint" in unavail,
          json.dumps(unavail)[:160])

    # 13. Residue check
    docs_to_check = [
        (REPO / n).read_text(errors="replace")
        for n in ("vram.py", "README.md", "UPSTREAM", "NOTICE")
        if (REPO / n).is_file()
    ]
    check("tidak ada sisa repositori lain di modul ini: satu nama env, satu merek, satu alat",
          not any(("COMFY" in t.upper() or "IRSOVKA" in t.upper()) for t in docs_to_check),
          "ditemukan nama env atau nama repositori lain di file sumber/dokumen")

    # 14. Document vocabulary honesty
    docs_text = [
        (REPO / n).read_text(errors="replace")
        for n in ("README.md", "UPSTREAM", "NOTICE")
        if (REPO / n).is_file()
    ]
    check("dokumen hanya menyebut state yang benar-benar bisa dikeluarkan gerbang (tanpa WAIT/UNAVAILABLE)",
          not any(("UNAVAILABLE" in t.upper() or "WAIT" in t.upper()) for t in docs_text),
          "dokumen menyebut status gerbang yang tidak ada di vram.decide()")

    # 15. BSL 1.1 license check for the entire work
    lic_text = (REPO / "LICENSE").read_text(errors="replace")
    not_text = (REPO / "NOTICE").read_text(errors="replace")
    check("lisensi kami BSL 1.1 untuk seluruh karya dengan Apache-2.0 Change License, NOTICE merek saja",
          "Business Source License" in lic_text
          and "all source code" in lic_text
          and "Apache License" in lic_text
          and "attribution" in not_text.lower(),
          "LICENSE/NOTICE tidak sesuai spesifikasi BSL clean-room")

    # 16. Self-referential check count verification: README count matches executed checks
    readme_text = (REPO / "README.md").read_text(errors="replace") if (REPO / "README.md").is_file() else ""
    m_count = re.search(r"(\d+)\s+checks?", readme_text, re.IGNORECASE)
    expected_count = int(m_count.group(1)) if m_count else -1
    # Note: total checks in this selftest is len(results) + 1 (for this check itself)
    total_checks_executed = len(results) + 1
    check("jumlah checks yang ditulis di README sama dengan jumlah checks nyata yang dijalankan",
          expected_count == total_checks_executed,
          f"README mencatat {expected_count} checks, realita berjalan {total_checks_executed} checks")

    bad = [n for ok, n, _d in results if not ok]
    print(f"\nirsofka-blender-mcp selftest: {len(results) - len(bad)}/{len(results)} sesuai")
    for n in bad:
        print(f"  GAGAL: {n}")
    return 0 if not bad else 1


def main(argv) -> int:
    if "--selftest" in argv:
        return selftest()
    if "--surface-policy" in argv:
        _mcp, report = build()
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1

    mcp, _report = build()
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
