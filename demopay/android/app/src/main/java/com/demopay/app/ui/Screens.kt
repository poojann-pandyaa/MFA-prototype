package com.demopay.app.ui

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.demopay.app.data.RiskRequest
import com.demopay.app.logic.ManualToggles
import com.demopay.app.logic.SCENARIOS
import com.demopay.app.logic.Scenario
import com.demopay.app.logic.rupees

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AppScaffold(title: String, onBack: (() -> Unit)? = null, actions: @Composable () -> Unit = {}, content: @Composable () -> Unit) {
    Scaffold(
        containerColor = Page,
        topBar = {
            Column {
                TopAppBar(
                    title = { Text(title, fontWeight = FontWeight.SemiBold) },
                    navigationIcon = { if (onBack != null) TextButton(onClick = onBack) { Text("Back", color = Color.White) } },
                    actions = { actions() },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Navy, titleContentColor = Color.White, actionIconContentColor = Color.White),
                )
                DemoBanner()
            }
        },
    ) { pad -> Column(Modifier.padding(pad).fillMaxSize()) { content() } }
}

// ---- Login ---------------------------------------------------------------------------------------------------------
@Composable
fun LoginScreen(vm: AppViewModel) {
    var signUp by remember { mutableStateOf(true) }
    var user by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    AppScaffold("DemoPay", actions = { TextButton(onClick = { vm.go(Screen.Settings) }) { Text("Server", color = Color.White) } }) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Title(if (signUp) "Create a demo account" else "Sign in")
            Small("Demo accounts start with Rs 1,00,000 of simulated money. No real details are needed.")
            vm.error?.let { Notice(it, error = true) }
            OutlinedTextField(user, { user = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(
                pass, { pass = it }, label = { Text("Password (6 or more characters)") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                visualTransformation = PasswordVisualTransformation(), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            )
            PrimaryButton(if (vm.busy) "Please wait..." else if (signUp) "Create account" else "Sign in", enabled = !vm.busy && user.length >= 3 && pass.length >= 6) {
                vm.authenticate(user, pass, signUp)
            }
            TextButton(onClick = { signUp = !signUp; vm.error = null }, modifier = Modifier.fillMaxWidth()) {
                Text(if (signUp) "I already have an account" else "Create a new demo account")
            }
            Small("Server: ${vm.baseUrl}")
        }
    }
}

// ---- Home ----------------------------------------------------------------------------------------------------------
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun HomeScreen(vm: AppViewModel) {
    val me = vm.me
    var persona by remember { mutableStateOf("asha") }
    AppScaffold("DemoPay", actions = { TextButton(onClick = { vm.go(Screen.Settings) }) { Text("Settings", color = Color.White) } }) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            SectionCard {
                Small("Available balance (simulated)  ${me?.username ?: ""}")
                Text(me?.let { rupees(it.balance) } ?: "...", fontSize = 30.sp, fontWeight = FontWeight.SemiBold)
                PrimaryButton("Send money") { vm.go(Screen.Send) }
            }
            vm.error?.let { Notice(it, error = true) }
            vm.info?.let { GoodNotice(it) }
            if (me?.demo == true) {
                SectionCard {
                    Text("Demo: load a user's payment history", fontWeight = FontWeight.Medium)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        vm.personas.forEach { p -> FilterChip(selected = persona == p.name, onClick = { persona = p.name }, label = { Text("${p.name} (${p.payments})") }) }
                    }
                    vm.personas.firstOrNull { it.name == persona }?.let { Small(it.story) }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        PrimaryButton("Load", enabled = !vm.busy, modifier = Modifier.weight(1f)) { vm.loadPersona(persona) }
                        SecondaryButton("Reset", modifier = Modifier.weight(1f)) { vm.resetDemo() }
                    }
                }
            }
            Text("Recent payments", fontWeight = FontWeight.Medium)
            if (vm.rows.isEmpty()) Small("No payments yet. Load a demo history above, or send one.")
            vm.rows.take(8).forEach { r ->
                Row(
                    Modifier.fillMaxWidth().clickable { vm.openPayment(r.id) }.padding(vertical = 6.dp), horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) { Text(r.payeeVpa, fontSize = 14.sp, maxLines = 1); Small(r.createdAt.take(16).replace('T', ' ')) }
                    Column(horizontalAlignment = Alignment.End) { Text(rupees(r.amount), fontSize = 14.sp); Small(r.status) }
                }
                HorizontalDivider()
            }
        }
    }
}

