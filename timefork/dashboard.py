"""A thin FastAPI dashboard for Timefork: list runs, view a run's timeline,
fork a run, and diff two runs.

  uvicorn timefork.dashboard:app --port 8000
  open http://localhost:8000
"""

from html import escape

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from .diff import _describe, diff_runs
from .events import connect, read_events
from .fork import children_of, fork_run

app = FastAPI(title="Timefork")

STYLE = """
  body { font-family: ui-monospace, SFMono-Regular, monospace; max-width: 880px;
         margin: 2rem auto; color: #1f2937; padding: 0 1rem; }
  h2 a { color: inherit; text-decoration: none; }
  table { border-collapse: collapse; width: 100%; }
  td, th { border-bottom: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; font-size: 13px; }
  a { color: #2563eb; text-decoration: none; }
  .status { font-weight: 600; }
  .ev { padding: 5px 0; border-bottom: 1px solid #f1f5f9; font-size: 13px; }
  form { margin: 1rem 0; padding: 0.8rem; background: #f8fafc; border-radius: 8px; }
  input { padding: 4px 6px; border: 1px solid #cbd5e1; border-radius: 4px; }
  button { padding: 5px 12px; border: 0; background: #2563eb; color: #fff; border-radius: 6px; }
  .diff-row td { font-size: 12px; }
  .note { color: #6b7280; font-style: italic; }
"""


def _page(title, body):
    return (
        f"<!doctype html><html><head><title>Timefork · {escape(title)}</title>"
        f"<style>{STYLE}</style></head><body>"
        f'<h2><a href="/">Timefork</a> · {escape(title)}</h2>{body}</body></html>'
    )


@app.get("/", response_class=HTMLResponse)
def index():
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_id, status, agent_name, parent_run_id, fork_seq "
            "FROM runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    body = "<table><tr><th>run</th><th>status</th><th>agent</th><th>lineage</th></tr>"
    for run_id, status, agent, parent, fork_seq in rows:
        lineage = (
            f'fork of <a href="/run/{parent}">{parent[:8]}</a> @ {fork_seq}' if parent else ""
        )
        body += (
            f'<tr><td><a href="/run/{run_id}">{run_id[:8]}</a></td>'
            f'<td class="status">{escape(status)}</td><td>{escape(agent)}</td>'
            f"<td>{lineage}</td></tr>"
        )
    return _page("runs", body + "</table>")


@app.get("/run/{run_id}", response_class=HTMLResponse)
def show_run(run_id: str):
    with connect() as conn:
        info = conn.execute(
            "SELECT agent_name, status, parent_run_id, fork_seq FROM runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        if info is None:
            return _page("not found", f"<p>no run {escape(run_id)}</p>")
        events = read_events(conn, run_id)
        forks = children_of(conn, run_id)
    agent, status, parent, fork_seq = info

    body = (
        f"<p><b>{run_id}</b><br>status: "
        f'<span class="status">{escape(status)}</span> · agent: {escape(agent)}</p>'
    )
    if parent:
        body += (
            f'<p>forked from <a href="/run/{parent}">{parent[:8]}</a> at step {fork_seq} · '
            f'<a href="/diff/{parent}/{run_id}">diff vs parent</a></p>'
        )
    if forks:
        body += "<p>forks: " + ", ".join(
            f'<a href="/run/{c}">{c[:8]}</a> @ {s} (<a href="/diff/{run_id}/{c}">diff</a>)'
            for c, s in forks
        ) + "</p>"
    body += "<h3>timeline</h3>"
    for e in events:
        body += f'<div class="ev">{e.seq}. {escape(_describe(e) or "")}</div>'
    body += (
        f'<h3>fork this run</h3><form method="post" action="/run/{run_id}/fork">'
        f'at step <input name="at_seq" type="number" value="{len(events)}" min="1"> &nbsp; '
        f'set <input name="key" value="style"> = <input name="value" value="generous"> &nbsp; '
        f"<button type=\"submit\">fork</button></form>"
    )
    return _page(f"run {run_id[:8]}", body)


@app.post("/run/{run_id}/fork")
def do_fork(run_id: str, at_seq: int = Form(...), key: str = Form(""), value: str = Form("")):
    patch = {key: value} if key else {}
    with connect() as conn:
        child = fork_run(conn, run_id, at_seq, patch)
    return RedirectResponse(f"/run/{child}", status_code=303)


@app.get("/diff/{a}/{b}", response_class=HTMLResponse)
def show_diff(a: str, b: str):
    with connect() as conn:
        d = diff_runs(conn, a, b)
    body = (
        f'<p>shared prefix: {d["shared"]} steps · '
        f'first divergence at seq {d["diverge_at"]}</p>'
    )
    body += '<table><tr><th>seq</th><th>A</th><th>B</th></tr>'
    for r in d["rows"]:
        bg = "" if r.same else ' style="background:#fff7ed"'
        body += (
            f'<tr class="diff-row"{bg}><td>{r.seq}</td>'
            f'<td>{escape(r.a or "—")}</td><td>{escape(r.b or "—")}</td></tr>'
        )
    body += "</table>"
    body += '<p class="note">a fork is a fresh experiment, not proof of what the parent would have done.</p>'
    return _page("diff", body)
