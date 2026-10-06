# Read-only S24 observations

Observed locally on 2026-10-06: Samsung S24, Android 16, native 1080x2340
screen. Device identifiers, endpoints, raw hierarchies and recordings are
excluded from this document. The user operated the phone manually. The observer
used video-only scrcpy and hierarchy queries; no application input occurred.

## Evidence, not trusted signatures

| Package | Activity | Observation / limitation |
| --- | --- | --- |
| `com.gimica.treasuremaster` | `com.unity3d.player.UnityPlayerActivity` | Gameplay and target/knife angles observed; the same activity alone does not establish gameplay or a menu. |
| `com.gimica.treasuremaster` | `com.unity3d.ads.adplayer.FullScreenWebViewDisplay` | Naturally encountered ad-related activity. One reviewed 60-second window yielded 25 hierarchy probes with four nodes and no actionable close candidate. |
| `com.gimica.treasuremaster` | `com.google.android.gms.ads.AdActivity` | Naturally encountered during the ten-minute manual session; this is observation evidence, not a universal ad classifier. |
| `com.gimica.treasuremaster` | `com.vungle.ads.internal.ui.VungleActivity` | Another naturally encountered ad-related activity; not added to trusted runtime signatures. |
| `com.gimica.treasuremaster` | `com.moloco.sdk.xenoss.sdkdevkit.android.adrenderer.internal.templates.renderer.fullscreen.FullscreenWebviewActivity` | Naturally encountered later in the manual session; no provider-specific closing behavior was inferred. |
| `com.android.vending` | `com.google.android.finsky.transparentmainactivity.HsdpAlias` | Foreground changed to Play Store during manual use. Hierarchy had 80 then 113 nodes; a close-related candidate had top-right bounds but no actionable evidence. The observer did not open or close the store. |

The reviewed 60-second ad/store window contained 3,418 visual UNKNOWN frames,
zero targets and no PLAYING classifications. Its 43 hierarchy probes succeeded.
The absence of English close labels does not prove no visible close button:
Unity/WebView controls may not be accessible to UIAutomator. A label/resource ID
also does not prove an ad control is safe. X/× remains explicitly ambiguous.

The catalogue retains sanitized package/activity, candidate types/counts,
actionable/ambiguous evidence, coarse bounds, node-count range and signature
reentries. Repeated polling is not a second independent ad episode. Reentry can
also mean a UI phase change in one ad. No entry is promoted to VERIFIED, and
none is loaded as trusted by the runtime. UNKNOWN is preserved unless existing
visual gameplay and fresh expected foreground evidence agree.

Play Store also exposed `com.google.android.finsky.transparentmainactivity.TransparentMainActivityPrivate`
and `com.google.android.finsky.activities.MainActivity` during manual foreground
changes. These are external foreground evidence, not proof of a particular ad
provider or permission to return to the game.

## Timing and menus

Time from ad start until legitimate close availability was not established.
The ten-minute session did expose actionable-evidence close candidates in Unity
(top-right), Google Mobile Ads (top-left), and Moloco (top-right) hierarchies.
None was clicked or treated as verified. No ambiguous X/× or Continue/Restart
candidate was returned by those successful hierarchy snapshots; absence of
accessible candidates does not establish absence of visible canvas controls.
Polling timestamps establish hierarchy observation times, not ad onset or
complete UI visibility. No provider-specific timing or close sequence is
assumed. Continue/Restart text alone cannot distinguish a game menu from an ad;
no new trusted menu signature or automatic action was introduced.

Recordings and detailed JSON/JSONL evidence remain local under ignored
`debug/runtime-live/`. A second recording, if obtained, is additional validation
material; it is not a manually labeled held-out accuracy benchmark yet.
The additional passive recording contains 1,708 frames, 1080x2340, 29.93 seconds
at 57.066 average FPS. Raw review at 0/10/20 seconds showed a Restart screen and
gameplay with different targets/knife appearances. Restart was visible on the
canvas but not returned as a hierarchy candidate. No Restart was pressed by
the observer. Continue was not established by this review.

All 1,708 recorded frames processed successfully offline: 423 PLAYING, 1,285
UNKNOWN, 12 transitions, 50.87 PLAYING pipeline FPS. Two PLAYING intervals were
shorter than 0.2 seconds, and count jumps flagged difficult boundaries. Stable
count variation (54) exceeded raw variation (46) in this recording. These are
unresolved vision validation limitations, not grounds to weaken the classifier
or authorize firing. Independent manual annotations are still required.
Raw review of the two short intervals at approximately 9.23 and 12.33 seconds
showed gameplay, including a hit flash in the latter. Their brevity indicates
intermittent gameplay recall rather than an observed non-gameplay false positive
at those reviewed timestamps. This does not validate every frame or knife count.

Eleven visual PLAYING frames preceded the UI poll updating from a cached Moloco
activity to UnityPlayerActivity. Their plausible target geometry and adjacent
gameplay suggest polling lag, but raw frames from that exact boundary were not
retained, so this is an inference, not verified absence of false positives.
The final live CLI therefore requires the known gameplay activity as well as
the expected package before valid knife processing. Visual state remains an
independent diagnostic. Initial startup or transition lag can conservatively
delay runtime PLAYING until a fresh matching snapshot arrives.
