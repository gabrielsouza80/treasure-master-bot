"""Read-only benchmark/observer loop shared by the two safe CLIs."""
from collections import Counter, deque
from pathlib import Path
import re
from time import monotonic, perf_counter, process_time

from src.android.live_buffer import LiveFrameBuffer
from src.runtime.bot_runtime import BotRuntime, JsonlTelemetry
from src.runtime.capture_metrics import CaptureMetrics, distribution
from src.states.runtime_state import RuntimeState


def safe_identifier(value):
    return value if value and re.fullmatch(r'\.?[A-Za-z_][A-Za-z0-9_.$]{0,199}',value) else None


def bounds_category(bounds, resolution):
    if not bounds or not resolution:
        return 'unknown'
    w,h=resolution
    x=(bounds[0]+bounds[2])/2/w
    y=(bounds[1]+bounds[3])/2/h
    return ('top' if y<.33 else 'bottom' if y>.67 else 'middle')+'-'+('left' if x<.33 else 'right' if x>.67 else 'center')


class EvidenceCatalogue:
    """Candidate signatures, never an automatic AD/menu classifier."""
    def __init__(self):
        self.entries={}
        self._last_observed=None
        self._active_key=None

    def observe(self, snapshot, resolution):
        if snapshot is None or snapshot.error or snapshot.observed_at==self._last_observed:
            return
        self._last_observed=snapshot.observed_at
        kinds={'close':snapshot.find_close_candidates(),
               'continue':snapshot.find_continue_candidates(),
               'restart':snapshot.find_restart_candidates()}
        package,activity=safe_identifier(snapshot.app.package),safe_identifier(snapshot.app.activity)
        # Candidate count alone loses changes in enabled evidence and location.
        key=(package,activity,tuple((k,len(v),sum(c.actionable_evidence for c in v),
                                    sum(c.ambiguous for c in v),
                                    tuple(sorted({bounds_category(c.node.bounds,resolution) for c in v})))
                                   for k,v in kinds.items()))
        if key not in self.entries:
            if len(self.entries)>=100:
                return
            self.entries[key]=dict(package=package,activity=activity,node_count=len(snapshot.nodes),
                                   node_count_min=len(snapshot.nodes),node_count_max=len(snapshot.nodes),
                                   observation_episodes=0,probes=0,stage='OBSERVED_ONCE',
                                   unambiguous_close=sum(c.actionable_evidence for c in kinds['close']),
                                   ambiguous_x=sum(c.ambiguous for c in kinds['close']),
                                   continue_candidates=len(kinds['continue']),restart_candidates=len(kinds['restart']),
                                   close_bounds=sorted({bounds_category(c.node.bounds,resolution) for c in kinds['close']}),
                                   verified=False)
        entry=self.entries[key]
        entry['node_count_min']=min(entry['node_count_min'],len(snapshot.nodes))
        entry['node_count_max']=max(entry['node_count_max'],len(snapshot.nodes))
        if key!=self._active_key:
            entry['observation_episodes']+=1
            if entry['observation_episodes']>1:
                entry['stage']='OBSERVED_REPEATEDLY'
        entry['probes']+=1
        self._active_key=key


