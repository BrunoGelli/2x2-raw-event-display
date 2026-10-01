"""Opt-in bounded timing diagnostics on existing decoded hits; NO clock repair."""
from collections import deque
import numpy as np
from .trigger_windows import batch_context

# Seconds on the detector clock, not network end-to-end latency.
EDGES = np.array([0., 1e-5, .001, .01, .1, .5, 1., 1.25, 2., 4., np.inf])
LABELS = ['[0,10us)', '[10us,1ms)', '[1ms,10ms)', '[10ms,100ms)',
          '[100ms,0.5s)', '[0.5s,1s)', '[1s,1.25s)', '[1.25s,2s)', '[2s,4s)', '[4s,infinity)']
FIELDS = ('selected_hits', 'late_hits', 'raw_ge_period', 'late_raw_ge_period',
          'receipt_ge_period', 'crossed_raw_receipt', 'late_crossed_raw_receipt')


class LateAudit:
    def __init__(self, iog, timing_config):
        self.iog, self.config = iog, timing_config
        self.by_chip = np.zeros((8*256, len(FIELDS)), dtype=np.int64)
        self.lateness = np.zeros(len(LABELS), dtype=np.int64)
        self.receipt_lag = np.zeros(len(LABELS), dtype=np.int64)
        self.samples = deque(maxlen=12)
        self.max_lateness_s = self.max_receipt_lag_s = 0.

    def record(self, hits, ticks, selected, cursor, initial):
        idx = np.flatnonzero(selected)
        if not len(idx):
            return
        raw = hits.timestamp[idx].astype(np.int64)
        receipt = hits.receipt_timestamp[idx].astype(np.int64)
        ticks = ticks[idx]
        period = self.config.rollover_ticks
        si, prefix = batch_context(hits, initial, self.config)
        before = np.searchsorted(si, hits.word_indices[idx], side='left')
        receipt_ticks = prefix[before] + receipt
        limit = -1 if cursor is None else int(np.floor(cursor / self.config.tick_seconds))
        late = ticks <= limit
        raw_ge = raw >= period
        rec_ge = receipt >= period
        crossed = receipt < raw
        tile = (hits.io_channel[idx].astype(np.int64)-1)//4
        chip = hits.chip[idx].astype(np.int64)
        physical = tile*256 + chip
        masks = (np.ones(len(idx), bool), late, raw_ge, late & raw_ge,
                 rec_ge, crossed, late & crossed)
        for column, mask in enumerate(masks):
            self.by_chip[:, column] += np.bincount(physical[mask], minlength=8*256)
        lag = (receipt_ticks-ticks)*self.config.tick_seconds
        lag = np.maximum(lag, 0.)
        self.receipt_lag += np.histogram(lag, EDGES)[0]
        self.max_receipt_lag_s = max(self.max_receipt_lag_s, float(lag.max()))
        if np.any(late):
            lateness = (limit-ticks[late])*self.config.tick_seconds
            self.lateness += np.histogram(lateness, EDGES)[0]
            self.max_lateness_s = max(self.max_lateness_s, float(lateness.max()))
            for j in np.flatnonzero(late)[-3:]:
                self.samples.append(dict(io_channel=int(hits.io_channel[idx[j]]),
                    tile=int(tile[j])+1, chip=int(chip[j]), channel=int(hits.channel[idx[j]]),
                    raw_asic_ticks=int(raw[j]), raw_cycles=int(raw[j]//period),
                    asic_phase_ticks=int(raw[j]%period), receipt_ticks=int(receipt[j]),
                    unrolled_asic_ticks=int(ticks[j]), unrolled_receipt_ticks=int(receipt_ticks[j]),
                    playhead_ticks=limit,
                    lateness_s=float((limit-ticks[j])*self.config.tick_seconds),
                    inferred_asic_to_receipt_s=float(lag[j])))

    def summary(self):
        def record(values, **where):
            d = dict(zip(FIELDS, map(int, values)))
            d.update(where)
            d['late_fraction'] = d['late_hits']/d['selected_hits'] if d['selected_hits'] else None
            return d
        by_tile = self.by_chip.reshape(8, 256, -1).sum(axis=1)
        # Fixed-size top list, not one object per received packet.
        order = np.argsort(self.by_chip[:, 1], kind='stable')[::-1]
        top = [record(self.by_chip[k], tile=int(k//256)+1, chip=int(k%256))
               for k in order[:20] if self.by_chip[k, 1] > 0]
        return dict(iog=self.iog, totals=record(self.by_chip.sum(axis=0)),
                    tiles=[record(v, tile=i+1) for i,v in enumerate(by_tile)],
                    top_late_chips=top, samples=list(self.samples), histogram_labels=LABELS,
                    lateness_counts=self.lateness.tolist(),
                    inferred_asic_to_receipt_counts=self.receipt_lag.tolist(),
                    max_lateness_s=self.max_lateness_s, max_inferred_asic_to_receipt_s=self.max_receipt_lag_s,
                    caveat='Clock-model diagnostics, not proof of physical or network latency; no correction applied.')
