package com.adaptivemfa.app

import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
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

    @Test fun loginScreenNeedsBothFields() {
        compose.onNodeWithText("Sign in").assertIsDisplayed()
        compose.onNodeWithText("Server: http://10.0.2.2:8000").assertIsDisplayed()
        compose.onNodeWithText("Log in").assertIsNotEnabled()
        compose.onNodeWithText("Username").performTextInput("alice")
        compose.onNodeWithText("Log in").assertIsNotEnabled()
        compose.onNodeWithText("Password").performTextInput("pw")
        compose.onNodeWithText("Log in").assertIsEnabled()
    }

    @Test fun enrollNeedsMatchingPasswordsAndAPhoto() {
        compose.onNodeWithText("No account yet? Enroll").performClick()
        compose.onNodeWithText("Create your account").assertIsDisplayed()
        compose.onNodeWithText("Username").performTextInput("bob")
        compose.onNodeWithText("Password").performTextInput("secret1")
        compose.onNodeWithText("Confirm password").performTextInput("secret2")
        compose.onNodeWithText("Passwords do not match").assertIsDisplayed()
        compose.onNodeWithText("Confirm password").performTextClearance()
        compose.onNodeWithText("Confirm password").performTextInput("secret1")
        // Still no photo taken, so the account cannot be created yet.
        compose.onNodeWithText("Create account").performScrollTo().assertIsNotEnabled()
        compose.onNodeWithText("Back").performClick()
        compose.onNodeWithText("Sign in").assertIsDisplayed()
    }

    @Test fun settingsRejectsABadAddressAndSavesAGoodOne() {
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Server URL").performTextClearance()
        compose.onNodeWithText("Server URL").performTextInput("not an address")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("That does not look like a server address. Example: http://192.168.1.20:8000").assertIsDisplayed()

        compose.onNodeWithText("Server URL").performTextClearance()
        compose.onNodeWithText("Server URL").performTextInput("192.168.1.20:8000")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("Saved").assertIsDisplayed()
        compose.onNodeWithText("Back").performClick()
        compose.onNodeWithText("Server: http://192.168.1.20:8000").assertIsDisplayed()
    }

    @Test fun unreachableServerShowsAHelpfulError() {
        // Port 9 on the device itself: nothing listens there, so the connection is refused at once.
        compose.onNodeWithText("Settings").performClick()
        compose.onNodeWithText("Server URL").performTextClearance()
        compose.onNodeWithText("Server URL").performTextInput("http://127.0.0.1:9")
        compose.onNodeWithText("Save").performClick()
        compose.onNodeWithText("Back").performClick()

        compose.onNodeWithText("Username").performTextInput("alice")
        compose.onNodeWithText("Password").performTextInput("pw")
        compose.onNodeWithText("Log in").performClick()
        compose.waitUntil(10_000) {
            compose.onAllNodesWithTextContaining("Cannot reach the server").fetchSemanticsNodes().isNotEmpty()
        }
    }
}

private fun androidx.compose.ui.test.junit4.ComposeTestRule.onAllNodesWithTextContaining(text: String) =
    onAllNodes(androidx.compose.ui.test.hasText(text, substring = true))
