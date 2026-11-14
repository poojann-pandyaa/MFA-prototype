package com.adaptivemfa.app

import com.adaptivemfa.app.data.Http
import com.adaptivemfa.app.data.HttpResult
import com.adaptivemfa.app.data.Settings
import java.io.IOException

class InMemorySettings(
    override var username: String? = null,
    override var baseUrl: String = "http://10.0.2.2:8050",
    override val deviceId: String = "android_test01",
) : Settings

data class Recorded(val method: String, val url: String, val headers: Map<String, String>, val body: String?)

/** Answers by "METHOD /path" (query ignored); records every request. Unknown routes throw like an unreachable server. */
class RoutedHttp(vararg routes: Pair<String, HttpResult>) : Http {
    val routes = routes.toMap().toMutableMap()
    val calls = mutableListOf<Recorded>()
    override fun request(method: String, url: String, headers: Map<String, String>, body: String?): HttpResult {
        calls += Recorded(method, url, headers, body)
        val path = "/" + url.substringAfter("://").substringAfter('/').substringBefore('?')
        return routes["$method $path"] ?: throw IOException("no route for $method $path")
    }
    fun last(path: String) = calls.last { it.url.substringBefore('?').endsWith(path) }
    fun called(path: String) = calls.any { it.url.substringBefore('?').endsWith(path) }
}

fun ok(json: String) = HttpResult(200, json)
