#!/usr/bin/env python3
"""Simple checker page: pick two folders, then compare the pictures."""

from __future__ import annotations

import json
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from compare_optimized_images import compare_folders, write_report

HERE = Path(__file__).resolve().parent
REPORT_PATH = HERE / "image_comparison_report.csv"

STATUS_LABELS = {
    "PASS": "Same picture",
    "REVIEW": "Very close — worth a quick look",
    "CHECK": "Looks different",
    "MISSING": "Compressed file missing",
    "ERROR": "Could not read the file",
    "AMBIGUOUS_ORIGINAL": "More than one original with this name",
    "AMBIGUOUS_OPTIMIZED": "More than one compressed file with this name",
}

STATE = {
    "original": "",
    "optimized": "",
    "running": False,
    "done": False,
    "processed": 0,
    "total": 0,
    "total_originals": 0,
    "counts": {},
    "problems": [],
    "report": "",
    "error": "",
    "message": "Choose both folders, then click Check pictures.",
}
LOCK = threading.Lock()


def choose_folder(prompt: str) -> str | None:
    script = 'POSIX path of (choose folder with prompt "%s")' % prompt.replace('"', "")
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().rstrip("/")


def public_state() -> dict:
    with LOCK:
        counts = [
            {"key": key, "label": label, "count": STATE["counts"].get(key, 0)}
            for key, label in STATUS_LABELS.items()
            if STATE["counts"].get(key, 0)
        ]
        return {
            "original": STATE["original"],
            "optimized": STATE["optimized"],
            "running": STATE["running"],
            "done": STATE["done"],
            "processed": STATE["processed"],
            "total": STATE["total"],
            "total_originals": STATE["total_originals"],
            "counts": counts,
            "problems": STATE["problems"],
            "report": STATE["report"],
            "error": STATE["error"],
            "message": STATE["message"],
        }


