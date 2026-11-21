# Three-minute CI Guard walkthrough

The requested recording was produced from the running application and actual GitHub evidence on 21 November 2026. It is delivered with this chat as `artifacts/ci-guard-demo.mp4`, alongside a local browser player and timestamped transcript. Generated media stays outside source control.

## Format and verification

- Duration: **180.021 seconds** (3:00).
- Video: **1440 × 1080**, H.264, 30 frames per second.
- Audio: AAC synthesized narration; captions are rendered in a separate area below the interface.
- File size: approximately **8.34 MB**.
- Full video/audio decode completed without errors.
- Browser playback, seeking to the enforcement results, and playback through the final frame passed.
- An independent visual review sampled seven points. Captions were readable; GitHub and local demo evidence were distinguished; no credentials or private/unselected repositories were visible in those samples.

The initial plain file server did not support byte-range requests, causing browser seeking to restart near the beginning. A range-capable server returned HTTP 206 for partial content; the same browser seek/end check then passed. The recording itself required no codec or content workaround.

## Chapters

| Time | Content |
|---|---|
| 0:00 | The running application and dependency-outage problem |
| 0:15 | Actual connected GitHub account and selected repositories |
| 0:30 | Explicit account access and GitHub App setup state |
| 0:45 | Repository-scoped runner credentials and installation instructions |
| 1:00 | Actual recorded dispatch from the Projects screen |
| 1:15 | Downloaded evidence after GitHub completes |
| 1:30 | Live controlled outage in observe mode |
| 1:45 | Twelve measured attempts and failure evidence |
| 2:00 | Actual Flagsmith policy change to enforcement |
| 2:15 | Six measured attempts sharing one retry allowance |
| 2:30 | Assertion failure and healthy recovery |
| 2:45 | Connected projects and reusable workflow |

The GitHub queue/execution wait is condensed with an explicit **After GitHub completes** transition. The dispatch was submitted once. The later report view is generated from the actual downloaded artifacts, not a fabricated GitHub success screen. Live local demonstrations remain visibly labeled.

The recording executed 12 observe attempts, 6 enforce attempts, two ordinary assertion failures without dependency retries, and two healthy passing runs. The original Flagsmith policy was restored after recording.

## Real GitHub evidence

[Successful demonstration run 44881605727](https://github.com/ayushmanthehero-lab/toggle_wars/actions/runs/44881605727) ran the controlled fixture and **34 existing StreamPulse tests**, all passing, from the reviewed source pin. [Detailed verification](GITHUB_VERIFICATION.md) distinguishes those two forms of evidence.

The account uses explicitly configured server access. A GitHub App is not yet registered for this deployment; normal device sign-in still requires the setup described in [GITHUB.md](GITHUB.md). No sign-in code, account token, or administrator credential is included in the video.
