@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
package com.adaptivemfa.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.adaptivemfa.app.data.Learning
import com.adaptivemfa.app.data.Me
import androidx.compose.ui.text.input.KeyboardType
import com.adaptivemfa.app.logic.NO_CHECK_MAX
import com.adaptivemfa.app.logic.NO_CHECK_MIN
import com.adaptivemfa.app.logic.canCancel
import com.adaptivemfa.app.logic.formatMoney
import com.adaptivemfa.app.logic.helperStateText
import com.adaptivemfa.app.logic.parseHelperCode
import com.adaptivemfa.app.logic.parseNoCheck
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
        if (me.learning?.offerBiometrics == true && !me.faceEnrolled) {
            Note("brand", "shieldCheck", "Setting up a face check is optional. It lets us approve larger payments without extra steps.", "Want to do more with your account?")
            SoftBtn("Set up a face check") { vm.startSetup("FACE") }
        }
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
private fun ActivityRow(a: ActivityItem, onClick: (() -> Unit)? = null) {
    val (label, tone) = statusLabel(a.status)
    val money = a.kind == "transfer" || a.kind == "bill"
    Item(
        lead = { if (a.kind == "transfer") Avatar(a.initials) else Tile(if (a.kind == "bill") "bolt" else "shield", "grey") },
        title = a.title.ifBlank { a.payee },
        sub = { if (a.status == "DONE") SubText(whenText(a.createdAt)) else Chip(label, tone) },
        trail = if (money) ({ Text((if (a.status == "DONE") "−" else "") + formatMoney(a.amountMinor), color = if (a.status in setOf("DECLINED", "EXPIRED", "CANCELLED", "FAILED")) C.muted else C.ink, fontSize = 15.sp, fontWeight = FontWeight.ExtraBold) }) else null,
        chevron = onClick != null, onClick = onClick,
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
    // On a tall phone the amount sits centred between the header and the keypad; on a short one (or with large text) there is no room to
    // centre, so the page scrolls and Continue is always reachable.
    val tall = androidx.compose.ui.platform.LocalConfiguration.current.screenHeightDp >= 720 && androidx.compose.ui.platform.LocalDensity.current.fontScale <= 1.15f
    val gap: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit = { if (tall) Spacer(Modifier.weight(1f)) else Spacer(Modifier.height(8.dp)) }
    Page(scroll = !tall) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            IconBtn("back", "Back") { vm.back() }
            Row(Modifier.weight(1f).clip(RoundedCornerShape(16.dp)).background(C.card).padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (s.kind == "bill") Tile(BILL_ICONS[s.ref] ?: "bill", size = 36) else Avatar(s.initials, 36)
                Column { Text(s.display, color = C.ink, fontWeight = FontWeight.Bold, maxLines = 1); Muted(if (s.kind == "bill") "Bill payment" else "Bank transfer (IMPS)", small = true) }
            }
        }
        gap()
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Text("₹", color = C.muted, fontSize = 32.sp, fontWeight = FontWeight.ExtraBold, modifier = Modifier.padding(top = 4.dp, end = 4.dp))
                Text(shown, color = C.ink, fontSize = 52.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-2).sp)
            }
            Muted(over.ifEmpty { "${formatMoney(left)} of today's limit left" }, align = TextAlign.Center, color = if (over.isEmpty()) C.muted else C.bad)
        }
        gap()
        Messages(vm)
        Keypad { text = pressKey(text, it) }
        Btn("Continue", enabled = minor > 0 && over.isEmpty(), busy = vm.busy) { vm.startPayment(s.kind, s.ref, minor / 100.0) }
    }
}

