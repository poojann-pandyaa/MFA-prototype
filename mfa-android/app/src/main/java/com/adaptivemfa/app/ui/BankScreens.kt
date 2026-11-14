package com.adaptivemfa.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.adaptivemfa.app.BuildConfig
import com.adaptivemfa.app.data.ActivityItem
import com.adaptivemfa.app.data.Me
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.greeting
import com.adaptivemfa.app.logic.pressKey
import com.adaptivemfa.app.logic.rupeesParts
import com.adaptivemfa.app.logic.shortName
import com.adaptivemfa.app.logic.statusLabel
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val BILL_ICONS = mapOf("BILL-ELEC" to "bolt", "BILL-MOBILE" to "phone", "BILL-GAS" to "drop")

private fun at(epochSeconds: Double): LocalDateTime = LocalDateTime.ofInstant(Instant.ofEpochMilli((epochSeconds * 1000).toLong()), ZoneId.systemDefault())
private fun whenText(t: Double) = at(t).format(DateTimeFormatter.ofPattern("d MMM, h:mm a", Locale.ENGLISH))
private fun dayLabel(t: Double): String {
    val d = at(t).toLocalDate()
    val today = LocalDate.now()
    return when (d) { today -> "Today"; today.minusDays(1) -> "Yesterday"; else -> d.format(DateTimeFormatter.ofPattern("d MMM", Locale.ENGLISH)) }
}

// ---------------------------------------------------------------- home

@Composable
fun HomeScreen(vm: AppViewModel) {
    val me = vm.me
    var hidden by rememberSaveable { mutableStateOf(false) }
    Page {
        if (me == null) { Busy("Loading..."); return@Page }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Column { Muted(greeting(java.time.LocalTime.now().hour)); H2(me.name.substringBefore(' ')) }
            Box(Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(C.card).clickable(role = Role.Button) { vm.tab("profile") }, contentAlignment = Alignment.Center) {
                Avatar(me.name.split(" ").mapNotNull { it.firstOrNull() }.take(2).joinToString("").uppercase(), 36)
            }
        }
        Messages(vm)
        BalanceCard(me, hidden) { hidden = !hidden }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            Quick("send", "Send") { vm.tab("pay") }
            Quick("bill", "Pay bills") { vm.go(Screen.Pay(bills = true)) }
            Quick("activity", "Activity") { vm.tab("activity") }
            Quick("shield", "Security") { vm.tab("profile") }
        }
        val people = (me.recentPayees.ifEmpty { me.payees }).take(5)
        if (people.isNotEmpty()) {
            H3("Pay again")
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                people.forEach { p ->
                    Column(Modifier.width(60.dp).clickable(role = Role.Button) { vm.go(Screen.Amount("transfer", p.ref, p.display, p.initials)) },
                        horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Avatar(p.initials, 52)
                        Text(shortName(p.display), color = C.ink, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
                    }
                }
            }
        }
        if (me.protection != "Strong") CardBox {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("shieldAlert", "warn")
                Column(Modifier.weight(1f)) {
                    H3("Protection: ${me.protection.ifBlank { "Basic" }}")
                    if (me.canAdd.isNotEmpty()) Muted(if (me.faceEnrolled) "Voice and typing can be added on the web bank" else "Add your face to make it stronger", small = true)
                }
                if (!me.faceEnrolled) SoftBtn("Add") { vm.startFaceSetup() }
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            H3("Recent"); LinkText("See all", small = true) { vm.tab("activity") }
        }
        val recent = vm.activity.take(3)
        if (recent.isEmpty()) CardBox { Muted("Your payments will show up here.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
        else ListCard(recent.map { { ActivityRow(it) } })
    }
}

