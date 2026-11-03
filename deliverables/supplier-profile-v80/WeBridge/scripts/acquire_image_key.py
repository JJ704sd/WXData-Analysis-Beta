"""Explicit, bounded, read-only scan of the selected account's unique owner.

No injection, network calls, memory dumps or key output. Candidates must decode
two independent local V2 images before being saved in a new .secrets JSON.
"""
import argparse
import ctypes
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time

from acquire_database_keys import (AcquireError, WindowsDependencies, inventory,
                                   validate_output, write_keys)


def scan(source, timeout=90, budget=2048 * 1024 * 1024):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web_mvp'))
    from media_container import dat_decode, media_type
    root, _, files = inventory(source)
    deps = WindowsDependencies()
    owner = deps.owner(files)
    samples = []
    for path in (root.parent / 'msg/attach').rglob('*.dat'):
        if len(samples) >= 6:
            break
        if not path.resolve().is_relative_to(root.parent) or path.stat().st_size > 2 * 1024 * 1024:
            continue
        data = path.read_bytes()
        if data.startswith(b'\x07\x08V2\x08\x07') and data not in samples:
            samples.append(data)
    if len(samples) < 2:
        raise AcquireError('key_match_incomplete')
    handle = deps._open(owner[0])
    process = deps.scanner.ReadOnlyProcess(owner[0], handle=handle)
    deadline = time.monotonic() + timeout
    total = 0
    seen = set()
    try:
        if deps._identity(handle) != owner[1]:
            raise AcquireError('owner_changed')
        address = 0
        while address < 0x7fffffffffff:
            if time.monotonic() >= deadline:
                raise AcquireError('scan_timeout')
            info = deps.scanner.MEMORY_BASIC_INFORMATION()
            if not deps.kernel32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
                break
            start = info.BaseAddress or address
            end = start + info.RegionSize
            if end <= address:
                break
            address = end
            if info.State != 0x1000 or info.Protect & 0x101 or info.Protect & 0xff not in (4, 8, 0x40, 0x80):
                continue
            trailing = b''
            for offset in range(0, info.RegionSize, 1024 * 1024):
                if time.monotonic() >= deadline:
                    raise AcquireError('scan_timeout')
                size = min(1024 * 1024, info.RegionSize - offset)
                total += size
                if total > budget:
                    raise AcquireError('scan_budget_exceeded')
                try:
                    chunk = process.read_bytes(start + offset, size)
                except OSError:
                    trailing = b''
                    continue
                data = trailing + chunk
                trailing = data[-68:]
                candidates = [m.group()[:16] for m in re.finditer(rb'(?<![A-Za-z0-9])[A-Za-z0-9]{16,32}(?![A-Za-z0-9])', data)]
                candidates += [m.group()[::2][:16] for m in re.finditer(rb'(?:[A-Za-z0-9]\x00){16,32}', data)]
                for key in candidates:
                    if key in seen:
                        continue
                    if len(seen) >= 1000000:
                        raise AcquireError('scan_budget_exceeded')
                    seen.add(key)
                    matches = 0
                    for sample in samples:
                        try:
                            clear = dat_decode(sample, key)
                            if media_type(clear)[0] in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'):
                                matches += 1
                        except ValueError:
                            pass
                        if matches >= 2:
                            if deps.owner(files) != owner or deps._identity(handle) != owner[1]:
                                raise AcquireError('owner_changed')
                            return [{'aes_hex': key.hex(), 'sourceRoot': str(root), 'verifiedSamples': matches}]
    finally:
        process.close()
    raise AcquireError('key_match_incomplete')


def worker(source, pipe):
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1); os.dup2(sink.fileno(), 2)
        try:
            pipe.send({'keys': scan(source)})
        except AcquireError as exc:
            pipe.send({'code': exc.code})
        except Exception:
            pipe.send({'code': 'scan_failed'})
        finally:
            pipe.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--output-file', required=True)
    args = parser.parse_args()
    child = None
    try:
        validate_output(args.output_file)
        root, _, _ = inventory(args.source_root)
        context = multiprocessing.get_context('spawn')
        incoming, outgoing = context.Pipe(False)
        child = context.Process(target=worker, args=(str(root), outgoing))
        child.start(); outgoing.close()
        if not incoming.poll(100):
            raise AcquireError('scan_timeout')
        result = incoming.recv(); incoming.close()
        if 'keys' not in result:
            raise AcquireError(result.get('code'))
        write_keys(args.output_file, result['keys'])
        print(json.dumps({'status': 'ok', 'verifiedSamples': result['keys'][0]['verifiedSamples']}))
        return 0
    except AcquireError as exc:
        print(json.dumps({'status': 'error', 'code': exc.code}))
        return 1
    finally:
        if child and child.pid:
            child.join(1)
            if child.is_alive():
                child.terminate(); child.join(5)
            child.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
