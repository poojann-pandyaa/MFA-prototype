package com.demopay.app.logic

/** One-tap demo scenarios (same set as the web app). Each fills the Send form; the presenter then taps Pay. */
data class Scenario(
    val name: String, val persona: String, val payee: String, val amount: Double, val channel: String = "pay",
    val signals: Signals = Signals(), val baseRate: Double? = null, val hint: String,
)

val SCENARIOS: List<Scenario> = listOf(
    Scenario("Routine payment", "asha", "priya.sharma@okaxis", 300.0, hint = "A friend you pay often. Expect no extra steps."),
    Scenario("New local shop", "asha", "bakery.joy@ybl", 450.0, hint = "A new but genuine shop. Expect no extra steps."),
    Scenario("Fake refund (blatant)", "asha", "refund.desk@ybl", 9000.0, channel = "collect",
        signals = Signals(callActive = true, callMinutes = 6.0, callUnknownNumber = true),
        hint = "A collect request from a 2-day-old reported account. Expect the payment to be stopped."),
    Scenario("Digital arrest", "ramesh", "officer.verify@upi", 20000.0,
        signals = Signals(callActive = true, callMinutes = 25.0, callUnknownNumber = true),
        hint = "A long call with an unknown number and a new, unreported account. Expect a few safety steps."),
    Scenario("Digital arrest (large)", "ramesh", "officer.verify@upi", 60000.0,
        signals = Signals(callActive = true, callMinutes = 25.0, callUnknownNumber = true),
        hint = "Same call, bigger amount: the engine judges it too risky for any available check and stops it."),
    Scenario("Investment scam", "asha", "invest.profit99@ybl", 20000.0, hint = "A new investment-style account, no call."),
    Scenario("Look-alike payee", "asha", "priya.sharrna@okaxis", 4000.0, baseRate = 0.001,
        hint = "One letter off a friend's id. Uses scam rarity 1 in 1,000 so the engine reacts."),
    Scenario("Fake seller (often missed)", "kabir", "quickseller.deals@ybl", 6000.0, channel = "qr",
        hint = "An account that looks ordinary. The model can miss this, and so will the app: an honest weak case."),
    Scenario("Prize fee (often missed)", "asha", "lucky.draw.prize@ybl", 3000.0, hint = "A small fee to a fairly ordinary account. Often missed."),
    Scenario("SIM swap + new phone", "asha", "unknown.account@ybl", 60000.0,
        signals = Signals(simChangedHours = 2.0, deviceBoundHours = 1.0, pinResetHours = 1.0, oldDeviceActive = false, bioSimilarity = 0.3),
        hint = "Stopped for now. Identity checks (second device, face) are planned and would let the owner prove it is them."),
    Scenario("Remote control", "ramesh", "quickseller.deals@ybl", 30000.0,
        signals = Signals(remoteAccessActive = true, bioSimilarity = 0.25),
        hint = "Screen-share running with an unusual touch pattern. Stopped for now."),
)
