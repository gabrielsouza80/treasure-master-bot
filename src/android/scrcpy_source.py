"""Video-only client for the official bundled scrcpy 4.1 server.

Format verified against v4.1 Streamer.java/DesktopConnection.java. No control
channel, socket writes, old server, UI window, cropping or synthetic PTS.
"""
from fractions import Fraction
from collections import OrderedDict
from pathlib import Path
from threading import Event, Thread
from time import monotonic
import socket
import struct
import subprocess
import uuid

from src.android.live_buffer import LiveFrameBuffer

VERSION = '4.1'
MAX_PAYLOAD = 16 * 1024 * 1024
SESSION, CONFIG = 1 << 63, 1 << 62
PTS_MASK = (1 << 61) - 1


def recv_exact(connection, size, stop=None):
    data = bytearray()
    while len(data) < size:
        try:
            chunk = connection.recv(size - len(data))
        except socket.timeout:
            if stop is None:
                raise
            if stop.is_set():
                raise EOFError('capture_stopped') from None
            continue  # Idle screen: retain partial packet; never invent a frame.
        if not chunk:
            raise EOFError('video_eof')
        data.extend(chunk)
    return bytes(data)


def read_header(connection, stop=None):
    header = recv_exact(connection,12,stop)
    word, value = struct.unpack('>QI',header)
    if word & SESSION:
        flags,width,height = struct.unpack('>III',header)
        if not (0 < width <= 8192 and 0 < height <= 8192 and width*height <= 16_777_216):
            raise ValueError('invalid_session_dimensions')
        return ('SESSION',width,height)
    if not 0 < value <= MAX_PAYLOAD:
        raise ValueError('invalid_packet_length')
    return ('CONFIG' if word & CONFIG else 'FRAME',word & PTS_MASK,value)


def server_arguments(remote_path, scid, *, max_fps=60):
    if not 1 <= max_fps <= 120:
        raise ValueError('Invalid capture FPS limit')
    return ['shell',f'CLASSPATH={remote_path}','app_process','/',
            'com.genymobile.scrcpy.Server',VERSION,f'scid={scid:08x}',
            'tunnel_forward=true','video=true','audio=false','control=false',
            'power_on=false','cleanup=false','send_device_meta=false',
            'send_dummy_byte=false','send_stream_meta=true','send_frame_meta=true',
            'video_codec=h264','display_id=0','max_size=0',f'max_fps={max_fps}','video_bit_rate=16000000']


