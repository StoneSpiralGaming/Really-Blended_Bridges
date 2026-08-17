# Really Blended Bridges

Source code for the Mod Organizer 2 and Vortex editions of **Really Blended Bridges**.

Really Blended Bridges generates regional dirt-overlay textures for the supported Static Mesh Improvement Mod bridge meshes, matching the landscape textures active in the user's Skyrim setup. It also creates the accompanying ESL-flagged plugin.

## Editions

- `src/mo2/really_blended_bridges_builder.py` reads loose-file winners from a selected MO2 profile and creates the finished mod directly in that MO2 instance.
- `src/vortex/really_blended_bridges_vortex_builder.py` reads Vortex's deployed loose files from Skyrim's Data folder and creates an installable ZIP.

The two editions share the same texture-generation and plugin-generation approach but use different discovery and output workflows.

## Running from source

Requirements:

- Windows
- Python 3.12 or newer
- Pillow
- [esplib](https://github.com/BadDogSkyrim/esplib) at the revision listed in `requirements.txt`
- Microsoft DirectXTex `texconv.exe`

Install the Python dependencies:

```powershell
py -m pip install -r requirements.txt
```

Run the MO2 edition:

```powershell
py src/mo2/really_blended_bridges_builder.py
```

Run the Vortex edition:

```powershell
py src/vortex/really_blended_bridges_vortex_builder.py
```

Place `texconv.exe` beside the script or select it under Advanced Paths in the application.

## Building executables

Install the development requirements and package the desired entry point with PyInstaller:

```powershell
py -m pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean --onefile --windowed --name ReallyBlendedBridgesBuilder src/mo2/really_blended_bridges_builder.py
pyinstaller --noconfirm --clean --onefile --windowed --name ReallyBlendedBridgesVortexBuilder src/vortex/really_blended_bridges_vortex_builder.py
```

The official portable downloads also package `texconv.exe` and its required DirectXTex files beside the executable.

## Repository scope

This repository contains source code and documentation only. It intentionally excludes:

- compiled executables and release archives
- preference files containing local paths
- generated DDS textures and ESP files
- SMIM meshes or textures
- landscape textures from other mods
- DirectXTex binaries

See the edition-specific documents in `docs/` for end-user operation details.

## Third-party components

- [Pillow](https://python-pillow.org/) is used for image processing and previews.
- [esplib](https://github.com/BadDogSkyrim/esplib) is used for Bethesda plugin reading and writing under the Mozilla Public License 2.0.
- Microsoft DirectXTex/`texconv` is used for DDS encoding under the MIT License. DirectXTex binaries are not stored in this source repository.

No source landscape or SMIM textures are included.
