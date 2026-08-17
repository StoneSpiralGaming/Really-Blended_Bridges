Really Blended Bridges - Vortex Edition
=======================================

Your landscapes. Your bridges. Finally blended.

This separate builder creates a Vortex-installable Really Blended Bridges package
from the landscape textures currently deployed into Skyrim Special Edition's Data
folder. It does not read or modify the frozen MO2 edition.

Before building
---------------
1. In Vortex, enable SMIM and your landscape texture mods.
2. Resolve any file conflicts so the desired landscape textures win.
3. Click Deploy Mods.
4. Close Skyrim and any programs using its files.

Building
--------
1. Open ReallyBlendedBridgesVortexBuilder.exe.
2. Confirm that the correct Skyrim Data folder was detected. It must contain
   Skyrim.esm.
3. Confirm that all five deployed landscape rows show Deployed.
4. Choose where to save the package.
5. Click BUILD VORTEX PACKAGE.

Installing the result
---------------------
1. Open Vortex and select Skyrim Special Edition.
2. On the Mods page, choose Install From File.
3. Select "Really Blended Bridges.zip".
4. Enable the installed mod and click Deploy Mods.
5. Ensure ReallyBlendedBridges.esp is enabled.

If another plugin edits the same bridge STAT records, create a Vortex plugin rule
that loads ReallyBlendedBridges.esp after it.

Rebuild the package after changing landscape mods or Vortex file-conflict winners.
Always deploy the changed setup before rebuilding so the Data folder represents the
new winners.

What the builder detects
------------------------
- Skyrim Special Edition's deployed Data folder
- Skyrim.esm
- SMIM's deployed alpha template
- Five deployed landscape diffuse textures
- Five matching deployed normal maps
- The bundled Microsoft DirectXTex converter

Regional inputs
---------------
- Default:      textures\landscape\dirt02.dds
- Snow:         textures\landscape\snow01.dds
- Fall Forest:  textures\landscape\fallforestdirt01.dds
- Reach:        textures\landscape\reachdirt01.dds
- River Bottom: textures\landscape\riverbottom.dds

Output package
--------------
- ReallyBlendedBridges.esp, ESL-flagged with Skyrim.esm as its only master
- Five 512x2048 regional diffuse overlays using SMIM's original alpha
- Five matching 512x2048 normal maps
- BC3/DXT5 compression with full mip chains
- A Vortex installation README

The generated ZIP has plugin and texture folders at its root and can be installed
directly through Vortex. Original source textures are read only and never modified.

Limitations
-----------
The relevant SMIM and landscape textures must exist as loose deployed files. Files
stored only inside BSA archives cannot currently be selected. Because the builder
reads Vortex's final deployed view, it reliably sees the winning file but does not
attempt to identify the staging-mod name that supplied it.

Preferences
-----------
Enable Remember my choices and click Save preferences to create
ReallyBlendedBridgesVortexBuilder.preferences.json beside the EXE.

Portable converter
------------------
The executable contains Microsoft's DirectXTex texconv 2026.5.8.1 under the MIT
License. See DirectXTex_LICENSE.txt and https://github.com/microsoft/DirectXTex

Plugin component
----------------
Plugin reading/writing uses BadDogSkyrim/esplib 0.2.0 under the Mozilla Public
License 2.0, commit 7a24a5a7906ab9eb227a039d6e2b191229045f46.
Source: https://github.com/BadDogSkyrim/esplib/tree/7a24a5a7906ab9eb227a039d6e2b191229045f46
