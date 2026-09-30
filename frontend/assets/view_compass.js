/* Public bridge for the SVG compass.  The renderer itself remains in the
 * legacy asset so static publication exports keep their existing behaviour. */
(function (root) {
  "use strict";
  root.MatterVisViewCompass = {
    setEnabled: function (enabled) {
      root.__mv_axes_enabled = !!enabled;
      var gd = root.MatterVisViewCamera && root.MatterVisViewCamera.graph();
      var svg = document.getElementById("mv-compass-svg");
      if (!enabled) {
        if (svg) while (svg.firstChild) svg.removeChild(svg.firstChild);
        return true;
      }
      if (root.MatterVisCompass && gd) root.MatterVisCompass.redraw(gd, null, false);
      return true;
    },
    redraw: function () {
      var gd = root.MatterVisViewCamera && root.MatterVisViewCamera.graph();
      if (root.MatterVisCompass && gd) root.MatterVisCompass.redraw(gd, null, true);
    }
  };
})(window);
