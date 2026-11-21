/** What a panel does with a cached stream frame and the next HTTP read. */

export function liveReadDecision(connected: boolean, hasFrame: boolean, poll: boolean) {
  if (!connected) {
    return { startPoll: poll, acceptHttp: true, applyCachedFrame: false };
  }
  return { startPoll: false, acceptHttp: !hasFrame, applyCachedFrame: hasFrame };
}
