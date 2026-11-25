import { frames, nextFrame, policies } from "./story.mjs";
const $ = (id) => document.getElementById(id);
let page = "parallax", step = 0, mode = "observe";
const names = ["Take shared DB offline", "Try the same run again", "Use a temporary DB", "See how Flagsmith cuts retries"];
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
  $("scene-title").textContent = compare ? "SECOND EXAMPLE: RETRY BUDGET" : "FIRST EXAMPLE: PARALLAX TEST";
  document.querySelector(".shared b").textContent = compare ? "API" : "DB";
  $("parallax").setAttribute("aria-pressed", String(!compare));
  $("flags").setAttribute("aria-pressed", String(compare));
  $("story-controls").hidden = compare;
  $("policy-controls").hidden = !compare;
  $("steps").hidden = compare;
  $("restart").hidden = !compare;
  $("legend").replaceChildren();
  for (const label of (compare ? ["4 test jobs","CI Guard","Shared service offline"] : ["Parallax check","CI Guard",f.shared ? "Shared DB online" : "Shared DB offline",...(f.isolated ? ["Temporary DB online"] : [])])) {
    const marker = document.createElement("span"); marker.textContent = label; $("legend").append(marker);
  }
  $("counter").textContent = compare ? "SAME OUTAGE · TWO SETTINGS" : `STEP 0${step + 1} / 04`;
  $("title").textContent = compare ? "Observe counts. Enforce stops repeats." : f.name;
  $("description").textContent = compare ? "Flagsmith gives teams a switch to change CI Guard’s behavior. " + p.caption : f.text;
  $("outcome").textContent = compare ? (mode === "observe" ? "Not blocked" : "Blocked") : f.outcome;
  $("outcome").dataset.outcome = $("outcome").textContent;
  $("attempts").textContent = compare ? p.attempts : f.attempts;
  $("back").disabled = step === 0;
  $("next").firstChild.textContent = names[step] + " ";
  for (const m of ["observe","enforce"]) $(m).setAttribute("aria-pressed", String(mode === m));
  markers.forEach((m, i) => m.setAttribute("aria-current", i === step ? "step" : "false"));
}
$("next").addEventListener("click", () => { if (step === 3) { page = "flags"; mode = "observe"; } else { step = nextFrame(step, 1); } render(); if (page === "flags") $("observe").focus(); });
$("back").addEventListener("click", () => { step = nextFrame(step, -1); render(); });
for (const p of ["parallax","flags"]) $(p).addEventListener("click", () => { page = p; render(); });
$("restart").addEventListener("click", () => { page = "parallax"; step = 0; render(); $("next").focus(); });
for (const m of ["observe","enforce"]) $(m).addEventListener("click", () => { mode = m; render(); });
render();
