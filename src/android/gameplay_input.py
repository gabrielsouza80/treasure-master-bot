"""Only gameplay taps; two explicit switches and a fresh runtime permit."""
from dataclasses import dataclass
from time import monotonic
import subprocess
from src.android.input_controller import scale_point


@dataclass(frozen=True)
class TapPermit:
    allowed: bool
    created_at: float
    sequence: int
    reason: str


class GameplayInput:
    def __init__(self,serial,adb,*,enable_input=False,autoplay=False,max_shots=10,
                 clock=monotonic,popen=subprocess.Popen,foreground=None):
        if bool(enable_input)!=bool(autoplay): raise ValueError('Both input flags required')
        if not serial or serial.startswith('-') or not 1<=max_shots<=20:
            raise ValueError('Explicit device and bounded shot limit required')
        self.enabled=bool(enable_input and autoplay)
        self.serial,self.adb=serial,str(adb)
        self.max_shots=max_shots
        self.clock,self.popen=clock,popen
        self.sent=0
        self.process=None
        self.command_at=None
        self.last_sequence=-1
        self.failed=False
        self.outstanding=False
        self.foreground=foreground

    def fire(self,permit,resolution,point=(.5,.78)):
        now=self.clock()
        if (not permit.allowed or not 0<=now-permit.created_at<=.08 or permit.sequence<=self.last_sequence
                or permit.reason!='SAFE' or self.failed or self.outstanding
                or self.process is not None or self.sent>=self.max_shots):
            return 'BLOCKED'
        if not (.47<=point[0]<=.53 and .74<=point[1]<=.81): return 'BLOCKED'
        x,y=scale_point(point,*resolution)
        if not self.enabled: return 'DRY_RUN'
        if self.foreground is None or not self.foreground():return 'BLOCKED'
        if not 0<=self.clock()-permit.created_at<=.08:return 'BLOCKED'
        # Fixed arguments only. No generic shell, BACK, app/menu/ad operation.
        self.command_at=self.clock()
        self.last_sequence=permit.sequence
        try:
            self.process=self.popen([self.adb,'-s',self.serial,'shell','input','tap',str(x),str(y)],
                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError:
            self.failed=True
            return 'INPUT_FAILED'
        self.sent+=1
        self.outstanding=True
        return 'SENT'

    def acknowledge_confirmed(self,shot_number):
        if shot_number==self.sent and self.process is None and not self.failed:
            self.outstanding=False

    def poll(self):
        if self.process is None: return 'FAILED' if self.failed else 'IDLE'
        code=self.process.poll()
        if code is not None:
            self.process=None
            self.failed=code!=0
            return 'FAILED' if self.failed else 'DELIVERED'
        if self.clock()-self.command_at>.5:
            self.process.kill()  # Host ADB child; delivery outcome unknown: latch stop.
            self.process.wait(timeout=1)
            self.process=None
            self.failed=True
            return 'FAILED'
        return 'PENDING'

    def close(self):
        if self.process is not None:
            self.process.kill(); self.process.wait(timeout=1)
            self.process=None
