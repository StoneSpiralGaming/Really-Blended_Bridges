#!/usr/bin/env python3
"""Build a Vortex-ready archive of landscape-matched SMIM bridge textures."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
import tkinter as tk
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import BOTH, END, LEFT, X, BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

try:
    from PIL import Image, ImageChops, ImageDraw, ImageStat, ImageTk
    from esplib import Plugin
except ImportError as exc:
    raise SystemExit(f"A required component is missing: {exc}") from exc


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DEFAULT_OUTPUT_FOLDER = APP_DIR
VORTEX_ARCHIVE_NAME = "Really Blended Bridges.zip"
PLUGIN_NAME = "ReallyBlendedBridges.esp"
OUTPUT_TEXTURE_ROOT = Path("textures") / "ReallyBlendedBridges"
SMIM_TEMPLATE_ASSET = Path("textures") / "smim" / "landscape" / "bridges" / "smim_bridge_dirt.dds"
PREFERENCES_PATH = APP_DIR / "ReallyBlendedBridgesVortexBuilder.preferences.json"
BUNDLED_TEXCONV_LABEL = "Bundled Microsoft DirectXTex texconv"
VORTEX_INSTALL_README = """Really Blended Bridges - Vortex Installation
================================================

1. Open Vortex and select Skyrim Special Edition.
2. On the Mods page, choose Install From File and select this ZIP.
3. Enable Really Blended Bridges when prompted.
4. Deploy Mods.
5. Ensure ReallyBlendedBridges.esp is enabled.

If another plugin edits the same bridge STAT records, create a Vortex plugin rule
that loads ReallyBlendedBridges.esp after that plugin.

