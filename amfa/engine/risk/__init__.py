"""The risk model: scores every transaction and sets its threat level (T0-T4).

features   the numbers the model sees, built identically for training and for live decisions
scorer     loads a checksum-verified model and scores one transaction (fails closed on any problem)
threat     score cut-offs -> T0-T4, the checks each level asks for, and the small-payment cap
scenarios  simulated customers, genuine look-alike situations and attacks for training
train      trains, evaluates and (if it passes the release gate) activates a model
export     writes the simulated dataset and a report of what it covers
"""
