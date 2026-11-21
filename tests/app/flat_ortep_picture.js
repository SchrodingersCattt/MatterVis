/* Decide whether a publication picture is still the one the user can see.
 * A single good sample is not enough: the next sample has to be read after
 * the caller has waited, and a later loss or camera-status failure rejects.
 */

function noteSample(state, sample) {
  const current = state || { ready: false, seenLater: false, failed: "" };
  if (current.failed) return current;
  if (sample && sample.statusBad) {
    return { ready: current.ready, seenLater: current.seenLater, failed: "status" };
  }
  const holds = !!(sample && sample.holds);
  if (current.ready && !holds) {
    return { ready: true, seenLater: current.seenLater, failed: "disappeared" };
  }
  if (holds) {
    return { ready: true, seenLater: current.ready, failed: "" };
  }
  return { ready: current.ready, seenLater: current.seenLater, failed: "" };
}

function accepted(state) {
  return !!(state && state.ready && state.seenLater && !state.failed);
}

module.exports = { noteSample, accepted };
