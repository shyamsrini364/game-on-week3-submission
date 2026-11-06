"""Dedicated Python entry point; never re-import the serverless handler as main.

The payload and pipe are private parent/child IPC, not an HTTP deserialisation API.
The parent retains all graph/tool deadlines and forcibly reaps the child.
"""
import os
import pickle
import queue
import subprocess
import sys
import tempfile
import threading


class EventReceiver:
    def __init__(self):
        self.events = queue.Queue()
        self.pending = None

    def read(self, stream):
        try:
            while True:
                self.events.put(pickle.load(stream))
        except (EOFError, OSError, ValueError, pickle.UnpicklingError):
            self.events.put(('failure', 'worker_error'))

    def poll(self, timeout):
        if self.pending is None:
            try:
                self.pending = self.events.get(timeout=max(0, timeout))
            except queue.Empty:
                return False
        return True

    def recv(self):
        result, self.pending = self.pending, None
        return result

    def close(self):
        pass


class UnusedSender:
    def close(self):
        pass


class WorkerProcess:
    def __init__(self, receiver, args):
        self.receiver, self.args = receiver, args
        self.process = None
        self.reader = None

    @property
    def pid(self):
        return self.process.pid if self.process else None

    def start(self):
        # No database/Auth/model credentials or external tracing settings cross
        # into the worker. Only interpreter/import and locale settings survive.
        allowed = ('PATH', 'PYTHONPATH', 'PYTHONHOME', 'LD_LIBRARY_PATH',
                   'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'TZ')
        env = {k: os.environ[k] for k in allowed if k in os.environ}
        # Serverless bootstrap code may extend sys.path for vendored packages
        # without exporting PYTHONPATH. Preserve those actual import locations.
        env['PYTHONPATH'] = os.pathsep.join(str(p) for p in sys.path if p)
        env.update(LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')
        # A file-backed stdin avoids blocking the deadline-monitoring parent
        # while a cold interpreter imports dependencies before reading input.
        with tempfile.TemporaryFile() as payload:
            pickle.dump(self.args, payload, protocol=pickle.HIGHEST_PROTOCOL)
            payload.seek(0)
            self.process = subprocess.Popen(
                [sys.executable, '-m', 'gameon.erne.subprocess_worker'],
                stdin=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self.receiver.read,
                                       args=(self.process.stdout,), daemon=True)
        self.reader.start()

    def is_alive(self):
        return self.process.poll() is None

    def terminate(self):
        self.process.terminate()

    def kill(self):
        self.process.kill()

    def join(self, timeout):
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def close(self):
        if self.reader:
            self.reader.join(timeout=0.5)
        self.process.stdout.close()


def prepare_worker(args):
    receiver = EventReceiver()
    return receiver, UnusedSender(), WorkerProcess(receiver, args)


def main():
    # Capture the binary pipe before the existing worker silences stdout.
    stream = sys.stdout.buffer

    class Sender:
        def send(self, event):
            pickle.dump(event, stream, protocol=pickle.HIGHEST_PROTOCOL)
            stream.flush()

        def close(self):
            stream.flush()

    args = pickle.load(sys.stdin.buffer)
    from .graph import _worker
    _worker(Sender(), *args)


if __name__ == '__main__':
    main()