@Composable
fun ReviewScreen(vm: AppViewModel, s: Screen.Review) {
    val a = s.started.action
    val bill = a.kind == "bill"
    val limit = a.kind == "nocheck"
    val helper = a.kind == "helper" || a.kind == "device"
    val n = a.checks.size
    Page {
        TopBar(title = if (a.kind == "device") "Verify this phone" else if (helper) "Review your helper" else if (limit) "Review your limit" else if (bill) "Review bill payment" else "Review payment", onBack = { vm.back() })
        Messages(vm)
        if (helper) CardBox {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Tile("shieldCheck", size = 56)
                Text(a.title, color = C.ink, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
                Muted("Nothing is paid or changed in your money", small = true)
            }
        } else if (limit) CardBox {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Tile("shieldCheck", size = 56)
                Muted("No extra checks up to")
                Text(formatMoney(a.amountMinor), color = C.ink, fontSize = 36.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-1).sp)
                Text("a day", color = C.ink, fontWeight = FontWeight.Bold)
            }
        } else CardBox {
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
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { a.checks.forEachIndexed { i, c -> Chip("${i + 1} · ${c.replaceFirstChar { it.uppercase() }}") } }
            a.reason?.let { Muted(it) }
        }
        if (a.reviewAfter) Note("brand", "shieldClock", "After these steps, someone at Demo Bank will look at this before it goes through. Your money stays in your account until then.", "Then a quick check by our team")
        if (a.scamWarning) Note("warn", "warn", "Demo Bank will never ask you to move money to keep it safe, to share a code, or to pay to receive a refund or prize.", "Did someone ask you to make this payment?")
        Btn(if (a.kind == "device") "Confirm and verify" else if (helper) "Confirm and add helper" else if (limit) "Confirm and set limit" else "Confirm and pay ${formatMoney(a.amountMinor)}", busy = vm.busy) { vm.confirmPayment() }
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
        a?.helperCode?.let { code ->
            CardBox {
                Muted("Code for their phone", small = true)
                Text(code.chunked(4).joinToString(" "), color = C.ink, fontSize = 34.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 2.sp)
                Muted("Works once, for 15 minutes", small = true)
            }
        }
        if (a != null && a.status == "DONE") CardBox(padding = 16) {
            Column { Kv("When", whenText(a.createdAt)); Kv("Reference", a.id)
                if (a.confirmedWith.isNotEmpty()) Kv("Confirmed with", a.confirmedWith.joinToString(", ")) }
        }
        Btn(if (o.kind == "bad" && a != null && a.kind != "") "Try again" else "Done") { if (o.kind == "bad" && a != null) vm.go(if (a.kind == "nocheck") Screen.NoCheck else if (a.kind == "helper") Screen.Helpers else if (a.kind == "device") Screen.Device else Screen.Pay(a.kind == "bill")) else vm.go(if (a?.kind == "helper") Screen.Helpers else if (a?.kind == "nocheck") Screen.Profile else Screen.Home) }
        if (o.kind == "bad") Btn("Back to home", BtnKind.Secondary) { vm.go(Screen.Home) }
    }
}

// ---------------------------------------------------------------- activity, profile, settings