def observe(source, *, duration=60., ui=None, vision=False, expected_package=None,
            expected_game_activity=None, log=None, clock=monotonic):
    if not 1 <= duration <= 600:
        raise ValueError('Observation duration must be 1..600 seconds')
    sink=JsonlTelemetry(Path(log),limit=40000) if log else None
    runtime=BotRuntime(expected_package=expected_package,observation_only=True,clock=clock,
                       expected_game_activity=expected_game_activity)
    metrics=CaptureMetrics()
    catalogue=EvidenceCatalogue()
    states,knives,runtime_states=Counter(),Counter(),Counter()
    watchdog_statuses=Counter()
    durations,completion_ages,ui_durations=deque(maxlen=12000),deque(maxlen=12000),deque(maxlen=1000)
    received=targets=uncertain=0
    resolution=None
    last_ui_probe=-1
    last_received=None
    stopped_reason='duration_complete'
    started,cpu_started=clock(),process_time()
    try:
        while clock()-started<duration:
            packet=source.read()
            snapshot=ui.read() if ui else None
            if ui and ui.probes!=last_ui_probe and ui.probes:
                ui_durations.append(ui.last_duration)
                last_ui_probe=ui.probes
            catalogue.observe(snapshot,resolution)
            if packet is None:
                if source.exhausted:
                    stopped_reason=getattr(getattr(source,'buffer',None),'error',None) or 'source_eof'
                    runtime.state=RuntimeState.STALLED
                    runtime.stabilizer.reset()
                    runtime.tracker.reset()
                    watchdog_statuses['STOP']+=1
                    if sink:
                        sink.write(dict(host_timestamp=clock(),capture_state='DISCONNECTED',
                                        runtime_state='STALLED',watchdog_status='STOP',
                                        watchdog_reason=stopped_reason,planned_action='STOP'))
                    break
                verdict=runtime.check_without_frame()
                watchdog_statuses[verdict.recommendation]+=1
                if sink:
                    sink.write(dict(host_timestamp=clock(),capture_state='NO_FRESH_FRAME',
                                    runtime_state=runtime.state.value,watchdog_status=verdict.recommendation,
                                    watchdog_reason=verdict.reason,planned_action=verdict.recommendation))
                continue  # ScrcpyFrameSource.read blocks on a condition, no spin.
            now=clock()
            metrics.produced(packet.timestamp,packet.received_at)
            metrics.consumed(packet.received_at,now)
            resolution=(packet.frame.shape[1],packet.frame.shape[0])
            record={}
            if vision:
                before=perf_counter()
                record=runtime.process(packet,snapshot)
                durations.append(perf_counter()-before)
                states[record['game_state']]+=1
                watchdog_statuses[record['watchdog_status']]+=1
                runtime_states[record['runtime_state']]+=1
                targets+=record['target_valid']
                if record['game_state']=='PLAYING':
                    knives[record['knife_count']]+=1
                    uncertain+=record['knife_count_uncertain']
                if record['planned_action'] not in ('WAIT','RECHECK_UI','STOP'):
                    raise RuntimeError('Observer emitted a non-observation action')
            else:
                runtime.watchdog.observe(packet.sequence_number,packet.timestamp,now,
                                         RuntimeState.UNKNOWN,False)
            completed=clock()
            completion_ages.append(completed-packet.received_at)
            record.update(host_timestamp=now,source_timestamp=packet.timestamp,sequence=packet.sequence_number,
                          interframe_delta_ms=(packet.received_at-last_received)*1000 if last_received is not None else None,
                          frame_age_ms=(now-packet.received_at)*1000,
                          observation_age_ms=(completed-packet.received_at)*1000,
                          capture_state='OBSERVING',
                          ui_snapshot_age_ms=(now-snapshot.observed_at)*1000 if snapshot else None,
                          ui_error=snapshot.error if snapshot else 'no_snapshot',
                          current_android_package=safe_identifier(snapshot.app.package) if snapshot else None,
                          current_android_activity=safe_identifier(snapshot.app.activity) if snapshot else None)
            if sink:
                sink.write(record)
            last_received=packet.received_at
            received+=1
    finally:
        finished=clock()
        try:
            if sink:
                sink.close()
        finally:
            try:
                source.close()
            finally:
                if ui:
                    ui.close()
    elapsed=finished-started
    capture=(source.buffer.metrics if hasattr(source,'buffer') else metrics)
    # Consumer age samples live in the consumer, producer samples include replaced frames.
    capture.ages=metrics.ages
    capture.stale=metrics.stale
    summary=capture.summary(elapsed,received)
    summary.update(resolution=resolution,stopped_reason=stopped_reason,
                   dropped_or_replaced=max(0,source.buffer.frames-received) if hasattr(source,'buffer') else 0,
                   replaced_frames=getattr(getattr(source,'buffer',None),'replaced',0),
                   duplicate_pts=getattr(getattr(source,'buffer',None),'duplicate_pts',0),
                   out_of_order_pts=getattr(getattr(source,'buffer',None),'out_of_order_pts',0),
                   consumed_frames=received,own_process_cpu_percent=100*(process_time()-cpu_started)/elapsed,
                   watchdog_status_counts=dict(watchdog_statuses),
                   vision=dict(frames=received if vision else 0,state_counts=dict(states),runtime_state_counts=dict(runtime_states),
                               target_detection_rate=targets/received if received and vision else None,
                               knife_count_histogram=dict(knives),
                               count_uncertain_rate=uncertain/states['PLAYING'] if states['PLAYING'] else None,
                               processing_fps=len(durations)/sum(durations) if durations else None,
                               frame_time=distribution(durations),observation_age=distribution(completion_ages)),
                   ui=dict(probes=ui.probes if ui else 0,success=ui.success if ui else 0,failure=ui.failure if ui else 0,
                           mean_ms=1000*ui.total_seconds/ui.probes if ui and ui.probes else None,
                           p95_ms=ui.duration_stats['p95_ms'] if ui else None,
                           duration_sample=ui.duration_stats if ui else distribution([])),
                   evidence_catalogue=list(catalogue.entries.values()),
                   android_input_enabled=False,scrcpy_control_enabled=False,
                   end_to_end_latency_ms=None)
    summary['telemetry_records']=sink.count if sink else 0
    summary['telemetry_truncated']=bool(sink and sink.count>=sink.limit)
    return summary
