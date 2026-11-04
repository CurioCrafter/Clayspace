# Hands and adaptive UI v2 implementation status

Reference: `D:/LargeAmountOfMeshesForShipwreckVR/Blender_XR_Hands_and_Adaptive_UI_Implementation_Specification.docx`,
version 2.0, September 21, 2026. The reference was read in full, remains
unchanged, and is design input rather than evidence of implemented behavior.

## Product decisions adopted

The primary product is a source-native semantic task surface; the existing
internal full-window editor becomes an expert compatibility feature. Begin
with Arrange Objects, Edit Mesh and Session, one detailed panel and at most
six primary actions including More. Reuse View3D and Blender's authoritative
data/undo rather than a new editor type or duplicate scene.

The user's direct requirements take precedence: only Index cameras, genuine
bare hands, no controller requirement and no virtual keyboard. Desktop and
runtime recovery remain available, but cannot be counted as completion of
the hands-only core workflow. The document's virtual keyboard proposal is not
adopted. Full text entry remains an unresolved hands-only interaction boundary;
buttons/sliders can cover bounded numeric controls without introducing a keyboard.

Semantic IDs, fixed slots, matching draw/hit generations and capture ownership
must remain stable while engaging. No OCR or image-based button discovery.
Topology release freezes a preview until explicit Apply. Two-hand scale's first
release freezes and second qualified release commits; loss is never release.
The single-hand radial scale remains an explicit accessibility alternative.
Two-hand uniform scale has software-level state and transaction validation in
exact Blender 5.1.2, but is not optical-hardware-qualified. The existing
quaternion rotation is not yet a positional ring.

## Current evidence and next implementation

The source-built editor surface, optical-only native sample API, gesture
hysteresis, reversible object/component transforms, and guarded XR lifecycle
provide reusable foundations. Their passing software checks do not close the
optical or native task workflow acceptance gates.

The source add-on now previews and commits an Object Mode selection from a
frozen visible ray target, with a 10 mm default click limit. Synthetic hand
tests and isolated Blender 5.1.2 selection/undo tests passed, including an
empty-selection cancel that restores no active object. Mesh component selection
and physical optical selection remain unverified.

The source-native semantic card now has a six-slot Arrange Objects page with
Select, Move, Rotate, Scale and Add Cube actions, plus a Session page for panel
opacity, Undo, Redo, Cancel Edit and Exit XR. Cancel Edit is disabled without
an active transaction and rolls back a preview without stopping optical input.
The remaining More slot shows a disabled reason
rather than pretending to work. Source-native drawing, generation guards, capture
cancellation, tool switching, Add Cube, Undo, Redo, Cancel Edit and Exit passed a SteamVR-free
Monado simulator test on the current full-feature source build, with no
physical Index display claim. Earlier eye-image inspection exposed mirrored
text; placement now uses the rendered stereo basis, with matching +X/+Y drawing
and hit testing verified. A later simulator capture showed targets and labels
too small for a hands-first default; native size/distance defaults and the
page-switch hit area were enlarged and a second eye-image check passed.
Physical readability/comfort remain unmeasured. The frozen launcher build passed ten lifecycle cycles
and continuous draw/recovery checks. Final swapchain readback and physical
display comfort remain unverified. The complete adaptive registry and modeling
scenario remain work to implement, not scaffolding counted as a feature.

The Index-only sensing spike is an isolated Mercury inference core evaluation.
No hand camera driver has been installed. A hands-present run exposed an OpenCV
debug-image crash; the disabled-debug pointer was corrected and its keypoint
branch regression passed. A bounded live retest then processed thirty fresh,
distinct stereo pairs without crashing but detected zero active hands and zero
valid HMD poses. The runtime reported tracking out of range on all thirty
samples. SteamVR device metadata showed zero connected tracking references;
a hands-on test confirmed no Lighthouse base station is available. The temporary power
settings were explicitly restored and read back (pause on standby true, screen
timeout 5 seconds). No room images or joint traces were saved. Optical tracking
is not qualified, and model redistribution licensing is unresolved. Passthrough
initialization has separate consented evidence; it is not optical tracking.
The later aggregate-counter probe could not initialize OpenVR (`NoLogPath`,
code 112) and stopped before acquiring camera frames or changing headset power
settings. The user then explicitly requested bypassing SteamVR, so this OpenVR
camera route is diagnostic history only; it is not the implementation path for
the SteamVR-free product. With SteamVR stopped, the physical Index DirectShow
camera subsequently produced five advancing 1920x960 YUY2 frames to a null
output. A separate 15-second consented DirectShow-to-Mercury diagnostic then
processed 14 non-flat, distinct pairs with zero detector scores over 0.3 and
zero hand regions/keypoints. Source review found the evaluator's unconditional
vertical flip did not match Mercury's active Index path, so it was removed and
the corrected Release evaluator passed an eye/row mapping test. A second
consented 15-second run still processed 14 distinct pairs with zero detector
scores above 0.3. A third consented 15-second run with aggregate brightness and
timing counters processed 13 distinct pairs and again found zero detector scores
above 0.3. Its center-eye grayscale means were 61.56/67.93, first-frame read
was 2.91 seconds, subsequent reads averaged 0.94 seconds, and Mercury processing
averaged 2.68 ms per pair. No frames or joint traces were saved. This validates
independent camera acquisition, not hand inference or headset-relative calibration.
For the requested see-through hand visualization, a separate one-click desktop
stereo preview now gates a thin blue estimated contour on current-frame palm
and landmark agreement (or a labeled Mercury region fallback). Its Release
offline self-test and a read-only smoke on the user-provided screenshot passed;
the screenshot produced one conservative contour per camera half. The camera
was not reopened after the user deferred live testing. This does not establish
live detection, calibrated in-headset passthrough, or hand input in Blender.

