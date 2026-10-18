# Vendored browser libraries

Served offline from `/static/vendor/`; no CDN, no build step.

| File | Source | Version | Notes |
|---|---|---|---|
| `three.module.min.js` | npm `three` → `build/three.module.min.js` | 0.170.0 | unmodified |
| `OrbitControls.js` | npm `three` → `examples/jsm/controls/OrbitControls.js` | 0.170.0 | import line repointed from `'three'` to `./three.module.min.js` |
| `three.LICENSE` | npm `three` → `LICENSE` | 0.170.0 | MIT |

Tarball integrity (matches the npm registry `dist.integrity`):
`sha512-FQK+LEpYc0fBD+J8g6oSEyyNzjp+Q7Ks1C568WWaoMRLW+TkNNWmenWeGgJjV105Gd+p/2ql1ZcjYvNiPZBhuQ==`