// ---- Send ----------------------------------------------------------------------------------------------------------
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SendScreen(vm: AppViewModel) {
    val me = vm.me
    var payee by remember { mutableStateOf("") }
    var amount by remember { mutableStateOf("") }
    var channel by remember { mutableStateOf("pay") }
    var fromContact by remember { mutableStateOf(false) }
    var attested by remember { mutableStateOf(true) }
    var baseRate by remember { mutableStateOf<Double?>(null) }
    var toggles by remember { mutableStateOf(ManualToggles()) }
    var hint by remember { mutableStateOf("") }

    fun apply(s: Scenario) {
        payee = s.payee; amount = s.amount.toLong().toString(); channel = s.channel; baseRate = s.baseRate
        toggles = ManualToggles.from(s.signals)
        hint = "Best with the \"${s.persona}\" history. ${s.hint}"
    }
    LaunchedEffect(payee) { vm.lookupPayee(payee.trim()) }

    AppScaffold("Send money", onBack = { vm.back() }) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            vm.error?.let { Notice(it, error = true) }
            if (me?.demo == true) {
                SectionCard {
                    Text("Demo scenarios (each fills the form)", fontWeight = FontWeight.Medium, fontSize = 13.sp)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        SCENARIOS.forEach { s -> AssistChip(onClick = { apply(s) }, label = { Text(s.name, fontSize = 12.sp) }) }
                    }
                    if (hint.isNotEmpty()) Small(hint)
                }
            }
            OutlinedTextField(payee, { payee = it }, label = { Text("Pay to (payee id)") }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                placeholder = { Text("name@bank") })
            if (me?.demo == true && payee.length >= 2) {
                vm.directory.filter { it.vpa.contains(payee, ignoreCase = true) && it.vpa != payee }.take(3).forEach { d ->
                    Text("${d.vpa}  (${d.label})", fontSize = 12.sp, color = Teal, modifier = Modifier.clickable { payee = d.vpa }.padding(vertical = 2.dp))
                }
            }
            vm.lookup?.let { l ->
                if (payee.length >= 3) {
                    if (l.found) GoodNotice("Registered name: ${l.registeredName}") else Notice("No registered name found for this payee id.")
                }
            }
            OutlinedTextField(amount, { amount = it.filter { c -> c.isDigit() || c == '.' } }, label = { Text("Amount (Rs)") }, singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal), modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("pay" to "Pay", "collect" to "Collect request", "qr" to "Scanned QR").forEach { (k, label) ->
                    FilterChip(selected = channel == k, onClick = { channel = k }, label = { Text(label, fontSize = 12.sp) })
                }
            }
            if (channel == "collect") Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(fromContact, { fromContact = it }); Text("  requester is a saved contact", fontSize = 13.sp)
            }

            if (me?.demo == true) SignalPanel(vm, toggles, { toggles = it }, attested, { attested = it }, baseRate, { baseRate = it })

            val value = amount.toDoubleOrNull()
            PrimaryButton(if (vm.busy) "Checking..." else "Pay", enabled = !vm.busy && payee.trim().length >= 3 && value != null && value > 0) {
                val manual = if (me?.demo == true) toggles.toSignals() else com.demopay.app.logic.Signals()
                vm.submit(
                    RiskRequest(payee.trim(), value!!, channel, fromContact, attested, vm.effectiveSignals(manual), if (me?.demo == true) baseRate else null),
                )
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SignalPanel(
    vm: AppViewModel, t: ManualToggles, onT: (ManualToggles) -> Unit, attested: Boolean, onAttested: (Boolean) -> Unit,
    baseRate: Double?, onBaseRate: (Double?) -> Unit,
) {
    SectionCard {
        Text("Demo: what the phone reports", fontWeight = FontWeight.Medium)
        Small("Real readings from this phone are used when enabled in Settings. The switches below override them, so the presenter can stage a scenario.")
        @Composable
        fun toggle(label: String, on: Boolean, change: (Boolean) -> Unit) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                Text(label, fontSize = 13.sp, modifier = Modifier.weight(1f)); Switch(on, change)
            }
        }
        toggle("On a phone call while paying (${t.callMinutes.toInt()} min, unknown number)", t.call) { onT(t.copy(call = it)) }
        toggle("Screen-share / remote control running", t.remote) { onT(t.copy(remote = it)) }
        toggle("SIM changed 2 hours ago", t.sim) { onT(t.copy(sim = it)) }
        toggle("Brand-new phone (UPI bound 1 hour ago)", t.newPhone) { onT(t.copy(newPhone = it)) }
        toggle("UPI PIN reset recently", t.pinReset) { onT(t.copy(pinReset = it)) }
        toggle("Touch pattern unlike the owner", t.oddTouch) { onT(t.copy(oddTouch = it)) }
        toggle("Phone SDK is trusted (off = server-only model)", attested, onAttested)
        Text("Scam rarity in this traffic", fontSize = 13.sp)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf(null to "default 1 in 10,000", 0.00001 to "1 in 100,000", 0.001 to "1 in 1,000").forEach { (v, label) ->
                FilterChip(selected = baseRate == v, onClick = { onBaseRate(v) }, label = { Text(label, fontSize = 12.sp) })
            }
        }
        val real = vm.phone.snapshot()
        if (vm.useRealSignals && !real.isEmpty()) Small("Reading from this phone now: ${real.toJson()}")
    }
}

