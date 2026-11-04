# Rectus passthrough provider feasibility (M0)

Checked September 21, 2026 against Rectus `v0.4.2-beta` (`603c4c8e8d707ba2130c6a472ce5e539e3841a72`) and current wiki revision `15c8f8a34b6750f561a2b031b0869d885f2458ea`.

## Decision

The pinned release was installed after explicit user consent, but **it does not provide a supported Blender-only activation path on Windows**. Its setup utility registers the manifest under the 64-bit machine-wide implicit-layer key:

`HKLM\SOFTWARE\Khronos\OpenXR\1\ApiLayers\Implicit`

The manifest has `disable_environment = DISABLE_XR_APILAYER_NOVENDOR_steamvr_passthrough`, but no `enable_environment`. Rectus also states that application-provided explicit API layers are not supported. The disable variable can opt a process out after global registration; it cannot turn the staged ZIP into a supported opt-in layer for Blender alone.

When loaded, the layer reads per-user configuration from `%APPDATA%\OpenXR SteamVR Passthrough\config.ini`, writes per-application logs under `%LOCALAPPDATA%\OpenXR SteamVR Passthrough`, and can launch its menu process. Its menu also has an explicit **Dump Camera Frame to File** control. Never use that control during M0; the workbench report must contain status/timing metadata only, never pixels.

Do not run `passthrough-setup.exe`, edit the layer registry, enable a camera, or load the DLL without the user's explicit consent. The consented installation is now registered but **globally disabled between controlled tests**. SteamVR remains the active OpenXR runtime.

## Consented local result — September 21, 2026

- The exact pinned manifest/DLL were installed through `scripts/manage_passthrough.ps1`, matching the upstream registry registration method. The x64 Visual C++ runtime was already present.
- A conservative configuration disables menu autostart, camera-frame dumping, RenderDoc, composition overrides, and undocumented camera block-queue paths. The default 2D projection was retained.
- SteamVR `camera/enableCamera` was false, was enabled with user consent, and took effect after the user restarted SteamVR.
- Current-instance loader evidence proves Rectus loaded; the native fork enabled `XR_FB_passthrough`, created the route, reported `passthrough_status=active` and `composition_route=fb_passthrough`, and released it on shutdown. Provider metadata confirms D3D11 interop initialization.
- Evidence: `.tmp_xr_probe_cffaa360d1944adaa10c8b7e45707f25`. The strict continuous-draw assertion **failed** because the runtime stayed SYNCHRONIZED. Camera initialization is verified, in-headset visual quality is not.
- Registry status after the test: registered=true, enabled=false. The SteamVR camera setting remains enabled for further explicitly consented camera-only tracking tests. No camera images were recorded or exported.

## Pinned artifacts inspected without execution

| Artifact | SHA-256 |
| --- | --- |
| `native/providers/rectus-openxr-steamvr-passthrough-v0.4.2-beta/openxr-steamvr-passthrough-0.4.2.zip` | `ECAC0872C3C0F48877F2E58467B4AE676A459B412279B382835E3E3ED1238F2D` |
| Release DLL inside ZIP | `49D119D7498D5C03FB185C3C14D23826F8885FF17B8CB2A8E444B1078CC59A56` |
| Release manifest inside ZIP | `07F7D61C0DCA0D39880F5116A81FF6CF33093C3444C63A60B1CC6D4D3DCC9E0A` |
| `native/providers/rectus-openxr-steamvr-passthrough-v0.4.2-beta/source-v0.4.2-beta.zip` | `B2E1A25E91689B2D3591E4A4DAA4A2BF7E63365EEB65D8C72AC04DF04BE5FADB` |

The binary release is 5,879,461 bytes and contains the layer DLL/manifest, setup/menu/calibration executables, README, and `THIRD_PARTY.txt`. It does not contain the repository `LICENSE` file. The tagged source is MIT licensed, copyright 2025 Rectus. If the provider is redistributed, include that MIT license and the supplied third-party notices.

The hashes above were computed locally from GitHub's TLS download. No upstream checksum/signature attestation was published with the release metadata, so these hashes pin the inspected bytes but are not an independent authenticity guarantee.

Pinned upstream links:

