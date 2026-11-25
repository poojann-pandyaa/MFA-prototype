"""DemoPay's UPI scam model (branch Research/face-auth), brought into the platform so the risk model can learn from it.

features.py, simulate.py and train.py are DemoPay's own files, ported unchanged apart from their import lines:
  simulate  13 UPI scam types in three families (takeover, coached scam, bad payee), with the phone signals a scam leaves
            (a call in progress, a screen-sharing app, a tampered phone) and payee intelligence (fraud-risk band, complaints,
            how many people paid the payee today)
  features  DemoPay's 46 inputs, used here only to rebuild DemoPay's own model for comparison
  train     DemoPay's three-detector trainer, used the same way

convert.py is new: it turns DemoPay's simulated payments into the platform's Event / Profile / signals, so one model can
learn from both simulators (see amfa/engine/risk/features.py, SIGNALS, and docs/merged-risk-model.md).
"""