class ScrcpyFrameSource:
    def __init__(self, serial, scrcpy, *, adb=None, max_fps=60):
        import av  # Optional dependency; offline imports remain lightweight.
        if not serial or serial.startswith('-'):
            raise ValueError('Explicit selected device required')
        self._av = av
        self.scrcpy = Path(scrcpy).resolve()
        self.adb = str(adb or self.scrcpy.with_name('adb.exe'))
        self.serial = serial
        self.buffer = LiveFrameBuffer()
        self._stop = Event()
        self._socket = self._process = self._thread = None
        self._port = None
        self._remote = f'/data/local/tmp/treasure-observer-{uuid.uuid4().hex}.jar'
        self.dimensions = None
        self._scid = int(uuid.uuid4().hex[:7],16)
        self._last_media_pts = None
        self._config = b''
        self._codec = None
        self._max_fps = max_fps
        self._arrivals = OrderedDict()
        self._uploaded = False

    @property
    def exhausted(self):
        return self.buffer.exhausted

    def _query(self, args):
        return subprocess.run([self.adb,'-s',self.serial,*args],check=True,
                              capture_output=True,timeout=5,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout

    def start(self):
        if self._thread is not None or self.buffer.exhausted:
            raise RuntimeError('Capture source cannot be restarted')
        try:
            version = subprocess.run([str(self.scrcpy),'--version'],capture_output=True,
                                     check=True,timeout=5,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.decode()
            if version.splitlines()[0].split()[:2] != ['scrcpy',VERSION]:
                raise RuntimeError('Only matching official scrcpy 4.1 is supported')
            server = self.scrcpy.with_name('scrcpy-server')
            if not server.is_file():
                raise RuntimeError('Bundled official server missing')
            self._query(['push',str(server),self._remote])
            self._uploaded = True
            self._port = int(self._query(['forward','tcp:0',f'localabstract:scrcpy_{self._scid:08x}']).strip())
            args = server_arguments(self._remote,self._scid,max_fps=self._max_fps)
            self._process = subprocess.Popen([self.adb,'-s',self.serial,*args],
                                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            # ADB forward may accept before the remote helper is ready. Retry
            # only connections; never restart an app or alter device state.
            deadline = monotonic()+5
            while True:
                connection = None
                try:
                    connection = socket.create_connection(('localhost',self._port),timeout=.5)
                    connection.settimeout(2.)
                    codec_id = recv_exact(connection,4)
                    if codec_id != b'h264':
                        connection.close()
                        raise RuntimeError('Unsupported scrcpy video codec')
                    self._socket = connection
                    break
                except (OSError,EOFError):
                    if connection:
                        connection.close()
                    if monotonic() >= deadline or self._process.poll() is not None:
                        raise RuntimeError('Read-only capture connection failed') from None
                    self._stop.wait(.05)
            self._thread = Thread(target=self._decode,name='scrcpy-video-readonly',daemon=True)
            self._thread.start()
            return self
        except Exception:
            self.close()
            raise RuntimeError('Read-only scrcpy startup failed') from None

    def _new_codec(self):
        self._codec = self._av.CodecContext.create('h264','r')
        self._codec.thread_count = 1
        self._codec.extradata = self._config

    def _decode(self):
        error = None
        try:
            while not self._stop.is_set():
                kind, first, second = read_header(self._socket,self._stop)
                if kind == 'SESSION':
                    self.dimensions = (first,second)
                    self._config = b''
                    self._arrivals.clear()
                    self._new_codec()
                    continue
                payload = recv_exact(self._socket,second,self._stop)
                received_at = monotonic()  # Packet arrival, before decoding/BGR conversion.
                if kind == 'CONFIG':
                    self._config = payload
                    if self.dimensions is None:
                        raise ValueError('missing_session')
                    self._new_codec()
                    continue
                pts = first / 1_000_000
                valid_pts = True
                if self._last_media_pts is not None and pts <= self._last_media_pts:
                    # Decoder still sees packets to retain reference state; invalid
                    # presentation timestamps are not published as fresh frames.
                    if pts == self._last_media_pts:
                        self.buffer.duplicate_pts += 1
                    else:
                        self.buffer.out_of_order_pts += 1
                    valid_pts = False
                else:
                    self._last_media_pts = pts
                if valid_pts:
                    self._arrivals[first] = received_at
                    if len(self._arrivals) > 32:
                        raise ValueError('decoder_backlog_limit')
                if self._codec is None:
                    raise ValueError('missing_codec_session')
                packet = self._av.Packet(payload)
                packet.pts = packet.dts = first
                packet.time_base = Fraction(1,1_000_000)
                for frame in self._codec.decode(packet):
                    if frame.pts is None or frame.time_base is None:
                        raise ValueError('missing_decoded_pts')
                    if (frame.width,frame.height) != self.dimensions:
                        raise ValueError('decoded_dimension_mismatch')
                    frame_key = int(frame.pts*frame.time_base*1_000_000)
                    arrival = self._arrivals.pop(frame_key,None)
                    if arrival is None:
                        continue
                    self.buffer.publish(frame.to_ndarray(format='bgr24'),
                                        float(frame.pts*frame.time_base),arrival)
        except EOFError:
            error = 'source_disconnected'
        except socket.timeout:
            error = 'source_timeout'
        except Exception:
            error = 'capture_decode_failed'
        finally:
            self.buffer.finish(None if self._stop.is_set() else error)

    def read(self, timeout=.2):
        return self.buffer.read(timeout)

    def close(self):
        self._stop.set()
        shutdown_failed = False
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                self.buffer.finish('decoder_shutdown_timeout')
                shutdown_failed = True
        if self._process:
            if self._process.poll() is None:
                self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        if self._port is not None:
            try:
                self._query(['forward','--remove',f'tcp:{self._port}'])
            except (OSError,subprocess.SubprocessError):
                pass
        # Delete only our unique capture helper; never apps or user files.
        if self._uploaded:
            try:
                self._query(['shell','rm',self._remote])
            except (OSError,subprocess.SubprocessError):
                pass
        self.buffer.close()
        self._port = None
        self._uploaded = False
        if shutdown_failed:
            raise RuntimeError('Decoder did not shut down')
