/* Complete-frame decisions for the canvas that is already mounted.
 *
 * This is a rendering diagnostic. It records which frame was applied or
 * rejected. It does not select atoms, bonds, or scenes, and it does not
 * decide which frame is painted. Set MatterVisFrameWitness.enable(false)
 * to stop recording. */
(function (root) {
  "use strict";
  var enabled = true;
  var LIMIT = 16;
  function note(gd, record) {
    if (!enabled || !gd || !record || typeof record !== "object") return;
    var log = Array.isArray(gd.__mvFrameWitness) ? gd.__mvFrameWitness.slice() : [];
    log.push(record);
    while (log.length > LIMIT) log.shift();
    gd.__mvFrameWitness = log;
    if (typeof gd.setAttribute === "function") {
      gd.setAttribute("data-mv-frame-witness", JSON.stringify(log));
    }
  }
  root.MatterVisFrameWitness = {
    note: note,
    enable: function (value) { enabled = value !== false; },
    enabled: function () { return enabled; },
  };
})(window);
