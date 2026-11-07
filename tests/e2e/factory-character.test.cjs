/* SPDX-License-Identifier: MIT */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const three = import('three');
const window = {};
vm.runInNewContext(fs.readFileSync(require.resolve('../../dashboard/assets/factory-character.js'), 'utf8'), { window });
const { create } = window.FactoryCharacter;

const cast = ['knit', 'overshirt', 'knit', 'hoodie', 'bomber', 'hoodie',
  'blazer', 'overshirt', 'bomber', 'overshirt', 'blazer', 'overshirt'];
const modes = ['seated', 'stand', 'walk', 'request', 'listen', 'reply'];

function checkTube(THREE, geometry) {
  const { position, normal } = geometry.attributes;
  for (const attribute of [position, normal]) assert.ok(attribute.array.every(Number.isFinite));
  const { index } = geometry;
  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  for (let i = 0; i < index.count; i += 3) {
    a.fromBufferAttribute(position, index.array[i]);
    b.fromBufferAttribute(position, index.array[i + 1]);
    c.fromBufferAttribute(position, index.array[i + 2]);
    assert.ok(geometry.boundingSphere.containsPoint(a));
    assert.ok(b.sub(a).cross(c.sub(a)).lengthSq() > 1e-10, 'fabric triangles retain area');
  }
}

test('all 12 outfits and the neutral live rig retain continuous cloth and finite pose anchors', async () => {
  const THREE = await three;
  const skinMat = new THREE.MeshStandardMaterial({ color: '#b3927b' });
  const accentMat = new THREE.MeshStandardMaterial({ color: '#6c7286' });
  const rig = create(THREE, { skinMat, accentMat });
  const garment = rig.body.getObjectByName('garment');
  const trousers = rig.group.getObjectsByProperty('name', 'continuous-trouser');
  const parts = [garment, ...trousers];
  assert.equal(rig.group.getObjectsByProperty('name', 'garment').length, 1);
  assert.equal(parts.length, 3);
  assert.ok(rig.body.getObjectByName('neck'));
  assert.ok(rig.body.getObjectByName('collar'));
  assert.ok(rig.body.getObjectByName('inner-tee'));
  for (const part of parts) {
    assert.ok(part.userData.avatarDetail);
    const edges = new Map();
    const neighbors = Array.from({ length: part.geometry.attributes.position.count }, () => []);
    const { array } = part.geometry.index;
    for (let i = 0; i < array.length; i += 3) {
      for (const [a, b] of [[array[i], array[i + 1]], [array[i + 1], array[i + 2]], [array[i + 2], array[i]]]) {
        const key = a < b ? a + ':' + b : b + ':' + a;
        const [count, winding] = edges.get(key) || [0, 0];
        edges.set(key, [count + 1, winding + Math.sign(b - a)]);
        neighbors[a].push(b);
        neighbors[b].push(a);
      }
    }
    assert.ok([...edges.values()].every(([count, winding]) => count === 2 && winding === 0),
      'closed fabric has opposing triangle edges through each joint');
    const visited = new Set([0]), queue = [0];
    for (const current of queue) for (const next of neighbors[current]) {
      if (visited.has(next)) continue;
      visited.add(next);
      queue.push(next);
    }
    assert.equal(visited.size, neighbors.length, 'the mesh is one welded surface with no unused vertices');
  }

  for (const [i, style] of cast.concat('neutral').entries()) {
    const id = 'p' + String(i + 1).padStart(2, '0');
    assert.equal(rig.setProfile(id, i < 12).id, style);
    assert.equal(garment.material === accentMat, style === 'neutral');
    for (const mode of modes) {
      for (const phase of [0, .62, Math.PI / 2, Math.PI, 3 * Math.PI / 2, NaN]) {
        const head = rig.pose(mode, phase);
        assert.equal(rig.body.parent.position.y, head - 1.43);
        assert.ok(mode === 'seated' ? head === 1.43 : head > 1.64 && head < 1.68);
        for (const part of parts) checkTube(THREE, part.geometry);
        for (const [index, side] of ['left', 'right'].entries()) {
          const shoe = rig.group.getObjectByName(side + '-shoe');
          const hand = rig.group.getObjectByName(side + '-hand');
          const { position: leg } = trousers[index].geometry.attributes;
          const ankle = new THREE.Vector3().fromBufferAttribute(leg, leg.count - 1);
          assert.ok(shoe.position.distanceTo(ankle) < 1e-7, 'trousers follow the shoe anchor');
          const { position: cloth } = garment.geometry.attributes;
          const wrist = hand.position.clone().sub(rig.body.position), point = new THREE.Vector3();
          assert.ok(Array.from({ length: cloth.count }, (_, i) => i).some(i =>
            point.fromBufferAttribute(cloth, i).distanceToSquared(wrist) < 1e-14), 'the garment follows the hand anchor');
          const sole = shoe.getObjectByName('shoe-sole');
          rig.group.updateMatrixWorld(true);
          const foot = new THREE.Box3().setFromObject(sole, true);
          assert.ok(foot.min.y >= -.002, 'the shoe does not penetrate the ground');
        }
      }
    }
  }
  assert.equal(rig.setProfile('p01', false).id, 'neutral');
  assert.equal(rig.setProfile('p01', 'true').id, 'neutral');
  assert.equal(rig.setProfile('p99', true).id, 'neutral');
  assert.equal(rig.pose('unknown', Infinity), 1.43);
});

test('rests without reuploading geometry, shares clothes across actors and isolates moving buffers', async () => {
  const THREE = await three;
  const skinMat = new THREE.MeshStandardMaterial();
  const accentMat = new THREE.MeshStandardMaterial();
  const a = create(THREE, { skinMat, accentMat }), b = create(THREE, { skinMat, accentMat });
  a.setProfile('p02', true);
  b.setProfile('p08', true);
  const garment = a.body.getObjectByName('garment'), other = b.body.getObjectByName('garment');
  assert.equal(garment.material[0], other.material[0]);
  const cloth = garment.geometry;
  assert.notEqual(cloth, other.geometry);
  assert.equal(cloth.index, other.geometry.index);
  assert.deepEqual(cloth.attributes.position.array, other.geometry.attributes.position.array);
  assert.equal(cloth.groups.length, 2);
  assert.equal(cloth.groups.reduce((sum, group) => sum + group.count, 0), cloth.index.count);
  assert.ok(cloth.attributes.position.count <= 650);
  const position = cloth.attributes.position;
  const initialVersion = position.version;
  for (const phase of [1, 2, 10]) a.pose('seated', phase);
  assert.equal(position.version, initialVersion);
  a.pose('walk', .5);
  assert.equal(position.version, initialVersion + 1);
  a.pose('walk', .5);
  assert.equal(position.version, initialVersion + 1);
  a.setProfile('p04', true);
  assert.equal(cloth.groups.length, 0);
  assert.equal(position.version, initialVersion + 2);
});