def run_check() -> None:
    with LOCK:
        original = STATE["original"]
        optimized = STATE["optimized"]
        STATE["running"] = True
        STATE["done"] = False
        STATE["error"] = ""
        STATE["problems"] = []
        STATE["counts"] = {}
        STATE["report"] = ""
        STATE["message"] = "Looking through the folders..."

    def progress(processed: int, total_pairs: int, total_originals: int) -> None:
        with LOCK:
            STATE["processed"] = processed
            STATE["total"] = total_pairs
            STATE["total_originals"] = total_originals
            if total_pairs:
                STATE["message"] = f"Checked {processed:,} of {total_pairs:,} pictures..."
            else:
                STATE["message"] = "No pictures found in the original folder."

    try:
        result = compare_folders(Path(original), Path(optimized), progress=progress)
        write_report(REPORT_PATH, result["rows"])
        problems = []
        for row in result["rows"]:
            if row["status"] == "PASS":
                continue
            name = Path(row["original_file"]).name if row["original_file"] else ""
            problems.append({
                "name": name,
                "format": row["format"],
                "label": STATUS_LABELS.get(row["status"], row["status"]),
            })
        with LOCK:
            STATE["counts"] = result["counts"]
            STATE["problems"] = problems[:30]
            STATE["report"] = str(REPORT_PATH)
            STATE["done"] = True
            STATE["message"] = "Check finished. The spreadsheet lists only pictures that need attention."
    except Exception as exc:
        with LOCK:
            STATE["error"] = str(exc)
            STATE["message"] = "The check stopped because of a problem."
    finally:
        with LOCK:
            STATE["running"] = False


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Check compressed pictures</title>
<style>
  :root { color-scheme: light; }
  body {
    margin: 0; background: #f4f1ea; color: #1f1a14;
    font: 18px/1.45 "Iowan Old Style", Palatino, Georgia, serif;
  }
  main { max-width: 720px; margin: 0 auto; padding: 40px 24px 64px; }
  h1 { font-size: 34px; line-height: 1.15; margin: 0 0 12px; }
  p.lead { margin: 0 0 28px; color: #4d453c; }
  .card {
    background: #fffdf8; border: 1px solid #e2d8c8; border-radius: 16px;
    padding: 18px 18px 16px; margin: 0 0 14px;
  }
  .step { font-size: 13px; letter-spacing: .04em; text-transform: uppercase; color: #8a7b68; margin: 0 0 6px; }
  button {
    font: 600 17px/1 "Avenir Next", "Segoe UI", sans-serif;
    border: 0; border-radius: 999px; padding: 12px 18px; cursor: pointer;
    background: #2f5d50; color: white;
  }
  button.secondary { background: #efe7da; color: #2b241c; }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .path { margin: 10px 0 0; word-break: break-all; color: #2b241c; }
  .path.empty { color: #8a7b68; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0; }
  #message { font-size: 20px; margin: 8px 0 16px; }
  .results { display: grid; gap: 8px; }
  .result {
    display: flex; justify-content: space-between; gap: 16px;
    background: white; border-radius: 12px; padding: 12px 14px;
    border: 1px solid #e2d8c8;
  }
  .result strong { font-variant-numeric: tabular-nums; }
  ul { margin: 8px 0 0; padding-left: 18px; }
  li { margin: 4px 0; }
  .warn { color: #8a3b12; }
  .ok { color: #1e6b45; }
</style>
</head>
<body>
<main>
  <h1>Check compressed pictures</h1>
  <p class="lead">Choose the original pictures, then the compressed pictures. This checks that each compressed file is still the same photo. It does not change or delete anything.</p>

  <section class="card">
    <p class="step">Step 1</p>
    <button class="secondary" id="pick-original" type="button">Choose original folder</button>
    <p class="path empty" id="original-path">No folder chosen yet</p>
  </section>
  <section class="card">
    <p class="step">Step 2</p>
    <button class="secondary" id="pick-optimized" type="button">Choose compressed folder</button>
    <p class="path empty" id="optimized-path">No folder chosen yet</p>
  </section>

  <div class="actions">
    <button id="start" type="button" disabled>Check pictures</button>
    <button class="secondary" id="open-report" type="button" hidden>Open the full report</button>
  </div>
  <p id="message">Choose both folders, then click Check pictures.</p>
  <div class="results" id="results"></div>
  <div id="problems"></div>
</main>
<script>
const originalPath = document.getElementById("original-path");
const optimizedPath = document.getElementById("optimized-path");
const startBtn = document.getElementById("start");
const message = document.getElementById("message");
const results = document.getElementById("results");
const problems = document.getElementById("problems");
const openReport = document.getElementById("open-report");
let polling = null;

function showPath(el, value) {
  el.textContent = value || "No folder chosen yet";
  el.classList.toggle("empty", !value);
}

async function refresh() {
  const data = await (await fetch("/api/status")).json();
  showPath(originalPath, data.original);
  showPath(optimizedPath, data.optimized);
  startBtn.disabled = data.running || !data.original || !data.optimized;
  document.getElementById("pick-original").disabled = data.running;
  document.getElementById("pick-optimized").disabled = data.running;
  startBtn.textContent = data.running ? "Checking..." : "Check pictures";
  message.textContent = data.error ? data.message + " " + data.error : data.message;
  message.className = data.error ? "warn" : (data.done ? "ok" : "");
  openReport.hidden = !data.report;
  results.innerHTML = data.counts.map(item =>
    `<div class="result"><span>${item.label}</span><strong>${item.count.toLocaleString()}</strong></div>`
  ).join("");
  if (data.problems.length) {
    const extra = data.problems.length === 30 ? "<p>The spreadsheet has the full list.</p>" : "";
    problems.innerHTML = "<h2>Needs attention</h2><ul>" +
      data.problems.map(item => `<li>${item.name} · ${item.format} · ${item.label}</li>`).join("") +
      "</ul>" + extra;
  } else {
    problems.innerHTML = "";
  }
  if (!data.running && polling) {
    clearInterval(polling);
    polling = null;
  }
}

async function choose(which, button) {
  button.disabled = true;
  message.textContent = "A Finder window is open. Choose the folder there.";
  await fetch("/api/choose", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({which})
  });
  button.disabled = false;
  await refresh();
}

document.getElementById("pick-original").onclick = (event) => choose("original", event.currentTarget);
document.getElementById("pick-optimized").onclick = (event) => choose("optimized", event.currentTarget);
startBtn.onclick = async () => {
  startBtn.disabled = true;
  await fetch("/api/start", {method: "POST"});
  if (!polling) polling = setInterval(refresh, 700);
  await refresh();
};
openReport.onclick = () => fetch("/api/open-report", {method: "POST"});
refresh();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            self.send_json(public_state())
            return
        if path != "/":
            self.send_error(404)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/choose":
            which = self.read_json().get("which")
            if which not in ("original", "optimized"):
                self.send_json({"ok": False}, 400)
                return
            prompt = (
                "Choose the folder of original pictures"
                if which == "original"
                else "Choose the folder of compressed pictures"
            )
            folder = choose_folder(prompt)
            if folder:
                with LOCK:
                    STATE[which] = folder
                    STATE["done"] = False
                    STATE["message"] = "Choose both folders, then click Check pictures."
            self.send_json(public_state())
            return

        if path == "/api/start":
            with LOCK:
                if STATE["running"]:
                    self.send_json(public_state())
                    return
                if not STATE["original"] or not STATE["optimized"]:
                    self.send_json({"ok": False, "error": "Choose both folders first."}, 400)
                    return
                STATE["running"] = True
            threading.Thread(target=run_check, daemon=True).start()
            self.send_json(public_state())
            return

        if path == "/api/open-report":
            with LOCK:
                report = STATE["report"]
            if report and Path(report).is_file():
                subprocess.run(["open", report], check=False)
            self.send_json({"ok": True})
            return

        self.send_error(404)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    print("The checker is open in your browser.", flush=True)
    print(url, flush=True)
    print("Keep this window open. Closing it closes the checker.", flush=True)
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nChecker closed.")


if __name__ == "__main__":
    main()
