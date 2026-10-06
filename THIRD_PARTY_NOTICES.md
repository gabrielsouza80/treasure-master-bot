# Third-party notices

No external repository source was copied or vendored for the runtime milestone.
Original small implementations use architectural inspiration as identified in
[EXTERNAL_REUSE_AUDIT.md](docs/EXTERNAL_REUSE_AUDIT.md).

| Project | URL | Reviewed upstream commit | License | Use |
| --- | --- | --- | --- | --- |
| eatventure-autobot-v2 | https://github.com/RACentino/eatventure-autobot-v2 | dc0af31ea2717fd860e915504824f7503078e257 | Apache-2.0 | Inspiration: protocol boundaries, foreground checks, watchdog |
| eatventure-bot | https://github.com/KarahanKARA/eatventure-bot | e9b32150e795fc2218307aca8af865989ca2cdc3 | Apache-2.0 | Inspiration: forbidden zones, original implementation |
| uiautomator2 | https://github.com/openatx/uiautomator2 | 6de6d4ce6e944998544e3f71aab5dbb31bd663f8 | MIT, copyright (c) 2017 openatx | Optional installed library 3.7.0, app/hierarchy read APIs |

BlockBlast-AI-Autopilot and scrcpy-opencv remain reference-only because compatible
licensing for their custom source was not confirmed. No code from them is included.

uiautomator2 is distributed by its upstream package, not embedded in this source
tree. Its MIT notice/license must accompany any redistributed copy of that
library: https://github.com/openatx/uiautomator2/blob/6de6d4ce6e944998544e3f71aab5dbb31bd663f8/LICENSE
Keep licenses/notices of all installed transitive packages if distributing an
environment. Existing OpenCV/NumPy remain upstream dependencies, not copied code.

Live capture additionally uses installed PyAV 18.0.0 (BSD-3-Clause Python
bindings; retain distribution notices for its bundled FFmpeg libraries if
redistributing binaries). No decoder code is vendored. The original read-only
scrcpy metadata reader references official scrcpy 4.1 (Apache-2.0), reviewed
commit 2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0 and its Streamer.java/
DesktopConnection.java protocol definitions. No upstream source block is copied.
https://github.com/Genymobile/scrcpy/tree/v4.1
https://github.com/PyAV-Org/PyAV
