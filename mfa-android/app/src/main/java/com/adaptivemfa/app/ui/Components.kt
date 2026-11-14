package com.adaptivemfa.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

// ---------------------------------------------------------------- type (same scale as the web bank)

@Composable fun H1(text: String, modifier: Modifier = Modifier, align: TextAlign? = null) =
    Text(text, modifier, color = C.ink, fontSize = 28.sp, lineHeight = 32.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.6).sp, textAlign = align)

@Composable fun H2(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier, color = C.ink, fontSize = 22.sp, lineHeight = 26.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.4).sp)

@Composable fun H3(text: String, modifier: Modifier = Modifier) = Text(text, modifier, color = C.ink, fontSize = 16.sp, fontWeight = FontWeight.ExtraBold)

@Composable fun Lede(text: String, modifier: Modifier = Modifier, align: TextAlign? = null) =
    Text(text, modifier, color = C.muted, fontSize = 16.sp, lineHeight = 24.sp, textAlign = align)

@Composable fun Muted(text: String, modifier: Modifier = Modifier, small: Boolean = false, align: TextAlign? = null, color: Color = C.muted) =
    Text(text, modifier, color = color, fontSize = if (small) 13.sp else 14.sp, textAlign = align)

@Composable fun SectionLabel(text: String) =
    Text(text.uppercase(), Modifier.padding(start = 4.dp), color = C.muted, fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.6.sp)

// ---------------------------------------------------------------- page frame, top bar, tabs

/** [scroll] false lets a screen pin its button to the bottom with `Spacer(Modifier.weight(1f))`. */
@Composable
fun Page(scroll: Boolean = true, content: @Composable ColumnScope.() -> Unit) {
    val base = Modifier.fillMaxSize().background(C.bg).windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal)).imePadding()
    Column(
        (if (scroll) base.verticalScroll(rememberScrollState()) else base).padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp), content = content,
    )
}

@Composable
fun IconBtn(icon: String, label: String, onClick: () -> Unit) {
    Box(
        Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(C.card).clickable(role = Role.Button, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) { Ic(icon, C.ink, 20.dp, label) }
}

@Composable
fun TopBar(title: String? = null, label: String? = null, onBack: (() -> Unit)? = null) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        if (onBack != null) IconBtn("back", "Back", onBack)
        when {
            title != null -> H2(title, Modifier.weight(1f))
            label != null -> Text(label, Modifier.weight(1f), color = C.muted, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
            else -> Spacer(Modifier.weight(1f))
        }
        if (onBack != null && title == null) Spacer(Modifier.width(44.dp))
    }
}

@Composable
fun StepsBar(on: Int, total: Int) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        repeat(total) { Box(Modifier.weight(1f).height(5.dp).clip(RoundedCornerShape(3.dp)).background(if (it < on) C.brand else C.line)) }
    }
}

private val TABS = listOf(Triple("home", "Home", "home"), Triple("pay", "Pay", "send"), Triple("activity", "Activity", "activity"), Triple("profile", "Profile", "profile"))

@Composable
fun TabBar(on: String, onSelect: (String) -> Unit) {
    Column(Modifier.fillMaxWidth().background(C.card).navigationBarsPadding()) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(C.line))
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp)) {
            TABS.forEach { (key, label, ic) ->
                val sel = key == on
                Column(
                    Modifier.weight(1f).clickable(role = Role.Tab) { onSelect(key) }.padding(vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    Ic(ic, if (sel) C.brand else C.muted, 22.dp)
                    Text(label, color = if (sel) C.brand else C.muted, fontSize = 12.sp, fontWeight = if (sel) FontWeight.ExtraBold else FontWeight.SemiBold)
                }
            }
        }
    }
}

// ---------------------------------------------------------------- buttons

enum class BtnKind { Primary, Secondary, Danger }

