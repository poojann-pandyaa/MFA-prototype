package com.demopay.app.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.demopay.app.data.Challenge
import com.demopay.app.data.Payment
import com.demopay.app.logic.mmss
import com.demopay.app.logic.rupees
import com.demopay.app.logic.secondsUntil
import com.demopay.app.logic.warningReady
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject

@Composable
fun PayScreen(vm: AppViewModel) {
    val p = vm.payment
    AppScaffold("Payment", onBack = { vm.back() }) {
        if (p == null) {
            Column(Modifier.padding(16.dp)) { if (vm.error != null) Notice(vm.error!!, error = true) else Small("Loading...") }
            return@AppScaffold
        }
        // Start the current step's challenge as soon as it becomes the current step.
        LaunchedEffect(p.id, p.currentStep, p.status, vm.challenge == null) {
            if (p.status == "pending" && vm.challenge == null) vm.startStep()
        }
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Summary(p)
            vm.error?.let { Notice(it, error = true) }
            when (p.status) {
                "pending" -> PendingBody(vm, p)
                "approved" -> {
                    if (p.plan.steps.isNotEmpty()) GoodNotice("Safety checks complete.")
                    PinPad(vm.busy) { vm.complete(it) }
                    TextButton(onClick = { vm.cancelPayment() }, modifier = Modifier.fillMaxWidth()) { Text("Cancel this payment") }
                }
                "completed" -> Done(
                    "Payment sent", "${rupees(p.amount)} sent to ${p.registeredName ?: p.payeeVpa}." + (vm.paidBalance?.let { " New balance ${rupees(it)}." } ?: ""), true, vm,
                )
                "blocked" -> Done("Payment stopped", p.message, false, vm)
                "cancelled" -> Done("Payment cancelled", "", false, vm)
                else -> Small("Status: ${p.status}")
            }
            if (vm.me?.demo == true) XRayPanel(vm)
        }
    }
}

@Composable
private fun Summary(p: Payment) {
    SectionCard {
        Text(rupees(p.amount), fontSize = 28.sp, fontWeight = FontWeight.SemiBold)
        Text("to ${p.registeredName ?: p.payeeVpa}", fontWeight = FontWeight.Medium)
        Small("${p.payeeVpa} · " + when (p.channel) { "collect" -> "collect request"; "qr" -> "scanned QR"; else -> "payment" })
    }
}

