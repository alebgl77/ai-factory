/* SPDX-License-Identifier: MIT */
// A registered four-view photo atlas colors an indexed silhouette relief in
// avatar coordinates. The first registration is Camille's original calibration.
(() => {
  'use strict';

  const PIVOT = 1.43;
  const SIDE = Math.SQRT1_2;
  const HEIGHT_RANGE = [1.108, 1.660];
  const MAX_DIMENSION = 2048;
  // Front, three-quarter facing image-right, profile facing image-right, back.
  // Each axis projects avatar coordinates onto the positive image-x direction.
  const VIEW_AXES = [[-1, 0], [-SIDE, -SIDE], [0, -1], [1, 0]];
  const CAMILLE = {
    width: 1536, height: 1024, backdrop: '#ada09b',
    views: [
      { bounds: [10, 10, 763, 506], pivot: [386, 213], scale: 850 },
      { bounds: [774, 10, 1526, 506], pivot: [1129, 212], scale: 850 },
      { bounds: [10, 518, 763, 1014], pivot: [408, 721], scale: 845 },
      { bounds: [774, 518, 1526, 1014], pivot: [1155, 724], scale: 850 },
    ],
  };
  // Lower edges of the visible neck and hair on the fictional portrait
  // sheets. Each row is a sequence of x/y pixel offsets from its view pivot.
  // Register the edge from the source garment seam, independent of color.
  const NECKLINES = {
    p01: [
      [-200, 175, -143, 184, -104, 181, -77, 178, -44, 194, 0, 199, 45, 194, 77, 178, 123, 182, 200, 158],
      [-200, 162, -160, 185, -115, 179, -70, 175, -34, 182, 4, 191, 49, 199, 80, 190, 117, 183, 173, 152],
      [-200, 174, -158, 188, -120, 203, -94, 188, -52, 178, -12, 174, 30, 179, 48, 151, 91, 112],
      [-200, 165, -155, 180, -104, 190, -49, 190, 0, 193, 56, 190, 118, 188, 160, 175, 200, 159],
    ],
    p02: [
      [-125, 113, -76, 113, -68, 157, -39, 191, 5, 219, 35, 199, 62, 170, 74, 151, 82, 114, 140, 106],
      [-130, 102, -67, 112, -56, 126, -12, 153, 27, 181, 60, 213, 67, 224, 74, 197, 77, 148, 120, 110],
      [-120, 114, -78, 120, -38, 132, 0, 146, 45, 170, 55, 167, 69, 126, 130, 116],
      [-120, 105, -70, 120, 0, 118, 69, 120, 117, 105],
    ],
    p03: [
      [-200, 192, -150, 183, -110, 175, -84, 173, -55, 189, 0, 199, 55, 190, 79, 177,
        97, 218, 112, 269, 140, 269, 157, 196, 200, 170],
      [-200, 202, -152, 190, -103, 176, -76, 172, -40, 186, 8, 199, 60, 204, 75, 196,
        95, 228, 112, 267, 155, 267, 172, 199, 200, 175],
      [-200, 264, -160, 268, -136, 236, -111, 196, -78, 174, -44, 159, -11, 170, 36, 189, 51, 155, 90, 112],
      [-200, 200, -174, 200, -156, 204, -135, 254, -106, 270, 113, 270, 147, 226, 169, 196, 201, 192],
    ],
    p04: [
      [-131, 108, -77, 116, -70, 148, -52, 171, 0, 203, 54, 172, 71, 148, 81, 118, 134, 106],
      [-138, 109, -69, 120, -52, 151, -13, 179, 54, 202, 78, 195, 83, 155, 97, 124, 140, 108],
      [-153, 110, -82, 131, -45, 142, 0, 159, 31, 179, 47, 179, 56, 139, 108, 104],
      [-124, 106, -66, 123, 0, 125, 64, 123, 121, 106],
    ],
    p05: [
      [-185, 149, -155, 177, -120, 182, -104, 171, -79, 148, -63, 132, -55, 171,
        -36, 202, 2, 239, 30, 209, 58, 172, 59, 132, 93, 136, 122, 138, 151, 110],
      [-193, 144, -170, 177, -129, 192, -96, 179, -71, 153, -53, 140, -39, 158, 8, 184,
        33, 198, 65, 235, 70, 233, 76, 201, 79, 151, 118, 139, 147, 132, 175, 105],
      [-200, 157, -184, 184, -154, 217, -133, 232, -117, 219, -101, 198, -88, 170,
        -72, 137, -44, 142, -13, 152, 31, 172, 44, 175, 58, 127, 112, 114],
      [-171, 106, -103, 131, -86, 147, -72, 186, -55, 217, -30, 227, -4, 234, 20, 239,
        47, 228, 67, 204, 82, 170, 96, 132, 150, 103],
    ],
    p06: [
      [-135, 106, -83, 120, -76, 153, -62, 175, -26, 197, 3, 207, 38, 194, 74, 177, 83, 150, 87, 120, 134, 110],
      [-143, 110, -75, 119, -66, 141, -24, 166, 22, 189, 62, 204, 76, 185, 88, 162, 88, 131, 129, 112],
      [-150, 114, -91, 120, -56, 127, -13, 137, 29, 160, 49, 179, 61, 176, 70, 134, 120, 138],
      [-142, 113, -84, 130, -41, 130, 0, 126, 44, 129, 86, 131, 139, 112],
    ],
    p07: [
      [-186, 105, -149, 115, -112, 125, -76, 126, -64, 132, -61, 171, -37, 202,
        2, 211, 43, 202, 58, 178, 59, 132, 90, 123, 133, 118, 174, 102],
      [-178, 106, -148, 119, -104, 128, -68, 132, -49, 140, -30, 169, 9, 203,
        44, 214, 68, 210, 77, 192, 81, 147, 85, 123, 125, 122, 174, 104],
      [-192, 124, -156, 136, -120, 145, -81, 147, -51, 147, -18, 160, 23, 179, 37, 178, 43, 147, 95, 114],
      [-183, 103, -149, 123, -114, 135, -63, 138, 0, 140, 68, 137, 109, 135, 152, 118, 182, 103],
    ],
    p08: [
      [-129, 103, -85, 111, -80, 119, -75, 149, -61, 184, -37, 205, 0, 208, 43, 204, 58, 188, 61, 151, 65, 115, 125, 101],
      [-133, 103, -78, 106, -62, 119, -57, 132, -18, 162, 18, 193, 48, 207, 64, 201, 69, 176, 69, 137, 119, 110],
      [-135, 104, -87, 112, -57, 116, -20, 128, 13, 147, 39, 167, 49, 155, 60, 125, 116, 108],
      [-133, 103, -68, 114, -29, 115, 28, 113, 66, 114, 130, 103],
    ],
    p09: [
      [-209, 171, -172, 182, -139, 174, -106, 156, -78, 129, -72, 133, -72, 175,
        -57, 208, -25, 216, 26, 215, 58, 209, 70, 184, 72, 131, 94, 149, 128, 172, 165, 181, 204, 171],
      [-203, 170, -176, 180, -139, 172, -96, 153, -54, 132, -29, 121, -20, 148, 6, 180,
        44, 211, 82, 216, 107, 202, 107, 169, 124, 151, 143, 171, 173, 181, 201, 170],
      [-207, 182, -186, 205, -155, 218, -122, 218, -101, 202, -74, 178, -43, 164,
        -22, 143, 4, 164, 28, 183, 38, 172, 55, 127, 114, 113],
      [-205, 176, -176, 191, -143, 202, -99, 212, -45, 211, 0, 213, 46, 211, 100, 208, 150, 194, 198, 178],
    ],
    p10: [
      [-133, 106, -82, 108, -70, 114, -66, 152, -48, 182, -24, 193, 0, 196,
        28, 192, 51, 182, 65, 151, 68, 112, 133, 107],
      [-137, 100, -80, 104, -64, 109, -61, 123, -27, 151, 5, 179, 35, 193,
        62, 192, 73, 180, 77, 147, 81, 122, 134, 104],
      [-143, 104, -93, 109, -67, 112, -31, 122, 0, 137, 37, 159, 46, 163, 56, 137, 108, 112],
      [-133, 104, -67, 113, -29, 111, 25, 111, 66, 114, 131, 103],
    ],
    p11: [
      [-145, 103, -78, 109, -62, 116, -58, 142, -55, 163, -26, 168, 0, 169,
        29, 167, 54, 164, 57, 140, 57, 116, 87, 107, 124, 108],
      [-131, 105, -61, 111, -44, 119, -40, 132, -17, 155, 7, 166, 39, 171,
        67, 168, 68, 144, 70, 120, 107, 110, 147, 107],
      [-142, 107, -75, 118, -50, 121, -17, 134, 12, 147, 30, 162, 35, 149, 44, 119, 111, 106],
      [-129, 105, -66, 116, -45, 121, 0, 120, 45, 121, 68, 116, 130, 105],
    ],
    p12: [
      [-152, 113, -108, 130, -85, 127, -68, 122, -64, 148, -49, 177, -24, 203, 0, 221,
        23, 205, 48, 178, 59, 149, 65, 122, 88, 129, 113, 132, 150, 108],
      [-150, 109, -111, 127, -78, 128, -62, 121, -46, 130, -15, 158, 20, 188,
        49, 209, 60, 223, 72, 204, 72, 176, 77, 137, 97, 127, 129, 132, 166, 110],
      [-149, 111, -92, 119, -68, 127, -34, 134, -5, 145, 22, 157, 42, 164, 51, 149, 56, 125, 118, 111],
      [-151, 105, -106, 131, -80, 132, -59, 126, -28, 127, 29, 127, 59, 129, 81, 131, 111, 132, 150, 105],
    ],
  };
  // Verified neck-skin pixels for the lower native tube. Long hairstyles
  // conceal the rear neck in their source plates and use the front swatch.
  const NECK_SKIN = {
    p01: [[0, 156], [30, 159], [15, 143], null],
    p02: [[0, 157], [22, 147], [6, 133], [0, 104]],
    p03: [[0, 158], [15, 160], [10, 155], null],
    p04: [[0, 145], [25, 146], [5, 141], [0, 107]],
    p05: [[0, 167], [45, 159], [12, 151], null],
    p06: [[0, 164], [35, 164], [20, 153], [0, 119]],
    p07: [[0, 156], [25, 158], [8, 150], null],
    p08: [[0, 152], [22, 155], [0, 129], [0, 103]],
    p09: [[0, 164], [43, 173], [12, 157], null],
    p10: [[0, 150], [22, 151], [11, 136], [0, 102]],
    p11: [[0, 149], [25, 151], [6, 135], [0, 103]],
    p12: [[0, 153], [29, 156], [9, 139], [0, 109]],
  };
  // Mounted head drops in avatar-space meters. Calibrated from the visible
  // chin/beard-to-collar gap in orthographic profiles at 2 mm per pixel.
  const HEAD_DROP = {
    p01: .090, p02: .087, p03: .088, p04: .093, p05: .087, p06: .063,
    p07: .091, p08: .089, p09: .097, p10: .090, p11: .093, p12: .097,
  };
  const NECK_BASE = .258;
  const NECK_LENGTH = .151;

  function validBackdrop(value) {
    return value === undefined || typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
  }

  function validateRegistration(registration) {
    if (!registration || typeof registration !== 'object') return null;
    const { width, height, views, heightRange = HEIGHT_RANGE, backdrop } = registration;
    if (![width, height].every(size => Number.isInteger(size) && size >= 128 && size <= MAX_DIMENSION)
        || !Array.isArray(views) || views.length !== VIEW_AXES.length
        || !Array.isArray(heightRange) || heightRange.length !== 2
        || !heightRange.every(Number.isFinite) || heightRange[0] < .8 || heightRange[0] >= PIVOT
        || heightRange[1] > 2.1 || heightRange[1] <= PIVOT || !validBackdrop(backdrop)) return null;
    const registered = [];
    for (const [index, view] of views.entries()) {
      if (!view) return null;
      const { bounds, pivot, scale } = view;
      if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isInteger)
          || !Array.isArray(pivot) || pivot.length !== 2 || !pivot.every(Number.isFinite)
          || !Number.isFinite(scale) || scale < 64 || scale > 4096
          || !validBackdrop(view.backdrop)) return null;
      const [left, top, right, bottom] = bounds;
      if (left < 0 || top < 0 || right > width || bottom > height
          || right - left < 64 || bottom - top < 64
          || pivot[0] <= left || pivot[0] >= right || pivot[1] <= top || pivot[1] >= bottom
          || registered.some(({ bounds: [x1, y1, x2, y2] }) =>
            left < x2 && right > x1 && top < y2 && bottom > y1)) return null;
      registered.push({ bounds, pivot, scale, axis: VIEW_AXES[index], backdrop: view.backdrop ?? backdrop });
    }
    return { width, height, views: registered, heightRange };
  }

  function readSilhouettes(image, { width, height, views }) {
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(image, 0, 0, width, height);
    const pixels = ctx.getImageData(0, 0, width, height).data;
    return views.map(({ bounds, backdrop }) => {
      const [left, top, right, bottom] = bounds;
      const rows = new Array(height);
      const edgeColors = [[], [], []];
      for (let y = top + 2; y < bottom - 1; y++) {
        const edgeA = 4 * (y * width + left + 12);
        const edgeB = 4 * (y * width + right - 12);
        const bg = [0, 1, 2].map(i => (pixels[edgeA + i] + pixels[edgeB + i]) * .5);
        if (!backdrop && y % 8 === 0) bg.forEach((channel, i) => edgeColors[i].push(channel));
        let start = -1, end = -1, run = 0;
        for (let x = left + 15; x < right - 15; x++) {
          const i = 4 * (y * width + x);
          const delta = Math.abs(pixels[i] - bg[0]) + Math.abs(pixels[i + 1] - bg[1])
            + Math.abs(pixels[i + 2] - bg[2]);
          if (delta <= 43) { run = 0; continue; }
          if (++run >= 5) {
            if (start < 0) start = x - 4;
            end = x;
          }
        }
        if (end - start >= 8) rows[y] = [start, end];
      }
      const key = backdrop ?? '#' + edgeColors.map(values => {
        values.sort((a, b) => a - b);
        return Math.round(values[values.length >> 1]).toString(16).padStart(2, '0');
      }).join('');
      return { rows, backdrop: key };
    });
  }

  function silhouetteAt(rows, imageY) {
    const row = Math.round(imageY);
    if (!rows[row]) return null;
    // A profile chin joins the neck at an occlusion boundary, rather than at
    // a horizontal step in the surface. Smooth those consecutive constraints
    // while retaining the shorter filter close to the crown and frame edges.
    const radius = rows[row - 16] && rows[row + 16] ? 16 : 2;
    const steps = radius === 16 ? 4 : 1;
    const bounds = [0, 0];
    let total = 0;
    for (let offset = -radius; offset <= radius; offset += steps) {
      const contour = rows[row + offset];
      if (!contour) continue;
      const weight = 1 + radius - Math.abs(offset);
      bounds[0] += contour[0] * weight;
      bounds[1] += contour[1] * weight;
      total += weight;
    }
    return bounds.map(value => value / total);
  }

  function clip(poly, ax, az, bound) {
    const result = [];
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length];
      const da = ax * a[0] + az * a[1] - bound;
      const db = ax * b[0] + az * b[1] - bound;
      if (da <= 0) result.push(a);
      if ((da < 0 && db > 0) || (da > 0 && db < 0)) {
        const t = da / (da - db);
        result.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
      }
    }
    return result;
  }

  function constraintsAt(silhouettes, views, y) {
    const limits = [];
    for (let i = 0; i < views.length; i++) {
      const { pivot, scale, axis } = views[i];
      const bounds = silhouetteAt(silhouettes[i].rows, pivot[1] - (y - PIVOT) * scale);
      if (!bounds) return null;
      const [ax, az] = axis;
      // Inset the edge by one texel to avoid sampling the studio backdrop.
      const lo = (bounds[0] + 1 - pivot[0]) / scale;
      const hi = (bounds[1] - 1 - pivot[0]) / scale;
      limits.push([ax, az, hi], [-ax, -az, -lo]);
      if (i === 1) limits.push([-ax, az, hi], [ax, -az, -lo]);
    }
    return limits;
  }

  function makeRelief(THREE, silhouettes, { views, heightRange: [bottomY, topY] }) {
    const sectors = 84, rows = 106, positions = [], indices = [], centers = [];
    const directions = Array.from({ length: sectors }, (_, i) => {
      const a = 2 * Math.PI * i / sectors;
      return [Math.cos(a), Math.sin(a)];
    });
    for (let row = 0; row <= rows; row++) {
      const y = bottomY + (topY - bottomY) * row / rows;
      const limits = constraintsAt(silhouettes, views, y);
      if (!limits) continue;
      let poly = [[-.6, -.6], [.6, -.6], [.6, .6], [-.6, .6]];
      for (const [ax, az, limit] of limits) poly = clip(poly, ax, az, limit);
      if (poly.length < 3) continue;
      const center = [0, 1].map(i => poly.reduce((sum, v) => sum + v[i], 0) / poly.length);
      const first = positions.length / 3;
      centers.push([center[0], y, center[1]]);
      for (const [dx, dz] of directions) {
        let radius = .6;
        for (const [ax, az, limit] of limits) {
          const denominator = ax * dx + az * dz;
          if (denominator > 1e-6) radius = Math.min(radius,
            (limit - ax * center[0] - az * center[1]) / denominator);
        }
        positions.push(center[0] + radius * dx, y, center[1] + radius * dz);
      }
      if (first === 0) continue;
      for (let i = 0; i < sectors; i++) {
        const next = (i + 1) % sectors;
        const a = first - sectors + i, b = first - sectors + next;
        indices.push(a, first + i, b, b, first + i, first + next);
      }
    }
    if (centers.length < 10) return null;
    const bottom = positions.length / 3, top = bottom + 1;
    positions.push(...centers[0], ...centers.at(-1));
    const topRing = bottom - sectors;
    for (let i = 0; i < sectors; i++) {
      const next = (i + 1) % sectors;
      indices.push(bottom, i, next, top, topRing + next, topRing + i);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
    return geometry;
  }

  function makeSilhouetteMatte(THREE, height, silhouettes) {
    // A four-row texture stores the left/right silhouette edge of each source
    // scanline in two byte pairs. This preserves gray hair and clothing inside
    // the shape without treating those colors as studio backdrop.
    const edges = new Uint8Array(height * silhouettes.length * 4);
    for (const [view, { rows }] of silhouettes.entries()) {
      rows.forEach(([left, right], row) => {
        const i = 4 * (view * height + row);
        edges[i] = left & 255;
        edges[i + 1] = left >> 8;
        edges[i + 2] = right & 255;
        edges[i + 3] = right >> 8;
      });
    }
    const matte = new THREE.DataTexture(edges, height, silhouettes.length, THREE.RGBAFormat);
    matte.needsUpdate = true;
    return matte;
  }

  function makeNecklineMatte(THREE, { width, views }, curves) {
    // One texel per source x keeps the contour registered to its photograph.
    // Each little-endian pair stores an integer image-space row.
    const cutoffs = new Uint8Array(width * views.length * 4);
    for (const [view, { pivot, bounds }] of views.entries()) {
      const curve = curves[view];
      let next = 2;
      for (let x = 0; x < width; x++) {
        const dx = x - pivot[0];
        while (next < curve.length && dx >= curve[next]) next += 2;
        const last = next - 2;
        let cutoff = curve[last + 1];
        if (dx > curve[last] && next < curve.length) {
          const t = (dx - curve[last]) / (curve[next] - curve[last]);
          cutoff += t * (curve[next + 1] - cutoff);
        }
        const row = Math.round(Math.max(bounds[1], Math.min(bounds[3], pivot[1] + cutoff)));
        const i = 4 * (view * width + x);
        cutoffs[i] = row & 255;
        cutoffs[i + 1] = row >> 8;
      }
    }
    const matte = new THREE.DataTexture(cutoffs, width, views.length, THREE.RGBAFormat);
    matte.needsUpdate = true;
    return matte;
  }

  function makeMaterial(THREE, texture, { width, height, views }, silhouettes, matte, neckline, skinSwatches, headDrop) {
    const shaderVec = values => `vec2(${values.map(v => Number(v).toFixed(7)).join(', ')})`;
    const projection = (i, strict = false) => {
      const { bounds, pivot, scale, axis } = views[i];
      const x = i === 1 || i === 2 ? 'mirrored' : 'localPosition.x';
      const skinView = skinSwatches?.[i] ? i : 0;
      const swatch = views[skinView].pivot.map((point, j) => point + (skinSwatches?.[skinView]?.[j] ?? 0));
      return `projectReference(vec3(${x}, localPosition.y - ${PIVOT.toFixed(2)}, localPosition.z),
        ${shaderVec(axis)}, ${shaderVec(pivot)}, ${scale.toFixed(1)},
        ${shaderVec([bounds[0] + 2, bounds[1] + 2])}, ${shaderVec([bounds[2] - 2, bounds[3] - 2])},
        abs(yaw - ${[0, Math.PI / 4, Math.PI / 2, Math.PI][i].toFixed(7)}), backdrops[${i}],
        ${((i + .5) / views.length).toFixed(3)}, ${shaderVec(swatch)}, ${skinView !== i}, ${strict})`;
    };
    return new THREE.ShaderMaterial({
      toneMapped: false,
      uniforms: { atlas: { value: texture }, cameraLocal: { value: new THREE.Vector3(0, PIVOT, -1) },
        onBody: { value: false }, necklineMatte: { value: neckline },
        nativeNeck: { value: false }, neckToPortrait: { value: new THREE.Matrix4() },
        silhouetteMatte: { value: matte },
        backdrops: { value: silhouettes.map(({ backdrop }) => new THREE.Color(backdrop)) } },
      vertexShader: `
        uniform bool onBody;
        uniform bool nativeNeck;
        uniform mat4 neckToPortrait;
        varying vec3 localPosition;
        varying vec3 localNormal;
        varying float throatClearance;
        vec3 neckRing(float y) {
          // Radii and depth center of the real seated neck, in avatar space.
          // The top influence eases out above the final physical ring.
          if (y < 1.234) return mix(vec3(0.060, 0.053, 0.006), vec3(0.058, 0.052, 0.005),
            smoothstep(1.212, 1.234, y));
          if (y < 1.260) return mix(vec3(0.058, 0.052, 0.005), vec3(0.054, 0.048, 0.004),
            (y - 1.234) / 0.026);
          if (y < 1.286) return mix(vec3(0.054, 0.048, 0.004), vec3(0.043, 0.040, 0.003),
            (y - 1.260) / 0.026);
          return mix(vec3(0.043, 0.040, 0.003), vec3(0.025, 0.025, 0.003),
            clamp((y - 1.286) / 0.023, 0.0, 1.0));
        }
        void main() {
          localPosition = nativeNeck ? (neckToPortrait * vec4(position, 1.0)).xyz : position;
          localNormal = nativeNeck ? normalize(mat3(neckToPortrait) * normal) : normal;
          throatClearance = 1.0;
          vec3 fitted = position;
          if (onBody && !nativeNeck && ${!!neckline}) {
            // Bring the photographed throat into the real neck volume while
            // retaining the jaw and hair that fall outside the throat.
            float blend = (1.0 - smoothstep(1.216, 1.307, position.y))
              * (1.0 - smoothstep(0.074, 0.112, abs(position.x)))
              * (1.0 - smoothstep(0.043, 0.086, position.z));
            if (${!!skinSwatches?.[3]}) {
              float nape = (1.0 - smoothstep(1.252, 1.330, position.y))
                * smoothstep(-0.095, -0.038, position.z);
              blend = max(blend, nape);
            }
            fitted.x *= 1.0 - 0.41 * blend;
            fitted.z = (position.z - 0.005) * (1.0 - 0.28 * blend) + 0.005;
            fitted.y -= 0.007 * blend;
            if (${!!skinSwatches?.[3]}) {
              // The short-hair rear matte can expose the old bust's wider
              // bottom edge. Join it to the real neck ring without moving UVs.
              float rear = smoothstep(-0.075, -0.020, position.z);
              float fit = rear * (1.0 - smoothstep(1.303, 1.367, position.y));
              float nativeY = 1.158 + (fitted.y - ${headDrop.toFixed(3)} - 1.158)
                / ${(1 - headDrop / NECK_LENGTH).toFixed(7)};
              vec3 ring = neckRing(min(nativeY, 1.304));
              vec2 center = vec2(0.0, ring.z);
              vec2 radial = fitted.xz - center;
              float overflow = max(1.0, length(radial / (ring.xy + 0.001)));
              fitted.xz = center + mix(radial, radial / overflow, fit);
            }
            // Keep the photo throat inside the actual collar after head drop.
            // Side and rear hair still follows its registered source edge.
            float front = 1.0 - smoothstep(-0.024, 0.002, fitted.z);
            float center = 1.0 - smoothstep(0.065, 0.085, abs(fitted.x));
            throatClearance = fitted.y - ${headDrop.toFixed(3)}
              - mix(1.090, 1.182, front * center);
          } else if (onBody && ${!neckline}) {
            // Preserve the calibrated photo coordinates. Only reshape the
            // lower rings, where a reference bust would overhang the rig.
            float blend = 1.0 - smoothstep(1.170, 1.240, position.y);
            float radial = length(vec2(position.x, (position.z + 0.012) * 0.85));
            float shoulder = smoothstep(0.045, 0.145, radial);
            fitted.x *= 1.0 - blend * (0.18 + 0.36 * shoulder);
            fitted.z = (position.z + 0.012) * (1.0 - blend * (0.19 + 0.30 * shoulder)) - 0.012;
            fitted.y -= blend * (0.024 + 0.039 * shoulder);
          }
          gl_Position = projectionMatrix * modelViewMatrix * vec4(fitted, 1.0);
        }`,
      fragmentShader: `
        uniform sampler2D atlas;
        uniform sampler2D silhouetteMatte;
        uniform sampler2D necklineMatte;
        uniform vec3 cameraLocal;
        uniform bool onBody;
        uniform bool nativeNeck;
        uniform vec3 backdrops[4];
        varying vec3 localPosition;
        varying vec3 localNormal;
        varying float throatClearance;
        vec3 samplePhoto(vec2 pixel) {
          return texture2D(atlas, vec2(pixel.x / ${width.toFixed(1)}, 1.0 - pixel.y / ${height.toFixed(1)})).rgb;
        }
        float necklineRow(float x, float matteRow) {
          vec2 bytes = 255.0 * texture2D(necklineMatte,
            vec2((x + 0.5) / ${width.toFixed(1)}, matteRow)).rg;
          return dot(bytes, vec2(1.0, 256.0));
        }
        vec4 projectReference(vec3 p, vec2 axis, vec2 pivot, float scale, vec2 low, vec2 high,
            float angleOffset, vec3 backdrop, float matteRow, vec2 swatch, bool hiddenSkin, bool strict) {
          vec3 right = vec3(axis.x, 0.0, axis.y);
          float radius = max(length(cameraLocal.xz), 0.65);
          vec3 opticalCenter = vec3(-axis.y * radius, cameraLocal.y - ${PIVOT.toFixed(2)}, axis.x * radius);
          vec3 direction = normalize(opticalCenter);
          vec3 up = cross(direction, right);
          float distance = length(opticalCenter);
          float perspective = distance / max(0.35, distance - dot(direction, p));
          vec2 ortho = pivot + scale * vec2(dot(right, p), -p.y);
          vec2 aligned = pivot + scale * perspective * vec2(dot(right, p), -dot(up, p));
          // The reference stops at the chest. Fade into the unprojected
          // coordinates there instead of stretching its last source row.
          float sweaterEdge = smoothstep(high.y - 42.0, high.y - 13.0, aligned.y);
          aligned.y = mix(aligned.y, ortho.y, sweaterEdge);
          // At a photographed angle perspective calibration removes the chin
          // seam. Further away it would key out visible parts of the relief.
          float nearAnchor = 1.0 - smoothstep(0.045, 0.21, angleOffset);
          vec2 pixel = clamp(mix(ortho, aligned, nearAnchor), low, high);
          if (nativeNeck && ${!!neckline}) {
            // Continue the reference skin onto the actual rig neck. The
            // registered cutoff prevents any source fabric returning here.
            pixel.x = mix(pivot.x, pixel.x, 0.94);
            pixel.y = min(pixel.y, necklineRow(pixel.x, matteRow) - 7.0);
            vec3 anchor = (samplePhoto(swatch - vec2(3.0, 0.0)) + samplePhoto(swatch)
              + samplePhoto(swatch + vec2(3.0, 0.0))) / 3.0;
            vec3 skin = samplePhoto(pixel);
            float difference = length(skin - anchor) / max(0.055, length(anchor));
            float confidence = 1.0 - smoothstep(0.29, 0.71, difference);
            float baseFade = 0.66 * (1.0 - smoothstep(1.156, 1.264, localPosition.y));
            return vec4(mix(anchor, skin, hiddenSkin ? 0.0 : confidence * (1.0 - baseFade)), 1.0);
          }
          vec3 photographic = samplePhoto(pixel);
          // Key out the studio backdrop and prevent sampling the next panel.
          // Premultiplication keeps the keyed edge out of inter-view blends.
          float backdropDistance = length(photographic - backdrop);
          float coverage = smoothstep(0.055, 0.116, backdropDistance);
          if (${!!matte}) {
            vec4 edgeBytes = 255.0 * texture2D(silhouetteMatte,
              vec2((pixel.y + 0.5) / ${height.toFixed(1)}, matteRow));
            float left = dot(edgeBytes.rg, vec2(1.0, 256.0));
            float right = dot(edgeBytes.ba, vec2(1.0, 256.0));
            float margin = min(pixel.x - left, right - pixel.x);
            // Loose locks can enclose genuine air beside the jaw and neck.
            // Near the crown keep fine highlights even if they match the key.
            float lowerFace = 1.0 - smoothstep(-0.04, 0.01, p.y);
            float interior = smoothstep(1.0, 3.0, margin)
              * mix(1.0, smoothstep(0.008, 0.023, backdropDistance), lowerFace);
            coverage = max(coverage, interior);
            if (strict) coverage *= smoothstep(-2.0, 0.0, margin);
          }
          if (!strict && coverage < 0.95 && nearAnchor < 0.98) {
            vec3 interior = samplePhoto(vec2(mix(pixel.x, pivot.x, 0.20), pixel.y));
            photographic = mix(photographic, interior, (1.0 - coverage) * (1.0 - nearAnchor));
            coverage = max(coverage, 1.0 - nearAnchor);
          }
          if (onBody && ${!!neckline}) {
            float cutoff = necklineRow(pixel.x, matteRow);
            coverage *= 1.0 - smoothstep(cutoff - 1.0, cutoff + 1.0, pixel.y);
          }
          return vec4(photographic * coverage, coverage);
        }
        void main() {
          if (throatClearance < 0.0) discard;
          float mirrored = cameraLocal.x < 0.0 ? -localPosition.x : localPosition.x;
          float yaw = atan(abs(cameraLocal.x), -cameraLocal.z);
          vec4 paint;
          if (yaw < 0.7853982) {
            paint = mix(${projection(0)}, ${projection(1)}, smoothstep(0.376, 0.409, yaw));
          } else if (yaw < 1.5707963) {
            paint = mix(${projection(1)}, ${projection(2)}, smoothstep(1.160, 1.196, yaw));
          } else {
            paint = mix(${projection(2)}, ${projection(3)}, smoothstep(2.226, 2.27, yaw));
            // At a rear oblique angle, one photo alone would paint a tangent
            // face by repeating the last visible texel across the surface.
            // Give each surface its facing, valid source. Retain the exact
            // calibrated photo near the side and rear anchor cameras.
            float anchorFade = smoothstep(1.59, 1.77, yaw) * (1.0 - smoothstep(2.94, 3.12, yaw));
            if (anchorFade > 0.0) {
              vec3 normalLocal = normalize(localNormal);
              float azimuth = smoothstep(1.5707963, 3.1415927, yaw);
              float profileWeight = (1.0 - azimuth) * pow(max(0.0, normalLocal.x * sign(cameraLocal.x)), 2.0);
              float rearWeight = azimuth * pow(max(0.0, normalLocal.z), 2.0);
              vec4 weighted = ${projection(2, true)} * profileWeight + ${projection(3, true)} * rearWeight;
              if (weighted.a > 0.0001) {
                float blend = anchorFade * smoothstep(0.005, 0.09, weighted.a);
                paint = mix(paint, vec4(weighted.rgb / weighted.a, 1.0), blend);
              }
            }
          }
          if (paint.a < 0.43) discard;
          gl_FragColor = vec4(paint.rgb / paint.a, 1.0);
          #include <colorspace_fragment>
        }`,
    });
  }

  function create(THREE, image, registration = CAMILLE, profileId) {
    const spec = validateRegistration(registration);
    const { naturalWidth, naturalHeight } = image ?? {};
    if (!spec || ![naturalWidth, naturalHeight].every(size => Number.isInteger(size)
        && size > 0 && size <= MAX_DIMENSION)
        || Math.abs(naturalWidth / naturalHeight - spec.width / spec.height) > .01) return null;
    let silhouettes;
    try { silhouettes = readSilhouettes(image, spec); }
    catch { return null; }
    if (!silhouettes) return null;
    const geometry = makeRelief(THREE, silhouettes, spec);
    if (!geometry) return null;
    const texture = new THREE.Texture(image);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 4;
    texture.needsUpdate = true;
    const camille = registration === CAMILLE;
    const matte = camille ? null : makeSilhouetteMatte(THREE, spec.height, silhouettes);
    const neckline = Object.hasOwn(NECKLINES, profileId) ? makeNecklineMatte(THREE, spec, NECKLINES[profileId]) : null;
    const headDrop = neckline ? HEAD_DROP[profileId] : 0;
    const material = makeMaterial(THREE, texture, spec, silhouettes, matte, neckline, neckline && NECK_SKIN[profileId], headDrop);
    const neckBindings = new Set();
    let disposed = false;
    function viewOf(portrait, camera, localCamera, onBody, nativeNeck) {
      camera.getWorldPosition(localCamera);
      portrait.worldToLocal(localCamera);
      material.uniforms.cameraLocal.value.copy(localCamera);
      material.uniforms.onBody.value = onBody;
      material.uniforms.nativeNeck.value = nativeNeck;
      // Another avatar may use this material in the same render pass.
      material.uniformsNeedUpdate = true;
    }
    return {
      hasNativeNeck: neckline !== null,
      instantiate({ onBody = false } = {}) {
        if (disposed) return null;
        const model = new THREE.Group();
        model.name = camille ? 'Camille · calibrated reference relief' : 'Calibrated portrait relief';
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = camille ? 'Camille relief' : 'Portrait relief';
        mesh.raycast = () => {};
        mesh.userData.avatarDetail = 'reference-relief';
        mesh.position.y = onBody === true ? -headDrop : 0;
        model.userData.headOffsetY = mesh.position.y;
        const localCamera = new THREE.Vector3();
        mesh.onBeforeRender = (_renderer, _scene, camera) => {
          viewOf(mesh, camera, localCamera, onBody === true, false);
        };
        model.add(mesh);
        return model;
      },
      bindNativeNeck(model, neck) {
        const portrait = model?.children[0];
        if (disposed || !neckline || portrait?.material !== material || !neck?.isMesh
            || neck.material === material) return null;
        const previousMaterial = neck.material, previousRender = neck.onBeforeRender;
        const previousY = neck.position.y, previousScaleY = neck.scale.y;
        const drop = -portrait.position.y;
        neck.scale.y = previousScaleY * (1 - drop / NECK_LENGTH);
        neck.position.y = previousY + NECK_BASE * (previousScaleY - neck.scale.y);
        const localCamera = new THREE.Vector3();
        const render = (...args) => {
          previousRender.apply(neck, args);
          viewOf(portrait, args[2], localCamera, true, true);
          material.uniforms.neckToPortrait.value.copy(portrait.matrixWorld).invert().multiply(neck.matrixWorld);
        };
        neck.material = material;
        neck.onBeforeRender = render;
        const unbind = () => {
          neckBindings.delete(unbind);
          if (neck.material === material) neck.material = previousMaterial;
          if (neck.onBeforeRender === render) {
            neck.onBeforeRender = previousRender;
            neck.position.y = previousY;
            neck.scale.y = previousScaleY;
          }
        };
        neckBindings.add(unbind);
        return unbind;
      },
      // Call after detaching the instances. Instance transforms own no GPU data.
      dispose() {
        if (disposed) return;
        disposed = true;
        for (const unbind of neckBindings) unbind();
        geometry.dispose();
        material.dispose();
        matte?.dispose();
        neckline?.dispose();
        texture.dispose();
      },
    };
  }

  window.FactoryPortraitModel = Object.freeze({ create });
  window.createFactoryCamille = (THREE, image) => create(THREE, image)?.instantiate() ?? null;
})();