@Composable
private fun BalanceCard(me: Me, hidden: Boolean, toggle: () -> Unit) {
    val (whole, paise) = rupeesParts(me.balanceMinor)
    val used = (100.0 * me.sentTodayMinor / maxOf(me.dailyLimitMinor, 1)).coerceIn(0.0, 100.0).toFloat() / 100f
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp)).background(C.brand).padding(20.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("Savings · ${me.ref}", color = C.brandInk, fontSize = 13.sp)
            Box(Modifier.size(36.dp).clip(RoundedCornerShape(12.dp)).background(C.brandInk.copy(alpha = 0.16f)).clickable(role = Role.Button, onClick = toggle), contentAlignment = Alignment.Center) {
                Ic(if (hidden) "eye" else "eyeOff", C.brandInk, 18.dp, if (hidden) "Show balance" else "Hide balance")
            }
        }
        Row(Modifier.padding(top = 6.dp, bottom = 14.dp), verticalAlignment = Alignment.Bottom) {
            if (hidden) Text("₹ • • • • •", color = C.brandInk, fontSize = 34.sp, fontWeight = FontWeight.ExtraBold)
            else { Text("₹$whole", color = C.brandInk, fontSize = 34.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-1).sp); Text(paise, color = C.brandInk.copy(alpha = 0.8f), fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, modifier = Modifier.padding(bottom = 4.dp)) }
        }
        Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(3.dp)).background(C.brandInk.copy(alpha = 0.22f))) {
            Box(Modifier.fillMaxWidth(used).height(6.dp).background(C.brandInk))
        }
        Text("${formatMoney(me.sentTodayMinor)} of ${formatMoney(me.dailyLimitMinor)} daily limit used today", Modifier.padding(top = 8.dp), color = C.brandInk.copy(alpha = 0.9f), fontSize = 13.sp)
    }
}

