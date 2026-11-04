# Visual pack: generation in ChatGPT and integration

*[Version française](fr/ASSETS.md) (with the French prompts)*

This document lists the 15 images that enrich the rendering of the 3D dashboard, with a
ready-to-paste prompt for each one. The images are **optional**: if one is missing, the dashboard
keeps its current procedural rendering. The prompts below are in English; the original French
prompts are kept in [fr/ASSETS.md](fr/ASSETS.md). Either set produces the same files.

## Procedure (≈ 30 min)

1. Open **a single** ChatGPT conversation and paste the framing message below first: the style
   will stay consistent from one image to the next.
2. Then paste the prompts one by one, in order. For each image, check the requested format
   (square 1024 × 1024, landscape 1536 × 1024 or portrait 1024 × 1536); otherwise, write "same
   image, in … format".
3. Download each image as PNG and **rename it exactly** as indicated (`floor-oak.png`, etc.).
4. Put the files in `dashboard/assets/src/`, then run:
   ```bash
   pip install -r scripts/requirements-assets.txt
   python scripts/build_assets.py            # checks, optimizes and publishes dashboard/assets/assets.js
   python scripts/build_assets.py --check    # lists what is missing or does not match the format
   ```

The script:
- makes the textures tileable (edge blending);
- removes magenta (`#FF00FF`) backgrounds when the image has no transparency;
- slices the portrait sheet into 12 avatars;
- converts to WebP;
- writes a `dashboard/assets/assets.js` bundle that also works offline (`file://`);
- records the provenance (SHA-256 hash, generator, date) in `dashboard/assets/PROVENANCE.md`.

## Common rules

- **Fictional data only**: no real person, no celebrity, no brand, no existing logo, no
  readable text (unless stated otherwise).
- **Flat magenta background** `#FF00FF` for elements to cut out: the script makes it
  transparent. ChatGPT does not guarantee native transparency, depending on the model used.
- **License**: by decision of the author, the images of the visual pack are published under the
  repository's MIT license, with their provenance (SHA-256). Under OpenAI's terms of use, the
  generated images belong to the user, who may publish them; check the terms in force at the
  time of generation. Copyright protection of a fully generated image is not guaranteed in every
  country (see [LICENSES.md](LICENSES.md)).

## Framing message (paste first)

```text
You are going to generate a consistent series of images for the 3D digital twin of a fictional
tech office, "the AI Factory": open space, secure laboratory, R&D Lab, Skills Lab (training),
AI agent supervision room, coffee corner, reception. Common style for the whole series: modern
Scandinavian interior architecture, soft and diffuse daylight, palette of light oak, off-white
#F8FAFC, slate gray #334155, accents of violet #6D28D9 and teal #0EA5A4. Strict rules for every
image: no readable text, no existing logo or brand, no real person, no watermark. Follow the
requested format exactly. When I ask for a magenta background, use a perfectly flat solid
#FF00FF background, with no drop shadow and no gradient. Reply with the image only.
```

## The 15 images

### Tileable textures (square 1024 × 1024, orthographic top view)

| File | Use in the 3D scene |
|---|---|
| `floor-oak.png` | floors of the coffee corner and the secure Lab |
| `floor-carpet.png` | Delivery floor and Skills Lab |
| `floor-concrete.png` | reception, R&D Lab, Agent Ops room |
| `floor-terrazzo.png` | restrooms and War Room |
| `wood-desk.png` | desktops, tables, counter |
| `felt-acoustic.png` | focus pods, acoustic panels, seating |

**floor-oak.png**
```text
Floor texture, strictly top-down, orthographic, no perspective: light oak parquet laid in a
herringbone pattern, 10 cm planks, fine natural grain, matte finish. Perfectly even lighting, no
shadow, no reflection, no vignette. Seamless tileable texture on all four edges. Square format
1024 × 1024.
```

**floor-carpet.png**
```text
Floor texture, strictly top-down, orthographic: 50 × 50 cm carpet tiles, dense loop pile in a
slightly bluish charcoal gray, fine fiber variations, barely visible tile joints. Even lighting,
no shadow. Seamless tileable texture on all four edges. Square format 1024 × 1024.
```

**floor-concrete.png**
```text
Floor texture, strictly top-down, orthographic: very light gray polished concrete, subtle cloudy
shades, micro-aggregates, soft satin look. Even lighting, no shadow and no window reflection.
Seamless tileable texture on all four edges. Square format 1024 × 1024.
```

**floor-terrazzo.png**
```text
Floor texture, strictly top-down, orthographic: off-white terrazzo with marble chips in slate
gray, teal and subtle violet (small fragments of 2 to 8 mm), matte polish. Even lighting, no
shadow. Seamless tileable texture on all four edges. Square format 1024 × 1024.
```

**wood-desk.png**
```text
Desk surface texture, strictly top-down, orthographic: straight-grain light oak veneer, fine and
regular grain running horizontally, matte oiled finish. Even lighting, no shadow, no object.
Seamless tileable texture on all four edges. Square format 1024 × 1024.
```

**felt-acoustic.png**
```text
Acoustic fabric texture, orthographic front view: thick heathered gray wool felt with a very
slight violet tint, fibers visible in close-up, no pattern. Even lighting, no shadow. Seamless
tileable texture on all four edges. Square format 1024 × 1024.
```

### Environment

**ground-plaza.png** (square 1024 × 1024): landscaped ground around the building
```text
Strictly vertical, orthographic aerial view of a landscaped plaza surrounding an office
building: light paved walkways, lawns, clusters of trees seen from above, benches, bike lanes, a
few discreet flower beds. The center of the image (a rectangle of about 55% of the width and 40%
of the height) is a uniform, empty gray slab where the building will be placed. The edges of the
image fade into a solid gray-green. Soft late-morning light, very light shadows. No vehicle, no
person, no text. Square format 1024 × 1024.
```

