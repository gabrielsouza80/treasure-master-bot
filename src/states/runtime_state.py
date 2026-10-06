"""Runtime context never broadens the existing visual PLAYING classifier."""
from enum import Enum
from src.states.game_state import GameState


class RuntimeState(str, Enum):
    PLAYING = 'PLAYING'
    UNKNOWN = 'UNKNOWN'
    GAME_OVER = 'GAME_OVER'
    CONTINUE = 'CONTINUE'
    ADVERTISEMENT = 'ADVERTISEMENT'
    RETURNING_TO_GAME = 'RETURNING_TO_GAME'
    STALLED = 'STALLED'


def infer_runtime_state(game_state, snapshot, *, expected_package=None,
                        verified_ad_activities=(), previous=RuntimeState.UNKNOWN):
    # Offline vision is usable without pretending a phone foreground was checked.
    if expected_package is None:
        return RuntimeState.PLAYING if game_state==GameState.PLAYING else RuntimeState.UNKNOWN
    if snapshot is None or snapshot.error or snapshot.app.package != expected_package:
        return RuntimeState.UNKNOWN
    ad_activity = snapshot.app.activity in verified_ad_activities
    if game_state==GameState.PLAYING:
        return RuntimeState.UNKNOWN if ad_activity else RuntimeState.PLAYING
    if ad_activity:
        return RuntimeState.ADVERTISEMENT
    if previous in (RuntimeState.ADVERTISEMENT,RuntimeState.RETURNING_TO_GAME):
        return RuntimeState.RETURNING_TO_GAME
    # Text 'Continue'/'Restart' alone also occurs in ads. Reserved states need
    # separately verified game-specific UI signatures; don't invent them here.
    return RuntimeState.UNKNOWN