@Composable
private fun Quick(icon: String, label: String, onClick: () -> Unit) {
    Column(Modifier.clickable(role = Role.Button, onClick = onClick).padding(4.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Box(Modifier.size(56.dp).clip(RoundedCornerShape(18.dp)).background(C.card), contentAlignment = Alignment.Center) { Ic(icon, C.brand, 24.dp) }
        Text(label, color = C.ink, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun ActivityRow(a: ActivityItem) {
    val (label, tone) = statusLabel(a.status)
    val money = a.kind == "transfer" || a.kind == "bill"
    Item(
        lead = { if (a.kind == "transfer") Avatar(a.initials) else Tile(if (a.kind == "bill") "bolt" else "shield", "grey") },
        title = a.title.ifBlank { a.payee },
        sub = { if (a.status == "DONE") SubText(whenText(a.createdAt)) else Chip(label, tone) },
        trail = if (money) ({ Text((if (a.status == "DONE") "−" else "") + formatMoney(a.amountMinor), color = if (a.status in setOf("DECLINED", "EXPIRED", "CANCELLED", "FAILED")) C.muted else C.ink, fontSize = 15.sp, fontWeight = FontWeight.ExtraBold) }) else null,
    )
}

// ---------------------------------------------------------------- pay: who, how much, review

@Composable
fun PayScreen(vm: AppViewModel, s: Screen.Pay) {
    val me = vm.me ?: return
    var query by rememberSaveable { mutableStateOf("") }
    val q = query.trim().lowercase()
    val people = me.payees.filter { it.display.lowercase().contains(q) }
    val bills = me.billers.filter { it.display.lowercase().contains(q) }
    Page {
        TopBar(title = if (s.bills) "Pay a bill" else "Who are you paying?")
        Messages(vm)
        Field("Search", query, { query = it }, placeholder = "Name or biller")
        val sections: List<@Composable () -> Unit> = listOf(
            {
                SectionLabel("People")
                if (people.isEmpty()) CardBox { Muted(if (q.isEmpty()) "No saved payees yet." else "No payee matches that.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
                else ListCard(people.map { p -> { Item({ Avatar(p.initials) }, p.display, { SubText(if (me.recentPayees.any { it.ref == p.ref }) "Paid recently" else "Saved payee") }, chevron = true) { vm.go(Screen.Amount("transfer", p.ref, p.display, p.initials)) } } })
            },
            {
                SectionLabel("Bills")
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    bills.chunked(3).forEach { row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEach { b ->
                                Column(Modifier.weight(1f).clip(RoundedCornerShape(18.dp)).background(C.card).clickable(role = Role.Button) { vm.go(Screen.Amount("bill", b.ref, b.display, "")) }.padding(vertical = 14.dp, horizontal = 8.dp),
                                    horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Ic(BILL_ICONS[b.ref] ?: "bill", C.brand, 24.dp)
                                    Text(b.display, color = C.ink, fontSize = 13.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                                }
                            }
                            repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
                        }
                    }
                    if (bills.isEmpty()) CardBox { Muted("No biller matches that.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
                }
            },
        )
        (if (s.bills) sections.reversed() else sections).forEach { it() }
    }
}

@Composable
fun AmountScreen(vm: AppViewModel, s: Screen.Amount) {
    val me = vm.me ?: return
    var text by rememberSaveable { mutableStateOf("") }
    val minor = Math.round((text.ifEmpty { "0" }.toDoubleOrNull() ?: 0.0) * 100)
    val left = maxOf(0L, me.dailyLimitMinor - me.sentTodayMinor)
    val over = if (minor > left) "That's more than today's limit left (${formatMoney(left)})." else if (minor > me.balanceMinor) "That's more than your balance." else ""
    val shown = if (text.isEmpty()) "0" else text.substringBefore('.').toLong().let { String.format(Locale.US, "%,d", it) } + (if (text.contains('.')) "." + text.substringAfter('.') else "")
    Page(scroll = false) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconBtn("back", "Back") { vm.back() }
            Row(Modifier.weight(1f).clip(RoundedCornerShape(16.dp)).background(C.card).padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (s.kind == "bill") Tile(BILL_ICONS[s.ref] ?: "bill", size = 36) else Avatar(s.initials, 36)
                Column { Text(s.display, color = C.ink, fontWeight = FontWeight.Bold, maxLines = 1); Muted(if (s.kind == "bill") "Bill payment" else "Bank transfer (IMPS)", small = true) }
            }
        }
        Spacer(Modifier.weight(1f))
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Text("₹", color = C.muted, fontSize = 32.sp, fontWeight = FontWeight.ExtraBold, modifier = Modifier.padding(top = 4.dp, end = 4.dp))
                Text(shown, color = C.ink, fontSize = 52.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-2).sp)
            }
            Muted(over.ifEmpty { "${formatMoney(left)} of today's limit left" }, align = TextAlign.Center, color = if (over.isEmpty()) C.muted else C.bad)
        }
        Spacer(Modifier.weight(1f))
        Messages(vm)
        Keypad { text = pressKey(text, it) }
        Btn("Continue", enabled = minor > 0 && over.isEmpty(), busy = vm.busy) { vm.startPayment(s.kind, s.ref, minor / 100.0) }
    }
}

@Composable
fun ReviewScreen(vm: AppViewModel, s: Screen.Review) {
    val a = s.started.action
    val bill = a.kind == "bill"
    val n = a.checks.size
    Page {
        TopBar(title = if (bill) "Review bill payment" else "Review payment", onBack = { vm.back() })
        Messages(vm)
        CardBox {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (bill) Tile("bolt", size = 56) else Avatar(a.initials, 56)
                Muted(if (bill) "You're paying" else "You're sending")
                Text(formatMoney(a.amountMinor), color = C.ink, fontSize = 36.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-1).sp)
                Text("to ${a.payee}", color = C.ink, fontWeight = FontWeight.Bold)
            }
            Column { Kv("To", a.payee); Kv("From", "Savings account"); Kv("Type", if (bill) "Bill payment" else "IMPS transfer") }
        }
        CardBox {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("shieldCheck")
                Text(if (n > 0) "We'll confirm it's you in $n quick ${if (n == 1) "step" else "steps"}" else "We'll confirm it's you", color = C.ink, fontWeight = FontWeight.Bold)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { a.checks.forEachIndexed { i, c -> Chip("${i + 1} · ${c.replaceFirstChar { it.uppercase() }}") } }
            a.reason?.let { Muted(it) }
        }
        if (a.reviewAfter) Note("brand", "shieldClock", "After these steps, someone at Demo Bank will look at this before it goes through. Your money stays in your account until then.", "Then a quick check by our team")
        if (a.scamWarning) Note("warn", "warn", "Demo Bank will never ask you to move money to keep it safe, to share a code, or to pay to receive a refund or prize.", "Did someone ask you to make this payment?")
        Btn("Confirm and pay ${formatMoney(a.amountMinor)}", busy = vm.busy) { vm.confirmPayment() }
        LinkText("Cancel", modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.cancelReview() }
    }
}

@Composable
fun ResultScreen(vm: AppViewModel, s: Screen.Result) {
    val o = s.outcome
    val a = s.action
    Page {
        Spacer(Modifier.height(8.dp))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { ResultMark(o.kind, o.icon) }
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            H1(o.title, align = TextAlign.Center)
            Lede(o.text, align = TextAlign.Center)
        }
        if (a != null && a.status == "DONE") CardBox(padding = 16) {
            Column { Kv("When", whenText(a.createdAt)); Kv("Reference", a.id)
                if (a.confirmedWith.isNotEmpty()) Kv("Confirmed with", a.confirmedWith.joinToString(", ")) }
        }
        Btn(if (o.kind == "bad" && a != null && a.kind != "") "Try again" else "Done") { if (o.kind == "bad" && a != null) vm.go(Screen.Pay(a.kind == "bill")) else vm.go(Screen.Home) }
        if (o.kind == "bad") Btn("Back to home", BtnKind.Secondary) { vm.go(Screen.Home) }
    }
}