**skyline-panorama.png** (landscape 1536 × 1024): 360° backdrop
```text
Horizon panorama for a background set: distant, hazy silhouettes of a modern city and hills,
soft gradient sky from pale blue to light peach, a few light clouds. Low contrast, pastel
colors, no recognizable building, no text. The horizon line sits at the lower third. The left
and right edges must join seamlessly (the image will be wrapped at 360°). Landscape format
1536 × 1024.
```

### Decor

**art-1.png** (portrait 1024 × 1536): wall painting of the War Room
```text
Framed abstract artwork, perfectly frontal view: geometric composition of soft organic shapes
evoking a neural network and data flows, palette of violet #6D28D9, teal #0EA5A4, sand and
off-white, visible canvas texture. Thin light oak frame. The artwork fills the whole image, with
no wall and no shadow around it. No text. Portrait format 1024 × 1536.
```

**art-2.png** (portrait 1024 × 1536): wall painting of the coffee corner
```text
Framed art poster, frontal view: minimalist landscape of stylized hills in layered flat colors,
low sun, palette of peach, soft terracotta, teal and cream, risograph print grain. Thin matte
black frame. The poster fills the whole image, with no wall and no shadow. No text. Portrait
format 1024 × 1536.
```

**art-3.png** (landscape 1536 × 1024): large artwork of the reception
```text
Large framed abstract mural, frontal view: waves of fine parallel lines forming contour lines,
like a topographic map of data, gradient from deep violet to teal on a cream background. Thin
light oak frame. The artwork fills the whole image, with no wall and no shadow. No text.
Landscape format 1536 × 1024.
```

**logo-factory.png** (square 1024 × 1024, magenta background): emblem of the factory
```text
Original graphic emblem for a fictional "AI Factory": a geometric monogram combining a hexagon,
stylized gears and a neural network node, thick rounded strokes, gradient from violet #6D28D9 to
teal #0EA5A4, modern flat style. No letter, no text. Centered, filling 70% of the image, on a
perfectly flat solid magenta #FF00FF background. Square format 1024 × 1024.
```

### Characters

**avatars-sheet.png** (landscape 1536 × 1024): sheet of the team's 12 portraits
```text
Sheet of 12 head-and-shoulders avatar portraits for an app, laid out in an exact grid of
4 columns × 3 rows, cells of equal size separated by regular margins, each bust centered in its
cell, front or three-quarter view, on the same solid off-white #F8FAFC background in every cell.
Consistent modern vector illustration style (soft flat colors, light gradients), entirely
fictional characters. Reading order from left to right, then top to bottom:
1 light rosy skin, mid-length chestnut hair, violet sweater;
2 tan copper skin, short curly black hair, magenta shirt;
3 golden skin, long straight black hair, raspberry pink top;
4 very fair skin, short red hair, round glasses, petrol blue jacket;
5 fair skin, blond hair tied back, fir green shirt;
6 brown skin, short beard, close-cropped black hair, caramel brown sweater;
7 light rosy skin, silver-gray bob, slate gray blazer;
8 fair skin, messy brown hair, olive green sweatshirt;
9 tan copper skin, voluminous curly hair, light lilac top;
10 fair skin, short blond hair, burgundy turtleneck;
11 dark skin, fine braids pulled up, stone gray sweater;
12 golden skin, mid-length black hair in a bun, lavender shirt.
No text, no name. Landscape format 1536 × 1024.
```

**mascot-visitor.png** (portrait 1024 × 1536, magenta background): character of the visit mode
```text
Small original mascot character for an immersive street-level "visit" mode: round, friendly
silhouette, amber yellow #F5B301 body with soft volume (matte 3D toy style), arms slightly
raised as if picked up from above, small teal backpack, no detailed face (two dots for eyes).
Three-quarter view, feet visible, centered, filling 80% of the height, on a perfectly flat solid
magenta #FF00FF background. No text. Portrait format 1024 × 1536.
```

### Interface

**hero-isometric.png** (landscape 1536 × 1024): illustration of the help screen and the README
```text
Polished isometric illustration of a tech office floor in cutaway (model without a roof): open
space with 6 desks, glass meeting room, secure laboratory with a glowing server rack, training
workshop, supervision room with a large wall of chart screens, coffee corner, reception with
lockers, plants. Small stylized fictional characters at work. Soft 3D illustration style,
daylight, palette of light oak, off-white, slate, accents of violet #6D28D9 and teal #0EA5A4,
very light solid background. No text, no logo. Landscape format 1536 × 1024.
```

## Mapping to the code

| Identifier | Source file | Processing | Use |
|---|---|---|---|
| `floor-*`, `wood-desk`, `felt-acoustic` | PNG 1024² | tiling + WebP 1024 | material `map`, UVs in world coordinates (1 repeat ≈ 2 m) |
| `ground-plaza` | PNG 1024² | WebP 1024, faded edges | 64 × 64 m ground plane under the building |
| `skyline-panorama` | PNG 1536 × 1024 | horizontal tiling + WebP | background cylinder (3 repeats), night tint in the dark theme |
| `art-1..3` | PNG | WebP | frames on the walls (hidden with the walls in cutaway view) |
| `logo-factory` | magenta PNG | cut-out + WebP | reception sign, header logo |
| `avatars-sheet` | PNG 1536 × 1024 | 4 × 3 slicing, WebP 256² | people badges and video tiles of the War Room (`p01` to `p12` in the order of `config/profiles.yaml`) |
| `mascot-visitor` | magenta PNG | cut-out + WebP | "Visit" button and dragged character |
| `hero-isometric` | PNG | WebP 1280 | help (`?`) and README |
