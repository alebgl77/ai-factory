/* SPDX-License-Identifier: MIT */
// A clothed, articulated body in the avatar's local frame: floor y=0, forward −Z.
(() => {
  'use strict';

  const variants = Object.freeze([
    { id: 'overshirt', label: 'Surchemise marine', presentation: 'masculine',
      top: '#283e55', trim: '#213349', pants: '#aa9374', shoes: '#dcd6ca', shoulder: .195, rolled: true },
    { id: 'hoodie', label: 'Hoodie graphite', presentation: 'masculine',
      top: '#424852', trim: '#373d46', pants: '#334053', shoes: '#a9b3ba', shoulder: .199, hood: true },
    { id: 'knit', label: 'Maille prune', presentation: 'feminine',
      top: '#79598b', trim: '#654a77', pants: '#3d3747', shoes: '#e9dfcf', shoulder: .177,
      rolled: true },
    { id: 'bomber', label: 'Veste sauge', presentation: 'feminine',
      top: '#728978', trim: '#556b5e', pants: '#d6c9b4', shoes: '#e1ded3', shoulder: .184, wide: true },
    { id: 'blazer', label: 'Blazer ardoise', presentation: 'feminine',
      top: '#455977', trim: '#35465f', pants: '#343644', shoes: '#663549', shoulder: .181, flats: true },
  ].map(Object.freeze));

  // Costumes belong to the fictional cast, independent of live identity.
  const demoStyles = [
    'knit', 'overshirt', 'knit', 'hoodie', 'bomber', 'hoodie',
    'blazer', 'overshirt', 'bomber', 'overshirt', 'blazer', 'overshirt',
  ];
  const neutral = Object.freeze({ id: 'neutral', label: 'Tenue neutre', presentation: 'neutral',
    top: '#536375', trim: '#404a58', pants: '#363d4a', shoes: '#c2bdb4', shoulder: .188, accent: true });
  const costumes = new Map([...variants, neutral].map(style => [style.id, style]));
  const shared = new WeakMap();
  const modes = new Set(['seated', 'walk', 'stand', 'request', 'listen', 'reply']);
  const noRaycast = () => {};
  const sides = 14;
  const torsoSides = 24;
  const angles = Array.from({ length: sides }, (_, side) => {
    const angle = side * 2 * Math.PI / sides;
    return [Math.cos(angle), Math.sin(angle)];
  });

  // Rows straddle the bend, with closer spacing for the cuff and small tension folds.
  const sleeve = [
    [0, .057, .062], [.16, .060, .057], [.26, .056, .054], [.37, .051, .050],
    [.46, .049, .047], [.53, .048, .048], [.60, .049, .047], [.69, .048, .047],
    [.745, .046, .045], [.78, .044, .043], [.81, .040, .040], [.90, .037, .036],
    [.96, .037, .035], [1, .033, .033],
  ];
  const rolledSleeve = sleeve.slice(0, 8).concat([
    [.745, .049, .047], [.78, .048, .046], [.81, .037, .036], [.90, .034, .033],
    [.96, .030, .029], [1, .029, .028],
  ]);
  const trouser = [
    [0, .074, .078], [.10, .081, .084], [.22, .077, .080], [.36, .070, .072],
    [.45, .066, .067], [.52, .066, .066], [.59, .066, .066], [.68, .063, .063],
    [.79, .060, .060], [.88, .055, .056], [.95, .052, .053], [1, .052, .053],
  ];

  function tubeIndices(rows, count) {
    const index = [];
    const first = rows * count, last = first - count;
    for (let side = 0; side < count; side++) index.push(first, side, (side + 1) % count);
    for (let row = 0; row < rows - 1; row++) {
      for (let side = 0; side < count; side++) {
        const a = row * count + side;
        const b = row * count + (side + 1) % count;
        index.push(a, a + count, b, b, a + count, b + count);
      }
    }
    for (let side = 0; side < count; side++) {
      index.push(first + 1, last + (side + 1) % count, last + side);
    }
    return index;
  }

  function tubePoints(rows, getPoint, count) {
    const points = [];
    for (const row of rows) for (let side = 0; side < count; side++) {
      const angle = side * 2 * Math.PI / count;
      points.push(...getPoint(row, Math.cos(angle), Math.sin(angle)));
    }
    points.push(...getPoint(rows[0], 0, 0), ...getPoint(rows[rows.length - 1], 0, 0));
    return points;
  }

  function staticTube(THREE, rows, getPoint, count = torsoSides) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(tubePoints(rows, getPoint, count), 3));
    geometry.setIndex(tubeIndices(rows.length, count));
    geometry.computeVertexNormals();
    return geometry;
  }

  const shape = (x, power) => Math.sign(x) * Math.pow(Math.abs(x), power);
  const xzSection = ([y, width, depth, z], cos, sin) =>
    [shape(cos, .87) * width, y, z + shape(sin, .87) * depth];

  function garmentRows(style) {
    const shoulder = style.shoulder / .195;
    const fitted = style.id === 'blazer' || style.id === 'knit';
    const loose = style.hood || style.wide;
    return [
      [-.264, .172, .115, .012], [-.246, .175, .117, .012],
      [-.185, .164, .113, .012], [-.113, (fitted ? .155 : .162) * shoulder, .105, .012],
      [-.025, (fitted ? .151 : .164) * shoulder, .108, .009],
      [.067, .175 * shoulder, .112, .006], [.158, .195 * shoulder, loose ? .119 : .109, .007],
      [.208, .189 * shoulder, .099, .011], [.236, .151 * shoulder, .081, .013],
      [.255, .098, .066, .011], [.264, .067, .052, .010],
    ];
  }

  function garmentTopology(THREE, rows) {
    // The holes are cut from the torso bands, leaving a 14-point boundary at each shoulder.
    const low = 5, high = 8, half = torsoSides / 2, width = 2;
    const wrap = col => (col + torsoSides) % torsoSides;
    const vertex = (row, col) => row * torsoSides + wrap(col);
    const torsoIndex = tubeIndices(rows.length, torsoSides);
    const cap = torsoSides * 3;
    const torso = torsoIndex.slice(0, cap);
    for (let row = 0; row < rows.length - 1; row++) for (let col = 0; col < torsoSides; col++) {
      const side = (col + width) % half;
      if (row >= low && row < high && side < 2 * width) continue;
      const offset = cap + 6 * (row * torsoSides + col);
      torso.push(...torsoIndex.slice(offset, offset + 6));
    }
    torso.push(...torsoIndex.slice(-cap));
    const restVertices = [...new Set(torso)].sort((a, b) => a - b);
    const remap = new Map(restVertices.map((original, packed) => [original, packed]));
    const cloth = torso.map(index => remap.get(index)), forearms = [];
    let vertexCount = restVertices.length;
    const point = tubePoints(rows, xzSection, torsoSides);
    const arms = [-1, 1].map(side => {
      const center = side > 0 ? 0 : half;
      const a = center - width, b = center + width;
      const perimeter = [];
      for (let col = a; col <= b; col++) perimeter.push(vertex(low, col));
      for (let row = low + 1; row <= high; row++) perimeter.push(vertex(row, b));
      for (let col = b - 1; col >= a; col--) perimeter.push(vertex(high, col));
      for (let row = high - 1; row > low; row--) perimeter.push(vertex(row, a));
      const mid = (rows[low][0] + rows[high][0]) / 2;
      const angles = perimeter.map(index => {
        const height = (point[index * 3 + 1] - mid) / .0845;
        const depth = side * (.011 - point[index * 3 + 2]) / .060;
        const length = Math.hypot(height, depth);
        return [height / length, depth / length];
      });
      const offset = vertexCount;
      const rim = perimeter.map(index => remap.get(index));
      let previous = rim;
      for (let row = 0; row < sleeve.length; row++) {
        const base = offset + row * sides;
        const index = row <= 9 ? cloth : forearms;
        const current = angles.map((_, col) => base + col);
        for (let col = 0; col < sides; col++) {
          const next = (col + 1) % sides;
          index.push(previous[col], current[col], previous[next], previous[next], current[col], current[next]);
        }
        previous = current;
      }
      const wrist = offset + sleeve.length * sides;
      for (let col = 0; col < sides; col++) forearms.push(wrist, previous[(col + 1) % sides], previous[col]);
      vertexCount = wrist + 1;
      return { side, angles, rim, offset, wrist };
    });
    return { restVertices, vertexCount, arms, clothIndexCount: cloth.length,
      index: new THREE.Uint16BufferAttribute(cloth.concat(forearms), 1) };
  }

  function section(rows, y) {
    let index = rows.findIndex(row => row[0] >= y);
    if (index < 0) index = rows.length - 1;
    const [lowerY, lowerW, lowerD, lowerZ] = rows[Math.max(0, index - 1)];
    const [upperY, upperW, upperD, upperZ] = rows[index];
    const blend = upperY === lowerY ? 0 : (y - lowerY) / (upperY - lowerY);
    return [lowerW + (upperW - lowerW) * blend, lowerD + (upperD - lowerD) * blend,
      lowerZ + (upperZ - lowerZ) * blend];
  }

  function crossSection(fraction) {
    const across = Math.min(.995, Math.abs(fraction));
    return Math.pow(1 - Math.pow(across, 2 / .87), .87 / 2);
  }

  function front(rows, y, x, lift = .002) {
    const [width, depth, offset] = section(rows, y);
    return offset - depth * crossSection(x / width) - lift;
  }

  // Fit small garment panels to the same torso sections rather than layering spherical bibs.
  function panel(THREE, rows, outline, lift = .003) {
    const points = [], index = [], columns = 6;
    const sections = [];
    for (let index = 0; index < outline.length - 1; index++) {
      const [y, left, right] = outline[index], [nextY, nextLeft, nextRight] = outline[index + 1];
      sections.push(outline[index]);
      for (const [height] of rows) if (height > y && height < nextY) {
        const t = (height - y) / (nextY - y);
        sections.push([height, left + t * (nextLeft - left), right + t * (nextRight - right)]);
      }
    }
    sections.push(outline[outline.length - 1]);
    for (const [y, left, right] of sections) for (let col = 0; col <= columns; col++) {
      const x = left + (right - left) * col / columns;
      points.push(x, y, front(rows, y, x, lift));
    }
    for (let row = 0; row < sections.length - 1; row++) for (let col = 0; col < columns; col++) {
      const a = row * (columns + 1) + col, b = a + columns + 1;
      index.push(a, b, a + 1, a + 1, b, b + 1);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    geometry.setIndex(index);
    geometry.computeVertexNormals();
    return geometry;
  }

  function smallDetails(THREE, ellipsoids) {
    const sphere = new THREE.SphereGeometry(1, 8, 6);
    const points = [], index = [];
    const { position } = sphere.attributes;
    for (const [x, y, z, sx, sy, sz] of ellipsoids) {
      const base = points.length / 3;
      for (let i = 0; i < position.count; i++) points.push(
        x + position.getX(i) * sx, y + position.getY(i) * sy, z + position.getZ(i) * sz,
      );
      for (const i of sphere.index.array) index.push(base + i);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
    geometry.setIndex(index);
    geometry.computeVertexNormals();
    return geometry;
  }

  function resources(THREE) {
    if (shared.has(THREE)) return shared.get(THREE);
    const material = (color, roughness = .9) => new THREE.MeshStandardMaterial({ color, roughness });
    const empty = new THREE.BufferGeometry();
    const garment = garmentTopology(THREE, garmentRows(neutral));
    const wardrobe = new Map([...costumes.values()].map(style => {
      const rows = garmentRows(style);
      const points = tubePoints(rows, xzSection, torsoSides);
      const torso = new Float32Array(garment.restVertices.length * 3);
      garment.restVertices.forEach((index, packed) => {
        torso[packed * 3] = points[index * 3];
        torso[packed * 3 + 1] = points[index * 3 + 1];
        torso[packed * 3 + 2] = points[index * 3 + 2];
      });
      for (const { side, rim, angles } of garment.arms) for (let i = 0; i < rim.length; i++) {
        const index = rim[i] * 3, [cos, sin] = angles[i];
        // Round the cutout on the existing torso, so the welded border follows an oval armhole.
        const y = .28 * torso[index + 1] + .72 * (.1515 + .0845 * cos);
        const z = .28 * torso[index + 2] + .72 * (.011 - side * .062 * sin);
        const [width, depth, center] = section(rows, y);
        torso[index] = side * width * crossSection((z - center) / depth);
        torso[index + 1] = y;
        torso[index + 2] = z;
      }
      const overshirt = style.id === 'overshirt', bomber = style.id === 'bomber';
      const tee = overshirt || style.flats || bomber;
      const opening = style.flats ? [[-.066, .002], [.035, .022], [.10, .039], [.158, .052],
        [.208, .064], [.236, .067], [.253, .052]] : bomber ? [[.146, .010], [.188, .029],
        [.226, .052], [.251, .050]] : [[-.258, .024], [-.218, .026], [-.113, .026], [-.025, .028],
        [.067, .031], [.158, .041], [.208, .055], [.236, .060], [.255, .051]];
      const collarOutline = style.flats
        ? [[-.04, .015, .025], [.053, .029, .050], [.13, .044, .085], [.167, .055, .082],
          [.186, .051, .101], [.228, .057, .083], [.253, .051, .065]]
        : [[.177, .062, .081], [.201, .054, .081], [.229, .051, .082], [.251, .047, .070]];
      const cuff = [[-.262, -.159, .159], [-.235, -.159, .159]];
      const narrow = overshirt ? [[-.258, .026, .036], [-.218, .027, .037], [-.113, .027, .037],
        [-.025, .028, .038], [.067, .032, .044], [.158, .042, .052], [.208, .056, .068]]
        : [[-.258, -.004, .004], [-.218, -.004, .004], [-.113, -.004, .004],
          [-.025, -.004, .004], [.067, -.004, .004], [.158, -.004, .004], [.229, -.004, .004]];
      return [style.id, {
        top: material(style.top), trim: material(style.trim), pants: material(style.pants),
        shoes: material(style.shoes), torso, rows,
        shirt: tee ? panel(THREE, rows, opening.map(([y, x]) => [y, -x, x])) : empty,
        lapels: overshirt || style.flats ? [-1, 1].map(side => panel(THREE, rows, collarOutline.map(
          ([y, inner, outer]) => side < 0 ? [y, -outer, -inner] : [y, inner, outer]), .006)) : [empty, empty],
        edge: overshirt || bomber ? panel(THREE, rows, narrow, .005) : empty,
        hem: style.hood || bomber ? panel(THREE, rows, cuff, .005) : empty,
        buttons: overshirt ? smallDetails(THREE, [-.243, -.165, -.083, -.001, .081].map(y =>
          [.031, y, front(rows, y, .031, .009), .0042, .0042, .0028])) : empty,
        pocket: style.hood ? panel(THREE, rows, [[-.237, -.091, .091], [-.216, -.101, .101],
          [-.163, -.091, .091], [-.128, -.061, .061]], .008) : empty,
      }];
    }));
    const shoeSection = ([z, width, height, y], cos, sin) =>
      [shape(cos, .74) * width, y - shape(sin, .7) * height, z];
    const shoe = (flat = false) => staticTube(THREE, [
      [-.161, .018, .006, -.044], [-.151, .044, .014, -.039], [-.125, .056, .021, -.032],
      [-.083, .058, flat ? .021 : .035, flat ? -.032 : -.018],
      [-.034, .051, flat ? .023 : .042, flat ? -.029 : -.009],
      [.008, .047, flat ? .025 : .039, flat ? -.028 : -.011],
      [.045, .042, flat ? .022 : .033, flat ? -.031 : -.017], [.053, .023, .015, -.028],
    ], shoeSection, 20);
    const value = {
      round: new THREE.SphereGeometry(1, 16, 12),
      collar: staticTube(THREE, [[.248, .078, .061, .003], [.257, .077, .060, .004],
        [.273, .067, .052, .005], [.283, .063, .048, .005]], xzSection),
      neck: staticTube(THREE, [[.258, .055, .050, .007], [.287, .057, .052, .007],
        [.312, .060, .053, .006], [.334, .058, .052, .005], [.360, .054, .048, .004],
        [.386, .043, .040, .003], [.409, .025, .025, .003]], xzSection),
      waist: staticTube(THREE, [[-.101, .053, .043, .022], [-.063, .128, .094, .019],
        [.012, .158, .100, .017], [.064, .145, .091, .014], [.122, .124, .080, .011]], xzSection),
      palm: staticTube(THREE, [[-.094, .013, .010, -.011], [-.087, .024, .015, -.009],
        [-.068, .031, .019, -.006], [-.047, .034, .023, -.004], [-.025, .032, .023, 0],
        [-.008, .027, .020, 0], [.016, .025, .018, .001]], xzSection, 18),
      sneaker: shoe(), flat: shoe(true),
      sole: staticTube(THREE, [[-.164, .021, .009, -.060], [-.153, .046, .010, -.063],
        [-.126, .059, .011, -.064], [-.082, .061, .011, -.064], [-.028, .054, .011, -.064],
        [.017, .050, .011, -.063], [.049, .042, .011, -.062], [.056, .022, .009, -.060]], shoeSection, 20),
      laces: smallDetails(THREE, [-.069, -.051, -.033].map(z => [0, .022, z, .030, .0025, .0038])),
      trouserIndex: new THREE.Uint16BufferAttribute(tubeIndices(trouser.length, sides), 1),
      cream: material('#e1d9ce'), dark: material('#302d36'), fastener: material('#aaa394', .68),
      empty, wardrobe, garment,
    };
    shared.set(THREE, value);
    return value;
  }

  function mesh(THREE, parent, name, geometry, material, x = 0, y = 0, z = 0, sx = 1, sy = 1, sz = 1) {
    const node = new THREE.Mesh(geometry, material);
    node.name = name;
    node.position.set(x, y, z);
    node.scale.set(sx, sy, sz);
    node.userData.avatarDetail = name;
    node.raycast = noRaycast;
    parent.add(node);
    return node;
  }

  function makeCloth(THREE, parent, name, vertexCount, index, material, boundCenter) {
    const geometry = new THREE.BufferGeometry();
    const vertices = new Float32Array(vertexCount * 3);
    geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3).setUsage(THREE.DynamicDrawUsage));
    geometry.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(vertices.length), 3)
      .setUsage(THREE.DynamicDrawUsage));
    geometry.setIndex(index);
    // All poses fit this local envelope. Avoid walking the buffer for new bounds each frame.
    geometry.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, boundCenter, -.10), .95);
    const node = mesh(THREE, parent, name, geometry, material);
    node.castShadow = true;
    return node;
  }

  function create(THREE, { skinMat, accentMat }) {
    const g = resources(THREE);
    const group = new THREE.Group();
    group.name = 'factory-body-rig';
    const upper = new THREE.Group();
    upper.name = 'upper-body-pose';
    group.add(upper);
    const body = new THREE.Group();
    body.name = 'clothed-torso';
    body.position.y = .9;
    upper.add(body);
    const detail = (name, material, xyz, scale) => mesh(THREE, body, name, g.round, material, ...xyz, ...scale);
    const trunk = makeCloth(THREE, body, 'garment', g.garment.vertexCount, g.garment.index, accentMat, 0);
    mesh(THREE, body, 'neck', g.neck, skinMat);
    const collar = mesh(THREE, body, 'collar', g.collar, g.dark);
    const shirt = mesh(THREE, body, 'inner-tee', g.empty, g.cream);
    const edge = mesh(THREE, body, 'jacket-placket', g.empty, g.dark);
    const hem = mesh(THREE, body, 'garment-hem', g.empty, g.dark);
    const lapels = [-1, 1].map(() => mesh(THREE, body, 'jacket-lapel', g.empty, g.dark));
    const hood = detail('hood', g.dark, [0, .199, .078], [.102, .077, .062]);
    const pocket = mesh(THREE, body, 'kangaroo-pocket', g.empty, g.dark);
    const strings = [-1, 1].map(side => {
      const string = detail('hoodie-drawstring', g.cream, [side * .027, .165, -.112], [.0024, .043, .0025]);
      string.rotation.z = side * -.10;
      return string;
    });
    const buttons = mesh(THREE, body, 'shirt-buttons', g.empty, g.fastener);

    const pelvis = mesh(THREE, group, 'trouser-waist', g.waist, g.dark, 0, .54);
    pelvis.castShadow = true;

    const limbs = g.garment.arms.map(arm => {
      const { side } = arm;
      const hand = new THREE.Group();
      hand.name = side < 0 ? 'left-hand' : 'right-hand';
      upper.add(hand);
      mesh(THREE, hand, 'palm', g.palm, skinMat);
      const thumb = mesh(THREE, hand, 'thumb', g.round, skinMat,
        -side * .027, -.040, -.011, .012, .028, .013);
      thumb.rotation.z = -side * .28;
      const trouserNode = makeCloth(THREE, group, 'continuous-trouser', trouser.length * sides + 2,
        g.trouserIndex, g.dark, .72);
      const shoe = new THREE.Group();
      shoe.name = side < 0 ? 'left-shoe' : 'right-shoe';
      group.add(shoe);
      const vamp = mesh(THREE, shoe, 'shoe-upper', g.sneaker, g.dark);
      const sole = mesh(THREE, shoe, 'shoe-sole', g.sole, g.cream);
      const laces = mesh(THREE, shoe, 'shoelaces', g.laces, g.cream);
      return { side, arm, hand, trouserNode, shoe, vamp, sole, laces };
    });

    const down = new THREE.Vector3(0, -1, 0);
    const start = new THREE.Vector3(), end = new THREE.Vector3(), pole = new THREE.Vector3();
    const direction = new THREE.Vector3(), bend = new THREE.Vector3(), joint = new THREE.Vector3();
    const first = new THREE.Vector3(), second = new THREE.Vector3(), cornerIn = new THREE.Vector3();
    const cornerOut = new THREE.Vector3(), center = new THREE.Vector3(), tangent = new THREE.Vector3();
    const previous = new THREE.Vector3(), across = new THREE.Vector3(), depth = new THREE.Vector3();
    const curvature = new THREE.Vector3(), inward = new THREE.Vector3();
    const outward = new THREE.Vector3(), rotation = new THREE.Quaternion();
    let style, torsoRows, lastMode, lastPhase, headY;

    // A fixed-length two-bone IK chain with an outward elbow and a forward knee.
    function solveJoint(lengthA, lengthB) {
      direction.subVectors(end, start);
      const actual = direction.length();
      direction.multiplyScalar(1 / actual);
      const reach = Math.max(Math.abs(lengthA - lengthB) + .001, Math.min(actual, lengthA + lengthB - .001));
      end.copy(start).addScaledVector(direction, reach);
      const along = (lengthA * lengthA - lengthB * lengthB + reach * reach) / (2 * reach);
      bend.copy(pole).addScaledVector(direction, -pole.dot(direction)).normalize();
      joint.copy(start).addScaledVector(direction, along)
        .addScaledVector(bend, Math.sqrt(Math.max(0, lengthA * lengthA - along * along)));
      first.subVectors(joint, start).multiplyScalar(1 / lengthA);
      second.subVectors(end, joint).multiplyScalar(1 / lengthB);
    }

    // Sweep fabric through a rounded corner and parallel-transport its frame so seams don't flip.
    function drape(values, rows, lengthA, lengthB, round, ease, arm) {
      const total = lengthA + lengthB;
      const before = lengthA - round, after = lengthA + round;
      const offset = arm ? arm.offset : 0;
      const localY = arm ? body.position.y : 0;
      const ringAngles = arm ? arm.angles : angles;
      const tension = 1 - first.dot(second);
      const sinBend = curvature.crossVectors(first, second).length();
      curvature.subVectors(second, first);
      cornerIn.copy(joint).addScaledVector(first, -round);
      cornerOut.copy(joint).addScaledVector(second, round);
      const firstRow = arm ? 2 : 0;
      for (let row = firstRow; row < rows.length; row++) {
        const [fraction, width, thickness] = rows[row];
        const distance = total * fraction;
        let innerLimit = Infinity;
        if (distance <= before) {
          center.copy(start).addScaledVector(first, distance);
          tangent.copy(first);
        } else if (distance >= after) {
          center.copy(joint).addScaledVector(second, distance - lengthA);
          tangent.copy(second);
        } else {
          const t = (distance - before) / (2 * round), inv = 1 - t;
          center.copy(cornerIn).multiplyScalar(inv * inv).addScaledVector(joint, 2 * inv * t)
            .addScaledVector(cornerOut, t * t);
          tangent.copy(first).multiplyScalar(inv).addScaledVector(second, t);
          const speed = tangent.length();
          // Inside a tight bend, keep the inner cloth away from the curve's center of curvature.
          if (sinBend > .001) innerLimit = 1.8 * round * speed ** 3 / sinBend;
          tangent.multiplyScalar(1 / speed);
        }
        if (row > firstRow) across.applyQuaternion(rotation.setFromUnitVectors(previous, tangent));
        else if (arm) {
          outward.set(arm.side, 0, 0);
          across.set(0, 1, 0).applyQuaternion(rotation.setFromUnitVectors(outward, tangent));
        } else across.set(1, 0, 0).addScaledVector(tangent, -tangent.x).normalize();
        depth.crossVectors(across, tangent);
        previous.copy(tangent);

        inward.copy(curvature).addScaledVector(tangent, -curvature.dot(tangent)).normalize();
        const innerX = inward.dot(across), innerZ = inward.dot(depth);
        // Broad tension along the front, with slightly uneven compression around the bend.
        const jointFold = row === 5 ? -.055 : row === 6 ? .040 : row === 7 ? -.022 : 0;
        for (let side = 0; side < sides; side++) {
          const [cos, sin] = ringAngles[side];
          const flare = 1 + ease * fraction + tension * jointFold * (.4 + .6 * Math.abs(sin));
          let x = cos * width * flare, z = sin * thickness * flare;
          const compression = Math.max(0, x * innerX + z * innerZ - innerLimit);
          x -= innerX * compression;
          z -= innerZ * compression;
          const index = (offset + row * sides + side) * 3;
          values[index] = center.x + across.x * x + depth.x * z;
          values[index + 1] = center.y - localY + across.y * x + depth.y * z;
          values[index + 2] = center.z + across.z * x + depth.z * z;
        }
      }
      if (arm) {
        end.toArray(values, arm.wrist * 3);
        values[arm.wrist * 3 + 1] -= localY;
      } else {
        start.toArray(values, values.length - 6);
        end.toArray(values, values.length - 3);
      }
    }

    function upload(geometry) {
      geometry.attributes.position.needsUpdate = true;
      geometry.computeVertexNormals();
    }

    function keepSleeveOutside(values, { offset, side }, from, to) {
      for (let row = from; row <= to; row++) for (let col = 0; col < sides; col++) {
        const index = (offset + row * sides + col) * 3;
        const y = values[index + 1];
        if (y < torsoRows[0][0] || y > torsoRows[torsoRows.length - 1][0]) continue;
        const [width, depth, center] = section(torsoRows, y);
        const around = (values[index + 2] - center) / depth;
        if (Math.abs(around) >= 1) continue;
        const edge = width * crossSection(around) + .003 + .004 * Math.min(row, 3);
        const gap = edge - side * values[index];
        const soft = .006;
        if (gap >= soft) values[index] = side * edge;
        else if (gap > -soft) values[index] += side * (gap + soft) ** 2 / (4 * soft);
      }
    }

    function fitShoulder(values, arm) {
      const { offset, rim, side } = arm;
      // Use the cleared upper arm as the endpoint of both interpolated shoulder bands.
      keepSleeveOutside(values, arm, 2, 6);
      for (let col = 0; col < sides; col++) {
        const a = rim[col] * 3;
        const b = (offset + 2 * sides + col) * 3;
        const c = b + sides * 3;
        const d = (offset + col) * 3;
        const chord = Math.hypot(values[b] - values[a], values[b + 1] - values[a + 1], values[b + 2] - values[a + 2]);
        const tail = Math.hypot(values[c] - values[b], values[c + 1] - values[b + 1], values[c + 2] - values[b + 2]);
        const scale = Math.min(1.65, .85 * chord / Math.max(tail, .001));
        for (let axis = 0; axis < 3; axis++) {
          const start = values[a + axis], end = values[b + axis];
          const outward = axis === 0 ? side * Math.min(.060, Math.max(.022, .52 * chord)) : .30 * (end - start);
          const terminal = (values[c + axis] - end) * scale;
          // Cubic Hermite basis at t=1/3 and t=2/3. Both bands follow the same curve.
          values[d + axis] = (20 * start + 4 * outward + 7 * end - 2 * terminal) / 27;
          values[d + sides * 3 + axis] = (7 * start + 2 * outward + 20 * end - 4 * terminal) / 27;
        }
      }
      keepSleeveOutside(values, arm, 0, 1);
    }

    function setProfile(profileId, demo) {
      const index = demo === true && typeof profileId === 'string' && /^p(?:0[1-9]|1[0-2])$/.test(profileId)
        ? Number(profileId.slice(1)) - 1 : -1;
      const next = costumes.get(demoStyles[index]) || neutral;
      if (style === next) return style;
      style = next;
      const clothes = g.wardrobe.get(style.id);
      torsoRows = clothes.rows;
      const top = style.accent ? accentMat : clothes.top;
      const clothGeometry = trunk.geometry;
      clothGeometry.attributes.position.array.set(clothes.torso);
      clothGeometry.clearGroups();
      if (style.rolled) {
        trunk.material = [top, skinMat];
        const split = g.garment.clothIndexCount;
        clothGeometry.addGroup(0, split, 0);
        clothGeometry.addGroup(split, clothGeometry.index.count - split, 1);
      } else trunk.material = top;
      collar.material = hem.material = hood.material = pocket.material = edge.material = clothes.trim;
      for (const [node, geo] of [[shirt, clothes.shirt], [edge, clothes.edge], [hem, clothes.hem], [buttons, clothes.buttons],
        [pocket, clothes.pocket], [lapels[0], clothes.lapels[0]], [lapels[1], clothes.lapels[1]]]) {
        node.geometry = geo;
        node.visible = geo !== g.empty;
      }
      for (const lapel of lapels) lapel.material = clothes.trim;
      hood.visible = !!style.hood;
      for (const string of strings) string.visible = !!style.hood;
      pelvis.material = clothes.pants;
      for (const { trouserNode, vamp, sole, laces } of limbs) {
        trouserNode.material = clothes.pants;
        vamp.geometry = style.flats ? g.flat : g.sneaker;
        vamp.material = clothes.shoes;
        sole.material = style.flats ? g.dark : g.cream;
        sole.scale.y = style.flats ? .85 : 1;
        sole.position.y = style.flats ? -.009 : 0;
        laces.visible = !style.flats;
      }
      group.userData.outfit = style.id;
      const mode = lastMode;
      lastMode = null;
      pose(mode, lastPhase);
      return style;
    }

    function pose(mode, phase = 0) {
      if (!modes.has(mode)) mode = 'seated';
      phase = mode === 'seated' || mode === 'stand' || mode === 'listen' || !Number.isFinite(phase)
        ? 0 : phase % (2 * Math.PI);
      if (lastMode === mode && lastPhase === phase) return headY;
      lastMode = mode;
      lastPhase = phase;
      const seated = mode === 'seated', walking = mode === 'walk';
      const gesture = mode === 'request' || mode === 'reply';
      const bob = walking ? -.013 * Math.cos(2 * phase) : 0;
      headY = seated ? 1.43 : 1.66 + bob;
      const hipY = seated ? .54 : walking ? .831 + bob : .872;
      upper.position.y = headY - 1.43;
      pelvis.position.y = hipY;

      const clothGeometry = trunk.geometry;
      for (const { side, arm, hand, trouserNode, shoe } of limbs) {
        const stride = walking ? side * Math.sin(phase) : 0;
        const step = walking ? Math.max(0, side * Math.cos(phase)) : 0;
        start.set(side * .083, hipY, .012);
        end.set(side * (seated ? .109 : .097), .076 + .113 * step, seated ? -.42 : -.026 - .219 * stride);
        pole.set(side * .16, 0, -1);
        solveJoint(.414, .407);
        const legGeometry = trouserNode.geometry;
        drape(legGeometry.attributes.position.array, trouser, .414, .407, .155,
          style.wide ? .39 : style.flats ? .13 : .05);
        upload(legGeometry);
        shoe.position.copy(end);
        shoe.rotation.set(.18 * step, side * -.075, 0);

        start.set(side * (style.shoulder - .014), 1.132, .006);
        if (seated) end.set(side * .163, .795, -.343);
        else if (gesture && side > 0) {
          const wave = Math.sin(phase);
          end.set(.27 + .037 * wave, (mode === 'request' ? 1.13 : 1.04) + .037 * Math.cos(phase),
            -.29 - .035 * wave);
        } else if (mode === 'listen') end.set(side * .122, .773, -.215);
        else end.set(side * (style.shoulder + .051), .653 + .023 * Math.abs(stride), .008 + .24 * stride);
        pole.set(side * .32, -.80, .44);
        solveJoint(.262, .248);
        drape(clothGeometry.attributes.position.array, style.rolled ? rolledSleeve : sleeve,
          .262, .248, .112, style.hood ? .04 : 0, arm);
        fitShoulder(clothGeometry.attributes.position.array, arm);
        hand.position.copy(end);
        hand.quaternion.setFromUnitVectors(down, second);
      }
      upload(clothGeometry);
      return headY;
    }

    setProfile(null, false);
    return { group, body, setProfile, pose, get variant() { return style; } };
  }

  window.FactoryCharacter = Object.freeze({ create, variants });
})();