@Composable
fun ActivityScreen(vm: AppViewModel) {
    var filter by rememberSaveable { mutableStateOf("all") }
    var openId by rememberSaveable { mutableStateOf<String?>(null) }   // the row whose sheet is open
    LaunchedEffect(Unit) { vm.refreshHome() }
    val shown = vm.activity.filter { filter == "all" || (filter == "money") == (it.kind == "transfer" || it.kind == "bill") }
    val open = vm.activity.firstOrNull { it.id == openId && canCancel(it.status) }   // closes by itself once it can no longer be cancelled
    Page {
        TopBar(title = "Activity")
        Messages(vm)
        Segmented(listOf("all" to "All", "money" to "Money", "account" to "Account changes"), filter) { filter = it }
        if (shown.isEmpty()) CardBox { Text("Nothing here yet", color = C.ink, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth()); Muted("Payments and account changes will appear here.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth()) }
        else shown.groupBy { dayLabel(it.createdAt) }.forEach { (day, items) -> SectionLabel(day); ListCard(items.map { a -> { ActivityRow(a, if (canCancel(a.status)) ({ openId = a.id }) else null) } }) }
    }
    if (open != null) CancelSheet(vm, open) { openId = null }
}

/** Opened from an Activity row that can still be cancelled: what it is, then a slide to cancel it, then a popup that asks once more. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CancelSheet(vm: AppViewModel, a: ActivityItem, onClose: () -> Unit) {
    val money = a.kind == "transfer" || a.kind == "bill"
    val (label, tone) = statusLabel(a.status)
    val name = a.title.ifBlank { a.payee }
    val who = a.payee.ifBlank { name }                   // the title already reads "Sent to ...", so the sentence below uses the payee
    var confirming by remember { mutableStateOf(false) }
    var resets by remember { mutableIntStateOf(0) }     // bumped when the popup is dismissed, so the thumb goes back to the start
    ModalBottomSheet(onDismissRequest = onClose, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = C.card, contentColor = C.ink) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 28.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(14.dp)) {
            if (a.kind == "transfer") Avatar(a.initials, 56) else Tile(if (a.kind == "bill") "bolt" else "shield", "grey", 56)
            if (money) Text(formatMoney(a.amountMinor), color = C.ink, fontSize = 28.sp, fontWeight = FontWeight.ExtraBold)
            Text(name, color = C.ink, fontSize = 16.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.Center)
            Chip(label, tone)
            Muted(if (a.status == "UNDER_REVIEW") "Our team is taking a quick look. Your money hasn't left your account." else "You haven't finished confirming this yet. Nothing has been sent.", align = TextAlign.Center, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(4.dp))
            SlideToCancel(if (money) "Slide to cancel this payment" else "Slide to cancel this request", onComplete = { confirming = true }, enabled = !vm.busy && !confirming, resetKey = resets)
            LinkText("Keep it", small = true) { onClose() }
        }
    }
    if (confirming) AlertDialog(
        onDismissRequest = { confirming = false; resets++ },
        containerColor = C.card, titleContentColor = C.ink, textContentColor = C.muted,
        title = { Text(if (money) "Cancel this payment?" else "Cancel this request?", fontWeight = FontWeight.Bold) },
        text = { Text(if (money) "${formatMoney(a.amountMinor)} to $who will not be sent. Your money stays in your account." else "$name will be cancelled and nothing will change.") },
        confirmButton = { TextButton(onClick = { confirming = false; onClose(); vm.cancelFromActivity(a) }) { Text("Yes, cancel it", color = C.bad, fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = { confirming = false; resets++ }) { Text("Keep it", color = C.ink) } },
    )
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
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) { me.have.forEach { Chip(it, "ok") } }
        }
        me.learning?.takeIf { it.learning }?.let { l ->
            CardBox {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Tile("shieldCheck")
                    Column(Modifier.weight(1f)) {
                        H3("Getting to know how you pay")
                        Muted(learningText(l), small = true)
                    }
                }
                LinearProgressIndicator(progress = { learningProgress(l) }, modifier = Modifier.fillMaxWidth(), color = C.brand, trackColor = C.lineSoft, drawStopIndicator = {})
            }
        }
        me.profile?.let { a ->
            ListCard(listOf({ Item({ Tile("key") }, "About you", { SubText(if (a.declared) "Your answers help us protect your account" else "Two quick questions, optional") },
                { Chip(if (a.declared) "Shared" else "Add", if (a.declared) "ok" else "") }, chevron = true) { vm.go(Screen.Questions) } }))
        }
        ListCard(listOf(
            { Item({ Tile("face") }, "Face check", { SubText(if (me.faceEnrolled) "Set up" else "Not set up yet") }, if (me.faceEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("FACE") } })) },
            { Item({ Tile("phone") }, "Voice", { SubText(if (me.voiceEnrolled) "Set up" else "Read three sentences aloud") }, if (me.voiceEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("VOICE") } })) },
            { Item({ Tile("lock") }, "Authenticator app", { SubText(if (me.authenticatorEnrolled) "Set up" else "Codes from an app on this phone") }, if (me.authenticatorEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("OTP") } })) },
            { Item({ Tile("key") }, "Typing", { SubText(if (me.typingEnrolled) "Set up" else "Your typing rhythm and swipe") }, if (me.typingEnrolled) ({ Chip("Ready", "ok") }) else ({ SoftBtn("Set up") { vm.startSetup("UEBA") } })) },
            { Item({ Tile("shieldCheck") }, "No extra checks", { SubText(noCheckSummary(me)) }, chevron = true) { vm.go(Screen.NoCheck) } },
            { Item({ Tile("key") }, "Family helper", { SubText(helpersSummary(me)) }, chevron = true) { vm.go(Screen.Helpers) } },
            { Item({ Tile("phone") }, "This phone", { SubText("Calls the bank at ${vm.store.baseUrl}") }, chevron = true) { vm.go(Screen.Settings) } },
        ))
        Btn("Sign out", BtnKind.Secondary, busy = vm.busy) { vm.logout() }
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


private fun helpersSummary(me: Me): String = me.helpers?.helpers?.takeIf { it.isNotEmpty() }?.let { l -> l.joinToString(", ") { it.label } }
    ?: "Optional. Let a family member help you pay"

/** Signed in on a phone the bank has not seen for this account: verify it before anything else (same wording as the web bank). */
@Composable
fun DeviceScreen(vm: AppViewModel) {
    Page {
        Spacer(Modifier.height(24.dp))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { ResultMark("warn", "phone") }
        Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            H1("Is this your new phone?", align = TextAlign.Center)
            Lede("This phone isn't linked to your account yet. A quick check keeps your money safe if someone else has your password.", align = TextAlign.Center)
        }
        Messages(vm)
        Note("brand", "shieldCheck", "We'll ask you to confirm it's you, usually with a code and your face.", "A quick check")
        Btn("Verify this phone", busy = vm.busy) { vm.startDevice() }
        Btn("Sign out", BtnKind.Secondary, busy = vm.busy) { vm.logout() }
        Muted("Didn't sign in yourself? Sign out and change your password.", small = true)
    }
}

/** Optional. Someone who gets help paying can let that family member's phone be trusted; the same screen turns this phone into a helper. */
@Composable
fun HelpersScreen(vm: AppViewModel) {
    val me = vm.me ?: return
    val list = me.helpers?.helpers ?: emptyList()
    var label by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    val parsed = parseHelperCode(code)
    Page {
        TopBar(title = "Family helper", onBack = { vm.back() })
        Messages(vm)
        Note("brand", "shieldCheck", "If a family member sometimes pays for you from their own phone, you can tell us. Their phone is then expected, so your payments through it don't get extra checks that look for strangers. It is optional, and you can remove them any time.", "Optional")
        if (list.isNotEmpty()) ListCard(list.map { h ->
            { Item({ Tile("phone") }, h.label, { SubText(helperStateText(h)) }, { SoftBtn("Remove") { vm.removeHelper(h.id) } }) }
        })
        if (me.helpers != null && list.size < me.helpers.max) CardBox {
            H3("Add a helper")
            Field("Their name (for you)", label, { label = it.take(24) }, enabled = !vm.busy)
            Btn("Get a code", busy = vm.busy) { vm.startHelper(label) }
            Muted("You confirm it's you first. Nothing is paid.", small = true)
        }
        CardBox {
            H3("Helping someone with this phone?")
            Muted("Signed in as them, enter the code from their phone.", small = true)
            Field("8-digit code", code, { code = it.filter { c -> c.isDigit() }.take(8) }, enabled = !vm.busy, keyboard = KeyboardType.Number)
            Btn("Use this phone to help", enabled = parsed != null, busy = vm.busy) { parsed?.let { vm.redeemHelper(it); code = "" } }
        }
    }
}

private fun noCheckSummary(me: Me): String = when {
    me.noCheckMinor > 0 -> "Up to ${formatMoney(me.noCheckMinor)} a day" + (me.noCheckPendingMinor?.let { ", raising to ${formatMoney(it)} soon" } ?: "")
    me.noCheckPendingMinor != null -> "Starting soon: up to ${formatMoney(me.noCheckPendingMinor)} a day"
    else -> "Off. Every payment gets the checks we think it needs"
}

/** "I don't want extra checks for payments up to this much a day." Choosing it is itself checked. */
@Composable
fun NoCheckScreen(vm: AppViewModel) {
    val me = vm.me ?: return
    var text by rememberSaveable { mutableStateOf(if (me.noCheckMinor > 0) (me.noCheckMinor / 100).toString() else "") }
    val rupees = parseNoCheck(text)
    val problem = if (text.isNotBlank() && rupees == null) "Enter a whole amount from ₹$NO_CHECK_MIN to ₹${String.format(Locale.US, "%,d", NO_CHECK_MAX)}." else null
    Page {
        TopBar(title = "No extra checks", onBack = { vm.back() })
        Messages(vm)
        CardBox {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Tile("shieldCheck")
                Column(Modifier.weight(1f)) { H3("Up to how much a day?"); Muted(noCheckSummary(me), small = true) }
            }
            Field("Amount a day", text, { text = it.filter { c -> c.isDigit() }.take(6) }, enabled = !vm.busy, keyboard = KeyboardType.Number, prefix = "₹", error = problem)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { listOf(1000, 2000, 5000, 10000).forEach { v -> SoftBtn("₹${String.format(Locale.US, "%,d", v)}") { text = v.toString() } } }
        }
        Note("brand", "shieldCheck", "Payments up to this much a day, to people you've paid before, from this phone, skip the extra checks. New payees, new phones, a lot of payments in a day and anything unusual are still checked. Your bank may also keep some checks it requires by law.", "What this does")
        Note("warn", "shieldClock", "Raising your limit may take effect after a waiting period. Lowering or turning it off is immediate.", "Raised limits wait")
        Btn(if (me.noCheckMinor > 0) "Change limit" else "Set limit", enabled = rupees != null, busy = vm.busy) { rupees?.let { vm.startNoCheck(it) } }
        if (me.noCheckMinor > 0 || me.noCheckPendingMinor != null) Btn("Turn off", BtnKind.Secondary, busy = vm.busy) { vm.removeNoCheck() }
    }
}