This package was generated from the loose landscape textures deployed in Skyrim's
Data folder at build time. Rebuild it after changing landscape textures or Vortex
file-conflict winners.
"""


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    source_stem: str
    output_stem: str
    txst_edid: str

    @property
    def diffuse_asset(self) -> Path:
        return Path("textures") / "landscape" / f"{self.source_stem}.dds"

    @property
    def normal_asset(self) -> Path:
        return Path("textures") / "landscape" / f"{self.source_stem}_n.dds"


VARIANTS = (
    Variant("dirt02", "Default", "dirt02", "rbb_dirt02", "RBB_Dirt02"),
    Variant("snow01", "Snow", "snow01", "rbb_snow01", "RBB_Snow01"),
    Variant("fallforest", "Fall Forest", "fallforestdirt01", "rbb_fallforestdirt01", "RBB_FallForestDirt01"),
    Variant("reach", "Reach", "reachdirt01", "rbb_reachdirt01", "RBB_ReachDirt01"),
    Variant("riverbottom", "River Bottom", "riverbottom", "rbb_riverbottom", "RBB_RiverBottom01"),
)

# Skyrim.esm STAT override, SMIM target shape, runtime shape index, regional TXST.
BRIDGE_TARGETS = (
    (0x00022468, "Bridge01", "Object36", 2, "dirt02"),
    (0x000A7149, "Bridge01Snow", "Object36", 2, "snow01"),
    (0x000B27AE, "Bridge01FallForestDirt01", "Object36", 2, "fallforest"),
    (0x00088640, "Bridge01ReachDirt01", "Object36", 2, "reach"),
    (0x0002F144, "Bridge01RiverBottom01", "Object36", 2, "riverbottom"),
    (0x000CAD7C, "BridgeLong01", "BridgeLong01:4", 1, "dirt02"),
    (0x000EDBC9, "BridgeLong01ReachDirt01", "BridgeLong01:4", 1, "reach"),
    (0x000CAD7D, "BridgeNarrow01", "BridgeNarrow01:4", 1, "dirt02"),
    (0x000EB78F, "BridgeNarrow01_FallForestDirt01", "BridgeNarrow01:4", 1, "fallforest"),
    (0x000CAD7B, "BridgeShort01", "BridgeShort01:4", 1, "dirt02"),
    (0x000EB7A5, "BridgeShort01FallForestDirt01", "BridgeShort01:4", 1, "fallforest"),
)


def load_preferences() -> dict:
    try:
        if PREFERENCES_PATH.is_file():
            value = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        pass
    return {}


def _as_data_folder(candidate: Path) -> Path:
    return candidate if candidate.name.lower() == "data" else candidate / "Data"


def _steam_library_roots() -> list[Path]:
    roots: list[Path] = []
    steam_homes = (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    )
    for steam_home in steam_homes:
        vdf = steam_home / "steamapps" / "libraryfolders.vdf"
        if not vdf.is_file():
            continue
        roots.append(steam_home)
        try:
            for line in vdf.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith('"path"'):
                    parts = stripped.split('"')
                    if len(parts) >= 4:
                        roots.append(Path(parts[3].replace("\\\\", "\\")))
        except OSError:
            pass
    return roots


def _registry_game_roots() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        return roots
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in views:
            try:
                with winreg.OpenKey(hive, uninstall, 0, winreg.KEY_READ | view) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as child:
                                display = str(winreg.QueryValueEx(child, "DisplayName")[0]).lower()
                                location = str(winreg.QueryValueEx(child, "InstallLocation")[0]).strip()
                                if "skyrim special edition" in display and location:
                                    roots.append(Path(location))
                        except OSError:
                            continue
            except OSError:
                continue
    return roots


def discover_data_root(preferred: str = "") -> Path | None:
    candidates: list[Path] = []
    test_data = os.environ.get("RBB_VORTEX_TEST_DATA", "")
    if test_data:
        candidates.append(Path(test_data))
    if preferred:
        candidates.append(Path(preferred))
    candidates.extend((APP_DIR, *list(APP_DIR.parents)[:4]))
    candidates.extend(_registry_game_roots())
    for library in _steam_library_roots():
        candidates.append(library / "steamapps" / "common" / "Skyrim Special Edition")
    for letter in "CDEFGHI":
        candidates.append(Path(f"{letter}:\\") / "SteamLibrary" / "steamapps" / "common" / "Skyrim Special Edition")
    seen = set()
    for candidate in candidates:
        data = _as_data_folder(candidate)
        key = str(data).lower()
        if key in seen:
            continue
        seen.add(key)
        if (data / "Skyrim.esm").is_file():
            return data
    return None


def default_output_folder() -> Path:
    return DEFAULT_OUTPUT_FOLDER


def resolve_deployed_asset(data_root: Path, asset: Path) -> Path | None:
    test_template = os.environ.get("RBB_VORTEX_TEST_TEMPLATE", "")
    if test_template and asset == SMIM_TEMPLATE_ASSET:
        candidate = Path(test_template)
        return candidate if candidate.is_file() else None
    test_landscape = os.environ.get("RBB_VORTEX_TEST_LANDSCAPE_ROOT", "")
    if test_landscape and asset.parent == Path("textures") / "landscape":
        candidate = Path(test_landscape) / asset.name
        return candidate if candidate.is_file() else None
    candidate = data_root / asset
    return candidate if candidate.is_file() else None


def bundled_texconv_path() -> Path | None:
    runtime_root = Path(getattr(sys, "_MEIPASS", APP_DIR))
    candidate = runtime_root / "texconv.exe"
    return candidate if candidate.is_file() else None


def texconv_display_value(path: Path | None) -> str:
    bundled = bundled_texconv_path()
    if path and bundled and path.resolve() == bundled.resolve():
        return BUNDLED_TEXCONV_LABEL
    return str(path or "")


def find_texconv(_data_root: Path | None = None) -> Path | None:
    bundled = bundled_texconv_path()
    if bundled:
        return bundled
    located = shutil.which("texconv.exe") or shutil.which("texconv")
    if located:
        return Path(located)
    guesses = [
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\Fallout 4\Tools\Elric\texconv.exe"),
        Path(r"C:\Program Files\Steam\steamapps\common\Fallout 4\Tools\Elric\texconv.exe"),
    ]
    found = next((path for path in guesses if path.is_file()), None)
    return found


def find_skyrim_esm(data_root: Path) -> Path | None:
    candidate = data_root / "Skyrim.esm"
    return candidate if candidate.is_file() else None


def load_rgba(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            return source.convert("RGBA")
    except Exception as exc:
        raise RuntimeError(f"Could not decode {path.name}: {exc}") from exc


def make_vertical_strip(source_path: Path, size: tuple[int, int]) -> tuple[Image.Image, dict[str, float | int]]:
    source = load_rgba(source_path)
    width, height = size
    if width <= 0 or height <= 0 or width > height:
        raise ValueError(f"Bridge template must be a vertical strip; got {width}x{height}.")
    if source.width != source.height:
        side = min(source.size)
        left, top = (source.width - side) // 2, (source.height - side) // 2
        source = source.crop((left, top, left + side, top + side))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    normalized = source.resize((height, height), resampling)
    crop_left = (height - width) // 2
    strip = normalized.crop((crop_left, 0, crop_left + width, height))
    horizontal = ImageChops.difference(strip.crop((0, 0, 1, height)), strip.crop((width - 1, 0, width, height)))
    vertical = ImageChops.difference(strip.crop((0, 0, width, 1)), strip.crop((0, height - 1, width, height)))
    return strip, {
        "width": width,
        "height": height,
        "horizontal_seam": sum(ImageStat.Stat(horizontal.convert("RGB")).mean) / 3.0,
        "vertical_seam": sum(ImageStat.Stat(vertical.convert("RGB")).mean) / 3.0,
    }


def build_diffuse(template_path: Path, source_path: Path) -> tuple[Image.Image, dict[str, float | int]]:
    template = load_rgba(template_path)
    strip, stats = make_vertical_strip(source_path, template.size)
    result = strip.convert("RGBA")
    result.putalpha(template.getchannel("A"))
    return result, stats


def encode_bc3_with_mips(rgba: Image.Image, output: Path, texconv: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="really_blended_bridges_") as temp_name:
        temp = Path(temp_name)
        png = temp / "rbb_work.png"
        rgba.save(png, format="PNG")
        completed = subprocess.run(
            [str(texconv), "-nologo", "-m", "0", "-f", "BC3_UNORM", "-o", str(temp), str(png)],
            capture_output=True,
            text=True,
            creationflags=0x08000000,
        )
        if completed.returncode:
            details = (completed.stdout + "\n" + completed.stderr).strip()
            raise RuntimeError(f"texconv failed ({completed.returncode}):\n{details}")
        encoded = temp / "rbb_work.dds"
        if not encoded.is_file():
            raise RuntimeError("texconv completed but did not create the expected DDS.")
        shutil.copy2(encoded, output)


def inspect_dds(path: Path) -> tuple[str, int, int, int]:
    data = path.read_bytes()[:148]
    if len(data) < 128 or data[:4] != b"DDS ":
        raise RuntimeError(f"Output validation failed for {path.name}: missing DDS header.")
    height, width, mip_count = struct.unpack_from("<III", data, 12)
    fourcc = data[84:88].decode("ascii", errors="replace")
    return fourcc, mip_count, width, height


def parse_alternate_textures(data: bytes | bytearray) -> list[tuple[str, int, int]]:
    if not data:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    offset, entries = 4, []
    for _ in range(count):
        name_length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        raw_name = bytes(data[offset : offset + name_length])
        offset += name_length
        txst_form_id, shape_index = struct.unpack_from("<II", data, offset)
        offset += 8
        entries.append((raw_name.rstrip(b"\0").decode("cp1252"), txst_form_id, shape_index))
    return entries


def set_alternate_texture(plugin: Plugin, record, shape_name: str, shape_index: int, txst_record) -> None:
    old = record.get_subrecord("MODS")
    entries = parse_alternate_textures(old.data) if old else []
    entries = [entry for entry in entries if entry[0] != shape_name and entry[2] != shape_index]
    entries.append((shape_name, txst_record.form_id.value, shape_index))
    payload, fixup_offsets = bytearray(struct.pack("<I", len(entries))), []
    for name, form_id, index in entries:
        raw_name = name.encode("cp1252") + b"\0"
        payload += struct.pack("<I", len(raw_name)) + raw_name
        form_id_offset = len(payload)
        payload += struct.pack("<II", form_id, index)
        if form_id == txst_record.form_id.value:
            fixup_offsets.append(form_id_offset)
    record.remove_subrecords("MODS")
    mods = record.add_subrecord("MODS", payload)
    for offset in fixup_offsets:
        plugin.write_form_id(mods, offset, txst_record.form_id)


def build_plugin(skyrim_esm: Path, output: Path) -> None:
    master = Plugin.load(skyrim_esm, only_signatures={"STAT"})
    master.set_game("tes5")
    patch = Plugin.new_plugin(output, masters=["Skyrim.esm"], game="tes5")
    patch.header.is_esl = True
    patch.header.author = "Really Blended Bridges Builder"
    patch.header.description = "Landscape-matched SMIM bridge textures generated from the deployed Vortex setup."

    txsts = {}
    for object_id, variant in enumerate(VARIANTS, start=0x800):
        txst = patch.new_record("TXST", edid=variant.txst_edid, form_id=object_id)
        txst.version = 14
        txst.add_subrecord("OBND", b"\0" * 12)
        txst.add_subrecord("TX00", str(Path("ReallyBlendedBridges") / f"{variant.output_stem}.dds"))
        txst.add_subrecord("TX01", str(Path("ReallyBlendedBridges") / f"{variant.output_stem}_n.dds"))
        txsts[variant.key] = txst

    for form_id, expected_edid, shape_name, shape_index, variant_key in BRIDGE_TARGETS:
        source = master.get_record_by_form_id(form_id)
        if source is None or source.editor_id != expected_edid:
            actual = source.editor_id if source else "missing"
            raise RuntimeError(f"Skyrim.esm record {form_id:08X} did not match {expected_edid} (found {actual}).")
        override = patch.copy_record(source, master)
        set_alternate_texture(patch, override, shape_name, shape_index, txsts[variant_key])
    patch.save()


def validate_plugin(path: Path) -> tuple[int, int]:
    plugin = Plugin.load(path)
    plugin.set_game("tes5")
    if not plugin.header.is_esl or plugin.header.masters != ["Skyrim.esm"]:
        raise RuntimeError("Plugin validation failed: expected an ESL-flagged ESP with Skyrim.esm as its only master.")
    txsts = list(plugin.get_records_by_signature("TXST"))
    statics = list(plugin.get_records_by_signature("STAT"))
    if len(txsts) != len(VARIANTS) or len(statics) != len(BRIDGE_TARGETS):
        raise RuntimeError(f"Plugin validation failed: found {len(txsts)} TXST and {len(statics)} STAT records.")
    for record in statics:
        mods = record.get_subrecord("MODS")
        if not mods or not any((fid >> 24) == 1 and (fid & 0xFFFFFF) in range(0x800, 0x805) for _n, fid, _i in parse_alternate_textures(mods.data)):
            raise RuntimeError(f"Plugin validation failed: {record.editor_id} has no generated alternate texture.")
    return len(txsts), len(statics)


def create_vortex_archive(mod_root: Path, archive_path: Path) -> list[str]:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = archive_path.with_name(archive_path.name + ".tmp")
    if temporary_archive.exists():
        temporary_archive.unlink()
    with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(path for path in mod_root.rglob("*") if path.is_file()):
            archive.write(source, source.relative_to(mod_root).as_posix())
    os.replace(temporary_archive, archive_path)
    with zipfile.ZipFile(archive_path, "r") as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"Archive validation failed for {bad_member}.")
        names = archive.namelist()
    expected = {PLUGIN_NAME, "README - Vortex.txt"}
    expected.update((OUTPUT_TEXTURE_ROOT / f"{variant.output_stem}{suffix}.dds").as_posix() for variant in VARIANTS for suffix in ("", "_n"))
    missing = expected.difference(names)
    if missing or len(names) != len(expected):
        raise RuntimeError("Archive validation failed: incorrect package contents" + (f"; missing {sorted(missing)}" if missing else "."))
    return names


class ModernScrollbar(tk.Canvas):
    """Slim, rounded vertical scrollbar backed by a canvas yview command."""

    def __init__(self, parent, command, background: str, accent: str) -> None:
        super().__init__(
            parent,
            width=16,
            bg=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=True,
        )
        self.command = command
        self.track_color = "#E1E5EB"
        self.thumb_color = accent
        self.thumb_hover = "#4848B8"
        self.first = 0.0
        self.last = 1.0
        self.drag_origin = 0
        self.drag_first = 0.0
        self.dragging = False
        self.hovering = False
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def set(self, first, last) -> None:
        self.first, self.last = float(first), float(last)
        self._redraw()

    def _thumb_bounds(self) -> tuple[float, float, float]:
        pad = 9.0
        track_length = max(1.0, self.winfo_height() - pad * 2)
        top = pad + self.first * track_length
        bottom = pad + self.last * track_length
        minimum = 38.0
        if bottom - top < minimum:
            centre = (top + bottom) / 2
            top, bottom = centre - minimum / 2, centre + minimum / 2
            if top < pad:
                bottom += pad - top
                top = pad
            limit = pad + track_length
            if bottom > limit:
                top -= bottom - limit
                bottom = limit
        return top, bottom, track_length

    def _redraw(self) -> None:
        self.delete("all")
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        centre = width / 2
        self.create_line(centre, 9, centre, max(9, height - 9), fill=self.track_color, width=4, capstyle=tk.ROUND)
        top, bottom, _track_length = self._thumb_bounds()
        color = self.thumb_hover if self.hovering or self.dragging else self.thumb_color
        self.create_line(centre, top, centre, bottom, fill=color, width=8, capstyle=tk.ROUND, tags="thumb")

    def _press(self, event) -> None:
        top, bottom, _track_length = self._thumb_bounds()
        if top <= event.y <= bottom:
            self.dragging = True
            self.drag_origin = event.y
            self.drag_first = self.first
        else:
            self.command("scroll", -1 if event.y < top else 1, "pages")
        self._redraw()

    def _drag(self, event) -> None:
        if not self.dragging:
            return
        _top, _bottom, track_length = self._thumb_bounds()
        visible = self.last - self.first
        target = self.drag_first + (event.y - self.drag_origin) / track_length
        self.command("moveto", max(0.0, min(1.0 - visible, target)))

    def _release(self, _event) -> None:
        self.dragging = False
        self._redraw()

    def _enter(self, _event) -> None:
        self.hovering = True
        self._redraw()

    def _leave(self, _event) -> None:
        self.hovering = False
        if not self.dragging:
            self._redraw()


class ReallyBlendedBridgesVortexBuilder(Tk):
    BG = "#F3F5F8"
    CARD = "#FFFFFF"
    INK = "#172033"
    MUTED = "#64748B"
    BORDER = "#DDE3EA"
    ACCENT = "#5B5BD6"
    ACCENT_DARK = "#4848B8"
    SUCCESS = "#168568"
    SUCCESS_BG = "#E8F7F1"
    WARNING = "#B86B13"
    WARNING_BG = "#FFF4E5"

    def __init__(self) -> None:
        super().__init__()
        if os.environ.get("RBB_VORTEX_UI_TEST") == "1":
            self.withdraw()
        self.title("Really Blended Bridges — Vortex Edition")
        self.geometry("1120x820")
        self.minsize(980, 740)
        self.configure(bg=self.BG)
        self.preview_photo = None
        self.preview_source_image = None
        self.preview_zoom = 1.0
        self.preview_zoom_var = StringVar(value="100%")
        self._preview_render_pending = False
        self.last_archive_path: Path | None = None
        self.advanced_visible = False
        preferences = load_preferences()
        use_saved = bool(preferences.get("remember", False)) and os.environ.get("RBB_VORTEX_UI_TEST") != "1"
        saved = preferences if use_saved else {}
        data_root = discover_data_root(str(saved.get("data_root", "")))
        self.data_var = StringVar(value=str(data_root or ""))
        self.template_var = StringVar(value=str(saved.get("template", "")))
        saved_output = str(saved.get("output", ""))
        self.output_var = StringVar(value=saved_output or str(default_output_folder()))
        detected_texconv = find_texconv(data_root)
        saved_texconv = str(saved.get("texconv", ""))
        if saved_texconv == BUNDLED_TEXCONV_LABEL and not bundled_texconv_path():
            saved_texconv = ""
        self.texconv_var = StringVar(value=saved_texconv or texconv_display_value(detected_texconv))
        self.open_folder_var = BooleanVar(value=bool(saved.get("open_folder", True)))
        self.remember_var = BooleanVar(value=use_saved)
        self.preview_variant_var = StringVar(value=str(saved.get("preview_region", VARIANTS[0].label)))
        self.source_detail_var = StringVar(value="Select a region to see its exact source paths.")
        self.ready_title_var = StringVar(value="Checking setup…")
        self.ready_detail_var = StringVar(value="Checking the deployed Skyrim Data folder")
        self.activity_var = StringVar(value="Ready to scan")
        self.winners: dict[str, tuple[Path, Path]] = {}
        self._configure_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.auto_discover(show_dialog=False, force_template=not use_saved)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor="#CBD3DD", lightcolor="#CBD3DD", darkcolor="#CBD3DD", padding=8)
        style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF", bordercolor="#CBD3DD", arrowsize=14, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")], selectbackground=[("readonly", "#FFFFFF")], selectforeground=[("readonly", self.INK)])
        style.configure("Secondary.TButton", font=("Segoe UI", 9, "bold"), foreground=self.INK, background="#EEF1F5", bordercolor="#D7DEE7", padding=(13, 8))
        style.map("Secondary.TButton", background=[("active", "#E2E7ED")])
        style.configure("Primary.TButton", font=("Segoe UI", 11, "bold"), foreground="#FFFFFF", background=self.ACCENT, bordercolor=self.ACCENT, padding=(18, 12))
        style.map("Primary.TButton", background=[("active", self.ACCENT_DARK), ("disabled", "#A9AFC8")], foreground=[("disabled", "#F4F5FA")])
        style.configure("Link.TButton", font=("Segoe UI", 9), foreground=self.ACCENT, background=self.CARD, borderwidth=0, padding=(2, 4))
        style.map("Link.TButton", background=[("active", self.CARD)], foreground=[("active", self.ACCENT_DARK)])
        style.configure("Zoom.TButton", font=("Segoe UI", 9, "bold"), foreground=self.INK, background="#EEF1F5", bordercolor="#D7DEE7", padding=(9, 4))
        style.map("Zoom.TButton", background=[("active", "#E2E7ED")])
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=32, background="#FFFFFF", fieldbackground="#FFFFFF", bordercolor=self.BORDER)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), foreground=self.MUTED, background="#F7F8FA", bordercolor=self.BORDER, padding=(8, 8))
        style.map("Treeview", background=[("selected", "#ECECFF")], foreground=[("selected", self.INK)])
        style.configure("Build.Horizontal.TProgressbar", troughcolor="#E8EBF0", background=self.ACCENT, bordercolor="#E8EBF0", lightcolor=self.ACCENT, darkcolor=self.ACCENT)
        style.configure("TCheckbutton", background=self.CARD, foreground=self.MUTED, font=("Segoe UI", 9))

    def _build_ui(self) -> None:
        header = tk.Frame(self, bg=self.INK, height=96)
        header.pack(fill=X)
        header.pack_propagate(False)
        head_text = tk.Frame(header, bg=self.INK)
        head_text.pack(side=LEFT, padx=28, pady=18)
        tk.Label(head_text, text="Really Blended Bridges — Vortex Edition", bg=self.INK, fg="#FFFFFF", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(head_text, text="Build from your deployed landscape setup, then install the finished ZIP through Vortex.", bg=self.INK, fg="#BFC8D8", font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
        scroll_shell = tk.Frame(self, bg=self.BG)
        scroll_shell.pack(fill=BOTH, expand=True)
        self.scroll_canvas = tk.Canvas(scroll_shell, bg=self.BG, borderwidth=0, highlightthickness=0)
        self.scrollbar = ModernScrollbar(scroll_shell, self.scroll_canvas.yview, self.BG, self.ACCENT)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y", padx=(2, 5), pady=5)
        self.scroll_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        body = tk.Frame(self.scroll_canvas, bg=self.BG)
        self.body_window = self.scroll_canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all")))
        self.scroll_canvas.bind("<Configure>", lambda event: self.scroll_canvas.itemconfigure(self.body_window, width=event.width))
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        body.grid_columnconfigure(0, weight=7)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=self.BG)
        right = tk.Frame(body, bg=self.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(24, 10), pady=20)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 24), pady=20)

        setup = self._card(left)
        setup.pack(fill=X, pady=(0, 12))
        self._section_heading(setup, "1", "Find your deployed Skyrim setup", "In Vortex, deploy your mods first. The app reads the files currently winning in Skyrim's Data folder.")
        self._field_label(setup, "Skyrim Data folder")
        data_row = tk.Frame(setup, bg=self.CARD)
        data_row.pack(fill=X, padx=18, pady=(0, 14))
        ttk.Entry(data_row, textvariable=self.data_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(data_row, text="Choose folder", command=self.choose_data, style="Secondary.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(data_row, text="Auto-detect", command=self.auto_discover, style="Secondary.TButton").pack(side=LEFT, padx=(8, 0))
        ttk.Button(setup, text="▸  Advanced paths", command=self.toggle_advanced, style="Link.TButton").pack(anchor="w", padx=17, pady=(0, 10))
        self.advanced_frame = tk.Frame(setup, bg="#F8F9FB", highlightbackground=self.BORDER, highlightthickness=1)
        self._advanced_path(self.advanced_frame, "SMIM alpha template", self.template_var, self.choose_template)
        self._advanced_path(self.advanced_frame, "Texture converter (texconv.exe)", self.texconv_var, self.choose_texconv)

        sources = self._card(left)
        sources.pack(fill=BOTH, expand=True, pady=(0, 12))
        self._section_heading(sources, "2", "Confirm deployed landscape textures", "All five winners must be deployed as loose files and show Ready before building.")
        tree_wrap = tk.Frame(sources, bg=self.CARD)
        tree_wrap.pack(fill=BOTH, expand=True, padx=18, pady=(0, 8))
        self.tree = ttk.Treeview(tree_wrap, columns=("region", "mod", "status"), show="headings", height=5, selectmode="browse")
        self.tree.heading("region", text="REGION")
        self.tree.heading("mod", text="DEPLOYED SOURCE")
        self.tree.heading("status", text="STATUS")
        self.tree.column("region", width=125, stretch=False)
        self.tree.column("mod", width=330)
        self.tree.column("status", width=90, stretch=False, anchor="center")
        self.tree.tag_configure("ready", foreground=self.SUCCESS)
        self.tree.tag_configure("missing", foreground="#B42318")
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_source)
        tk.Label(sources, textvariable=self.source_detail_var, bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 8), anchor="w", justify="left", wraplength=620).pack(fill=X, padx=19, pady=(0, 12))

        output = self._card(left)
        output.pack(fill=X)
        self._section_heading(output, "3", "Choose where to save the package", "The app creates an installable Really Blended Bridges package.")
        out_row = tk.Frame(output, bg=self.CARD)
        out_row.pack(fill=X, padx=18, pady=(0, 10))
        ttk.Entry(out_row, textvariable=self.output_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(out_row, text="Choose folder", command=self.choose_output, style="Secondary.TButton").pack(side=LEFT, padx=(8, 0))
        options_row = tk.Frame(output, bg=self.CARD)
        options_row.pack(fill=X, padx=18, pady=(0, 14))
        ttk.Checkbutton(options_row, text="Open the package folder after building", variable=self.open_folder_var).pack(side=LEFT)
        ttk.Checkbutton(options_row, text="Remember my choices", variable=self.remember_var).pack(side=LEFT, padx=(16, 0))
        ttk.Button(options_row, text="Save preferences", command=self.save_preferences, style="Secondary.TButton").pack(side="right")

        status = self._card(right)
        status.pack(fill=X, pady=(0, 12))
        self._section_heading(status, "4", "Build the Vortex package", "Creates ten textures, the ESL-flagged plugin, and a clean installable ZIP.")
        self.ready_badge = tk.Frame(status, bg=self.WARNING_BG)
        self.ready_badge.pack(fill=X, padx=18, pady=(0, 12))
        self.ready_icon = tk.Label(self.ready_badge, text="●", bg=self.WARNING_BG, fg=self.WARNING, font=("Segoe UI", 14))
        self.ready_icon.pack(side=LEFT, padx=(12, 9), pady=10)
        self.ready_text = tk.Frame(self.ready_badge, bg=self.WARNING_BG)
        self.ready_text.pack(side=LEFT, fill=X, expand=True, pady=8)
        self.ready_title = tk.Label(self.ready_text, textvariable=self.ready_title_var, bg=self.WARNING_BG, fg=self.INK, font=("Segoe UI", 10, "bold"))
        self.ready_title.pack(anchor="w")
        self.ready_detail = tk.Label(self.ready_text, textvariable=self.ready_detail_var, bg=self.WARNING_BG, fg=self.MUTED, font=("Segoe UI", 8))
        self.ready_detail.pack(anchor="w")
        self.build_button = ttk.Button(status, text="BUILD VORTEX PACKAGE", command=self.build_package, style="Primary.TButton")
        self.build_button.pack(fill=X, padx=18)
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=7, style="Build.Horizontal.TProgressbar")
        self.progress.pack(fill=X, padx=18, pady=(10, 4))
        tk.Label(status, textvariable=self.activity_var, bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 8)).pack(anchor="w", padx=18, pady=(0, 14))

        preview = self._card(right)
        preview.pack(fill=BOTH, expand=True, pady=(0, 12))
        preview_head = tk.Frame(preview, bg=self.CARD)
        preview_head.pack(fill=X, padx=18, pady=(15, 8))
        tk.Label(preview_head, text="Texture preview", bg=self.CARD, fg=self.INK, font=("Segoe UI", 12, "bold")).pack(side=LEFT)
        self.preview_combo = ttk.Combobox(preview_head, textvariable=self.preview_variant_var, values=[v.label for v in VARIANTS], state="readonly", width=14)
        self.preview_combo.pack(side="right")
        self.preview_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_preview())
        preview_tools = tk.Frame(preview, bg=self.CARD)
        preview_tools.pack(fill=X, padx=18, pady=(0, 7))
        tk.Label(preview_tools, text="Mouse wheel to zoom • drag to move", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 8)).pack(side=LEFT)
        ttk.Button(preview_tools, text="Fit", command=self._fit_preview, style="Zoom.TButton").pack(side="right")
        ttk.Button(preview_tools, text="+", command=lambda: self._change_preview_zoom(1.25), style="Zoom.TButton", width=3).pack(side="right", padx=(5, 0))
        tk.Label(preview_tools, textvariable=self.preview_zoom_var, bg=self.CARD, fg=self.INK, font=("Segoe UI", 9, "bold"), width=6).pack(side="right")
        ttk.Button(preview_tools, text="−", command=lambda: self._change_preview_zoom(0.8), style="Zoom.TButton", width=3).pack(side="right")
        self.preview_canvas = tk.Canvas(preview, bg="#E8EBEF", height=350, borderwidth=0, highlightthickness=0, cursor="fleur")
        self.preview_canvas.pack(fill=BOTH, expand=True, padx=18, pady=(0, 8))
        self.preview_canvas.create_text(1, 1, text="Preview appears after scanning", fill=self.MUTED, font=("Segoe UI", 9), tags=("placeholder",))
        self.preview_canvas.bind("<Configure>", self._schedule_preview_render)
        self.preview_canvas.bind("<MouseWheel>", self._preview_mousewheel)
        self.preview_canvas.bind("<ButtonPress-1>", self._preview_pan_start)
        self.preview_canvas.bind("<B1-Motion>", self._preview_pan_move)
        self.preview_caption = tk.Label(preview, text="", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 8), justify="left")
        self.preview_caption.pack(fill=X, padx=18, pady=(0, 12))

        details = self._card(right)
        details.pack(fill=X)
        tk.Label(details, text="Activity details", bg=self.CARD, fg=self.INK, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 4))
        self.log = tk.Text(details, wrap="word", height=5, relief="flat", bg="#F8F9FB", fg="#475569", font=("Consolas", 8), padx=8, pady=8)
        self.log.pack(fill=X, padx=16, pady=(0, 14))

    def _card(self, parent) -> tk.Frame:
        return tk.Frame(parent, bg=self.CARD, highlightbackground=self.BORDER, highlightthickness=1)

    def _on_mousewheel(self, event) -> None:
        if self.scroll_canvas.winfo_exists():
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _section_heading(self, parent, number: str, title: str, subtitle: str) -> None:
        row = tk.Frame(parent, bg=self.CARD)
        row.pack(fill=X, padx=18, pady=(15, 12))
        tk.Label(row, text=number, bg="#ECECFF", fg=self.ACCENT, font=("Segoe UI", 10, "bold"), width=2, height=1).pack(side=LEFT, anchor="n", padx=(0, 10))
        text = tk.Frame(row, bg=self.CARD)
        text.pack(side=LEFT, fill=X, expand=True)
        tk.Label(text, text=title, bg=self.CARD, fg=self.INK, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(text, text=subtitle, bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

    def _field_label(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(0, 5))

    def _advanced_path(self, parent, label: str, variable: StringVar, command) -> None:
        row = tk.Frame(parent, bg="#F8F9FB")
        row.pack(fill=X, padx=12, pady=8)
        tk.Label(row, text=label, bg="#F8F9FB", fg=self.MUTED, font=("Segoe UI", 8, "bold"), width=26, anchor="w").pack(side=LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(row, text="Browse", command=command, style="Secondary.TButton").pack(side=LEFT, padx=(7, 0))

    def toggle_advanced(self) -> None:
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced_frame.pack(fill=X, padx=18, pady=(0, 14))
        else:
            self.advanced_frame.pack_forget()

    def write_log(self, text: str, clear: bool = False) -> None:
        if clear:
            self.log.delete("1.0", END)
        self.log.insert(END, text.rstrip() + "\n")
        self.log.see(END)
        self.update_idletasks()

    def current_texconv(self) -> Path | None:
        value = self.texconv_var.get().strip()
        if value == BUNDLED_TEXCONV_LABEL:
            return bundled_texconv_path()
        candidate = Path(value) if value else None
        return candidate if candidate and candidate.is_file() else None

    def auto_discover(self, show_dialog: bool = True, force_template: bool = True) -> None:
        self.activity_var.set("Auto-detecting Skyrim, deployed textures, SMIM, and tools…")
        current_root = self.data_var.get()
        root = discover_data_root(current_root)
        if root:
            self.data_var.set(str(root))
            if not self.output_var.get():
                self.output_var.set(str(default_output_folder()))
        else:
            self.activity_var.set("Skyrim Data was not detected — choose its folder")
            self.update_readiness()
            if show_dialog:
                messagebox.showwarning("Skyrim not detected", "Choose Skyrim Special Edition's Data folder — the folder containing Skyrim.esm.")
            return

        if force_template or not Path(self.template_var.get()).is_file():
            template = resolve_deployed_asset(root, SMIM_TEMPLATE_ASSET)
            if template:
                self.template_var.set(str(template))
        if not self.current_texconv():
            converter = find_texconv(root)
            if converter:
                self.texconv_var.set(texconv_display_value(converter))
        self.resolve_all(show_dialog=show_dialog)

    def save_preferences(self, show_dialog: bool = True) -> None:
        try:
            if self.remember_var.get():
                data = {
                    "remember": True,
                    "data_root": self.data_var.get(),
                    "template": self.template_var.get(),
                    "output": self.output_var.get(),
                    "texconv": self.texconv_var.get(),
                    "open_folder": bool(self.open_folder_var.get()),
                    "preview_region": self.preview_variant_var.get(),
                }
            else:
                data = {"remember": False}
            PREFERENCES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.activity_var.set("Preferences saved beside the application" if self.remember_var.get() else "Saved preferences disabled")
            if show_dialog:
                messagebox.showinfo("Preferences saved", f"Settings file:\n{PREFERENCES_PATH}")
        except OSError as exc:
            if show_dialog:
                messagebox.showerror("Could not save preferences", f"The application folder is not writable:\n{PREFERENCES_PATH.parent}\n\n{exc}")
            else:
                self.write_log(f"Could not save preferences: {exc}")

    def on_close(self) -> None:
        if os.environ.get("RBB_VORTEX_UI_TEST") != "1":
            self.save_preferences(show_dialog=False)
        self.destroy()

    def choose_data(self) -> None:
        selected = filedialog.askdirectory(title="Choose Skyrim Special Edition's Data folder", initialdir=self.data_var.get() or None)
        if selected:
            self.data_var.set(str(_as_data_folder(Path(selected))))
            self.auto_discover(show_dialog=False, force_template=True)

    def choose_template(self) -> None:
        selected = filedialog.askopenfilename(title="Choose the SMIM bridge dirt texture", filetypes=[("DDS texture", "*.dds"), ("All files", "*.*")])
        if selected:
            self.template_var.set(selected)
            self.refresh_preview()
            self.update_readiness()

    def choose_output(self) -> None:
        selected = filedialog.askdirectory(title="Choose where to save the package", initialdir=self.output_var.get() or None)
        if selected:
            self.output_var.set(selected)
            self.update_readiness()

    def choose_texconv(self) -> None:
        selected = filedialog.askopenfilename(title="Choose texconv.exe", filetypes=[("texconv", "texconv.exe"), ("Executable", "*.exe")])
        if selected:
            self.texconv_var.set(selected)
            self.update_readiness()

    def resolve_all(self, show_dialog: bool = True) -> None:
        root = Path(self.data_var.get())
        self.activity_var.set("Scanning deployed loose textures…")
        self.winners.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        missing = []
        for variant in VARIANTS:
            diffuse = resolve_deployed_asset(root, variant.diffuse_asset)
            normal = resolve_deployed_asset(root, variant.normal_asset)
            if diffuse and normal:
                self.winners[variant.key] = (diffuse, normal)
                source_name, status, tag = "Skyrim Data", "✓  Deployed", "ready"
            else:
                missing.append(variant.label)
                source_name, status, tag = "Not found", "Missing", "missing"
            self.tree.insert("", END, iid=variant.key, values=(variant.label, source_name, status), tags=(tag,))
        self.write_log("Deployed texture scan:", clear=True)
        for variant in VARIANTS:
            pair = self.winners.get(variant.key)
            self.write_log(f"  {variant.label}: {'DEPLOYED' if pair else 'MISSING'}")
        self.activity_var.set("Scan complete" if not missing else "Some regional textures are missing")
        self.update_readiness()
        self.refresh_preview()
        if missing and show_dialog:
            messagebox.showwarning("Deployment incomplete", "The following deployed loose textures could not be found: " + ", ".join(missing) + ".\n\nIn Vortex, enable your landscape mods and SMIM, resolve conflicts, then click Deploy Mods before scanning again. Textures stored only inside a BSA must be extracted as loose files.")

    def show_selected_source(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        pair = self.winners.get(selected[0])
        self.source_detail_var.set(f"Diffuse: {pair[0]}\nNormal:  {pair[1]}" if pair else "This region is missing a loose diffuse or normal texture.")

    def update_readiness(self) -> bool:
        root = Path(self.data_var.get())
        problems = []
        if not find_skyrim_esm(root):
            problems.append("choose Skyrim's deployed Data folder")
        if len(self.winners) != len(VARIANTS):
            problems.append(f"deploy all {len(VARIANTS)} regional texture sets")
        if not Path(self.template_var.get()).is_file():
            problems.append("locate the SMIM alpha template")
        if not self.current_texconv():
            problems.append("locate texconv.exe")
        if not self.output_var.get().strip():
            problems.append("choose a package output folder")
        ready = not problems
        if ready:
            self.ready_title_var.set("Ready to build")
            self.ready_detail_var.set("5 deployed regions • template and tools verified")
            badge_bg, icon_color = self.SUCCESS_BG, self.SUCCESS
            self.build_button.state(["!disabled"])
        else:
            self.ready_title_var.set("Setup needs attention")
            self.ready_detail_var.set("Please " + ", then ".join(problems[:2]))
            badge_bg, icon_color = self.WARNING_BG, self.WARNING
            self.build_button.state(["disabled"])
        for widget in (self.ready_badge, self.ready_text, self.ready_icon, self.ready_title, self.ready_detail):
            widget.configure(bg=badge_bg)
        self.ready_icon.configure(fg=icon_color)
        return ready

    def refresh_preview(self) -> None:
        variant = next((item for item in VARIANTS if item.label == self.preview_variant_var.get()), VARIANTS[0])
        template, pair = Path(self.template_var.get()), self.winners.get(variant.key)
        if not template.is_file() or not pair:
            self.preview_source_image = None
            self.preview_photo = None
            self._show_preview_message("Preview unavailable")
            self.preview_caption.configure(text="Deploy your Vortex setup and resolve the missing files first.")
            return
        try:
            rgba, stats = build_diffuse(template, pair[0])
            checker = Image.new("RGBA", rgba.size, "#D9DDE3")
            draw = ImageDraw.Draw(checker)
            tile = 32
            for y in range(0, rgba.height, tile):
                for x in range(0, rgba.width, tile):
                    if (x // tile + y // tile) % 2:
                        draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill="#F1F3F6")
            checker.alpha_composite(rgba)
            self.preview_source_image = checker.convert("RGB")
            self.preview_zoom = 1.0
            self._render_preview_zoom()
            self.preview_caption.configure(text=f"{variant.label} • deployed winner\nOutput: {stats['width']} × {stats['height']} • SMIM alpha preserved")
        except Exception as exc:
            self.preview_source_image = None
            self.preview_photo = None
            self._show_preview_message("Preview failed")
            self.preview_caption.configure(text=str(exc))
            self.write_log(f"Preview failed: {exc}")

    def _show_preview_message(self, message: str) -> None:
        self.preview_canvas.delete("all")
        width = max(self.preview_canvas.winfo_width(), 100)
        height = max(self.preview_canvas.winfo_height(), 100)
        self.preview_canvas.create_text(width // 2, height // 2, text=message, fill=self.MUTED, font=("Segoe UI", 9), tags=("placeholder",))
        self.preview_canvas.configure(scrollregion=(0, 0, width, height))
        self.preview_zoom_var.set("100%")

    def _schedule_preview_render(self, _event=None) -> None:
        if self._preview_render_pending:
            return
        self._preview_render_pending = True
        self.after_idle(self._render_scheduled_preview)

    def _render_scheduled_preview(self) -> None:
        self._preview_render_pending = False
        if self.preview_source_image is not None:
            self._render_preview_zoom()
        elif self.preview_canvas.find_withtag("placeholder"):
            self._show_preview_message(self.preview_canvas.itemcget("placeholder", "text"))

    def _render_preview_zoom(self) -> None:
        source = self.preview_source_image
        if source is None:
            return
        self.preview_canvas.update_idletasks()
        canvas_width = max(self.preview_canvas.winfo_width(), 100)
        canvas_height = max(self.preview_canvas.winfo_height(), 100)
        fit_scale = min((canvas_width - 20) / source.width, (canvas_height - 20) / source.height)
        scale = max(0.01, fit_scale * self.preview_zoom)
        width = max(1, round(source.width * scale))
        height = max(1, round(source.height * scale))
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        display = source.resize((width, height), resampling)
        self.preview_photo = ImageTk.PhotoImage(display)
        region_width = max(canvas_width, width)
        region_height = max(canvas_height, height)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(region_width // 2, region_height // 2, image=self.preview_photo, anchor="center")
        self.preview_canvas.configure(scrollregion=(0, 0, region_width, region_height))
        self.preview_canvas.xview_moveto(max(0.0, (region_width - canvas_width) / (2 * region_width)))
        self.preview_canvas.yview_moveto(max(0.0, (region_height - canvas_height) / (2 * region_height)))
        self.preview_zoom_var.set(f"{round(self.preview_zoom * 100)}%")

    def _change_preview_zoom(self, multiplier: float) -> None:
        if self.preview_source_image is None:
            return
        self.preview_zoom = min(8.0, max(0.5, self.preview_zoom * multiplier))
        self._render_preview_zoom()

    def _fit_preview(self) -> None:
        self.preview_zoom = 1.0
        self._render_preview_zoom()

    def _preview_mousewheel(self, event):
        self._change_preview_zoom(1.15 if event.delta > 0 else 1 / 1.15)
        return "break"

    def _preview_pan_start(self, event) -> None:
        self.preview_canvas.scan_mark(event.x, event.y)

    def _preview_pan_move(self, event) -> None:
        self.preview_canvas.scan_dragto(event.x, event.y, gain=1)

    def build_package(self, show_dialog: bool = True) -> Path | None:
        if not self.update_readiness():
            if show_dialog:
                messagebox.showwarning("Setup incomplete", "Deploy the missing files and complete the highlighted requirements before building.")
            return None
        self.build_button.state(["disabled"])
        self.progress["value"] = 0
        self.configure(cursor="watch")
        try:
            data_root = Path(self.data_var.get())
            template, output_folder, texconv = Path(self.template_var.get()), Path(self.output_var.get()), self.current_texconv()
            if not texconv:
                raise FileNotFoundError("The bundled or selected texconv.exe could not be loaded.")
            skyrim_esm = find_skyrim_esm(data_root)
            if not skyrim_esm:
                raise FileNotFoundError("Skyrim.esm was not found in the selected Data folder.")
            template_size = load_rgba(template).size
            output_folder.mkdir(parents=True, exist_ok=True)
            archive_path = output_folder / VORTEX_ARCHIVE_NAME
            self.write_log(f"Building Vortex package:\n  {archive_path}", clear=True)
            with tempfile.TemporaryDirectory(prefix="really_blended_bridges_vortex_") as temp_name:
                mod_root = Path(temp_name) / "Really Blended Bridges"
                output_texture_dir = mod_root / OUTPUT_TEXTURE_ROOT
                for index, variant in enumerate(VARIANTS, start=1):
                    self.activity_var.set(f"Creating {variant.label} textures ({index} of {len(VARIANTS)})…")
                    diffuse_source, normal_source = self.winners[variant.key]
                    diffuse, stats = build_diffuse(template, diffuse_source)
                    normal, _normal_stats = make_vertical_strip(normal_source, template_size)
                    diffuse_output = output_texture_dir / f"{variant.output_stem}.dds"
                    normal_output = output_texture_dir / f"{variant.output_stem}_n.dds"
                    encode_bc3_with_mips(diffuse, diffuse_output, texconv)
                    encode_bc3_with_mips(normal, normal_output, texconv)
                    for produced in (diffuse_output, normal_output):
                        fourcc, mips, width, height = inspect_dds(produced)
                        if (width, height) != template_size or fourcc != "DXT5" or mips <= 1:
                            raise RuntimeError(f"DDS validation failed for {produced.name}: {width}x{height}, {fourcc}, {mips} mips")
                    self.progress["value"] = index
                    self.write_log(f"✓ {variant.label}: {stats['width']}x{stats['height']}")

                self.activity_var.set("Creating and validating the ESL-flagged plugin…")
                plugin_path = mod_root / PLUGIN_NAME
                build_plugin(skyrim_esm, plugin_path)
                txst_count, stat_count = validate_plugin(plugin_path)
                (mod_root / "README - Vortex.txt").write_text(VORTEX_INSTALL_README, encoding="utf-8")
                self.progress["value"] = 6
                self.write_log(f"✓ {PLUGIN_NAME}\n✓ {txst_count} TXST records\n✓ {stat_count} bridge overrides")

                self.activity_var.set("Compressing and validating the Vortex package…")
                names = create_vortex_archive(mod_root, archive_path)
                self.progress["value"] = 7
                self.write_log(f"✓ Installable ZIP validated ({len(names)} files)")

            self.last_archive_path = archive_path
            self.activity_var.set("Vortex package completed successfully")
            if self.remember_var.get():
                self.save_preferences(show_dialog=False)
            if show_dialog:
                messagebox.showinfo("Vortex package created", f"Your installable package is ready:\n\n{archive_path}\n\nIn Vortex, choose Install From File, select this ZIP, enable it, and Deploy Mods.\n\n10 textures • {stat_count} bridge overrides • ESL-flagged ESP")
            if self.open_folder_var.get():
                subprocess.Popen(["explorer.exe", str(output_folder)], creationflags=0x08000000)
            return archive_path
        except Exception as exc:
            self.activity_var.set("Build failed — see Activity details")
            self.write_log("\nBUILD FAILED\n" + traceback.format_exc())
            if show_dialog:
                messagebox.showerror("Build failed", str(exc))
            return None
        finally:
            self.configure(cursor="")
            self.update_readiness()


def self_test() -> None:
    data_root = discover_data_root()
    assert data_root and find_skyrim_esm(data_root)
    template_value = os.environ.get("RBB_VORTEX_TEST_TEMPLATE")
    landscape_value = os.environ.get("RBB_VORTEX_TEST_LANDSCAPE_ROOT")
    if not template_value or not landscape_value:
        raise RuntimeError(
            "Set RBB_VORTEX_TEST_TEMPLATE and RBB_VORTEX_TEST_LANDSCAPE_ROOT before running --self-test"
        )
    template = Path(template_value)
    landscape_root = Path(landscape_value)
    assert template.is_file() and landscape_root.is_dir()
    winners = {}
    for variant in VARIANTS:
        diffuse = landscape_root / variant.diffuse_asset.name
        normal = landscape_root / variant.normal_asset.name
        assert diffuse.is_file() and normal.is_file(), variant.label
        winners[variant.key] = (diffuse, normal)
        normal_strip, _normal_stats = make_vertical_strip(normal, (512, 2048))
        assert normal_strip.size == (512, 2048)
    image, stats = build_diffuse(template, winners["dirt02"][0])
    assert image.size == (512, 2048) == (stats["width"], stats["height"])
    skyrim_esm = find_skyrim_esm(data_root)
    assert skyrim_esm
    with tempfile.TemporaryDirectory(prefix="rbb_vortex_test_") as temp_name:
        temp_root = Path(temp_name)
        mod_root = temp_root / "mod"
        mod_root.mkdir(parents=True)
        test_plugin = mod_root / PLUGIN_NAME
        build_plugin(skyrim_esm, test_plugin)
        assert validate_plugin(test_plugin) == (5, 11)
        converter = find_texconv(data_root)
        assert converter and converter.is_file()
        test_dds = temp_root / "converter_test.dds"
        encode_bc3_with_mips(image, test_dds, converter)
        fourcc, mips, width, height = inspect_dds(test_dds)
        assert (fourcc, width, height) == ("DXT5", 512, 2048)
        assert mips > 1
        for variant in VARIANTS:
            destination = mod_root / OUTPUT_TEXTURE_ROOT / f"{variant.output_stem}.dds"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(test_dds, destination)
            shutil.copy2(test_dds, destination.with_name(f"{variant.output_stem}_n.dds"))
        (mod_root / "README - Vortex.txt").write_text(VORTEX_INSTALL_README, encoding="utf-8")
        names = create_vortex_archive(mod_root, temp_root / VORTEX_ARCHIVE_NAME)
        assert len(names) == 12


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
        raise SystemExit(0)
    if "--ui-test" in sys.argv:
        os.environ["RBB_VORTEX_UI_TEST"] = "1"
        if not os.environ.get("RBB_VORTEX_TEST_TEMPLATE") or not os.environ.get("RBB_VORTEX_TEST_LANDSCAPE_ROOT"):
            raise RuntimeError(
                "Set RBB_VORTEX_TEST_TEMPLATE and RBB_VORTEX_TEST_LANDSCAPE_ROOT before running --ui-test"
            )
        with tempfile.TemporaryDirectory(prefix="rbb_vortex_ui_test_") as temp_name:
            _app = ReallyBlendedBridgesVortexBuilder()
            _app.update_idletasks()
            assert Path(_app.data_var.get()).name.lower() == "data"
            assert len(_app.winners) == 5
            assert str(_app.build_button["state"]) != "disabled"
            assert _app.preview_photo is not None
            assert _app.preview_source_image is not None
            _app._change_preview_zoom(1.25)
            assert _app.preview_zoom == 1.25
            assert _app.preview_zoom_var.get() == "125%"
            _app._fit_preview()
            assert _app.preview_zoom == 1.0
            assert _app.current_texconv() and _app.current_texconv().is_file()
            if bundled_texconv_path():
                assert _app.texconv_var.get() == BUNDLED_TEXCONV_LABEL
            assert Path(_app.template_var.get()).name.lower() == "smim_bridge_dirt.dds"
            _first, _last = _app.scroll_canvas.yview()
            assert _last < 1.0
            assert isinstance(_app.scrollbar, ModernScrollbar)
            _app.scrollbar.command("moveto", 0.1)
            _app.update_idletasks()
            assert _app.scroll_canvas.yview()[0] > _first
            _app.output_var.set(temp_name)
            _app.open_folder_var.set(False)
            archive_path = _app.build_package(show_dialog=False)
            assert archive_path and archive_path.is_file()
            with zipfile.ZipFile(archive_path) as archive:
                assert len(archive.namelist()) == 12
            _app.destroy()
        raise SystemExit(0)
    ReallyBlendedBridgesVortexBuilder().mainloop()
