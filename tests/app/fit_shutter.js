/* Camera stability and drawn-geometry identity for a Fit screenshot.
 *
 * A render-revision tick is not movement of the camera or the data box.
 * It is also not proof that the mesh is the same object. The geometry
 * tag is the scene generation, the geometry version, and a digest of the
 * finite coordinates on the visible traces.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.MatterVisFitShutter = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function quantize(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.round(number * 1e6) / 1e6;
  }
  function coordinateDigest(traces) {
    const parts = [];
    (traces || []).forEach(function (trace) {
      if (!trace || trace.visible === false) return;
      const xs = trace.x;
      const ys = trace.y;
      const zs = trace.z;
      if (!xs || !ys || !zs) return;
      const count = Math.min(xs.length, ys.length, zs.length);
      const coords = [];
      for (let index = 0; index < count; index += 1) {
        const x = quantize(xs[index]);
        const y = quantize(ys[index]);
        const z = quantize(zs[index]);
        if (x === null || y === null || z === null) continue;
        coords.push(x + "," + y + "," + z);
      }
      if (!coords.length) return;
      parts.push(String(trace.type || "") + ":" + coords.join(";"));
    });
    return parts.join("|");
  }
  function cameraTag(sample) {
    return JSON.stringify({
      revision: sample.revision,
      sceneId: sample.sceneId,
      projection: sample.projection,
      eye: sample.eye,
      center: sample.center,
      aspect: sample.aspect,
      ranges: sample.ranges,
      status: sample.status,
      boundaryChecked: sample.boundaryChecked,
      plot: sample.plot,
      canvas: sample.canvas,
      chrome: sample.chrome,
    });
  }
  function geometryTag(sample) {
    return JSON.stringify({
      sceneId: sample.sceneId,
      sceneGeneration: sample.sceneGeneration == null ? null : String(sample.sceneGeneration),
      geometryVersion: sample.geometryVersion == null ? null : Number(sample.geometryVersion),
      coordinateDigest: sample.coordinateDigest,
    });
  }
  function geometryReady(sample) {
    return !!(sample && typeof sample.coordinateDigest === "string" && sample.coordinateDigest.length > 0);
  }
  function sameCamera(before, after) {
    return !!(before && after && cameraTag(before) === cameraTag(after));
  }
  function sameGeometry(before, after) {
    return !!(geometryReady(before) && geometryReady(after) && geometryTag(before) === geometryTag(after));
  }
  function sameStableFrame(before, after) {
    return sameCamera(before, after) && sameGeometry(before, after);
  }
  return {
    quantize: quantize,
    coordinateDigest: coordinateDigest,
    cameraTag: cameraTag,
    geometryTag: geometryTag,
    geometryReady: geometryReady,
    sameCamera: sameCamera,
    sameGeometry: sameGeometry,
    sameStableFrame: sameStableFrame,
  };
});
