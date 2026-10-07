# Upstream references

PACMAN/LArPix-v2 wire definitions and the detector mapping convention were referenced from BrunoGelli/2x2Pacmon (commit 2cf0e2c7db056dd205efb7f41616c1795fa9ea67).

Upstream PacMon copyright notice:

Copyright © 2023 FERMI NATIONAL ACCELERATOR LABORATORY for the benefit of the DUNE Collaboration.

Upstream license: Apache License, Version 2.0. The source repository's LICENSE applies to its separately downloaded geometry and other upstream material:
https://github.com/BrunoGelli/2x2Pacmon/blob/2cf0e2c7db056dd205efb7f41616c1795fa9ea67/LICENSE

This repository implements an independent Python observer and browser renderer. The initial implementation does not bundle upstream Go source or geometry JSONs.

ASIC-time rollover arithmetic references DUNE/ndlar_flow, commit a0eb2f364e35340d67fd73dc09a8e8f847211a58, src/proto_nd_flow/reco/charge/raw_event_builder.py (RawEventBuilder.unroll_timestamps). The streaming implementation here is independent and has no Flow runtime dependency.

## Optional 3D display

`raw_display/geometry3d/` is generated from DUNE/ndlar_flow commit
`5d63843212cd32d88de2c15d5ffa95b8d43b50a5`, using the GeometryData configuration,
2x2 detector definition and module-specific layouts_v5 charge geometries.
The generation inputs and their SHA256 hashes are in `metadata.json`.
The nominal drift speed is evaluated using that checkout's
`LArData.electron_mobility` parameterization. Flow source is not vendored or
imported by the live runtime. The inspected Flow checkout did not contain a
top-level license; this notice does not assign a new license to its geometry.

Three.js r170 is pinned at `beab9e845f9e5ae11d648f55b24a0e910b56a85a`.
`static/vendor/three.module.min.js` is the unmodified upstream build.
`static/vendor/OrbitControls.js` differs only in its import path, pointing to
that local module. The upstream MIT license is included in
`raw_display/static/vendor/THREE-LICENSE.txt`. No CDN assets are used.