## v2 acceptance boundaries

These IDs refer only to the new document; v1's 22-case ledger is separate.

| v2 cases | Current evidence | Remaining gate |
| --- | --- | --- |
| AT-01 | Background missing-provider API returns unavailable and zero samples | No-headset GUI fault exercise |
| AT-02 | Native actual-method fixture rejects controller/unknown provenance | Real provider matrix |
| AT-03–04 | Pure pinch neutral/hysteresis/stale tests | Physical gestures |
| AT-05–06 | Native joint validity checks | Tool-specific router and physical required-joint tests |
| AT-07–10 | Not implemented/qualified | Poke, popup neutral entry, opacity/occlusion targeting |
| AT-11–14 | Native Arrange/Session draw/hit generations, capture guards and cancellation passed simulator checks; full-feature binary reached SteamVR-free Monado task-card assertions for four tools, Add Cube, Undo, Redo and Exit | Physical eye-image presentation and full semantic registry |
| AT-15–16 | Actual Blender simulator move and constraint tests | Optical capture |
| AT-17 | 10 mm default click slop and exact selection cancel/commit passed pure and isolated Blender tests | Physical optical click and mesh component selection |
| AT-18–22 | Synthetic two-hand tests cover coherent capture, locked pivot/separation, bounded uniform preview, first-release freeze, second-release commit, no-resume and neutral rearm; exact Blender 5.1.2 proved one transaction with exact cancel and one undo/redo | Physical optical separation, release, crossing and identity tests |
| AT-23–25 | Synthetic loss, backward-time, stable-generation and viewer-transform cancellation tests plus exact Blender restore | Real provider recenter, source reset and file/focus faults |
| AT-26 | Actual Blender object and component undo/redo passed | Physical hand workflow |
| AT-27 | Real Blender simulator no-op undo test passed | Physical no-op workflow |
| AT-28–30 | Not implemented | Transactional topology and async evaluation |
| AT-31 | Presentation overlay does not alter scene geometry | Native navigation/save exercise |
| AT-32 | Unqualified modifier cages explicitly rejected in Blender tests | In-headset explanation and eligible cage selection |
| AT-33–34 | Not implemented | Adaptive slots and one-hand complete workflow |
| AT-35 | Native editor text dispatch tested internally | Hands-only text interaction remains unresolved |
| AT-36 | Crash guard and keypoint regression passed; SteamVR hands-ready retest processed 30 fresh pairs, zero hands and zero valid HMD poses; SteamVR-free DirectShow-to-Mercury diagnostics processed 14, 14 and 13 distinct pairs with zero detector scores over 0.3, including after the row-orientation correction and aggregate brightness/timing probe | Detector diagnosis, calibrated pose and latency tests; Lighthouse tracking unavailable on this hardware setup |
| AT-37 | Corrected native candidate passed 100 camera-off XR cycles; later candidate passed 1520 continuous XR draw callbacks and Recall/recovery | Camera-active, final-candidate and long editing-soak coverage |
| AT-38–39 | Isolated save/reload omits presentation objects/images; metadata export redacts | Final full-stack privacy and upstream round-trip checks |
| AT-40 | Not implemented | Full native core scenario without desktop panels |

No v2 release profile is qualified. Calibration, optical accuracy, full-stack
90 Hz measurements, user comfort and controller-free completion require actual
connected/worn hardware and measured fixtures.