/** What a customer reads while the model is still learning them (see [Learning]). */
fun learningText(l: Learning): String =
    (if (l.moreAtLeast != null && l.moreAtLeast > 0) "About ${l.moreAtLeast} more payments and we'll tailor your checks to how you pay. " else "Almost there. ") +
        "Until then your phone's fingerprint or screen lock confirms each payment."

fun learningProgress(l: Learning): Float {
    val more = l.moreAtLeast ?: return 0f
    val done = l.verifiedPayments
    return if (done + more == 0) 1f else done.toFloat() / (done + more)
}


/** Two optional questions at sign-up. Each answer is a band; the model uses them only until the customer's own payments are enough. */
@Composable
fun QuestionsScreen(vm: AppViewModel) {
    val a = vm.me?.profile
    var month by rememberSaveable { mutableStateOf(a?.monthBand ?: -1) }
    var typical by rememberSaveable { mutableStateOf(a?.typicalBand ?: -1) }
    Page {
        TopBar(label = "Optional · about 20 seconds", onBack = { vm.back() })
        H1("Help us protect you")
        Lede("Two questions about how you use your account. We use your answers only to spot payments that don't look like you, and you can remove them any time in Profile.")
        Messages(vm)
        CardBox {
            H3("About how much do you send in a month?")
            (a?.monthLabels ?: emptyList()).forEachIndexed { i, label -> ChoiceRow(label, month == i) { month = if (month == i) -1 else i } }
        }
        CardBox {
            H3("What is a typical payment?")
            (a?.typicalLabels ?: emptyList()).forEachIndexed { i, label -> ChoiceRow(label, typical == i) { typical = if (typical == i) -1 else i } }
        }
        Btn(if (month < 0 && typical < 0) "Skip" else "Save", busy = vm.busy) {
            if (month < 0 && typical < 0) vm.skipQuestions() else vm.saveAnswers(month.takeIf { it >= 0 }, typical.takeIf { it >= 0 })
        }
        if (vm.me?.helpers != null) Muted("Does a family member help you pay? You can add them any time from Profile, Family helper. Optional.", small = true)
        if (a?.declared == true) LinkText("Remove my answers", modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.removeAnswers() }
        else LinkText("Not now", modifier = Modifier.align(Alignment.CenterHorizontally)) { vm.skipQuestions() }
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(if (selected) C.brandSoft else C.card)
            .clickable(role = Role.RadioButton, onClick = onClick).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(20.dp).clip(CircleShape).background(if (selected) C.brand else C.line), contentAlignment = Alignment.Center) {
            if (selected) Box(Modifier.size(8.dp).clip(CircleShape).background(C.bg))
        }
        Text(label, color = C.ink, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal)
    }
}
