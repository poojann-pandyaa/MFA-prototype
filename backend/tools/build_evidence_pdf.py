"""
Builds docs/ato-model-evidence.pdf from docs/evidence/results.json and the PNG charts written by `python -m ato.evaluate`.

    cd backend && .venv/bin/python -m tools.build_evidence_pdf
"""
import json
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, Image, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent.parent
EV = ROOT / "docs" / "evidence"
OUT = ROOT / "docs" / "ato-model-evidence.pdf"
NAVY, TEAL, AMBER, GRID, LIGHT = colors.HexColor("#14325c"), colors.HexColor("#1b7f8c"), colors.HexColor("#b86b00"), colors.HexColor("#c4cfdb"), colors.HexColor("#eef3f8")
ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13.5, spaceAfter=5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5, spaceAfter=3)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=7.6, leading=9.4, spaceAfter=0)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
HEADC = ParagraphStyle("headc", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=NAVY, spaceBefore=4, spaceAfter=7, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=TEAL, spaceBefore=7, spaceAfter=3, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2.5)
NOTE = ParagraphStyle("note", parent=BODY, fontSize=9, leading=12.5, textColor=colors.HexColor("#5a3a00"), backColor=colors.HexColor("#fff4de"),
                      borderColor=AMBER, borderWidth=0.6, borderPadding=6, spaceBefore=4, spaceAfter=10)
COVT = ParagraphStyle("covt", parent=BODY, fontName="Helvetica-Bold", fontSize=25, leading=30, textColor=NAVY, spaceAfter=8)
COVS = ParagraphStyle("covs", parent=BODY, fontSize=12.5, leading=17, textColor=TEAL, spaceAfter=12)
W = 174 * mm


def pct(x):
    return "n/a" if x is None or x != x else f"{100 * x:.1f}%"


def P(t, st=BODY):
    return Paragraph(t, st)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, first_bold=False):
    data = []
    for i, r in enumerate(rows):
        data.append([Paragraph(str(c), HEADC if i == 0 else (CELLB if (first_bold and j == 0) else CELL)) for j, c in enumerate(r)])
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, GRID), ("BACKGROUND", (0, 0), (-1, 0), NAVY),
          ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
    st += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2, len(rows), 2)]
    t.setStyle(TableStyle(st))
    return t


def img(name, frac=1.0):
    p = EV / name
    w, h = PILImage.open(p).size
    ww = W * frac
    return Image(str(p), width=ww, height=ww * h / w)


def footer(c, doc):
    c.saveState()
    c.setFont("Helvetica", 7.5); c.setFillColor(colors.HexColor("#666666"))
    c.drawString(18 * mm, 10 * mm, "Payment-risk model evidence | every number is on SIMULATED data unless stated")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    c.setStrokeColor(GRID); c.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    c.restoreState()