@Composable
fun Btn(text: String, kind: BtnKind = BtnKind.Primary, enabled: Boolean = true, busy: Boolean = false, onClick: () -> Unit) {
    val (bg, fg, border) = when (kind) {
        BtnKind.Primary -> Triple(C.brand, C.brandInk, null)
        BtnKind.Secondary -> Triple(C.card, C.ink, C.line)
        BtnKind.Danger -> Triple(C.card, C.bad, C.bad)
    }
    Box(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).alpha(if (enabled) 1f else 0.5f).clip(RoundedCornerShape(16.dp)).background(bg)
            .then(if (border != null) Modifier.border(BorderStroke(1.5.dp, border), RoundedCornerShape(16.dp)) else Modifier)
            .clickable(enabled = enabled && !busy, role = Role.Button, onClick = onClick).padding(horizontal = 20.dp),
        contentAlignment = Alignment.Center,
    ) {
        if (busy) CircularProgressIndicator(Modifier.size(22.dp), color = fg, strokeWidth = 2.5.dp)
        else Text(text, color = fg, fontSize = 16.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun SoftBtn(text: String, onClick: () -> Unit) {
    Box(Modifier.heightIn(min = 40.dp).clip(RoundedCornerShape(12.dp)).background(C.brandSoft).clickable(role = Role.Button, onClick = onClick).padding(horizontal = 14.dp), contentAlignment = Alignment.Center) {
        Text(text, color = C.brand, fontSize = 14.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun LinkText(text: String, bad: Boolean = false, small: Boolean = false, modifier: Modifier = Modifier, onClick: () -> Unit) =
    Text(text, modifier.clickable(role = Role.Button, onClick = onClick), color = if (bad) C.bad else C.brand, fontSize = if (small) 13.sp else 14.sp, fontWeight = FontWeight.Bold)

// ---------------------------------------------------------------- cards, lists, tiles, chips, notes

@Composable
fun CardBox(modifier: Modifier = Modifier, padding: Int = 18, content: @Composable ColumnScope.() -> Unit) =
    Column(modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(C.card).padding(padding.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)

@Composable
fun ListCard(rows: List<@Composable () -> Unit>) {
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp)).background(C.card).padding(horizontal = 16.dp, vertical = 4.dp)) {
        rows.forEachIndexed { i, row ->
            row()
            if (i < rows.lastIndex) Box(Modifier.fillMaxWidth().height(1.dp).background(C.lineSoft))
        }
    }
}

@Composable
fun Item(
    lead: @Composable () -> Unit, title: String, sub: (@Composable () -> Unit)? = null, trail: (@Composable () -> Unit)? = null,
    chevron: Boolean = false, onClick: (() -> Unit)? = null,
) {
    Row(
        Modifier.fillMaxWidth().then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier).padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        lead()
        Column(Modifier.weight(1f)) {
            Text(title, color = C.ink, fontSize = 15.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            sub?.invoke()
        }
        trail?.invoke()
        if (chevron) Ic("chevron", C.muted, 18.dp)
    }
}

@Composable fun SubText(text: String) = Text(text, color = C.muted, fontSize = 13.sp)

@Composable
fun Tile(icon: String, tone: String = "brand", size: Int = 44) {
    val (bg, fg) = when (tone) { "grey" -> C.lineSoft to C.muted; "warn" -> C.warnSoft to C.warn; "bad" -> C.badSoft to C.bad; else -> C.brandSoft to C.brand }
    Box(Modifier.size(size.dp).clip(RoundedCornerShape(14.dp)).background(bg), contentAlignment = Alignment.Center) { Ic(icon, fg, (size / 2).dp) }
}

@Composable
fun Avatar(initials: String, size: Int = 44) {
    val (bg, fg) = C.tones[(initials.sumOf { it.code }) % C.tones.size]
    Box(Modifier.size(size.dp).clip(CircleShape).background(bg), contentAlignment = Alignment.Center) {
        Text(initials.ifBlank { "?" }, color = fg, fontWeight = FontWeight.ExtraBold, fontSize = (size * 0.34).sp)
    }
}

@Composable
fun Chip(text: String, tone: String = "") {
    val (bg, fg) = when (tone) { "ok" -> C.okSoft to C.ok; "warn" -> C.warnSoft to C.warn; "bad" -> C.badSoft to C.bad; "brand" -> C.brandSoft to C.brand; else -> C.lineSoft to C.muted }
    Text(text, Modifier.clip(RoundedCornerShape(999.dp)).background(bg).padding(horizontal = 10.dp, vertical = 3.dp), color = fg, fontSize = 12.sp, fontWeight = FontWeight.Bold)
}

/** kind: "brand", "warn", "bad" or "plain". */
@Composable
fun Note(kind: String, icon: String, text: String, title: String? = null) {
    val (bg, fg) = when (kind) { "warn" -> C.warnSoft to C.warnInk; "bad" -> C.badSoft to C.bad; "plain" -> C.card to C.muted; else -> C.brandSoft to C.brandDeep }
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(bg).padding(horizontal = 16.dp, vertical = 14.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Ic(icon, fg, 20.dp, modifier = Modifier.padding(top = 1.dp))
        Column {
            if (title != null) Text(title, color = if (kind == "warn") C.ink else fg, fontSize = 14.sp, fontWeight = FontWeight.Bold)
            Text(text, color = fg, fontSize = 14.sp, lineHeight = 20.sp)
        }
    }
}

@Composable
fun Kv(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = C.muted, fontSize = 14.sp)
        Text(value, color = C.ink, fontSize = 14.sp, fontWeight = FontWeight.Bold, textAlign = TextAlign.End, modifier = Modifier.padding(start = 12.dp))
    }
}

@Composable
fun ResultMark(kind: String, icon: String) {
    val (soft, solid) = when (kind) { "ok" -> C.okSoft to C.ok; "bad" -> C.badSoft to C.bad; else -> C.warnSoft to C.warn }
    Box(Modifier.size(104.dp).clip(CircleShape).background(soft), contentAlignment = Alignment.Center) {
        if (kind == "warn") Ic(icon, solid, 48.dp)
        else Box(Modifier.size(76.dp).clip(CircleShape).background(solid), contentAlignment = Alignment.Center) { Ic(icon, Color.White, 40.dp) }
    }
}

// ---------------------------------------------------------------- forms

@Composable
fun Field(
    label: String, value: String, onChange: (String) -> Unit, enabled: Boolean = true, password: Boolean = false,
    keyboard: KeyboardType = KeyboardType.Text, prefix: String? = null, error: String? = null, placeholder: String? = null,
) {
    var shown by remember { mutableStateOf(false) }
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, color = C.ink, fontSize = 14.sp, fontWeight = FontWeight.Bold)
        Row(
            Modifier.fillMaxWidth().height(52.dp).clip(RoundedCornerShape(14.dp)).background(C.card)
                .border(BorderStroke(1.5.dp, if (focused) C.brand else C.line), RoundedCornerShape(14.dp)).padding(start = 14.dp, end = 6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (prefix != null) Text(prefix, color = C.muted, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
            Box(Modifier.weight(1f)) {
                if (value.isEmpty() && placeholder != null) Text(placeholder, color = C.muted.copy(alpha = 0.6f), fontSize = 16.sp)
                BasicTextField(
                    value, onChange, Modifier.fillMaxWidth(), enabled = enabled, singleLine = true, interactionSource = interaction,
                    textStyle = TextStyle(color = C.ink, fontSize = 16.sp), cursorBrush = SolidColor(C.brand),
                    visualTransformation = if (password && !shown) PasswordVisualTransformation() else VisualTransformation.None,
                    keyboardOptions = KeyboardOptions(keyboardType = if (password) KeyboardType.Password else keyboard),
                )
            }
            if (password) Box(Modifier.size(40.dp).clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button) { shown = !shown }, contentAlignment = Alignment.Center) {
                Ic(if (shown) "eyeOff" else "eye", C.muted, 20.dp, if (shown) "Hide password" else "Show password")
            }
        }
        if (error != null) Text(error, color = C.bad, fontSize = 13.sp)
    }
}

