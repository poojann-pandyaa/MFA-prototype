package com.adaptivemfa.app

import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Runs the real app on an emulator or phone: `gradlew :app:connectedDebugAndroidTest`. */
@RunWith(AndroidJUnit4::class)
class AppUiTest {
    @get:Rule val compose = createEmptyComposeRule()
    private var scenario: ActivityScenario<MainActivity>? = null

    @Before fun freshInstall() {
        ApplicationProvider.getApplicationContext<Context>()
            .getSharedPreferences("adaptive_mfa", Context.MODE_PRIVATE).edit().clear().commit()
        scenario = ActivityScenario.launch(MainActivity::class.java)
    }

    @After fun close() {
        scenario?.close()
    }

    @Test fun welcomeLeadsToSignInWhichNeedsBothFields() {
        compose.onNodeWithText("Banking that knows it's really you").assertIsDisplayed()
        compose.onNodeWithText("I already bank here").performClick()
        compose.onNodeWithText("Welcome back").assertIsDisplayed()
        compose.onAllNodesWithText("Sign in").onLast().assertIsNotEnabled()
        compose.onNodeWithText("Username").performTextInput("alice")
        compose.onAllNodesWithText("Sign in").onLast().assertIsNotEnabled()
        compose.onNodeWithText("Password").performTextInput("pw")
        compose.onAllNodesWithText("Sign in").onLast().assertIsEnabled()
    }

    @Test fun openingAnAccountShowsTheFormAndGoesBack() {
        compose.onNodeWithText("Open an account").performClick()
        compose.onNodeWithText("It takes about a minute", substring = true).assertIsDisplayed()
        compose.onNodeWithText("Full name").assertIsDisplayed()
        compose.onNodeWithText("Mobile number").assertIsDisplayed()
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithText("Banking that knows it's really you").assertIsDisplayed()
    }

    @Test fun settingsRejectsABadAddressAndSavesAGoodOne() {
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Bank URL").performTextClearance()
        compose.onNodeWithText("Bank URL").performTextInput("not an address")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("That does not look like a server address. Example: http://192.168.1.20:8050").assertIsDisplayed()

        compose.onNodeWithText("Bank URL").performTextClearance()
        compose.onNodeWithText("Bank URL").performTextInput("192.168.1.20:8050")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("Saved").assertIsDisplayed()
        compose.onNodeWithContentDescription("Back").performClick()
        compose.onNodeWithText("Bank: http://192.168.1.20:8050").assertIsDisplayed()
    }

    @Test fun unreachableBankShowsAHelpfulError() {
        // Port 9 on the device itself: nothing listens there, so the connection is refused at once.
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Bank URL").performTextClearance()
        compose.onNodeWithText("Bank URL").performTextInput("http://127.0.0.1:9")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithContentDescription("Back").performClick()

        compose.onNodeWithText("I already bank here").performClick()
        compose.onNodeWithText("Username").performTextInput("alice")
        compose.onNodeWithText("Password").performTextInput("pw")
        compose.onAllNodesWithText("Sign in").onLast().performClick()
        compose.waitUntil(10_000) {
            compose.onAllNodesWithTextContaining("Cannot reach").fetchSemanticsNodes().isNotEmpty()
        }
    }
}

private fun androidx.compose.ui.test.junit4.ComposeTestRule.onAllNodesWithTextContaining(text: String) =
    onAllNodes(androidx.compose.ui.test.hasText(text, substring = true))
