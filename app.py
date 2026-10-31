"""LabLens web app (Gradio). Runs locally or as a Hugging Face Space.

Flow:  upload -> read (deterministic extraction + flags) -> you check the table -> explain.
The check step is deliberate: OCR can misread a digit, and a human glance is the cheapest
safety net there is.
"""
from __future__ import annotations

import html
from pathlib import Path

import gradio as gr

from lablens.catalog import load_catalog, match_test_name
from lablens.explain import explain, llm_available, render_markdown
from lablens.extract import extract
from lablens.flagger import flag_all
from lablens.i18n import CHECK_NOTE, DISCLAIMER, FLAG_LABEL, GENERIC_RANGE, LANGS, RTL, URGENT, t
from lablens.models import Flag, Result

SAMPLES = Path(__file__).parent / "samples"
HEADERS = ["Test", "Value", "Unit", "Range low", "Range high"]
LANG_CHOICES = [(v, k) for k, v in LANGS.items()]
catalog = load_catalog()

FLAG_COLOR = {
    "normal": "ok", "low": "warn", "high": "warn", "critical_low": "crit", "critical_high": "crit",
    "unknown": "muted", "check": "check",
}


def _num(x):
    if x is None:
        return None
    s = str(x).strip().replace(",", ".")
    if s in ("", "nan", "None", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_report(file, lang):
    if file is None:
        return gr.update(value=[]), "Upload a report or pick a sample first.", None
    path = file if isinstance(file, str) else file.name
    results, unmatched, detected, method = extract(path)
    analysis = flag_all(results, method=method)
    rows = [[catalog[r.test_key].display["en"], r.value, r.unit or "", r.ref_low, r.ref_high]
            for r in analysis.results]
    n_check = sum(r.flag == Flag.CHECK for r in analysis.results)
    how = "the PDF's text" if method == "pdf-text" else "the photo (OCR)"
    status = (f"Read **{len(rows)} results** from {how}. "
              "Compare the table with your report, fix anything that's wrong, then press **Explain**.")
    if n_check:
        status += f"\n\n⚠️ {t(CHECK_NOTE, lang)}"
    if unmatched:
        status += f"\n\n<details><summary>{len(unmatched)} line(s) with numbers I couldn't match to a test</summary>\n\n" + \
                  "\n".join(f"- `{html.escape(u)}`" for u in unmatched[:15]) + "\n</details>"
    return gr.update(value=rows), status, detected


def _rows_to_results(rows) -> list[Result]:
    out = []
    data = rows.values.tolist() if hasattr(rows, "values") else (rows or [])
    for row in data:
        if not row or not str(row[0]).strip():
            continue
        key, score = match_test_name(str(row[0]))
        value = _num(row[1])
        if key is None or value is None:
            continue
        lo, hi = _num(row[3]), _num(row[4])
        out.append(Result(test_key=key, raw_name=str(row[0]), value=value, unit=str(row[2] or "") or None,
                          ref_low=lo, ref_high=hi,
                          ref_source="report" if (lo is not None or hi is not None) else "none",
                          match_score=score))
    return out


def _results_table(analysis, lang) -> str:
    rtl = ' dir="rtl"' if lang in RTL else ""
    trs = []
    for r in analysis.results:
        td = catalog[r.test_key]
        name = html.escape(td.display.get(lang) or td.display["en"])
        if r.ref_low is not None and r.ref_high is not None:
            rng = f"{r.ref_low:g} – {r.ref_high:g}"
        elif r.ref_high is not None:
            rng = f"&lt; {r.ref_high:g}"
        elif r.ref_low is not None:
            rng = f"&gt; {r.ref_low:g}"
        else:
            rng = "—"
        if r.ref_source == "catalog":
            rng += f'<div class="ll-note">{html.escape(t(GENERIC_RANGE, lang))}</div>'
        label = html.escape(t(FLAG_LABEL[r.flag.value], lang))
        trs.append(f'<tr><td>{name}</td><td class="ll-num">{r.value:g} <span class="ll-unit">{html.escape(r.unit or "")}</span></td>'
                   f'<td class="ll-num">{rng}</td><td><span class="ll-badge ll-{FLAG_COLOR[r.flag.value]}">{label}</span></td></tr>')
    return f'<table class="ll-table"{rtl}><tbody>{"".join(trs)}</tbody></table>'


def run_explain(rows, lang):
    results = _rows_to_results(rows)
    if not results:
        return "", "", "Nothing to explain yet: read a report first.", ""
    analysis = flag_all(results)
    rtl = ' dir="rtl"' if lang in RTL else ""
    banner = ""
    if analysis.urgent:
        banner = f'<div class="ll-urgent"{rtl}>🚨 {_md_bold(t(URGENT, lang))}</div>'
    if any(r.flag == Flag.CHECK for r in analysis.results):
        banner += f'<div class="ll-check"{rtl}>⚠️ {html.escape(t(CHECK_NOTE, lang))}</div>'
    exp, source = explain(analysis, lang)
    md = render_markdown(analysis, exp, lang)
    note = ("Explanation written by an open model, grounded in the LabLens knowledge base and checked by guardrails."
            if source == "llm" else
            "Explanation from the LabLens knowledge base (AI model not configured or unavailable).")
    return banner, _results_table(analysis, lang), md, f"<small>{note} {html.escape(t(DISCLAIMER, lang))}</small>"


def _md_bold(s: str) -> str:
    s = html.escape(s)
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def load_sample(name):
    return str(SAMPLES / name)


CSS = """
:root { --ll-ink:#14213d; --ll-accent:#0f766e; }
.gradio-container { max-width: 1080px !important; margin: auto; }
#ll-hero h1 { font-size: 2.1rem; margin-bottom: .2rem; letter-spacing: -0.02em; }
#ll-hero p { font-size: 1.02rem; opacity: .85; margin-top: 0; }
.ll-pill { display:inline-block; padding:.18rem .6rem; border-radius:999px; font-size:.78rem;
           background: color-mix(in srgb, var(--ll-accent) 14%, transparent); color: var(--ll-accent);
           margin-right:.35rem; font-weight:600; }
.ll-table { width:100%; border-collapse:collapse; font-size:.95rem; }
.ll-table td { padding:.55rem .6rem; border-bottom:1px solid color-mix(in srgb, currentColor 12%, transparent); vertical-align: top; }
.ll-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.ll-unit { opacity:.65; font-size:.85em; }
.ll-note { font-size:.75rem; opacity:.65; white-space: normal; }
.ll-badge { padding:.15rem .55rem; border-radius:6px; font-size:.82rem; font-weight:600; white-space:nowrap; }
.ll-ok { background:#dcfce7; color:#14532d; }
.ll-warn { background:#fef3c7; color:#78350f; }
.ll-crit { background:#fee2e2; color:#7f1d1d; }
.ll-check { background:#e0e7ff; color:#312e81; padding:.7rem .9rem; border-radius:10px; margin:.4rem 0; }
.ll-muted { background:#e5e7eb; color:#374151; }
.ll-urgent { background:#fee2e2; color:#7f1d1d; border:2px solid #dc2626; padding:.9rem 1rem;
             border-radius:12px; font-size:1.02rem; margin:.4rem 0; }
"""

with gr.Blocks(title="LabLens") as demo:
    gr.HTML(
        '<div id="ll-hero"><h1>🔬 LabLens</h1>'
        "<p>Understand your blood test in plain language — English, Français, العربية, or Tounsi.</p>"
        '<span class="ll-pill">Flags by fixed medical rules, not AI</span>'
        '<span class="ll-pill">Your image and name never reach the AI</span>'
        '<span class="ll-pill">You check every value</span></div>'
    )
    with gr.Row():
        with gr.Column(scale=5):
            file = gr.File(label="Lab report (photo or PDF)", file_types=["image", ".pdf"], type="filepath")
            with gr.Row():
                s1 = gr.Button("Sample: English PDF", size="sm")
                s2 = gr.Button("Sample: French photo", size="sm")
                s3 = gr.Button("Sample: urgent value", size="sm")
            lang = gr.Dropdown(LANG_CHOICES, value="en", label="Explain in")
            read_btn = gr.Button("1 · Read report", variant="primary")
        with gr.Column(scale=7):
            status = gr.Markdown("Upload a report to begin.")
            table = gr.Dataframe(headers=HEADERS, datatype=["str", "number", "str", "number", "number"],
                                 interactive=True, label="What I read — edit anything that's wrong",
                                 row_count=(0, "dynamic"), column_count=(5, "fixed"))
            explain_btn = gr.Button("2 · Explain my results", variant="primary")

    detected = gr.State(None)
    banner = gr.HTML()
    results_html = gr.HTML()
    explanation = gr.Markdown()
    footnote = gr.HTML(f"<small>{t(DISCLAIMER, 'en')} Synthetic sample reports only — fictional patients and labs.</small>")

    s1.click(lambda: load_sample("sample_en_report.pdf"), outputs=file)
    s2.click(lambda: (load_sample("sample_fr_photo.png"), "fr"), outputs=[file, lang])
    s3.click(lambda: (load_sample("sample_fr_urgent.png"), "darija"), outputs=[file, lang])
    read_btn.click(read_report, [file, lang], [table, status, detected])
    explain_btn.click(run_explain, [table, lang], [banner, results_html, explanation, footnote])
    lang.change(lambda l: gr.update(rtl=l in RTL), lang, explanation)

if __name__ == "__main__":
    print("LLM configured:", llm_available())
    demo.launch(css=CSS, theme=gr.themes.Soft(primary_hue="teal"))
