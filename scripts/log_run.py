#!/usr/bin/env python3
"""Run argv without a shell; append a bounded record only after completion."""
import datetime as dt
import fcntl
import os
import re
import selectors
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
MAX_CAPTURE = 2 * 1024 * 1024


def main():
    arguments = sys.argv[1:]
    streaming = bool(arguments and arguments[0] == '--stream')
    if streaming:
        arguments = arguments[1:]
    if len(arguments) < 2:
        print('Usage: ncs2_log_run.sh [--stream] LABEL COMMAND [ARG ...]', file=sys.stderr)
        return 64
    label, command = arguments[0], arguments[1:]
    log = Path(os.environ.get('NCS2_LOG_FILE', ROOT / 'ncs2_read_attempts.md'))
    start = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    started = time.monotonic()
    # Nonblocking lock refuses concurrent/nested log writers rather than interleaving.
    with log.open('a', encoding='utf-8') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Experiment log is busy; do not nest loggers or run concurrent writers.', file=sys.stderr)
            return 73
        chunks = []
        captured = total = 0
        try:
            with subprocess.Popen(command, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE if streaming else subprocess.STDOUT) as child:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ, sys.stdout)
                    if streaming:
                        selector.register(child.stderr, selectors.EVENT_READ, sys.stderr)
                    while selector.get_map():
                        for key, _ in selector.select():
                            chunk = key.fileobj.read1(65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            if streaming:
                                # Keep protocol stdout clean; record both streams after exit.
                                key.data.buffer.write(chunk)
                                key.data.buffer.flush()
                            total += len(chunk)
                            keep = chunk[:max(0, MAX_CAPTURE - captured)]
                            if keep:
                                chunks.append(keep)
                            captured += len(keep)
                code = child.wait()
        except OSError as error:
            chunks = [str(error).encode()]
            code = 127
            if streaming:
                print(str(error), file=sys.stderr)
        output = b''.join(chunks).decode('utf-8', errors='replace')
        if total > MAX_CAPTURE:
            output += f'\n[Output truncated: {total} bytes; retained first {MAX_CAPTURE}.]\n'
        # Never append before the child exits: even `cat` of this log stays finite.
        fence = '`' * max(3, max((len(part) for part in re.findall(r'`+', output)), default=0) + 1)
        elapsed = time.monotonic() - started
        stream.write(f'\n## {label}\n\n- 时间：{start}\n- 工作目录：`{Path.cwd()}`\n')
        stream.write(f'- 命令：`{shlex.join(command)}`\n\n{fence}text\n{output.rstrip()}\n{fence}\n')
        stream.write(f'- 退出码：{code}\n- 用时：{elapsed:.3f} 秒\n')
        stream.flush()
    if not streaming:
        print(output, end='' if output.endswith('\n') else '\n')
    print(f'[recorded] exit={code}; elapsed={elapsed:.3f}s; log={log}',
          file=sys.stderr if streaming else sys.stdout, flush=True)
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    sys.exit(main())
