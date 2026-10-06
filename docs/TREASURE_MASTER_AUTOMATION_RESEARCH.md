# Treasure Master automation research

DATE_ACCESSED for all entries: 2026-10-06. Community comments describe individual
experiences, not guaranteed rules or a trustworthy implementation. No community
scripts were installed, executed or imported. Searches included Treasure Master
bot/autoclicker/Automate/JustPlay/ad automation/restart ads/knife collision on
GitHub, Reddit and the public web. No compatible maintained game-specific Android
bot was verified in those results; this is not a claim that none exists.

## OBSERVED

Our local recording `debug/treasure-sample-2.mp4` and existing regression suite
show changing circular targets, attached knives, transitions, menus and ads.
The conservative baseline separates 2209 PLAYING from 4419 UNKNOWN frames.
These observations do not establish live input timing or every possible ad UI.

## OFFICIAL / VERIFIED

- URL: https://play.google.com/store/apps/details?id=com.gimica.treasuremaster
- WHAT_IT_DOES: official listing describes throwing knives at monsters and boss
  battles; verifies listing/package identity `com.gimica.treasuremaster`.
- WHAT_WE_CAN_REUSE: explicit package identity for foreground checks, supplied
  through configuration rather than silently selecting any attached device.
- LIMITATIONS: listing does not specify collision timing, ad-control semantics,
  direction-change rules or reliable automation behavior.
- SAFETY/RISK: package identity alone cannot distinguish an in-package ad.
- DECISION: REFERENCE_ONLY official identity/description.

## COMMUNITY_REPORTED

### Fixed autoclicker and periodic reopening

- URL: https://www.reddit.com/r/JustPlay/comments/13ek21b/automated_gaming_on_justplay/
- WHAT_IT_DOES: May 2023 comments discuss fixed-point autoclickers and Automate
  reopening the app periodically; also describe unwanted store redirects and
  difficulty stopping automation.
- WHAT_WE_CAN_REUSE: motivation for bounded stall monitoring and a separately
  reviewed relaunch *proposal*.
- LIMITATIONS: no reliable geometry, collision avoidance or verified source.
- SAFETY/RISK: blind timed clicks can hit advertisements/store controls.
- DECISION: REJECT autoclicking; REFERENCE_ONLY recovery motivation.

### Reopening after ads

- URL: https://www.reddit.com/r/JustPlay/comments/1dylc40/how_to_automate_treasure_master/
- WHAT_IT_DOES: July 2024 discussion reports reopening the app to get out of ads,
  with possible return to a menu rather than gameplay.
- WHAT_WE_CAN_REUSE: last-resort relaunch candidate after waiting/reinspection.
- LIMITATIONS: community workaround, no guarantee of state preservation.
- SAFETY/RISK: losing progress or relaunching during an unrelated foreground app.
- DECISION: REFERENCE_ONLY; no automatic relaunch in this milestone.

### Image-based close suggestions

- URL: https://www.reddit.com/r/JustPlay/comments/1h5pmr2/how_to_automate_treasure_masters/
- WHAT_IT_DOES: December 2024 comments propose image recognition of X controls;
  reward/account claims vary and are not verified.
- WHAT_WE_CAN_REUSE: prioritize UI hierarchy evidence and treat visual close
  recognition as a future separately tested fallback.
- LIMITATIONS: no audited Android implementation or broad ad coverage supplied.
- SAFETY/RISK: an X can be decoration, navigation, a deceptive ad control or have
  ambiguous bounds. It is never trusted merely by its text.
- DECISION: REFERENCE_ONLY; reject arbitrary X clicking and earnings claims.

## Rejected search results

- https://github.com/prawl/FFTTreasureMaster/blob/main/docs/TREASURE_MASTER_PLAN.md
  concerns Final Fantasy Tactics; name match does not make it reusable here.
- https://blink.new/p/treasure-agent-bot-s83bznkd is a generated marketing/project
  page, not a verified compatible Android implementation. REJECT as source code.

Next research validation requires controlled read-only live observations of
actual package/activity/hierarchy and ad transitions. Community reports cannot
authorize firing, Continue/Restart actions, BACK or closing ads.
