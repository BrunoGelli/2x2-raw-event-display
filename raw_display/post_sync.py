"""Display-only raw-ASIC timestamp policy and SYNC-83 arrival metadata.

This module never changes detector configuration, decoder word order, or clocks.
"""
from numbers import Integral
import numpy as np

DEFAULT_MIN_RAW_TIMESTAMP = 10


def validate_min_raw_timestamp(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or not 0 <= value <= 2**31:
        raise ValueError('min_raw_timestamp must be an integer in 0..2**31 ticks')
    return int(value)


def display_mask(ids, raw_timestamp, minimum):
    """Select mapped charge hits with RAW timestamp >= minimum (not modulo)."""
    return (np.asarray(ids) >= 0) & (np.asarray(raw_timestamp) >= minimum)


def sync83_message(stats, iogs, now, columns):
    """Small optional WebSocket message, derived from the existing shared state.

    age_s is collector-consumption age, not ASIC time or a hardware lock claim.
    Unknown timestamps are null, not zero-age fake arrivals.
    """
    sources = []
    for iog in iogs:
        row = stats[iog]
        stamp = row[columns['last_sync83']]
        sources.append(dict(iog=iog, count=int(row[columns['sync83_packets']]),
                            age_s=max(0., now-stamp) if stamp else None))
    return dict(type='sync83', version=1, sources=sources)
