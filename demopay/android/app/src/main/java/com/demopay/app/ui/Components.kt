package com.demopay.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Always-on reminder that nothing here is real. */
@Composable
fun DemoBanner() {
    Box(Modifier.fillMaxWidth().background(Amber).padding(horizontal = 12.dp, vertical = 6.dp)) {
        Text(
            "DEMO: simulated bank, money and phone signals. Not a real bank or payment app.",
            color = AmberText, fontSize = 11.sp, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
fun Notice(text: String, error: Boolean = false, modifier: Modifier = Modifier) {
    val bg = if (error) DangerBg else Amber
    val fg = if (error) Danger else AmberText
    Box(modifier.fillMaxWidth().background(bg, RoundedCornerShape(10.dp)).padding(12.dp).semantics { liveRegion = LiveRegionMode.Polite }) {
        Text(text, color = fg, fontSize = 13.sp)
    }
}

@Composable
fun GoodNotice(text: String, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().background(GoodBg, RoundedCornerShape(10.dp)).padding(12.dp)) { Text(text, color = Good, fontSize = 13.sp) }
}

@Composable
fun SectionCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Card(
        modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
    ) { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) { content() } }
}

@Composable
fun PrimaryButton(text: String, enabled: Boolean = true, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Button(onClick, modifier.fillMaxWidth().height(48.dp), enabled = enabled, shape = RoundedCornerShape(12.dp)) { Text(text, fontWeight = FontWeight.Medium) }
}

@Composable
fun SecondaryButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    OutlinedButton(onClick, modifier.fillMaxWidth().height(48.dp), shape = RoundedCornerShape(12.dp)) { Text(text) }
}

@Composable
fun Title(text: String) = Text(text, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = Navy)

@Composable
fun Small(text: String, modifier: Modifier = Modifier, color: Color = Slate) = Text(text, fontSize = 12.sp, color = color, modifier = modifier)

/** Fake UPI PIN pad. Any 4 to 6 digits are accepted; the real UPI PIN is never used. */
@Composable
fun PinPad(busy: Boolean, onSubmit: (String) -> Unit) {
    var pin by remember { mutableStateOf("") }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Enter your UPI PIN", fontWeight = FontWeight.Medium)
        Small("Demo: any 4 to 6 digits work. The real UPI PIN is not used.", color = AmberText)
        Text(
            "•".repeat(pin.length).ifEmpty { " " }, fontSize = 30.sp, letterSpacing = 8.sp, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp).semantics { contentDescription = "${pin.length} digits entered" },
        )
        val keys = listOf(listOf("1", "2", "3"), listOf("4", "5", "6"), listOf("7", "8", "9"), listOf("Clear", "0", "Del"))
        keys.forEach { row ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { k ->
                    OutlinedButton(
                        onClick = {
                            pin = when (k) { "Clear" -> ""; "Del" -> pin.dropLast(1); else -> if (pin.length < 6) pin + k else pin }
                        },
                        modifier = Modifier.weight(1f).height(52.dp), shape = RoundedCornerShape(12.dp),
                    ) { Text(k, fontSize = if (k.length > 1) 13.sp else 20.sp) }
                }
            }
        }
        Spacer(Modifier.height(4.dp))
        PrimaryButton(if (busy) "Paying..." else "Pay now", enabled = pin.length >= 4 && !busy) { onSubmit(pin) }
    }
}

@Composable
fun LabelValue(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Top) {
        Text(label, fontSize = 12.sp, color = Slate, modifier = Modifier.padding(end = 8.dp))
        Text(value, fontSize = 12.sp)
    }
}
