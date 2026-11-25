export const frames = [
  { name: "Healthy baseline", outcome: "Passed", attempts: 1, shared: true, isolated: false,
    text: "The shared test database is online. GitHub sends Parallax’s graph test to it, and the test passes on the first try. Now see what happens if the shared database goes down." },
  { name: "Shared outage", outcome: "Failed", attempts: 3, shared: false, isolated: false,
    text: "The database stops replying. The test fails once, then both retries fail too. The code hasn’t changed. A rerun would just hit the same offline database." },
  { name: "Immediate rerun", outcome: "Blocked", attempts: 0, shared: false, isolated: false,
    text: "CI Guard sees the same outage and stops the rerun before the test starts. That saves an unnecessary execution. Blocked means the test never ran, not that the code passed." },
  { name: "Isolated PR check", outcome: "Passed", attempts: 1, shared: false, isolated: true,
    text: "Give this test its own temporary copy of FalkorDB. The same check now passes, even though the shared database is still offline. Before release, a separate check still has to pass against the shared test environment." },
];
export const policies = {
  observe: { attempts: 12, caption: "Four test jobs hit one offline service. In Observe, each job starts once and retries twice: 12 attempts. CI Guard records what it would block, but lets every retry run." },
  enforce: { attempts: 6, rerunAttempts: 0, caption: "Same outage, same four jobs. In Enforce, all four get a first try, but together they get only two retries: 6 attempts. An immediate rerun while the outage remains active starts no tests." },
};
export const nextFrame = (index, delta) => Math.max(0, Math.min(frames.length - 1, index + delta));
