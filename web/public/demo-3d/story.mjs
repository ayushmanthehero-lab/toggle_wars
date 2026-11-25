export const frames = [
  { name: "Healthy baseline", outcome: "Passed", attempts: 1, shared: true, isolated: false,
    text: "Parallax’s graph check completes against the shared FalkorDB. One actual execution passes." },
  { name: "Shared outage", outcome: "Failed", attempts: 3, shared: false, isolated: false,
    text: "The database stops responding. The initial check and two retries fail. This is a dependency outage, not an assertion failure." },
  { name: "Immediate rerun", outcome: "Blocked", attempts: 0, shared: false, isolated: false,
    text: "CI Guard recognizes the existing outage and blocks the repeat before the test starts. Blocked does not count as passed." },
  { name: "Isolated PR check", outcome: "Passed", attempts: 1, shared: false, isolated: true,
    text: "The same graph check passes against its own temporary FalkorDB while the shared copy stays down. A real release still needs a separate live staging check." },
];
export const policies = {
  observe: { attempts: 12, caption: "Four jobs each make an initial attempt and two retries. Observe records what it would block without stopping the jobs." },
  enforce: { attempts: 6, rerunAttempts: 0, caption: "Four initial attempts plus two shared retries. An immediate rerun while the outage remains active starts no tests." },
};
export const nextFrame = (index, delta) => Math.max(0, Math.min(frames.length - 1, index + delta));