def main():
    J = json.loads((EV / "results.json").read_text())
    R, S = J["main"], J["shifts"]
    h = R["headline"]["full"]
    hi = h["inc_HIGH"]
    ov = R["overall"]
    rc = R["reconcile"]
    mf = R["model_facts"]
    from ato.evaluate import reweighted_saved
    rw = reweighted_saved(R["types"], rc["rupee_share_by_type"])
    story = []

    # cover
    story += [Spacer(1, 30 * mm), P("How the payment-risk model works, and what it can and cannot prove", COVT),
              P("Statistics, every-case results, and real-world relevance", COVS),
              table([["Model", "3 detectors (takeover, coached scam, bad payee) + a router; %d inputs; saved size %d KB" % (mf["features"], sum(mf["files_kb"].values()))],
                     ["Test", "%s payments in a held-out month, %d scam incidents across 14 scam types" % (f"{mf['test_rows']:,}", R["n_incidents"])],
                     ["Speed", "about %.0f ms per payment (full), %.0f ms (server-only)" % (R["latency_ms"]["full"], R["latency_ms"]["server_only"])],
                     ["Data", "Simulated. No real labelled UPI fraud data exists publicly."]], [0.14, 0.86], first_bold=True),
              Spacer(1, 8 * mm),
              P("<b>Read this first.</b> Nothing in this document proves real-world accuracy. Real accuracy needs labelled history from a bank or UPI app. "
                "What this document does show is (1) how the model works and predicts, (2) how it behaves on every scam type, genuine situation and user segment we could construct, "
                "(3) which assumptions the results depend on, and (4) how much the results move when those assumptions are wrong.", NOTE),
              PageBreak()]

    # 1 summary
    story += [P("1. The short version", H1)]
    story += bullets([
        f"On the held-out simulated month the full model caught <b>{pct(hi['caught'])}</b> of scam incidents at the strong-action level (95% range {pct(hi['caught_ci'][0])} to {pct(hi['caught_ci'][1])}), "
        f"stopped the very first payment {pct(hi['first_payment_caught'])} of the time, and saved {pct(hi['rupees_saved'])} of the rupees, while giving a strong action to only {pct(h['fpr_HIGH'])} of genuine payments.",
        f"The hand-written rules we started from caught {pct(R['headline']['rules']['inc_HIGH']['caught'])} at the same friction.",
        f"The three detectors separate scams from genuine payments well in the simulation (combined ROC-AUC {R['discrimination']['combined']['roc_auc']:.3f}, PR-AUC {R['discrimination']['combined']['pr_auc']:.3f}).",
        "The model is one shared model, but almost every input is measured against each user's own history, so it behaves personally.",
        "<b>The big real-world caveat:</b> real scams are about 1 in 100,000 payments, not the 1 in 260 simulated here. At that rarity most strong actions land on genuine users, so the action must be cheap and kind (section 6).",
        f"Re-scoring the unchanged model on {len(S)} worlds with different assumptions shows where it holds and where it breaks (section 7)."])

    # 2 how it works
    story += [P("2. How it works", H1),
              P("Each payment is turned into %d numbers: the amount compared with this user's norm, whether the payee is new, how old the payee account is and how many people paid it today, "
                "whether a SIM change or phone binding just happened, whether a call is in progress, whether the payee id is almost identical to a saved one, and so on. "
                "Three detectors each score the payment. Each score becomes a <b>surprise</b>: how rare a score that high is among genuine payments. The largest surprise sets the severity "
                "(prompt, strong action, block) and a small router names the family so the action fits (identity check for takeover, warning or wait for a tricked owner, hold for a bad payee)." % mf["features"]),
              P("Strictness is a budget on genuine users, not a guess: at most %s of genuine payments may reach prompt level, %s strong action, %s block. "
                "Because the budget is about genuine users, it does not depend on how common fraud is." % tuple(f"{100 * mf['budgets'][k]:g}%" for k in ("MEDIUM", "HIGH", "CRITICAL"))),
              img("score_distribution.png", 0.86), Spacer(1, 3 * mm),
              table([["Detector", "ROC-AUC", "PR-AUC", "Scam payments in test"]] + [[k, f"{v['roc_auc']:.3f}", f"{v['pr_auc']:.3f}", v["positives"]] for k, v in R["discrimination"].items()], [0.34, 0.2, 0.2, 0.26]),
              P("ROC-AUC near 1 means the score ranks scams above genuine payments almost always; PR-AUC is harsher because scams are rare in the test month. Higher is better.", SMALL),
              img("operating_curve.png", 0.86),
              table([["Genuine payments alerted", "Incidents caught", "First payment caught", "Rupees saved"]] +
                    [[f"{100 * r['legit_alert_rate']:.2f}%", pct(r["incidents_caught"]), pct(r["first_payment"]), pct(r["rupees_saved"])] for r in R["curve"]["full"]], [0.25, 0.25, 0.25, 0.25]),
              PageBreak()]

    # 3 what it relies on
    story += [P("3. What the detectors rely on", H1), img("importance.png", 0.78),
              P("Each cell is how much that detector gets worse when one group of inputs is shuffled. Takeover relies mostly on identity signals (binding, SIM, PIN reset); coached scams and bad payees rely heavily on payee information, "
                "which is why the model is weaker when payee intelligence is poor (section 7)."),
              P("What each alert level means", H2),
              table([["Level", "Genuine payments here", "Scam payments here", "Alerts that are scams (simulation)", "at 1 in 10,000", "at 1 in 100,000"]] +
                    [[b["level"], pct(b["legit_share"]), pct(b["fraud_share"]), pct(b["precision_sim"]), pct(b["precision_1e-4"]), pct(b["precision_1e-5"])] for b in R["severity_bins"]], [0.22, 0.15, 0.15, 0.18, 0.15, 0.15]),
              P("The last two columns recompute precision at realistic scam rarity using the same genuine and scam score shares.", SMALL)]

    # 4 every case
    story += [PageBreak(), P("4. Every case", H1), P("4a. Every scam type", H2), img("by_scam_type.png", 0.9),
              table([["Scam", "Family", "Inc.", "Caught", "First pay.", "Prompt+", "Server-only", "Loud", "Mid", "Stealthy"]] +
                    [[r["type"], r["family"], r["overall"]["incidents"], pct(r["overall"]["caught"]), pct(r["overall"]["first_payment_caught"]), pct(r["medium"]["caught"]), pct(r["server_only"]["caught"])] +
                     [pct(r["by_stealth"][b]["caught"]) + f" (n={r['by_stealth'][b]['incidents']})" if r["by_stealth"][b]["incidents"] else "n/a" for b in ("loud", "mid", "stealthy")] for r in R["types"]],
                    [0.17, 0.09, 0.06, 0.09, 0.09, 0.09, 0.1, 0.11, 0.1, 0.1]),
              P("'Caught' means at least one payment of the incident got a strong action; stealthier incidents make more payments, so compare 'First pay.' across bands. n is the number of incidents; small n means wide uncertainty.", SMALL),
              P("4b. Every genuine situation", H2), img("genuine_friction.png", 0.86),
              table([["Situation", "Payments", "Prompt+ (full)", "Strong (full)", "Strong (server-only)", "Strong (old rules)"]] +
                    [[r["situation"], f"{r['n']:,}", pct(r["full"]["MEDIUM"]), pct(r["full"]["HIGH"]), pct(r["server_only"]["HIGH"]), pct(r["rules"]["HIGH"])] for r in sorted(R["legit_situations"], key=lambda r: -r["full"]["HIGH"])],
                    [0.28, 0.12, 0.15, 0.15, 0.15, 0.15]),
              PageBreak(), P("4c. Every user segment", H2), img("segments.png", 0.9),
              P(f"Overall, {pct(ov['legit_high'])} of genuine payments get a strong action. The gate is that no segment exceeds 1.5 times that ({pct(ov['legit_high'] * 1.5)}).")]
    rows = [["Segment", "Genuine payments", "Prompt+", "Strong", "x overall", "Incidents", "Caught", "First pay."]]
    for r in R["segments"]:
        ratio = r["legit_high"] / ov["legit_high"] if ov["legit_high"] else float("nan")
        rows.append([r["segment"], f"{r['legit_n']:,}", pct(r["legit_medium"]), pct(r["legit_high"]), f"{ratio:.1f}x" + (" OVER" if ratio > 1.5 else ""), r["fraud_incidents"], pct(r["caught"]), pct(r["first_payment"])])
    story += [table(rows, [0.3, 0.12, 0.09, 0.09, 0.1, 0.09, 0.1, 0.11])]
    over = [r["segment"] for r in R["segments"] if ov["legit_high"] and r["legit_high"] / ov["legit_high"] > 1.5]
    story += [P(("Segments above the gate: " + "; ".join(over) + ". These are the first things to fix.") if over else "No segment exceeds the gate.", BODY)]

    # worked examples
    story += [PageBreak(), P("4d. Worked examples (real rows from the test month)", H2),
              P("Each row shows the main inputs, the three surprises, the outcome, and the inputs that pushed the score up most. Values equal to zero or missing are omitted.", SMALL)]
    rows = [["Case", "Main inputs", "Surprise T / C / P", "Result", "Why"]]
    for e in R["examples"]:
        inp = ", ".join(f"{k}={v:g}" for k, v in e["inputs"].items() if v not in (None, 0.0))
        s = e["surprise"]
        rows.append([e["case"], inp, f"{s['takeover']} / {s['coached']} / {s['payee']}", f"{e['severity']} ({e['family']})", ", ".join(f"{f} +{v}" for f, v in e["reasons"]) or "-"])
    story += [table(rows, [0.15, 0.37, 0.12, 0.12, 0.24])]

    # 5 real-world
    story += [PageBreak(), P("5. Is this true for the real world?", H1),
              P("<b>We cannot say, and nothing here should be quoted as real-world accuracy.</b> Real labelled UPI fraud data is not public. "
                "The strongest honest evidence available without it is: how our simulated world compares with reported real figures (5a), which assumptions the results lean on (5b), "
                "and how the model behaves when each assumption is wrong (section 7).", NOTE),
              P("5a. Simulated world versus reported real-world figures", H2),
              table([["Quantity", "Simulated", "Reported real world", "Comment"],
                     ["Scams as a share of all payments", f"{100 * rc['sim_prevalence']:.2f}%", "about 0.001% (13.42 lakh UPI fraud incidents in FY24, as reported, against roughly 131 billion payments, my recollection of NPCI data: check)", f"simulation is about {rc['sim_prevalence'] / 1e-5:,.0f}x richer in scams; friction rates unaffected, precision is"],
                     ["Coached-scam share of incidents / rupees", f"{pct(rc['incident_share_by_family']['coached'])} / {pct(rc['rupee_share_by_family']['coached'])}", "investment-type about 77% and digital arrest about 8% of 2025 reported losses (news reports of portal data)", "same direction; exact split not calibrated"],
                     ["Investment/task + romance share of scam rupees", pct(rc["rupee_share_by_type"]["task_invest"] + rc["rupee_share_by_type"]["romance"]), "about 77%", "simulation under-represents long-running investment losses"],
                     ["Digital arrest share of rupees", pct(rc["rupee_share_by_type"]["digital_arrest"]), "about 8%", f"simulation over-weights digital arrest by about {rc['rupee_share_by_type']['digital_arrest'] / 0.08:.1f}x"],
                     ["Incidents never reported", pct(rc["unreported_share"]), "over half of victims did not report (survey)", "real label noise is likely worse; section 7 tests 40%"]],
                    [0.22, 0.14, 0.36, 0.28]),
              P(f"Because the simulated loss mix is far from the reported one, the 'rupees saved' headline ({pct(hi['rupees_saved'])}) is not representative. Reweighted to the reported mix "
                f"(investment-type 77% of rupees, digital arrest 8%, the other 15% split in simulated proportions), the model would have saved about <b>{pct(rw)}</b> of rupees on this simulated month. "
                "Illustrative only: it assumes the per-type catch rates hold in the real world.", BODY),
              P("5b. The assumptions the results lean on", H2),
              table([["Assumption", "Value used", "Source", "How much it matters"],
                     ["Government fraud-risk data flags scam payees", "75% of new mule accounts, 40% of aged ones", "no public coverage figure", "large for coached scams"],
                     ["Scam payees are young accounts with many senders", "80% minus stealth", "reported mule behaviour, not measured", "large (see the aged-account worlds)"],
                     ["Takeover attacker's phone runs the telemetry SDK", "60%", "none", "small"],
                     ["Genuine users on a call while paying", "4%", "none", "medium"],
                     ["Genuine phone change", "15% of users in 5 months", "none", "medium; largest friction source"],
                     ["Scam mix", "28% takeover, 63% coached, 8% payee", "loosely from reported loss mix", "medium"],
                     ["Victims who never report", "15%", "reported: over half in a survey", "medium"]], [0.3, 0.25, 0.25, 0.2]),
              PageBreak(), P("6. What real rarity does to alerts", H1), img("precision_vs_prevalence.png", 0.8),
              P(f"At about 1 in 100,000 payments being a scam, a strong action would still hit about {pct(h['fpr_HIGH'])} of genuine payments. That is roughly "
                f"<b>{h['fpr_HIGH'] * 1e5 / h['txn_recall_HIGH']:,.0f} genuine payments given a strong action for every scam payment stopped</b>. "
                "This is the central real-world risk of any detector like this, and it is why the strong action has to be cheap and kind (a face check, a short wait), why the prompt level is only a prompt, "
                "and why a real pilot must measure the genuine-user cost first. Example: a user making 40 payments a month and a 0.2% strong-action rate would see one strong action about every 12 months.")]

    # 7 shifts
    if S:
        story += [P("7. What if my assumptions are wrong? (same model, same thresholds, different worlds)", H1), img("shifts.png", 0.95),
                  table([["World", "Genuine strong", "Incidents", "Caught", "First pay.", "Takeover / coached / payee caught"]] +
                        [[s["scenario"], pct(s["legit_high"]), s["incidents"], pct(s["caught"]), pct(s["first_payment"]),
                          f"{pct(s['family_caught']['takeover'])} / {pct(s['family_caught']['coached'])} / {pct(s['family_caught']['payee'])}"] for s in S], [0.32, 0.12, 0.09, 0.1, 0.1, 0.27]),
                  P("The thresholds are fixed at what was set on the original world. If the strong-action rate for genuine payments drifts far from 0.3% in a row, real users would feel it; "
                    "if catch collapses, the model leans on an assumption that may not hold.", SMALL)]

    # 8 path to real
    story += [P("8. What it would take to make this real", H1)]
    story += bullets(["Shadow mode at a partner bank or UPI app (<font name='Courier'>MFA_ATO_SHADOW=1</font>): score every payment, add no friction, log outcomes.",
                      "After 30 to 60 days, join the scores to confirmed fraud (complaints, disputes) and measure catch and false alarms per segment on real labels.",
                      "Re-derive the three thresholds from real genuine traffic so the friction budget holds.",
                      "Retrain on real labels with the same pipeline (only the data loader changes) and compare with this document; large gaps show which assumptions were wrong.",
                      "Check every segment in section 4c on real users, including age bands if the partner can supply them under consent."])
    doc = BaseDocTemplate(str(OUT), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                          title="Payment-risk model evidence", author="Poojan Pandya", subject="Statistics, every-case results and real-world relevance (simulated data)")
    doc.addPageTemplates([PageTemplate(id="m", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")], onPage=footer)])
    doc.build(story)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
