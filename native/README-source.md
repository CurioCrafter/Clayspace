# Rebuilding the published Blender XR source

The repository contains project-owned add-on, launcher, probe, test and documentation
source, plus **one consolidated patch** against Blender 5.1.2. It does not contain
Blender binaries, upstream source/vendor trees, Windows libraries, personal profiles,
purchased add-ons, models, camera imagery/traces, local calibration or runtime logs.
Blender modifications retain their upstream license notices; see upstream's `COPYING`
after checkout. Project add-on license terms are in the repository `LICENSE` and manifests.

## Reproduce the SteamVR build

Use a Windows machine with Visual Studio 2022 C++ tools (including the Windows SDK),
CMake, Ninja, Git and Git LFS. Put CMake, Ninja and Git on `PATH`. The build script
discovers Visual Studio 2022 using `vswhere`; explicit `-VisualStudioPath` and
`-NinjaExe` overrides are available. Dependencies are not fetched automatically.

Clone this project first (GitHub access is required while the repository is private),
then obtain the exact official revisions:

```powershell
git clone https://github.com/CurioCrafter/Clayspace.git
Set-Location Clayspace
git lfs install
git clone https://projects.blender.org/blender/blender.git native/blender-5.1.2
git -C native/blender-5.1.2 checkout --detach ec6e62d40fa9e9d1bea33ad5d00148c99a4f0832
git -C native/blender-5.1.2 apply --check ../patches/blender-5.1.2-xr-workbench.patch
git -C native/blender-5.1.2 apply ../patches/blender-5.1.2-xr-workbench.patch

git clone https://projects.blender.org/blender/lib-windows_x64.git native/dependencies/windows_x64_5_1
git -C native/dependencies/windows_x64_5_1 checkout --detach 17e87e0869383bcf1dd8339502e6008b7c0c8eb0
git -C native/dependencies/windows_x64_5_1 lfs pull
powershell -NoProfile -ExecutionPolicy Bypass -File native/build_feature_complete.ps1 -Target install -Jobs 4
```

The install target is explicitly confined to `native/build-features/bin`, not
`Program Files` or an existing Blender installation. The runnable result is
`native/build-features/bin/blender.exe` with its locally installed runtime resources.
Run these commands in a normal, non-administrator shell. Dependency checkout/build
downloads and disk usage are substantial; the repository does not redistribute them.

`native/source-lock.json` records the pinned revisions, fresh patch digest, and **every
changed native source path**, including files that were untracked in the development
checkout. Its `patch_verified_against_base` means the patch was applied to a disposable
Git index at the pinned upstream revision and the resulting source was compared with
the exported development snapshot. It is not a claim that a fresh-machine build or
physical device test was performed by the export script.

The build enables Blender's release feature configuration with XR and Vulkan, but
vendor-specific Cycles GPU kernel bundles are disabled. GPU renderer production parity
therefore needs additional SDK/build and hardware work.

To start the new build using your installed SteamVR runtime without importing personal
add-ons, use the clean isolated profile:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/launch_steamvr_blender.ps1 -Launch -CleanProfile
```

This creates private local state after checkout. The default personal add-on launcher
requires a separately prepared local profile; no purchased add-ons or preferences are
distributed in this repository. Follow `docs/personal-addons-vr-workspace.md` when
preparing your own copies. Native UI access does not guarantee every third-party
add-on has a qualified controller or gesture interaction.

## Focused checks

```powershell
python -m unittest discover -s tests
native/build-features/bin/blender.exe --background --factory-startup --python native/test_editor_api.py
native/build-features/bin/blender.exe --background --factory-startup --python native/test_feature_build.py
```

In a Visual Studio developer shell, the extracted actual-code fixtures need no headset:

```powershell
python native/test_editor_input.py
python native/test_native_failure_paths.py
python native/test_xr_image_vertex_format.py
```

GUI/OpenXR and controller checks remain separate and opt-in. Read the relevant test
script and current verification limits in `docs/steamvr-index-workspace.md` before
launching them. No export command starts a headset, camera or Blender.

## Optional experimental camera / SteamVR-free research

Authored probe sources are retained for review, not presented as a self-contained
rebuild of the experimental runtime. Monado/Mercury/Rectus vendor checkouts, private
evaluation models, downloaded SDKs and modified runtime DLLs are deliberately absent.
The camera providers also need their documented upstream sources and OpenVR header
dependency; `native/providers/mercury_offline/CMakeLists.txt` checks for missing inputs.
Some research fixtures expect an excluded vendor checkout and cannot pass until it
is supplied separately. None of these providers is needed for the camera-off SteamVR
Blender build above. See the existing provider/runtime documents for research status;
calibrated passthrough and base-station-free 6DoF are not implied by this source export.
