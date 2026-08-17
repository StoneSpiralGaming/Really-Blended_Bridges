Really Blended Bridges - Landscape Matching for SMIM
=====================================================

Your landscapes. Your bridges. Finally blended.

Really Blended Bridges makes the dirt overlays on SMIM's bridges match the
landscape textures installed in your game.

Instead of shipping bridge textures designed around one particular landscape
overhaul, the included builder detects the winning textures in your active MO2
profile and generates matching overlays specifically for your setup. Regional
variants are created for the default landscape, snow, fall forest, the Reach,
and riverbeds.

The original source textures are read only and never changed. Generated files
always match the SMIM bridge template dimensions (normally 512x2048).

Blended Roads is not required. The name is a playful nod to Blended Roads and
its "Really Blended Roads" option.

Regional inputs
---------------
- Default:      textures\landscape\dirt02.dds
- Snow:         textures\landscape\snow01.dds
- Fall Forest:  textures\landscape\fallforestdirt01.dds
- Reach:        textures\landscape\reachdirt01.dds
- River Bottom: textures\landscape\riverbottom.dds

The matching _n normal map is also resolved and converted for every region.

Bridge targets
--------------
- bridge01.nif:       Object36, runtime shape index 2
- bridgelong01.nif:   BridgeLong01:4, runtime shape index 1
- bridgenarrow01.nif: BridgeNarrow01:4, runtime shape index 1
- bridgeshort01.nif:  BridgeShort01:4, runtime shape index 1

Plugin coverage
---------------
ReallyBlendedBridges.esp contains five new TXST records and overrides 11
Skyrim.esm STAT records: the default bridge statics plus their Snow, Fall
Forest, Reach, and River Bottom variants where those records exist. Existing
alternate-texture assignments are preserved. The plugin carries the ESL flag
and uses Skyrim.esm as its only master.

Usage
-----
1. Open ReallyBlendedBridgesBuilder.exe.
2. The app automatically looks for the MO2 instance, active profile, SMIM alpha
   template, regional texture winners, Skyrim.esm, converter, and output folder.
3. If anything is not detected, choose the MO2 folder and click Auto-detect setup.
4. Confirm that all five regional rows show Ready.
5. The default output is <MO2 folder>\mods\Really Blended Bridges.
6. Confirm that the status card says Ready to build.
7. Click BUILD TEXTURES + PLUGIN.
8. Enable ReallyBlendedBridges.esp and place it after other plugins that edit
   the same bridge STAT records.

Technical paths are available under Advanced paths if automatic detection needs
correcting. Enable Remember my choices and click Save preferences to create
ReallyBlendedBridgesBuilder.preferences.json beside the EXE.

Texture preview
---------------
Select any detected region from the preview menu. Use the minus and plus buttons
or the mouse wheel over the preview to zoom from 50% to 800%. Click and drag the
image to pan around it. Fit returns the complete texture to the preview area.

The header remains fixed while the main content scrolls vertically. The automatic
resolver handles loose files in enabled mods and MO2's overwrite folder. If a
winning regional texture exists only inside a BSA, it must first be extracted as
a loose file.

Output details
--------------
- Output mod folder: Really Blended Bridges
- Plugin: ReallyBlendedBridges.esp
- Texture folder: textures\ReallyBlendedBridges
- 10 DDS files: diffuse + normal for five regions
- Native template dimensions (normally 512x2048)
- BC3/DXT5 with full mip chains
- Diffuse textures retain the SMIM template alpha exactly
- One square landscape tile runs over the full strip height and is centre-cropped
  horizontally, preserving the source landscape scale

Portable converter
------------------
The standalone EXE contains Microsoft's official signed DirectXTex texconv
2026.5.8.1, so an external installation is not required. DirectXTex is
distributed under the MIT License. See DirectXTex_LICENSE.txt and:
https://github.com/microsoft/DirectXTex

Third-party component
---------------------
Plugin reading/writing uses BadDogSkyrim/esplib 0.2.0 under the Mozilla Public
License 2.0, commit 7a24a5a7906ab9eb227a039d6e2b191229045f46.
Source: https://github.com/BadDogSkyrim/esplib/tree/7a24a5a7906ab9eb227a039d6e2b191229045f46
License: https://www.mozilla.org/MPL/2.0/
