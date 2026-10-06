# External reuse audit

Reviewed 2026-10-06. Reference checkouts are outside this project in
`C:\game-references`. No reference source code was copied or vendored. Decisions
below distinguish original implementations inspired by architecture from reuse
of an installed library. SHA values identify the actual inspected checkouts.

## RACentino/eatventure-autobot-v2

- PROJECT: https://github.com/RACentino/eatventure-autobot-v2
- LICENSE: Apache-2.0, actual root LICENSE inspected.
- COMMIT_SHA_REVIEWED: dc0af31ea2717fd860e915504824f7503078e257
- USEFUL_COMPONENTS: protocol boundaries for capture/input; transitions separated
  from handlers; foreground safety; same-state watchdog; runtime metrics.
- REUSE_DECISION: ADAPT architectural ideas, original implementation.
- REASON: Treasure Master needs observation/decision/I/O boundaries around its
  existing polar detector. Our watchdog recommends UI reinspection/STOP rather
  than resetting into another active handler indefinitely. Foreground uncertainty
  blocks gameplay. No Eatventure templates, states, notification integrations or
  automatic handler actions were imported.
- FILES/IDEAS_INSPECTED: `src/eatventure_autobot/domain/protocols.py`,
  `state/transitions.py`, `runtime/bot.py`, `resilience/watchdog.py`.

## Mathr81/BlockBlast-AI-Autopilot

- PROJECT: https://github.com/Mathr81/BlockBlast-AI-Autopilot
- LICENSE: no LICENSE/COPYING found in reviewed tree; compatibility unconfirmed.
- COMMIT_SHA_REVIEWED: 96f3774a116dfec5901dd86e8069e27ed7e12f23
- USEFUL_COMPONENTS: separate control/vision modules; mapping reference device
  coordinates to actual resolution; callback-based scrcpy frames.
- REUSE_DECISION: REFERENCE_ONLY; no source adaptation permitted without license.
- REASON: independently implement normalized device coordinates and frame
  packets. Reject grid solving, reinforcement-learning dependencies, fixed
  revive pixel checks and automatically issued touches. Its Python scrcpy
  integration is not evidence of compatibility with our installed scrcpy 4.1.
- FILES/IDEAS_INSPECTED: `run_autopilot.py`, `autopilot/control.py`,
  `autopilot/vision.py`, `autopilot/config.py`, `requirements.txt`, README.

## KarahanKARA/eatventure-bot

- PROJECT: https://github.com/KarahanKARA/eatventure-bot
- LICENSE: Apache-2.0; read actual LICENSE before examining potential reuse.
- COMMIT_SHA_REVIEWED: e9b32150e795fc2218307aca8af865989ca2cdc3
- USEFUL_COMPONENTS: forbidden click rectangles; state separation; window checks.
- REUSE_DECISION: ADAPT safety ideas with original code.
- REASON: deny zones take precedence over configured launch zones; default launch
  zones are empty until calibrated. Its absolute-coordinate click path can bypass
  relative-coordinate checks, so no equivalent bypass is supplied. Reject GDI
  desktop mouse control, game-specific pixel coordinates and automatic recovery.
- FILES/IDEAS_INSPECTED: `state_machine.py`, `mouse_controller.py`,
  `window_capture.py`, runtime loop in `bot.py`.

## openatx/uiautomator2

- PROJECT: https://github.com/openatx/uiautomator2
- LICENSE: MIT, copyright 2017 openatx; actual LICENSE inspected.
- COMMIT_SHA_REVIEWED: 6de6d4ce6e944998544e3f71aab5dbb31bd663f8
- USEFUL_COMPONENTS: `app_current()` and `dump_hierarchy()`; hierarchy attributes
  text/content-desc/resource-id/bounds/enabled/clickable.
- REUSE_DECISION: REUSE optional library, no vendored source.
- REASON: accessibility evidence complements image analysis without OCR or
  brittle close-button templates. The read-only adapter requires an injected,
  already initialized client. Connecting a fresh client may deploy u2.jar/start
  the device server; validation therefore did not initialize it on a phone.
- FILES/IDEAS_INSPECTED: LICENSE, `pyproject.toml`, `uiautomator2/__init__.py`,
  `uiautomator2/core.py`. Released PyPI version verified as 3.7.0, Python >=3.8,<4:
  https://pypi.org/project/uiautomator2/3.7.0/
  The checkout's dynamic-version placeholder is not a released-version claim.

## RobbertH/scrcpy-opencv

- PROJECT: https://github.com/RobbertH/scrcpy-opencv
- LICENSE: no root license confirmed for fork additions. Nested `scrcpy/LICENSE`
  is Apache-2.0, but coverage of custom additions is unconfirmed.
- COMMIT_SHA_REVIEWED: 1de8efaad9a9653e04cb1f446b990b41b33f1aff
- USEFUL_COMPONENTS: decoded AVFrame -> BGR/OpenCV observation boundary.
- REUSE_DECISION: REFERENCE_ONLY.
- REASON: old scrcpy v1.12.1 fork, vision directly calls input. Neither copying
  custom source nor adopting the fork as our base is justified. Our frame packet
  adapter has no protocol or input code.
- FILES/IDEAS_INSPECTED: README changed-file list, nested LICENSE,
  `scrcpy/app/src/opencv_injection.cpp`, frame conversion and circle/input coupling.

## Additional primary API references

- Official scrcpy 4.1 protocol architecture:
  https://github.com/Genymobile/scrcpy/blob/v4.1/doc/develop.md
- Window behavior: https://github.com/Genymobile/scrcpy/blob/v4.1/doc/window.md
- Python client comparison:
  https://github.com/leng-yue/py-scrcpy-client/blob/main/scrcpy/core.py
  (reviewed server version constant 2.4; not installed/adopted as a 4.1 client).

See RUNTIME_FOUNDATION.md for transport selection and deployment limitations.
