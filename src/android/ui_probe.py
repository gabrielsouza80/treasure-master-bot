"""Read-only adapter for an already initialized uiautomator2 client.

Never connects/installs/starts a server itself. UI labels are evidence, not
permission to click. Unknown languages remain unknown unless configured.
"""
from dataclasses import dataclass
import hashlib
import re
from time import monotonic
import xml.etree.ElementTree as ET

from src.android.device import CurrentApp


@dataclass(frozen=True)
class UiNode:
    text: str
    description: str
    resource_id: str
    package: str
    bounds: tuple[int, int, int, int] | None
    clickable: bool
    enabled: bool
    visible: bool


@dataclass(frozen=True)
class UiCandidate:
    kind: str
    node: UiNode
    ambiguous: bool

    @property
    def actionable_evidence(self):
        return bool(not self.ambiguous and self.node.bounds and self.node.clickable
                    and self.node.enabled and self.node.visible)


def parse_ui(xml):
    if len(xml) > 2_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Unsupported UI XML')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError('Malformed UI XML') from exc
    nodes = []
    def walk(element, enabled=True, visible=True):
        # Missing node evidence is unknown, hence disabled for action evidence.
        default_enabled = 'false' if element.tag == 'node' else 'true'
        enabled = enabled and element.attrib.get('enabled', default_enabled) == 'true'
        visible = visible and element.attrib.get('visible-to-user', 'true') == 'true'
        if element.tag == 'node':
            yield element, enabled, visible
        for child in element:
            yield from walk(child, enabled, visible)

    for element, enabled, visible in walk(root):
        a = element.attrib
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', a.get('bounds', ''))
        bounds = tuple(map(int, match.groups())) if match else None
        if bounds and (bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
            bounds = None
        nodes.append(UiNode(a.get('text',''), a.get('content-desc',''), a.get('resource-id',''),
                            a.get('package',''), bounds, a.get('clickable')=='true',
                            enabled, visible))
    return tuple(nodes)


_WORDS = {
    'CLOSE': {'close','done','skip','no thanks','fechar','concluir','pular','saltar','cerrar','fermer','schließen','关闭','跳过'},
    'CONTINUE': {'continue','continuar','continuer','weiter','继续'},
    'RESTART': {'restart','reiniciar','recommencer','neustart','重新开始'},
}
_IDS = {'CLOSE': {'close','close_button','button_close','ad_close','skip_button'},
        'CONTINUE': {'continue','continue_button'}, 'RESTART': {'restart','restart_button'}}


def find_candidates(nodes, kind, *, extra_words=()):
    words = _WORDS[kind] | {w.strip().casefold() for w in extra_words}
    result = []
    for node in nodes:
        text, desc = node.text.strip().casefold(), node.description.strip().casefold()
        symbol = text in {'x','×'} or desc in {'x','×'}
        rid = node.resource_id.rsplit('/',1)[-1].casefold()
        if text in words or desc in words or rid in _IDS[kind] or (kind=='CLOSE' and symbol):
            result.append(UiCandidate(kind,node,symbol))
    return tuple(result)


@dataclass(frozen=True)
class UiSnapshot:
    app: CurrentApp
    nodes: tuple[UiNode, ...]
    observed_at: float
    error: str | None = None

    @property
    def signature(self):
        # Never expose raw hierarchy/text in runtime logs.
        data = repr((self.app, self.nodes)).encode('utf-8')
        return hashlib.sha256(data).hexdigest()

    def find_close_candidates(self):
        return find_candidates(self.nodes,'CLOSE')

    def find_continue_candidates(self):
        return find_candidates(self.nodes,'CONTINUE')

    def find_restart_candidates(self):
        return find_candidates(self.nodes,'RESTART')


class UiProbe:
    def __init__(self, client, *, clock=monotonic):
        self._client, self._clock = client, clock

    def get_current_app(self):
        data = self._client.app_current()
        return CurrentApp(data.get('package'), data.get('activity'))

    def dump_ui(self):
        return self._client.dump_hierarchy(compressed=False, pretty=False, max_depth=50)

    def read(self):
        started = self._clock()
        try:
            app = self.get_current_app()
            nodes = parse_ui(self.dump_ui())
            # Package/activity changes mid-query invalidate the combined evidence.
            if self.get_current_app() != app:
                return UiSnapshot(CurrentApp(),(),started,'app_changed_during_probe')
            return UiSnapshot(app,nodes,started)
        except Exception:
            return UiSnapshot(CurrentApp(),(),started,'ui_probe_failed')
