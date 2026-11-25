import { frames, nextFrame, policies } from "./story.mjs";
const $ = (id) => document.getElementById(id);
let page = "parallax", step = 0, mode = "observe";
const names = ["Take shared DB offline", "Try the same run again", "Use a temporary DB", "Replay from the start"];
const markers = frames.map((entry, index) => {
  const button = document.createElement("button");
  button.type = "button";
  const n = document.createElement("span"); n.textContent = `0${index + 1}`;
  button.append(n, entry.name);
  button.addEventListener("click", () => { step = index; render(); });
  $("steps").append(button);
  return button;
});
function render() {
  const compare = page === "flags";
  const f = frames[step];
  const p = policies[mode];
  $("stage").dataset.state = compare ? mode : ["healthy","outage","blocked","isolated"][step];
  $("scene-title").textContent = compare ? "CONTROLLED INVENTORY TEST" : "PARALLAX PIPELINE";
  document.querySelector(".shared b").textContent = compare ? "API" : "DB";
  $("parallax").setAttribute("aria-pressed", String(!compare));
  $("flags").setAttribute("aria-pressed", String(compare));
  $("story-controls").hidden = compare;
  $("policy-controls").hidden = !compare;
  $("steps").hidden = compare;
  $("legend").replaceChildren();
  for (const label of (compare ? ["4 CI jobs","CI Guard","Shared API offline"] : ["Parallax check","CI Guard",f.shared ? "Shared DB online" : "Shared DB offline",...(f.isolated ? ["Temporary DB online"] : [])])) {
    const marker = document.createElement("span"); marker.textContent = label; $("legend").append(marker);
  }
  $("counter").textContent = compare ? "SELF-HOSTED FLAGSMITH / CI_GUARD_MODE" : `STEP 0${step + 1} / 04`;
  $("title").textContent = compare ? (mode === "observe" ? "Observe only" : "Enforce the retry limit") : f.name;
  $("description").textContent = compare ? p.caption : f.text;
  $("outcome").textContent = compare ? (mode === "observe" ? "Not blocked" : "Blocked") : f.outcome;
  $("outcome").dataset.outcome = $("outcome").textContent;
  $("attempts").textContent = compare ? p.attempts : f.attempts;
  $("back").disabled = step === 0;
  $("next").firstChild.textContent = names[step] + " ";
  for (const m of ["observe","enforce"]) $(m).setAttribute("aria-pressed", String(mode === m));
  markers.forEach((m, i) => m.setAttribute("aria-current", i === step ? "step" : "false"));
}
$("next").addEventListener("click", () => { step = step === 3 ? 0 : nextFrame(step, 1); render(); });
$("back").addEventListener("click", () => { step = nextFrame(step, -1); render(); });
for (const p of ["parallax","flags"]) $(p).addEventListener("click", () => { page = p; render(); });
for (const m of ["observe","enforce"]) $(m).addEventListener("click", () => { mode = m; render(); });
render();
