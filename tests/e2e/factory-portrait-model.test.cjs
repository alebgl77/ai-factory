/* SPDX-License-Identifier: MIT */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');
const three = import('three');

const source = fs.readFileSync(require.resolve('../../dashboard/assets/camille-model.js'), 'utf8');
const image = { naturalWidth: 512, naturalHeight: 512 };
const registration = {
  width: 512,
  height: 512,
  views: [
    { bounds: [0, 0, 256, 256], pivot: [128, 128], scale: 500 },
    { bounds: [256, 0, 512, 256], pivot: [384, 128], scale: 500 },
    { bounds: [0, 256, 256, 512], pivot: [128, 384], scale: 500 },
    { bounds: [256, 256, 512, 512], pivot: [384, 384], scale: 500 },
  ],
};

function loadModel(readPixels) {
  const document = {
    createElement: () => ({ getContext: () => ({
      drawImage() {},
      getImageData: (_x, _y, width, height) => ({ data: readPixels(width, height) }),
    }) }),
  };
  const window = {};
  vm.runInNewContext(source, { document, window });
  return window;
}

function atlasPixels(width, height, withFace = true) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  pixels.fill(180);
  if (!withFace) return pixels;
  for (let y = 0; y < height; y++) {
    const dy = (y % 256 - 128) / 95;
    if (Math.abs(dy) >= 1) continue;
    const halfWidth = Math.round(Math.sqrt(1 - dy * dy) * 58);
    for (let left = 0; left < width; left += 256) {
      for (let x = left + 128 - halfWidth; x < left + 128 + halfWidth; x++) {
        const i = (y * width + x) * 4;
        pixels[i] = 125;
        pixels[i + 1] = 70;
        pixels[i + 2] = 30;
      }
    }
  }
  return pixels;
}

test('one atlas read builds a real, shared 3D relief with per-actor camera coordinates', async () => {
  const THREE = await three;
  let reads = 0;
  const { FactoryPortraitModel } = loadModel((width, height) => {
    reads++;
    return atlasPixels(width, height);
  });
  const portrait = FactoryPortraitModel.create(THREE, image, registration);
  assert.ok(portrait);
  const first = portrait.instantiate();
  const second = portrait.instantiate({ onBody: true });
  const a = first.children[0], b = second.children[0];
  assert.equal(reads, 1);
  assert.notEqual(first, second);
  assert.equal(a.geometry, b.geometry);
  assert.equal(a.material, b.material);
  assert.equal(a.material.uniforms.atlas.value.image, image);
  assert.equal(a.material.uniforms.backdrops.value[0].getHexString(), 'b4b4b4');
  const matte = a.material.uniforms.silhouetteMatte.value;
  assert.deepEqual([matte.image.width, matte.image.height], [512, 4]);
  assert.deepEqual(Array.from(matte.image.data.slice(4 * 128, 4 * 129)), [70, 0, 185, 0]);
  assert.ok(a.geometry.index.count > 1000);
  a.geometry.computeBoundingBox();
  assert.ok(a.geometry.boundingBox.max.z - a.geometry.boundingBox.min.z > .1);

  const camera = new THREE.PerspectiveCamera();
  camera.position.set(1, 1.43, -2);
  second.position.set(2, 0, 0);
  a.onBeforeRender(null, null, camera);
  assert.deepEqual(a.material.uniforms.cameraLocal.value.toArray(), [1, 1.43, -2]);
  assert.equal(a.material.uniforms.onBody.value, false);
  a.material.uniformsNeedUpdate = false;
  b.onBeforeRender(null, null, camera);
  assert.deepEqual(b.material.uniforms.cameraLocal.value.toArray(), [-1, 1.43, -2]);
  assert.equal(b.material.uniforms.onBody.value, true);
  assert.equal(b.material.uniformsNeedUpdate, true);
  a.onBeforeRender(null, null, camera);
  assert.equal(a.material.uniforms.onBody.value, false);

  const dispose = [];
  a.geometry.addEventListener('dispose', () => dispose.push('geometry'));
  a.material.addEventListener('dispose', () => dispose.push('material'));
  matte.addEventListener('dispose', () => dispose.push('matte'));
  a.material.uniforms.atlas.value.addEventListener('dispose', () => dispose.push('texture'));
  portrait.dispose();
  portrait.dispose();
  assert.deepEqual(dispose, ['geometry', 'material', 'matte', 'texture']);
  assert.equal(portrait.instantiate(), null);
});

