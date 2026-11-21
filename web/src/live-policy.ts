/** What a panel does with a cached stream frame and the next HTTP read. */

export function liveReadDecision(connected: boolean, hasFrame: boolean, poll: boolean) {
  if (!poll) {
    return { startPoll: false, acceptHttp: true, applyCachedFrame: false };
  }
  if (!connected) {
    return { startPoll: true, acceptHttp: true, applyCachedFrame: false };
  }
  return { startPoll: false, acceptHttp: !hasFrame, applyCachedFrame: hasFrame };
}