/** Errors and notices from the ViewModel, in the web bank's note style. */
@Composable
fun Messages(vm: AppViewModel) {
    vm.error?.let { Note("bad", "warn", it) }
    vm.notice?.let { Note("brand", "shieldCheck", it) }
}

@Composable
fun Segmented(options: List<Pair<String, String>>, on: String, onSelect: (String) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { (key, label) ->
            val sel = key == on
            Text(
                label, Modifier.height(38.dp).clip(RoundedCornerShape(999.dp)).background(if (sel) C.ink else C.card)
                    .border(BorderStroke(1.5.dp, if (sel) C.ink else C.line), RoundedCornerShape(999.dp))
                    .clickable(role = Role.Button) { onSelect(key) }.padding(horizontal = 16.dp, vertical = 8.dp),
                color = if (sel) C.bg else C.ink, fontSize = 14.sp, fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
fun Keypad(onKey: (String) -> Unit) {
    val keys = listOf("1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "del")
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        keys.chunked(3).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { k ->
                    Box(
                        Modifier.weight(1f).height(58.dp).clip(RoundedCornerShape(16.dp)).background(if (k == "del" || k == ".") Color.Transparent else C.card)
                            .clickable(role = Role.Button) { onKey(k) },
                        contentAlignment = Alignment.Center,
                    ) { if (k == "del") Ic("del", C.ink, 24.dp, "Delete") else Text(k, color = C.ink, fontSize = 24.sp, fontWeight = FontWeight.Bold) }
                }
            }
        }
    }
}

// ---------------------------------------------------------------- welcome art

@Composable
fun HeroArt() {
    Box(Modifier.size(230.dp)) {
        Box(Modifier.fillMaxSize().clip(CircleShape).background(C.brandSoft))
        Box(Modifier.fillMaxSize().padding(32.dp).shadow(10.dp, CircleShape).clip(CircleShape).background(C.card), contentAlignment = Alignment.Center) {
            Ic("shieldCheck", C.brand, 96.dp)
        }
        Pill("face", "Face", Modifier.align(Alignment.TopEnd).offset(x = 8.dp, y = 34.dp))
        Pill("mail", "Email code", Modifier.align(Alignment.BottomStart).offset(x = (-12).dp, y = (-44).dp))
    }
}

@Composable
private fun Pill(icon: String, text: String, modifier: Modifier) {
    Row(modifier.shadow(8.dp, RoundedCornerShape(14.dp)).clip(RoundedCornerShape(14.dp)).background(C.card).padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Ic(icon, C.brand, 16.dp)
        Text(text, color = C.ink, fontSize = 13.sp, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun BrandMark() {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Box(Modifier.size(36.dp).clip(RoundedCornerShape(11.dp)).background(C.brand), contentAlignment = Alignment.Center) { Ic("bank", C.brandInk, 20.dp) }
        Text("Demo Bank", color = C.ink, fontSize = 18.sp, fontWeight = FontWeight.ExtraBold)
    }
}

@Composable
fun Busy(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        CircularProgressIndicator(Modifier.size(20.dp), color = C.brand, strokeWidth = 2.5.dp)
        Text(text, color = C.muted, fontSize = 14.sp)
    }
}

/** Six boxes over one real input, like the web bank's code step: autofill and paste still work. */
@Composable
fun CodeBoxes(value: String, onChange: (String) -> Unit, enabled: Boolean = true) {
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    Box(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            repeat(6) { i ->
                val current = focused && i == minOf(value.length, 5)
                Box(
                    Modifier.weight(1f).height(60.dp).clip(RoundedCornerShape(14.dp)).background(C.card)
                        .border(BorderStroke(if (current) 2.dp else 1.5.dp, if (current) C.ink else if (i < value.length) C.brand else C.line), RoundedCornerShape(14.dp)),
                    contentAlignment = Alignment.Center,
                ) { Text(value.getOrNull(i)?.toString() ?: "", color = C.ink, fontSize = 26.sp, fontWeight = FontWeight.ExtraBold) }
            }
        }
        BasicTextField(
            value, { onChange(it.filter(Char::isDigit).take(6)) }, Modifier.matchParentSize().alpha(0f), enabled = enabled, singleLine = true,
            interactionSource = interaction, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
        )
    }
}
