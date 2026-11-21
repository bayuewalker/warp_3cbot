/** What a panel does with a cached stream frame and the next HTTP read. */

export function liveSessionAfterLogout() {
  return { connected: false, reconnect: false, cachedRoutes: 0 };
}

export function liveReadDecision(connected: boolean, hasFrame: boolean, poll: boolean) {
  if (!poll) {
    return { startPoll: false, acceptHttp: true, applyCachedFrame: false };
  }
  if (!connected) {
    return { startPoll: true, acceptHttp: true, applyCachedFrame: false };
  }
  return { startPoll: false, acceptHttp: !hasFrame, applyCachedFrame: hasFrame };
}

/** A failed HTTP read is visible unless this panel is already showing data. */
export function surfaceHttpFailure(aborted: boolean, showing: boolean) {
  return !aborted && !showing;
}

/**
 * Hide a failed read only when the panel has data on screen.
 * A cached stream frame that policy will not apply is not data.
 */
export function failureVisible(
  connected: boolean,
  hasFrame: boolean,
  poll: boolean,
  aborted: boolean,
  showing: boolean,
) {
  const decision = liveReadDecision(connected, hasFrame, poll);
  const usable = showing || (decision.applyCachedFrame && hasFrame);
  return surfaceHttpFailure(aborted, usable);
}
