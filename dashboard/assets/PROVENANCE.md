# Visual pack provenance

The seven bundled materials were generated for AI Factory using ChatGPT's built-in image
generation. The generation records describe prompts for fictional surfaces and landscaping and
list no third-party image inputs. The timestamps below come from those records. The hashes are
for the recovered 1024 × 1024 PNG sources after normalization from the native 1254 × 1254 outputs.

The project author approved distribution of these seven assets under the repository's [MIT
license](../../LICENSE), to the extent that applicable rights exist.

| Source PNG | Generated at (UTC) | SHA-256 of source PNG |
| --- | --- | --- |
| `floor-oak.png` | 2026-11-03T23:02:55.119Z | `e491313447cc5cca4cf8738c67bfe9f3c319273c3f31c0c812b826d5a4df8c6f` |
| `floor-carpet.png` | 2026-11-03T23:04:00.097Z | `259c1114ff313568b3daf17fbe43d6bbbf7656bbc3e65427572035ec9aeead39` |
| `floor-concrete.png` | 2026-11-03T23:04:02.116Z | `0a8140c6e07ea9f80bfbe18f6347fc7fc288ad65986207d32c0107333b30f9e9` |
| `floor-terrazzo.png` | 2026-11-03T23:05:34.256Z | `7213b89e6e960796b09d37bcb32985fe4d2c5454d5f51088b01552c130062673` |
| `wood-desk.png` | 2026-11-03T23:05:35.743Z | `5ea572a7071fed8a67001e1b5b44ec0d2dd4301d58326ea6f4f3e25573381682` |
| `felt-acoustic.png` | 2026-11-03T23:05:37.249Z | `e0eafca3d1b822b7d1f6c79002f0ddb0fb9767cf0c38b250bc9a0c10cefc2bd1` |
| `ground-plaza.png` | 2026-11-03T23:03:26.502Z | `4c48fe09e04bd7d1391744116743993a472309c407142fadc661491fc937258f` |

## Rebuilding the bundle

Copy just the seven PNGs above from the original AI Factory visual pack into a staging directory.
Verify the hashes, install the pinned asset dependencies, and build into a separate output directory.

```bash
python -m pip install --require-hashes -r scripts/requirements-assets.txt
python scripts/build_assets.py --src path/to/seven-pngs --out path/to/texture-build
```

Copy the generated `assets.js` to `dashboard/assets/assets.js`. Keep this provenance record with
the bundle. The builder's automatic date is the source file's modification date. The dashboard
loads the WebP bundle locally in both the Pages and Docker builds.
