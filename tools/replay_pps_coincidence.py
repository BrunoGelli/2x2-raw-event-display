"""Check live LRS routing against one-second controls in an eight-IOG packet file.

Offline only. Rebuild complete PACMAN envelopes from the HDF5 header rows,
preserving word order, parity acceptance and receipt phases. The packet file
contains no host monotonic timestamps. Advance the queues on hardware time and
exercise wall-clock staleness separately in the collector tests.
"""
import argparse
import json

import h5py
import numpy as np

from raw_display.codec import HEADER, WORD, decode_batch, odd_parity
from raw_display.detector_triggers import DetectorTriggerRouter
from raw_display.geometry import load_geometry
from raw_display.timing import DetectorPlayback, TimingConfig
from raw_display.trigger_windows import ObserverConfig


def complete_blocks(dataset, size=24_000):
    pending = dataset[:0]
    for start in range(0, len(dataset), size):
        rows = np.concatenate((pending, dataset[start:start + size]))
        headers = np.flatnonzero(rows['packet_type'] == 4)
        if not len(headers) or headers[0] != 0:
            raise ValueError('expected complete PACMAN messages starting at a type-4 row')
        stop = len(rows) if start + size >= len(dataset) else headers[-1]
        pending = rows[stop:]
        if stop:
            yield rows[:stop]


def envelopes(rows):
    headers = np.flatnonzero(rows['packet_type'] == 4)
    data = rows[rows['packet_type'] != 4]
    if not len(data):
        return
    packet_type = data['packet_type']
    aux = (packet_type == 6) | (packet_type == 7)
    if np.any((packet_type > 3) & ~aux) or np.any(data['fifo_diagnostics_enabled']):
        raise ValueError('replay expects ordinary packet-v2 data and PACMAN SYNC/TRIG words')
    words = np.zeros(len(data), WORD)
    words['kind'] = np.where(packet_type == 6, ord('S'), np.where(packet_type == 7, ord('T'), ord('D')))
    words['io'] = np.where(aux, data['trigger_type'], data['io_channel'])
    words['receipt'] = data['receipt_timestamp']
    aux_time = np.ndarray(len(words), dtype='<u4', buffer=words, offset=4, strides=(WORD.itemsize,))
    aux_time[aux] = data['timestamp'][aux]
    payload = (packet_type & 3).astype(np.uint64)
    for field, shift in [('chip_id', 2), ('channel_id', 10), ('timestamp', 16),
                         ('dataword', 48), ('downstream_marker', 62)]:
        payload |= data[field].astype(np.uint64) << np.uint64(shift)
    flip = odd_parity(payload) != data['valid_parity'].astype(bool)
    words['payload'] = payload | (flip.astype(np.uint64) << np.uint64(63))
    ends = np.r_[headers[1:], len(rows)] - np.arange(1, len(headers) + 1)
    body = memoryview(words).cast('B')
    start = 0
    for end, second in zip(ends, rows['timestamp'][headers]):
        yield HEADER.pack(b'D', int(second), end-start) + body[WORD.itemsize*start:WORD.itemsize*end]
        start = end


def record_light_windows(matcher, starts):
    # Capture actual target-local t0 at the matcher boundary, including routing skips.
    consume = matcher.consume_triggers

    def record(source, ticks, frontier):
        if source == 'light':
            starts.append(ticks.copy())
        consume(source, ticks, frontier)

    matcher.consume_triggers = record


def replay(dataset, geometry_dir):
    iogs = range(1, 9)
    geometry = load_geometry(geometry_dir, iogs)
    timing = TimingConfig()
    normal = np.full(len(geometry.pixels), -1.)
    clocks = {i: DetectorPlayback(normal, timing) for i in iogs}
    router = DetectorTriggerRouter(len(normal), iogs, timing, ObserverConfig(trigger_view=True, view3d=True))
    charge = {i: [] for i in iogs}
    light = {i: [] for i in iogs}
    for i in iogs:
        record_light_windows(router.matchers[i], light[i])
    for block in complete_blocks(dataset):
        for i in iogs:
            rows = block[block['io_group'] == i]
            if not len(rows):
                continue
            decoded = decode_batch(envelopes(rows), strict=True)
            clock = clocks[i]
            initial = (clock.unroller.offset, clock.unroller.initial_tick is not None)
            pixels = geometry.lookup(i, decoded)
            ticks, _, selected = clock.ingest(pixels, decoded)
            frontier = clock.unroller.frontier_tick
            router.record(i, pixels, decoded, ticks, selected, initial, frontier, 0.)
            if frontier is not None:
                clock.cursor = frontier * timing.tick_seconds
                clock.step(0.)
                router.step(i, clock.cursor, 0.)
            charge[i].append(ticks[selected])

    origins = {i: router.aligner.offset(i, 0.) for i in iogs}
    if any(origin is None for origin in origins.values()):
        raise ValueError(f'incomplete alignment at end of replay: {origins}')
    period = timing.rollover_ticks
    start = max(clocks[i].unroller.initial_tick + origins[i] for i in iogs) + 4*period
    stop = min(clocks[i].unroller.frontier_tick + origins[i] for i in iogs) - 4*period
    results = {}
    for i in iogs:
        triggers = np.concatenate(light[i]) if light[i] else np.empty(0, dtype=np.int64)
        phase = triggers % period
        keep = ((triggers + origins[i] >= start) & (triggers + origins[i] < stop) &
                (phase >= period//200) & (phase < period - period//200))
        triggers = triggers[keep]
        hits = np.sort(np.concatenate(charge[i]))
        counts = []
        for shift in (-period, 0, period):
            lo = triggers + shift
            pairs = np.searchsorted(hits, lo + router.matchers[i].post) - np.searchsorted(hits, lo)
            counts.append(int(pairs.sum()))
        results[i] = dict(triggers=len(triggers), minus_1s=counts[0], routed=counts[1], plus_1s=counts[2])
    return dict(targets=results, alignment_revocations=router.generation, routing=router.stats)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packet_file')
    parser.add_argument('--geometry-dir', default='layout')
    args = parser.parse_args()
    with h5py.File(args.packet_file, 'r') as f:
        result = replay(f['packets'], args.geometry_dir)
    print(json.dumps(result, indent=2))
    if not all(t['routed'] > max(t['minus_1s'], t['plus_1s']) for t in result['targets'].values()):
        raise SystemExit('At least one target lacks an LRS coincidence excess')