@Composable
private fun Done(title: String, body: String, ok: Boolean, vm: AppViewModel) {
    SectionCard {
        Text(if (ok) "✔ $title" else "⚠ $title", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = if (ok) Good else Danger, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        if (body.isNotEmpty()) Text(body, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        PrimaryButton("Back to home") { vm.back() }
    }
}

@Composable
private fun PendingBody(vm: AppViewModel, p: Payment) {
    val steps = p.plan.steps
    val current = p.currentStep ?: 0
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Small("Safety check: step ${current + 1} of ${steps.size}" + if (p.plan.timeSeconds > 0) " · about ${p.plan.timeSeconds.toInt()} seconds" else "")
        LinearProgressIndicator(progress = { if (steps.isEmpty()) 0f else p.stepsDone.toFloat() / steps.size }, modifier = Modifier.fillMaxWidth().height(6.dp), drawStopIndicator = {})
    }
    val ch = vm.challenge
    if (ch == null) {
        Small("Preparing the next check...")
    } else when (ch.method) {
        "warning" -> WarningStep(vm, ch)
        "typed_confirm" -> TypedConfirmStep(vm, ch)
        "payee_check" -> PayeeCheckStep(vm, ch)
        "cooling_off" -> HoldStep(vm, ch)
        else -> Notice("This check (${ch.method}) is not supported in this version of the app.", error = true)
    }
    if (steps.getOrNull(current)?.tier != "slow") TextButton(onClick = { vm.cancelPayment() }, modifier = Modifier.fillMaxWidth()) { Text("Cancel this payment") }
}

@Composable
private fun WarningStep(vm: AppViewModel, ch: Challenge) {
    val d = ch.data
    val questions = d.getJSONArray("questions").let { a -> (0 until a.length()).map { a.getJSONObject(it).let { q -> q.getString("id") to q.getString("text") } } }
    var left by remember(ch.nonce) { mutableIntStateOf(Math.ceil(d.getDouble("min_read_seconds")).toInt()) }
    var ack by remember(ch.nonce) { mutableStateOf(false) }
    val answers = remember(ch.nonce) { mutableStateOf<Map<String, String>>(emptyMap()) }
    LaunchedEffect(ch.nonce) { while (left > 0) { delay(1000); left-- } }
    SectionCard {
        Text("⚠ ${d.getString("title")}", fontWeight = FontWeight.SemiBold, color = Navy)
        Notice(d.getString("body"))
        questions.forEach { (id, text) ->
            Text(text, fontSize = 14.sp)
            Row(verticalAlignment = Alignment.CenterVertically) {
                RadioButton(answers.value[id] == "no", { answers.value = answers.value + (id to "no") }); Text("No")
                Text("   ")
                RadioButton(answers.value[id] == "yes", { answers.value = answers.value + (id to "yes") }); Text("Yes")
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) { Checkbox(ack, { ack = it }); Text("I have read this warning.", fontSize = 14.sp) }
        vm.stepMessage?.let { Notice(it, error = true) }
        val ready = warningReady(left, ack, answers.value, questions.map { it.first })
        PrimaryButton(if (left > 0) "Please read ($left)" else if (vm.busy) "Checking..." else "Continue", enabled = ready && !vm.busy) {
            val body = JSONObject().put("acknowledged", true)
            body.put("answers", JSONObject(answers.value as Map<*, *>))
            vm.answerStep(body)
        }
        Small(d.getString("helpline"))
    }
}

@Composable
private fun TypedConfirmStep(vm: AppViewModel, ch: Challenge) {
    var typed by remember(ch.nonce) { mutableStateOf("") }
    SectionCard {
        Text("Confirm who you are paying", fontWeight = FontWeight.SemiBold, color = Navy)
        Text(ch.data.getString("prompt"), fontSize = 14.sp)
        Small("Payee id: ${ch.data.getString("payee_vpa")}")
        OutlinedTextField(typed, { typed = it }, singleLine = true, label = { Text("Registered name") }, modifier = Modifier.fillMaxWidth())
        vm.stepMessage?.let { Notice(it, error = true) }
        PrimaryButton(if (vm.busy) "Checking..." else "Confirm", enabled = typed.isNotBlank() && !vm.busy) { vm.answerStep(JSONObject().put("typed", typed)) }
    }
}

@Composable
private fun PayeeCheckStep(vm: AppViewModel, ch: Challenge) {
    val d = ch.data
    SectionCard {
        Text("Check the payee", fontWeight = FontWeight.SemiBold, color = Navy)
        LabelValue("Payee id", d.getString("payee_vpa"))
        LabelValue("Registered name", if (d.isNull("registered_name")) "not found" else d.getString("registered_name"))
        if (!d.isNull("age_days")) {
            val age = d.getDouble("age_days")
            LabelValue("Account age", if (age < 30) "${age.toInt()} day(s), very new" else "${age.toInt()} days")
        }
        if (!d.isNull("similar_to")) Notice("Looks very similar to a payee you may know: ${d.getString("similar_to")}. Did you mean that one?")
        if (d.optBoolean("reported")) Notice("This account has been reported before.", error = true)
        Text(d.getString("question"), fontWeight = FontWeight.Medium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SecondaryButton("No, stop", Modifier.weight(1f)) { vm.answerStep(JSONObject().put("confirmed", false)) }
            PrimaryButton("Yes, continue", enabled = !vm.busy, modifier = Modifier.weight(1f)) { vm.answerStep(JSONObject().put("confirmed", true)) }
        }
    }
}

@Composable
private fun HoldStep(vm: AppViewModel, ch: Challenge) {
    val d = ch.data
    var left by remember(ch.nonce) { mutableIntStateOf(secondsUntil(d.getString("release_at"), System.currentTimeMillis())) }
    LaunchedEffect(ch.nonce) { while (left > 0) { delay(500); left = secondsUntil(d.getString("release_at"), System.currentTimeMillis()) } }
    SectionCard {
        Text("⏳ This payment is on a short hold", fontWeight = FontWeight.SemiBold, color = Navy)
        Text(mmss(left), fontSize = 40.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth())
        if (d.optBoolean("shortened_for_demo")) {
            Notice("Demo: the real hold is ${d.getInt("real_hold_seconds") / 60} minutes. It is shortened to ${d.getInt("hold_seconds")} seconds here.")
        }
        val tips: JSONArray = d.getJSONArray("tips")
        (0 until tips.length()).forEach { Text("• ${tips.getString(it)}", fontSize = 13.sp) }
        vm.stepMessage?.let { Notice(it) }
        PrimaryButton(if (left > 0) "Please wait..." else "Continue", enabled = left == 0 && !vm.busy) { vm.answerStep(JSONObject()) }
        TextButton(onClick = { vm.cancelPayment() }, modifier = Modifier.fillMaxWidth()) { Text("Cancel this payment") }
    }
}

@Composable
private fun XRayPanel(vm: AppViewModel) {
    val x = vm.xray ?: return
    SectionCard {
        Text("Model X-ray (demo only)", fontWeight = FontWeight.SemiBold, color = Navy)
        Small("What the model saw and why. A real app never shows this to the user. All data is simulated.", color = AmberText)
        Text("${x.severity}  ·  threat: ${x.threat}  ·  model: ${x.model}", fontWeight = FontWeight.Medium)
        listOf("takeover" to "Takeover (someone else paying)", "coached" to "Coached (owner being tricked)", "payee" to "Bad payee").forEach { (k, label) ->
            val v = x.surprises[k] ?: 0.0
            Column {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Small(label); Small("%.1f".format(v)) }
                LinearProgressIndicator(progress = { (v / 12.0).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth().height(8.dp), drawStopIndicator = {})
            }
        }
        x.thresholds?.let { t -> Small("Levels: MEDIUM ${t["MEDIUM"]}, HIGH ${t["HIGH"]}, CRITICAL ${t["CRITICAL"]} (set so few genuine payments reach each)") }
        Text("Decision engine", fontWeight = FontWeight.Medium)
        Small("Chance this is a scam: ${"%.3f".format(x.plan.pScam * 100)}% (base rate 1 in ${"%,d".format(Math.round(1 / x.plan.baseRate))})")
        Text("Decision: ${x.plan.decision}  ·  expected cost Rs ${x.plan.expectedCost}", fontSize = 13.sp)
        x.plan.steps.forEach { s ->
            Column {
                Text("${s.index + 1}. ${s.name}  (${s.tier}, ~${if (s.estSeconds >= 60) "${(s.estSeconds / 60).toInt()} min" else "${s.estSeconds.toInt()}s"})", fontSize = 13.sp, fontWeight = FontWeight.Medium)
                Small(s.why)
                Small("Doubt after this step: ${"%.3f".format(s.doubtAfter * 100)}%")
            }
        }
        x.plan.notes.forEach { Small(it) }
        if (x.reasons.isNotEmpty()) {
            Text("What pushed the score up", fontWeight = FontWeight.Medium)
            x.reasons.forEach { (k, v) -> Small("${k.replace('_', ' ')} (+$v)") }
        }
        Text("Inputs sent to the model", fontWeight = FontWeight.Medium)
        x.inputs.forEach { (k, v) -> LabelValue(k.replace('_', ' '), v) }
    }
}
