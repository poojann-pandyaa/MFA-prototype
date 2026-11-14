plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.adaptivemfa.app"
    compileSdk = 37      // Compose 1.12 requires 37

    defaultConfig {
        applicationId = "com.adaptivemfa.app"
        minSdk = 26          // Android 8.0
        targetSdk = 36
        // CI sets these from the release tag: -PAPP_VERSION_NAME=1.2.0 -PAPP_VERSION_CODE=12
        versionCode = (project.findProperty("APP_VERSION_CODE") as String?)?.toInt() ?: 1
        versionName = (project.findProperty("APP_VERSION_NAME") as String?) ?: "1.0.0"
        // The bank the app talks to until someone changes it in Settings: -PAMFA_BANK_URL=https://bank.example.com
        val bank = (project.findProperty("AMFA_BANK_URL") as String?) ?: "http://10.0.2.2:8050"
        buildConfigField("String", "DEFAULT_BANK_URL", "\"$bank\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
        // What GitHub publishes for people to try: not debuggable, signed with the debug key so any phone can
        // install it, and (src/demo manifest) allowed to talk to a plain-http demo server on a local network.
        create("demo") {
            initWith(getByName("release"))
            signingConfig = signingConfigs.getByName("debug")
            matchingFallbacks += "release"
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        // JVM unit tests: Android framework calls return defaults instead of throwing.
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.12.4")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.10.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.10.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")

    val camerax = "1.6.2"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // JVM unit + integration tests (src/test)
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")   // the Android org.json is a stub in JVM unit tests
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")

    // On-device UI tests (src/androidTest), run on an emulator or phone
    androidTestImplementation(composeBom)
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("androidx.test:core:1.7.0")
    // Compose's test library pulls Espresso 3.5, which calls a hidden API removed in Android 17 (API 37).
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
}