test('rejects malformed or unbounded atlas registrations before reading pixels', async () => {
  const THREE = await three;
  let reads = 0;
  const { FactoryPortraitModel } = loadModel(() => { reads++; });
  const options = [
    null,
    { ...registration, width: 8192 },
    { ...registration, backdrop: 'invalid' },
    { ...registration, heightRange: [1.45, 1.65] },
    { ...registration, views: registration.views.slice(0, 3) },
    { ...registration, views: Array(4).fill(registration.views[0]) },
    { ...registration, views: [
      { ...registration.views[0], pivot: [-1, 128] },
      ...registration.views.slice(1),
    ] },
  ];
  for (const option of options) assert.equal(FactoryPortraitModel.create(THREE, image, option), null);
  assert.equal(FactoryPortraitModel.create(THREE, { ...image, naturalHeight: 1024 }, registration), null);
  assert.equal(reads, 0);
});

test('returns a normal fallback on missing silhouette or unreadable atlas', async () => {
  const THREE = await three;
  const blank = loadModel((width, height) => atlasPixels(width, height, false));
  const unreadable = loadModel(() => { throw new Error('Image read denied'); });
  assert.equal(blank.FactoryPortraitModel.create(THREE, image, registration), null);
  assert.equal(unreadable.FactoryPortraitModel.create(THREE, image, registration), null);
  assert.equal(blank.createFactoryCamille(THREE, image), null);
});

test('applies the registered skin crop to the physical neck and restores its materials', async () => {
  const THREE = await three;
  const { FactoryPortraitModel } = loadModel(atlasPixels);
  const resource = FactoryPortraitModel.create(THREE, image, registration, 'p10');
  const head = resource.instantiate({ onBody: true });
  assert.equal(head.userData.headOffsetY, -.090);
  assert.equal(resource.instantiate().userData.headOffsetY, 0);
  const material = head.children[0].material;
  const crop = material.uniforms.necklineMatte.value.image;
  assert.equal(resource.hasNativeNeck, true);
  assert.deepEqual([crop.width, crop.height], [512, 4]);
  assert.deepEqual(Array.from(crop.data.slice(0, 4)), [234, 0, 0, 0]);
  const skin = new THREE.MeshBasicMaterial();
  const neck = new THREE.Mesh(new THREE.SphereGeometry(.04), skin);
  const rig = new THREE.Group();
  rig.add(head, neck);
  neck.position.y = .9;
  head.position.y = .23;
  rig.updateMatrixWorld(true);
  let previousCalls = 0;
  const previous = () => previousCalls++;
  neck.onBeforeRender = previous;
  const detach = resource.bindNativeNeck(head, neck);
  assert.equal(neck.material, material);
  assert.equal(resource.bindNativeNeck(head, neck), null);
  const neckScale = 1 - .090 / .151;
  assert.ok(Math.abs(neck.scale.y - neckScale) < 1e-10);
  assert.ok(Math.abs(neck.position.y + .258 * neck.scale.y - (.9 + .258)) < 1e-10);
  rig.updateMatrixWorld(true);
  const camera = new THREE.PerspectiveCamera();
  neck.onBeforeRender(null, null, camera);
  assert.equal(previousCalls, 1);
  assert.equal(material.uniforms.nativeNeck.value, true);
  const projectedY = .67 + .090 + .258 * (1 - neckScale);
  assert.ok(Math.abs(material.uniforms.neckToPortrait.value.elements[13] - projectedY) < 1e-10);
  head.children[0].onBeforeRender(null, null, camera);
  assert.equal(material.uniforms.nativeNeck.value, false);
  detach();
  assert.equal(neck.material, skin);
  assert.equal(neck.onBeforeRender, previous);
  assert.equal(neck.position.y, .9);
  assert.equal(neck.scale.y, 1);
  resource.bindNativeNeck(head, neck);
  resource.dispose();
  assert.equal(neck.material, skin);
  assert.equal(neck.onBeforeRender, previous);
});