// ---- Settings ------------------------------------------------------------------------------------------------------
@Composable
fun SettingsScreen(vm: AppViewModel) {
    var url by remember { mutableStateOf(vm.baseUrl) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { vm.onPhonePermissionResult() }
    LaunchedEffect(Unit) { vm.refreshPhoneReport() }
    AppScaffold("Settings", onBack = { if (vm.me != null) vm.back() else vm.go(Screen.Login) }) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            vm.error?.let { Notice(it, error = true) }
            vm.info?.let { GoodNotice(it) }
            SectionCard {
                Text("Server address", fontWeight = FontWeight.Medium)
                Small("The Android emulator reaches your computer at 10.0.2.2. On a real phone use your computer's address on the same Wi-Fi, for example http://192.168.1.20:8070, and start the server with --host 0.0.0.0.")
                OutlinedTextField(url, { url = it }, singleLine = true, modifier = Modifier.fillMaxWidth(), label = { Text("http://host:port") })
                PrimaryButton("Save") { vm.saveBaseUrl(url) }
            }
            SectionCard {
                Text("Signals from this phone", fontWeight = FontWeight.Medium)
                Small("The app can tell the risk engine facts it reads on this phone, such as whether a phone call is in progress. It never reads call numbers, contacts or audio, and sends only yes/no flags and the call length.")
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                    Text("Use this phone's real signals", modifier = Modifier.weight(1f)); Switch(vm.useRealSignals, { vm.changeUseRealSignals(it) })
                }
                if (!vm.phone.hasPhonePermission()) {
                    SecondaryButton("Allow phone-call detection") { permission.launch(Manifest.permission.READ_PHONE_STATE) }
                }
                vm.phoneReport.forEach { (k, v) -> LabelValue(k, v) }
            }
            if (vm.me != null) SecondaryButton("Sign out") { vm.signOut() }
        }
    }
}
