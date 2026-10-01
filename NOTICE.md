# Upstream references

PACMAN/LArPix-v2 wire definitions and the detector mapping convention were referenced from BrunoGelli/2x2Pacmon (commit 2cf0e2c7db056dd205efb7f41616c1795fa9ea67).

Upstream PacMon copyright notice:

Copyright © 2023 FERMI NATIONAL ACCELERATOR LABORATORY for the benefit of the DUNE Collaboration.

Upstream license: Apache License, Version 2.0. The source repository's LICENSE applies to its separately downloaded geometry and other upstream material:
https://github.com/BrunoGelli/2x2Pacmon/blob/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/LICENSE

This repository implements an independent Python observer and browser renderer. The initial implementation does not bundle upstream Go source or geometry JSONs.

ASIC-time rollover arithmetic references DUNE/ndlar_flow, commit a0eb2f364e35340d67fd73dc09a8e8f847211a58, src/proto_nd_flow/reco/charge/raw_event_builder.py (RawEventBuilder.unroll_timestamps). The streaming implementation here is independent and has no Flow runtime dependency.
