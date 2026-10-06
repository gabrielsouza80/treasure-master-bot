"""Explicit-session, read-only ADB queries. No input or app-launch commands."""
from dataclasses import dataclass
import re
import subprocess


@dataclass(frozen=True)
class CurrentApp:
    package: str | None = None
    activity: str | None = None


def parse_current_app(output, *, display_id=0):
    # Prefer current focus regardless of dump order. mFocusedApp may be stale.
    lines = output.splitlines()
    # scrcpy creates a virtual display whose null focus may precede display 0.
    # Scope to the captured physical display, never pick any non-null focus.
    displays = [(i,int(match.group(1))) for i,line in enumerate(lines)
                if (match := re.match(r'\s*Display: mDisplayId=(\d+)',line))]
    top = re.search(r'mTopFocusedDisplayId=(\d+)',output)
    if top and int(top.group(1)) != display_id:
        return CurrentApp()
    if displays:
        selected = [(i,displays[n+1][0] if n+1<len(displays) else len(lines))
                    for n,(i,d) in enumerate(displays) if d==display_id]
        if len(selected)!=1:
            return CurrentApp()
        start,end=selected[0]
        lines=lines[start:end]
    for marker in ('mCurrentFocus=', 'topResumedActivity=', 'mResumedActivity:'):
        records = [line for line in lines if marker in line]
        if records:
            line = records[0]
            match = re.search(r'([\w.]+)/([\w.$]+)', line)
            return CurrentApp(*match.groups()) if match else CurrentApp()
    return CurrentApp()


class AdbSession:
    def __init__(self, serial, *, adb='adb', timeout=2., runner=subprocess.run):
        if not serial or serial.startswith('-') or not 0 < timeout <= 10:
            raise ValueError('Explicit device serial and bounded timeout required')
        self.serial, self.adb, self.timeout, self._runner = serial, str(adb), timeout, runner

    def _query(self, arguments):
        try:
            result = self._runner([self.adb, '-s', self.serial, *arguments],
                                  capture_output=True, timeout=self.timeout, check=True,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            # ADB stderr/serial can contain endpoints; do not put it in telemetry.
            raise RuntimeError('Read-only ADB query failed') from exc

    def screenshot_png(self):
        return self._query(['exec-out', 'screencap', '-p'])

    def get_current_app(self):
        # Android 16 omits focus fields from the 'windows' subcommand.
        return parse_current_app(self._query(['shell', 'dumpsys', 'window']).decode('utf-8', errors='replace'))

    def get_resolution(self):
        output = self._query(['shell', 'wm', 'size']).decode('utf-8', errors='replace')
        sizes = re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)', output)
        if not sizes:
            raise RuntimeError('Device resolution unavailable')
        return tuple(map(int, sizes[-1]))
