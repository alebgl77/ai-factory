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

## Fictional demo portraits

ChatGPT's built-in image generation produced `avatars-sheet.png` at
2026-11-03T23:02:04.545Z. The sheet's SHA-256 is
`367b485144c4eb5f9a7a6bbf790c0bd5b215235374c9bd44210a329bd391da7c`. The asset builder
slices it into twelve 256 × 256 WebPs in `assets.js` (`avatar-p01` through `avatar-p12`).

ChatGPT Image also generated the four-view Camille reference (`p01`) and eleven additional
four-view sheets on 2026-11-06 using the fictional profiles as references. The Camille atlas is
`camille-turnaround` in `assets.js`. The other atlases are keyed by profile in
`portrait-atlases.js`. Only the WebP derivatives and their runtime registration are bundled.

The maintainer authorized publication of these fictional portraits with the demo. The dashboard
code is under the repository's [MIT license](../../LICENSE). The explicit MIT approval for
visual materials above applies to those seven materials.

| Profile | SHA-256 of single-view WebP | SHA-256 of four-view source PNG | SHA-256 of four-view WebP |
| --- | --- | --- | --- |
| `p01` | `dd272f517a1146f4f94f866da9feff27da4e9a634d5996ab20d8cf087889f87e` | `d4633af01a2592d7ff160d1db6f2bfd80048901714ca8af340ab5d23b612f35f` | `281457a54d31d382de63373d3c224def12ee89932de8f68b5dcfbe2cbd726ccb` |
| `p02` | `0282a07e72aae4cce396152c36f3b5c8f38d9241844fe47072b43521b89fd407` | `5d1e65ab07b5d26a0e2eb4a6e96f7871a3c5a06a85bc0dc0782c09bcedff785b` | `31ddd2d1981ef3286fc80efaf8cf1b6cdd6c3e962988aa210a90f4038beb88e7` |
| `p03` | `0efbe8c2f1f266077381d6cdc7be5b34dc82d709ebe67ed5e94af28d0e014679` | `a03a6d2b20b9f4b3d39cdfd7de2aa44dc793e80e991f182113feb5a57cff31d2` | `7d88302edb20688e522e3e511c6cfd277daef7df6bdf3f987fbe405d60126d37` |
| `p04` | `f8afc4236742593f55e01f157d5dca471e9ad1ba30e8610d4c96eccb39201594` | `197b51293323ad0432b2cac4dfd447848cfb5c84609b60bc16ed77f8bbd19e01` | `6b876e673b193ebbabd448010f9148817100ab74760d79bf488a289cc0dee925` |
| `p05` | `fff383b3302be5692bdf3131a0c9f90249d6e9856bb7878b3801b487e3c7e114` | `7c141127867152586db398df4f173cf21b44058781dfa2b10a958eaf20966a96` | `c1ee3b55c48e329829bb803908cbf96d6ab1defbe5b0eccd0247e3fab7011e22` |
| `p06` | `986d971680434738d5816d206f2ef4a27d8274cc48812b4c32e4e859bce3fb03` | `6e52b9451d02c69162f799a565b3cd287ba67aab671724d7583d831008db2b71` | `f652c33dd1c8ccb58660d279fe8938b8cb3c30a8c120e74564cfb17126ecf2ad` |
| `p07` | `116cec3cde92fc40a54785779777a5b1fe3a28e0f8b1710ead4321f20a10fe13` | `5058e35814ff2a8f3a28f18009d0bdec855f85168633573db1c2283194442f9b` | `3636b20e66bb2f0243a076e567745345f589ab80ac57346e6f564ccaa5f4fe47` |
| `p08` | `c84aff4a42ede4c7d0e60670c2218abaeca18ff4dfea7307edf7b9d2cc81f189` | `dc733f6ebb6c506adf1a84e8b3fc2fca90f10be71ea437eb710b2ec8dfdaddc8` | `ee1d97089ff0e7dad551e2c45e59f04ab40137c7c405dfc3fb55bec8f64196ff` |
| `p09` | `3bc15f00eb38d4300a2ad1d6f88d10df821db18a1b0afca599b5027e45830d57` | `73554579a5e0f131ab3a541fc025fa5a7e8b71d773d8e271abd2d95c652761ab` | `60588799a6b4cc6feddabdb73bc6c6efc58af4d9bf438400430fc3565058cbcc` |
| `p10` | `50a85a003fe1eeabea6b14481edce7d59b083f046b48ae824a6d0b34492b06c9` | `8e228974e41db69939bfc2a79befee59210c7431121562897f42be918a241e6d` | `2dc7252ca1447f505e911949e1ce4fceb2c6a1e6f01e0430614ec4eaa74c22d7` |
| `p11` | `15842387b1e688d471112496b718f5df6417fed37fc1a92f120749354c342b9e` | `ff95a2f1bb629d9533f09946a2964cf6f0c27010d8c513021b1fe8d9fba95f39` | `f416f05c3052aa47b77c457eb99e261eff62027e52e3e85bd6f714ba589924e2` |
| `p12` | `900e78a9cd3692ea156fa283e80d3f6d7614fcfbd1eddb9d50c505fa8300e650` | `6c17192d4a81228cf1ff4499559095d63033fcd8802bbc9fc976ec1c299d37cb` | `ff20ed328a5f5cc7a0b8a8b8b8192290577e59dc82c770ef138704ae5e505c58` |

## Rebuilding the seven materials

Copy just the seven PNGs above from the original AI Factory visual pack into a staging directory.
Verify the hashes, install the pinned asset dependencies, and build into a separate output directory.

```bash
python -m pip install --require-hashes -r scripts/requirements-assets.txt
python scripts/build_assets.py --src path/to/seven-pngs --out path/to/texture-build
```

Merge the seven generated material entries into `dashboard/assets/assets.js`, preserving the
`avatar-p01` through `avatar-p12` and `camille-turnaround` entries. Keep this provenance record
with the bundle. The builder's automatic date is the source file's modification date. The
dashboard loads the WebP bundles locally in both the Pages and Docker builds.
