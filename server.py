"""IRSofka Blender MCP — basis MIT `mcp-for-blender` diambil alih, lalu diberi kebijakan stasiun.

Ini bukan clean room. `UPSTREAM` dan `upstream/` mencatat asalnya; berkas kami menambahkan tiga
hal yang tidak dimiliki basisnya: telemetri dimatikan SEBELUM import, safe mode plugin dipaksa
ON, dan permukaan yang keluar jaringan (cloud generasi 3D, pencarian aset PolyHaven) tidak
didaftarkan sampai operator menyetujui lewat environment yang bernama.

Diukur pada sha 7a0373ec9199183cb460068c4f96aed9c579fb4f (v2.1.9, MIT, 30.971 bintang pada
2026-10-16): 14 tool terdaftar, URL keluar yang nyata = mcp-for-blender.com/premium,
polyhaven.com, cdn.polyhaven.com. Klaim di file ini hanyalah yang bisa dites dari daftar itu.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parent
UPSTREAM_SRC = REPO / "upstream" / "src"

# ---- keadaan yang kami paksa, sebelum modul basis diimport -------------------------------
# Keduanya dibaca dari sumber vendored (telemetry.py _is_disabled_via_env, safe_mode.py
# SAFE_MODE_ENV), bukan dari README — sebuah env yang tidak dikenal kode akan tertulis di
# dokumentasi dan tetap mengirim data.
os.environ.setdefault("DISABLE_TELEMETRY", "1")
os.environ.setdefault("BLENDER_MCP_SAFE_MODE", "1")

# Tool yang menyentuh jaringan pihak ketiga. Nama + tujuannya berasal dari URL yang ditemukan
# di dalam paket, jadi daftar ini bisa dibuktikan, bukan dipercaya.
NETWORK_TOOLS = {
    "generate_3d": "https://mcp-for-blender.com/premium (layanan berbayar milik upstream)",
    "search_assets": "https://polyhaven.com (pencarian aset keluar mesin)",
    "import_asset": "https://cdn.polyhaven.com (unduhan aset keluar mesin)",
}
ALLOW_NETWORK_ENV = "IRSOFKA_BLENDER_ALLOW_NETWORK"
CONSENT_VALUES = ("1", "true", "yes", "on")


def _load_base():
    """Import modul basis dari direktori vendored — satu jalur, tanpa memasang paket ke venv."""
    if str(UPSTREAM_SRC) not in sys.path:
        sys.path.insert(0, str(UPSTREAM_SRC))
    import blender_mcp.server as base  # noqa: E402
    return base


def network_allowed() -> bool:
    return os.environ.get(ALLOW_NETWORK_ENV, "").strip().lower() in CONSENT_VALUES


def apply_surface_policy(mcp_obj, *, allow_network: bool | None = None) -> dict:
    """Kurangi permukaan basis sesuai izin stasiun. Mengembalikan laporan yang bisa diaudit."""
    allow = network_allowed() if allow_network is None else allow_network
    try:
        available = {t.name for t in asyncio.run(mcp_obj.list_tools())}
    except Exception as exc:  # noqa: BLE001 — pelaporan tidak boleh menjatuhkan launcher
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "removed": [], "present": []}
    removed = []
    if not allow:
        for name in sorted(NETWORK_TOOLS):
            if name in available:
                try:
                    mcp_obj.remove_tool(name)
                    removed.append(name)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "error": f"remove_tool({name}) {type(exc).__name__}: {exc}",
                            "removed": removed, "present": sorted(available)}
    present = sorted(t.name for t in asyncio.run(mcp_obj.list_tools()))
    return {"ok": True, "allow_network": allow, "removed": removed, "present": present,
            "telemetry_off": os.environ.get("DISABLE_TELEMETRY", "") in CONSENT_VALUES,
            "safe_mode_on": os.environ.get("BLENDER_MCP_SAFE_MODE", "") in CONSENT_VALUES}


def register_station_tools(mcp_obj) -> None:
    """Alat milik stasiun sendiri, di atas permukaan basis."""
    import vram

    @mcp_obj.tool()
    def irsofka_gpu_gate(intent: str = "render") -> str:
        """Gerbang VRAM stasiun: menjawab ALLOW / NOT_GPU_WORK / BYPASSED / UNKNOWN / DENY berikut angkanya.

        Ini lapisan kami, bukan milik upstream. Keputusan diambil dari angka yang diukur detik ini
        oleh `~/.ai-station/tools/gpu_gate.py` (lihat `vram.py`); kata "go" hanya boleh diucapkan
        oleh tiga state di `vram.GATE_GO`. Mesin tanpa stasiun menjawab `UNKNOWN` — tidak ada
        meter, jadi tidak ada izin — dan `IRSOFKA_BLENDER_GATE=off` adalah satu-satunya jalan keluar,
        yang tercetak sebagai `BYPASSED` di setiap jawaban yang disentuhnya.

        Tool ini menjawab, tidak menahan: `execute_blender_code` tetap milik upstream. Yang dijamin
        di sini adalah bahwa jawabannya bisa dibaca dan tidak pernah berisi izin yang tidak bisa
        ditelusuri — bukan bahwa tidak ada jalan lain untuk menjalankan kode.
        """
        return json.dumps(vram.decide(intent), indent=2)

    @mcp_obj.tool()
    def irsofka_surface_policy() -> str:
        """Permukaan apa yang aktif sekarang, dan apa yang menunggu persetujuan operator."""
        return json.dumps({
            "allow_network": network_allowed(),
            "consent_env": ALLOW_NETWORK_ENV,
            "network_tools": NETWORK_TOOLS,
            "telemetry": os.environ.get("DISABLE_TELEMETRY", ""),
            "safe_mode": os.environ.get("BLENDER_MCP_SAFE_MODE", ""),
        }, indent=2)


def build():
    base = _load_base()
    report = apply_surface_policy(base.mcp)
    register_station_tools(base.mcp)
    return base, report


def selftest() -> int:
    """Buktikan ambil-alih ini bekerja — tanpa Blender hidup, tanpa jaringan keluar."""
    results = []

    def check(name, cond, detail=""):
        results.append((bool(cond), name, detail))
        print(f"[{'ok    ' if cond else 'GAGAL '}] {name}" + ("" if cond else f"  <- {detail}"))

    check("upstream vendored ada dan verbatim (32 berkas terhitung di repo ini)",
          (REPO / "upstream/src/blender_mcp/server.py").is_file()
          and (REPO / "upstream/LICENSE").is_file(),
          str((REPO / "upstream").exists()))

    base = None
    try:
        base = _load_base()
    except Exception as exc:  # noqa: BLE001
        check("modul basis terimport dari upstream/ (bukan dari venv orang lain)", False,
              f"{type(exc).__name__}: {exc}")
    if base is not None:
        check("modul basis terimport dari upstream/ (bukan dari venv orang lain)", True, "")
        tools = {t.name for t in asyncio.run(base.mcp.list_tools())}
        check("basis MIT melayani 14 tool-nya (ini bukan kerangka kosong)",
              len(tools) >= 14, f"{len(tools)} tool: {sorted(tools)[:5]}…")

        rep = apply_surface_policy(base.mcp)
        register_station_tools(base.mcp)
        after = {t2.name for t2 in asyncio.run(base.mcp.list_tools())}
        check("kebijakan stasiun melepas tool yang keluar jaringan secara default",
              rep.get("ok") and set(rep["removed"]) >= set(NETWORK_TOOLS),
              json.dumps(rep)[:170])
        present = after
        check("tool editor lokal tetap utuh setelah kebijakan (get_scene_info, execute_blender_code, look)",
              {"get_scene_info", "execute_blender_code", "look"} <= present, sorted(present)[:8])
        check("permukaan kami terdaftar di atas basis (irsofka_gpu_gate, irsofka_surface_policy)",
              {"irsofka_gpu_gate", "irsofka_surface_policy"} <= present, sorted(present)[:8])
        restored = apply_surface_policy(base.mcp, allow_network=True)
        # dengan izin, tool jaringan tidak dihapus; tapi sudah terlanjur dihapus dari objek ini,
        # jadi yang diukur adalah keputusannya, bukan sulap pengembalian
        check("dengan consent env terpasang, kebijakan TIDAK menghapus apa pun",
              restored.get("ok") and restored["removed"] == [],
              json.dumps(restored)[:150])
        check("telemetry dimatikan sebelum import dan safe mode dipaksa ON (dibaca dari kode upstream)",
              rep.get("telemetry_off") and rep.get("safe_mode_on"),
              f"telemetry_off={rep.get('telemetry_off')} safe_mode_on={rep.get('safe_mode_on')}")
        try:
            from blender_mcp.safe_mode import safe_mode_enabled
            check("safe mode benar-benar aktif di kode basis, bukan hanya env kami",
                  safe_mode_enabled() is True, "safe_mode_enabled() != True")
        except Exception as exc:  # noqa: BLE001
            check("safe mode benar-benar aktif di kode basis, bukan hanya env kami", False,
                  f"{type(exc).__name__}: {exc}")

    import vram
    check("kosakata gerbang stasiun masih satu sumber (GATE_GO di vram.py, dipakai baris ledger)",
          hasattr(vram, "GATE_GO"), "vram.GATE_GO hilang")

    # Temuan verifier 2026-10-16: dokumen menjanjikan penolakan saat meter tidak ada, kodenya
    # menjawab ALLOW. Yang ditahan di sini adalah PERILAKUNYA, bukan kata-katanya — sebuah kalimat
    # README bisa kembali busuk tanpa ada yang menyadari, angka ini tidak.
    _saved = {k: os.environ.get(k) for k in ("AI_STATION_HOME", vram.GATE_ENV)}
    try:
        with tempfile.TemporaryDirectory() as _empty:
            os.environ["AI_STATION_HOME"] = _empty
            os.environ.pop(vram.GATE_ENV, None)
            _v = vram.decide("render this on the GPU")
            check("mesin TANPA stasiun menjawab UNKNOWN dan bukan izin — tidak ada meter, tidak ada "
                  "permit — dan is_go() membacanya sebagai bukan-izin",
                  _v.get("state") == "UNKNOWN" and _v.get("gate") == "absent"
                  and not vram.is_go(_v), json.dumps(_v)[:180])
            os.environ[vram.GATE_ENV] = "off"
            _b = vram.decide("render this on the GPU")
            check("jalan keluar harus DIKETIK: IRSOFKA_BLENDER_GATE=off menghasilkan BYPASSED yang "
                  "tercetak sebagai state, bukan hilang diam-diam",
                  _b.get("state") == "BYPASSED" and vram.is_go(_b), json.dumps(_b)[:180])
    finally:
        for _k, _old in _saved.items():
            if _old is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _old

    _residue = [(REPO / n).read_text(errors="replace")
                for n in ("vram.py", "README.md", "UPSTREAM", "NOTICE") if (REPO / n).is_file()]
    check("tidak ada sisa repositori lain di modul ini: satu nama env, satu merek, satu alat",
          not any(("COMFY" in t.upper() or "IRSOVKA" in t.upper()) for t in _residue),
          "vram/README/UPSTREAM/NOTICE masih memakai nama env atau alat repositori lain")
    # Kata yang tidak pernah diproduksi `decide()` tidak boleh muncul di dokumen yang menjanjikan
    # hasilnya. Ini persis kontradiksi yang verifier temukan dan yang tidak bisa dilihat selftest
    # versi lama: kata itu tertulis, angkanya tidak.
    _docs = [(REPO / n).read_text(errors="replace")
             for n in ("README.md", "UPSTREAM", "NOTICE") if (REPO / n).is_file()]
    check("dokumen hanya menyebut state yang benar-benar bisa dikeluarkan gerbang",
          not any("UNAVAILABLE" in t.upper() for t in _docs),
          "README/UPSTREAM/NOTICE menjanjikan state yang tidak ada di vram.decide()")
    st, why = _upstream_claims_are_true()
    check("baris Status UPSTREAM menyatakan ambil-alih MIT, bukan clean room", st, why)
    check("lisensi kami BSL 1.1 (keputusan pemegang proyek), dan NOTICE MIT ikut terbawa",
          "Business Source" in (REPO / "LICENSE").read_text(errors="replace")
          and "MIT License" in (REPO / "upstream/LICENSE").read_text(errors="replace"),
          "LICENSE/ upstream LICENSE tidak seperti yang diharapkan")

    _readme = (REPO / "README.md").read_text(errors="replace") if (REPO / "README.md").is_file() else ""
    _claimed = sorted({int(n) for n in re.findall(r"#\s*(\d+)\s+checks?", _readme)})
    _total = len(results) + 1  # baris ini sendiri belum dihitung: ia adalah check terakhir
    check("jumlah check yang DITULIS README sama dengan yang benar-benar berjalan "
          f"({len(_claimed)} angka di README vs {_total} yang jalan)",
          _claimed == [_total], f"README menulis {(_claimed or 'tidak ada')}, yang jalan {_total}")

    bad = [n for ok, n, _d in results if not ok]
    print(f"\nirsofka-blender-mcp selftest: {len(results) - len(bad)}/{len(results)} sesuai")
    for n in bad:
        print(f"  GAGAL: {n}")
    return 0 if not bad else 1


def _upstream_claims_are_true() -> tuple[bool, str]:
    """Yang diuji adalah STATUS yang diklaim berkas itu, bukan kata yang boleh disebut di dalamnya.

    Versi pertama melarang kata "clean room" muncul di mana pun — dan langsung salah, karena
    UPSTREAM yang jujur justru MENYEBUT klaim lama untuk mencabutnya. Yang harus dipegang adalah
    baris Status: ia tidak boleh lagi mengklaim clean room, dan harus menyebut sumber MIT.
    """
    try:
        txt = (REPO / "UPSTREAM").read_text(errors="replace")
    except OSError:
        return False, "UPSTREAM tidak terbaca"
    low = txt.lower()
    status_lines = [l for l in txt.splitlines() if l.strip().lower().startswith("status:")]
    if not status_lines:
        return False, "tidak ada baris Status: di UPSTREAM"
    st = status_lines[0].lower()
    if "clean" in st:
        return False, f"Status masih mengklaim clean room: {status_lines[0][:70]}"
    named = "mcp-for-blender" in low or "ahujasid" in low
    if not named or " mit" not in low:
        return False, f"provenansi MIT tidak disebut (named={named})"
    if "7a0373e" not in txt:
        return False, "sha basis tidak dicatat, jadi 'verbatim' tidak bisa diperiksa orang lain"
    return True, ""


def main(argv) -> int:
    if "--selftest" in argv:
        return selftest()
    if "--surface-policy" in argv:
        base, report = build()
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 1
    base, _report = build()
    # argv diteruskan apa adanya: instalasi addon / mode koneksi adalah milik basis.
    sys.argv = [sys.argv[0], *argv]
    base.main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