- [Release v0.4.2-beta](https://github.com/Rectus/openxr-steamvr-passthrough/releases/tag/v0.4.2-beta)
- [Release commit](https://github.com/Rectus/openxr-steamvr-passthrough/commit/603c4c8e8d707ba2130c6a472ce5e539e3841a72)
- [Tagged manifest](https://github.com/Rectus/openxr-steamvr-passthrough/blob/603c4c8e8d707ba2130c6a472ce5e539e3841a72/XR_APILAYER_NOVENDOR_steamvr_passthrough/XR_APILAYER_NOVENDOR_steamvr_passthrough.json)
- [Pinned setup registration source](https://github.com/Rectus/openxr-steamvr-passthrough/blob/603c4c8e8d707ba2130c6a472ce5e539e3841a72/passthrough-setup/passthrough-setup.cpp#L25-L28)
- [MIT license](https://github.com/Rectus/openxr-steamvr-passthrough/blob/603c4c8e8d707ba2130c6a472ce5e539e3841a72/LICENSE)
- [Current compatibility matrix](https://github.com/Rectus/openxr-steamvr-passthrough/wiki/Compatibility)
- [Native OpenXR setup notes](https://github.com/Rectus/openxr-steamvr-passthrough/wiki/Setup-Native-OpenXR)
- [Khronos loader API-layer rules](https://registry.khronos.org/OpenXR/specs/1.1/loader.html#api-layer-manifest-file-format)

## Graphics-backend result

| Blender application binding | Upstream evidence | M0 position |
| --- | --- | --- |
| OpenGL (`XR_KHR_opengl_enable`) | Supported since Rectus 0.3.5, currently labeled “limited performance.” The current source handles `XrGraphicsBindingOpenGLWin32KHR` through the Direct3D 11 interop renderer and disables provider depth support. The compatibility wiki records Blender 4.5.3 alpha blending, with frame-timing/incorrect-pose warnings at low frame rates. | First qualification route because it has the only published Blender result. Blender 5.1.2, this binary, this driver, latency, calibration, and headset comfort remain unverified. |
| Vulkan 2 (`XR_KHR_vulkan_enable2`) | Supported but currently labeled “limited performance.” The default source path uses Direct3D 11 interop for the Vulkan application binding and marks provider depth unsupported. Release 0.4.2 also adds an asynchronous internal Vulkan processing renderer; that does not itself prove the application's Vulkan binding is faster. | Qualify only after the native log proves `XR_KHR_vulkan_enable2` was enabled. Measure; do not infer improvement from the release note. |
| Legacy Vulkan (`XR_KHR_vulkan_enable`) | Wiki says it works with a possible timeline-semaphore availability failure. In v0.4.2 source, the default renderer rejects passthrough unless the unconfirmed-features option is allowed or the legacy Vulkan renderer is selected. | Not the default M0 route. Treat it as unsupported for this build until the exact extension and configuration are observed and a controlled test passes. |

Rectus's Blender evidence is for 4.5.3, not 5.1.2. No upstream source provides measured latency, frame-time, or calibration values for this machine.

## Integration with Blender's existing `XR_FB_passthrough` route

After a consented global installation, the implicit layer advertises `XR_FB_passthrough` version 4. The layer's support is partial: underlays, reconstructed mode, depth testing, and color adjustments are listed; overlays, custom surface projection, keyboard hands, edge color, and color remapping are not.

Use Blender's native route rather than Rectus override modes for acceptance:

1. Prove Rectus loaded in the live Blender instance using bounded, current-instance provider or loader evidence, such as a fresh per-application Rectus log tied to that Blender launch. Registry presence proves registration only. Blender's `enabled_api_layer` native markers cover explicit `XrInstanceCreateInfo` layers and cannot enumerate an active implicit layer; without separate provider/loader instrumentation, Rectus activation is **unknown**.
2. Blender must enumerate and enable `XR_FB_passthrough` before instance creation.
3. The existing native code creates the passthrough feature/layer and requests an underlay only after the user's in-app passthrough confirmation.
4. Accept the route only when current-instance provider/loader evidence proves the Rectus implicit layer loaded and native markers show `enabled_extension=XR_FB_passthrough`, `passthrough_status=active`, and `composition_route=fb_passthrough` during the same live session. Never infer implicit-layer activation from the absence or presence of explicit-layer markers.
5. Keep Rectus overrides off. An override can make imagery appear without proving Blender's native integration.

The add-on remains a control/status surface and must not read camera frames. Rectus, if explicitly enabled, is the camera/compositor owner.

## Minimal consented setup path

This remains the consent/qualification checklist; the local result above records the steps already performed.

1. Ask for explicit consent to install an experimental machine-wide API layer and to enable the Index cameras. Separately warn that Rectus reports possible heavy flicker in 3D stereo mode for photosensitive users.
2. Confirm SteamVR Room View works. Rectus states that if Room View cannot access the camera, the layer cannot access it either.
3. Confirm the x64 Visual C++ runtime requirement without installing anything silently.
4. Extract the pinned release to a permanent, user-approved directory. Do not run any executable from the review ZIP.
5. With the user's approval, run the upstream setup utility elevated and choose Install. This writes the implicit HKLM registration. Confirm the exact manifest path and DWORD state afterward.
6. Keep the layer globally disabled in SteamVR between controlled tests. Rectus's environment variable supports per-process opt-out, not a supported install-free Blender-only opt-in.
7. Start the isolated Blender 5.1.2 workbench with camera/passthrough off. Confirm the extension marker and bounded current-instance provider/loader evidence first; if that evidence is unavailable, report implicit-layer activation as unknown and do not pass the gate.
8. Only after an explicit in-app confirmation, request passthrough. Start with OpenGL, no override, conservative 2D mode, and an immediate room-only/off control. Test Vulkan 2 separately.
9. Record camera failure behavior, stereo calibration against a real target, frame timing, reprojection behavior, and clean stop/recovery. Registration or visible imagery alone does not pass M0.
10. Roll back with the upstream setup utility's Uninstall action and verify the registry entry is gone. Preserve configuration/log files for review unless the user separately asks to remove them.

## Remaining gates

- **Verified:** pinned artifact identity; machine-wide registration and controlled disable; current-instance layer loading; OpenGL-to-D3D11 initialization; native FB route activation and shutdown; SteamVR camera enable setting.
- **Inferred from upstream design:** Rectus can bridge SteamVR camera frames into Blender's native `XR_FB_passthrough` underlay path when all advertised features initialize successfully.
- **Unverified:** independent binary provenance/signature; sustained Blender 5.1.2 headset visibility; camera freshness; stereo calibration; frame timing; OpenGL versus Vulkan performance; complete runtime-fault recovery; photosensitivity/comfort.

M0 must remain incomplete until the unverified hardware and timing gates are measured in a consented session.
