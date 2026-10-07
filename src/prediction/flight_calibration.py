"""Observed command-to-impact horizon, including capture delay; local only."""
from dataclasses import dataclass,field
import json
from math import isfinite
from pathlib import Path
import time
import numpy as np


@dataclass
class FlightCalibration:
    samples: list = field(default_factory=list)
    measured_at: float = 0.

    def add(self,command,movement,impact,confirmed=None):
        response,flight,total=movement-command,impact-movement,impact-command
        if (not all(isfinite(v) for v in (response,flight,total))
                or not .005<=response<=.45 or not .01<=flight<=.35 or not .02<=total<=.65):
            return False
        if confirmed is not None and (not isfinite(confirmed) or not impact<=confirmed<=command+1.2):
            return False
        sample=dict(response_s=response,flight_s=flight,total_s=total)
        if confirmed is not None:sample['confirmation_s']=confirmed-command
        self.samples.append(sample)
        self.samples=self.samples[-8:]
        self.measured_at=time.time()
        return True

    @property
    def has_recent_samples(self):
        return (bool(self.samples) and 0<=time.time()-self.measured_at<=600
                and self.spread_s<=.10)

    @property
    def valid(self):return len(self.samples)>=3 and self.has_recent_samples

    @property
    def median_s(self): return float(np.median([s['total_s'] for s in self.samples])) if self.samples else None
    @property
    def spread_s(self):
        values=[s['total_s'] for s in self.samples]
        return max(values)-min(values) if values else float('inf')
    @property
    def timing_error_s(self): return max(.10,self.spread_s+.05)

    def summary(self):
        def stat(key,percentile):
            return float(np.percentile([s[key] for s in self.samples],percentile)*1000) if self.samples else None
        return dict(valid=self.valid,samples=len(self.samples),command_to_impact_median_ms=stat('total_s',50),
                    command_to_impact_p95_ms=stat('total_s',95),input_response_median_ms=stat('response_s',50),
                    flight_only_median_ms=stat('flight_s',50),timing_error_s=self.timing_error_s if self.samples else None,
                    spread_ms=self.spread_s*1000 if self.samples else None,
                    meaning='host command to visually observed impact; includes capture delay')

    def save(self,path,resolution):
        with Path(path).open('x',encoding='utf-8') as output:
            json.dump(dict(schema=1,resolution=list(resolution),measured_at=self.measured_at,
                           samples=self.samples),output)

    @classmethod
    def load(cls,path,resolution):
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('schema')!=1 or data.get('resolution')!=list(resolution): raise ValueError('Calibration geometry mismatch')
        samples=data['samples']
        if not 0<len(samples)<=8: raise ValueError('Invalid calibration samples')
        result=cls()
        for s in samples:
            if not result.add(0.,s['response_s'],s['total_s'],s.get('confirmation_s')): raise ValueError('Invalid calibration sample')
            if abs(s['flight_s']-(s['total_s']-s['response_s']))>.001: raise ValueError('Inconsistent calibration')
        result.measured_at=float(data['measured_at'])
        return result