// ---------------------------------------------------------------- activity, profile, settings

@Composable
fun ActivityScreen(vm: AppViewModel) {
    var filter by rememberSaveable { mutableStateOf("all") }
    LaunchedEffect(Unit) { vm.refreshHome() }
    val shown = vm.activity.filter { filter == "all" || (filter == "money") == (it.kind == "transfer" || it.kind == "bill") }
    Page {
        TopBar(title = "Activity")
        Messages(vm)
        Segmented(listOf("all" to "All", "money" to "Money", "account" to "Account changes"), filter) { filter = it }
        if (shown.isEmpty()) CardBox { Text("Nothing here yet", color = C.ink, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()); Muted("Payments and account changes will appear here.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
        else shown.groupBy { dayLabel(it.createdAt) }.forEach { (day, items) -> SectionLabel(day); ListCard(items.map { { ActivityRow(it) } }) }
    }
}

@Composable
fun ProfileScreen(vm: AppViewModel) {
    val me = vm.me ?: return
    Page {
        TopBar(title = "Profile")
        Messages(vm)
        CardBox {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Avatar(me.name.split(" ").mapNotNull { it.firstOrNull() }.take(2).joinToString("").uppercase(), 56)
                Column { H3(me.name); Muted(me.ref, small = true) }
            }
        }
        CardBox {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("shieldCheck")
                Column(Modifier.weight(1f)) { H3("Protection: ${me.protection.ifBlank { "Basic" }}"); Muted("The ways you can confirm it's you", small = true) }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { me.have.forEach { Chip(it, "ok") } }
        }
        ListCard(listOf(
            { Item({ Tile("face") }, "Face check", { SubText(if (me.faceEnrolled) "Set up" else "Not set up yet") }, if (me.faceEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("FACE") } })) },
            { Item({ Tile("phone") }, "Voice", { SubText(if (me.voiceEnrolled) "Set up" else "Read three sentences aloud") }, if (me.voiceEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("VOICE") } })) },
            { Item({ Tile("lock") }, "Authenticator app", { SubText(if (me.authenticatorEnrolled) "Set up" else "Codes from an app on this phone") }, if (me.authenticatorEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("OTP") } })) },
            { Item({ Tile("key") }, "Typing", { SubText(if (me.typingEnrolled) "Set up" else "Your typing rhythm and swipe") }, if (me.typingEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("UEBA") } })) },
            { Item({ Tile("phone") }, "This phone", { SubText("Calls the bank at ${vm.store.baseUrl}") }, chevron = true) { vm.go(Screen.Settings) } },
        ))
        Btn("Sign out", BtnKind.Secondary) { vm.logout() }
    }
}

@Composable
fun SettingsScreen(vm: AppViewModel) {
    var url by rememberSaveable { mutableStateOf(vm.store.baseUrl) }
    Page {
        TopBar(title = "Settings", onBack = { vm.back() })
        Messages(vm)
        CardBox {
            H3("Bank server address")
            Field("Bank URL", url, { url = it }, enabled = !vm.busy)
            Muted("Android emulator: http://10.0.2.2:8050 (your computer's localhost). Real phone: http://<your computer's Wi-Fi IP>:8050, same Wi-Fi. AMFA's own address comes from the bank; when it says 127.0.0.1 the app uses the bank's host instead.", small = true)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SoftBtn("Save") { if (vm.saveBaseUrl(url)) { url = vm.store.baseUrl; vm.notice = "Saved" } }
                SoftBtn(if (vm.busy) "Testing..." else "Test connection") { vm.testConnection(url) }
            }
        }
        CardBox {
            H3("About")
            Muted("Adaptive MFA ${BuildConfig.VERSION_NAME}", small = true)
            Muted("Device id: ${vm.store.deviceId}", small = true)
            Muted("Face detection, liveness and matching all run on AMFA's servers. The app only captures camera frames.", small = true)
        }
    }
}
